# -*- coding: utf-8 -*-
"""在线服务层单元测试：术语、缓存、质量、预算与安全。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "products/mcp_online"))
sys.path.insert(0, str(ROOT / "core"))

from stalker_translate_core import GlossaryIndex, QualityIssue, TokenUsage, TranslationResult  # noqa: E402
from stalker_translate_online.config import Config, load_config  # noqa: E402
from stalker_translate_online.security import SecurityError, allowed_path, output_path  # noqa: E402
from stalker_translate_online.service import BudgetExceeded, TranslationService  # noqa: E402


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0
        self.prompts = []

    def chat(self, messages, max_tokens=None, temperature=None):
        self.calls += 1
        self.prompts.append(messages)
        text = self.outputs.pop(0) if self.outputs else "你好，潜行者。"
        return text, TokenUsage(input_tokens=10, output_tokens=5), "req-%d" % self.calls

    def translate(self, request, max_tokens=None, extra_system=""):
        text, usage, request_id = self.chat(
            [{"role": "user", "content": request.text}],
            max_tokens=max_tokens)
        return TranslationResult(text=text, source_text=request.text,
                                 provider=self.name, model=self.model,
                                 request_id=request_id, usage=usage)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ait_online_")
        base = Path(self.tmp.name)
        self.cfg = Config(model="fake", allowed_roots=[str(base)],
                          cache_dir=str(base / "cache"),
                          audit_dir=str(base / "audit"), rpm=0, tpm=0)
        self.glossary = GlossaryIndex.from_mapping({"Pripyat": "普里皮亚季"})
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.close()
        self.tmp.cleanup()

    def service(self, provider):
        service = TranslationService(self.cfg, provider, self.glossary, "assets")
        self.services.append(service)
        return service

    def test_exact_glossary_skips_provider(self):
        provider = FakeProvider([])
        result = self.service(provider).translate("Pripyat", "eng", "chs")
        self.assertEqual(result.text, "普里皮亚季")
        self.assertEqual(provider.calls, 0)

    def test_cache_skips_second_provider_call(self):
        # 夹具句需避开随包词库精确键（"Hello"→你好 会直取短路 Provider 路径）
        provider = FakeProvider(["你好。"])
        service = self.service(provider)
        first = service.translate("Zeta test", "eng", "chs")
        second = service.translate("Zeta test", "eng", "chs")
        self.assertEqual(first.text, "你好。")
        self.assertTrue(second.cache_hit)
        self.assertEqual(provider.calls, 1)

    def test_placeholder_failure_retains_source(self):
        provider = FakeProvider(["你好", "还是坏了"])
        result = self.service(provider).translate("%c[default]Hello", "eng", "chs")
        self.assertTrue(result.retained_source)
        self.assertEqual(result.text, "%c[default]Hello")
        self.assertIn("placeholder_mismatch", [i.code for i in result.issues])
        self.assertEqual(provider.calls, 2)

    def test_budget_stops_after_limit(self):
        self.cfg.budget = {"max_requests": 1}
        provider = FakeProvider(["你好。", "再见。"])
        service = self.service(provider)
        service.translate("Zeta probe", "eng", "chs")
        with self.assertRaises(BudgetExceeded):
            service.translate("Omega probe", "eng", "chs")

    def test_config_rejects_plaintext_key(self):
        path = Path(self.tmp.name) / "config.json"
        path.write_text(json.dumps({"model": "m", "api_key": "secret"}),
                        encoding="utf-8")
        with self.assertRaises(ValueError):
            load_config(path)

    def test_allowed_root_and_output_overlap(self):
        root = Path(self.tmp.name)
        outside = root.parent / "outside"
        with self.assertRaises(SecurityError):
            allowed_path(outside, self.cfg)
        with self.assertRaises(SecurityError):
            output_path(root, root, self.cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
