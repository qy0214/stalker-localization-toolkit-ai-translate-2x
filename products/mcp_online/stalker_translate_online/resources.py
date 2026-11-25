# -*- coding: utf-8 -*-
"""共享资源装配：X-Ray 解析器、分层词库与用户词表（进程内缓存）。"""
from __future__ import annotations

from pathlib import Path

from stalker_translate_core import GlossaryIndex, Termbase, UserGlossaryStore
from stalker_translate_core.assets import resolve_asset_root
from stalker_translate_core.vendor import load_vendor

_CACHE: dict[str, object] = {}


def asset_root() -> Path:
    return resolve_asset_root(
        Path(__file__).resolve().parent.parent / "__init__.py", "assets")


def get_parser():
    """vendored xRayXmlParser（与 2.x 插件、Hermes 同一份资产与加载链）。

    解析器来源: wzyddg/stalker_auto-trans-tool_remake (license: null,
    见 THIRD_PARTY_NOTICES.md 第 1 节)。
    """
    if "parser" not in _CACHE:
        _CACHE["parser"] = load_vendor(str(asset_root() / "xray_pkg.zip"))
    return _CACHE["parser"]


def get_termbase() -> Termbase:
    if "termbase" not in _CACHE:
        root = asset_root()
        _CACHE["termbase"] = Termbase(str(root / "stalker_termbase.json"),
                                      (str(root / "glossary_builtin.json"),))
    return _CACHE["termbase"]


def get_user_gloss(data_dir):
    key = "user_gloss:%s" % data_dir
    if key not in _CACHE:
        _CACHE[key] = UserGlossaryStore(
            str(Path(data_dir) / "glossary_user.json"),
            get_termbase().load_builtin)
    return _CACHE[key]


def get_merged_gloss(data_dir) -> dict:
    """用户词表合并层（与 Hermes `_get_user_gloss().layers()` 同口径）。"""
    merged, _user_set, _deleted, _clear = get_user_gloss(data_dir).layers()
    return dict(merged)


def get_text_index(data_dir):
    """提示词术语命中索引：用户合并层 + 词库 case_sensitive/variants。"""
    key = "text_index:%s" % data_dir
    if key not in _CACHE:
        layers = get_termbase().load_layers()
        from stalker_translate_core.matching import GlossaryIndex as TermIndex
        _CACHE[key] = TermIndex(
            get_merged_gloss(data_dir),
            case_sensitive=layers.get("case_sensitive"),
            variants=layers.get("variants"))
    return _CACHE[key]


def get_glossary() -> GlossaryIndex:
    if "glossary" not in _CACHE:
        _CACHE["glossary"] = GlossaryIndex.from_json(
            asset_root() / "stalker_termbase.json")
    return _CACHE["glossary"]
