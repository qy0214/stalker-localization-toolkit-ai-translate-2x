# -*- coding: utf-8 -*-
"""v2.0.11 回归：一次性修复的五类问题。

1. P0 冒号/破折号标题正则误伤带冒号长对话
2. P0 玩家可见短标签被 identifier/code 规则跳过
3. P1 数字闸门把型号尾号/年份区间当金额，整段保留原文
4. P1 三路并发被 LCP 相似度选槽黏在单一 slot
5. P2 UTF-8 文本表声明仍是 windows-1251

用法: python tests/test_2111_all_fixes.py [plugin.py 路径]
不依赖 pytest；退出码 0 = 全通过。
"""
import importlib.util
import os
import re
import sys
sys.dont_write_bytecode = True   # 别把 .pyc 写进随包插件目录（发布门禁 A6 会判为运行产物）
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = (sys.argv[1] if len(sys.argv) > 1 else
          os.path.join(ROOT, "plugins", "ai_translate", "plugin.py"))

spec = importlib.util.spec_from_file_location("ait2111", PLUGIN)
ait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ait)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + str(detail)[:120]) if detail else ""))


def t1_title_regex():
    long_dialog = ("Just about anyone, buddy. Think about it: his own faction "
                   "will bury the cocksucker alive, other factions will kick up a fuss")
    check("P0 带冒号长对话不再被当标题保护",
          not ait.looks_like_proper_text("configs/text/eng/st_dialogs.xml",
                                         long_dialog))
    long_dash = "He was there — and then everything went wrong. Run!"
    check("P0 带句末标点的长破折号句不再被当标题",
          not ait.looks_like_proper_text("x.xml", long_dash))
    for t in ("Vano: Hi", "Hint: press F", "MP5 — Sanitizer",
              "AK-74 \"Rubin\""):
        check("P0 真标题仍受保护：" + t[:20],
              ait.looks_like_proper_text("x.xml", t))


def t2_number_gate():
    src = ('Take a look - this is the "FN FNX-45". The pistols were developed '
           'in 2007-2009 for a program. In general, it is 2800 from you.')
    miss = ait.missing_big_numbers(src, "这把枪完全没有任何数字")
    # 2.0.14：年份区间只豁免尾年(2009)，起始年(2007)参与保真——
    # 防止模型把「2007-2009」整体换成别的数字(如 P220)时闸门放行。
    check("P1 型号尾号豁免、金额与年份起点仍校验",
          sorted(miss) == ["2007", "2800"], miss)
    check("P1 金额与年份都在即通过",
          ait.missing_big_numbers(src, "2007年研发，价格是 2800 卢布") == [])
    check("P1 独立大额金额仍严格校验",
          ait.missing_big_numbers("报酬 30000 卢布", "报酬 15000 卢布")
          == ["30000"])
    check("P1 中文万计数仍被识别",
          ait.missing_big_numbers("30000 рублей", "三万卢布") == [])


def t3_player_labels():
    cases = [
        ("configs/text/eng/st_achievement.xml", "Radiotherapy", True),
        ("configs/text/eng/st_achievement.xml", "Completionist", True),
        ("configs/text/eng/st_game_relations.xml", "Enemy", True),
        ("configs/text/eng/st_game_relations.xml", "Rookie", True),
        ("configs/text/eng/gunsl_up_binoc.xml", "Magnification", True),
        ("configs/text/eng/gunsl_up_binoc.xml", "Optical rangefinder", True),
        ("configs/text/eng/st_characters.xml", "Vano", False),
        # ★ 2.0.21 起 dynamic_news 属玩家可见目录（新闻标题/碎片是正文），
        # 旧期望 False 是白名单扩充前的口径，未同步导致长期失败。
        ("configs/text/eng/st_dynamic_news.xml", "Courier document", True),
    ]
    for rel, text, should_translate in cases:
        skipped = (ait.looks_like_identifier_text(text)
                   and not ait.is_player_facing_label(rel, text))
        check("P0 短标签放行=%s：%s" % (should_translate, text),
              (not skipped) == should_translate)
    for text in ("[Say nothing]", "[Issue Command]"):
        check("P0 对话选项不再当调试标记：" + text,
              not ait.looks_like_code_or_debug(
                  "configs/text/eng/st_dialogs.xml", text))
    check("P0 真调试标记仍跳过",
          ait.looks_like_code_or_debug("st_dialogs.xml", "[obj : test]"))
    for rel in ("configs/text/eng/st_achievement.xml",
                "configs/text/eng/st_game_relations.xml",
                "configs/text/eng/gunsl_up_binoc.xml"):
        check("P0 会话词表覆盖：" + rel.split("/")[-1],
              ait.session_category(rel, "st_achievement_1_name")
              == "成就·称号·属性标签")


def t4_parallel_args():
    with open(PLUGIN, encoding="utf-8") as f:
        src = f.read()
    check("P1 多路时禁用相似度选槽",
          "--slot-prompt-similarity" in src
          and "self.parallel > 1" in src)
    check("P1 参数值为 0（强制轮转）",
          re.search(r'"--slot-prompt-similarity",\s*"0"', src) is not None)


def t5_xml_declaration():
    job = ait.TranslationJob.__new__(ait.TranslationJob)
    job.log = lambda *a, **k: None
    job.detail = lambda *a, **k: None
    job._copy_file = lambda *a, **k: None
    with tempfile.TemporaryDirectory(prefix="ait2111_") as d:
        src = os.path.join(d, "in.xml")
        out = os.path.join(d, "out.xml")
        with open(src, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="windows-1251"?>\n'
                    '<string_table>\n'
                    '  <string id="a"><text>english</text></string>\n'
                    '</string_table>\n')
        ok = job._write_table_changes(
            src, out, {"a": ("eng", "中文译文")}, "x.xml")
        data = open(out, "rb").read().decode("utf-8")
        check("P2 UTF-8 表写入成功", ok)
        check("P2 声明同步改为 utf-8",
              'encoding="utf-8"' in data and "windows-1251" not in data)
        check("P2 中文正文正确写入", ">中文译文<" in data)
        # 无声明的文件应补上声明
        src2 = os.path.join(d, "in2.xml")
        out2 = os.path.join(d, "out2.xml")
        with open(src2, "w", encoding="utf-8") as f:
            f.write('<string_table>\n'
                    '  <string id="b"><text>hello</text></string>\n'
                    '</string_table>\n')
        job._write_table_changes(
            src2, out2, {"b": ("eng", "你好")}, "y.xml")
        data2 = open(out2, "rb").read().decode("utf-8")
        check("P2 无声明文件补上 utf-8 声明",
              data2.lstrip().startswith('<?xml version="1.0"'
                                        ' encoding="utf-8"?>'))


def main():
    t1_title_regex()
    t2_number_gate()
    t3_player_labels()
    t4_parallel_args()
    t5_xml_declaration()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n==== 总计 %d 项，通过 %d，失败 %d ===="
          % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
    for n in failed:
        print("  FAIL:", n)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
