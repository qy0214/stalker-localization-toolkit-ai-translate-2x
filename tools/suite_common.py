# -*- coding: utf-8 -*-
"""Monorepo 公共路径与校验工具。"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS = ("plugin_2x", "plugin_1_9x", "mcp_hermes", "mcp_online", "skill")


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def product_manifest(product: str) -> dict[str, Any]:
    base = ROOT / "skills/stalker-translate" if product == "skill" \
        else ROOT / "products" / product
    return load_json(base / "manifest.json")


def run_python(script: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(ROOT / script), *args],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )
