#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TBX / TMX 导入导出命令行。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from stalker_translate_core.exchange import export_tbx, export_tmx, import_tbx, import_tmx
from stalker_translate_core.termbase import Termbase


def main() -> int:
    parser = argparse.ArgumentParser(description="TBX / TMX 术语交换")
    sub = parser.add_subparsers(dest="command", required=True)
    tbx_out = sub.add_parser("tbx-export")
    tbx_out.add_argument("--termbase", required=True, type=Path)
    tbx_out.add_argument("--out", required=True, type=Path)
    tbx_in = sub.add_parser("tbx-import")
    tbx_in.add_argument("--in", dest="input", required=True, type=Path)
    tbx_in.add_argument("--out", required=True, type=Path)
    tmx_out = sub.add_parser("tmx-export")
    tmx_out.add_argument("--in", dest="input", required=True, type=Path)
    tmx_out.add_argument("--out", required=True, type=Path)
    tmx_in = sub.add_parser("tmx-import")
    tmx_in.add_argument("--in", dest="input", required=True, type=Path)
    tmx_in.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "tbx-export":
        store = Termbase(str(args.termbase))
        layers = store.load_layers()
        table = dict(layers["canon_table"])
        table.update(layers["names_table"])
        export_tbx(table, args.out, layers["details"])
        result = {"entries": len(table), "output": str(args.out)}
    elif args.command == "tbx-import":
        rows = import_tbx(args.input)
        args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        result = {"entries": len(rows), "output": str(args.out)}
    elif args.command == "tmx-export":
        rows = json.loads(args.input.read_text(encoding="utf-8-sig"))
        pairs = {str(row["source"]): str(row["target"]) for row in rows}
        export_tmx(pairs, args.out)
        result = {"entries": len(pairs), "output": str(args.out)}
    else:
        rows = import_tmx(args.input)
        args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        result = {"entries": len(rows), "output": str(args.out)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
