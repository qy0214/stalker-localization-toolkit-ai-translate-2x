#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""五产品统一发布列车：构建、汇总清单、校验与本地 tag。

不自动推送、不自动创建 GitHub Release。tag 仅在工作区干净时允许。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from suite_common import ACTIVE_PRODUCTS, load_json, product_manifest, run_python, sha256  # noqa: E402

OUT_JSON = ROOT / "release_train.json"
OUT_MD = ROOT / "RELEASE_TRAIN.md"


def tag_name(train: str) -> str:
    return train if train.startswith("suite-v") else "suite-v" + train


def artifact_specs() -> dict[str, list[tuple[str, str]]]:
    specs: dict[str, list[tuple[str, str]]] = {}
    for product in ACTIVE_PRODUCTS:
        manifest = product_manifest(product)
        items: list[tuple[str, str]] = []
        if manifest.get("package"):
            items.append(("package", manifest["package"]))
        if manifest.get("install_package"):
            items.append(("install_package", manifest["install_package"]))
        if manifest.get("release_manifest"):
            items.append(("manifest", manifest["release_manifest"]))
        specs[product] = items
    return specs


def collect() -> dict[str, Any]:
    versions = load_json(ROOT / "versions.json")
    compatibility = load_json(ROOT / "compatibility.json")
    train = str(compatibility["release_train"])
    if compatibility.get("core") != versions.get("core"):
        raise ValueError("compatibility.json 与 versions.json 的 core 版本不一致")
    products: dict[str, Any] = {}
    for product in ACTIVE_PRODUCTS:
        manifest = product_manifest(product)
        if manifest.get("status") != "active":
            raise ValueError("%s 不是 active 产品" % product)
        if manifest.get("version") != versions.get(product):
            raise ValueError("%s 版本与 versions.json 不一致" % product)
        if manifest.get("core") != versions.get("core"):
            raise ValueError("%s core 版本与 versions.json 不一致" % product)
        artifacts = []
        for role, rel in artifact_specs()[product]:
            path = ROOT / rel
            if not path.is_file():
                raise FileNotFoundError("缺少发布资产：%s" % rel)
            artifacts.append({"role": role, "path": rel,
                              "size": path.stat().st_size, "sha256": sha256(path)})
        package = next((item for item in artifacts if item["role"] == "package"), None)
        release_manifest = next((item for item in artifacts if item["role"] == "manifest"), None)
        if package is not None and release_manifest is not None:
            details = load_json(ROOT / release_manifest["path"])
            expected = {
                "product": product,
                "version": versions[product],
                "core": versions["core"],
                "package": Path(package["path"]).name,
                "package_size": package["size"],
                "package_sha256": package["sha256"],
            }
            for key, value in expected.items():
                if details.get(key) != value:
                    raise ValueError("%s 发布清单 %s 与当前产品不一致" % (product, key))
        products[product] = {
            "version": manifest["version"], "core": manifest["core"],
            "status": manifest["status"], "artifacts": artifacts,
        }
    return {
        "release_train": train,
        "tag": tag_name(train),
        "core": versions["core"],
        "products": products,
        "compatibility": compatibility,
        "legacy_repositories": {
            "plugin_1_9x": {
                "url": "https://github.com/qy0214/stalker-localization-toolkit-ai-translate",
                "state": "archived",
            },
            "mcp_hermes": {
                "url": "https://github.com/qy0214/stalker-translate-mcp",
                "state": "archived",
            },
        },
    }


def render_md(data: dict[str, Any]) -> str:
    lines = [
        "# STALKER AI Translate Suite 发布列车", "",
        "- Release train：`%s`" % data["release_train"],
        "- Git tag：`%s`" % data["tag"],
        "- 共享 core：`%s`" % data["core"], "",
        "## 产品与资产", "",
        "| 产品 | 版本 | 资产 | 字节 | SHA-256 |",
        "|---|---|---|---:|---|",
    ]
    for product, info in data["products"].items():
        for art in info["artifacts"]:
            lines.append("| %s | %s | `%s` | %d | `%s` |" % (
                product, info["version"], art["path"], art["size"],
                art["sha256"]))
    pending = data["compatibility"].get("pending_products") or []
    if pending:
        lines += ["", "## Pending 产品（不进入当前列车）", ""]
        for product in pending:
            manifest = product_manifest(product)
            lines.append("- %s %s：%s" % (
                product, manifest.get("version", "?"),
                manifest.get("release_note", "pending")))
    lines += [
        "", "## 兼容矩阵", "",
        "| 组件 | 版本/要求 |", "|---|---|",
        "| core | %s |" % data["compatibility"]["core"],
    ]
    for product, version in data["compatibility"]["products"].items():
        lines.append("| %s | %s |" % (product, version))
    for name, requirement in data["compatibility"].get("python", {}).items():
        lines.append("| Python %s | %s |" % (name, requirement))
    lines += [
        "", "## 旧仓库切换", "",
        "本仓库启用后，旧仓库只保留历史与跳转说明，不再同步源码：", "",
    ]
    for name, info in data["legacy_repositories"].items():
        lines.append("- %s：%s（%s）" % (
            name, info["url"], info["state"]))
    lines += [
        "", "归档前确认：主仓库 tag、五个分享包、SHA-256 与注册表条目均已发布。", "",
    ]
    return "\n".join(lines)


def write_outputs(data: dict[str, Any]) -> None:
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    OUT_MD.write_text(render_md(data), encoding="utf-8", newline="\n")


def check() -> int:
    current = collect()
    if not OUT_JSON.is_file() or not OUT_MD.is_file():
        print("FAIL 发布列车清单尚未生成")
        return 1
    committed = load_json(OUT_JSON)
    if committed != current:
        print("FAIL 发布列车清单与当前资产/版本不一致")
        return 1
    if OUT_MD.read_text(encoding="utf-8") != render_md(current):
        print("FAIL 发布列车说明与当前数据不一致")
        return 1
    print("OK 发布列车清单、说明、版本与资产哈希一致")
    print("tag=%s products=%d artifacts=%d" % (
        current["tag"], len(current["products"]),
        sum(len(p["artifacts"]) for p in current["products"].values())))
    return 0


def create_tag(dry_run: bool) -> int:
    data = collect()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                           stdout=subprocess.PIPE, text=True, check=True).stdout
    if dirty.strip():
        print("FAIL 工作区不干净，拒绝打 tag；请先提交发布改动")
        return 1
    tag = data["tag"]
    if dry_run:
        print("git tag -a %s -m %s" % (tag, data["release_train"]))
        return 0
    subprocess.run(["git", "tag", "-a", tag, "-m", data["release_train"]],
                   cwd=str(ROOT), check=True)
    print("OK 已创建本地 tag：%s" % tag)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true", help="构建全部 active 产品")
    parser.add_argument("--check", action="store_true", help="只校验发布列车")
    parser.add_argument("--tag", action="store_true", help="工作区干净后创建本地 tag")
    parser.add_argument("--dry-run", action="store_true", help="tag 只打印命令")
    args = parser.parse_args()
    if not any((args.build, args.check, args.tag)):
        parser.error("必须指定 --build、--check 或 --tag")
    if args.build:
        run_python("tools/build.py", "--product", "all")
        write_outputs(collect())
        print("OK 已生成 %s 与 %s" % (OUT_JSON.name, OUT_MD.name))
        return 0
    # --check / --tag 都是只读校验，严禁重写发布列车文件。
    if args.check:
        return check()
    if args.tag:
        rc = check()
        return rc if rc else create_tag(args.dry_run)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
