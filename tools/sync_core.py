#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把根 core 包同步到各桌面插件的 vendored 镜像。"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "core/stalker_translate_core"
TARGETS = {
    "plugin_2x": ROOT / "plugins/ai_translate/stalker_translate_core",
    "plugin_1_9x": ROOT / "products/plugin_1_9x/stalker_translate_core",
    "mcp_hermes": ROOT / "products/mcp_hermes/stalker_translate_core",
    "mcp_online": ROOT / "products/mcp_online/stalker_translate_core",
}


def sync(target: Path, write: bool) -> list[str]:
    failures: list[str] = []
    sources = sorted(SOURCE.rglob("*.py"))
    if write:
        target.mkdir(parents=True, exist_ok=True)
        for source in sources:
            rel = source.relative_to(SOURCE)
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        # 只清理源侧已消失的 .py：整目录 rmtree 会触发批量删除门禁，
        # 也会误删镜像目录里可能存在的运行产物之外的非源码文件。
        for path in list(target.rglob("*.py")):
            if not (SOURCE / path.relative_to(target)).is_file():
                path.unlink()
        return failures

    target_files = {str(path.relative_to(target)): path
                    for path in target.rglob("*.py")} if target.exists() else {}
    source_files = {str(path.relative_to(SOURCE)): path for path in sources}
    if set(source_files) != set(target_files):
        failures.append("core 文件集合不一致")
    for rel in sorted(set(source_files) & set(target_files)):
        if source_files[rel].read_bytes() != target_files[rel].read_bytes():
            failures.append(rel)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", choices=("all", *TARGETS), default="all")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if not args.check and not args.write:
        parser.error("必须指定 --check 或 --write")

    selected = TARGETS if args.product == "all" else (args.product,)
    failed = False
    for product in selected:
        target = TARGETS[product]
        failures = sync(target, args.write)
        if failures:
            failed = True
            for rel in failures:
                print("FAIL %s core 镜像不一致 %s" % (product, rel))
        elif args.write:
            print("OK 已同步 %d 个 core 文件到 %s" % (
                len(list(SOURCE.rglob("*.py"))), product))
        else:
            print("OK %s core 镜像与根 core 一致" % product)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
