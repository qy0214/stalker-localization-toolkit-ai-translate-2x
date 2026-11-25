# -*- coding: utf-8 -*-
"""共享文本处理：确定性句式、占位符安全切分。"""
from __future__ import annotations

import re

from .pipeline import (
    _CLAUSE_SPLIT,
    _RUNTIME_TOKEN_RE,
    _SENT_SPLIT,
    unescape_newlines,
)


_NEWS_PATTERNS = (
    # (正则, 模板)  —— 组1是名词（含可选冠词/复数由正则吃掉）
    (re.compile(r"^We (?:were|got) (?:attacked|ambushed) by (?:a |an |some )?(.+?)[.!]?$", re.I),
     "我们被%s袭击了。"),
    (re.compile(r"^(?:A |An |The )?(.+?) ambushed us[.!]?$", re.I),
     "%s伏击了我们。"),
    (re.compile(r"^(?:A |An |The )?(.+?) attacked us[.!]?$", re.I),
     "%s袭击了我们。"),
    (re.compile(r"^We (?:ran into|encountered|met) (?:a |an |some )?(.+?)[.!]?$", re.I),
     "我们遇到了%s。"),
    (re.compile(r"^We got attacked by (?:a |an |some )?(.+?)[.!]?$", re.I),
     "我们被%s袭击了。"),
)


def pattern_translate(src, lookup):
    """通用句式 + 词库名词 → 确定性译文；无法覆盖返回 None。

    lookup: 单调函数 term -> 译名 或 None（由调用方注入词库查询）。
    """
    s = (src or "").strip()
    if not s or not s.isascii():
        return None
    for pat, tpl in _NEWS_PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        noun = m.group(1).strip()
        zh = lookup(noun)
        if not zh:
            return None
        return tpl % zh
    return None


def split_protected(text, max_chars, pattern):
    """按 split_long_text 切分，但**绝不切断 pattern 的匹配**。

    强制术语模式下原文被换成 $$T1$$ 这类占位符；按字符硬切若切在占位符中间，
    模型会看到半截标记，回填必然失败并退回旧的切片做法（质量下降且难排查）。
    做法：先把每个匹配替换成一个私有区单字符，切分后再还原 —— 匹配永远完整。
    """
    spans = list(pattern.finditer(text))
    if not spans or len(spans) > 5000:
        return split_long_text(text, max_chars)
    if any(0xE000 <= ord(c) <= 0xF8FF for c in text):
        # 源文本已含私有区字符时不能用 PUA 做哨兵（会被误还原）；
        # 退回普通切分：占位符可能被切断，但 _translate_force 会校验并回退，
        # 不会产出坏译文。
        return split_long_text(text, max_chars)
    mapping = {}

    def repl(m):
        ch = chr(0xE000 + len(mapping))
        mapping[ch] = m.group(0)
        return ch

    protected = pattern.sub(repl, text)
    parts = split_long_text(protected, max_chars)
    if not mapping:
        return parts
    out = []
    for part in parts:
        for ch, orig in mapping.items():
            if ch in part:
                part = part.replace(ch, orig)
        out.append(part)
    return out


def split_long_text(text, max_chars):
    """把超长文本切成 ≤ max_chars 的段：先按句末标点（。！？!?…换行；分号），
    再按从句标点（逗号等），最后硬切。分隔符随前文保留，绝不丢字、绝不截断。"""
    if len(text) <= max_chars:
        return [text]

    def split_keep(pattern, s):
        """按捕获组切分，分隔符并回前一片段。"""
        parts = pattern.split(s)
        units, buf = [], ""
        for i, piece in enumerate(parts):
            buf += piece
            if i % 2 == 1:
                if buf:
                    units.append(buf)
                buf = ""
        if buf:
            units.append(buf)
        return units

    def hard_wrap(s, limit):
        return [s[i:i + limit] for i in range(0, len(s), limit)]

    units = split_keep(_SENT_SPLIT, text)
    fine = []
    for u in units:
        if len(u) <= max_chars:
            fine.append(u)
            continue
        for clause in split_keep(_CLAUSE_SPLIT, u):
            fine.extend(hard_wrap(clause, max_chars))

    chunks, cur = [], ""
    for u in fine:
        if len(cur) + len(u) <= max_chars:
            cur += u
        else:
            if cur:
                chunks.append(cur)
            cur = u
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


def tm_norm(text):
    normalized = unescape_newlines(str(text or "")).strip()
    return re.sub(r"[ \t]+", " ", normalized)


def tm_key(text, lang, normalizer=None):
    normalize = normalizer or tm_norm
    source = normalize(text)
    signature = (tuple(sorted(_RUNTIME_TOKEN_RE.findall(source))),
                 source.count("\n"))
    return str(lang), len(source), signature, source


def tm_measure(values, normalizer=None):
    normalize = normalizer or tm_norm
    seen = set()
    total = 0
    for value in values:
        source = normalize(value)
        if len(source) < 4:
            continue
        total += 1
        seen.add(source)
    unique = len(seen)
    return {"total": total, "unique": unique,
            "ratio": (1.0 - float(unique) / total) if total else 0.0}


def tm_should_enable(stats, enabled=True, min_entries=40, min_ratio=0.08):
    total = int(stats.get("total") or 0)
    unique = int(stats.get("unique") or 0)
    ratio = stats.get("ratio")
    if ratio is None:
        ratio = (1.0 - float(unique) / total) if total else 0.0
    return enabled and total >= min_entries and ratio >= min_ratio
