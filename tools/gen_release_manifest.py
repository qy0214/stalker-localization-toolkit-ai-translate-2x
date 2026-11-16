#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 / 校验 ai_translate 2.x 发布包与清单。

输出：
  dist/ai_translate_plugin_v<版本>.zip   目录插件发布包（可复现：固定时间戳）
  RELEASE_MANIFEST.md                    人读版
  release_manifest.json                  机读版

用法：
  python tools/gen_release_manifest.py --build      # 重建 zip + 清单
  python tools/gen_release_manifest.py --check      # CI 门禁：只校验，不写文件

范围：`plugins/ai_translate/`（`plugin.py` + `assets/`）与同版本发布 zip。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT_MD = ROOT / "RELEASE_MANIFEST.md"
OUT_JSON = ROOT / "release_manifest.json"

PLUGIN_DIR = ROOT / "plugins/ai_translate"
PLUGIN = PLUGIN_DIR / "plugin.py"
ASSETS = PLUGIN_DIR / "assets"
DIST = ROOT / "dist"

EXPECTED_PLUGIN_VERSION = "2.0.1"
EXPECTED_PLUGIN_API = 1
# 2.x 发布包剥掉了 _ssl.pyd ⇒ 这些导入链会连本机 HTTP 都跑不起来
FORBIDDEN_IMPORTS = ("ssl", "http", "urllib.request", "urllib.error", "tkinter")

# 2026-10-23 从 llama.cpp b11612 GitHub Release asset digest 取得。
EXPECTED_LLAMA = {
    "cpu": ("llama-b11612-bin-win-cpu-x64.zip",
            "b8456ccb1be98365a5a2227efa5c495508a6a3ebce881c6189a1dbeaaa170b69"),
    "cuda-12.4": ("llama-b11612-bin-win-cuda-12.4-x64.zip",
                  "4c6d8afd23475f6eded74c4a3019a6746aab83183a3158f3fc4de1cb7c4f5f12"),
    "vulkan": ("llama-b11612-bin-win-vulkan-x64.zip",
               "e62f0674970a70b007fa3c425d191ceacabbf02406d6c9ac8b6f27cd23abe9e9"),
    "cudart-12.4": ("cudart-llama-bin-win-cuda-12.4-x64.zip",
                    "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"),
    "cuda-13.4": ("llama-b11612-bin-win-cuda-13.4-x64.zip",
                  "e1c59ee10ead069dee12bf5a1f5a3ed294b144ea3f9c5006aa89675c5306de38"),
    "cudart-13.4": ("cudart-llama-bin-win-cuda-13.4-x64.zip",
                    "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668"),
}

# 2026-10-23 从 HuggingFace LFS oid 取得（hf-mirror API，官方仓库元数据）。
EXPECTED_MODELS = {
    "Hy-MT2-7B-Q4_K_M.gguf": (
        "9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b",
        4624648896),
    "Hy-MT2-1.8B-Q8_0.gguf": (
        "5c3fe0b1408a5ceb0143184ef247b11b579c525f4b02b060e6c851bb76fef1a4",
        1908528192),
}

# 发布包只含这 5 个文件（目录插件形状：plugins/ai_translate/…）
ZIP_ENTRIES = {
    "plugins/ai_translate/plugin.py",
    "plugins/ai_translate/assets/THIRD_PARTY_NOTICES.txt",
    "plugins/ai_translate/assets/glossary_builtin.json",
    "plugins/ai_translate/assets/stalker_termbase.json",
    "plugins/ai_translate/assets/xray_pkg.zip",
}

ZIP_DATE = (2026, 11, 16, 0, 0, 0)


def zip_path() -> Path:
    return DIST / ("ai_translate_plugin_v%s.zip" % EXPECTED_PLUGIN_VERSION)


def install_zip_path() -> Path:
    """平台安装就绪包：顶层就是插件目录 ai_translate/。

    平台的插件管理器会把注册表里的安装包解压进 plugins/<id>/，
    所以给平台的资产用这个结构（顶层 ai_translate/），
    仓库内部分发仍用 zip_path()（仓库结构原样，便于核对）。
    """
    return DIST / ("ai_translate_plugin_v%s_install.zip" % EXPECTED_PLUGIN_VERSION)


def fmt_size(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return ("%d %s" % (value, unit)) if unit == "B" else (
                "%.2f %s" % (value, unit))
        value /= 1024
    return "%d B" % n


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def dir_size(path: Path) -> tuple[int, int]:
    total = 0
    count = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
                count += 1
            except OSError:
                pass
    return total, count


def git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT)] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        return out.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def git_info() -> dict[str, Any]:
    commit = git("rev-parse", "--short", "HEAD")
    branch = git("branch", "--show-current")
    status_lines = [
        line for line in git("status", "--porcelain").splitlines()
        if ("RELEASE_MANIFEST.md" not in line
                and "release_manifest.json" not in line
                and "gen_release_manifest.py" not in line)
    ]
    return {
        "branch": branch or "（未提交/无分支）",
        "commit": commit or "（尚无提交）",
        "dirty": bool(status_lines),
        "dirty_files": status_lines[:20],
    }


def tracked_stats() -> tuple[int, int]:
    raw = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False).stdout
    if not raw:
        return 0, 0
    total = 0
    count = 0
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        rel = entry.decode("utf-8", "surrogateescape")
        p = ROOT / rel
        try:
            total += p.stat().st_size
            count += 1
        except OSError:
            pass
    return count, total


def parse_plugin(text: str) -> dict[str, Any]:
    """静态读 PLUGIN_INFO / PLUGIN_API / 官方哈希常量（不 import 插件）。"""
    info: dict[str, Any] = {}
    api = None
    llama: dict[str, str] = {}
    models: dict[str, tuple[str, int]] = {}
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if target.id == "PLUGIN_INFO":
                try:
                    info = ast.literal_eval(node.value)
                except Exception:
                    info = {}
            elif target.id == "PLUGIN_API":
                try:
                    api = ast.literal_eval(node.value)
                except Exception:
                    api = None
            elif target.id == "_LLAMA_SHA256":
                value = ast.literal_eval(node.value)
                if isinstance(value, dict):
                    llama = {str(k): str(v) for k, v in value.items()}
            elif target.id == "_MODEL_HASHES":
                value = ast.literal_eval(node.value)
                if isinstance(value, dict):
                    models = {str(k): (str(v[0]), int(v[1]))
                              for k, v in value.items()}
    tag = ""
    m = re.search(r'_LLAMA_TAG\s*=\s*"([^"]+)"', text)
    if m:
        tag = m.group(1)
    return {"info": info if isinstance(info, dict) else {},
            "api": api, "llama": llama, "models": models, "tag": tag}


def imported_modules(text: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def json_count(path: Path) -> int | None:
    try:
        data = json.loads(read_text(path))
    except Exception:
        return None
    if isinstance(data, dict):
        return len([k for k in data if k != "_meta"])
    if isinstance(data, list):
        return len(data)
    return None


def artifact(role: str, path: Path, note: str = "") -> dict[str, Any]:
    rel = str(path.relative_to(ROOT))
    if not path.exists():
        return {"role": role, "path": rel, "exists": False,
                "size": 0, "sha256": "", "note": note or "缺失"}
    if path.is_dir():
        total, count = dir_size(path)
        return {"role": role, "path": rel, "exists": True,
                "size": total, "sha256": "", "note": "%d 个文件（目录）" % count}
    return {"role": role, "path": rel, "exists": True,
            "size": path.stat().st_size, "sha256": sha256(path), "note": note}


def check(name: str, ok: bool | None, detail: str) -> dict[str, Any]:
    return {"check": name, "ok": ok, "detail": detail}


def build_zip() -> None:
    """可复现 zip：固定时间戳 + 固定条目顺序（同样的输入 ⇒ 同样的哈希）。

    产出两个包：
      * ai_translate_plugin_v<ver>.zip          —— 仓库结构原样（plugins/ai_translate/…）
      * ai_translate_plugin_v<ver>_install.zip  —— 顶层 ai_translate/，供平台安装器直接解压
    """
    DIST.mkdir(parents=True, exist_ok=True)
    target = zip_path()
    tmp = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(ZIP_ENTRIES):
            src = ROOT / rel
            info = zipfile.ZipInfo(rel, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, src.read_bytes())
    os.replace(tmp, target)

    install_target = install_zip_path()
    itmp = install_target.with_suffix(".zip.tmp")
    prefix = "plugins/ai_translate/"
    with zipfile.ZipFile(itmp, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(ZIP_ENTRIES):
            src = ROOT / rel
            arc = "ai_translate/" + rel[len(prefix):]
            info = zipfile.ZipInfo(arc, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, src.read_bytes())
    os.replace(itmp, install_target)

    # 非当前版本 zip 不留在 dist 里，避免"发布包到底是哪个"的歧义
    keep = {target, install_target}
    for stale in DIST.glob("ai_translate_plugin_*.zip"):
        if stale not in keep:
            stale.unlink()


def build_manifest() -> dict[str, Any]:
    plugin_text = read_text(PLUGIN) if PLUGIN.is_file() else ""
    meta = parse_plugin(plugin_text) if plugin_text else {
        "info": {}, "api": None, "llama": {}, "models": {}, "tag": ""}
    info = meta["info"]
    version = str(info.get("version", ""))
    zip_file = zip_path()

    artifacts = [
        artifact("插件入口", PLUGIN, "2.x 目录插件（api.ui 门面）"),
        artifact("插件分享目录", PLUGIN_DIR,
                 "整个目录即分享包（plugin.py + assets/）"),
        artifact("插件发布 zip", zip_file, "当前发布包"),
        artifact("平台安装包（顶层 ai_translate/）", install_zip_path(),
                 "给平台插件管理器直接解压用"),
        artifact("术语库（随包，只读）", ASSETS / "glossary_builtin.json",
                 "运行时匹配的内置层"),
        artifact("术语基表（分类）", ASSETS / "stalker_termbase.json", "导入用"),
        artifact("X-Ray 解析器", ASSETS / "xray_pkg.zip", "解析 XML/LTX/脚本"),
        artifact("第三方来源声明", ASSETS / "THIRD_PARTY_NOTICES.txt",
                 "随包分发（上游来源与许可状态）"),
    ]

    zip_sha = ""
    zip_entries: list[str] = []
    zip_notes: list[str] = []
    if zip_file.is_file():
        zip_sha = sha256(zip_file)
        with zipfile.ZipFile(zip_file) as z:
            zip_entries = z.namelist()
            zip_notes = [n for n in zip_entries
                         if n.endswith("/")
                         or n.startswith(("bin/", "models/", "logs/",
                                          "downloads/"))
                         or "__pycache__" in n]

    checks: list[dict[str, Any]] = []

    try:
        compile(plugin_text, str(PLUGIN), "exec")
        checks.append(check("插件语法", True, "compile() 通过"))
    except Exception as e:
        checks.append(check("插件语法", False, "compile() 失败：%s" % e))

    checks.append(check(
        "插件版本 == 预期", version == EXPECTED_PLUGIN_VERSION,
        "PLUGIN_INFO %s / 预期 %s" % (
            version or "未读到", EXPECTED_PLUGIN_VERSION)))
    checks.append(check(
        "PLUGIN_API == %d（2.x 宿主契约）" % EXPECTED_PLUGIN_API,
        meta["api"] == EXPECTED_PLUGIN_API, str(meta["api"])))
    checks.append(check(
        "PLUGIN_INFO.id 为 ASCII", str(info.get("id", "")).isascii()
        and bool(info.get("id")), str(info.get("id"))))
    checks.append(check(
        "PLUGIN_INFO.repo 指向 2.x 专用仓库",
        "ai-translate-2x" in str(info.get("repo", "")),
        str(info.get("repo"))))

    # 目录插件形状
    checks.append(check(
        "目录插件形状：plugins/ai_translate/{plugin.py,assets/}",
        PLUGIN.is_file() and ASSETS.is_dir(),
        "plugin.py + assets/"))

    # 2.x 禁用导入（剥掉 _ssl.pyd ⇒ 连 127.0.0.1 都 import 失败）
    imported = imported_modules(plugin_text) if plugin_text else set()
    bad_imports = sorted(
        m for m in imported
        for p in FORBIDDEN_IMPORTS if m == p or m.startswith(p + "."))
    checks.append(check(
        "无 2.x 禁用导入（ssl/http/urllib.request/urllib.error/tkinter）",
        not bad_imports, str(bad_imports) if bad_imports else "干净"))

    checks.append(check(
        "zip 文件名含插件版本", version and ("v%s" % version) in zip_file.name,
        zip_file.name))
    checks.append(check(
        "zip 条目 == 5 个预期文件", set(zip_entries) == ZIP_ENTRIES,
        "多出：%s；缺少：%s" % (
            sorted(set(zip_entries) - ZIP_ENTRIES) or "无",
            sorted(ZIP_ENTRIES - set(zip_entries)) or "无")))
    checks.append(check(
        "zip 不含空目录/运行库/模型/日志/__pycache__", not zip_notes,
        "发现：%s" % zip_notes if zip_notes else "干净"))

    # 术语库是翻译 MOD 的前提：两件套必须随包（不是可选件）。
    shipped_term = {"plugins/ai_translate/assets/glossary_builtin.json",
                    "plugins/ai_translate/assets/stalker_termbase.json"}
    checks.append(check(
        "随包术语库：内置词表 + 分类基表都在 zip 内", 
        shipped_term <= set(zip_entries),
        "缺少：%s" % (sorted(shipped_term - set(zip_entries)) or "无")))
    if zip_file.is_file():
        with zipfile.ZipFile(zip_file) as z:
            term_bytes = {n: len(z.read(n)) for n in sorted(shipped_term)
                          if n in z.namelist()}
        checks.append(check(
            "随包术语库非空壳（两文件合计 > 600 KB）",
            sum(term_bytes.values()) > 600 * 1024,
            "；".join("%s=%d" % (k.rsplit("/", 1)[-1], v)
                     for k, v in term_bytes.items()) or "缺失"))

    if zip_file.is_file():
        with zipfile.ZipFile(zip_file) as z:
            same = True
            details = []
            for rel in sorted(ZIP_ENTRIES):
                try:
                    data = z.read(rel)
                except KeyError:
                    same = False
                    details.append("%s 缺失" % rel)
                    continue
                disk = ROOT / rel
                if not disk.is_file() or hashlib.sha256(data).digest() != \
                        hashlib.sha256(disk.read_bytes()).digest():
                    same = False
                    details.append("%s 不同" % rel)
            checks.append(check("zip 内文件 == 工作副本", same,
                                "全部一致" if same else "；".join(details)))

    expected_llama = {k: v[1] for k, v in EXPECTED_LLAMA.items()}
    checks.append(check(
        "官方哈希：llama.cpp 6 项", meta["llama"] == expected_llama,
        "登记 %d 项；不一致：%s" % (
            len(meta["llama"]),
            sorted(set(expected_llama.items()) ^ set(meta["llama"].items()))
            or "无")))
    checks.append(check(
        "官方哈希：模型 2 项", meta["models"] == EXPECTED_MODELS,
        "登记 %d 项；不一致：%s" % (
            len(meta["models"]),
            sorted(set(EXPECTED_MODELS.items()) ^ set(meta["models"].items()))
            or "无")))
    all_hashes = list(meta["llama"].values()) + [
        v[0] for v in meta["models"].values()]
    checks.append(check(
        "官方哈希：8 个合法 SHA-256", len(all_hashes) == 8 and
        all(re.fullmatch(r"[0-9a-f]{64}", h or "") for h in all_hashes),
        "%d 个" % len(all_hashes)))
    checks.append(check(
        "官方哈希：llama.cpp 标签 b11612", meta["tag"] == "b11612",
        "源码常量 _LLAMA_TAG=%r" % meta["tag"]))

    # 分享目录不含运行产物（默认数据根在 %LOCALAPPDATA%，这里只防误提交）
    forbidden = []
    if ASSETS.is_dir():
        for name in os.listdir(ASSETS):
            if name in ("bin", "models", "logs", "downloads", "config.json",
                        "glossary_user.json", "__pycache__"):
                forbidden.append(name)
    checks.append(check(
        "分享目录不含 bin/models/logs/downloads/config.json",
        not forbidden, "发现：%s" % forbidden if forbidden else "干净"))
    if PLUGIN_DIR.is_dir() and (PLUGIN_DIR / "__pycache__").is_dir():
        # 本地跑过插件/测试会留下字节码缓存（已在 .gitignore 内）；真正要防的是
        # 它被提交进仓库。发布 zip 是白名单打包，不可能带进去。
        tracked_cache = [
            rel for rel in git("ls-files", "plugins/ai_translate").splitlines()
            if "__pycache__" in rel]
        checks.append(check(
            "插件目录无被跟踪的 __pycache__", not tracked_cache,
            str(tracked_cache[:3]) if tracked_cache
            else "本地缓存存在但未跟踪（不影响发布包）"))
    else:
        checks.append(check("插件目录无被跟踪的 __pycache__", True, "干净"))

    leak = []
    patterns = (r"[A-Za-z]:\\STALKER-HH", r"[A-Za-z]:/STALKER-HH",
                r"C:\\Users\\", r"C:/Users/")
    for pat in patterns:
        if re.search(pat, plugin_text):
            leak.append(pat)
    checks.append(check("无绝对路径/用户名泄漏", not leak,
                        "发现：%s" % leak if leak else "未发现"))

    # 术语规模：按实际分类求和，再和 _meta 比（不自己比自己）
    glossary_count = json_count(ASSETS / "glossary_builtin.json")
    termbase_path = ASSETS / "stalker_termbase.json"
    try:
        termbase = json.loads(read_text(termbase_path))
    except Exception:
        termbase = {}
    actual_cats = {str(k): len(v) for k, v in termbase.items()
                   if isinstance(v, dict) and not str(k).startswith("_")}
    actual_sum = sum(actual_cats.values())
    meta_block = termbase.get("_meta") if isinstance(
        termbase.get("_meta"), dict) else {}
    termbase_count = meta_block.get("total_entries")
    checks.append(check(
        "术语基表：_meta.total_entries == 实际分类合计",
        isinstance(termbase_count, int) and termbase_count == actual_sum,
        "meta %s / 实际 %s" % (termbase_count, actual_sum)))
    meta_cats = meta_block.get("categories") \
        if isinstance(meta_block.get("categories"), dict) else {}
    checks.append(check(
        "术语基表：_meta.categories == 实际分类条数",
        meta_cats == actual_cats,
        "meta %d 类 / 实际 %d 类，差异：%s" % (
            len(meta_cats), len(actual_cats),
            sorted(set(meta_cats.items()) ^ set(actual_cats.items()))[:4]
            or "无")))
    checks.append(check(
        "术语库：随包条目 >= 8000",
        bool(glossary_count and glossary_count >= 8000),
        "%s 条" % glossary_count))
    checks.append(check(
        "术语库：随包 >= 分类基表",
        bool(glossary_count and actual_sum and glossary_count >= actual_sum),
        "%s / %s" % (glossary_count, actual_sum)))

    keep_zips = {zip_file, install_zip_path()}
    stale_zips = sorted(p.name for p in DIST.glob("ai_translate_plugin_*.zip")
                        if p not in keep_zips)
    checks.append(check(
        "dist 只保留当前版本 zip", not stale_zips,
        ("多余：%s" % stale_zips) if stale_zips else "仅当前版本"))

    tracked_count, tracked_bytes = tracked_stats()

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "workspace": ".",
        "scope": "AI 离线翻译插件（ai_translate）· STALKER Localization Toolkit 2.x",
        "git": git_info(),
        "versions": {
            "plugin_version": version,
            "expected_plugin_version": EXPECTED_PLUGIN_VERSION,
            "plugin_api": meta["api"],
            "target_toolkit": "STALKER Localization Toolkit 2.x",
        },
        "artifacts": artifacts,
        "zip": {"path": str(zip_file.relative_to(ROOT)), "sha256": zip_sha,
                "entries": zip_entries},
        "official_hashes": {
            "llama": {k: {"file": v[0], "sha256": v[1]}
                      for k, v in EXPECTED_LLAMA.items()},
            "models": {k: {"sha256": v[0], "bytes": v[1]}
                       for k, v in EXPECTED_MODELS.items()},
        },
        "consistency": checks,
        "term_data": {
            "glossary_entries": glossary_count,
            "termbase_entries": actual_sum,
            "termbase_categories": actual_cats,
            "termbase_meta_total": termbase_count,
        },
        "tracked": {"files": tracked_count, "bytes": tracked_bytes},
    }


def render_markdown(data: dict[str, Any]) -> str:
    def yes(ok: bool | None) -> str:
        return "一致" if ok is True else ("不一致" if ok is False else "无法比对")

    lines: list[str] = []
    lines.append("# AI 离线翻译插件（2.x）· 发布清单")
    lines.append("")
    lines.append("- 生成时间：`%s`" % data["generated_at"])
    lines.append("- 范围：**%s**" % data["scope"])
    lines.append("- Git：`%s` @ `%s`%s" % (
        data["git"]["branch"], data["git"]["commit"],
        "（有未提交改动）" if data["git"]["dirty"] else ""))
    lines.append("- 已跟踪文件：%d 个，%s" % (
        data["tracked"]["files"], fmt_size(data["tracked"]["bytes"])))
    lines.append("")
    lines.append("## 1. 版本与交付物")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append("| 插件版本 | `%s` |" % (
        data["versions"]["plugin_version"] or "未读到"))
    lines.append("| 预期版本 | `%s` |" % data["versions"]["expected_plugin_version"])
    lines.append("| 宿主插件 API | `%s` |" % data["versions"]["plugin_api"])
    lines.append("| 适用平台 | `%s` |" % data["versions"]["target_toolkit"])
    lines.append("")
    lines.append("| 角色 | 路径 | 大小 | SHA-256 | 说明 |")
    lines.append("|---|---|---:|---|---|")
    for a in data["artifacts"]:
        digest = ("`%s`" % a["sha256"]) if a.get("sha256") else "—"
        lines.append("| %s | `%s` | %s | %s | %s |" % (
            a["role"], a["path"], fmt_size(a["size"]), digest, a["note"]))
    lines.append("")
    lines.append("## 2. 下载链路官方 SHA-256")
    lines.append("")
    lines.append("| 产物 | 文件 | 字节数 | SHA-256 |")
    lines.append("|---|---|---:|---|")
    for key, item in data["official_hashes"]["llama"].items():
        lines.append("| llama.cpp `%s` | `%s` | — | `%s` |" % (
            key, item["file"], item["sha256"]))
    for key, item in data["official_hashes"]["models"].items():
        lines.append("| 模型 | `%s` | %s | `%s` |" % (
            key, item["bytes"], item["sha256"]))
    lines.append("")
    lines.append("来源：llama.cpp `b11612` GitHub Release asset digest；"
                 "HuggingFace LFS oid（`tencent/Hy-MT2-7B-GGUF`、"
                 "`tencent/Hy-MT2-1.8B-GGUF`）。"
                 "下载后强制比对，不一致即删除并换源。")
    lines.append("")
    lines.append("## 3. 一致性核对")
    lines.append("")
    lines.append("| 检查 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    for c in data["consistency"]:
        lines.append("| %s | %s | %s |" % (c["check"], yes(c["ok"]), c["detail"]))
    lines.append("")
    lines.append("## 4. 术语数据规模")
    lines.append("")
    lines.append("| 文件 | 条目 |")
    lines.append("|---|---:|")
    lines.append("| `glossary_builtin.json`（随包，运行时匹配） | %s |" % (
        data["term_data"]["glossary_entries"] or "—"))
    lines.append("| `stalker_termbase.json`（分类导入） | %s |" % (
        data["term_data"]["termbase_entries"] or "—"))
    lines.append("")
    cats = data["term_data"]["termbase_categories"]
    if cats:
        lines.append("分类明细：" + "、".join(
            "%s %s" % (k, v) for k, v in cats.items()))
        lines.append("")
    lines.append("## 5. 安装位置与数据落点")
    lines.append("")
    lines.append("- 插件：解压到 `<工具集>\\plugins\\` ⇒ `plugins\\ai_translate\\"
                 "plugin.py` + `assets\\`（目录插件）。")
    lines.append("- 运行数据（模型/运行库/日志/配置）默认落 "
                 "`%LOCALAPPDATA%\\STALKER Localization Toolkit\\ai_translate`。")
    lines.append("- 绿色版：在 `plugin.py` 同目录放一个空的 `portable.txt`，"
                 "数据改为写插件目录（只读安装位置会自动回落）。")
    lines.append("- 运行库/模型不随包分发，首次使用由插件按官方 SHA-256 下载。")
    lines.append("")
    lines.append("## 6. 校验方式")
    lines.append("")
    lines.append("```powershell")
    lines.append("# 校验当前插件 zip")
    lines.append("Get-FileHash 'dist\\%s' -Algorithm SHA256" % zip_path().name)
    lines.append("")
    lines.append("# 重新打包 + 重生成清单")
    lines.append("python tools/gen_release_manifest.py --build")
    lines.append("")
    lines.append("# CI 门禁（只校验不写文件）")
    lines.append("python tools/gen_release_manifest.py --check")
    lines.append("```")
    lines.append("")
    lines.append("## 7. 备注")
    lines.append("")
    lines.append("- 本清单只读文件，不修改插件；范围仅限 `plugins/ai_translate/` 与发布 zip。")
    lines.append("- 发布包只含代码 + 解析器 + 术语数据；运行库、模型、配置、日志均不随包。")
    lines.append("- 2.x 发行包不带 `_ssl.pyd`：插件网络层用 ctypes + 系统 WinHTTP，"
                 "发布前由一致性检查守住「无 ssl/http/urllib.request 导入链」。")
    lines.append("- 官方哈希取自发布方元数据（2026-10-23）；若上游重新上传同标签产物，"
                 "需重新核对。")
    lines.append("")
    return "\n".join(lines)


def compare_with_committed(data: dict[str, Any]) -> list[str]:
    """与已提交的 release_manifest.json 比对（忽略生成时间与 git 元信息）。"""
    if not OUT_JSON.is_file():
        return ["release_manifest.json 不存在"]
    try:
        old = json.loads(read_text(OUT_JSON))
    except Exception as e:
        return ["release_manifest.json 无法解析：%s" % e]
    keys = ("versions", "artifacts", "zip", "official_hashes", "term_data")
    return [k for k in keys if data.get(k) != old.get(k)]


def main() -> int:
    if not PLUGIN.is_file():
        print("找不到插件：%s" % PLUGIN, file=sys.stderr)
        return 2
    if "--check" in sys.argv:
        # CI 门禁：只校验、不写文件。生成时间每次不同，因此不做整文件 diff；
        # 只比对实质内容（版本/产物哈希/zip/官哈希/术语规模）。
        data = build_manifest()
        bad = [c for c in data["consistency"] if c["ok"] is False]
        diffs = compare_with_committed(data)
        print("一致性：%d 项检查，%d 项不一致"
              % (len(data["consistency"]), len(bad)))
        for c in bad:
            print("  - %s：%s" % (c["check"], c["detail"]))
        if diffs:
            print("与已提交清单不一致：%s（请 --build 后提交）"
                  % ", ".join(diffs))
        ok = not bad and not diffs
        print("check:", "OK" if ok else "FAIL")
        return 0 if ok else 1

    build_zip()
    data = build_manifest()
    OUT_MD.write_text(render_markdown(data), encoding="utf-8", newline="\n")
    OUT_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    print("已生成：%s" % OUT_MD)
    print("已生成：%s" % OUT_JSON)
    print("已打包：%s" % zip_path())
    print("插件版本：%s" % data["versions"]["plugin_version"])
    print("插件 zip：%s" % data["zip"]["sha256"])
    bad = [c for c in data["consistency"] if c["ok"] is False]
    print("一致性：%d 项检查，%d 项不一致" % (len(data["consistency"]), len(bad)))
    for c in bad:
        print("  - %s：%s" % (c["check"], c["detail"]))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
