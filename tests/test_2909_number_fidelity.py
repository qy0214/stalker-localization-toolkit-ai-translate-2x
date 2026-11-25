# -*- coding: utf-8 -*-
"""v2.0.9 回归：数字保真闸门 + 专名词库修正/新增。

用法: python tests/test_2909_number_fidelity.py [plugin.py 路径]
约定: 不依赖 pytest，自带断言器；退出码 0 = 全通过。
"""
import importlib.util
import io
import os
import shutil
import sys
sys.dont_write_bytecode = True   # 别把 .pyc 写进随包插件目录（发布门禁 A6 会判为运行产物）
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = (sys.argv[1] if len(sys.argv) > 1 else
          os.path.join(ROOT, "plugins", "ai_translate", "plugin.py"))
_ASSET_SRC = os.path.join(os.path.dirname(PLUGIN), "assets")

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ait2909_local_")
_ORIG_PLUGIN = PLUGIN
_TMP = tempfile.mkdtemp(prefix="ait2909_plugin_")
_PDIR = os.path.join(_TMP, "ai_translate")
os.makedirs(os.path.join(_PDIR, "assets"), exist_ok=True)
shutil.copy2(_ORIG_PLUGIN, os.path.join(_PDIR, "plugin.py"))
shutil.copytree(os.path.join(os.path.dirname(_ORIG_PLUGIN), "stalker_translate_core"),
                  os.path.join(_PDIR, "stalker_translate_core"),
                  dirs_exist_ok=True)
for _f in os.listdir(_ASSET_SRC):
    _src = os.path.join(_ASSET_SRC, _f)
    if os.path.isfile(_src):
        shutil.copy2(_src, os.path.join(_PDIR, "assets", _f))
PLUGIN = os.path.join(_PDIR, "plugin.py")
sys.path.insert(0, _PDIR)

spec = importlib.util.spec_from_file_location("ait2909", PLUGIN)
ait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ait)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + str(detail)) if detail else ""))


class ScriptedServer:
    def __init__(self, replies):
        self.replies = list(replies)
        self.n = 0
        self.notes = []

    def is_running(self):
        return True

    def translate(self, text, src, tgt, **kw):
        self.notes.append(kw.get("extra_sys"))
        i = min(self.n, len(self.replies) - 1)
        self.n += 1
        return self.replies[i]


def make_job(srv, **kw):
    root = tempfile.mkdtemp(prefix="ait2909_job_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs"), exist_ok=True)
    os.makedirs(out, exist_ok=True)
    with io.open(os.path.join(ind, "configs", "localization.ltx"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("[string_table]\nlanguage = rus\nfiles = stable_dialogs\n")
    defaults = dict(out_dir=out, log=lambda *a, **k: None,
                    reference_mode=True, copy_reference=True,
                    protect_proper=True, auto_session_terms=False,
                    freq_names=False, tm_lite=False, force_glossary=False)
    defaults.update(kw)
    return ait.TranslationJob(ait._load_vendor(), srv,
                              ind, "auto", "auto", "chs", **defaults), root


def t1_missing_detector():
    print("\n--- T1 缺失数字检测（纯函数）---")
    # 金额腰斩：必须抓
    check("T1-30000→15000 抓",
          ait.missing_big_numbers("30000 рублей будет достаточно",
                                  "15,000卢布就足够了") == ["30000"],
          ait.missing_big_numbers("30000 рублей", "15,000卢布"))
    check("T1-30000 原样保留不抓",
          not ait.missing_big_numbers("30000 рублей", "30000卢布"))
    check("T1-30000→30,000 千分位不抓",
          not ait.missing_big_numbers("30000 рублей", "30,000卢布"))
    check("T1-10000→1万 不抓",
          not ait.missing_big_numbers("за работу 10000 давай", "工作报酬1万"))
    check("T1-100000→10万 不抓",
          not ait.missing_big_numbers("100000 рублей", "10万卢布"))
    check("T1-2006-м→2006年 不抓",
          not ait.missing_big_numbers("в 2006-м году", "在2006年"))
    check("T1-年份篡改 2006→2016 抓",
          "2006" in ait.missing_big_numbers("в 2006-м году", "在2016年"),
          ait.missing_big_numbers("в 2006-м", "在2016年"))
    check("T1-多个金额缺一个也抓",
          "50000" in ait.missing_big_numbers("100000 и 50000 рублей",
                                             "100,000卢布"))
    check("T1-无大额数字不抓",
          not ait.missing_big_numbers("Я не готов.", "我还没准备好。"))
    check("T1-三位数编号原样不抓",
          not ait.missing_big_numbers("комната 1243", "房间1243"))
    check("T1-空译文返回空",
          ait.missing_big_numbers("30000 рублей", "") == [])


def t2_zh_number_parser():
    print("\n--- T2 中文数字解析 ---")
    v = ait._zh_number_values("三万卢布")
    check("T2-三万", 30000 in v, v)
    check("T2-一万五千", 15000 in ait._zh_number_values("一万五千卢布"),
          ait._zh_number_values("一万五千卢布"))
    check("T2-两万", 20000 in ait._zh_number_values("两万"),
          ait._zh_number_values("两万"))
    check("T2-1.5万", 15000 in ait._zh_number_values("1.5万"),
          ait._zh_number_values("1.5万"))
    check("T2-十万", 100000 in ait._zh_number_values("十万"),
          ait._zh_number_values("十万"))


def t3_gate_retry():
    print("\n--- T3 闸门：篡改→重试成功 ---")
    long_src = ("Мужик тот матёрый сталкер, так что, думаю, и хабар у него "
                "там не хуже. В общем, 30000 рублей будет достаточно.")
    bad = "那个家伙是个经验丰富的潜行者，所以我认为他的赃物也不会差。总之，15,000卢布就足够了。"
    good = "那个家伙是个经验丰富的潜行者，所以我认为他的赃物也不会差。总之，30000卢布就足够了。"
    srv = ScriptedServer([bad, good])
    job, _ = make_job(srv)
    out = job._translate_one(long_src, "rus", style="dialogue")
    check("T3-重试2次", srv.n == 2, "n=%d" % srv.n)
    check("T3-返回正确金额", out == good, out[:60])
    check("T3-带逐字保留提示", any(n and "逐字保留" in n for n in srv.notes),
          srv.notes)


def t4_gate_keep_source():
    print("\n--- T4 闸门：重试仍篡改→补数字保留译文（2.0.12）---")
    long_src = ("Мужик тот матёрый сталкер, так что, думаю, и хабар у него "
                "там не хуже. В общем, 30000 рублей будет достаточно.")
    bad = "总之，15,000卢布就足够了。那个经验丰富的潜行者赃物应该不差。"
    srv = ScriptedServer([bad, bad])
    job, _ = make_job(srv)
    out = job._translate_one(long_src, "rus", style="dialogue")
    # ★ 2.0.12：不再整段回退原文——补数字保留已译好的中文正文，
    # 一整段俄文比一个数字缺失严重得多（玩家看不懂）。
    check("T4-补数字保留译文",
          out != long_src and "30000" in out and "总之" in out, out[:60])
    # 完全没有中文时才回退原文
    bad_no_zh = "totally wrong no chinese at all here"
    srv2 = ScriptedServer([bad_no_zh, bad_no_zh])
    job2, _ = make_job(srv2)
    out2 = job2._translate_one(long_src, "rus", style="dialogue")
    check("T4-无中文仍回退原文", out2 == long_src, out2[:50])


def t5_termbase():
    print("\n--- T5 专名词库修正/新增 ---")
    fixes = {"Крысолов": "捕鼠人", "Борова": "博罗夫", "Гном": "格诺姆"}
    adds = {"Корефан": "科雷凡", "Калугин": "卡卢金",
            "100 Рентген": "100伦琴", "Юбилейный": "周年纪念",
            "Х-8": "X-8", "ХолодМаш": "冷机", "Тёмыч": "焦梅奇"}
    for w, want in list(fixes.items()) + list(adds.items()):
        got = ait.termbase_lookup(w)
        check("T5-%s"%w, got == want, "%s / 期望 %s" % (got, want))


def main():
    t1_missing_detector()
    t2_zh_number_parser()
    t3_gate_retry()
    t4_gate_keep_source()
    t5_termbase()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n==== 总计 %d 项，通过 %d，失败 %d ===="
          % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
    for n in failed:
        print("  FAIL:", n)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
