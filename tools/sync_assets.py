#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把根 assets/ 同步到产品兼容镜像；--check 只比对哈希。"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from suite_common import sha256  # noqa: E402

ASSETS = (
    "stalker_termbase.json",
    "glossary_builtin.json",
    "xray_pkg.zip",
    "THIRD_PARTY_NOTICES.txt",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if not args.check and not args.write:
        parser.error("必须指定 --check 或 --write")

    targets = {
        "plugin_2x": ROOT / "plugins/ai_translate/assets",
    }
    failures = []
    for product, target in targets.items():
        if args.write:
            target.mkdir(parents=True, exist_ok=True)
        for name in ASSETS:
            source = ROOT / "assets" / name
            dest = target / name
            try:
                dest_hash = sha256(dest)
            except OSError:
                dest_hash = ""
            if sha256(source) != dest_hash:
                failures.append("%s/%s" % (product, name))
                if args.write:
                    shutil.copy2(source, dest)
                    print("同步", dest.relative_to(ROOT))
    if args.write and failures:
        print("OK 已刷新 %d 个产品资产镜像" % len(failures))
        return 0
    if failures:
        for name in failures:
            print("FAIL 资产镜像不一致", name)
        return 1
    print("OK 产品资产镜像与根 assets/ 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
