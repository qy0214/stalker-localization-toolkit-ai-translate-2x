# -*- coding: utf-8 -*-
"""在线翻译应用服务：术语、缓存、限流、预算、质量闸门与元数据审计。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from stalker_translate_core import (
    GlossaryIndex,
    QualityIssue,
    TokenUsage,
    TranslationRequest,
    TranslationResult,
    pipeline_fingerprint,
)

from .config import Config
from .provider import OpenAICompatibleProvider, ProviderError, ProviderSettings

PLACEHOLDER_RE = re.compile(
    r"\$[A-Za-z_][A-Za-z0-9_]*|%(?:\d+\$)?[sdifgexX%]|%c\[[^\]]*\]|\$\$[^$]+\$\$"
)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class BudgetExceeded(RuntimeError):
    pass


class RateLimiter:
    def __init__(self, rpm: int, tpm: int):
        self.rpm = max(0, int(rpm))
        self.tpm = max(0, int(tpm))
        self.lock = threading.Lock()
        self.requests: deque[float] = deque()
        self.tokens: deque[tuple[float, int]] = deque()

    def wait(self, estimated_tokens: int) -> None:
        if not self.rpm and not self.tpm:
            return
        while True:
            with self.lock:
                now = time.monotonic()
                while self.requests and now - self.requests[0] >= 60:
                    self.requests.popleft()
                while self.tokens and now - self.tokens[0][0] >= 60:
                    self.tokens.popleft()
                used = sum(n for _t, n in self.tokens)
                if ((not self.rpm or len(self.requests) < self.rpm)
                        and (not self.tpm or used + estimated_tokens <= self.tpm)):
                    self.requests.append(now)
                    self.tokens.append((now, estimated_tokens))
                    return
                wait = 0.1
                if self.rpm and len(self.requests) >= self.rpm:
                    wait = max(wait, 60 - (now - self.requests[0]) + 0.01)
                if self.tpm and used + estimated_tokens > self.tpm:
                    wait = max(wait, 60 - (now - self.tokens[0][0]) + 0.01)
            time.sleep(wait)


class BudgetTracker:
    def __init__(self, cfg: Config):
        limits = cfg.budget or {}
        self.max_requests = limits.get("max_requests")
        self.max_input_tokens = limits.get("max_input_tokens")
        self.max_output_tokens = limits.get("max_output_tokens")
        self.max_cost = limits.get("max_cost")
        self.input_price = cfg.input_price_per_million
        self.output_price = cfg.output_price_per_million
        self.lock = threading.Lock()
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0

    def before_request(self, estimated_input: int) -> None:
        with self.lock:
            if self.max_requests is not None and self.requests >= self.max_requests:
                raise BudgetExceeded("超过 max_requests 预算")
            if self.max_input_tokens is not None \
                    and self.input_tokens + estimated_input > self.max_input_tokens:
                raise BudgetExceeded("超过 max_input_tokens 预算")

    def consume(self, usage: TokenUsage) -> None:
        with self.lock:
            self.requests += 1
            self.input_tokens += usage.input_tokens
            self.output_tokens += usage.output_tokens
            self.cost += (usage.input_tokens * self.input_price
                          + usage.output_tokens * self.output_price) / 1_000_000

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"requests": self.requests, "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens, "cost": round(self.cost, 6)}


class JsonCache:
    def __init__(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path / "translation_cache.sqlite3"),
                                  check_same_thread=False)
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS cache "
                            "(key TEXT PRIMARY KEY, text TEXT NOT NULL, usage TEXT NOT NULL)")

    def get(self, key: str) -> TranslationResult | None:
        with self.lock:
            row = self.db.execute("SELECT text, usage FROM cache WHERE key=?",
                                  (key,)).fetchone()
        if not row:
            return None
        return TranslationResult(text=row[0], usage=TokenUsage(**json.loads(row[1])),
                                 cache_hit=True)

    def put(self, key: str, result: TranslationResult) -> None:
        usage = {"input_tokens": result.usage.input_tokens,
                 "output_tokens": result.usage.output_tokens,
                 "cached_input_tokens": result.usage.cached_input_tokens,
                 "estimated": result.usage.estimated}
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                            (key, result.text, json.dumps(usage)))

    def close(self) -> None:
        with self.lock:
            self.db.close()


class Audit:
    def __init__(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        self.path = path / "events.jsonl"
        self.lock = threading.Lock()

    def record(self, event: str, **payload: Any) -> None:
        row = {"ts": time.time(), "event": event, **payload}
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def quality_issues(source: str, translated: str, target_lang: str) -> tuple[QualityIssue, ...]:
    issues: list[QualityIssue] = []
    if translated.strip() == source.strip() and re.search(r"[A-Za-zА-Яа-яЁё]", source):
        issues.append(QualityIssue("source_echo", "译文与源文相同"))
    if target_lang == "chs" and not CJK_RE.search(translated):
        issues.append(QualityIssue("no_target_text", "译文未产生中文"))
    if sorted(PLACEHOLDER_RE.findall(source)) != sorted(PLACEHOLDER_RE.findall(translated)):
        issues.append(QualityIssue("placeholder_mismatch", "占位符或颜色码不一致"))
    if source.count("\\n") != translated.count("\\n"):
        issues.append(QualityIssue("newline_mismatch", "字面换行数量不一致"))
    return tuple(issues)


class TranslationService:
    def __init__(self, cfg: Config, provider: OpenAICompatibleProvider,
                 glossary: GlossaryIndex, asset_fingerprint: str):
        self.cfg = cfg
        self.provider = provider
        self.glossary = glossary
        self.pipeline = pipeline_fingerprint({
            "core": "1.0.0", "provider": provider.name, "model": provider.model,
            "assets": asset_fingerprint, "prompt": "online-v1",
        })
        self.limiter = RateLimiter(cfg.rpm, cfg.tpm)
        self.budget = BudgetTracker(cfg)
        self.cache = JsonCache(cfg.cache_path)
        self.audit = Audit(cfg.audit_path)

    def _cache_key(self, request: TranslationRequest) -> str:
        payload = {
            "source": request.text, "source_lang": request.source_lang,
            "target_lang": request.target_lang, "context": request.context,
            "style": request.style, "pipeline": self.pipeline,
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                                         sort_keys=True).encode("utf-8")).hexdigest()

    def translate(self, text: str, source_lang: str = "auto",
                  target_lang: str = "chs", context: str = "",
                  use_cache: bool = True) -> TranslationResult:
        if not text or not text.strip():
            return TranslationResult(text=text, source_text=text,
                                     retained_source=True,
                                     issues=(QualityIssue("empty_source", "源文为空"),))
        if len(text) > self.cfg.max_text_chars:
            raise ValueError("文本超过 max_text_chars=%d" % self.cfg.max_text_chars)
        request = TranslationRequest(text=text, source_lang=source_lang,
                                     target_lang=target_lang, context=context,
                                     glossary_hits=self.glossary.hits(text))
        if target_lang == "chs":
            exact = self.glossary.lookup(text)
            if exact is not None and exact != text:
                return TranslationResult(text=exact, source_text=text,
                                         provider="termbase", model="exact")
        key = self._cache_key(request)
        if use_cache:
            cached = self.cache.get(key)
            if cached is not None:
                self.audit.record("cache_hit", pipeline=self.pipeline,
                                  provider=self.provider.name, model=self.provider.model)
                return TranslationResult(text=cached.text, source_text=text,
                                         provider=self.provider.name,
                                         model=self.provider.model, cache_hit=True)
        max_tokens = max(1024, min(int(self.cfg.ctx_size * 0.6),
                                   int(len(text) * 1.5) + 256))
        started = time.monotonic()
        result: TranslationResult | None = None
        issues: tuple[QualityIssue, ...] = ()
        for attempt in range(2):
            estimated = max(1, len(text) // 2)
            self.budget.before_request(estimated)
            self.limiter.wait(estimated + max_tokens // 2)
            try:
                result = self.provider.translate(
                    request, max_tokens=max_tokens,
                    extra_system=("上一次输出未通过校验；请重新完整翻译，"
                                  "严格保留占位符与换行。") if attempt else "")
            except ProviderError as exc:
                self.audit.record("provider_error", pipeline=self.pipeline,
                                  provider=self.provider.name, model=self.provider.model,
                                  error_type=type(exc).__name__, retryable=exc.retryable)
                raise
            self.budget.consume(result.usage)
            issues = quality_issues(text, result.text, target_lang)
            if not issues:
                break
        elapsed = time.monotonic() - started
        if issues:
            final = TranslationResult(
                text=text, source_text=text, provider=self.provider.name,
                model=self.provider.model, request_id=result.request_id if result else "",
                usage=result.usage if result else TokenUsage(), issues=issues,
                retained_source=True,
            )
            self.audit.record("translation_rejected", pipeline=self.pipeline,
                              provider=self.provider.name, model=self.provider.model,
                              issue_codes=[i.code for i in issues], elapsed=elapsed)
            return final
        if use_cache:
            self.cache.put(key, result)
        self.audit.record("translation_ok", pipeline=self.pipeline,
                          provider=self.provider.name, model=self.provider.model,
                          request_id=result.request_id, usage=result.usage.__dict__,
                          elapsed=elapsed)
        return result

    def close(self) -> None:
        self.cache.close()
