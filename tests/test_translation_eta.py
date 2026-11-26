# -*- coding: utf-8 -*-
"""真实可译规模统计与多路 ETA 契约测试。"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from stalker_translate_core.estimation import (  # noqa: E402
    estimate_translation,
    parallel_speedup,
)
from stalker_translate_core.models import TIER_PROFILES  # noqa: E402
from stalker_translate_core.xray import analyze_input_dir  # noqa: E402


class TranslationEstimateTests(unittest.TestCase):
    def test_scan_reports_real_translatable_scale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "text").mkdir()
            (root / "text/a.xml").write_text(
                '<string_table>'
                '<string id="a"><text>Hello stalker, take this artifact.</text></string>'
                '<string id="b"><text>你好，潜行者。</text></string>'
                '<string id="c"><text>identifier_value_123</text></string>'
                '</string_table>', encoding="utf-8")
            info = analyze_input_dir(str(root), "text")
            self.assertEqual(info["n_strings"], 3)
            self.assertEqual(info["translatable_entries"], 1)
            self.assertEqual(info["translatable_files"], 1)
            self.assertEqual(info["translatable_chars"],
                             len("Hello stalker, take this artifact."))
            self.assertIn("total_chars", info)

    def test_parallel_speedup_is_sublinear(self):
        self.assertEqual(parallel_speedup(1), 1.0)
        self.assertAlmostEqual(parallel_speedup(2), 1.681792830507429)
        self.assertLess(parallel_speedup(4), 4.0)
        self.assertLess(parallel_speedup(16), 16.0)

    def test_eta_uses_measured_mt2_baseline_and_route_share(self):
        info = {
            "translatable_files": 10,
            "translatable_entries": 100,
            "translatable_chars": 10000,
            "extra_chunks": {"mt2": 0, "mt3_q4": 2, "mt3_q8": 2},
        }
        mt2 = estimate_translation(info, "mt2", 1, "cuda")
        q4 = estimate_translation(info, "mt3_q4", 1, "cuda")
        q8 = estimate_translation(info, "mt3_q8", 1, "cuda")
        self.assertGreater(mt2["seconds"], q8["seconds"])
        self.assertGreater(q8["seconds"], q4["seconds"])
        self.assertAlmostEqual(q4["speedup_vs_mt2"], 340.34 / 175.66)
        self.assertGreater(q4["low_seconds"], 0)
        self.assertGreater(q4["high_seconds"], q4["seconds"])
        multi = estimate_translation(info, "mt3_q4", 4, "cuda")
        self.assertLess(multi["seconds"], q4["seconds"])
        self.assertEqual(multi["per_route_files"], 3)
        self.assertGreater(multi["per_route_seconds"], 0)
        self.assertLess(multi["per_route_seconds"], multi["seconds"])
        cpu = estimate_translation(info, "mt3_q4", 1, "cpu")
        self.assertGreater(cpu["seconds"], q4["seconds"])

    def test_plugin_parallel_labels_and_eta_use_scan_scale(self):
        old_local = os.environ.get("LOCALAPPDATA")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LOCALAPPDATA"] = tmp
            try:
                spec = importlib.util.spec_from_file_location(
                    "ait_eta_ui", ROOT / "plugins/ai_translate/plugin.py")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            finally:
                if old_local is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old_local
            model = Path(tmp) / "model.gguf"
            model.write_bytes(b"GGUF" + b"\0" * 1024)
            tab = object.__new__(mod.AiTranslateTab)
            tab.sel = {"tier": "mt3_q4", "parallel": 2, "variant": "cuda"}
            tab._hw_vram_mb = 8192
            tab.scan_dir = tmp
            tab.scan_eta_label = object()
            tab.scan_info = {
                "translatable_files": 10, "translatable_entries": 100,
                "translatable_chars": 10000, "extra_chunks": {"mt3_q4": 2},
            }
            tab.find_model = lambda _tier=None: str(model)
            choices = tab._parallel_choices()
            self.assertTrue(any("1.68×" in label and "ETA " in label
                                for _n, label in choices))
            captured = {}
            old_ui = mod._UI
            mod._UI = type("UI", (), {"set_text": staticmethod(
                lambda _widget, text: captured.update(text=text))})
            try:
                tab._refresh_eta_estimate()
            finally:
                mod._UI = old_ui
            self.assertIn("10 文件 / 100 条 / 10000 字符", captured["text"])
            self.assertIn("2 路", captured["text"])
            self.assertIn("每路约 5 文件", captured["text"])

    def test_q4_q8_are_builtin_and_mt2_remains_baseline(self):
        self.assertEqual(TIER_PROFILES["mt3_q4"]["file"],
                         "Hy-MT3-1.8B-Q4_K_M.gguf")
        self.assertEqual(TIER_PROFILES["mt3_q8"]["file"],
                         "Hy-MT3-1.8B-Q8_0.gguf")
        self.assertEqual(TIER_PROFILES["mt3_q4"]["ctx"], 8192)
        self.assertEqual(TIER_PROFILES["mt3_q8"]["ctx"], 8192)
        self.assertEqual(TIER_PROFILES["mt2"]["ctx"], 16384)
        self.assertEqual(TIER_PROFILES["mt3_q4"]["model_id"], "hy-mt3")
        self.assertEqual(TIER_PROFILES["mt3_q8"]["prompt_mode"], "hymt3")

    def test_plugin_download_tasks_include_both_quantizations(self):
        old_local = os.environ.get("LOCALAPPDATA")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LOCALAPPDATA"] = tmp
            try:
                spec = importlib.util.spec_from_file_location(
                    "ait_eta", ROOT / "plugins/ai_translate/plugin.py")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            finally:
                if old_local is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old_local
        self.assertEqual(mod.TIER_ORDER[:4],
                         ("7b", "mt3_q8", "mt3_q4", "mt2"))
        self.assertIn("model_mt3_q4", mod.DOWNLOAD_TASKS)
        self.assertIn("model_mt3_q8", mod.DOWNLOAD_TASKS)
        self.assertEqual(mod.DEFAULT_CONFIG["tier"], "mt2")
        self.assertIn("1.68", mod.PARALLEL_LABELS[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
