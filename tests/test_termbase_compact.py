# -*- coding: utf-8 -*-
"""术语库低价值词条精简与保留策略测试。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from compact_termbase import compact, restore_curated  # noqa: E402


class TermbaseCompactTests(unittest.TestCase):
    def test_preview_and_apply_preserve_core_policy_used_and_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            termbase = root / "termbase.json"
            builtin = root / "builtin.json"
            document = {
                "_meta": {
                    "total_entries": 7,
                    "categories": {"核心": 2, "人物": 2, "参考": 3},
                    "layers": {"canon": ["核心"], "names": ["人物"],
                               "reference": ["参考"]},
                    "variants": {"Alias": "Canon"},
                    "terms": {"Policy": {"status": "preserve"}},
                },
                "核心": {"Canon": "核心词", "UnusedCore": "无用核心"},
                "人物": {"UnusedName": "无用人物", "Policy": "保留策略"},
                "参考": {"UnusedRef": "无用参考", "UsedRef": "已用参考",
                         "Alias": "别名"},
            }
            termbase.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            builtin.write_text(json.dumps({
                "UnusedName": "无用人物", "UnusedRef": "无用参考",
                "UsedRef": "已用参考", "Canon": "核心词",
                "嵌套": {"UnusedRef": "无用参考"},
            }, ensure_ascii=False), encoding="utf-8")
            rows = [
                {"key": "UnusedName", "value": "无用人物", "layer": "names",
                 "decision": "candidate_unused_soft"},
                {"key": "UnusedRef", "value": "无用参考", "layer": "reference",
                 "decision": "candidate_unused_long", "source_chars": 48},
                {"key": "UsedRef", "value": "已用参考", "layer": "reference",
                 "decision": "keep_used", "hits": 2},
                {"key": "Alias", "value": "别名", "layer": "reference",
                 "decision": "candidate_unused_soft"},
                {"key": "Policy", "value": "保留策略", "layer": "names",
                 "decision": "candidate_unused_soft"},
                {"key": "UnusedCore", "value": "无用核心", "layer": "canon",
                 "decision": "keep_core"},
                {"key": "Canon", "value": "核心词", "layer": "canon",
                 "decision": "keep_used", "hits": 5},
            ]
            report = root / "usage.json"
            report.write_text(json.dumps({"terms": rows}, ensure_ascii=False), encoding="utf-8")
            preview = compact(termbase, builtin, report, root / "backups", apply=False)
            self.assertEqual(preview["delete_count"], 2)
            self.assertEqual(json.loads(termbase.read_text(encoding="utf-8")), document)
            result = compact(termbase, builtin, report, root / "backups", apply=True)
            self.assertEqual(result["delete_count"], 2)
            updated = json.loads(termbase.read_text(encoding="utf-8"))
            self.assertNotIn("UnusedName", updated["人物"])
            self.assertNotIn("UnusedRef", updated["参考"])
            self.assertIn("Policy", updated["人物"])
            self.assertIn("Alias", updated["参考"])
            self.assertIn("UnusedCore", updated["核心"])
            self.assertEqual(updated["_meta"]["total_entries"], 5)
            self.assertEqual(updated["_meta"]["categories"]["人物"], 1)
            self.assertEqual(json.loads(builtin.read_text(encoding="utf-8")), {
                "UsedRef": "已用参考", "Canon": "核心词", "嵌套": {}})
            backups = list((root / "backups").glob("*.json"))
            self.assertEqual(len(backups), 2)

    def test_regression_aliases_are_restored_from_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            termbase = root / "termbase.json"
            builtin = root / "builtin.json"
            backup = root / "TERMBASE_COMPACT_BACKUP_test.json"
            builtin_backup = root / "GLOSSARY_BUILTIN_COMPACT_BACKUP_test.json"
            original = {
                "_meta": {"total_entries": 1, "categories": {"物品": 1},
                          "case_sensitive": ["Browning High-Power"],
                          "compaction_2026_11_26": {"deleted": 1}},
                "物品": {"Browning High-Power": "勃朗宁大威力手枪"},
            }
            current = {
                "_meta": {"total_entries": 0, "categories": {"物品": 0},
                          "case_sensitive": [],
                          "compaction_2026_11_26": {"deleted": 1}},
                "物品": {},
            }
            backup.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
            builtin_backup.write_text(json.dumps({
                "Browning High-Power": "勃朗宁大威力手枪",
            }, ensure_ascii=False), encoding="utf-8")
            termbase.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
            builtin.write_text("{}", encoding="utf-8")
            result = restore_curated(termbase, builtin, backup)
            self.assertEqual(result["restored"], ["Browning High-Power"])
            updated = json.loads(termbase.read_text(encoding="utf-8"))
            self.assertEqual(updated["物品"]["Browning High-Power"], "勃朗宁大威力手枪")
            self.assertEqual(updated["_meta"]["total_entries"], 1)
            self.assertEqual(updated["_meta"]["compaction_2026_11_26"]["effective_deleted"], 0)
            self.assertIn("Browning High-Power", updated["_meta"]["case_sensitive"])

    def test_stale_usage_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            termbase = root / "termbase.json"
            builtin = root / "builtin.json"
            termbase.write_text(json.dumps({
                "_meta": {"layers": {"reference": ["参考"]}},
                "参考": {"Old": "旧"},
            }, ensure_ascii=False), encoding="utf-8")
            builtin.write_text("{}", encoding="utf-8")
            report = root / "usage.json"
            report.write_text(json.dumps({"terms": [{
                "key": "Changed", "value": "改", "layer": "reference",
                "decision": "candidate_unused_soft",
            }]}, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                compact(termbase, builtin, report, root / "backups", apply=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
