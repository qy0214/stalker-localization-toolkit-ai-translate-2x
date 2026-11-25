# -*- coding: utf-8 -*-
"""术语库归一化与无副作用查询。"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .pipeline import (
    GLOSSARY_HINT_HEADER,
    GLOSSARY_HINT_HEADER_OFFICIAL,
    looks_like_model_name,
)
from .termbase import flatten_glossary as flatten_glossary_overwrite


_QUOTE_CHARS = "\"'«»“”‘’"


def flatten_glossary(data: Mapping[str, Any]) -> dict[str, str]:
    """兼容扁平、分类与混合 JSON；分类顺序即覆盖优先级。"""
    flat: dict[str, str] = {}

    def visit(node: Mapping[str, Any]) -> None:
        for key, value in node.items():
            if str(key).startswith("_"):
                continue
            if isinstance(value, Mapping):
                visit(value)
            elif value is not None:
                flat.setdefault(str(key), str(value))

    visit(data)
    return flat


def load_termbase_document(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("术语库顶层必须是对象")
    return data


def layer_counts(document: Mapping[str, Any]) -> dict[str, int]:
    layers = (document.get("_meta") or {}).get("layers") or {}
    result: dict[str, int] = {}
    for layer, categories in layers.items():
        result[str(layer)] = sum(
            len(document.get(category) or {})
            for category in categories
            if isinstance(document.get(category), Mapping)
        )
    return result


def load_glossary_json(path: str | Path) -> dict[str, str]:
    return flatten_glossary(load_termbase_document(path))


@dataclass(frozen=True)
class GlossaryIndex:
    exact: Mapping[str, str]
    lowered: Mapping[str, str]

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "GlossaryIndex":
        exact = dict(mapping)
        lowered: dict[str, str] = {}
        for key, value in exact.items():
            lowered.setdefault(key.casefold(), value)
        return cls(exact=exact, lowered=lowered)

    @classmethod
    def from_json(cls, path: str | Path) -> "GlossaryIndex":
        return cls.from_mapping(load_glossary_json(path))

    def lookup(self, text: str) -> str | None:
        raw = (text or "").strip()
        if not raw:
            return None
        stripped = raw.strip(_QUOTE_CHARS)
        candidates = (raw, raw.casefold(), stripped, stripped.casefold())
        for candidate in candidates:
            if candidate in self.exact:
                return self.exact[candidate]
            if candidate in self.lowered:
                return self.lowered[candidate]
        return None

    def hits(self, text: str, limit: int = 32) -> tuple[tuple[str, str], ...]:
        """返回文本内完整词条命中；长词优先，限制结果数量。"""
        if not text or limit <= 0:
            return ()
        found: list[tuple[int, str, str]] = []
        for key, value in self.exact.items():
            if len(key) < 3 or key not in text:
                continue
            start = text.find(key)
            found.append((start, key, value))
        found.sort(key=lambda row: (-len(row[1]), row[0]))
        return tuple((key, value) for _start, key, value in found[:limit])


def normalized_key(text: str) -> str:
    return (text or "").strip().strip(_QUOTE_CHARS).casefold()


def is_symbol_only(text: str) -> bool:
    return not bool(re.search(r"[0-9A-Za-zА-Яа-яЁё\u4e00-\u9fff]", text or ""))


_SESSION_TERM_NOTE = (
    "这是游戏名词表。逐行译成简体中文，保持行数与顺序；"
    "人名音译，绰号/物品/地点按游戏习惯意译；"
    "不要编号、不要解释、不要保留俄文或英文原文。")

_SESSION_QUOTES = "\"'«»“”‘’"

_SESSION_NAME_DIRS = ("character_name", "snames", "fnames", "bio")

_SESSION_ITEM_DIRS = ("item", "weapon", "equipment", "artefact",
                      "artifact", "arsenal")

_SESSION_LOC_DIRS = ("mapspot", "level", "place", "zone")

_SESSION_SKIP_DIRS = ("dialog", "news", "task", "tip", "stash", "secret",
                      "credits", "ui", "gameplay", "spawn", "npc")

_SESSION_LIMITS = {"人物与军衔": 1500, "物品·武器·装备·神器": 500,
                   "地点与区域": 400, "阵营与组织": 150, "变异生物": 100,
                   # ★ 2.0.11 成就名/声望等级/武器属性短标签：模型单条常原样
                   # 回显，纳入会话词表批量定译（批量带"不得保留英文"提示）。
                   "成就·称号·属性标签": 300}

def session_category(rel, eid):
    """会话词表只收“名字型”条目；对话框/新闻/任务/署名不碰。"""
    s = (rel or "").lower().replace("\\", "/")
    k = (eid or "").lower()
    if any(x in s for x in _SESSION_SKIP_DIRS):
        return None
    if any(x in s for x in _SESSION_NAME_DIRS):
        if k.endswith(("_bio", "_descr", "_desc", "_text", "_hint")):
            return None
        if k.endswith("_name") or "snames" in s or "fnames" in s:
            return "人物与军衔"
        return None
    if any(x in s for x in ("faction", "community")):
        return "阵营与组织"
    # ★ 2.0.11 玩家可见短标签：成就名、声望/等级、武器属性。
    # 必须放在 _SESSION_ITEM_DIRS 之前（gunsl_up_binoc 等含 weapon 字样的
    # 属性表优先归到标签类，模型原样回显时由会话词表批量定译）。
    if any(x in s for x in ("achievement", "game_relations", "binoc",
                            "reputation", "rank")):
        return "成就·称号·属性标签"
    if any(x in s for x in _SESSION_ITEM_DIRS):
        if k.endswith(("_descr", "_desc", "_bio", "_text", "_hint")):
            return None
        return "物品·武器·装备·神器"
    if any(x in s for x in ("creature", "mutant", "monster")):
        return "变异生物"
    if any(x in s for x in _SESSION_LOC_DIRS):
        return "地点与区域"
    return None

def session_term_shape(src, cat):
    """名词形状：短、无句末标点；人名允许缩写点。"""
    s = (src or "").strip().strip(_SESSION_QUOTES).strip()
    if not s or len(s) > 60 or "\n" in s or "\r" in s:
        return ""
    words = s.split()
    if not (1 <= len(words) <= 5):
        return ""
    if cat == "人物与军衔":
        if re.search(r"[。！？!?；;:]", s):
            return ""
        if re.match(r"^[А-ЯЁA-Z]\.\s*[А-ЯЁA-Z]?\.?\s*[А-ЯЁA-Z]", s):
            return s
        if s[0].isupper():
            return s
        return ""
    if re.search(r"[.!?。！？；;:，,]", s):
        return ""
    if not re.search(r"[А-Яа-яЁёA-Za-z]", s):
        return ""
    if cat == "物品·武器·装备·神器" and looks_like_model_name(s):
        return ""
    return s

GLOSSARY_HINT_BUDGET = 240

GLOSSARY_HINT_STOPWORDS = frozenset((
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "нет", "есть", "был", "была", "были", "быть", "это", "этот",
    "для", "или", "если", "уже", "ещё", "еще", "тут", "там", "тогда", "чем",
    "кто", "где", "когда", "почти", "всего", "всегда", "очень", "надо",
    "можно", "нужно", "теперь", "потом", "себя", "себе", "свой", "свою",
    "один", "два", "три", "без", "под", "над", "про", "при", "между",
)) | frozenset((
    # 英语源文同样要过滤功能词，否则术语提示预算会被 the/a/of 吃掉。
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "and", "or",
    "is", "are", "was", "were", "be", "been", "being", "with", "by",
    "from", "this", "that", "these", "those", "it", "its", "as", "not",
    "no", "yes", "you", "your", "yours", "he", "she", "they", "we", "i",
    "me", "my", "our", "their", "his", "her", "them", "us", "do", "does",
    "did", "can", "could", "will", "would", "should", "may", "might",
    "must", "have", "has", "had", "so", "but", "if", "then", "than",
    "also", "very", "just", "there", "here", "what", "which", "who",
    "whom", "whose", "when", "where", "why", "how", "all", "any", "some",
))

def glossary_hint_block(pairs, budget=GLOSSARY_HINT_BUDGET, official=False):
    """把命中术语压成尽可能小的提示块，返回 (文本, 计费字符数)。

    计费字符数是**保守上界**（每行按含换行计、另预留与正文之间的空行），
    略大于实际块长；预算判断因此偏安全，不会算少。

    规则：跳过功能词、大小写去重、按预算截断；超预算的长术语直接跳过而不是
    截半（半截术语反而会误导模型）。实测把原来的 15 条上限换成 240 字符预算
    后，每条请求少注入约 25% 字符，且长专名不再被短词挤掉。

    official=True 用官方模板措辞（「X 翻译成 Y」），False 用自研格式（X=Y）。
    """
    seen = set()
    lines = []
    header = (GLOSSARY_HINT_HEADER_OFFICIAL if official
              else GLOSSARY_HINT_HEADER)
    used = len(header) + 2
    fmt = "%s 翻译成 %s" if official else "%s=%s"
    for k, v in pairs:
        k = str(k).strip()
        lk = k.lower()
        if not k or lk in seen or lk in GLOSSARY_HINT_STOPWORDS:
            continue
        v = str(v)
        cost = len(k) + len(v) + (4 if official else 2)
        if used + cost > budget:
            continue
        seen.add(lk)
        lines.append(fmt % (k, v))
        used += cost
    if not lines:
        return "", 0
    return header + "\n".join(lines), used


USER_GLOSSARY_FORMAT = "ai-translate-glossary-user/1"


class UserGlossaryStore:
    def __init__(self, path: str, builtin_loader, layer_format: str = USER_GLOSSARY_FORMAT):
        self.path = path
        self.builtin_loader = builtin_loader
        self.layer_format = layer_format
        self._lock = threading.Lock()

    def read_layer(self, builtin=None):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return {}, set(), False
        if isinstance(data, dict) and data.get("_format") == self.layer_format:
            raw_set = data.get("set") if isinstance(data.get("set"), dict) else {}
            set_map = {str(k): str(v) for k, v in raw_set.items()
                       if str(k).strip() and not str(k).startswith("_")}
            try:
                deleted = {str(k) for k in (data.get("del") or [])
                           if str(k).strip()}
            except TypeError:
                deleted = set()
            return set_map, deleted, bool(data.get("clear_builtin"))
        flat = flatten_glossary_overwrite(data) if isinstance(data, dict) else {}
        base = self.builtin_loader() if builtin is None else builtin
        return {k: v for k, v in flat.items() if base.get(k) != v}, set(), False

    def layers(self):
        builtin = self.builtin_loader()
        set_map, deleted, clear = self.read_layer(builtin)
        merged = {} if clear else dict(builtin)
        for key in deleted:
            merged.pop(key, None)
        merged.update(set_map)
        return merged, set_map, deleted, clear

    def save(self, set_map, deleted=None, clear_builtin=False):
        payload = json.dumps({
            "_format": self.layer_format,
            "clear_builtin": bool(clear_builtin),
            "set": dict(set_map or {}),
            "del": sorted(deleted or ()),
        }, ensure_ascii=False, indent=1, sort_keys=True)
        with self._lock:
            temporary = self.path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temporary, self.path)


def clean_term_line(line):
    s = (line or "").strip()
    s = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", s)
    return s.strip().strip("\"'“”‘’` ").strip()


_FREQ_NAME_MIN_TOTAL = 5    # 全文出现 ≥5 次才候选

_FREQ_NAME_MIN_MID = 2      # 其中句中位置（前随空白/标点）≥2 次（滤句首普通词）

_FREQ_NAME_LIMIT = 64       # 按频次取 top-N，控预算

_FREQ_SCAN_FILE_CAP = 4000  # 解析文件数上限，防超大 MOD 拖慢启动

_FREQ_WORD_RE = re.compile(r"[А-ЯЁ][а-яё]{2,}")

_FREQ_MID_RE = re.compile(
    r"(?<=[\s«„\"'(，。！？…：；—])[А-ЯЁ][а-яё]{2,}")
