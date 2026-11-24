# -*- coding: utf-8 -*-
"""2.0.28 交付硬门禁回归：零变化、中断一致性、token 与终检状态。"""

import atexit
import importlib.util
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import types

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = (sys.argv[1] if len(sys.argv) > 1 else
          os.path.join(ROOT, "plugins", "ai_translate", "plugin.py"))
ASSETS = os.path.join(os.path.dirname(PLUGIN), "assets")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ait28_local_")
atexit.register(shutil.rmtree, os.environ["LOCALAPPDATA"], ignore_errors=True)
COPY_ROOT = tempfile.mkdtemp(prefix="ait28_plugin_")
COPY_DIR = os.path.join(COPY_ROOT, "ai_translate")
os.makedirs(os.path.join(COPY_DIR, "assets"), exist_ok=True)
shutil.copy2(PLUGIN, os.path.join(COPY_DIR, "plugin.py"))
for name in ("xray_pkg.zip", "stalker_termbase.json", "glossary_builtin.json"):
    shutil.copy2(os.path.join(ASSETS, name),
                 os.path.join(COPY_DIR, "assets", name))
atexit.register(shutil.rmtree, COPY_ROOT, ignore_errors=True)
spec = importlib.util.spec_from_file_location(
    "ait28", os.path.join(COPY_DIR, "plugin.py"))
ait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ait)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       (" | " + str(detail)) if detail else ""))


class FakeServer:
    def __init__(self, answer=None, batch_answer=None, on_call=None):
        self.answer = answer or (lambda text, **kw: "译文：" + text)
        self.batch_answer = batch_answer
        self.on_call = on_call
        self.calls = []

    def translate(self, text, src, tgt, **kw):
        self.calls.append(text)
        if self.on_call:
            self.on_call()
        return self.answer(text, **kw)

    def translate_batch(self, lines, src, tgt, **kw):
        self.calls.append("|".join(lines))
        if self.on_call:
            self.on_call()
        if self.batch_answer:
            return self.batch_answer(lines)
        return "\n".join("[%d] 译文：%s" % (i + 1, line)
                         for i, line in enumerate(lines))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def table(path, entries):
    write(path, '<?xml version="1.0" encoding="utf-8"?>\n<string_table>'
          + "".join('<string id="%s"><text>%s</text></string>' % pair
                    for pair in entries) + "</string_table>")


def make_job(root, srv, mode="auto", **kw):
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(ind, exist_ok=True)
    options = dict(out_dir=out, reference_mode=False, copy_reference=False,
                   protect_proper=False, auto_session_terms=False,
                   freq_names=False, tm_lite=False, force_glossary=False,
                   log=lambda *a, **k: None)
    options.update(kw)
    job = ait.TranslationJob(ait._load_vendor(), srv, ind, mode,
                             "auto", "chs", **options)
    job._logs = []
    job.log = lambda msg, tag="info": job._logs.append((tag, str(msg)))
    return job, ind, out


def test_tokens_and_semantics():
    check("T1-1 token 计数保留重复次数",
          ait.runtime_token_counts("%s %s $name") ==
          ait.runtime_token_counts("%s %s $name") and
          not ait.runtime_tokens_match("%s %s", "%s"))
    check("T1-2 色码内容损坏必须拦截",
          not ait.runtime_tokens_match("%c[default]", "%c[默认]"))
    check("T1-3 第一人称付款方向反转可识别",
          ait.semantic_role_issues("I will pay you.", "你必须付给我。", "eng"))
    check("T1-4 相同付款方向不误报",
          not ait.semantic_role_issues("I will pay you.", "我会付给你。", "eng"))


def test_zero_change():
    root = tempfile.mkdtemp(prefix="ait28_zero_")
    srv = FakeServer(lambda text, **kw: text)
    job, ind, out = make_job(root, srv)
    rel = "configs/text/st_symbols.xml"
    table(os.path.join(ind, rel), [("a", "%c[default]"), ("b", "12345")])
    ok = job.run()
    check("T2-1 纯符号表不生成输出", not os.path.exists(os.path.join(out, rel)))
    check("T2-2 纯符号表不记账",
          rel.replace("\\", "/") not in job._ledger)
    check("T2-3 无需翻译不请求模型", not srv.calls)

    root2 = tempfile.mkdtemp(prefix="ait28_echo_")
    srv2 = FakeServer(lambda text, **kw: text)
    job2, ind2, out2 = make_job(root2, srv2)
    rel2 = "configs/text/st_dialogs.xml"
    table(os.path.join(ind2, rel2), [("a", "The stalker opened the door.")])
    ok2 = job2.run()
    check("T2-4 模型原样回显不生成输出",
          not os.path.exists(os.path.join(out2, rel2)))
    check("T2-5 原样回显计为失败且不能判完成",
          not ok2 and job2.failed_strings > 0 and
          not any(msg.startswith("全部完成") for _tag, msg in job2._logs),
          (ok2, job2.failed_strings, job2._logs))

    os.makedirs(os.path.dirname(os.path.join(out2, rel2)), exist_ok=True)
    shutil.copy2(os.path.join(ind2, rel2), os.path.join(out2, rel2))
    write(os.path.join(out2, ".ai_translate_done.json"), json.dumps({
        rel2: {"in_size": os.path.getsize(os.path.join(ind2, rel2)),
               "in_mtime_ns": os.stat(os.path.join(ind2, rel2)).st_mtime_ns,
               "out_size": os.path.getsize(os.path.join(out2, rel2))}}))
    srv3 = FakeServer(lambda text, **kw: text)
    job3, _ind3, out3 = make_job(root2, srv3)
    job3.run()
    check("T2-6 旧零变化输出和旧账本被清理",
          not os.path.exists(os.path.join(out3, rel2)) and
          rel2 not in job3._ledger and bool(srv3.calls))


def test_ledger_pipeline_and_rel_name():
    root = tempfile.mkdtemp(prefix="ait28_ledger_")
    srv = FakeServer()
    job, ind, out = make_job(root, srv)
    rel = "configs/text/a.xml"
    src = os.path.join(ind, rel)
    dst = os.path.join(out, rel)
    table(src, [("a", "Open the door.")])
    table(dst, [("a", "开门。")])
    job._ledger_present = True
    job._ledger = {rel: {"in_size": os.path.getsize(src),
                         "in_mtime_ns": os.stat(src).st_mtime_ns,
                         "in_hash": ait.sha256_file(src),
                         "out_size": os.path.getsize(dst),
                         "out_hash": ait.sha256_file(dst)}}
    check("T3-1 无 pipeline 的旧账本不能跳过",
          not job._should_skip(rel, src, dst, "text"))
    job._ledger[rel]["pipeline"] = "2.0.27"
    check("T3-2 旧版 pipeline 强制重译",
          not job._should_skip(rel, src, dst, "text"))
    job._ledger[rel]["pipeline"] = ait.PLUGIN_INFO["version"]
    check("T3-3 当前 pipeline 且指纹一致才跳过",
          job._should_skip(rel, src, dst, "text"))

    root2 = tempfile.mkdtemp(prefix="ait28_rel_")
    srv2 = FakeServer()
    job2, ind2, out2 = make_job(root2, srv2)
    rel2 = "configs/text/dialog_test.xml"
    src2 = os.path.join(ind2, rel2)
    table(src2, [("player_visible_name", "quest_item_123")])
    try:
        n = job2._run_text_file("dialog_test.xml", src2,
                                os.path.join(out2, rel2),
                                os.path.join(out2, "__temp__", rel2), {}, 1, 1)
        ok = n > 0
    except Exception as e:
        ok = False
        n = "%s: %s" % (type(e).__name__, e)
    check("T3-4 批量筛选使用已定义路径（rel NameError 回归）",
          ok, n)

    for mode in ("auto", "text"):
        root3 = tempfile.mkdtemp(prefix="ait28_exact_")
        srv3 = FakeServer()
        job3, ind3, out3 = make_job(root3, srv3, mode=mode)
        rel3 = "configs/text/units.xml"
        src3 = os.path.join(ind3, rel3)
        dst3 = os.path.join(out3, rel3)
        table(src3, [("unit", "ч.")])
        ok3 = job3.run()
        parsed3 = (ait._load_vendor().parse_xray_text_xml(dst3, ["utf-8"])
                   or []) if os.path.exists(dst3) else []
        value3 = (ait._load_vendor().getRecommendLangText(parsed3[0], "chs")[1]
                  if parsed3 else "")
        check("T3-5 %s 模式短技术词直取精确译名" % mode,
              ok3 and value3 == "小时" and not srv3.calls,
              "value=%r calls=%d" % (value3, len(srv3.calls)))


def test_non_text_and_cancel_flush():
    root = tempfile.mkdtemp(prefix="ait28_echo_ltx_")
    srv = FakeServer(lambda text, **kw: text)
    job, ind, out = make_job(root, srv, mode="ltx")
    write(os.path.join(ind, "a.ltx"), '[dialogs]\ndescr = "Open the door."\n')
    ok = job.run()
    defs = os.path.join(out, "___ltx__put_this_to_text_folder.xml")
    check("T4-1 非文本原样回显不生成 ID 或定义表",
          not ok and not os.path.exists(defs) and
          not os.path.exists(os.path.join(out, "a.ltx")))

    for mode, filename in (("auto", "a.ltx"), ("ltx", "a.ltx")):
        root2 = tempfile.mkdtemp(prefix="ait28_cancel_")
        cancel = threading.Event()
        srv2 = FakeServer()
        job2, ind2, out2 = make_job(root2, srv2, mode=mode,
                                    cancel_event=cancel)
        original_write = job2._write_atomic

        def cancel_after_body(path, writer):
            original_write(path, writer)
            if os.path.basename(path) == filename:
                cancel.set()

        job2._write_atomic = cancel_after_body
        write(os.path.join(ind2, filename),
              '[dialogs]\ndescr = "Open the door."\n'
              'description1 = "Close the window."\n')
        ok2 = job2.run()
        name = ("___auto_put_this_to_text_folder.xml" if mode == "auto"
                else "___ltx__put_this_to_text_folder.xml")
        path = os.path.join(out2, name)
        raw = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
        body = (open(os.path.join(out2, filename), encoding="utf-8").read()
                if os.path.isfile(os.path.join(out2, filename)) else "")
        refs = set(re.findall(r"sgtat_gen_text_\d+_\d+", body))
        defs_found = set(re.findall(r'id="(sgtat_gen_text_\d+_\d+)"', raw))
        check("T4-2 %s 取消后引用 ID 全部有定义" % mode,
              not ok2 and refs and refs <= defs_found,
              "refs=%d defs=%d" % (len(refs), len(defs_found)))

    root3 = tempfile.mkdtemp(prefix="ait28_lit_cancel_")
    cancel3 = threading.Event()
    srv3 = FakeServer()
    job3, ind3, out3 = make_job(root3, srv3, reference_mode=True,
                                translate_literals=True, cancel_event=cancel3)
    original_write3 = job3._write_atomic

    def cancel_after_literal(path, writer):
        original_write3(path, writer)
        if os.path.basename(path) == "a.script":
            cancel3.set()

    job3._write_atomic = cancel_after_literal
    table(os.path.join(ind3, "configs", "text", "main.xml"),
          [("a", "Open the door.")])
    write(os.path.join(ind3, "scripts", "a.script"),
          'amk.send_tip("Open the door.")\n'
          'amk.send_tip("Close the window.")\n')
    job3.run()
    lit = os.path.join(out3, "configs", "text", "sgtat_auto_texts.xml")
    raw = open(lit, encoding="utf-8").read() if os.path.isfile(lit) else ""
    refs = set()
    for base, _dirs, files in os.walk(out3):
        for fn in files:
            if fn.lower().endswith((".ltx", ".script", ".xml")):
                p = os.path.join(base, fn)
                if p != lit:
                    refs.update(re.findall(r"sgtat_gen_text_\d+_\d+",
                                           open(p, encoding="utf-8").read()))
    defs_found = set(re.findall(r'id="(sgtat_gen_text_\d+_\d+)"', raw))
    check("T4-3 字面层取消后引用 ID 全部有定义",
          refs and refs <= defs_found, "refs=%d defs=%d" %
          (len(refs), len(defs_found)))


def test_batch_token_and_final_audit():
    src = "Give me $$ACTION$$ now."
    srv = FakeServer(batch_answer=lambda lines: "[1] 给我 $$ACTION$$ 现在 %s")
    job, _ind, _out = make_job(tempfile.mkdtemp(prefix="ait28_batch_"), srv)
    batch = [{"id": "a", "src": src, "lang": "eng",
              "rel": "configs/text/dialog.xml"}]
    check("T5-1 批量还原后仍校验 token 全集",
          job._translate_dialogue_batch(batch) == {} and
          job.batch_stats["failed"] == 1)

    root = tempfile.mkdtemp(prefix="ait28_audit_")
    job, ind, out = make_job(root, FakeServer())
    rel = "configs/text/st_dialogs.xml"
    table(os.path.join(ind, rel), [
        ("good", "Open the door."),
        ("leak", "The stalker opened the door."),
        ("role", "I will pay you."),
        ("token", "Use %c[default] now."),
        ("lost", "Bring the artifact."),
    ])
    table(os.path.join(out, rel), [
        ("good", "开门。"),
        ("leak", "The stalker opened the door."),
        ("role", "你必须付给我。"),
        ("token", "现在使用 %c[默认]。"),
    ])
    job._ledger[rel] = {"out_size": 10}
    job._audit_tables = {rel}
    result = job._final_audit(out)
    details = result.get("details", {})
    check("T5-2 终检返回硬门禁结构",
          result.get("ok") is False and result.get("hard_problems", 0) >= 4)
    check("T5-3 英文漏译/语义方向/token 损坏全部点名",
          bool(details.get("untranslated_entries")) and
          bool(details.get("semantic_role_conflicts")) and
          bool(details.get("runtime_token_damage")), details)
    check("T5-4 问题文件撤销账本但保留输出",
          rel not in job._ledger and os.path.isfile(os.path.join(out, rel)))

    table(os.path.join(out, rel), [("good", "开门。")])
    job._ledger = {}
    write(os.path.join(out, "configs", "misc", "ref.ltx"),
          'description = "sgtat_gen_text_1_0"\n')
    result2 = job._final_audit(out)
    check("T5-5 悬空生成 ID 阻断完成",
          not result2["ok"] and
          bool(result2["details"].get("dangling_generated_ids")))


def test_adversarial_model_gates():
    check("T6-1 占位符换序必须拦截",
          not ait.runtime_tokens_match("%s $name", "$name %s"))
    check("T6-2 相同占位符原序通过",
          ait.runtime_tokens_match("%s $name %s", "%s $name %s"))
    check("T6-3 千分位逗号数字保真",
          not ait.missing_big_numbers("Reward: 30000 rubles.",
                                      "报酬：30,000卢布。"))
    check("T6-4 错误千分位金额仍拦截",
          bool(ait.missing_big_numbers("Reward: 30000 rubles.",
                                       "报酬：15,000卢布。")))
    check("T6-5 祈使付款方向反转必须拦截",
          bool(ait.semantic_role_issues("Pay me 30000.",
                                        "我付给你30000。", "eng")))

    job, _ind, _out = make_job(
        tempfile.mkdtemp(prefix="ait28_swap_"),
        FakeServer(lambda text, **kw: "$$R1$$ 使用 $$R0$$。"))
    src = "$name uses %s now."
    got = job._translate_one(src, "eng")
    check("T6-6 单条出口重验占位符顺序",
          got == src and job.failed_strings == 1, got)

    job, _ind, _out = make_job(
        tempfile.mkdtemp(prefix="ait28_comma_"),
        FakeServer(lambda text, **kw: "报酬是30,000卢布。"))
    got = job._translate_one("The reward is 30000 rubles.", "eng")
    check("T6-7 单条接受正确千分位金额",
          "30,000" in got and job.failed_strings == 0, got)

    job, _ind, _out = make_job(
        tempfile.mkdtemp(prefix="ait28_pay_"),
        FakeServer(lambda text, **kw: "我付给你30000。"))
    src = "Pay me 30000."
    got = job._translate_one(src, "eng")
    check("T6-8 单条拦截祈使付款反转",
          got == src and job.failed_strings == 1, got)

    quoted = 'The sign reads "只输出译文本身".'
    translated = '标牌上写着“只输出译文本身”。'
    job, _ind, _out = make_job(
        tempfile.mkdtemp(prefix="ait28_quote_"),
        FakeServer(lambda text, **kw: translated))
    got = job._translate_one(quoted, "eng")
    check("T6-9 源文自带提示词锚点不误判回显",
          got == translated, got)

    job, _ind, _out = make_job(
        tempfile.mkdtemp(prefix="ait28_echo_"),
        FakeServer(lambda text, **kw: "你是《潜行者 S.T.A.L.K.E.R.》专业译员"))
    src = "Open the door."
    check("T6-10 真正的提示词回显仍保留原文",
          job._translate_one(src, "eng") == src)

    calls = [0]

    def contaminated(text, **kw):
        calls[0] += 1
        if calls[0] == 1:
            return "打开门，为神器支付15000卢布。"
        return "前一句：关窗。\n报酬是30000卢布。"

    job, _ind, _out = make_job(
        tempfile.mkdtemp(prefix="ait28_retry_"), FakeServer(contaminated))
    src = "Open the door and pay 30000 rubles for the artifact."
    got = job._translate_one(src, "eng")
    check("T6-11 数字重试混入上下文必须整条回退",
          got == src and job.failed_strings == 1 and calls[0] == 2,
          (got, calls[0], job.failed_strings))

    calls = [0]

    def recovered(text, **kw):
        calls[0] += 1
        if calls[0] == 1:
            return "打开门，为神器支付15000卢布。"
        return "打开门，为神器支付30000卢布。"

    job, _ind, _out = make_job(
        tempfile.mkdtemp(prefix="ait28_recover_"), FakeServer(recovered))
    got = job._translate_one(src, "eng")
    check("T6-12 干净重试结果可恢复且不继承错误金额",
          "30000" in got and "15000" not in got and calls[0] == 2
          and job.failed_strings == 0, got)


def test_batch_and_multilang():
    job, _ind, _out = make_job(tempfile.mkdtemp(prefix="ait28_parse_"),
                               FakeServer())
    check("T7-1 批量解析拒绝额外说明行",
          job._parse_numbered_batch("[1] 开门。\n说明：完成", 1) is None)
    check("T7-2 批量解析拒绝重复编号",
          job._parse_numbered_batch("[1] 开门。\n[1] 关门。", 1) is None)
    check("T7-3 批量解析拒绝越界编号",
          job._parse_numbered_batch("[1] 开门。\n[2] 关门。", 1) is None)

    srv = FakeServer(batch_answer=lambda lines: (
        "[1] 开门。\n[2] 关门。\n说明：完成"))
    job, _ind, _out = make_job(tempfile.mkdtemp(prefix="ait28_batch_extra_"),
                               srv)
    batch = [
        {"id": "a", "src": "Open the door.", "lang": "eng",
         "rel": "configs/text/dialog.xml"},
        {"id": "b", "src": "Close the window.", "lang": "eng",
         "rel": "configs/text/dialog.xml"},
    ]
    check("T7-4 批量翻译拒绝模型附加说明",
          job._translate_dialogue_batch(batch) == {}
          and job.batch_stats["failed"] == 1)

    root = tempfile.mkdtemp(prefix="ait28_fra_")
    srv = FakeServer()
    job, ind, out = make_job(root, srv)
    job.src_lang = "fra"
    rel = "configs/text/fra.xml"
    write(os.path.join(ind, rel),
          '<string id="a"><fra>Bonjour.</fra><eng>Hello.</eng></string>')
    ok = job.run()
    raw = (open(os.path.join(out, rel), encoding="utf-8").read()
           if os.path.isfile(os.path.join(out, rel)) else "")
    check("T7-5 非标准语言标签原地翻译且不新增 text 标签",
          ok and "<fra>译文：Bonjour.</fra>" in raw and "<text>" not in raw,
          raw)


def test_write_failures_and_ledger():
    root = tempfile.mkdtemp(prefix="ait28_write_ltx_")
    srv = FakeServer()
    job, ind, out = make_job(root, srv)
    write(os.path.join(ind, "a.ltx"), '[dialogs]\ndescr = "Open the door."\n')
    original_write = job._write_atomic

    def fail_body(path, writer):
        if os.path.basename(path) == "a.ltx":
            raise OSError("injected body write failure")
        original_write(path, writer)

    job._write_atomic = fail_body
    ok = job.run()
    generated = []
    for base, _dirs, files in os.walk(out):
        for fn in files:
            if fn.lower().endswith(".xml"):
                generated.extend(re.findall(
                    r"sgtat_gen_text_\d+_\d+",
                    open(os.path.join(base, fn), encoding="utf-8").read()))
    check("T8-1 正文写盘失败不留下孤儿生成 ID",
          not ok and not os.path.exists(os.path.join(out, "a.ltx"))
          and not generated, generated)

    root2 = tempfile.mkdtemp(prefix="ait28_write_table_")
    srv2 = FakeServer()
    job2, ind2, out2 = make_job(root2, srv2)
    rel2 = "configs/text/a.xml"
    src2 = os.path.join(ind2, rel2)
    dst2 = os.path.join(out2, rel2)
    table(src2, [("a", "Open the door.")])
    check("T8-2 首次写盘成功建立账本", job2.run() and rel2 in job2._ledger)
    old_output = open(dst2, "rb").read()
    table(src2, [("a", "Open the door."), ("b", "Close the window.")])
    srv3 = FakeServer()
    job3, _ind3, _out3 = make_job(root2, srv3)
    original_write3 = job3._write_atomic

    def fail_table(path, writer):
        if os.path.normcase(path) == os.path.normcase(dst2):
            raise OSError("injected table write failure")
        original_write3(path, writer)

    job3._write_atomic = fail_table
    ok3 = job3.run()
    check("T8-3 写盘失败保留旧成品但撤销完成账本",
          not ok3 and open(dst2, "rb").read() == old_output
          and rel2 not in job3._ledger and bool(srv3.calls),
          (ok3, rel2 in job3._ledger, srv3.calls))

    root4 = tempfile.mkdtemp(prefix="ait28_tamper_")
    srv4 = FakeServer()
    job4, ind4, out4 = make_job(root4, srv4)
    rel4 = "configs/text/a.xml"
    src4 = os.path.join(ind4, rel4)
    dst4 = os.path.join(out4, rel4)
    table(src4, [("a", "Open the door.")])
    check("T8-4 哈希账本首次建立", job4.run())
    stat = os.stat(dst4)
    raw = open(dst4, "rb").read()
    open(dst4, "wb").write(raw.replace("译文".encode("utf-8"),
                                       "损坏".encode("utf-8")))
    os.utime(dst4, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    srv5 = FakeServer()
    job5, _ind5, _out5 = make_job(root4, srv5)
    job5._ledger_path = os.path.join(out4, ".ai_translate_done.json")
    job5._ledger_present = True
    job5._ledger = job5._load_ledger()
    check("T8-5 同尺寸同时间输出篡改仍强制重译",
          job5._should_skip(rel4, src4, dst4, "text") is False)
    ok5 = job5.run()
    check("T8-6 重译恢复被篡改输出",
          ok5 and "损坏" not in open(dst4, encoding="utf-8").read()
          and bool(srv5.calls))


def test_generated_recovery_and_reference_failure():
    root = tempfile.mkdtemp(prefix="ait28_gen_recover_")
    srv = FakeServer()
    job, ind, out = make_job(root, srv)
    write(os.path.join(ind, "a.ltx"), '[dialogs]\ndescr = "Open the door."\n')
    check("T9-1 首次生成定义表", job.run())
    table_path = os.path.join(out, "___auto_put_this_to_text_folder.xml")
    raw = open(table_path, encoding="utf-8").read()
    check("T9-2 生成表包含可破坏译文", "译文" in raw)
    open(table_path, "w", encoding="utf-8", newline="\n").write(
        raw.replace("译文", "损坏"))

    srv2 = FakeServer()
    job2, _ind2, _out2 = make_job(root, srv2)
    ok2 = job2.run()
    raw2 = open(table_path, encoding="utf-8").read()
    body = open(os.path.join(out, "a.ltx"), encoding="utf-8").read()
    refs = set(re.findall(r"sgtat_gen_text_\d+_\d+", body))
    defs = set(re.findall(r'id="(sgtat_gen_text_\d+_\d+)"', raw2))
    check("T9-3 损坏生成表不合并旧定义且正文重译",
          ok2 and "损坏" not in raw2 and refs and refs <= defs
          and bool(srv2.calls), (ok2, refs, defs, srv2.calls))

    srv3 = FakeServer()
    job3, _ind3, _out3 = make_job(root, srv3)
    ok3 = job3.run()
    check("T9-4 有效生成表与正文可无损跳过",
          ok3 and not srv3.calls and job3._ledger)

    root2 = tempfile.mkdtemp(prefix="ait28_ref_copy_")
    srv4 = FakeServer()
    job4, ind2, out2 = make_job(root2, srv4, reference_mode=True,
                                copy_reference=True, only_referenced=False)
    table(os.path.join(ind2, "configs", "text", "a.xml"),
          [("a", "Open the door.")])
    write(os.path.join(ind2, "scripts", "a.script"),
          'translate_string("a")\n')

    def fail_copy(_src, _dst):
        raise OSError("injected reference copy failure")

    job4._copy_file = fail_copy
    ok4 = job4.run()
    check("T9-5 引用层复制失败不能判完成",
          not ok4 and job4.failed_strings > 0,
          (ok4, job4.failed_strings, job4._logs))


def test_audit_review_paths_and_concurrency():
    root = tempfile.mkdtemp(prefix="ait28_partial_")
    job, ind, out = make_job(root, FakeServer())
    rel = "configs/text/a.xml"
    table(os.path.join(ind, rel), [("a", "Hello Привет.")])
    table(os.path.join(out, rel), [("a", "Hello 甲。")])
    job._audit_tables = {rel}
    result = job._final_audit(out)
    check("T10-1 部分源文残留进入人工复核而非硬失败",
          result["ok"] and result["warnings"] >= 1
          and bool(result["details"].get("partial_translation_review")),
          result)

    job.gloss = ait.GlossaryIndex({"artifact": "神器"}, case_sensitive=set())
    job.glossary = {"artifact": "神器"}
    job.gloss_hard = {"artifact"}
    table(os.path.join(ind, rel), [("a", "artifact artifact")])
    table(os.path.join(out, rel), [("a", "神器")])
    result = job._final_audit(out)
    check("T10-2 硬术语重复次数不足必须拦截",
          not result["ok"]
          and bool(result["details"].get("glossary_conflicts")), result)

    root2 = tempfile.mkdtemp(prefix="ait28_same_dir_")
    srv2 = FakeServer()
    job2, ind2, _out2 = make_job(root2, srv2, out_dir=os.path.join(root2, "in"))
    check("T10-3 输出等于输入时拒绝启动",
          job2.run() is False and not srv2.calls)

    root3 = tempfile.mkdtemp(prefix="ait28_ledger_fail_")
    srv3 = FakeServer()
    job3, ind3, _out3 = make_job(root3, srv3)
    table(os.path.join(ind3, "configs", "text", "a.xml"),
          [("a", "Open the door.")])

    def fail_ledger():
        job3._ledger_save_failed = True

    job3._save_ledger = fail_ledger
    ok3 = job3.run()
    check("T10-4 账本持久化失败不能判全部完成",
          not ok3 and not any(msg.startswith("全部完成")
                              for _tag, msg in job3._logs))

    root4 = tempfile.mkdtemp(prefix="ait28_lock_")
    entered = threading.Event()
    release = threading.Event()

    def blocking_answer(text, **kw):
        entered.set()
        release.wait(5)
        return "译文：" + text

    srv4 = FakeServer(blocking_answer)
    job4, ind4, _out4 = make_job(root4, srv4)
    table(os.path.join(ind4, "configs", "text", "a.xml"),
          [("a", "Open the door.")])
    first_result = []

    def run_first():
        first_result.append(job4.run())

    thread = threading.Thread(target=run_first)
    thread.start()
    check("T10-5 并发测试首任务已进入模型边界", entered.wait(5))
    srv5 = FakeServer()
    job5, _ind5, _out5 = make_job(root4, srv5)
    second_ok = job5.run()
    release.set()
    thread.join(5)
    check("T10-6 同一输出目录并发任务被互斥拒绝",
          second_ok is False and not srv5.calls and first_result == [True],
          (second_ok, srv5.calls, first_result))

    job6, _ind6, _out6 = make_job(tempfile.mkdtemp(prefix="ait28_atomic_"),
                                  FakeServer())
    atomic_path = os.path.join(job6.out_dir, "atomic.bin")
    payloads = [("payload-%02d-" % i + "x" * 10000).encode("ascii")
                for i in range(16)]
    barrier = threading.Barrier(len(payloads))
    errors = []

    def write_payload(data):
        try:
            barrier.wait(5)
            job6._write_atomic(
                atomic_path, lambda tmp: open(tmp, "wb").write(data))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=write_payload, args=(data,))
               for data in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    final_data = (open(atomic_path, "rb").read()
                  if os.path.isfile(atomic_path) else b"")
    leftovers = [fn for fn in os.listdir(job6.out_dir) if fn.endswith(".tmp")]
    check("T10-7 并发原子写不产生半文件或临时文件冲突",
          not errors and final_data in payloads and not leftovers,
          (errors, len(final_data), leftovers))


test_tokens_and_semantics()
test_zero_change()
test_ledger_pipeline_and_rel_name()
test_non_text_and_cancel_flush()
test_batch_token_and_final_audit()
test_adversarial_model_gates()
test_batch_and_multilang()
test_write_failures_and_ledger()
test_generated_recovery_and_reference_failure()
test_audit_review_paths_and_concurrency()
fails = [name for name, ok in RESULTS if not ok]
print("\n总计 %d 项，失败 %d 项" % (len(RESULTS), len(fails)))
for name in fails:
    print("  -", name)
sys.exit(1 if fails else 0)
