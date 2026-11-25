#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Monorepo 统一构建入口。

当前只开放 status=active 的产品；imported/scaffold 产品会在迁移完成后接入。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from suite_common import PRODUCTS, product_manifest, run_python  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", choices=("all", *PRODUCTS), default="all")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        for product in PRODUCTS:
            manifest = product_manifest(product)
            print("%-14s %-10s %s" % (
                product, manifest.get("version"), manifest.get("status")))
        return 0

    selected = PRODUCTS if args.product == "all" else (args.product,)
    sync_mode = "--check" if args.check else "--write"
    built = 0
    for product in selected:
        manifest = product_manifest(product)
        if manifest.get("status") != "active":
            print("跳过 %s（status=%s，尚未接入统一构建）" % (
                product, manifest.get("status")))
            continue
        if product == "plugin_2x":
            run_python("tools/sync_core.py", sync_mode)
            run_python("tools/sync_assets.py", sync_mode)
            run_python("tools/gen_release_manifest.py",
                       "--check" if args.check else "--build")
            built += 1
        elif product == "plugin_1_9x":
            run_python("tools/sync_core.py", sync_mode)
            run_python("tools/gen_release_manifest_1_9x.py",
                       "--check" if args.check else "--build")
            built += 1
        elif product == "mcp_hermes":
            run_python("tools/sync_core.py", sync_mode)
            run_python("tools/gen_release_manifest_mcp_hermes.py",
                       "--check" if args.check else "--build")
            built += 1
        elif product == "mcp_online":
            run_python("tools/sync_core.py", sync_mode)
            run_python("tools/gen_release_manifest_mcp_online.py",
                       "--check" if args.check else "--build")
            built += 1
        elif product == "skill":
            run_python("tools/sync_core.py", sync_mode)
            run_python("tools/gen_release_manifest_skill.py",
                       "--check" if args.check else "--build")
            built += 1
        else:
            print("FAIL 未实现 active 产品构建分支", product)
            return 2
    if not built:
        print("没有可构建的 active 产品")
        return 1
    print("OK 已%s %d 个产品" % ("校验" if args.check else "构建", built))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
