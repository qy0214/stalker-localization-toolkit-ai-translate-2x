# -*- coding: utf-8 -*-
"""规范化 core 的直接回归：覆盖迁移后曾漏检的公开路径。"""
from __future__ import annotations

import os
import sys
import tempfile
import pathlib
import unittest

sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from stalker_translate_core import pipeline, project, xray  # noqa: E402


class CanonicalCoreTests(unittest.TestCase):
    def test_mojibake_constant_name_is_resolvable(self):
        self.assertIs(xray.looks_like_mojibake("AAAAAAAA"), False)
        self.assertIs(xray.looks_like_mojibake("ÐÐÐÐÐAAAAA"), True)

    def test_patch_table_text_drops_illegal_xml_chars(self):
        src = '<string_table><string id="x"><text>old</text></string></string_table>'
        out, changed = xray.patch_table_text(src, "x", "rus", "新\x1f译文")
        self.assertTrue(changed)
        self.assertNotIn("\x1f", out)
        self.assertIn("新译文", out)

    def test_patch_table_text_sanitizes_source(self):
        out, changed = xray.patch_table_text("\x1f", "x", "rus", "")
        self.assertFalse(changed)
        self.assertEqual(out, "")

    def test_files_equal_call_site_is_resolvable(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = pathlib.Path(tmp) / "a"
            b = pathlib.Path(tmp) / "b"
            a.write_bytes(b"same")
            b.write_bytes(b"same")
            self.assertTrue(project.files_equal(str(a), str(b)))
            b.write_bytes(b"other")
            self.assertFalse(project.files_equal(str(a), str(b)))

    def test_runtime_tokens_are_order_sensitive(self):
        self.assertFalse(pipeline.runtime_tokens_match(
            "%c[default]%s", "%s%c[default]"))
        self.assertTrue(pipeline.runtime_tokens_match(
            "%c[default]%s", "%c[default]%s"))


if __name__ == "__main__":
    unittest.main()
