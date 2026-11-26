# -*- coding: utf-8 -*-
"""TBX/TMX、可选词形与上下文术语提示回归。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from stalker_translate_core.exchange import export_tbx, export_tmx, import_tbx, import_tmx
from stalker_translate_core.glossary import glossary_hint_block
from stalker_translate_core.morphology import MorphologyNormalizer, conservative_stem


class ExchangeMorphologyTests(unittest.TestCase):
    def test_tbx_roundtrip_preserves_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "terms.tbx"
            export_tbx({"Zone": "禁区"}, path,
                       {"Zone": {"status": "translate", "provenance": "test"}})
            rows = import_tbx(path)
            self.assertEqual(rows[0]["source"], "Zone")
            self.assertEqual(rows[0]["target"], "禁区")
            self.assertEqual(rows[0]["status"], "translate")
            self.assertEqual(rows[0]["provenance"], "test")

    def test_tmx_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "terms.tmx"
            export_tmx({"Привет": "你好"}, path)
            rows = import_tmx(path)
            self.assertEqual(rows[0]["source"], "Привет")
            self.assertEqual(rows[0]["target"], "你好")

    def test_morphology_is_optional_and_conservative(self):
        normalizer = MorphologyNormalizer(enabled=False)
        self.assertFalse(normalizer.available)
        self.assertEqual(normalizer.normalize("ЗОНА"), "зона")
        self.assertEqual(conservative_stem("Зоной"), "зон")

    def test_context_prioritizes_relevant_terms(self):
        block, _cost = glossary_hint_block(
            [("Boar", "野猪"), ("Zone", "禁区")], context="The Zone is dangerous.")
        self.assertLess(block.index("Zone"), block.index("Boar"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
