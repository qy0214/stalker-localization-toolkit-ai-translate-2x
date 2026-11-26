#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""核对术语库在全部真实 MOD 源文中的精确/子串命中，预览低价值词条。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "tools"))

from replay_filters_all_mods import MODS, entries, xml_files  # noqa: E402
from stalker_translate_core.matching import GlossaryIndex  # noqa: E402
from stalker_translate_core.termbase import Termbase  # noqa: E402


def layer_map(document):
    layers = document.get("_meta", {}).get("layers", {})
    result = {}
    for layer, categories in layers.items():
        for category in categories:
            result[category] = layer
    return result


def audit(mods, termbase_path):
    document = json.loads(Path(termbase_path).read_text(encoding="utf-8"))
    store = Termbase(str(termbase_path))
    snapshot = store.load_layers()
    mapping = {}
    layers = layer_map(document)
    row_layer = {}
    for category, table in document.items():
        if category.startswith("_") or not isinstance(table, dict):
            continue
        for key, value in table.items():
            if isinstance(value, str):
                mapping[str(key)] = value
                row_layer[str(key)] = layers.get(category, "unclassified")
    index = GlossaryIndex(
        mapping, case_sensitive=snapshot["case_sensitive"],
        variants=snapshot["variants"])
    exact = {}
    for key in mapping:
        folded = key.strip().strip('"\'«»“”‘’').casefold().replace("ё", "е")
        exact.setdefault(folded, []).append(key)
    hits = Counter()
    exact_hits = Counter()
    mods_by_term = {}
    examples = {}
    scanned = Counter()
    started = time.time()
    for mod, root in mods.items():
        if not Path(root).is_dir():
            print("跳过（不存在）：%s" % mod, flush=True)
            continue
        files = xml_files(root)
        print("扫描 %s：%d 个 XML" % (mod, len(files)), flush=True)
        for file_index, path in enumerate(files, 1):
            values = entries(path)
            if values is None:
                scanned["parse_fail_files"] += 1
                continue
            rel = Path(path).relative_to(root).as_posix()
            for source_id, source in values.items():
                if not isinstance(source, str) or not source.strip():
                    continue
                scanned["entries"] += 1
                folded = source.strip().strip('"\'«»“”‘’').casefold().replace("ё", "е")
                for key in exact.get(folded, ()):
                    exact_hits[key] += 1
                    hits[key] += 1
                    mods_by_term.setdefault(key, set()).add(mod)
                    examples.setdefault(key, []).append("%s:%s" % (rel, source_id))
                for _start, _end, key in index.find(source):
                    hits[key] += 1
                    mods_by_term.setdefault(key, set()).add(mod)
                    if len(examples.setdefault(key, [])) < 3:
                        examples[key].append("%s:%s" % (rel, source_id))
            if file_index % 100 == 0:
                print("  %s %d/%d，命中词条 %d" % (
                    mod, file_index, len(files), len(hits)), flush=True)
    policies = set(snapshot.get("details") or ())
    variants = set(snapshot.get("variants") or ())
    variants |= set((snapshot.get("variants") or {}).values())
    protected = policies | variants
    rows = []
    for key, value in mapping.items():
        layer = row_layer[key]
        count = hits[key]
        if count:
            decision = "keep_used"
        elif layer not in ("names", "reference"):
            decision = "keep_core"
        elif key in protected:
            decision = "keep_policy"
        elif len(key) >= 40:
            decision = "candidate_unused_long"
        else:
            decision = "candidate_unused_soft"
        rows.append({
            "key": key,
            "value": value,
            "layer": layer,
            "source_chars": len(key),
            "target_chars": len(value),
            "hits": count,
            "exact_hits": exact_hits[key],
            "mods": sorted(mods_by_term.get(key, ())),
            "examples": examples.get(key, [])[:3],
            "decision": decision,
        })
    rows.sort(key=lambda row: (row["decision"], -row["source_chars"], row["key"]))
    return {
        "_meta": {
            "termbase": str(termbase_path),
            "terms": len(rows),
            "mods": {name: root for name, root in mods.items() if Path(root).is_dir()},
            "scanned": dict(scanned),
            "seconds": round(time.time() - started, 2),
            "decisions": dict(Counter(row["decision"] for row in rows)),
            "policy": "只预览 names/reference 零命中词条；canon/technical、策略与变体始终保留。",
        },
        "terms": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--termbase", default=str(ROOT / "assets/stalker_termbase.json"))
    parser.add_argument("--extra-root", action="append", default=[])
    parser.add_argument("--output", default=str(
        ROOT / "audit-reports/TERMBASE_USAGE_20261126.json"))
    args = parser.parse_args(argv)
    mods = dict(MODS)
    for index, root in enumerate(args.extra_root, 1):
        mods["额外语料-%d" % index] = root
    report = audit(mods, args.termbase)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8", newline="\n")
    print(json.dumps(report["_meta"], ensure_ascii=False, indent=2))
    print("报告：%s" % output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
