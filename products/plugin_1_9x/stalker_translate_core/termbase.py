# -*- coding: utf-8 -*-
"""共享术语库装载、分层、精确查询与保守子串修复。"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping

from .pipeline import looks_like_hard_protected_file

TECHNICAL_EXACT = {
    "ч.": "小时", "ч": "小时", "час": "小时", "часа": "小时",
    "сек": "秒", "сек.": "秒", "м/с": "米/秒", "в/мин": "发/分钟",
    "шт": "个", "шт.": "个", "р.": "卢布", "руб.": "卢布",
    "дБм.": "分贝毫瓦", "дБм": "分贝毫瓦", "м.": "米", "м": "米",
    "RU": "俄语", "FX": "特效",
}
HARD_TERM_CATEGORIES = (
    "阵营与组织", "地点与区域", "变异生物", "物品·武器·装备·神器",
)
DEFAULT_LAYERS = {
    "canon": list(HARD_TERM_CATEGORIES),
    "names": ["人物与军衔"],
    "technical": ["技术缩写"],
    "reference": ["ANTHOLOGY 参考"],
}
_RISKY_LATIN_RE = re.compile(r"^[a-z][a-z0-9._\-/&()]{1,18}$")
_PARTIAL_MODEL_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])(?:[A-Z]+[A-Za-z]*[-/]\d[\w\-/]*"
    r"|\d[\w\-/]*[A-Z]+[A-Za-z]*|[A-Z]{2,})(?![a-z])")


def flatten_glossary(data) -> dict[str, str]:
    """兼容扁平、分类与混合术语表，后出现的同名条目覆盖先出现的。"""
    out: dict[str, str] = {}

    def walk(table, depth=0):
        for key, value in table.items():
            name = str(key).strip()
            if not name or name.startswith("_"):
                continue
            if isinstance(value, dict) and depth < 8:
                walk(value, depth + 1)
            elif not isinstance(value, (dict, list, tuple)):
                out[name] = str(value)

    if isinstance(data, dict):
        walk(data)
    return out


def load_builtin_glossary(paths: Iterable[str]) -> dict[str, str]:
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return flatten_glossary(json.load(handle))
        except Exception:
            continue
    return {}


class Termbase:
    def __init__(self, path: str, builtin_paths: Iterable[str] = (),
                 technical_exact: Mapping[str, str] | None = None):
        self.path = path
        self.builtin_paths = tuple(builtin_paths)
        self.technical_exact = dict(TECHNICAL_EXACT if technical_exact is None else technical_exact)
        self._data_cache = {"key": None, "data": {}}
        self._low_index = {"key": None, "low": {}}
        self._builtin_cache = {"key": None, "data": {}, "low": {}}
        self._partial_cache = {"key": None, "words": []}

    @staticmethod
    def _file_key(path: str):
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def load_data(self) -> dict:
        key = self._file_key(self.path)
        if key is None:
            return {}
        if self._data_cache.get("key") == key:
            return self._data_cache.get("data") or {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        self._data_cache = {"key": key, "data": data}
        return data

    def load_builtin(self) -> dict[str, str]:
        key = tuple((path, self._file_key(path)) for path in self.builtin_paths)
        if self._builtin_cache.get("key") != key:
            data = load_builtin_glossary(self.builtin_paths)
            self._builtin_cache = {
                "key": key,
                "data": data,
                "low": {str(k).casefold(): str(v) for k, v in data.items()},
            }
        return self._builtin_cache.get("data") or {}

    @staticmethod
    def layer_categories(meta, name: str) -> list[str]:
        layers = meta.get("layers") if isinstance(meta, dict) else None
        categories = layers.get(name) if isinstance(layers, dict) else None
        if isinstance(categories, str):
            categories = [categories]
        if not isinstance(categories, list):
            categories = DEFAULT_LAYERS.get(name, [])
        return [str(category) for category in categories if str(category).strip()]

    def load_layers(self) -> dict:
        data = self.load_data()
        meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}

        def collect(categories):
            keys, table = set(), {}
            for category in categories:
                entries = data.get(category)
                if not isinstance(entries, dict):
                    continue
                for key, value in entries.items():
                    name = str(key).strip()
                    if not name or name.startswith("_"):
                        continue
                    keys.add(name)
                    table[name] = str(value)
            return keys, table

        canon, _ = collect(self.layer_categories(meta, "canon"))
        names, _ = collect(self.layer_categories(meta, "names"))
        _, technical = collect(self.layer_categories(meta, "technical"))
        _, reference = collect(self.layer_categories(meta, "reference"))
        raw_variants = meta.get("variants")
        variants = ({str(k): str(v) for k, v in raw_variants.items()
                     if str(k).strip() and str(v).strip()}
                    if isinstance(raw_variants, dict) else {})
        explicit = meta.get("case_sensitive")
        case_sensitive = ({str(k) for k in explicit if str(k).strip()}
                          if isinstance(explicit, list) else set())
        case_sensitive |= names
        case_sensitive |= {k for k in technical if re.search(r"[A-Za-z]", k)}
        case_sensitive |= {k for k in canon if _RISKY_LATIN_RE.match(k)}
        quote_required = meta.get("quote_required")
        quote_required = ({str(k) for k in quote_required if str(k).strip()}
                          if isinstance(quote_required, list) else set())
        return {
            "canon": canon,
            "names": names,
            "technical": technical,
            "reference": reference,
            "variants": variants,
            "case_sensitive": case_sensitive,
            "quote_required": quote_required,
        }

    def lookup(self, text: str) -> str | None:
        raw = (text or "").strip()
        stripped = raw.strip('"\u00ab\u00bb\u201c\u201d\'')
        if not raw:
            return None
        candidates = []
        for form in (raw, stripped):
            if not form:
                continue
            for candidate in (form, form.casefold()):
                if candidate not in candidates:
                    candidates.append(candidate)
        key = self._file_key(self.path)
        if self._low_index.get("key") != key:
            index = {}
            for category, table in self.load_data().items():
                if category.startswith("_") or not isinstance(table, dict):
                    continue
                for term, value in table.items():
                    lowered = str(term).casefold()
                    if lowered not in index:
                        index[lowered] = str(value)
            self._low_index = {"key": key, "low": index}
        index = self._low_index.get("low") or {}
        for candidate in candidates:
            if candidate in index:
                return index[candidate]
        builtin = self.load_builtin()
        lowered_builtin = self._builtin_cache.get("low") or {}
        for candidate in candidates:
            if candidate in builtin:
                return str(builtin[candidate])
            if candidate in lowered_builtin:
                return lowered_builtin[candidate]
        return None

    def exact_translation(self, text: str) -> str | None:
        value = self.lookup(text)
        if value is None:
            return None
        source = (text or "").strip()
        quotes = "\"'«»“”‘’"
        if (len(source) >= 2 and source[0] in quotes and source[-1] in quotes
                and len(value) >= 2 and value[0] in quotes and value[-1] in quotes):
            value = value[1:-1]
        lead = text[:len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        return lead + value + trail

    def partial_translation(self, source: str, rel: str | None = None) -> str | None:
        text = (source or "").strip()
        if not text or len(text) > 60:
            return None
        if rel is not None and looks_like_hard_protected_file(rel):
            return None
        data = self.load_data()
        meta_layers = (data.get("_meta") or {}).get("layers") or {}
        key = self._file_key(self.path)
        if self._partial_cache.get("key") != key:
            layers = set(meta_layers.get("canon") or []) | set(meta_layers.get("names") or [])
            words = []
            for category, table in data.items():
                if (category.startswith("_") or not isinstance(table, dict)
                        or category not in layers):
                    continue
                for term, value in table.items():
                    term = str(term)
                    if len(term) < 3 or not re.search(r"[A-Za-z]", term):
                        continue
                    if not (term[:1].isupper() or term.isupper()):
                        continue
                    words.append((term, str(value)))
            self._partial_cache = {"key": key, "words": words}
        out = text
        changed = False
        for term, value in self._partial_cache.get("words") or []:
            if term in out:
                replaced = re.sub(rf"(?<!\w){re.escape(term)}(?!\w)", value, out)
                if replaced != out:
                    out = replaced
                    changed = True
        if not changed:
            return None
        for word in re.findall(r"[A-Za-z]{3,}", _PARTIAL_MODEL_TOKEN_RE.sub(" ", out)):
            if any(char.islower() for char in word):
                return None
        return out

    def hard_keys(self) -> set[str]:
        return set(self.load_layers()["canon"])

    def technical(self) -> dict[str, str]:
        result = dict(self.technical_exact)
        result.update(self.load_layers()["technical"])
        return result
