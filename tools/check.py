#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Monorepo 结构、版本、产品清单与资产哈希门禁。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from suite_common import PRODUCTS, load_json, product_manifest, run_python, sha256  # noqa: E402


def main() -> int:
    errors: list[str] = []
    versions = load_json(ROOT / "versions.json")
    compatibility = load_json(ROOT / "compatibility.json")
    if compatibility.get("core") != versions.get("core"):
        errors.append("compatibility.json 的 core 版本与 versions.json 不一致")

    for product in PRODUCTS:
        manifest = product_manifest(product)
        if manifest.get("version") != versions.get(product):
            errors.append("%s 产品版本与 versions.json 不一致" % product)
        if manifest.get("core") is not None and manifest.get("core") != versions.get("core"):
            errors.append("%s core 版本与 versions.json 不一致" % product)
        entry = manifest.get("entry")
        if entry and not (ROOT / entry).is_file():
            errors.append("%s 入口不存在：%s" % (product, entry))
        if manifest.get("status") == "active":
            package = manifest.get("package")
            if package and not (ROOT / package).is_file():
                errors.append("%s active 产品缺少发布包：%s" % (product, package))
            release_manifest = manifest.get("release_manifest")
            if release_manifest and not (ROOT / release_manifest).is_file():
                errors.append("%s active 产品缺少发布清单：%s" % (
                    product, release_manifest))

    skill = load_json(ROOT / "skills/stalker-translate/manifest.json")
    if skill.get("version") != versions.get("skill"):
        errors.append("skill 版本与 versions.json 不一致")
    if skill.get("runtime") != "mcp_online":
        errors.append("skill runtime 必须是 mcp_online")

    asset_names = (
        "stalker_termbase.json",
        "glossary_builtin.json",
        "xray_pkg.zip",
        "THIRD_PARTY_NOTICES.txt",
    )
    canonical = {name: sha256(ROOT / "assets" / name) for name in asset_names}
    mirror = {name: sha256(ROOT / "plugins/ai_translate/assets" / name)
              for name in asset_names}
    if canonical != mirror:
        errors.append("根 assets/ 与 2.x 兼容镜像不一致")

    core_check = run_python("tools/sync_core.py", "--check", check=False)
    if core_check.returncode:
        errors.append("2.x vendored core 与根 core 不一致")

    for base in (ROOT / "products", ROOT / "skills"):
        for path in base.rglob("stalker_termbase.json"):
            errors.append("产品目录存在重复术语库：%s" % path.relative_to(ROOT))

    if errors:
        for message in errors:
            print("FAIL", message)
        return 1
    print("OK Monorepo 清单、版本、入口与资产哈希一致")
    print("core=%s products=%s" % (
        versions["core"],
        ", ".join("%s=%s" % (name, versions[name]) for name in PRODUCTS),
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
