# -*- coding: utf-8 -*-
"""小模型输出外壳剥离（strip_output_wrappers）回归：代码级闸门，不靠提示词。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
from stalker_translate_core.pipeline import strip_output_wrappers  # noqa: E402


class TestStripOutputWrappers(unittest.TestCase):
    def test_plain_untouched(self):
        out, notes = strip_output_wrappers("你他妈怎么还不滚？", "Why don't you just leave?")
        self.assertEqual(out, "你他妈怎么还不滚？")
        self.assertEqual(notes, [])

    def test_code_fence(self):
        raw = "```text\n撑住！\n```"
        out, notes = strip_output_wrappers(raw, "Hold on!")
        self.assertEqual(out, "撑住！")
        self.assertIn("fence", notes)

    def test_code_fence_with_lang(self):
        raw = "```\n$$R0$$ 撑住！\n```"
        out, notes = strip_output_wrappers(raw, "Hold $level!")
        self.assertEqual(out, "$$R0$$ 撑住！")
        self.assertIn("fence", notes)

    def test_label_prefix(self):
        out, notes = strip_output_wrappers("译文：撑住！", "Hold on!")
        self.assertEqual(out, "撑住！")
        self.assertIn("label", notes)

    def test_label_english(self):
        out, notes = strip_output_wrappers("Translation: 撑住！", "Hold on!")
        self.assertEqual(out, "撑住！")

    def test_label_nested_fence_label(self):
        raw = "```\n译文：撑住！\n```"
        out, notes = strip_output_wrappers(raw, "Hold on!")
        self.assertEqual(out, "撑住！")

    def test_quote_wrap_stripped(self):
        out, notes = strip_output_wrappers("“撑住！”", "Hold on!")
        self.assertEqual(out, "撑住！")
        self.assertIn("quote", notes)

    def test_quote_wrap_ascii(self):
        out, notes = strip_output_wrappers('"撑住！"', "Hold on!")
        self.assertEqual(out, "撑住！")

    def test_source_quote_preserved(self):
        # 源文本身带同类引号 → 译文引号是正当内容，不能剥
        out, notes = strip_output_wrappers("“撑住！”", "«Hold on!»")
        self.assertEqual(out, "“撑住！”")
        self.assertEqual(notes, [])

    def test_rus_source_quote_preserved(self):
        out, _ = strip_output_wrappers("«撑住!»", "«Держись!»")
        self.assertEqual(out, "«撑住!»")

    def test_batch_numbered_lines_untouched(self):
        raw = "[1] 撑住！\n[2] 走。"
        out, notes = strip_output_wrappers(raw, "Hold on!\nGo.")
        self.assertEqual(out, raw)
        self.assertEqual(notes, [])

    def test_batch_with_preamble_label(self):
        raw = "以下是翻译：\n[1] 撑住！"
        out, notes = strip_output_wrappers(raw, "Hold on!")
        self.assertEqual(out, "[1] 撑住！")

    def test_unbalanced_quote_not_stripped(self):
        # 只有开头有引号时不动
        out, notes = strip_output_wrappers("“撑住，然后跑。", "Hold on and run.")
        self.assertEqual(out, "“撑住，然后跑。")
        self.assertEqual(notes, [])

    def test_empty(self):
        out, notes = strip_output_wrappers("", "")
        self.assertEqual((out, notes), ("", []))


if __name__ == "__main__":
    unittest.main()
