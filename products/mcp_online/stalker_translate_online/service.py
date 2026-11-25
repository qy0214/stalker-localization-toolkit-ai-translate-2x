# -*- coding: utf-8 -*-
"""在线翻译应用服务：术语、缓存、限流、预算、质量闸门与元数据审计。"""
from __future__ import annotations

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
    TranslationResult,
    pipeline_fingerprint,
    runtime_tokens_match,
)
from stalker_translate_core.pipeline import _PLACEHOLDER_RETRY_NOTE

from .config import Config
from .engine import MODE_ONLINE, BatchStore, OnlineEngine
from .provider import OpenAICompatibleProvider

PLACEHOLDER_RE = re.compile(
    r"\$[A-Za-z_][A-Za-z0-9_]*|%(?:\d+\$)?[sdifgexX%]|%c\[[^\]]*\]|\$\$[^$]+\$\$"
)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 与 Hermes 同款语义方向重试指令（本地常量，不引产品间依赖）。
_SEMANTIC_ROLE_NOTE = ("请核对付款、交付、雇佣与命令方向，严格按源文语义翻译，"
                       "不得反转双方关系。")


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
    """装配层：Provider + 限流/预算/缓存/审计 + 共享全链引擎（按模式）。"""

    def __init__(self, cfg: Config, provider: OpenAICompatibleProvider,
                 glossary: GlossaryIndex, asset_fingerprint: str):
        self.cfg = cfg
        self.provider = provider
        self.glossary = glossary
        self.pipeline = pipeline_fingerprint({
            "core": "1.0.1", "provider": provider.name, "model": provider.model,
            "assets": asset_fingerprint, "prompt": "online-v2",
        })
        self.limiter = RateLimiter(cfg.rpm, cfg.tpm)
        self.budget = BudgetTracker(cfg)
        self.cache = JsonCache(cfg.cache_path)
        self.audit = Audit(cfg.audit_path)
        self._engines: dict[str, OnlineEngine] = {}
        self._store: BatchStore | None = None

    def get_store(self) -> BatchStore:
        if self._store is None:
            self._store = BatchStore(self.cfg.cache_path / "agent_batches.jsonl")
        return self._store

    def get_engine(self, mode: str = MODE_ONLINE) -> OnlineEngine:
        engine = self._engines.get(mode)
        if engine is None:
            engine = OnlineEngine(self.cfg, self.provider, self.limiter,
                                  self.budget, self.cache, self.audit,
                                  self.pipeline, mode=mode,
                                  batch_store=self.get_store())
            self._engines[mode] = engine
        return engine

    def translate(self, text: str, source_lang: str = "auto",
                  target_lang: str = "chs", context: str = "",
                  use_cache: bool = True) -> TranslationResult:
        """单段文本：直取 → 共享引擎（core 同套提示词与卫生闸门）。"""
        from stalker_translate_core import semantic_role_issues
        from stalker_translate_core.pipeline import _has_translatable_text
        if not text or not text.strip():
            return TranslationResult(text=text, source_text=text,
                                     retained_source=True,
                                     issues=(QualityIssue("empty_source",
                                                          "源文为空"),))
        if len(text) > self.cfg.max_text_chars:
            raise ValueError("文本超过 max_text_chars=%d" % self.cfg.max_text_chars)
        if target_lang == "chs":
            exact = self.glossary.lookup(text)
            if exact is None:
                exact = self._termbase_exact(text)
            if exact is not None and exact != text:
                return TranslationResult(text=exact, source_text=text,
                                         provider="termbase", model="exact")
        engine = self.get_engine(MODE_ONLINE)
        from . import resources
        hints = resources.get_text_index(self.cfg.cache_path).hits(text) or None
        key, prepared = engine.probe(text, source_lang, target_lang,
                                     glossary_hits=hints,
                                     context=context or None)
        if prepared is not None and use_cache:
            self.audit.record("cache_hit", pipeline=self.pipeline,
                              provider=self.provider.name, key=key[:12])
            return TranslationResult(text=prepared, source_text=text,
                                     provider=self.provider.name,
                                     model=self.provider.model, cache_hit=True)
        issues: list[QualityIssue] = []
        before = engine.usage_snapshot()
        out = engine.translate(text, source_lang, target_lang,
                               glossary_hits=hints,
                               context=context or None)
        # 重试梯子（与 2.x 同序）：占位符未保 → 定向重试；整条回显 → 重试。
        if not runtime_tokens_match(text, out):
            out2 = engine.translate(
                text, source_lang, target_lang, glossary_hits=hints,
                context=context or None, extra_sys=_PLACEHOLDER_RETRY_NOTE)
            if runtime_tokens_match(text, out2):
                out = out2
        if out == text and _has_translatable_text(text):
            out2 = engine.translate(
                text, source_lang, target_lang, glossary_hits=hints,
                context=context or None, extra_sys=_PLACEHOLDER_RETRY_NOTE)
            if out2 != text:
                out = out2
        if not runtime_tokens_match(text, out):
            issues.append(QualityIssue("placeholder_mismatch",
                                       "颜色码/占位符未能原样保留"))
        if target_lang == "chs" and out != text:
            role = list(semantic_role_issues(text, out, source_lang))
            if role:
                out2 = engine.translate(
                    text, source_lang, target_lang, glossary_hits=hints,
                    context=context or None, extra_sys=_SEMANTIC_ROLE_NOTE)
                role2 = list(semantic_role_issues(text, out2, source_lang))
                if not role2 and runtime_tokens_match(text, out2):
                    out = out2
                else:
                    issues.extend(role2 or role)
        if target_lang == "chs" and out != text and not CJK_RE.search(out):
            issues.append(QualityIssue("no_target_text", "译文未产生中文"))
        if out == text and _has_translatable_text(text):
            issues.append(QualityIssue("source_echo", "译文与源文相同"))
        after = engine.usage_snapshot()
        usage = TokenUsage(
            input_tokens=max(0, after["input_tokens"] - before["input_tokens"]),
            output_tokens=max(0, after["output_tokens"] - before["output_tokens"]))
        if issues:
            return TranslationResult(text=text, source_text=text,
                                     provider=self.provider.name,
                                     model=self.provider.model, usage=usage,
                                     issues=tuple(issues), retained_source=True)
        return TranslationResult(text=out, source_text=text,
                                 provider=self.provider.name,
                                 model=self.provider.model, usage=usage)

    def _termbase_exact(self, text: str) -> str | None:
        try:
            from . import resources
            return resources.get_termbase().exact_translation(text)
        except Exception:
            return None

    def close(self) -> None:
        self.cache.close()
