# -*- coding: utf-8 -*-
"""2.0.25 回归：aera/CHS 全量对照驱动的判定链与数据修复。

覆盖：_id_player_visible 豁免（ident 假跳过 2206 条）、reconcile 0→≤2
收敛（lit_nl 11 条）、_translate_force 长文跳过占位符法（D12 签名①）、
上下文串入切片级兜底（D12 签名②）、人工对照层词库命中（神器英文键/
弹药状态/单名）、口语化提示词锚例。
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location(
    "m", os.path.join(ROOT, "plugins", "ai_translate", "plugin.py"))
ait = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(ait)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + str(detail)) if detail else ""))


class FakeServer:
    def __init__(self, script=None):
        self.script = script or {}
        self.batch_lines = None

    def is_running(self):
        return True

    def translate(self, text, src, tgt, **kw):
        # 先按文本内容分派（供上下文/切片多级重试区分响应）
        for key, val in self.script.items():
            if key and key in text:
                return val() if callable(val) else val
        note = kw.get("extra_sys") or ""
        for key, val in self.script.items():
            if key and key in note:
                return val() if callable(val) else val
        if "" in self.script:
            val = self.script[""]
            return val() if callable(val) else val
        return text

    def translate_batch(self, lines, src, tgt, **kw):
        return "\n".join("[%d] %s" % (i + 1, t)
                         for i, t in enumerate(lines))


def make_job(srv, **kw):
    root = tempfile.mkdtemp(prefix="ait2025_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(ind)
    os.makedirs(out)
    defaults = dict(out_dir=out, log=lambda *a, **k: None,
                    reference_mode=False, copy_reference=False,
                    protect_proper=True, auto_session_terms=False,
                    freq_names=False, tm_lite=False,
                    force_glossary=False, glossary={})
    defaults.update(kw)
    job = ait.TranslationJob(ait._load_vendor(), srv, ind, "auto",
                             "auto", "chs", **defaults)
    job._tmp_root = root
    return job


# ── 1 版本 ──────────────────────────────────────────────────────
check("version:2.0.25+",
      ait.PLUGIN_INFO["version"] in ("2.0.25", "2.0.26"),
      ait.PLUGIN_INFO["version"])

# ── 2 id 形态豁免（P3/P5）──────────────────────────────────────
check("豁免-_name 后缀", ait._id_player_visible("st_itm_upgr_flash_disk_alloy_name"))
check("豁免-_descr 后缀", ait._id_player_visible("st_af_camelbak_descr"))
check("豁免-_text 后缀", ait._id_player_visible("encyclopedia_locations_Bar_text"))
check("豁免-task_name 后缀", ait._id_player_visible("drx_sl_baraholka_trader_meet_task_1_name"))
check("不豁免-纯标识 id", not ait._id_player_visible("st_dyn_news_comm_dolg_5"))
check("不豁免-弹药状态 id", not ait._id_player_visible("ammo-12x70-buck_bad"))
check("不豁免-空 id", not ait._id_player_visible(""))

# ── 3 reconcile 0→≤2 收敛（lit_nl）────────────────────────────
extra, fixed = ait.reconcile_literal_newlines("源无换行", "译A\\n译B")
check("收敛-两个多余\\n变空格",
      fixed and ait.count_literal_newlines(extra) == 0 and "译A 译B" == extra,
      repr(extra))
extra2, fixed2 = ait.reconcile_literal_newlines("源无换行", "译A\\n译B\\n译C\\n译D")
check("收敛-超过两个不处理", not fixed2)
same, fixed3 = ait.reconcile_literal_newlines("带\\n源文", "带\\n源文译")
check("收敛-数量一致不动", not fixed3)

# ── 4 人工对照层词库命中（P1/P2/P4）───────────────────────────
for src, want in (
        ("«Spike»", "尖刺"),
        ("«Cocoon»", "茧"),
        ("12x70 Buckshot (wet)", "12x70 霰弹（受潮的）"),
        ("12.7x108mm FMJ Rounds (bad)", "12.7x108mm FMJ 弹药（劣质的）"),
        ("Flash drive with alloys data", "载有合金数据的闪存盘"),
        ("Magpie", "喜鹊"),
        ("Vicarius", "维卡里乌斯"),
        ("Eugene Paton", "尤金·帕通"),
):
    got = ait.termbase_exact_translation(src)
    check("词库 %r" % src, got == want, got)
check("词库-人工对照层挂 names",
      "人工对照" in ait.load_termbase_data()["_meta"]["layers"]["names"])

# ── 5 长文多术语跳过占位符法（D12 签名①）──────────────────────
# ★ 修正：旧夹具源文无词库命中（spans=0），根本没进 _translate_force，
# "$$T" not in out 是假通过。现在源文含词库术语（spans>0）走占位符法
# 前置检查，捕获真实请求断言全部无 $$T（= 走了 len>=600 分段链）。
long_text = ("General information about the Whirligig anomaly. " * 18)
seen_calls = []


class CapServer(FakeServer):
    def translate(self, text, src, tgt, **kw):
        seen_calls.append(str(text))
        return "（普通整译产出）" + str(text)[:20]


job = make_job(CapServer(), force_glossary=True,
               glossary={"Whirligig": "旋转木马"})
check("长文-词库命中就位", bool(job._glossary_spans(long_text)),
      str(len(job._glossary_spans(long_text))))
out = job._translate_plain(long_text, "eng")
check("长文-不走 $$T$$ 占位符",
      seen_calls and all("$$T" not in c for c in seen_calls),
      str(seen_calls[0])[:60] if seen_calls else "无请求")
shutil.rmtree(job._tmp_root, ignore_errors=True)

# ── 6 上下文串入→切片兜底（D12 签名②）────────────────────────
# ★ 修正：旧夹具按文本分派让主翻译直接拿到好译文，串入三级路径
# 完全没执行（单请求假通过）。现在按请求形态分派：带上下文的主请求
# 返回串入污染 → 清上下文整条重译仍串入 → 切片片段请求返回好译文。
SRC6 = ("Short clean sentence about the zone and its anomalies. " * 4)


class BleedServer(FakeServer):
    def translate(self, text, src, tgt, **kw):
        if kw.get("context"):
            return "前一句：上一条对话的串入译文"          # 主请求
        if str(text).strip() == SRC6.strip():
            return "前一句：去上下文重译仍串入"            # 整条无上下文
        return "正常的好译文。"                            # 切片片段


job = make_job(BleedServer())
out = job._translate_one(SRC6, "eng",
                         context="前一句：上一条对话", style="dialogue")
check("上下文-串入后切片救回",
      "前一句" not in str(out) and "正常的好译文" in str(out),
      str(out)[:60])
shutil.rmtree(job._tmp_root, ignore_errors=True)

# ── 7 提示词锚例（P6）─────────────────────────────────────────
check("提示词-描述文本锚例存在",
      "低剂量辐射" in ait.TRANSLATION_SYS_PROMPT_EN
      and "概不负责" in ait.TRANSLATION_SYS_PROMPT)

# ── 汇总 ──────────────────────────────────────────────────────
fails = [n for n, ok, _ in RESULTS if not ok]
print("\n%d/%d 通过" % (len(RESULTS) - len(fails), len(RESULTS)))
if fails:
    print("失败:", fails)
    sys.exit(1)
