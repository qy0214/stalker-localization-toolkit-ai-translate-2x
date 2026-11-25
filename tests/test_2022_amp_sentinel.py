# -*- coding: utf-8 -*-
"""2.0.22 终检 & 保真哨兵回归：源含 & 的条目译文吞 &（塌成双空格）被点名。

背景：CHANGELOG 2.0.22 记录第三方产物有 `Heckler & Koch` → `Heckler  Koch`
的静默吞 &。本插件解析/写回链路（normalize_xml_string 转义裸 & +
generateOutputXml 再转义）已验证往返保真，残余风险只剩模型译文吞 &；
终检新增第 ⑥ 层哨兵：源含 & 且译文出现源没有的双空格 → 点名，不自动改写。

用法: python tests/test_2022_amp_sentinel.py [plugin.py 路径]
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

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="aitamp_local_")
_ORIG_PLUGIN = PLUGIN
_TMP = tempfile.mkdtemp(prefix="aitamp_plugin_")
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

spec = importlib.util.spec_from_file_location("aitamp", PLUGIN)
ait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ait)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + str(detail)) if detail else ""))


class FakeServer:
    def is_running(self):
        return True

    def translate(self, text, src, tgt, **kw):
        return "[译]" + text[:20]

    def translate_batch(self, lines, src, tgt, **kw):
        return "\n".join("[%d] [译]%s" % (i + 1, t)
                         for i, t in enumerate(lines))


REL = "configs/text/character_name/stable_sms_character_name.xml"


def make_job(root):
    """终检测试专用 job：输入/输出目录就绪，登记一张改写表。"""
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs"), exist_ok=True)
    os.makedirs(out, exist_ok=True)
    with io.open(os.path.join(ind, "configs", "localization.ltx"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("[string_table]\nlanguage = rus\nfiles = stable_sms_character_name\n")
    job = ait.TranslationJob(ait._load_vendor(), FakeServer(),
                             ind, "auto", "auto", "chs",
                             out_dir=out, log=lambda *a, **k: None,
                             reference_mode=True, copy_reference=True,
                             protect_proper=True, auto_session_terms=False,
                             freq_names=False, tm_lite=False,
                             force_glossary=False)
    return job, ind, out


def write_pair(root, in_entries, out_entries, enc="windows-1251"):
    """输入表（enc 编码）+ 输出表（utf-8，保持 <rus> 槽结构）。"""
    def xml_of(entries, decl):
        body = "".join('<string id="%s"><rus>%s</rus></string>' % (i, t)
                       for i, t in entries)
        return '%s\n<string_table>%s</string_table>' % (decl, body)
    ind = os.path.join(root, "in")
    sub = os.path.join(ind, "configs", "text", "character_name")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "stable_sms_character_name.xml"), "wb") as f:
        f.write(xml_of(in_entries, '<?xml version="1.0" encoding="%s"?>'
                       % enc).encode(enc))
    osub = os.path.join(root, "out", "configs", "text", "character_name")
    os.makedirs(osub, exist_ok=True)
    op = os.path.join(osub, "stable_sms_character_name.xml")
    with io.open(op, "w", encoding="utf-8", newline="\n") as f:
        f.write(xml_of(out_entries, '<?xml version="1.0" encoding="utf-8"?>'))
    return op


def run_audit(root):
    job, ind, out = make_job(root)
    job._audit_tables = {REL}
    logs = []
    job.log = lambda m, tag="info": logs.append((tag, str(m)))
    job._final_audit(out)
    return "\n".join(m for _t, m in logs)


def t1_swallow_flagged():
    print("\n--- T1 吞 & 特征（& 塌成双空格）被点名 ---")
    root = tempfile.mkdtemp(prefix="aitamp_swallow_")
    try:
        write_pair(root,
                   [("a1", "Пистолет Heckler &amp; Koch Mk23"),
                    ("a2", "Обычная запись без амперсанда")],
                   [("a1", "黑克勒  科赫 Mk23 手枪"),
                    ("a2", "普通条目译文")])
        joined = run_audit(root)
        check("T1-吞 & 被点名", "疑似吞 &" in joined, joined[:200])
        check("T1-点名带表名与条目 id",
              "stable_sms_character_name.xml" in joined and "a1" in joined,
              joined[:200])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t2_legit_not_flagged():
    print("\n--- T2 正常翻译不误报 ---")
    root = tempfile.mkdtemp(prefix="aitamp_legit_")
    try:
        write_pair(root,
                   [("b1", "Пистолет Heckler &amp; Koch Mk23"),
                    ("b2", "Пистолет Heckler &amp; Koch Mk23")],
                   [("b1", "Heckler & Koch Mk23 手枪"),   # & 保留
                    ("b2", "黑克勒-科赫 Mk23 手枪")])       # 专名全译，& 消失
        joined = run_audit(root)
        check("T2-& 保留不点名", "疑似吞 &" not in joined, joined[:200])
        check("T2-专名全译（& 消失、无双空格）不点名",
              "疑似吞 &" not in joined, joined[:200])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t3_no_amp_source_not_flagged():
    print("\n--- T3 源无 & 的译文双空格不由本哨兵负责 ---")
    root = tempfile.mkdtemp(prefix="aitamp_noamp_")
    try:
        write_pair(root,
                   [("c1", "Обычная запись без амперсанда")],
                   [("c1", "带  双空格的译文")])
        joined = run_audit(root)
        check("T3-源无 & 不点名吞 &", "疑似吞 &" not in joined, joined[:200])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t4_untranslated_not_flagged():
    print("\n--- T4 未翻译条目（输出=源文，含 &）不点名 ---")
    root = tempfile.mkdtemp(prefix="aitamp_untrans_")
    try:
        # 全拉丁源文：不触发「条目未翻译」（那是西里尔口径）
        write_pair(root,
                   [("d1", "Made by Heckler & Koch")],
                   [("d1", "Made by Heckler & Koch")])
        joined = run_audit(root)
        check("T4-未翻译条目不点名吞 &",
              "疑似吞 &" not in joined, joined[:200])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t5_roundtrip_fidelity():
    print("\n--- T5 解析/写回往返保真（裸 & 与 &amp; 都不丢）---")
    pkg = ait._load_vendor()
    src = os.path.join(tempfile.mkdtemp(prefix="aitamp_rt_"), "t.xml")
    with io.open(src, "w", encoding="cp1251", newline="\n") as f:
        f.write('<?xml version="1.0" encoding="windows-1251"?>\n'
                "<string_table>"
                '<string id="r1"><rus>Пистолет Heckler &amp; Koch Mk23</rus></string>'
                '<string id="r2"><rus>Энциклопедия S.T.A.L.K.E.R. & N</rus></string>'
                "</string_table>")
    with io.open(os.devnull, "w") as devnull:
        old = sys.stdout
        try:
            sys.stdout = devnull
            ents = pkg.parse_xray_text_xml(src, ["cp1251"]) or []
        finally:
            sys.stdout = old
    texts = {}
    for e in ents:
        t = (e.texts or {}).get("rus")
        if t:
            texts[e.id] = t
    check("T5-裸 & 与 &amp; 解析后都在",
          "&" in texts.get("r1", "") and "&" in texts.get("r2", ""),
          repr(texts))
    out = src + ".out.xml"
    pkg.xmlFileGenerator.generateOutputXml(out, texts, splitLength=0)
    with io.open(os.devnull, "w") as devnull:
        old = sys.stdout
        try:
            sys.stdout = devnull
            back = pkg.parse_xray_text_xml(out, ["utf-8"]) or []
        finally:
            sys.stdout = old
    btexts = {}
    for e in back:
        t = (e.texts or {}).get("text") or (e.texts or {}).get("rus")
        if t:
            btexts[e.id] = t
    check("T5-写回再读 id 集合一致", set(texts) == set(btexts),
          "%s / %s" % (sorted(texts), sorted(btexts)))
    check("T5-写回再读 & 全保真",
          all("&" in btexts[k] for k in texts if "&" in texts[k]),
          repr(btexts))


def main():
    t1_swallow_flagged()
    t2_legit_not_flagged()
    t3_no_amp_source_not_flagged()
    t4_untranslated_not_flagged()
    t5_roundtrip_fidelity()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n总计 %d 项：通过 %d，失败 %d" %
          (len(RESULTS), passed, len(RESULTS) - passed))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
