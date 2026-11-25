#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台中立的 STALKER 在线翻译技能包装脚本。

优先使用已安装的 stalker-translate-online CLI；技能包内置 runtime/ 时
直接使用包内 CLI。脚本只负责定位与透传，不实现第二套翻译逻辑。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent


def candidates(explicit: str | None) -> list[list[str]]:
    if explicit:
        return [[explicit]]
    result: list[list[str]] = []
    env_cli = os.environ.get("STALKER_TRANSLATE_ONLINE_CLI")
    if env_cli:
        result.append([env_cli])
    found = shutil.which("stalker-translate-online")
    if found:
        result.append([found])
    bundled = SKILL_ROOT / "runtime" / "cli.py"
    if bundled.is_file():
        result.append([sys.executable, str(bundled)])
    repo = SKILL_ROOT.parents[1] / "products" / "mcp_online" / "cli.py"
    if repo.is_file():
        result.append([sys.executable, str(repo)])
    result.append([sys.executable, "-m", "stalker_translate_online"])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stalker-translate")
    parser.add_argument("--cli", help="在线 CLI 路径或命令")
    parser.add_argument("--config", help="在线配置文件路径")
    parser.add_argument("--json", action="store_true", default=True,
                        help="输出 JSON（默认）")
    parser.add_argument("--plain", action="store_true", help="纯文本输出")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check"); p.add_argument("--deep", action="store_true")
    p = sub.add_parser("scan"); p.add_argument("input_dir")
    p = sub.add_parser("translate-text"); p.add_argument("text")
    p.add_argument("--source-lang", default="auto")
    p.add_argument("--target-lang", default="chs")
    p.add_argument("--context", default="")
    p = sub.add_parser("translate"); p.add_argument("input_dir")
    p.add_argument("--output-dir")
    p.add_argument("--source-lang", default="auto")
    p.add_argument("--target-lang", default="chs")
    p.add_argument("--concurrency", type=int)
    p = sub.add_parser("validate"); p.add_argument("input_dir"); p.add_argument("output_dir")
    p = sub.add_parser("glossary"); p.add_argument("query"); p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("plan"); p.add_argument("input_dir"); p.add_argument("--output-dir")
    p = sub.add_parser("extract"); p.add_argument("input_dir")
    p.add_argument("--chunk", type=int, default=200); p.add_argument("--part", type=int, default=1)
    p.add_argument("--output-dir")
    p = sub.add_parser("merge"); p.add_argument("input_dir"); p.add_argument("answers_file")
    p.add_argument("--output-dir")
    args = parser.parse_args(argv)

    command: list[str] | None = None
    errors: list[str] = []
    for base in candidates(args.cli):
        probe = list(base)
        if len(probe) >= 2 and probe[0] == sys.executable and probe[1].endswith(".py"):
            if not Path(probe[1]).is_file():
                errors.append("缺少 CLI：%s" % probe[1])
                continue
        elif not shutil.which(probe[0]):
            errors.append("找不到命令：%s" % probe[0])
            continue
        command = probe
        break
    if command is None:
        for message in errors:
            print(message, file=sys.stderr)
        print("请先安装 stalker-translate-online，或设置 STALKER_TRANSLATE_ONLINE_CLI。",
              file=sys.stderr)
        return 2

    forwarded = [args.command]
    if args.command == "check" and args.deep:
        forwarded.append("--deep")
    elif args.command in ("scan", "translate", "validate"):
        forwarded.append(args.input_dir)
        if args.command == "translate" and args.output_dir:
            forwarded.extend(["--output-dir", args.output_dir])
        if args.command in ("translate", "translate-text"):
            forwarded.extend(["--source-lang", args.source_lang,
                              "--target-lang", args.target_lang])
        if args.command == "translate" and args.concurrency:
            forwarded.extend(["--concurrency", str(args.concurrency)])
        if args.command == "validate":
            forwarded.append(args.output_dir)
    elif args.command == "translate-text":
        forwarded.append(args.text)
        forwarded.extend(["--source-lang", args.source_lang,
                          "--target-lang", args.target_lang])
        if args.context:
            forwarded.extend(["--context", args.context])
    elif args.command == "glossary":
        forwarded.extend([args.query, "--limit", str(args.limit)])
    elif args.command in ("plan", "extract", "merge"):
        forwarded.append(args.input_dir)
        if args.command == "merge":
            forwarded.append(args.answers_file)
        if args.command == "extract":
            forwarded.extend(["--chunk", str(args.chunk),
                              "--part", str(args.part)])
        if args.output_dir:
            forwarded.extend(["--output-dir", args.output_dir])

    prefix = list(command)
    if args.config:
        if len(prefix) >= 2 and prefix[0] == sys.executable \
                and prefix[1].endswith(".py"):
            prefix.extend(["--config", args.config])
        else:
            prefix.extend(["--config", args.config])
    if args.plain:
        prefix.append("--plain")
    return subprocess.run(prefix + forwarded).returncode


if __name__ == "__main__":
    raise SystemExit(main())
