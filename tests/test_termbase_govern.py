# -*- coding: utf-8 -*-
"""Termbase v3 治理工具回归。"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/termbase_govern.py"
spec = importlib.util.spec_from_file_location("termbase_govern", TOOL)
govern = importlib.util.module_from_spec(spec)
spec.loader.exec_module(govern)


class TermbaseGovernTests(unittest.TestCase):
    def test_collect_approve_apply_and_affected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / "audit.json"
            audit.write_text(json.dumps({"coverage_gap_candidates": [
                {"token": "Zone", "refs": 12},
                {"token": "PDA", "refs": 9},
                {"token": "Heckler", "refs": 7},
            ]}), encoding="utf-8")
            candidates = root / "candidates.json"
            govern.collect([audit], candidates)
            rows = json.loads(candidates.read_text(encoding="utf-8"))
            by_token = {row["token"]: row for row in rows}
            self.assertEqual(by_token["Zone"]["suggested_status"], "contextual")
            self.assertEqual(by_token["PDA"]["suggested_status"], "preserve")
            by_token["Zone"].update({
                "status": "translate", "translation": "禁区",
                "category": "地点与区域", "domain": "location",
            })
            candidates.write_text(json.dumps(rows), encoding="utf-8")
            approved = root / "approved.json"
            govern.approve(candidates, approved)
            termbase = root / "termbase.json"
            termbase.write_text(json.dumps({
                "_meta": {"layers": {"canon": ["地点与区域"]}},
                "地点与区域": {},
            }), encoding="utf-8")
            preview = govern.apply_policy(approved, termbase, False)
            self.assertFalse(preview["applied"])
            self.assertNotIn("term_schema", json.loads(
                termbase.read_text(encoding="utf-8"))["_meta"])
            applied = govern.apply_policy(approved, termbase, True)
            self.assertTrue(applied["applied"])
            self.assertTrue(Path(applied["backup"]).is_file())
            data = json.loads(termbase.read_text(encoding="utf-8"))
            self.assertEqual(data["_meta"]["term_schema"], 3)
            self.assertEqual(data["_meta"]["terms"]["Zone"]["status"], "translate")
            self.assertEqual(data["地点与区域"]["Zone"], "禁区")

            mod = root / "mod/configs/text/eng"
            mod.mkdir(parents=True)
            (mod / "probe.xml").write_text(
                '<string_table><string id="x"><text>Zone and PDA</text>'
                '</string></string_table>', encoding="utf-8")
            output = root / "affected.json"
            result = govern.affected(approved, [root / "mod"], output)
            self.assertEqual(result["affected_entries"], 1)
            hits = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(hits[0]["id"], "x")
            self.assertEqual(set(hits[0]["terms"]), {"zone", "pda"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
