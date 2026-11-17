# -*- coding: utf-8 -*-
"""2.0.3 提示词与规则约束 回归测试

用法:
    python test_prompt_rules.py [plugin.py 路径]

约定（与既有套件相同）:
    * 不依赖 pytest，自带极简断言器；退出码 0 = 全通过
    * 在临时副本上跑，避免插件导入时在仓库里生成 bin/models/logs/config
    * LlamaServer / TranslationJob 真实构造 + 按需打桩

覆盖:
    T1 提示词重构后回显检测锚点仍在、分区与首尾复述存在
    T2 glossary_hint_block 官方措辞格式（X 翻译成 Y）与预算行为
    T3 规则违规遥测：_rule_hit 计数 + _audit_report 点名最高频
    T4 长度比闸门：漏译重试成功 / 重试仍异常保留首译 / 正常不触发 /
       label 风格不触发
    T5 高频专名定译：句中大写词筛选、停用词排除、批量定译、
       软层登记（glossary/gloss_soft/重建索引）、本机缓存落盘
    T6 官方措辞开关：translate payload 指令与术语块措辞切换
    T7 freq_names 构造参数传递
"""

import atexit
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
PLUGIN = (sys.argv[1] if len(sys.argv) > 1 else
          os.path.join(ROOT, "plugins", "ai_translate", "plugin.py"))
_ASSET_SRC = os.path.join(os.path.dirname(PLUGIN), "assets")

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ait_pr_localappdata_")
atexit.register(shutil.rmtree, os.environ["LOCALAPPDATA"], ignore_errors=True)
_TEST_ROOT = tempfile.mkdtemp(prefix="ait_pr_plugin_")
_PLUGIN_DIR = os.path.join(_TEST_ROOT, "ai_translate")
os.makedirs(os.path.join(_PLUGIN_DIR, "assets"), exist_ok=True)
shutil.copy2(PLUGIN, os.path.join(_PLUGIN_DIR, "plugin.py"))
for _name in ("stalker_termbase.json", "glossary_builtin.json",
              "xray_pkg.zip"):
    _src = os.path.join(_ASSET_SRC, _name)
    if os.path.isfile(_src):
        shutil.copy2(_src, os.path.join(_PLUGIN_DIR, "assets", _name))
PLUGIN = os.path.join(_PLUGIN_DIR, "plugin.py")
atexit.register(shutil.rmtree, _TEST_ROOT, ignore_errors=True)

TMP = tempfile.mkdtemp(prefix="ait_pr_tmp_")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + detail) if detail else ""))
    return ok


def load_module(path, name="ait"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


print("=" * 78)
print("加载被测插件:", PLUGIN)
ait = load_module(PLUGIN)
print("=" * 78)


class FakeServer(object):
    def __init__(self, reply=None):
        self.calls = []
        self._reply = reply

    def is_running(self):
        return True

    def translate(self, text, src, tgt, glossary_hits=None, timeout_sec=180,
                  max_tokens=None, extra_sys=None, **kwargs):
        self.calls.append(text)
        if self._reply is not None:
            return self._reply(text)
        return "译:" + text


def make_job(in_dir, mode="auto", server=None, glossary=None, **kw):
    parser = ait._load_vendor()
    detail = kw.pop("detail", None) or (lambda *a, **k: None)
    return ait.TranslationJob(
        parser, server or FakeServer(), in_dir, mode, "rus", "chs",
        glossary=glossary or {}, log=lambda *a, **k: None,
        detail=detail, **kw)


_RUS_LONG = ("Старый друг Кривой долго ждал меня у Свалки и все время "
             "спрашивал про Свалку и про тех, кто ушел к Свалке вчера")


# ----------------------------------------------------------------------
def t1_prompt_anchors():
    print("\n--- T1 提示词锚点与结构 ---")
    for tag, p in (("RU", ait.TRANSLATION_SYS_PROMPT),
                   ("EN", ait.TRANSLATION_SYS_PROMPT_EN)):
        # 前 4 个锚点在 sys prompt 里；最后一个锚定术语块 header（回显检测
        # 同时覆盖"把术语块当译文回显"的形态）。
        for m in ait._PROMPT_ECHO_MARKERS[:-1]:
            check("T1-%s 锚点保留：%s" % (tag, m[:12]), m in p)
        check("T1-%s 术语块锚点指向 header" % tag,
              ait.GLOSSARY_HINT_HEADER.startswith(
                  ait._PROMPT_ECHO_MARKERS[-1]))
        check("T1-%s 分区【硬性规则】" % tag, "【硬性规则】" in p)
        check("T1-%s 分区【语气与风格】" % tag, "【语气与风格】" in p)
        check("T1-%s 首尾复述（不得残留源文）" % tag,
              p.rstrip().endswith("俄文。") or "不得残留" in p[-160:])
    check("T1 短标签/批量提示词未动",
          "不扩写" in ait.TRANSLATION_SYS_PROMPT_LABEL
          and "[n]" in ait.TRANSLATION_BATCH_SYS_PROMPT)


def t2_glossary_official():
    print("\n--- T2 术语块官方措辞 ---")
    pairs = [("Свалка", "垃圾场"), ("Кривой", "克里沃伊")]
    mine, n1 = ait.glossary_hint_block(pairs)
    off, n2 = ait.glossary_hint_block(pairs, official=True)
    check("T2-1 自研格式 X=Y", mine.startswith("术语(用给定译名)：")
          and "Свалка=垃圾场" in mine)
    check("T2-2 官方格式 X 翻译成 Y",
          off.startswith("参考下面的翻译：")
          and "Свалка 翻译成 垃圾场" in off)
    check("T2-3 两种格式计费近似", abs(n1 - n2) <= 4 * len(pairs))
    check("T2-4 空词表返回空", ait.glossary_hint_block([]) == ("", 0))


def t3_audit():
    print("\n--- T3 规则违规遥测 ---")
    job = make_job(TMP)
    check("T3-1 初始为空", job._audit_stats == {})
    job._rule_hit("占位符保留", "retried_ok")
    job._rule_hit("占位符保留", "retried_ok")
    job._rule_hit("占位符保留", "kept_source")
    job._rule_hit("粗口力度", "kept_first")
    check("T3-2 计数正确",
          job._audit_stats["占位符保留"]["hits"] == 3
          and job._audit_stats["占位符保留"]["kept_source"] == 1
          and job._audit_stats["粗口力度"]["hits"] == 1)
    logs = []
    job.log = lambda m, tag="info": logs.append((tag, str(m)))
    job._audit_report()
    joined = " ".join(m for _t, m in logs)
    check("T3-3 报告点名最高频", "最需要关注的规则：占位符保留" in joined
          and "占位符保留（3 次" in joined)
    check("T3-4 收场明细在报告里", "重试成功 2" in joined
          and "保留原文 1" in joined)
    logs2 = []
    job2 = make_job(TMP, detail=lambda m, tag="info": logs2.append(str(m)))
    gui2 = []
    job2.log = lambda m, tag="info": gui2.append(str(m))
    job2._audit_report()
    check("T3-5 零违规只落明细日志（不上 GUI）",
          any("零违规" in m for m in logs2) and not gui2)


def t4_length_ratio():
    print("\n--- T4 长度比闸门 ---")
    short = "就这些了。"

    # 4-1 漏译 → 重试成功（完整稿与原文长度比需落在 0.3~2.0）
    seq = [short, "很久以前，老朋友克里沃伊就一直在垃圾场等着我，"
                 "还不停地向我打听那些早就离开这里、去了垃圾场的人"
                 "最后到底怎么样了。"]
    calls = []

    def plain_ok(text, lang, note=""):
        calls.append(note)
        return seq.pop(0) if len(seq) > 1 else seq[-1]

    job = make_job(TMP)
    job._translate_plain = plain_ok
    out = job._translate_one(_RUS_LONG, "rus", style="dialogue")
    check("T4-1 漏译触发重试并采用完整稿",
          len(out) > 10 and "克里沃伊" in out
          and job._audit_stats.get("长度比（漏译/扩写）", {}).get(
              "retried_ok") == 1,
          "ratio 修复")
    check("T4-2 重试带针对性补充要求",
          calls and "漏译" in (calls[-1] or ""))

    # 4-2 重试仍异常 → 保留首译
    job2 = make_job(TMP)
    job2._translate_plain = lambda text, lang, note="": short
    out2 = job2._translate_one(_RUS_LONG, "rus", style="dialogue")
    check("T4-3 重试仍异常保留首译",
          out2 == short
          and job2._audit_stats.get("长度比（漏译/扩写）", {}).get(
              "kept_first") == 1)

    # 4-3 正常长度比不触发（只发一次请求）
    normal = "老朋友克里沃伊在垃圾场等我很久，一直打听那些人。" * 2
    n = [0]

    def plain_once(text, lang, note=""):
        n[0] += 1
        return normal

    job3 = make_job(TMP)
    job3._translate_plain = plain_once
    job3._translate_one(_RUS_LONG, "rus", style="dialogue")
    check("T4-4 正常比值零重试", n[0] == 1
          and "长度比" not in job3._audit_stats)

    # 4-4 短标签不触发
    job4 = make_job(TMP)
    job4._translate_plain = lambda text, lang, note="": short
    m = [0]

    def plain_cnt(text, lang, note=""):
        m[0] += 1
        return short

    job4._translate_plain = plain_cnt
    job4._translate_one(_RUS_LONG, "rus", style="label")
    check("T4-5 label 风格跳过长度比", m[0] == 1
          and "长度比" not in job4._audit_stats)


def t5_freq_names():
    print("\n--- T5 高频专名定译 ---")
    cache_dir = os.path.join(TMP, "freqcache")
    os.makedirs(cache_dir, exist_ok=True)
    job = make_job(TMP, server=FakeServer(), auto_session_terms=False)
    job.session_cache_dir = cache_dir
    job.auto_freq_names = True

    # 文本语料：Свалка 出现 6 次（句中 5 次）→ 入选；Кривой 句中 1 次 →
    # 落选；Когда 句中 3 次但属停用词 → 排除。（注意：俄语格变
    # Свалка/Свалке/Свалки 是不同词，语料统一用主格。）
    corpus = [
        ("rus", "Тут Свалка. Свалка была тихая. Мы у Свалка сидели. "
                "Свалка дым. Огни у Свалка горели."),
        ("rus", "Когда он ушел. Когда вернулся. Когда все кончилось. "
                "Кривой молчал."),
    ]
    job._collect_text_targets = lambda: [("t/a.xml", "a"), ("t/b.xml", "b")]
    job._parse = lambda fn, path, enc: corpus
    job._pick_source = lambda ent: ent
    idx_calls = [0]

    def fake_index():
        idx_calls[0] += 1
        return object()

    job._new_gloss_index = fake_index
    repl = {"Свалка": "垃圾场", "Когда": "到时候"}

    def reply(text):
        lines = []
        for w in text.splitlines():
            lines.append(repl.get(w.strip(), "音译" + w.strip()))
        return "\n".join(lines)

    job.srv = FakeServer(reply=reply)
    job._build_freq_names()
    check("T5-1 高频专名入选并定译",
          job.freq_names.get("Свалка") == "垃圾场",
          "freq_names=%s" % job.freq_names)
    check("T5-2 停用词被排除", "Когда" not in job.freq_names)
    check("T5-3 低频/句首词被排除", "Кривой" not in job.freq_names)
    check("T5-4 软层登记（glossary+gloss_soft）",
          "Свалка" in job.glossary and "Свалка" in job.gloss_soft)
    check("T5-5 重建术语索引", idx_calls[0] >= 1)
    caches = [f for f in os.listdir(cache_dir) if f.startswith("freq_")]
    check("T5-6 本机缓存落盘", len(caches) == 1)
    stats = job.freq_stats
    check("T5-7 统计齐全", stats["candidates"] >= 1
          and stats["translated"] + stats["cached"] >= 1)

    # 二次运行命中缓存：不再发翻译请求
    calls_before = len(job.srv.calls)
    job2 = make_job(TMP, server=FakeServer(reply=reply),
                    auto_session_terms=False)
    job2.session_cache_dir = cache_dir
    job2.auto_freq_names = True
    job2._collect_text_targets = job._collect_text_targets
    job2._parse = job._parse
    job2._pick_source = job._pick_source
    job2._new_gloss_index = fake_index
    job2.srv = FakeServer(reply=reply)
    job2._build_freq_names()
    check("T5-8 二次运行零请求（缓存命中）",
          len(job2.srv.calls) == 0
          and job2.freq_names.get("Свалка") == "垃圾场")


class _FakeResp(object):
    def __init__(self, obj):
        self._b = json.dumps(obj).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def t6_official_payload():
    print("\n--- T6 官方措辞 payload ---")
    orig_post = ait._http_post
    captured = {}
    try:
        def fake_post(url, payload, headers=None, timeout=None):
            captured["payload"] = json.loads(payload.decode("utf-8"))
            return _FakeResp({"choices": [
                {"message": {"content": "垃圾场"}}]})

        ait._http_post = fake_post
        srv = ait.LlamaServer("x.exe", "m.gguf", "cpu",
                              lambda *a, **k: None, ctx_size=8192,
                              parallel=1)
        srv.is_running = lambda: True
        srv.base = "http://127.0.0.1:1"  # base 只在 start() 时赋值

        srv.official_prompts = False
        srv.translate("Свалка", "rus", "chs",
                      glossary_hits=[("Свалка", "垃圾场")])
        user = captured["payload"]["messages"][1]["content"]
        check("T6-1 自研措辞（将下面的俄语文本翻译为）",
              "将下面的俄语文本翻译为简体中文" in user
              and "术语(用给定译名)：" in user and "Свалка=垃圾场" in user)

        srv.official_prompts = True
        srv.translate("Свалка", "rus", "chs",
                      glossary_hits=[("Свалка", "垃圾场")])
        user2 = captured["payload"]["messages"][1]["content"]
        check("T6-2 官方措辞指令",
              "将以下文本翻译为简体中文，注意只需要输出翻译后的结果" in user2
              and "不要额外解释" in user2)
        check("T6-3 官方术语块格式",
              "参考下面的翻译：" in user2
              and "Свалка 翻译成 垃圾场" in user2)
        sysmsg = captured["payload"]["messages"][0]["content"]
        check("T6-4 system 规则两版共用（游戏约束保留）",
              "占位符" in sysmsg and "S.T.A.L.K.E.R." in sysmsg)
    finally:
        ait._http_post = orig_post


def t7_freq_param():
    print("\n--- T7 构造参数 ---")
    job = make_job(TMP, freq_names=False)
    check("T7-1 freq_names=False 不启用", job.auto_freq_names is False)
    job2 = make_job(TMP)
    check("T7-2 默认启用", job2.auto_freq_names is True)
    cfg = ait.load_config()
    check("T7-3 config 默认键齐全",
          cfg.get("freq_names") is True
          and cfg.get("official_prompts") is False)


def main():
    t1_prompt_anchors()
    t2_glossary_official()
    t3_audit()
    t4_length_ratio()
    t5_freq_names()
    t6_official_payload()
    t7_freq_param()
    failed = [r for r in RESULTS if not r[1]]
    print("\n" + "=" * 78)
    print("共 %d 项，通过 %d，失败 %d"
          % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
    if failed:
        for name, _ok, d in failed:
            print("  FAIL:", name, d)
        sys.exit(1)
    print("ALL_PROMPT_RULES_OK")


if __name__ == "__main__":
    main()
