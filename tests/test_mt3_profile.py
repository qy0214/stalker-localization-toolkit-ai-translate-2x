# -*- coding: utf-8 -*-
"""2.x Hy-MT3 测试档契约与请求参数回归。"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/ai_translate/plugin.py"


class _Response:
    def __init__(self, content="你好。"):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({
            "choices": [{
                "message": {"content": self.content},
                "finish_reason": "stop",
            }],
        }).encode("utf-8")


class HyMt3ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="ait_mt3_")
        cls._old_local = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = cls._tmp.name
        spec = importlib.util.spec_from_file_location("ait_mt3", PLUGIN)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    @classmethod
    def tearDownClass(cls):
        if cls._old_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = cls._old_local
        cls._tmp.cleanup()

    def _server(self, tier: str = "mt3_q8") -> object:
        server = self.mod.LlamaServer(
            "llama-server.exe", "model.gguf", "cpu", lambda *_args: None,
            ctx_size=8192, tier=tier,
        )
        server.is_running = lambda: True
        server.base = "http://127.0.0.1:1"
        return server

    def _capture(self, server: object, *args, **kwargs):
        captured = {}

        def fake_post(_url, payload, **_kwargs):
            captured.update(json.loads(payload.decode("utf-8")))
            return _Response()

        old = self.mod._http_post
        self.mod._http_post = fake_post
        try:
            result = server.translate(*args, **kwargs)
        finally:
            self.mod._http_post = old
        return result, captured

    def test_profile_contract(self):
        profile = self.mod.TIER_PROFILES["mt3_q8"]
        self.assertEqual(profile["file"], "Hy-MT3-1.8B-Q8_0.gguf")
        self.assertEqual(profile["ctx"], 8192)
        self.assertEqual(profile["model_id"], "hy-mt3")
        self.assertEqual(profile["prompt_mode"], "hymt3")
        self.assertEqual(profile["sampling"], {
            "temperature": 0.7,
            "top_p": 0.6,
            "top_k": 20,
            "repetition_penalty": 1.05,
            "max_tokens": 4096,
        })
        self.assertEqual(self.mod.TIER_ORDER[:4], ("7b", "mt3_q8", "mt3_q4", "mt2"))
        self.assertIn("mt3_q8", self.mod.BUILTIN_TIERS)
        self.assertIn("mt3_q4", self.mod.BUILTIN_TIERS)
        from stalker_translate_core.models import TIER_PROFILES as core_profiles
        self.assertEqual(core_profiles["mt3_q8"]["sampling"], profile["sampling"])
        self.assertEqual(
            self.mod._MODEL_HASHES["Hy-MT3-1.8B-Q4_K_M.gguf"],
            ("bae6e591103654ff43ef0de7d1492f6e6df1f955505f6bb2cc52e86fc399a942",
             1348432160),
        )
        self.assertEqual(
            self.mod._MODEL_HASHES[profile["file"]],
            ("03e246991a57d31fe01ac203331e032eabd327ffd0098e570411b811f6119af8",
             1908528448),
        )

    def test_single_request_uses_official_prompt_and_sampling(self):
        server = self._server()
        result, payload = self._capture(
            server, "Find %s.", "eng", "chs",
            glossary_hits=[("Cold", "冷战")],
        )
        self.assertEqual(result, "你好。")
        self.assertEqual(payload["model"], "hy-mt3")
        self.assertEqual(payload["temperature"], 0.7)
        self.assertLessEqual(payload["max_tokens"], 4096)
        user = payload["messages"][1]["content"]
        self.assertIn("将以下文本翻译为简体中文", user)
        self.assertIn("不要额外解释：\n\nFind %s.", user)
        self.assertIn("保留等量的分隔符", user)
        self.assertIn("参考下面的翻译", user)
        self.assertTrue(payload["messages"][0]["content"].startswith("你是《潜行者"))

    def test_masked_runtime_token_triggers_delimiter_instruction(self):
        server = self._server()
        _result, payload = self._capture(
            server, "$$R0$$Hello $$R1$$", "eng", "chs",
        )
        self.assertIn("保留等量的分隔符", payload["messages"][1]["content"])

    def test_hymt3_repairs_truncated_mask_delimiter(self):
        server = self._server()

        def fake_post(_url, _payload, **_kwargs):
            return _Response("$$R0$$你好 $$R1$。")

        old = self.mod._http_post
        self.mod._http_post = fake_post
        try:
            result = server.translate("$$R0$$Hello $$R1$$", "eng", "chs")
        finally:
            self.mod._http_post = old
        self.assertEqual(result, "$$R0$$你好 $$R1$$。")

    def test_context_uses_background_information_template(self):
        server = self._server()
        _result, payload = self._capture(
            server, "Where is the trader?", "eng", "chs",
            context="The previous speaker mentioned the trader.",
        )
        user = payload["messages"][1]["content"]
        self.assertIn("【背景信息】", user)
        self.assertIn("请结合背景信息将以下文本翻译为简体中文。", user)
        self.assertIn("【待翻译文本】", user)

    def test_batch_request_uses_mt3_identity_and_output_cap(self):
        server = self._server()
        captured = {}

        def fake_post(_url, payload, **_kwargs):
            captured.update(json.loads(payload.decode("utf-8")))
            return _Response()

        old = self.mod._http_post
        self.mod._http_post = fake_post
        try:
            server.translate_batch(["Hello.", "Where is $name?"], "eng", "chs")
        finally:
            self.mod._http_post = old
        self.assertEqual(captured["model"], "hy-mt3")
        self.assertEqual(captured["temperature"], 0.7)
        self.assertLessEqual(captured["max_tokens"], 4096)
        self.assertIn("[1] Hello.", captured["messages"][1]["content"])
        self.assertIn("[2] Where is $name?", captured["messages"][1]["content"])
        self.assertIn("保留等量的分隔符", captured["messages"][1]["content"])

    def test_batch_repairs_truncated_and_bare_masks(self):
        server = self._server()

        def fake_post(_url, _payload, **_kwargs):
            return _Response("[1] R0你好\n[2] $$R1$世界")

        old = self.mod._http_post
        self.mod._http_post = fake_post
        try:
            result = server.translate_batch(
                ["$$R0$$Hello", "$$R1$$World"], "eng", "chs")
        finally:
            self.mod._http_post = old
        self.assertEqual(result, "[1] $$R0$$你好\n[2] $$R1$$世界")

    def test_model_temperature_profile_is_not_overridden(self):
        mt3 = self._server("mt3_q8")
        mt4 = self._server("mt3_q4")
        mt2 = self._server("mt2")
        self.assertEqual(mt3.dialogue_temperature, 0.7)
        self.assertEqual(mt4.dialogue_temperature, 0.7)
        self.assertEqual(mt3.formal_temperature, 0.7)
        self.assertEqual(mt3.label_temperature, 0.0)
        self.assertEqual(mt2.dialogue_temperature, 0.1)
        self.assertEqual(mt2.formal_temperature, 0.1)
        self.assertEqual(mt2.label_temperature, 0.0)

    def test_job_inherits_model_temperature_profile(self):
        server = self._server("mt3_q8")
        with tempfile.TemporaryDirectory() as tmp:
            job = self.mod.TranslationJob(
                object(), server, tmp, "text", "eng", "chs",
                reference_mode=False, copy_reference=False,
                auto_session_terms=False, tm_lite=False,
            )
        self.assertEqual(job.dialogue_temp, 0.7)
        self.assertEqual(job.formal_temp, 0.7)
        self.assertEqual(job.label_temp, 0.0)

    def test_label_style_uses_label_system_prompt(self):
        server = self._server("mt3_q8")
        _result, payload = self._capture(
            server, "Health", "eng", "chs", style="label",
        )
        system = payload["messages"][0]["content"]
        self.assertIn("游戏本地化译者", system)
        self.assertIn("短标签", system)
        self.assertEqual(payload["temperature"], 0.0)

    def test_mt2_profile_remains_compatible(self):
        profile = self.mod.TIER_PROFILES["mt2"]
        server = self._server("mt2")
        self.assertEqual(profile["ctx"], 16384)
        self.assertEqual(server.model_id, "hy-mt2")
        self.assertEqual(server.sampling.get("temperature", 0.1), 0.1)
        self.assertEqual(server.max_output_tokens, 0)
        _result, payload = self._capture(server, "Hello.", "eng", "chs")
        self.assertEqual(payload["model"], "hy-mt2")
        self.assertEqual(payload["temperature"], 0.1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
