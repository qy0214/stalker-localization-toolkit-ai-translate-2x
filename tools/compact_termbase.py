#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按真实 MOD 使用审计精简零命中软词条与未命中长描述。"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURATED_KEYS = {
    "Browning High-Power", "Metal Key", "Circuit board", "Army body armor",
    "Anomalous stabilizer", "activated bombmaker's briefcase",
    "professor kruglov's flash drive", "insane stalker's flash drive",
    "the prisoner's flash drive", "lucky's pda", "tyran's pda", "borov's key",
    "giant's younger brother", "great-grandma's beads", "grandma's beads",
    "izomorf", "anomlaous stabilizer", "Circus", "Volcano", '"Monolith" Chief',
}


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                         + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def _flatten(document):
    rows = {}
    for category, table in document.items():
        if category.startswith("_") or not isinstance(table, dict):
            continue
        for key, value in table.items():
            if isinstance(value, str):
                rows[str(key)] = value
    return rows


def _remove_keys(table, keys):
    for key in list(table):
        if str(key) in keys:
            del table[key]
        elif isinstance(table[key], dict):
            _remove_keys(table[key], keys)


def _protected_keys(meta):
    protected = set((meta.get("terms") or {}).keys())
    protected |= set((meta.get("variants") or {}).keys())
    protected |= set((meta.get("variants") or {}).values())
    protected |= set(meta.get("quote_required") or ())
    return {str(key) for key in protected}


def _core_long_is_safe_to_remove(key):
    if re.search(r"\d", key) or "/" in key or "\\" in key:
        return False
    if re.search(r"\b[A-Z]{2,}\b", key):
        return False
    return True


def compact(termbase_path, builtin_path, usage_path, backup_dir, *, apply=False,
            keep_keys=()):
    termbase_path = Path(termbase_path)
    builtin_path = Path(builtin_path)
    document = json.loads(termbase_path.read_text(encoding="utf-8"))
    builtin = json.loads(builtin_path.read_text(encoding="utf-8"))
    usage = json.loads(Path(usage_path).read_text(encoding="utf-8"))
    current = _flatten(document)
    report_rows = usage.get("terms")
    if not isinstance(report_rows, list):
        raise ValueError("使用审计缺少 terms 数组")
    reported = {str(row.get("key")): row for row in report_rows}
    if set(current) != set(reported):
        raise ValueError("使用审计与当前词库键集合不一致，拒绝精简")
    for key, value in current.items():
        if reported[key].get("value") != value:
            raise ValueError("使用审计与当前词库译文不一致：%s" % key)
    meta = document["_meta"]
    protected = _protected_keys(meta) | CURATED_KEYS | {str(key) for key in keep_keys}
    deletions = {}
    for key, row in reported.items():
        decision = row.get("decision")
        candidate = decision in ("candidate_unused_soft", "candidate_unused_long")
        if (decision == "keep_core" and row.get("layer") == "canon"
                and int(row.get("source_chars") or len(key)) >= 20
                and _core_long_is_safe_to_remove(key)):
            candidate = True
            decision = "candidate_unused_core_long"
        if not candidate or key in protected:
            continue
        deletions[key] = {
            "value": current[key], "layer": row.get("layer", ""),
            "source_chars": len(key), "decision": decision,
        }
    result = {
        "term_count_before": len(current),
        "delete_count": len(deletions),
        "term_count_after": len(current) - len(deletions),
        "deleted_by_layer": dict(Counter(item["layer"] for item in deletions.values())),
        "deleted_by_decision": dict(Counter(item["decision"] for item in deletions.values())),
        "deleted": deletions,
        "scanned_entries": usage.get("_meta", {}).get("scanned", {}).get("entries", 0),
    }
    if not apply:
        return result
    if not deletions:
        return result
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    termbase_backup = backup_dir / ("TERMBASE_COMPACT_BACKUP_%s.json" % stamp)
    builtin_backup = backup_dir / ("GLOSSARY_BUILTIN_COMPACT_BACKUP_%s.json" % stamp)
    _atomic_json(termbase_backup, document)
    _atomic_json(builtin_backup, builtin)
    updated = copy.deepcopy(document)
    meta = updated["_meta"]
    for category, table in updated.items():
        if category.startswith("_") or not isinstance(table, dict):
            continue
        for key in list(table):
            if str(key) in deletions:
                del table[key]
    _remove_keys(builtin, set(deletions))
    categories = meta.setdefault("categories", {})
    for category, table in updated.items():
        if category.startswith("_") or not isinstance(table, dict):
            continue
        categories[category] = len(table)
    meta["total_entries"] = len(current) - len(deletions)
    existing = set(_flatten(updated))
    for field in ("case_sensitive", "quote_required"):
        if isinstance(meta.get(field), list):
            meta[field] = [key for key in meta[field] if str(key) in existing]
    for category, info in (meta.get("reference_layers") or {}).items():
        if isinstance(info, dict) and category in categories:
            info["entries"] = categories[category]
    meta["updated"] = datetime.now().strftime("%Y-%m-%d")
    meta["builder"] = str(meta.get("builder", "")) + " + tools/compact_termbase.py"
    meta["compaction_2026_11_26"] = {
        "scanned_entries": result["scanned_entries"],
        "deleted": len(deletions),
        "deleted_by_layer": result["deleted_by_layer"],
        "deleted_by_decision": result["deleted_by_decision"],
        "policy": "删除全 MOD 零命中 names/reference 与无数字/斜杠/缩写的零命中长描述；"
                  "canon 短核心词、technical、策略、变体、引号限定词始终保留。",
        "backup": termbase_backup.name,
        "builtin_backup": builtin_backup.name,
    }
    _atomic_json(termbase_path, updated)
    _atomic_json(builtin_path, builtin)
    result["backups"] = [str(termbase_backup), str(builtin_backup)]
    return result


def restore_curated(termbase_path, builtin_path, backup_path):
    termbase_path = Path(termbase_path)
    builtin_path = Path(builtin_path)
    backup_path = Path(backup_path)
    current = json.loads(termbase_path.read_text(encoding="utf-8"))
    original = json.loads(backup_path.read_text(encoding="utf-8"))
    builtin = json.loads(builtin_path.read_text(encoding="utf-8"))
    original_builtin = json.loads(
        Path(backup_path).with_name(backup_path.name.replace(
            "TERMBASE_COMPACT_BACKUP_", "GLOSSARY_BUILTIN_COMPACT_BACKUP_")
        ).read_text(encoding="utf-8"))
    restored = []
    existing = set(_flatten(current))
    for category, table in original.items():
        if category.startswith("_") or not isinstance(table, dict):
            continue
        target = current.setdefault(category, {})
        for key, value in table.items():
            if str(key) in CURATED_KEYS and key not in existing:
                target[key] = value
                existing.add(str(key))
                if key in original_builtin:
                    builtin[key] = original_builtin[key]
                restored.append(str(key))
    meta = current["_meta"]
    for category, table in current.items():
        if category.startswith("_") or not isinstance(table, dict):
            continue
        meta.setdefault("categories", {})[category] = len(table)
    meta["total_entries"] = len(_flatten(current))
    existing = set(_flatten(current))
    for field in ("case_sensitive", "quote_required"):
        values = list(meta.get(field) or ())
        for key in restored:
            if key not in values and key in (original.get("_meta", {}).get(field) or ()):
                values.append(key)
        meta[field] = [key for key in values if str(key) in existing]
    compaction = meta.get("compaction_2026_11_26")
    if isinstance(compaction, dict):
        compaction["restored_after_regression"] = sorted(restored)
        compaction["effective_deleted"] = int(compaction.get("deleted", 0)) - len(restored)
    _atomic_json(termbase_path, current)
    _atomic_json(builtin_path, builtin)
    return {"restored": sorted(restored), "term_count": meta["total_entries"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--termbase", default=str(ROOT / "assets/stalker_termbase.json"))
    parser.add_argument("--builtin", default=str(ROOT / "assets/glossary_builtin.json"))
    parser.add_argument("--usage", default=str(
        ROOT / "audit-reports/TERMBASE_USAGE_20261126.json"))
    parser.add_argument("--backup-dir", default=str(ROOT / "audit-reports"))
    parser.add_argument("--keep-key", action="append", default=[])
    parser.add_argument("--restore-curated", action="store_true")
    parser.add_argument("--backup")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.restore_curated:
        if not args.backup:
            parser.error("--restore-curated 需要 --backup 指定原始词库备份")
        try:
            result = restore_curated(args.termbase, args.builtin, args.backup)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print("恢复失败：%s" % exc, file=sys.stderr)
            return 2
        print("恢复回归必备别名 %d 条，词库现有 %d 条：%s" % (
            len(result["restored"]), result["term_count"], ", ".join(result["restored"])))
        return 0
    try:
        result = compact(args.termbase, args.builtin, args.usage, args.backup_dir,
                         apply=args.apply, keep_keys=args.keep_key)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("精简失败：%s" % exc, file=sys.stderr)
        return 2
    print("词条 %d → %d，拟删除/已删除 %d" % (
        result["term_count_before"], result["term_count_after"], result["delete_count"]))
    print("分层：%s" % json.dumps(result["deleted_by_layer"], ensure_ascii=False))
    print("口径：%s" % json.dumps(result["deleted_by_decision"], ensure_ascii=False))
    for key, item in list(result["deleted"].items())[:20]:
        print("  [%s] %r -> %r" % (
            item["decision"], key[:70], item["value"][:60]))
    if not args.apply:
        print("预览模式；确认口径后加 --apply 执行（自动备份词库与 builtin）")
    else:
        print("备份：%s" % ", ".join(result.get("backups", ())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
