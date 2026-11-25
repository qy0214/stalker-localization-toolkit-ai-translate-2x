# -*- coding: utf-8 -*-
"""tools/iwp_quality_gate.py 治理策略回归（合成数据，不依赖外部 IWP 目录）。"""
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import iwp_quality_gate as gate  # noqa: E402


def _xml_dir(root, rel, entries):
    d = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "sample.xml")
    lines = ["<string_table>"]
    for sid, text in entries.items():
        lines.append('<string id="%s"><text>%s</text></string>' % (sid, text))
    lines.append("</string_table>")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return "sample.xml"


class TestIwpQualityGate(unittest.TestCase):
    def _classify(self, rep, out_entries):
        tmp = tempfile.mkdtemp()
        _xml_dir(tmp, "configs/text/eng", out_entries)
        return gate.classify(rep, set(), tmp)

    def test_trunc_cjk_calibration(self):
        # 源 400 字，中文译文 130 字 → 有效 260 >= 200 → 豁免
        src = "word " * 80  # 400 chars
        out = "这是测试译文。" * 15  # 105 zh chars... need eff>=200: zh*2
        out = "汉" * 130
        rep = {"trunc": [["configs/text/eng/sample.xml", "id1", 400, 130,
                          src[:70], out[:70]]]}
        blocks, warns, waived, _ = self._classify(rep, {"id1": out})
        self.assertEqual(warns, [])
        self.assertEqual(waived.get("trunc_cjk_calibrated"), 1)

    def test_trunc_real_defect(self):
        out = "汉" * 60  # eff=120 < 200
        rep = {"trunc": [["configs/text/eng/sample.xml", "id1", 400, 60,
                         "x" * 70, out[:70]]]}
        blocks, warns, waived, _ = self._classify(rep, {"id1": out})
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0]["kind"], "trunc")

    def test_untranslated_is_block(self):
        rep = {"untranslated": [["configs/text/eng/sample.xml", "sid", "send",
                                 "An anonymous source has sent you."]]}
        blocks, warns, _, _ = self._classify(rep, {})
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["kind"], "untranslated")

    def test_cyr_proper_noun_waived(self):
        rep = {"cyr_residue": [["configs/text/eng/sample.xml", "id1", "src",
                                "「Жарку」很难发现"]]}
        blocks, warns, waived, _ = self._classify(rep, {"id1": "x"})
        self.assertEqual(blocks, [])
        self.assertEqual(waived.get("cyr_proper_noun"), 1)

    def test_cyr_sentence_is_block(self):
        long_sent = ("Пепелище старое кладбище возле Копачей очень опасно "
                     "много жарок снизу собираются кометы")
        rep = {"cyr_residue": [["configs/text/eng/sample.xml", "id1", "src",
                                long_sent]]}
        blocks, _, _, _ = self._classify(rep, {"id1": "x"})
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["kind"], "cyr_sentence")

    def test_num_year_conversion_waived(self):
        rep = {"num_lost": [["configs/text/eng/sample.xml", "id1", "1990s",
                             "Developed in the 1990s", "该枪于20世纪90年代开发"]]}
        blocks, warns, waived, _ = self._classify(rep, {"id1": "该枪于20世纪90年代开发"})
        self.assertEqual(blocks, [])
        self.assertEqual(warns, [])
        self.assertEqual(waived.get("num_partial"), 1)

    def test_orphan_generated_waived(self):
        rep = {"orphan_out": [["configs/text/sgtat_auto_texts.xml", ""]]}
        blocks, _, waived, _ = self._classify(rep, {})
        self.assertEqual(blocks, [])
        self.assertEqual(waived.get("orphan_out_generated"), 1)


if __name__ == "__main__":
    unittest.main()
