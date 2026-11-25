#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stalker-translate-online 命令行入口。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from stalker_translate_core.assets import resolve_asset_root

from .app import check_env, create_app
from .server import main as serve_main


def emit(obj, as_json: bool = True) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    elif isinstance(obj, str):
        print(obj)
    else:
        print(json.dumps(obj, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stalker-translate-online")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("--plain", action="store_true", help="纯文本输出")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check"); p.add_argument("--deep", action="store_true")
    p = sub.add_parser("scan"); p.add_argument("input_dir")
    p = sub.add_parser("translate-text"); p.add_argument("text"); p.add_argument("--source-lang", default="auto"); p.add_argument("--target-lang", default="chs"); p.add_argument("--context", default="")
    p = sub.add_parser("translate"); p.add_argument("input_dir"); p.add_argument("--output-dir"); p.add_argument("--source-lang", default="auto"); p.add_argument("--target-lang", default="chs"); p.add_argument("--concurrency", type=int)
    p = sub.add_parser("status"); p.add_argument("job_id")
    p = sub.add_parser("cancel"); p.add_argument("job_id")
    p = sub.add_parser("validate"); p.add_argument("input_dir"); p.add_argument("output_dir")
    p = sub.add_parser("glossary"); p.add_argument("query"); p.add_argument("--limit", type=int, default=20)
    sub.add_parser("serve")
    args = parser.parse_args(argv)

    if args.command == "serve":
        return serve_main()
    cfg, provider, service, jobs = create_app(args.config)
    if args.command == "check":
        root = resolve_asset_root(Path(__file__).resolve().parent.parent / "__init__.py",
                                  "assets")
        emit(check_env(cfg, provider, root, args.deep, service), not args.plain)
    elif args.command == "scan":
        emit(jobs.scan(args.input_dir), not args.plain)
    elif args.command == "translate-text":
        result = service.translate(args.text, args.source_lang,
                                   args.target_lang, args.context)
        emit(result.text if args.plain else {
            "translation": result.text, "retained_source": result.retained_source,
            "cache_hit": result.cache_hit, "usage": result.usage.__dict__,
            "issues": [i.code for i in result.issues],
        }, not args.plain)
    elif args.command == "translate":
        job_id = jobs.start(args.input_dir, args.output_dir,
                            args.source_lang, args.target_lang,
                            args.concurrency)
        while True:
            status = jobs.status(job_id)
            if status.state.value in ("succeeded", "needs_review", "failed",
                                      "cancelled"):
                emit({"job_id": job_id, "state": status.state.value,
                      "progress": status.progress, "total": status.total,
                      "message": status.message,
                      "output_dir": status.output_dir,
                      "usage": status.usage.__dict__,
                      "issues": [i.code for i in status.issues]},
                     not args.plain)
                return 0 if status.state.value == "succeeded" else 1
            time.sleep(0.2)
    elif args.command == "status":
        status = jobs.status(args.job_id)
        emit({"job_id": status.job_id, "state": status.state.value,
              "progress": status.progress, "total": status.total,
              "message": status.message}, not args.plain)
    elif args.command == "cancel":
        status = jobs.cancel(args.job_id)
        emit({"job_id": status.job_id, "state": status.state.value,
              "message": status.message}, not args.plain)
    elif args.command == "validate":
        result = jobs.validate(args.input_dir, args.output_dir)
        emit(result, not args.plain)
        return 0 if result.get("ok") else 1
    elif args.command == "glossary":
        emit({"exact": service.glossary.lookup(args.query),
              "hits": [{"source": k, "translation": v}
                       for k, v in service.glossary.hits(args.query, args.limit)]},
             not args.plain)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
