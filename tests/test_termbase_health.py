# -*- coding: utf-8 -*-
"""术语库健康门：缺陷类必须为 0（合法项白名单见 audit_termbase 文档头）。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from audit_termbase import run_audit  # noqa: E402

TB = os.path.join(ROOT, "assets", "stalker_termbase.json")
GL = os.path.join(ROOT, "assets", "glossary_builtin.json")

# 合法保留项：转写/缩写类值允许无 CJK
NO_CJK_WHITELIST = {"Х-8", "PDA", "КПК", "ПДА"}


class TestTermbaseHealth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rep = run_audit(TB, GL)
        cls.f = cls.rep["findings"]

    def test_no_raw_dup_keys(self):
        self.assertEqual(self.f.get("raw_dup_key", []), [])

    def test_no_cross_conflicts(self):
        self.assertEqual(self.f.get("cross_conflict", []), [])

    def test_no_mojibake(self):
        self.assertEqual(self.f.get("value_mojibake", []), [])

    def test_no_empty_or_invalid(self):
        for kind in ("empty_key", "empty_value", "non_scalar_value",
                     "non_dict_category", "meta_key_in_data",
                     "value_whitespace"):
            self.assertEqual(self.f.get(kind, []), [], kind)

    def test_no_meta_stale_refs(self):
        self.assertEqual(self.f.get("meta_ref_missing", []), [])

    def test_placeholder_preserved_exactly(self):
        # 值不得新增/丢失源侧占位符
        self.assertEqual(self.f.get("value_placeholder_added", []), [])
        self.assertEqual(self.f.get("value_placeholder_lost", []), [])

    def test_no_cjk_only_whitelisted(self):
        bad = [e for e in self.f.get("value_no_cjk", [])
               if e["key"] not in NO_CJK_WHITELIST]
        self.assertEqual(bad, [])

    def test_no_value_eq_key_beyond_whitelist(self):
        bad = [e for e in self.f.get("value_eq_key", [])
               if e["key"] not in NO_CJK_WHITELIST]
        self.assertEqual(bad, [])

    def test_no_long_partial_keys(self):
        self.assertEqual(self.f.get("long_partial_key", []), [])

    def test_total_entries_consistent(self):
        import json
        with open(TB, encoding="utf-8") as handle:
            data = json.load(handle)
        real = sum(len(v) for k, v in data.items() if k != "_meta")
        meta = data["_meta"]
        self.assertEqual(meta.get("total_entries"), real)


if __name__ == "__main__":
    unittest.main()
