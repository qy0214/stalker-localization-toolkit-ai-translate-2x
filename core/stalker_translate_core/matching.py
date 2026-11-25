# -*- coding: utf-8 -*-
"""共享术语匹配：变体、大小写、词边界与残留修复。"""
from __future__ import annotations

import re

from .pipeline import _CYR_WORD_RE, has_cjk


def _is_word_char(ch):
    """术语两侧的"词字符"判定：西里尔/拉丁按词边界锚定，CJK 视为边界。

    为什么要区分（原实现只用正则 \\w，两者都没分开）：
      * 西里尔/拉丁文字有词的概念，术语两侧必须锚定词边界，否则
        "No" 会误伤 "Now"、"Кот" 会误伤 "Который"；
      * 中文没有词间空格，而 Python 的 \\w 把汉字也算词字符 —— 于是术语表里
        任何中文条目在中文文本里都会因为"左右都是词字符"而永不命中。
        把 CJK 当边界即可命中（如术语「神器」在「一个神器装备」中）。
    """
    if not (ch.isalnum() or ch == "_"):
        return False
    o = ord(ch)
    if (0x3400 <= o <= 0x9FFF
            or 0x3040 <= o <= 0x30FF
            or 0xAC00 <= o <= 0xD7AF):
        return False
    return True


class GlossaryIndex(object):
    """术语匹配索引：首字符分桶 + 同位置长词优先 + 非重叠单向扫描。

    为什么不用 "|".join(...) 的巨型交替正则：实测 8208 条术语拼出的
    204 KB 正则单条 150 字符文本要 43.70 ms，而同样的数据用朴素子串
    扫描只要 1.22 ms（慢 36 倍）；两万条量级的任务上就是几十分钟纯 CPU
    空转，GPU 全程在等。分桶后每个字符只与"以该字符开头"的词条比较，
    比较次数降两个数量级，匹配语义与原正则逐字符一致（有等价性属性测试）。

    v2 增补：
    * `variants`：拼写变体映射到规范词条，避免 ё/е 两个译名打架；
    * `case_sensitive`：人物名/技术缩写/小写单单词拉丁硬词要求首字母
      大小写一致（大写专名仍命中，句子里的普通名词不再误伤）；
    * 查找时统一做 ё→е 折叠，俄文里漏写两点的名字也能命中规范词条。
    """

    def __init__(self, mapping, case_sensitive=None, variants=None):
        raw = {}
        for k, v in (mapping or {}).items():
            name = str(k)
            if name.strip():
                raw[name] = str(v)
        self._case_sensitive = {str(k) for k in (case_sensitive or ())}
        var = {str(k): str(v) for k, v in (variants or {}).items()
               if str(k).strip() and str(v).strip()}

        # 变体只做别名，不单独成为规范条目：否则硬词过滤会拿到变体键，
        # 把“当前 MOD 专名”误判成软词。
        self._map = {}
        # 规范词条自身的译文优先；变体只在规范词条缺席时补位。
        for key, value in raw.items():
            if key not in var or var[key] == key:
                self._map[key] = value
        for key, value in raw.items():
            canon = var.get(key, key)
            if canon in raw:
                self._map.setdefault(canon, value)
            else:
                self._map.setdefault(key, value)
        self._alias = {}
        for key in raw:
            canon = var.get(key, key)
            if canon not in self._map:
                canon = key
            self._alias.setdefault(self._fold(key), set()).add(canon)

        self._by2 = {}
        self._by1 = {}
        for key in self._map:
            kl = self._fold(key)
            if len(key) == 1:
                t = self._by1.setdefault(kl, self._new_table())
            else:
                t = self._by2.setdefault(kl[:2], self._new_table())
            L = len(key)
            t["e"].setdefault(L, set()).add(key)
            for alias in self._alias.get(kl, (key,)):
                if len(alias) != L:
                    continue
                t["c"].setdefault(L, {}).setdefault(
                    self._fold(alias), []).append(key)
        for t in list(self._by1.values()) + list(self._by2.values()):
            t["lens"] = sorted(t["e"].keys(), reverse=True)

    @staticmethod
    def _new_table():
        return {"e": {}, "c": {}, "lens": []}

    @staticmethod
    def _fold(s):
        return s.casefold().replace("ё", "е")

    def _case_ok(self, seg, key):
        """大小写敏感词条：全大写缩写必须逐字一致，其余只要求首字母一致。

        首字母一致而不是全等，是为了让 `Гришачев`/`Гришачёв`、
        `Санёк`/`Санек` 这类拼写变体仍能命中；`crystal` 与 `Crystal`
        的首字母不同，正好被挡在正文普通名词之外。
        """
        if key not in self._case_sensitive:
            return True
        if key.isupper():
            return seg == key
        if key[:1].islower():
            # 小写单单词 canon（bar/crystal/star）只认大写专名：
            # 正文里的普通名词保持原样，交给模型按语境处理。
            return bool(seg) and seg[:1].isupper()
        return bool(seg) and bool(key) and seg[0] == key[0]

    def __len__(self):
        return len(self._map)

    def _probe(self, table, text, i, low):
        """按"位置升序 + 长词优先 + 同长度精确优先"取命中，返回 key 或 None。"""
        if not table:
            return None
        n = len(text)
        for L in table["lens"]:
            end = i + L
            if end > n:
                continue
            seg = text[i:end]
            key = None
            pool = table["e"].get(L)
            if pool and seg in pool:
                key = seg
            else:
                segl = low[i:end] if low is not None else self._fold(seg)
                cand = table["c"].get(L, {}).get(segl)
                if cand:
                    cand = list(dict.fromkeys(cand))
                    exact = [k for k in cand if k[:1] == seg[:1]]
                    if exact:
                        cand = exact
                    key = max(cand, key=lambda k: sum(
                        1 for a, b in zip(seg, k) if a == b))
            if key is None:
                continue
            if not self._case_ok(seg, key):
                continue
            if i and _is_word_char(text[i - 1]):
                continue
            if end < n and _is_word_char(text[end]):
                continue
            return key
        return None

    def find(self, text):
        """返回 [(start, end, key)]：按位置升序、同位置最长优先、互不重叠。"""
        out = []
        if not text or not self._map:
            return out
        n = len(text)
        low = self._fold(text)
        if len(low) != n:
            # 极少数字符 casefold 后长度变化（如 ß→ss）：退回逐段折叠，
            # 保证位置与原文一一对应。
            low = None
        i = 0
        while i < n:
            p2 = low[i:i + 2] if low is not None else self._fold(
                text[i:i + 2])
            key = self._probe(self._by2.get(p2), text, i, low)
            if key is None and self._by1:
                key = self._probe(self._by1.get(p2[:1]), text, i, low)
            if key is None:
                i += 1
                continue
            end = i + len(key)
            out.append((i, end, key))
            i = end
        return out

    def hits(self, text):
        """命中的 (原文, 译文)，长词优先 —— 与旧 _glossary_hits 同序。"""
        pairs = [(key, self._map[key]) for _, _, key in self.find(text)]
        pairs.sort(key=lambda kv: len(kv[0]), reverse=True)
        return pairs


def inside_quotes(text, start, end):
    for opening, closing in (("«", "»"), ("“", "”"), ("‘", "’")):
        if (text.rfind(opening, 0, start) > text.rfind(closing, 0, start)
                and text.find(closing, end) != -1):
            return True
    for quote in ('"', "'"):
        if text.count(quote, 0, start) % 2 == 1 and text.find(quote, end) != -1:
            return True
    return False


def inline_term_value(text, start, end, value):
    value = str(value)
    quotes = "\"'«»“”‘’"
    if (start > 0 and end < len(text)
            and text[start - 1] in quotes and text[end] in quotes
            and len(value) >= 2 and value[0] in quotes and value[-1] in quotes):
        return value[1:-1]
    return value


def span_context_ok(text, start, end, key, quote_required):
    return key not in quote_required or inside_quotes(text, start, end)


def glossary_spans(text, index, hard=None, quote_required=()):
    if index is None:
        return []
    spans = [hit for hit in index.find(text)
             if span_context_ok(text, hit[0], hit[1], hit[2], quote_required)]
    if hard is None:
        return spans
    return [hit for hit in spans if hit[2] in hard]


def glossary_hits(text, index, hard=None, names=(), soft=(), session_keys=(),
                  quote_required=()):
    if index is None:
        return []
    spans = index.find(text)
    pairs = [(key, index._map[key]) for start, end, key in spans
             if span_context_ok(text, start, end, key, quote_required)]
    if hard is None:
        return pairs
    hard_pairs = [(key, value) for key, value in pairs
                  if key in hard or key in session_keys]
    used = {key for key, _value in hard_pairs}
    name_pairs = [(key, value) for key, value in pairs
                  if key in names and key not in used]
    used |= {key for key, _value in name_pairs}
    soft_pairs = [(key, value) for key, value in pairs
                  if key in soft and key not in used and len(key) <= 32]
    return hard_pairs + name_pairs + soft_pairs


def repair_residual_source_terms(src, out, index, mapping, hard=None,
                                 quote_required=(), detail=None):
    if not src or not out or index is None:
        return out
    spans = glossary_spans(src, index, hard=hard, quote_required=quote_required)
    quotes = "\"'«»“”‘’"
    for start, end, key in spans:
        term = src[start:end]
        value = mapping.get(key)
        if not term or value is None:
            continue
        if not _CYR_WORD_RE.search(term):
            continue
        if re.search(r"[\\/]", term) or re.search(r"\.[A-Za-z0-9]{1,5}$", term):
            continue
        value = str(value)
        if (inside_quotes(src, start, end) and len(value) >= 2
                and value[0] in quotes and value[-1] in quotes):
            value = value[1:-1]
        if not value or value == term or term not in out:
            continue
        pattern = re.compile(
            r"(?<![А-Яа-яЁё])%s(?![А-Яа-яЁё])" % re.escape(term))
        replaced = pattern.sub(lambda _match: value, out)
        if replaced != out:
            if detail:
                detail("  残留源词按词库替换：%s → %s" % (term, value))
            out = replaced
    return out


def repair_residual_latin_names(src, out, index, mapping, names, detail=None):
    if not src or not out or index is None or not names:
        return out
    spans = index.find(src)
    quotes = "\"'«»“”‘’"
    for start, end, key in spans:
        term = src[start:end]
        if len(term) < 4:
            continue
        if not re.fullmatch(r"[A-Za-z][A-Za-z'’\- ]*", term):
            continue
        if not (term[0].isupper() or " " in term
                or (key[:1] and key[0].islower())):
            continue
        value = mapping.get(key)
        if value is None:
            continue
        value = str(value)
        if not has_cjk(value) or value == term:
            continue
        if (inside_quotes(src, start, end) and len(value) >= 2
                and value[0] in quotes and value[-1] in quotes):
            value = value[1:-1]
        pattern_text = (r"(?<![A-Za-z])%s(?:s|’s|'s)?(?![A-Za-z])"
                        % re.escape(term))
        if not re.search(pattern_text, out, re.I):
            continue
        replaced = re.sub(pattern_text, lambda _match: value, out, flags=re.I)
        if replaced != out:
            if detail:
                detail("  残留英文专名按词库替换：%s → %s" % (term, value))
            out = replaced
    return out
