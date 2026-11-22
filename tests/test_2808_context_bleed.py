# -*- coding: utf-8 -*-
"""v2.0.8 回归：场景上下文串入译文（前一句/后一句）修复。

用法: python tests/test_2808_context_bleed.py [plugin.py 路径]
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

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ait2808_local_")
_ORIG_PLUGIN = PLUGIN
_TMP = tempfile.mkdtemp(prefix="ait2808_plugin_")
_PDIR = os.path.join(_TMP, "ai_translate")
os.makedirs(os.path.join(_PDIR, "assets"), exist_ok=True)
shutil.copy2(_ORIG_PLUGIN, os.path.join(_PDIR, "plugin.py"))
for _f in os.listdir(_ASSET_SRC):
    _src = os.path.join(_ASSET_SRC, _f)
    if os.path.isfile(_src):
        shutil.copy2(_src, os.path.join(_PDIR, "assets", _f))
PLUGIN = os.path.join(_PDIR, "plugin.py")
sys.path.insert(0, _PDIR)

spec = importlib.util.spec_from_file_location("ait2808", PLUGIN)
ait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ait)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + str(detail)) if detail else ""))


class ScriptedServer:
    """按调用次序返回预设译文；同时记录每次请求的上下文（kw['context']）。"""
    def __init__(self, replies):
        self.replies = list(replies)
        self.contexts = []
        self.n = 0

    def is_running(self):
        return True

    def translate(self, text, src, tgt, **kw):
        self.contexts.append(kw.get("context"))
        i = min(self.n, len(self.replies) - 1)
        self.n += 1
        r = self.replies[i]
        if callable(r):
            return r(text, kw)
        return r


def make_job(srv, **kw):
    root = tempfile.mkdtemp(prefix="ait2808_job_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs"), exist_ok=True)
    os.makedirs(out, exist_ok=True)
    with io.open(os.path.join(ind, "configs", "localization.ltx"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("[string_table]\nlanguage = rus\n"
                "files = stable_sms_character_name\n")
    defaults = dict(out_dir=out, log=lambda *a, **k: None,
                    reference_mode=True, copy_reference=True,
                    protect_proper=True, auto_session_terms=False,
                    freq_names=False, tm_lite=False, force_glossary=False)
    defaults.update(kw)
    return ait.TranslationJob(ait._load_vendor(), srv,
                              ind, "auto", "auto", "chs", **defaults), root


def t1_detectors():
    print("\n--- T1 上下文串入检测函数 ---")
    good = "我还没准备好。"
    bad_label = ("前一句：你终于接近了终点。你接下来要与一位熟练的潜行者"
                 "战斗。没错，他独自一人，但他穿着外骨骼装甲，还拿着 FN "
                 "F2000 步枪。如果你被击中，那你的下场就是死路一条。等待你"
                 "的将是一个惊喜，关于这个我无法告诉你，但我的")
    bad_long = "这是一整段上一条的长译文" * 16
    check("T1-正常译文不误判", not ait.looks_like_context_echo(good))
    check("T1-前一句标签命中", ait.looks_like_context_echo(bad_label))
    check("T1-后一句标签命中", ait.looks_like_context_echo("后一句：他来了。"))
    check("T1-上下文提示词回显命中",
          ait.looks_like_context_echo("上下文（仅供理解，不要翻译）：bla"))
    check("T1-bleed 短源文长输出命中",
          ait.looks_like_context_bleed("Я не готов.", bad_long))
    check("T1-bleed 短源文正常输出不命中",
          not ait.looks_like_context_bleed("Я не готов.", "我还没准备好。"))
    check("T1-bleed 长源文长输出不命中",
          not ait.looks_like_context_bleed("Вот ты и дошёл почти до финала. " * 4,
                                           bad_long))
    check("T1-bleed 标签命中", ait.looks_like_context_bleed("短", bad_label))


def t2_source_guard():
    print("\n--- T2 源头减险：极短条目不注入上下文 ---")
    # 极短条目：即便传入上下文也应被丢弃（<24 字符）
    srv = ScriptedServer(["我还没准备好。"])
    job, _ = make_job(srv)
    job._translate_one("Я не готов.", "rus", context="前一句：很长的上一条对话文本",
                       style="dialogue")
    check("T2-极短源文请求不带上下文", srv.contexts[0] in (None, ""),
          repr(srv.contexts[0]))

    # 长条目：上下文照常保留
    long_src = ("Вот ты и дошёл почти до финала. Тебе предстоит бой со "
                "сталкером-мастером. Да, он один, но он в экзоскелете.")
    srv2 = ScriptedServer(["译文：" + long_src[:10]])
    job2, _ = make_job(srv2)
    job2._translate_one(long_src, "rus", context="前一句：开场白",
                        style="dialogue")
    check("T2-长源文请求保留上下文", srv2.contexts[0] == "前一句：开场白",
          repr(srv2.contexts[0]))


def t3_retry_strips_context():
    print("\n--- T3 污染重试：清上下文 + 去污成功 ---")
    # 长源文才会带上下文。第一次带上下文返回被污染的长译文，
    # 第二次（应无上下文）返回干净译文。
    long_src = ("Вот ты и дошёл почти до финала. Тебе предстоит бой со "
                "сталкером-мастером. Да, он один, но он в экзоскелете и с "
                "FN F2000, если попадёшь под очередь, считай, что ты труп.")
    polluted = ("前一句：你终于接近了终点。你接下来要与一位熟练的潜行者"
                "战斗。" * 6)
    clean = ("你终于接近了终点，接下来要与一位熟练的潜行者战斗。没错，他独自"
             "一人，但他穿着外骨骼装甲，还拿着 FN F2000 步枪，如果你被击中"
             "那就是死路一条。")
    srv = ScriptedServer([polluted, clean])
    job, _ = make_job(srv)
    out = job._translate_one(long_src, "rus", context="前一句：某条源文",
                             style="dialogue")
    check("T3-重试次数为 2", srv.n == 2, "n=%d" % srv.n)
    check("T3-第一次带上下文", bool(srv.contexts[0]), repr(srv.contexts[0])[:40])
    check("T3-第二次已清上下文", srv.contexts[1] in (None, ""),
          repr(srv.contexts[1]))
    check("T3-返回干净译文", out == clean, out)


def t4_keep_source_on_failure():
    print("\n--- T4 重试仍污染：保留原文 ---")
    long_src = ("Вот ты и дошёл почти до финала. Тебе предстоит бой со "
                "сталкером-мастером. Да, он один, но он в экзоскелете и с "
                "FN F2000, если попадёшь под очередь, считай, что ты труп.")
    polluted = "前一句：你终于接近了终点。" * 10
    srv = ScriptedServer([polluted, polluted])  # 两次都污染
    job, _ = make_job(srv)
    out = job._translate_one(long_src, "rus", context="前一句：某条源文",
                             style="dialogue")
    check("T4-两次仍污染则保留原文", out == long_src, out[:40])


def t5_false_positive_guard():
    print("\n--- T5 不误伤：比喻性提到「前一句」的正常中文 ---")
    # 真正的对话内容里出现「前一句：」这种字面串极罕见；
    # 但测试确认普通含冒号译文不会被 looks_like_context_echo 误判。
    check("T5-普通中文冒号不误判",
          not ait.looks_like_context_echo("他说：我准备好了。"))
    check("T5-英文冒号不误判",
          not ait.looks_like_context_echo("Note: this is fine."))


def main():
    t1_detectors()
    t2_source_guard()
    t3_retry_strips_context()
    t4_keep_source_on_failure()
    t5_false_positive_guard()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n==== 总计 %d 项，通过 %d，失败 %d ===="
          % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
    for n in failed:
        print("  FAIL:", n)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
