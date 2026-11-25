# -*- coding: utf-8 -*-
"""共享资产定位、哈希与流水线指纹。"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


ASSET_FILES = (
    "stalker_termbase.json",
    "glossary_builtin.json",
    "xray_pkg.zip",
    "THIRD_PARTY_NOTICES.txt",
)
RUNTIME_ASSET_FILES = (
    "stalker_termbase.json",
    "glossary_builtin.json",
    "xray_pkg.zip",
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AssetBundle:
    root: Path
    hashes: Mapping[str, str]

    @classmethod
    def load(cls, root: str | Path) -> "AssetBundle":
        base = Path(root)
        hashes = {name: sha256_file(base / name) for name in ASSET_FILES}
        return cls(root=base, hashes=hashes)

    def fingerprint(self) -> str:
        return stable_hash(dict(self.hashes))


def resolve_asset_root(plugin_file: str | Path,
                       local_name: str = "assets") -> Path:
    """源码树优先根 assets；发布包仍使用插件目录内资产目录。

    local_name 允许 1.9.x 使用历史布局 ai_translate_assets/。
    """
    here = Path(plugin_file).resolve().parent
    local = here / local_name
    if all((local / name).is_file() for name in RUNTIME_ASSET_FILES):
        return local
    repo = here.parent.parent / "assets"
    if all((repo / name).is_file() for name in RUNTIME_ASSET_FILES):
        return repo
    raise FileNotFoundError("找不到完整资产目录：%s 或 %s" % (local, repo))


def pipeline_fingerprint(components: Mapping[str, object]) -> str:
    return stable_hash(components)


def iter_files(root: str | Path) -> Iterable[Path]:
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            yield Path(base) / name
