# -*- coding: utf-8 -*-
"""LQA 金标、指标与 CI 报告契约测试。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "tools"))

from stalker_translate_core.quality import (  # noqa: E402
    evaluate_lqa,
    gate_lqa,
    render_junit,
    render_sarif,
)
from lqa_gate import main  # noqa: E402


class LqaGateTests(unittest.TestCase):
    def setUp(self):
        self.cases = [
            {
                "id": "clean-1",
                "file": "configs/text/eng/dialogs.xml",
                "source_id": "hello",
                "source": "Hello, stalker.",
                "candidate": "你好，潜行者。",
                "expected_issues": [],
                "usage": {"input_tokens": 20, "output_tokens": 8, "cost": 0.001},
                "model_score": 0.99,
            },
            {
                "id": "number-1",
                "file": "configs/text/eng/dialogs.xml",
                "source_id": "pay",
                "source": "Pay me 30000 rubles.",
                "candidate": "付给我20000卢布。",
                "expected_issues": ["number_fidelity"],
                "usage": {"input_tokens": 18, "output_tokens": 7, "cost": 0.002},
                "model_score": 5.0,
            },
            {
                "id": "token-1",
                "file": "configs/text/eng/dialogs.xml",
                "source_id": "mask",
                "source": "Use $name and %s now.",
                "candidate": "现在使用$name。",
                "expected_issues": ["runtime_token_damage"],
            },
        ]

    def test_deterministic_evidence_and_metrics(self):
        report = evaluate_lqa(self.cases)
        self.assertEqual(report["metrics"]["true_positives"], 2)
        self.assertEqual(report["metrics"]["false_positives"], 0)
        self.assertEqual(report["metrics"]["false_negatives"], 0)
        self.assertEqual(report["metrics"]["precision"], 1.0)
        self.assertEqual(report["metrics"]["recall"], 1.0)
        self.assertEqual(report["usage"]["input_tokens"], 38)
        self.assertAlmostEqual(report["usage"]["cost"], 0.003)
        number = next(item for item in report["results"] if item["id"] == "number-1")
        self.assertEqual(number["detected"][0]["code"], "number_fidelity")
        self.assertEqual(number["detected"][0]["evidence"], ["30000"])
        self.assertEqual(number["status"], "matched")

    def test_mismatches_fail_gate_but_model_score_does_not(self):
        broken = [dict(self.cases[1], expected_issues=[])]
        report = gate_lqa(broken)
        self.assertFalse(report["ok"])
        self.assertEqual(report["metrics"]["false_positives"], 1)
        self.assertTrue(gate_lqa(self.cases)["ok"])
        scored = [dict(self.cases[0], model_score=0.0)]
        self.assertTrue(gate_lqa(scored)["ok"])

    def test_predictions_merge_and_missing_prediction_is_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "predictions.jsonl"
            path.write_text(json.dumps({
                "id": "clean-1", "candidate": "你好，潜行者。",
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            args = [
                "--golden", str(Path(tmp) / "golden.jsonl"),
                "--predictions", str(path),
            ]
            (Path(tmp) / "golden.jsonl").write_text(
                "\n".join(json.dumps(case, ensure_ascii=False) for case in self.cases),
                encoding="utf-8")
            self.assertEqual(main(args), 1)

    def test_sarif_and_junit_are_standard_and_private(self):
        report = gate_lqa(self.cases)
        sarif = render_sarif(report)
        self.assertEqual(sarif["version"], "2.1.0")
        self.assertEqual(len(sarif["runs"]), 1)
        self.assertEqual(sarif["runs"][0]["results"], [])
        serialized = json.dumps(sarif, ensure_ascii=False)
        self.assertNotIn("Hello, stalker.", serialized)
        self.assertNotIn("你好，潜行者。", serialized)
        junit = render_junit(report)
        root = ET.fromstring(junit)
        self.assertEqual(root.tag, "testsuites")
        suite = root.find("testsuite")
        self.assertEqual(suite.get("tests"), "3")
        self.assertEqual(suite.get("failures"), "0")
        self.assertNotIn("Hello, stalker.", junit)

        broken = gate_lqa([dict(self.cases[1], expected_issues=[])])
        results = render_sarif(broken)["runs"][0]["results"]
        self.assertEqual(results[0]["ruleId"], "number_fidelity")
        self.assertEqual(results[0]["level"], "error")
        location = results[0]["locations"][0]["physicalLocation"]
        self.assertEqual(location["artifactLocation"]["uri"],
                         "configs/text/eng/dialogs.xml")
        self.assertEqual(location["logicalLocations"][0]["name"], "pay")
        self.assertIn("failures=\"1\"", render_junit(broken))

    def test_false_negative_is_reported_without_leaking_text(self):
        report = gate_lqa([dict(self.cases[1], candidate="付给我30000卢布。")])
        self.assertFalse(report["ok"])
        result = render_sarif(report)["runs"][0]["results"][0]
        self.assertEqual(result["ruleId"], "number_fidelity")
        self.assertIn("未检出", result["message"]["text"])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("30000", serialized)
        self.assertNotIn("卢布", serialized)

    def test_real_mod_golden_dataset_is_complete_and_green(self):
        path = ROOT / "tests/lqa_golden.jsonl"
        cases = [json.loads(line) for line in path.read_text(
            encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(cases), 360)
        self.assertEqual(len({case["id"] for case in cases}), 360)
        self.assertEqual(sum(case["review_status"] == "human_gold"
                             for case in cases), 200)
        self.assertEqual(sum(case["review_status"] == "derived_gold"
                             for case in cases), 160)
        report = gate_lqa(cases)
        self.assertTrue(report["ok"])
        self.assertEqual(report["metrics"]["false_positives"], 0)
        self.assertEqual(report["metrics"]["false_negatives"], 0)
        self.assertEqual(report["metrics"]["human_correction_survival"], 1.0)

    def test_cli_writes_all_reports_and_returns_gate_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden = root / "golden.jsonl"
            golden.write_text(
                "\n".join(json.dumps(case, ensure_ascii=False) for case in self.cases),
                encoding="utf-8")
            sarif = root / "lqa.sarif"
            junit = root / "lqa.xml"
            output = root / "lqa.json"
            code = main([
                "--golden", str(golden), "--sarif", str(sarif),
                "--junit", str(junit), "--json", str(output),
            ])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(sarif.read_text(encoding="utf-8"))["version"],
                             "2.1.0")
            self.assertEqual(ET.parse(junit).getroot().tag, "testsuites")
            self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
