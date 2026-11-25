#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""构建 / 校验 Hermes MCP 独立分享包。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PRODUCT = ROOT / "products/mcp_hermes"
ASSETS = ROOT / "assets"
DIST = ROOT / "dist"
ZIP_DATE = (2026, 11, 16, 0, 0, 0)
ASSET_NAMES = (
    "stalker_termbase.json",
    "glossary_builtin.json",
    "xray_pkg.zip",
    "THIRD_PARTY_NOTICES.txt",
)
STATIC_FILES = (
    "plugin.json",
    "mcp.json",
    "config.example.json",
    "install-plugin.cmd",
    "install-plugin.ps1",
    "README-MCP.md",
    "server.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def version() -> str:
    data = json.loads((ROOT / "versions.json").read_text(encoding="utf-8"))
    return str(data["mcp_hermes"])


def zip_path(out_dir: Path = DIST) -> Path:
    return out_dir / ("stalker-translate-MCP-%s.zip" % version())


def manifest_path(out_dir: Path = DIST) -> Path:
    return out_dir / ("stalker-translate-MCP-%s.manifest.json" % version())


def default_config_bytes() -> bytes:
    cfg = {
        "base_url": "http://127.0.0.1:8080/v1",
        "api_key": "",
        "model": "local-model",
        "ctx_size": 8192,
        "parallel": 1,
        "open_progress_page": True,
        "timeout_sec": 300,
        "prompt_extra": "",
        "official_prompts": False,
        "source_lang": "auto",
        "target_lang": "chs",
    }
    return (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def entries() -> dict[str, Path]:
    result: dict[str, Path] = {}
    for name in STATIC_FILES:
        result[name] = PRODUCT / name
    for name in ASSET_NAMES:
        result["kernel/assets/" + name] = ASSETS / name
    for path in sorted((PRODUCT / "stalker_translate_core").rglob("*.py")):
        result["stalker_translate_core/" + path.relative_to(
            PRODUCT / "stalker_translate_core").as_posix()] = path
    for path in sorted((PRODUCT / "runtime").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            result["runtime/" + path.relative_to(PRODUCT / "runtime").as_posix()] = path
    for path in sorted((PRODUCT / "skills").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            result["skills/" + path.relative_to(PRODUCT / "skills").as_posix()] = path
    result["LICENSE"] = ROOT / "LICENSE"
    result["THIRD_PARTY_NOTICES.md"] = ROOT / "THIRD_PARTY_NOTICES.md"
    return result


def build_zip(out_dir: Path = DIST) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = zip_path(out_dir)
    tmp = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
        for arcname, source in sorted(entries().items()):
            info = zipfile.ZipInfo(arcname, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, source.read_bytes())
        info = zipfile.ZipInfo("config.json", date_time=ZIP_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, default_config_bytes())
    tmp.replace(target)
    return target


def build_manifest(out_dir: Path = DIST) -> dict[str, Any]:
    target = build_zip(out_dir)
    data = {
        "product": "mcp_hermes",
        "version": version(),
        "core": "1.0.0",
        "package": target.name,
        "package_size": target.stat().st_size,
        "package_sha256": sha256(target),
        "server_sha256": sha256(PRODUCT / "server.py"),
        "core_sha256": {
            path.relative_to(PRODUCT / "stalker_translate_core").as_posix():
            sha256(path)
            for path in sorted((PRODUCT / "stalker_translate_core").rglob("*.py"))
        },
        "asset_sha256": {name: sha256(ASSETS / name) for name in ASSET_NAMES},
        "entries": sorted(entries()) + ["config.json"],
        "legacy_pipeline": False,
        "note": "共享 core 提供完整离线翻译作业；不再包含过渡内核。",
    }
    manifest_path(out_dir).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    target.with_suffix(".zip.sha256").write_text(
        "%s  %s\n" % (data["package_sha256"], target.name),
        encoding="utf-8", newline="\n")
    return data


def check(out_dir: Path = DIST) -> int:
    errors: list[str] = []
    target = zip_path(out_dir)
    manifest = manifest_path(out_dir)
    if not target.is_file():
        errors.append("发布包不存在：%s" % target)
    if not manifest.is_file():
        errors.append("清单不存在：%s" % manifest)
    if errors:
        for message in errors:
            print("FAIL", message)
        return 1

    expected = entries()
    with zipfile.ZipFile(target) as archive:
        names = sorted(name for name in archive.namelist() if not name.endswith("/"))
        expected_names = sorted(list(expected) + ["config.json"])
        if names != expected_names:
            errors.append("zip 条目集合与源文件不一致")
        for arcname, source in expected.items():
            if arcname in archive.namelist() and archive.read(arcname) != source.read_bytes():
                errors.append("zip 内容不一致：%s" % arcname)
        if "config.json" in archive.namelist() \
                and archive.read("config.json") != default_config_bytes():
            errors.append("config.json 不是脱敏默认配置")

    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("version") != version():
        errors.append("清单版本与 versions.json 不一致")
    if data.get("package_sha256") != sha256(target):
        errors.append("清单包哈希与 zip 不一致")
    if data.get("server_sha256") != sha256(PRODUCT / "server.py"):
        errors.append("清单 server 哈希不一致")
    if data.get("legacy_pipeline") is not False:
        errors.append("清单仍声明包含过渡管线")
    if data.get("asset_sha256") != {name: sha256(ASSETS / name)
                                     for name in ASSET_NAMES}:
        errors.append("清单资产哈希与根 assets/ 不一致")
    expected_core = {
        path.relative_to(PRODUCT / "stalker_translate_core").as_posix(): sha256(path)
        for path in sorted((PRODUCT / "stalker_translate_core").rglob("*.py"))
    }
    if data.get("core_sha256") != expected_core:
        errors.append("清单 core 哈希与 vendored 镜像不一致")

    if errors:
        for message in errors:
            print("FAIL", message)
        return 1
    print("OK Hermes MCP 发布包、core 与资产哈希一致（无过渡内核）")
    print("zip=%s sha256=%s" % (target.name, sha256(target)))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--out-dir", default=str(DIST))
    args = parser.parse_args()
    if not args.build and not args.check:
        parser.error("必须指定 --build 或 --check")
    out_dir = Path(args.out_dir)
    if args.build:
        data = build_manifest(out_dir)
        print("已打包：%s（%d 字节）" % (zip_path(out_dir), data["package_size"]))
        print("sha256=%s" % data["package_sha256"])
    return check(out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
