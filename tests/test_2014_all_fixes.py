# -*- coding: utf-8 -*-
"""2.0.14 回归测试（测试前深度审计发现的 4 类新问题）：
A 句中英文专名回填 / B 型号年份篡改 / C 字面 \n 保真 / D 标点规范化。
全部为确定性后修复（零模型请求），通用型，不针对单一 MOD。
"""
import sys
sys.dont_write_bytecode = True   # 别把 .pyc 写进随包插件目录（发布门禁 A6 会判为运行产物）
import importlib.util
import json
import os

PLUGIN = os.path.join(os.path.dirname(__file__), "..", "plugins",
                      "ai_translate", "plugin.py")
s = importlib.util.spec_from_file_location("p2014", PLUGIN)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)

RESULTS = []
NL = chr(92) + "n"  # 字面反斜杠n


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    return bool(cond)


def make_job():
    """构造一个带真实词库索引的 TranslationJob（只填专名回填所需字段）。"""
    job = object.__new__(m.TranslationJob)
    tl = m.load_term_layers()
    job.gloss_names = set(tl["names"])
    d = json.load(open(os.path.join(os.path.dirname(__file__), "..",
                       "plugins", "ai_translate", "assets",
                       "stalker_termbase.json"), encoding="utf-8"))
    flat = {}
    for k, v in d.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            flat.update(v)
    job.glossary = flat
    job.gloss = m.GlossaryIndex(
        flat, case_sensitive=set(tl.get("case_sensitive", [])),
        variants=dict(tl.get("variants", {})))
    job.detail = lambda x: None
    job._rule_hit = lambda a, b: None
    return job


def t_version():
    check("version:2.0.19+", m.PLUGIN_INFO["version"] in ("2.0.19", "2.0.20", "2.0.21", "2.0.22", "2.0.23", "2.0.24", "2.0.25", "2.0.26", "2.0.27"))


def t_latin_names():
    """A：英文专名残留按词库回填；型号/通用词不误伤。"""
    job = make_job()
    r = job._repair_residual_latin_names(
        "Nimble and Sidorovich are here", "Nimble 和 Sidorovich 在这里")
    check("latin:name-nimble", "机灵鬼" in r and "西德洛维奇" in r)
    r2 = job._repair_residual_latin_names(
        "AK-74 is good", "AK-74 很好用")
    check("latin:model-untouched", r2 == "AK-74 很好用")
    r3 = job._repair_residual_latin_names(
        "go see Beard", "去见 Beard")
    check("latin:beard", "大胡子" in r3)
    r4 = job._repair_residual_latin_names(
        "already translated", "已经翻译好了")
    check("latin:no-residue", r4 == "已经翻译好了")


def t_model_tokens():
    """B：型号/年份被换成别的能被检出；保留原文时无误报。"""
    src = 'this is the "FN FNX-45". developed in 2007-2009. its 2800 from you.'
    bad = "这是“SIG-Sauer P220”，220年研发，2800"
    good = '这是"FN FNX-45"，2007至2009年研发，2800'
    check("model:swapped", "FNX-45" in m.missing_model_tokens(src, bad))
    check("model:kept", m.missing_model_tokens(src, good) == [])
    check("year:swapped", "2007" in m.missing_big_numbers(src, bad))
    check("year:kept", m.missing_big_numbers(src, good) == [])
    # 介词+数字（in 2007）不应被当型号
    check("model:no-preposition",
          all("in " not in t for t in
              m.missing_model_tokens(src, "完全无关的译文")))


def t_literal_newlines():
    """C：字面 \\n 丢失补齐、过多收敛、一致不动。"""
    src = "$clr_1A" + NL + "$clr_2B"
    lost, fixed = m.reconcile_literal_newlines(src, "$clr_1A $clr_2B翻")
    check("nl:restored", fixed and m.count_literal_newlines(lost) == 1)
    same, fixed2 = m.reconcile_literal_newlines(src, src + "翻")
    check("nl:same", not fixed2 and same == src + "翻")
    # ★ 2.0.25：源无换行、译文 ≤2 个多余 \n → 确定性收敛为空格
    # （aera 实测 11 条「源 0 → 译 2」漏过串段闸门原样落盘）；>2 仍交闸门
    extra, fixed3 = m.reconcile_literal_newlines(
        "无换行", "多" + NL + NL + "行")
    check("nl:extra-collapsed", fixed3 and m.count_literal_newlines(extra) == 0)
    extra2, fixed4 = m.reconcile_literal_newlines(
        "无换行", "多" + NL + NL + NL + "行")
    check("nl:extra-many-left", not fixed4)
    check("nl:count", m.count_literal_newlines("A" + NL + "B" + NL) == 2)


def t_punctuation():
    """D：中文标点规范化；英文/小数点不动。"""
    r, f = m.normalize_cn_punctuation("职业玩家了！！！")
    check("punct:bang", r == "职业玩家了！！" and f)
    r2, _ = m.normalize_cn_punctuation("还有什么？...")
    check("punct:ellipsis", "……" in r2)
    r3, f3 = m.normalize_cn_punctuation("价格是 3.14 元")
    check("punct:decimal", r3 == "价格是 3.14 元" and not f3)
    r4, f4 = m.normalize_cn_punctuation("Version 2.0.12 build")
    check("punct:english", r4 == "Version 2.0.12 build" and not f4)
    r5, _ = m.normalize_cn_punctuation("你好,世界")
    check("punct:comma", "你好，世界" in r5)


def main():
    t_version()
    t_latin_names()
    t_model_tokens()
    t_literal_newlines()
    t_punctuation()
    ok = sum(1 for _, c in RESULTS if c)
    for name, cond in RESULTS:
        print("  [%s] %s" % ("OK" if cond else "FAIL", name))
    print("总计: %d/%d 通过" % (ok, len(RESULTS)))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
