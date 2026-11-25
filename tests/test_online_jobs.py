# -*- coding: utf-8 -*-
"""在线项目翻译、断点续译与输出校验测试。"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "products/mcp_online"))
sys.path.insert(0, str(ROOT / "core"))

from stalker_translate_core import GlossaryIndex, TokenUsage, TranslationResult  # noqa: E402
from stalker_translate_online.config import Config  # noqa: E402
from stalker_translate_online.jobs import JobManager  # noqa: E402
from stalker_translate_online.service import TranslationService  # noqa: E402


class MappingProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self):
        self.calls = 0

    @staticmethod
    def _source_from_messages(messages):
        import re
        user = str(messages[-1].get("content") or "")
        m = re.search(r"翻译为[^：\n]*：\n", user)
        return user[m.end():] if m else user

    def chat(self, messages, max_tokens=None, temperature=None):
        self.calls += 1
        return (self._map(self._source_from_messages(messages)),
                TokenUsage(input_tokens=5, output_tokens=3),
                "req-%d" % self.calls)

    def _map(self, text):
        import re
        toks = re.findall(r"\$\$[^$]+\$\$", text)
        if "Привет" in text:
            zh = "你好，潜行者。"
        elif "Heckler" in text:
            zh = "Heckler & Koch 步枪"
        elif "Hello" in text:  # %c[...] 已被 core 掩码为 $$Rn$$，逐字带回
            return "".join(toks) + "你好"
        else:
            zh = "中文译文"
        return zh

    def translate(self, request, max_tokens=None, extra_system=""):
        self.calls += 1
        return TranslationResult(text=self._map(request.text),
                                 source_text=request.text,
                                 provider=self.name, model=self.model,
                                 usage=TokenUsage(input_tokens=5, output_tokens=3))


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ait_online_job_")
        self.root = Path(self.tmp.name)
        self.mod = self.root / "mod"
        (self.mod / "text").mkdir(parents=True)
        self.xml = self.mod / "text" / "st_hello.xml"
        self.xml.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n<string_table>\n'
            '<string id="hello"><rus>Привет, сталкер.</rus></string>\n'
            '<string id="gun"><eng>Heckler & Koch</eng></string>\n'
            '<string id="color"><eng>%c[default]Hello</eng></string>\n'
            '</string_table>\n', encoding="utf-8")
        self.cfg = Config(model="fake", allowed_roots=[str(self.root)],
                          cache_dir=str(self.root / "cache"),
                          audit_dir=str(self.root / "audit"), rpm=0, tpm=0)
        self.provider = MappingProvider()
        self.service = TranslationService(
            self.cfg, self.provider, GlossaryIndex.from_mapping({}), "assets")
        self.jobs = JobManager(self.cfg, self.service)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def wait(self, job_id):
        for _ in range(200):
            status = self.jobs.status(job_id)
            if status.state.value in ("succeeded", "needs_review", "failed",
                                      "cancelled"):
                return status
            time.sleep(0.05)
        self.fail("任务超时")

    def test_translate_project_and_validate(self):
        original = self.xml.read_bytes()
        job_id = self.jobs.start(str(self.mod), concurrency=2)
        status = self.wait(job_id)
        self.assertEqual(status.state.value, "succeeded", status.message)
        self.assertEqual(self.xml.read_bytes(), original)
        out = Path(status.output_dir) / "text" / "st_hello.xml"
        self.assertTrue(out.is_file())
        body = out.read_text(encoding="utf-8")
        self.assertIn("你好，潜行者。", body)
        self.assertIn("Heckler &amp; Koch", body)
        self.assertIn("%c[default]你好", body)
        validation = self.jobs.validate(str(self.mod), status.output_dir)
        self.assertTrue(validation["ok"], validation)

    def test_resume_skips_unchanged_entries(self):
        first = self.wait(self.jobs.start(str(self.mod)))
        calls = self.provider.calls
        second = self.wait(self.jobs.start(str(self.mod)))
        self.assertEqual(second.state.value, "succeeded")
        self.assertEqual(self.provider.calls, calls)
        self.assertTrue((Path(first.output_dir) / ".ai_translate_done.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
