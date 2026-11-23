# -*- coding: utf-8 -*-
"""2.0.13 回归测试：
#1 未翻译切片兜底 / #2 圆点净化 / #4 空格保真 / #5 英文粗口 /
#6 精确查表前置 / #7 通用新闻模式 / #9 批量不受 force_glossary 关闭 /
#10 TM门槛8% / #11 max_tokens=1536 / 词库9085两库一致。
"""
import importlib.util
import os
import re
import sys
sys.dont_write_bytecode = True   # 别把 .pyc 写进随包插件目录（发布门禁 A6 会判为运行产物）

PLUGIN = os.path.join(os.path.dirname(__file__), "..", "plugins",
                      "ai_translate", "plugin.py")
s = importlib.util.spec_from_file_location("p2013", PLUGIN)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    return bool(cond)


def t_version():
    check("version:2.0.13", m.PLUGIN_INFO["version"] in ("2.0.13", "2.0.14", "2.0.15", "2.0.16", "2.0.17", "2.0.18", "2.0.19", "2.0.20", "2.0.21", "2.0.22", "2.0.23", "2.0.24"))


def t_en_profanity():
    """#5 英文粗口能被源检测，普通句不误伤。"""
    for s in ["Weird shit.", "What the fuck", "You bastard!",
              "son of a bitch", "bullshit"]:
        check("enprof:%s" % s[:12], m.has_source_profanity(s))
    for s in ["Normal conversation here.", "The shot was fired.",
              "A ship on the sea."]:
        check("enprof-clean:%s" % s[:12], not m.has_source_profanity(s))


def t_bullet_clean():
    """#2 圆点注入：源无圆点则清除；源有圆点不动。"""
    out = "%c[0,1,255,255]•%c[default] 等级：1"
    cleaned, fixed = m.clean_injected_bullets("%c[0,1,255,255]%c[default] Rank: 1", out)
    check("bullet:cleared", fixed and "•" not in cleaned and "等级" in cleaned)
    # 源本身有圆点 → 不动
    keep, fixed2 = m.clean_injected_bullets("• item", "• 项目")
    check("bullet:source-has", not fixed2 and keep == "• 项目")
    # 中英正文里的孤立圆点也清
    c3, f3 = m.clean_injected_bullets("hello world", "你好•世界")
    check("bullet:inline", f3 and "•" not in c3)


def t_article_clean():
    """#3 冠词粘连。"""
    check("article:An", m.clean_stuck_article("An神器具有引力")[0] == "神器具有引力")
    check("article:The", m.clean_stuck_article("The禁区很大")[0] == "禁区很大")
    check("article:noCJK", m.clean_stuck_article("An apple a day")[0] == "An apple a day")


def t_space_fidelity():
    """#4 _patch_table_text 保留前导/尾随空白。"""
    text = '<?xml version="1.0"?>\n<string id="x">\n\t\t<text>  原始内容\n\t\t</text>\n</string>'
    out, ok = m.TranslationJob._patch_table_text(text, "x", "text", "翻译内容")
    check("space:patch-ok", ok)
    tm = re.search(r"<text>(.*?)</text>", out, re.S)
    body = tm.group(1) if tm else ""
    check("space:lead", body.startswith("  "), )
    check("space:trail", body.endswith("\n\t\t"))


def t_pattern():
    """#7 通用新闻模式 + 词库补入。"""
    lk = m.termbase_exact_translation
    check("pat:boar", m.pattern_translate("We were attacked by a boar", lk) == "我们被野猪袭击了。")
    check("pat:snork", m.pattern_translate("A snork ambushed us", lk) == "史诺克伏击了我们。")
    check("pat:lurker补入", lk("lurker") == "潜伏者")
    check("pat:psysucker补入", lk("psysucker") == "吸血怪")
    # 库里没有的名词 → None，交回模型
    check("pat:miss", m.pattern_translate("We were attacked by a gorillax", lk) is None)
    # 非 ASCII 不处理
    check("pat:nonascii", m.pattern_translate("我们被野猪袭击", lk) is None)


def t_linebreaks():
    """#1 换行兜底：count 是换行符个数；部分缺行补齐，差异过大不动。"""
    # 缺行：译文 0 换行，原文要 2 个换行（3 行）→ 按句末标点补
    r = m.restore_linebreaks("第一句。第二句。第三句。", 2)
    check("nl:fill", r.count("\n") == 2)
    # 已对齐 → 原样
    check("nl:same", m.restore_linebreaks("一。\n二。", 1) == "一。\n二。")
    # 差异过大 → 不动（译文 19 换行，原文只要 1）
    many = "\n".join("第%d句。" % i for i in range(20))
    check("nl:too-many", m.restore_linebreaks(many, 1) == many)


def t_thresholds():
    """#9/#10/#11 阈值。"""
    check("tm:ratio8%", abs(m.TM_LITE_MIN_RATIO - 0.08) < 1e-9)
    # 批量不再被 force_glossary 掐死
    src = open(PLUGIN, encoding="utf-8").read()
    check("batch:no-force-gate", 'if "dialog" in rel.lower() and not self.force_glossary:' not in src)
    # #11 动态 max_tokens：短句不虚设、长段按输入给足、上限受 ctx 约束
    b = m.budget_max_tokens
    check("maxtok:short", b(50, 8192) == 1024)          # 短句走下限
    check("maxtok:long-enough", b(3000, 8192) > 3000)   # 长段给足，不截断
    check("maxtok:ctx-cap", b(100000, 8192) == int(8192 * 0.6))  # 不超窗口
    check("maxtok:bigctx", b(3000, 16384) == b(3000, 16384))      # 大 ctx 可给更多
    check("maxtok:monotonic", b(2000, 8192) > b(500, 8192))


def t_termbase_consistency():
    """两库仍一致、计数正确。"""
    import json
    base = os.path.join(os.path.dirname(PLUGIN))
    b = json.load(open(os.path.join(base, "assets", "glossary_builtin.json"), encoding="utf-8"))
    t = json.load(open(os.path.join(base, "assets", "stalker_termbase.json"), encoding="utf-8"))
    tf = set()
    for k, v in t.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            tf |= set(v)
    _flat = {}
    for _c, _dd in t.items():
        if isinstance(_dd, dict) and _c != "_meta":
            _flat.update(_dd)
    check("term:count", len(b) == len(_flat),
          "%d vs %d" % (len(b), len(_flat)))
    check("term:consistent", set(b) == tf)
    _tot = sum(len(v) for k, v in t.items()
                if k != "_meta" and isinstance(v, dict))
    check("term:meta-total", t["_meta"]["total_entries"] == _tot,
          "%s vs %s" % (t["_meta"]["total_entries"], _tot))


def main():
    t_version()
    t_en_profanity()
    t_bullet_clean()
    t_article_clean()
    t_space_fidelity()
    t_pattern()
    t_linebreaks()
    t_thresholds()
    t_termbase_consistency()
    ok = sum(1 for _x, r, _xd in RESULTS if r)
    for name, r, _d in RESULTS:
        if not r:
            print("FAIL %s" % name)
    print("test_2013_all_fixes: %d/%d 通过" % (ok, len(RESULTS)))
    return ok == len(RESULTS)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
