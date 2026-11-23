# -*- coding: utf-8 -*-
"""2.0.15 回归：终审发现的 3 个小缺口（N1/N2/N3）。

用法: python tests/test_2015_all_fixes.py
覆盖：颜色码后粘连冠词剥离、专名回填复数/所有格/大小写/跨层扩展、
      词库补录 10 个全系列通用词。
"""
import importlib.util
import json
import os
import sys
sys.dont_write_bytecode = True   # 别把 .pyc 写进随包插件目录（发布门禁 A6 会判为运行产物）

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "plugins", "ai_translate", "plugin.py")

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


spec = importlib.util.spec_from_file_location("ait2015", PLUGIN)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def make_job():
    tl = m.load_term_layers()
    d = json.load(open(os.path.join(ROOT, "plugins", "ai_translate",
                                    "assets", "stalker_termbase.json"),
                       encoding="utf-8"))
    flat = {}
    for k, v in d.items():
        if not k.startswith("_") and isinstance(v, dict):
            flat.update(v)
    job = object.__new__(m.TranslationJob)
    job.gloss_names = set(tl["names"])
    job.glossary = flat
    job.gloss = m.GlossaryIndex(
        flat, case_sensitive=set(tl.get("case_sensitive", [])),
        variants=dict(tl.get("variants", {})))
    job.detail = lambda x: None
    return job


def main():
    check("version:2.0.19", m.PLUGIN_INFO["version"] in ("2.0.19", "2.0.20", "2.0.21", "2.0.22", "2.0.23", "2.0.24", "2.0.25", "2.0.26", "2.0.27"),
          m.PLUGIN_INFO["version"])

    # ---- N1 颜色码后粘连冠词 ----
    r, f = m.clean_stuck_article("%c[default]The科学家是一个机构")
    check("art:after-colorcode", f and "%c[default]科学家" in r
          and "The科学家" not in r, repr(r))
    r2, f2 = m.clean_stuck_article("An神器具有引力特性")
    check("art:line-start", f2 and r2 == "神器具有引力特性", repr(r2))
    check("art:english-kept",
          m.clean_stuck_article("An apple a day")[1] is False)

    # ---- N2 复数/所有格/大小写/跨层 ----
    job = make_job()
    src = ('you should watch those "Whirligigs"; I saw a Whirligig '
           'pull a body in')
    out = '你应该留意那些“Whirligigs”，附近有一个“Whirligig”'
    r = job._repair_residual_latin_names(src, out)
    check("latin:plural-crosslayer",
          "旋风" in r and "Whirligig" not in r, repr(r))
    r2 = job._repair_residual_latin_names('You have seen "Circus" anomalies',
                                          '你见过“Circus”异常现象吗')
    check("latin:new-term", "马戏团" in r2, repr(r2))
    r3 = job._repair_residual_latin_names("Nimble task", "Nimble’s 任务")
    check("latin:possessive", "机灵鬼" in r3 and "Nimble" not in r3, repr(r3))
    r4 = job._repair_residual_latin_names("The Sniper is good",
                                          "那个 Sniper 很不错")
    check("latin:generic-kept", "Sniper" in r4, repr(r4))
    r5 = job._repair_residual_latin_names("AK-74 is good", "AK-74 很好用")
    check("latin:model-safe", r5 == "AK-74 很好用", repr(r5))
    r6 = job._repair_residual_latin_names("Nimble and Sidorovich",
                                          "机灵鬼 和 西德洛维奇")
    check("latin:translated-kept", r6 == "机灵鬼 和 西德洛维奇", repr(r6))

    # ---- N3 词库补录 ----
    for w, zh in [("Circus", "马戏团"), ("Duga", "杜加"),
                  ("Skadovsk", "斯卡多夫斯克"), ("100 Rads", "辐射100"),
                  ("Spore", "孢子"), ("Hercules", "大力士"),
                  ("Psy-block", "精神阻断剂"), ("Nitro", "硝基"),
                  ("Swede", "瑞典佬"), ("Volcano", "火山")]:
        check("gloss:%s" % w.lower().replace(" ", "_"),
              m.termbase_exact_translation(w) == zh,
              m.termbase_exact_translation(w))
    # meta 一致
    d = json.load(open(os.path.join(ROOT, "plugins", "ai_translate",
                                    "assets", "stalker_termbase.json"),
                       encoding="utf-8"))
    total = sum(len(v) for k, v in d.items()
                if k != "_meta" and isinstance(v, dict))
    check("gloss:meta-sync", d["_meta"]["total_entries"] == total,
          "%s vs %s" % (d["_meta"]["total_entries"], total))
    bb = json.load(open(os.path.join(ROOT, "plugins", "ai_translate",
                                     "assets", "glossary_builtin.json"),
                        encoding="utf-8"))
    flat = {}
    for cat, dd in d.items():
        if isinstance(dd, dict) and cat != "_meta":
            flat.update(dd)
    # ★ 2.0.25：gb 允许混合结构（扁平+分类嵌套），本质等价断言用 flatten
    check("gloss:builtin-sync",
          m.flatten_glossary(bb) == flat)

    ok = sum(1 for _, r, _ in RESULTS if r)
    print("\n==== %d/%d 通过 ====" % (ok, len(RESULTS)))
    if ok != len(RESULTS):
        for name, r, detail in RESULTS:
            if not r:
                print("  - %s | %s" % (name, detail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
