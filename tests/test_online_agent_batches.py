# -*- coding: utf-8 -*-
"""轨道 B 对话 AI 批量协议测试：收集零请求、答案回灌零 HTTP、坏答回炉。"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "products/mcp_online"))
sys.path.insert(0, str(ROOT / "core"))

from stalker_translate_core import GlossaryIndex, TokenUsage  # noqa: E402
from stalker_translate_online.config import Config  # noqa: E402
from stalker_translate_online.jobs import JobManager  # noqa: E402
from stalker_translate_online.service import TranslationService  # noqa: E402


class RaisingProvider:
    """任何 chat 调用都视为违规：轨道 B 的收集与补全必须零 HTTP。"""
    name = "fake"
    model = "fake-model"

    def __init__(self):
        self.calls = 0

    def chat(self, messages, max_tokens=None, temperature=None):
        self.calls += 1
        raise AssertionError("轨道 B 不应发起 HTTP 请求")

    def health(self):
        return {"ok": True, "models": []}


class AgentBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ait_agent_batch_")
        self.root = Path(self.tmp.name)
        self.mod = self.root / "mod"
        (self.mod / "text").mkdir(parents=True)
        # 用词库外的生造句，确保没有整串直取短路，必然进入需要模型的集合
        self.xml = self.mod / "text" / "st_zz.xml"
        self.xml.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n<string_table>\n'
            '<string id="zz1"><eng>Zorbland quux beacon %d.</eng></string>\n'
            '<string id="zz2"><eng>Flarp device calibrated.</eng></string>\n'
            '</string_table>\n', encoding="utf-8")
        self.provider = RaisingProvider()
        self.cfg = Config(model="fake", allowed_roots=[str(self.root)],
                          cache_dir=str(self.root / "cache"),
                          audit_dir=str(self.root / "audit"), rpm=0, tpm=0)
        self.service = TranslationService(
            self.cfg, self.provider, GlossaryIndex.from_mapping({}), "assets-zz")
        self.jobs = JobManager(self.cfg, self.service)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def wait(self, job_id):
        for _ in range(400):
            status = self.jobs.status(job_id)
            if status.state.value in ("succeeded", "needs_review", "failed",
                                      "cancelled"):
                return status
            time.sleep(0.05)
        self.fail("任务超时")

    def test_collect_records_pending_without_http(self):
        result = self.jobs.collect_pending(str(self.mod))
        self.assertEqual(self.provider.calls, 0)
        keys = {item["key"] for item in result["items"]}
        self.assertGreaterEqual(len(keys), 1, result)
        # pending 项携带源文与术语提示字段，供对话 AI 编排批次
        for item in result["items"]:
            self.assertTrue(item["sources"])
            self.assertIn("n", item)

    def test_apply_answers_then_fill_pass_zero_http(self):
        collected = self.jobs.collect_pending(str(self.mod))
        before = self.provider.calls
        answers = []
        for item in collected["items"]:
            if item["kind"] != "single":
                continue
            src = item["sources"][0]
            import re
            toks = "".join(re.findall(r"\$\$[^$]+\$\$", src))
            answers.append({"key": item["key"],
                            "translation": toks + "佐布尔兰装置已校准。"})
        applied = self.jobs.apply_answers(answers)
        self.assertGreaterEqual(applied["applied"], 1, applied)
        filled = self.jobs.collect_pending(str(self.mod))
        self.assertEqual(self.provider.calls, before,
                         "已作答条目应全部从批次存储取回，零 HTTP")
        self.assertIn(filled["state"], ("succeeded", "needs_review"))
        out_files = list(Path(filled["output_dir"]).rglob("*.xml"))
        self.assertTrue(out_files)
        body = "\n".join(f.read_text(encoding="utf-8") for f in out_files)
        self.assertIn("佐布尔兰装置已校准。", body)
        self.assertIn("%d", body, "运行期占位符必须被回写链还原")

    def test_echo_answer_rejected_keeps_pending(self):
        collected = self.jobs.collect_pending(str(self.mod))
        first = collected["items"][0]
        echo = self.jobs.apply_answers(
            [{"key": first["key"], "translation": first["sources"][0]}])
        self.assertEqual(echo["rejected_or_echo"], 1)
        self.assertEqual(echo["applied"], 0)

    def test_same_source_keys_collapse_after_one_answer(self):
        from stalker_translate_online.engine import BatchStore
        store = BatchStore(Path(self.cfg.cache_path) / "t_collapse.jsonl")
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        store.record_pending("kA", msgs, "single", ["Optical rangefinder"], {})
        store.record_pending("kB", msgs, "single", ["Optical rangefinder"], {})
        store.record_pending("kC", msgs, "batch", ["Alpha", "Beta"], {})
        store.set_answer("kA", "光学测距仪")
        # 不同场景键、同源文：一次作答即可命中
        self.assertEqual(store.answer_for("single", ["Optical rangefinder"]),
                         "光学测距仪")
        store.set_answer("kC", "[1] 甲\n[2] 乙")
        self.assertEqual(store.answer_for("batch", ["Alpha", "Beta"]),
                         "[1] 甲\n[2] 乙")
        self.assertIsNone(store.answer_for("single", ["Untouched text"]))

    def test_stale_pending_pruned_after_entry_resolved(self):
        collected = self.jobs.collect_pending(str(self.mod))
        keys = [it["key"] for it in collected["items"]]
        self.assertTrue(keys)
        # 选一个无掩码占位符的键作答（含占位符的答案会被卫生闸门回炉，
        # 其键必须保留，见 test_placeholder_answer_invalidated_then_recollected）
        target = next((it for it in collected["items"]
                       if it["kind"] == "single" and "$$" not in it["sources"][0]),
                      None)
        if target is None:
            self.skipTest("夹具没有无占位符条目")
        self.jobs.apply_answers(
            [{"key": target["key"], "translation": "装置已校准。"}])
        again = self.jobs.collect_pending(str(self.mod))
        again_keys = {it["key"] for it in again["items"]}
        self.assertNotIn(target["key"], again_keys,
                         "已作答且可命中的键必须从 pending 消失（收敛前提）")
        dirty = [it for it in collected["items"]
                 if it["kind"] == "single" and "$$" in it["sources"][0]]
        for it in dirty:
            self.assertIn(it["key"], again_keys,
                          "含掩码占位符而未答的键必须保留在 pending")

    def test_placeholder_answer_invalidated_then_recollected(self):
        collected = self.jobs.collect_pending(str(self.mod))
        target = None
        for item in collected["items"]:
            if item["kind"] == "single" and "$$" in item["sources"][0]:
                target = item
                break
        if target is None:
            self.skipTest("夹具未产生带掩码占位符的源文")
        self.jobs.apply_answers([{"key": target["key"],
                                  "translation": "丢了占位符的译文"}])
        again = self.jobs.collect_pending(str(self.mod))
        keys = {it["key"] for it in again["items"]}
        self.assertIn(target["key"], keys,
                      "未过卫生检查的答案必须回炉重新收集")


if __name__ == "__main__":
    unittest.main(verbosity=2)
