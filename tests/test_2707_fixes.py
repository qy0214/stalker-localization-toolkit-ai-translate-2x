# -*- coding: utf-8 -*-
"""v2.0.7 修复回归：闸门阈值 / 专名词库直取 / 终检条目级漏译。

用法: python tests/test_2707_fixes.py [plugin.py 路径]
约定: 不依赖 pytest，自带断言器；退出码 0 = 全通过。
"""
import importlib.util
import io
import json
import os
import shutil
import sys
sys.dont_write_bytecode = True   # 别把 .pyc 写进随包插件目录（发布门禁 A6 会判为运行产物）
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = (sys.argv[1] if len(sys.argv) > 1 else
          os.path.join(ROOT, "plugins", "ai_translate", "plugin.py"))
_ASSET_SRC = os.path.join(os.path.dirname(PLUGIN), "assets")

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ait2707_local_")
_ORIG_PLUGIN = PLUGIN
_TMP = tempfile.mkdtemp(prefix="ait2707_plugin_")
_PDIR = os.path.join(_TMP, "ai_translate")
os.makedirs(os.path.join(_PDIR, "assets"), exist_ok=True)
shutil.copy2(_ORIG_PLUGIN, os.path.join(_PDIR, "plugin.py"))
for _f in os.listdir(_ASSET_SRC):
    _src = os.path.join(_ASSET_SRC, _f)
    if os.path.isfile(_src):
        shutil.copy2(_src, os.path.join(_PDIR, "assets", _f))
PLUGIN = os.path.join(_PDIR, "plugin.py")
sys.path.insert(0, _PDIR)

spec = importlib.util.spec_from_file_location("ait2707", PLUGIN)
ait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ait)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + str(detail)) if detail else ""))


class FakeServer:
    def __init__(self):
        self.calls = []

    def is_running(self):
        return True

    def translate(self, text, src, tgt, **kw):
        self.calls.append(text)
        return "[译]" + text[:20]

    def translate_batch(self, lines, src, tgt, **kw):
        self.calls.extend(lines)
        return "\n".join("[%d] [译]%s" % (i + 1, t)
                         for i, t in enumerate(lines))


def make_job(root, srv=None, **kw):
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs"), exist_ok=True)
    os.makedirs(out, exist_ok=True)
    with io.open(os.path.join(ind, "configs", "localization.ltx"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("[string_table]\nlanguage = rus\nfiles = stable_sms_character_name\n")
    defaults = dict(out_dir=out, log=lambda *a, **k: None,
                    reference_mode=True, copy_reference=True,
                    protect_proper=True, auto_session_terms=False,
                    freq_names=False, tm_lite=False,
                    force_glossary=False)
    defaults.update(kw)
    return ait.TranslationJob(ait._load_vendor(), srv or FakeServer(),
                              ind, "auto", "auto", "chs", **defaults), ind, out


def xml_text(entries):
    body = "".join('<string id="%s"><rus>%s</rus></string>' % (i, t)
                   for i, t in entries)
    return '<?xml version="1.0" encoding="windows-1251"?>\n<string_table>' \
           + body + '</string_table>'


def write_table(root, entries):
    ind = os.path.join(root, "in")
    sub = os.path.join(ind, "configs", "text", "character_name")
    os.makedirs(sub, exist_ok=True)
    p = os.path.join(sub, "stable_sms_character_name.xml")
    with open(p, "wb") as f:
        f.write(xml_text(entries).encode("windows-1251"))
    return p


def t1_termbase_lookup():
    print("\n--- T1 术语库既有译名直取 ---")
    cases = {
        "БАРМЕН": "酒保", "ПРИЗРАК": "幽灵", "ЗВЕРОБОЙ": "猎兽人",
        "СИДОРОВИЧ": "西德洛维奇", "СТРЕЛОК": "史特列洛克",
        "ЛИС": "狐狸", "ЛЕСНИК": "守林员", "БРОДЯГА": "流浪汉",
        "ХАЛЯВА": "白给者", "ЗАПИСЬ НА ДИКТОФОНЕ": "录音笔录音",
    }
    for src, want in cases.items():
        got = ait.termbase_lookup(src)
        check("T1-词库命中 %s" % src, got == want, "%s / 期望 %s" % (got, want))
    check("T1-大小写无关", ait.termbase_lookup("бармен") == "酒保")
    check("T1-词库未收录返回 None",
          ait.termbase_lookup("一些完全不存在的西里尔短语XYZ123") is None)
    check("T1-引号处理（去双引号避免嵌套）",
          ait.termbase_exact_translation('"СВОБОДА"') == '自由团',
          ait.termbase_exact_translation('"СВОБОДА"'))


def t2_cyrillic_upper_not_protected():
    print("\n--- T2 西里尔全大写短名不再自动保护 ---")
    rel = "configs/text/character_name/stable_sms_character_name.xml"
    for s in ("БАРМЕН", "ПРИЗРАК", "ЗВЕРОБОЙ", "ХАЛЯВА",
              "ЗАПИСЬ НА ДИКТОФОНЕ"):
        check("T2-西里尔不保护 %s" % s[:12],
              not ait.looks_like_proper_text(rel, s))
    for s in ("NVIDIA DLSS", "AK-74", "VSS"):
        check("T2-拉丁型号仍保护 %s" % s,
              ait.looks_like_proper_text(rel, s))
    check("T2-英文人名仍不整类保护",
          not ait.looks_like_proper_text(rel, "John Smith"))


def t3_end_to_end_names():
    print("\n--- T3 整表翻译：词库名字被译出 ---")
    root = tempfile.mkdtemp(prefix="ait2707_e2e_")
    try:
        names = [("sms_bar_barmen_name", "БАРМЕН"),
                 ("sms_yan_ghost_name", "ПРИЗРАК"),
                 ("sms_zveroboy_name", "ЗВЕРОБОЙ"),
                 ("sms_forest_lesnik_name", "ЛЕСНИК"),
                 ("sms_xalyava_name", "ХАЛЯВА"),
                 ("sms_unknown_name", "НЕИЗВЕСТНОЕСЛОВО123"),
                 ("sms_svoboda_name", '"СВОБОДА"')]
        write_table(root, names)
        srv = FakeServer()
        job, ind, out = make_job(
            root, srv, glossary={}, hard_glossary_keys=None)
        ok = job.run()
        p = os.path.join(out, "configs", "text", "character_name",
                         "stable_sms_character_name.xml")
        body = open(p, encoding="utf-8").read() if os.path.isfile(p) else ""
        check("T3-run 成功", ok)
        for _id, want in (("sms_bar_barmen_name", "酒保"),
                          ("sms_yan_ghost_name", "幽灵"),
                          ("sms_zveroboy_name", "猎兽人"),
                          ("sms_forest_lesnik_name", "守林员"),
                          ("sms_xalyava_name", "白给者"),
                          ("sms_svoboda_name", "自由团")):
            check("T3-译出 %s" % want, want in body, body[:200])
        # 词库未收的西里尔短名：应送模型，不再原样保留
        check("T3-未收词条送模型",
              any("НЕИЗВЕСТНОЕСЛОВО123" in c for c in srv.calls),
              "calls=%d" % len(srv.calls))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t4_length_ratio_ru_zh():
    print("\n--- T4 长度比：俄→中压缩比误判修正 ---")
    root = tempfile.mkdtemp(prefix="ait2707_ratio_")
    try:
        job, _, _ = make_job(root)
        src = ("Хранитель группировки Монолит. Лучше держаться от него. "
               "Детальная информация отсутствует.")
        good_zh = "“黑石”组织的守护者，最好远离他。暂无详细信息。"   # ratio ~0.3
        calls = []

        def plain(text, lang, note=""):
            calls.append(note)
            return good_zh

        job._translate_plain = plain
        out = job._translate_one(src, "rus", style="dialogue")
        check("T4-俄中 0.3 左右不触发重试", len(calls) == 1,
              "calls=%d out=%s" % (len(calls), out[:30]))
        check("T4-不记长度比闸门命中",
              "长度比（漏译/扩写）" not in job._audit_stats)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t5_final_audit_untranslated():
    print("\n--- T5 终检发现条目级未翻译 ---")
    root = tempfile.mkdtemp(prefix="ait2707_audit_")
    try:
        job, ind, out = make_job(root)
        ent = ("sms_bar_barmen_name", "БАРМЕН")
        write_table(root, [ent])
        p = os.path.join(out, "configs", "text", "character_name",
                         "stable_sms_character_name.xml")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        # 输出与原文字节相同（windows-1251），模拟"保留原文"落盘
        with open(p, "wb") as f:
            f.write(xml_text([ent]).encode("windows-1251"))
        job._audit_tables.add("configs/text/character_name/"
                              "stable_sms_character_name.xml")
        logs = []
        job.log = lambda m, tag="info": logs.append(m)
        job._final_audit(out)
        joined = "\n".join(logs)
        # БАРМЕН 在词库里有译名 → 终检会把它当"有意保留"排除。
        # 用未收词条的俄文验证扫描确实工作。
        check("T5-词库条目不算漏译", "条目未翻译" not in joined, joined[:200])
        ent2 = ("sms_unknown_name", "НЕИЗВЕСТНОЕСЛОВО123")
        write_table(root, [ent2])
        with open(p, "wb") as f:
            f.write(xml_text([ent2]).encode("windows-1251"))
        logs.clear()
        job._final_audit(out)
        joined = "\n".join(logs)
        check("T5-未收词条残留被点名",
              "条目未翻译" in joined and "sms_unknown_name" in joined,
              joined[:300])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    t1_termbase_lookup()
    t2_cyrillic_upper_not_protected()
    t3_end_to_end_names()
    t4_length_ratio_ru_zh()
    t5_final_audit_untranslated()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n总计 %d 项：通过 %d，失败 %d" %
          (len(RESULTS), passed, len(RESULTS) - passed))
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL: %s  %s" % (name, detail))
    shutil.rmtree(_TMP, ignore_errors=True)
    shutil.rmtree(os.environ["LOCALAPPDATA"], ignore_errors=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
