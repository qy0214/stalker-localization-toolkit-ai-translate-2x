#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Termbase v3 候选、审批、备份应用与受影响条目定位。"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from stalker_translate_core.vendor import load_vendor  # noqa: E402
from stalker_translate_core.xray import read_encoding_chain  # noqa: E402

PRESERVE = frozenset({
    "pda", "кпк", "пда", "ak-74", "ak-74m", "fnx-45", "p220", "svu2-a",
    "seva", "nato", "omon", "grau", "tskib", "heckler",
    "koch", "herstal", "remington", "galil", "magnum", "glock", "sauer",
    "mossberg", "benelli", "colt", "walther", "steyr", "aimpoint", "mtech",
})
MODEL_RE = re.compile(r"^[A-Z]{2,}(?:[-/][A-Za-z0-9]+)*$|^[A-Za-z]+[-/]\d[\w\-/]*$")


def classify(token: str) -> str:
    return "preserve" if token.casefold() in PRESERVE or MODEL_RE.match(token) \
        else "contextual"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def collect(audit_paths: list[Path], output: Path) -> dict:
    candidates: dict[str, dict[str, Any]] = {}
    for path in audit_paths:
        data = read_json(path)
        for item in data.get("coverage_gap_candidates", []):
            token = str(item.get("token") or "").strip()
            if not token:
                continue
            row = candidates.setdefault(token.casefold(), {
                "token": token, "refs": 0, "sources": [],
                "suggested_status": classify(token),
                "review_status": "candidate",
            })
            row["refs"] += int(item.get("refs") or 0)
            if str(path) not in row["sources"]:
                row["sources"].append(str(path))
    rows = sorted(candidates.values(), key=lambda row: (-row["refs"], row["token"]))
    write_json(output, rows)
    return {"candidates": len(rows), "output": str(output)}


def approve(input_path: Path, output: Path) -> dict:
    rows = read_json(input_path)
    if not isinstance(rows, list):
        raise ValueError("候选文件必须是 JSON 数组")
    approved = []
    for row in rows:
        token = str(row.get("token") or "").strip()
        if not token:
            continue
        status = str(row.get("status") or row.get("suggested_status")
                     or classify(token)).strip().lower()
        if status not in ("translate", "preserve", "contextual"):
            raise ValueError("非法术语状态：%s" % status)
        item = dict(row)
        item["token"] = token
        item["status"] = status
        item["review_status"] = "approved"
        approved.append(item)
    write_json(output, approved)
    return {"approved": len(approved), "output": str(output)}


def apply_policy(approved_path: Path, termbase_path: Path, apply: bool) -> dict:
    rows = read_json(approved_path)
    if not isinstance(rows, list):
        raise ValueError("审批文件必须是 JSON 数组")
    data = read_json(termbase_path)
    if not isinstance(data, dict):
        raise ValueError("术语库必须是 JSON 对象")
    meta = data.setdefault("_meta", {})
    meta["term_schema"] = 3
    meta.setdefault("terms", {})
    changed = 0
    for row in rows:
        if row.get("review_status") != "approved":
            continue
        token = str(row.get("token") or "").strip()
        if not token:
            continue
        detail = dict(meta["terms"].get(token) or {})
        for key in ("status", "preferred", "forbidden", "allowed_variants",
                    "domain", "pos", "inflections", "ambiguity_group",
                    "provenance", "confidence"):
            if key in row:
                detail[key] = row[key]
        detail["review_status"] = "approved"
        meta["terms"][token] = detail
        category = str(row.get("category") or "").strip()
        translation = str(row.get("translation") or "").strip()
        if category and translation:
            table = data.setdefault(category, {})
            table[token] = translation
            meta["total_entries"] = sum(len(value) for key, value in data.items()
                                        if key != "_meta" and isinstance(value, dict))
            meta.setdefault("categories", {})[category] = len(table)
        changed += 1
    if not apply:
        return {"changed": changed, "applied": False}
    backup = termbase_path.with_name(
        termbase_path.name + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(termbase_path, backup)
    temporary = termbase_path.with_suffix(termbase_path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                         encoding="utf-8")
    temporary.replace(termbase_path)
    return {"changed": changed, "applied": True, "backup": str(backup)}


def affected(approved_path: Path, roots: list[Path], output: Path) -> dict:
    rows = read_json(approved_path)
    terms = [str(row.get("token") or "").strip().casefold() for row in rows
             if str(row.get("token") or "").strip()]
    package = load_vendor(str(ROOT / "assets/xray_pkg.zip"))
    hits = []
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(path for path in root.rglob("*.xml") if path.is_file())
    for path in sorted(set(files)):
        try:
            entries = package.parse_xray_text_xml(
                str(path), list(read_encoding_chain(str(path))))
        except Exception:
            continue
        for entry in entries or []:
            source = str((getattr(entry, "texts", None) or {}).get("text")
                         or (getattr(entry, "texts", None) or {}).get("rus")
                         or (getattr(entry, "texts", None) or {}).get("eng") or "")
            matched = [term for term in terms if term and term in source.casefold()]
            if matched:
                hits.append({"file": str(path), "id": entry.id,
                             "terms": matched, "source": source[:500]})
    write_json(output, hits)
    return {"files_scanned": len(files), "affected_entries": len(hits),
            "output": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Termbase v3 治理工具")
    sub = parser.add_subparsers(dest="command", required=True)
    collect_parser = sub.add_parser("collect", help="从审计报告收集候选")
    collect_parser.add_argument("--audit", action="append", required=True, type=Path)
    collect_parser.add_argument("--out", required=True, type=Path)
    approve_parser = sub.add_parser("approve", help="标记人工审批结果")
    approve_parser.add_argument("--in", dest="input", required=True, type=Path)
    approve_parser.add_argument("--out", required=True, type=Path)
    apply_parser = sub.add_parser("apply", help="备份并应用审批策略")
    apply_parser.add_argument("--approved", required=True, type=Path)
    apply_parser.add_argument("--termbase", required=True, type=Path)
    apply_parser.add_argument("--apply", action="store_true", help="实际写盘；默认只预览")
    affected_parser = sub.add_parser("affected", help="定位受影响条目")
    affected_parser.add_argument("--approved", required=True, type=Path)
    affected_parser.add_argument("--root", action="append", required=True, type=Path)
    affected_parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "collect":
        result = collect(args.audit, args.out)
    elif args.command == "approve":
        result = approve(args.input, args.out)
    elif args.command == "apply":
        result = apply_policy(args.approved, args.termbase, args.apply)
    else:
        result = affected(args.approved, args.root, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
