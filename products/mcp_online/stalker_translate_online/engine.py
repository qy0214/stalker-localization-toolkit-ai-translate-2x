# -*- coding: utf-8 -*-
"""共享全链接口引擎：实现 OfflineTranslationJob 的 server 契约。

三种模式共用同一提示词装配与请求键——键含完整消息，任何重试指令、上下文
或术语差异都形成不同键，同一请求永远只花一次钱：
- ONLINE（轨道 A）：缓存未命中 → 预算/限流 → Provider HTTP；
- COLLECT（轨道 B 收集遍）：未命中 → 登记 pending 批次并返回空串
  （必被 core 闸门拒收，绝不写出占位错误译文）；
- AGENT（轨道 B 应用遍）：未命中但批次已作答 → 返回批次答案，零 HTTP。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from stalker_translate_core import (
    TokenUsage,
    TranslationResult,
    detect_text_lang,
    glossary_hint_block,
    runtime_tokens_match,
)
from stalker_translate_core.pipeline import (
    TRANSLATION_BATCH_SYS_PROMPT,
    TRANSLATION_SYS_PROMPT,
    TRANSLATION_SYS_PROMPT_EN,
    budget_max_tokens,
    strip_output_wrappers,
)

from .provider import OpenAICompatibleProvider

LANG_FULL = {"chs": "简体中文", "eng": "英语", "rus": "俄语", "ukr": "乌克兰语"}

MODE_ONLINE = "online"
MODE_COLLECT = "collect"
MODE_AGENT = "agent"


def request_key(kind: str, model: str, messages: list[dict[str, str]],
                temperature: float | None, max_tokens: int | None) -> str:
    payload = {"kind": kind, "model": model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class BatchStore:
    """对话 AI 批次存储：JSONL 落盘 + 进程内镜像（线程安全）。"""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.answers: dict[str, dict[str, Any]] = {}
        self.pending: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            if line.strip():
                try:
                    self._index(json.loads(line))
                except ValueError:
                    continue

    def _index(self, row: dict[str, Any]) -> None:
        key = str(row.get("key") or "")
        if not key:
            return
        if row.get("state") == "invalid":
            self.answers.pop(key, None)
            self.pending.pop(key, None)
            return
        if row.get("translation") is not None:
            self.answers[key] = row
            self.pending.pop(key, None)
        elif row.get("state") == "pending":
            self.pending[key] = row

    def record_pending(self, key: str, messages: list[dict[str, str]],
                       kind: str, sources: list[str], meta: dict) -> None:
        with self.lock:
            if key in self.pending or key in self.answers:
                return
            row = {"key": key, "state": "pending", "kind": kind,
                   "messages": messages, "sources": [str(s)[:1200]
                                                     for s in sources],
                   "recorded_at": time.time(), **meta}
            self.pending[key] = row
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def invalidate(self, key: str) -> None:
        with self.lock:
            self.answers.pop(key, None)
            self.pending.pop(key, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"key": key, "state": "invalid"}) + "\n")

    def set_answer(self, key: str, translation: str) -> None:
        with self.lock:
            row = {"key": key, "translation": translation}
            self.answers[key] = row
            self.pending.pop(key, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def answer(self, key: str) -> str | None:
        with self.lock:
            row = self.answers.get(key)
        return str(row["translation"]) if row else None

    def pending_snapshot(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {k: dict(v) for k, v in self.pending.items()}


class OnlineEngine:
    """OfflineTranslationJob 的 server 对象（对齐 Hermes RemoteEngine 契约）。"""

    name = "stalker-translate-online"

    def __init__(self, cfg, provider: OpenAICompatibleProvider, limiter,
                 budget, cache, audit, pipeline: str,
                 mode: str = MODE_ONLINE, batch_store: BatchStore | None = None):
        self.cfg = cfg
        self.provider = provider
        self.limiter = limiter
        self.budget = budget
        self.cache = cache
        self.audit = audit
        self.pipeline = pipeline
        self.mode = mode
        self.store = batch_store or BatchStore(cfg.cache_path /
                                               "agent_batches.jsonl")
        self.model = provider.model
        self.model_name = provider.model
        self.ctx_size = max(512, int(cfg.ctx_size or 8192))
        self.input_tokens = 0
        self.output_tokens = 0
        self.requests = 0
        self.cache_hits = 0
        self._counts_lock = threading.Lock()

    # ── server 契约 ──────────────────────────────────────────────────
    def is_running(self) -> bool:
        return True

    def translate(self, text, src_lang, tgt_lang, glossary_hits=None,
                  timeout_sec=None, max_tokens=None, use_extra=True,
                  extra_sys=None, temperature=None, context=None, style=None):
        system, user, used_max, key = self.key_and_messages(
            text, src_lang, tgt_lang, glossary_hits=glossary_hits,
            extra_sys=extra_sys, context=context, max_tokens=max_tokens,
            temperature=temperature, use_extra=use_extra)
        return self.dispatch("single", key,
                             [{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                             used_max, temperature, [str(text or "")])

    def translate_batch(self, lines, src_lang, tgt_lang, glossary_hits=None,
                        timeout_sec=None, max_tokens=None, temperature=None):
        lines = [str(x) for x in (lines or [])]
        target_name = LANG_FULL.get(tgt_lang, tgt_lang)
        numbered = "\n".join("[%d] %s" % (i + 1, line)
                             for i, line in enumerate(lines))
        system = TRANSLATION_BATCH_SYS_PROMPT
        extra = str(getattr(self.cfg, "prompt_extra", "") or "").strip()
        if extra:
            system += "\n补充要求（优先遵守）：\n" + extra
        user = "把下面的连续对话逐行翻译为%s：\n%s" % (target_name, numbered)
        if glossary_hits:
            block, _used = glossary_hint_block(glossary_hits)
            if block:
                user = block + "\n\n" + user
        used_max = budget_max_tokens(sum(len(x) for x in lines), self.ctx_size)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        return self.dispatch("batch",
                             request_key("batch", self.model, messages,
                                         temperature, used_max),
                             messages, used_max, temperature, lines)

    # ── 键与消息（探测与调用共用同一装配） ──────────────────────────
    def key_and_messages(self, text, src_lang, tgt_lang, glossary_hits=None,
                         extra_sys=None, context=None, max_tokens=None,
                         temperature=None, use_extra=True):
        if src_lang in ("auto", "text"):
            src_lang = detect_text_lang(str(text or "")) or src_lang
        system = (TRANSLATION_SYS_PROMPT_EN if src_lang == "eng"
                  else TRANSLATION_SYS_PROMPT)
        extra = str(getattr(self.cfg, "prompt_extra", "") or "").strip() \
            if use_extra else ""
        if extra:
            system += "\n补充要求（优先遵守）：\n" + extra
        if extra_sys:
            system += "\n补充要求（优先遵守）：\n" + extra_sys
        source_name = LANG_FULL.get(src_lang)
        target_name = LANG_FULL.get(tgt_lang, tgt_lang)
        user = ("将下面的%s文本翻译为%s：\n%s" % (source_name, target_name, text)
                if source_name and src_lang not in ("auto", "text")
                else "将下面文本翻译为%s：\n%s" % (target_name, text))
        if context:
            user = "上下文（仅供理解，不要翻译）：\n%s\n\n%s" % (context, user)
        if glossary_hits:
            block, _used = glossary_hint_block(glossary_hits)
            if block:
                user = block + "\n\n" + user
        used_max = (int(max_tokens) if max_tokens
                    else budget_max_tokens(len(str(text or "")), self.ctx_size))
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        key = request_key("single", self.model, messages, temperature, used_max)
        return system, user, used_max, key

    def probe(self, text, src_lang, tgt_lang, glossary_hits=None,
              extra_sys=None, context="", temperature=None):
        """返回 (key, 现成答案或 None)：service 层报告 cache_hit 用。"""
        _system, _user, _mt, key = self.key_and_messages(
            text, src_lang, tgt_lang, glossary_hits=glossary_hits,
            extra_sys=extra_sys, context=context, temperature=temperature)
        cached = self.cache.get(key)
        if cached is not None:
            return key, cached.text
        return key, self.store.answer(key)

    # ── 核心分发：键 → 缓存 → 批次 → 收集 / HTTP ─────────────────────
    def dispatch(self, kind: str, key: str, messages, max_tokens,
                 temperature, sources) -> str:
        cached = self.cache.get(key)
        if cached is not None:
            self._bump_cache()
            self.audit.record("cache_hit", kind=kind, key=key[:12],
                              pipeline=self.pipeline)
            return cached.text
        answer = self.store.answer(key)
        if answer is not None:
            if kind == "single" and sources and not runtime_tokens_match(
                    sources[0], answer):
                # 批次答案未过占位符卫生检查：作废旧答，回到收集/重试路径。
                self.store.invalidate(key)
                answer = None
            else:
                self.cache.put(key, self._result(answer, TokenUsage(),
                                                 "", kind))
                self._bump_cache()
                self.audit.record("batch_hit", kind=kind, key=key[:12],
                                  pipeline=self.pipeline)
                return answer
        if self.mode in (MODE_COLLECT, MODE_AGENT):
            self.store.record_pending(
                key, messages, kind, sources,
                {"model": self.model, "max_tokens": max_tokens,
                 "temperature": temperature, "pipeline": self.pipeline})
            self.audit.record("batch_pending", kind=kind, key=key[:12])
            return ""
        estimated = max(1, sum(len(m["content"]) for m in messages) // 2)
        self.budget.before_request(estimated)
        self.limiter.wait(estimated + (max_tokens or 1024) // 2)
        started = time.monotonic()
        text, usage, request_id = self.provider.chat(
            messages, max_tokens=max_tokens, temperature=temperature)
        self.budget.consume(usage)
        with self._counts_lock:
            self.requests += 1
            self.input_tokens += usage.input_tokens
            self.output_tokens += usage.output_tokens
        text, wrap_notes = strip_output_wrappers(text,
                                                 sources[0] if sources else "")
        if wrap_notes:
            self.audit.record("translation_unwrapped", kind=kind,
                              key=key[:12], notes=wrap_notes)
        # 仅基础卫生通过的活写入缓存：未过检输出留给 core 闸门重试梯子，
        # 避免坏答案被键命中造成永久污染（换模型后也能自愈）。
        if kind == "batch" or (sources and runtime_tokens_match(
                sources[0], text)):
            self.cache.put(key, self._result(text, usage, request_id, kind))
        else:
            self.audit.record("translation_uncached", kind=kind, key=key[:12],
                              reason="runtime_tokens")
        self.audit.record("translation_ok", kind=kind, key=key[:12],
                          request_id=request_id, usage=usage.__dict__,
                          elapsed=round(time.monotonic() - started, 3))
        return text

    # ── 计数与结果构造 ───────────────────────────────────────────────
    def _bump_cache(self) -> None:
        with self._counts_lock:
            self.cache_hits += 1

    def _result(self, text, usage, request_id, kind):
        return TranslationResult(text=text, source_text="", provider=kind,
                                 model=self.model, request_id=request_id,
                                 usage=usage)

    def usage_snapshot(self) -> dict[str, int]:
        with self._counts_lock:
            return {"requests": self.requests, "cache_hits": self.cache_hits,
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens}

    def health(self) -> dict[str, Any]:
        return self.provider.health()
