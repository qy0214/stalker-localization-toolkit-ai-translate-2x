# -*- coding: utf-8 -*-
"""0.3.0 知识技能薄工具测试：plan/extract/merge + BatchStore 折叠。"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "products/mcp_online"))
sys.path.insert(0, str(ROOT / "core"))

from stalker_translate_core import GlossaryIndex, TokenUsage  # noqa: E402
from stalker_translate_online.agentkit import AgentKit, sid_for  # noqa: E402
from stalker_translate_online.config import Config  # noqa: E402
from stalker_translate_online.jobs import JobManager  # noqa: E402
from stalker_translate_online.service import TranslationService  # noqa: E402


class RaisingProvider:
    """任何 chat 调用都视为违规：三命令全程零 HTTP。"""
    name = "fake"
    model = "fake-model"

    def __init__(self):
        self.calls = 0

    def chat(self, messages, max_tokens=None, temperature=None):
        self.calls += 1
        raise AssertionError("对话技能流程不应发起 HTTP 请求")

    def health(self):
        return {"ok": True, "models": []}


class AgentKitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ait_kit_")
        self.root = Path(self.tmp.name)
        self.mod = self.root / "mod"
        (self.mod / "text").mkdir(parents=True)
        (self.mod / "text" / "st_zz.xml").write_text(
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
        self.kit = AgentKit(self.jobs, self.cfg)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_plan_quotes_without_http(self):
        report = self.kit.plan(str(self.mod))
        self.assertEqual(self.provider.calls, 0)
        self.assertGreaterEqual(report["unique_sentences"], 1)
        self.assertIn(report["mode"], ("dialog", "engine_recommended",
                                       "engine_required"))
        self.assertNotRegex(json.dumps(report, ensure_ascii=False),
                            r"[0-9a-f]{64}", "报价里不出现 64 位请求键")

    def test_extract_tsv_compact_without_hex_keys(self):
        ex = self.kit.extract(str(self.mod), chunk=200, part=1)
        batch = Path(ex["batch_file"])
        self.assertTrue(batch.is_file())
        self.assertEqual(self.provider.calls, 0)
        body = batch.read_text(encoding="utf-8")
        self.assertFalse(re.search(r"[0-9a-f]{64}", body),
                         "批次文件禁止泄露 64 位请求键")
        rows = [l for l in body.splitlines() if l and not l.startswith("#")]
        self.assertEqual(len(rows), ex["rows"])
        for row in rows:
            sid = row.split("\t")[0].split("#")[0]
            self.assertRegex(sid, r"^[0-9a-f]{8}$")

    def test_merge_lands_and_next_extract_shrinks(self):
        ex = self.kit.extract(str(self.mod), chunk=200, part=1)
        rows = [l.split("\t") for l in Path(ex["batch_file"]).read_text(
            encoding="utf-8").splitlines() if l and not l.startswith("#")]
        answers = []
        for sid, src in [(r[0], r[1]) for r in rows]:
            plain = src.replace("\\n", "\n")
            if "$$" in plain or "%d" in plain:  # 含掩码令牌：需逐字保留
                tok = re.search(r"\$\$[^$]+\$\$|\{\d+\}|%[ds]", plain)
                answers.append("%s\t保留令牌 %s 的中文译文。" % (
                    sid, tok.group(0) if tok else ""))
            else:
                answers.append("%s\t佐布尔兰装置已校准。" % sid)
        ans_file = self.root / "answers.tsv"
        ans_file.write_text("\n".join(answers) + "\n", encoding="utf-8")
        merged = self.kit.merge(str(self.mod), str(ans_file))
        self.assertEqual(self.provider.calls, 0, "merge 及填充遍必须零 HTTP")
        self.assertGreaterEqual(merged["sentences_applied"], 1, merged)
        out_files = list(Path(merged["output_dir"]).rglob("*.xml"))
        self.assertTrue(out_files)
        body = "\n".join(f.read_text(encoding="utf-8") for f in out_files)
        self.assertIn("佐布尔兰装置已校准。", body)
        again = self.kit.extract(str(self.mod), chunk=200, part=1)
        self.assertLess(again["unique_remaining_total"],
                        merged["unique_remaining"] + again["rows"] + 1)

    def test_echo_answer_stays_pending(self):
        ex = self.kit.extract(str(self.mod), chunk=200, part=1)
        rows = [l.split("\t") for l in Path(ex["batch_file"]).read_text(
            encoding="utf-8").splitlines() if l and not l.startswith("#")]
        echo_sid, echo_src = rows[0][0], rows[0][1]
        ans = self.root / "echo.tsv"
        ans.write_text("%s\t%s\n" % (echo_sid, echo_src), encoding="utf-8")
        merged = self.kit.merge(str(self.mod), str(ans))
        self.assertEqual(merged["sentences_applied"], 0,
                         "整句回显不得入库")
        again = self.kit.extract(str(self.mod), chunk=200, part=1)
        sids = [l.split("\t")[0].split("#")[0]
                for l in Path(again["batch_file"]).read_text(
                    encoding="utf-8").splitlines()
                if l and not l.startswith("#")]
        self.assertIn(echo_sid, sids, "被拒句必须下批复现")

    def test_batch_group_line_sids(self):
        # sid#i 组句作答：merge 端按行序还原编号块
        store = self.service.get_store()
        srcs = ["Alpha line", "Beta line"]
        joined = "|||".join(srcs)
        sid = sid_for(srcs)
        store.record_pending("kk1", [{"role": "user", "content": "u"}],
                             "batch", srcs, {"recorded_at": 1})
        rows = "%s#1\t甲线\n%s#2\t乙线\n" % (sid, sid)
        # 直接经 store 单元层验证折叠与还原格式
        store.set_answer("kk1", "[1] 甲线\n[2] 乙线", srcs)
        self.assertEqual(store.answer("kk1"), "[1] 甲线\n[2] 乙线")
        # batch by_source 按行存：每行独立映射，供 answer_for 合成编号块
        self.assertEqual(store.by_source.get("Alpha line"), "甲线")
        self.assertEqual(store.answer_for("batch", ["Alpha line", "Beta line"]),
                         "[1] 甲线\n[2] 乙线")

    def test_same_source_keys_collapse_after_one_answer(self):
        from stalker_translate_online.engine import BatchStore
        store = BatchStore(Path(self.cfg.cache_path) / "t_collapse.jsonl")
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        store.record_pending("kA", msgs, "single", ["Optical rangefinder"], {})
        store.record_pending("kB", msgs, "single", ["Optical rangefinder"], {})
        store.record_pending("kC", msgs, "batch", ["Alpha", "Beta"], {})
        store.set_answer("kA", "光学测距仪")
        self.assertEqual(store.answer_for("single", ["Optical rangefinder"]),
                         "光学测距仪")
        store.set_answer("kC", "[1] 甲\n[2] 乙")
        self.assertEqual(store.answer_for("batch", ["Alpha", "Beta"]),
                         "[1] 甲\n[2] 乙")
        self.assertIsNone(store.answer_for("single", ["Untouched text"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
