# -*- coding: utf-8 -*-
"""全 MOD 离线模拟：判定链分类后，对「应送模型(send)」条目测术语覆盖与替换收益。

指标（每 MOD + 汇总）：
  send 条目数 / 字符数
  含 canon 硬层命中的条目占比（= 现行占位符强制管线覆盖面）
  含 names 层命中的条目占比（= 若把人物名也纳入强制的增量）
  含 reference 层命中的条目占比（= 若全词库就地替换的增量，含语境风险）
  硬层命中字符占 send 总字符比（占位符替换的输入缩减上限）
  硬层 span 覆盖 ≥90% 字符的条目数（可机械整译、无需模型的候选）
只读，不写盘（除本输出）。
"""
import contextlib
import glob
import importlib.util
import io
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter

PLUGIN = r"C:\STALKER-AiTranslate-2x\plugins\ai_translate\plugin.py"
spec = importlib.util.spec_from_file_location("m", PLUGIN)
m = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(m)
pkg = m._load_vendor()

merged, _set, _del, _clear = m.load_glossary_layers()
hard = m.load_hard_glossary_keys()
layers = m.load_term_layers()
names = set(layers["names"]) - hard
reference = set(layers.get("reference") or ()) - hard - names
idx = m.GlossaryIndex(merged, case_sensitive=set(layers["case_sensitive"]),
                      variants=dict(layers["variants"]))
QR = set(layers.get("quote_required") or ())

ZH = re.compile(r"[\u4e00-\u9fff]")
MODS = {
    "NS7": r"C:\game\NS7\gamedata\configs\text",
    "NS7-SQ基线": r"C:\game\CCCC\gamedata.sq_base_configs\configs\text",
    "NS219汉化": r"C:\game\NS\MODS\NS OGSR 2026年2月19日版本汉化补丁\gamedata\configs\text",
    "NS619汉化": r"C:\game\NS7\MODS\NS OGSR 2026年6月19日版本汉化补丁\gamedata\configs\text",
    "OP": r"C:\game\OP\gamedata\config\text",
    "OLR25": r"C:\game\STALKER-OLR25\gamedata\config\text",
    "疤脸归来2": r"C:\game\S.T.A.L.K.E.R\gamedata\config\text",
    "WOT汉化": r"C:\game\WOT\MODS\汉化\gamedata\configs\text",
    "GA汉化": r"C:\game\OGSR_GA\OGSR Gunslinger Addon\MODS\汉化\gamedata\config\text",
    "SoC1.0006原版": r"C:\game\OGSR-Engine\Game\Resources_SoC_1.0006\gamedata\config\text",
    "Anomaly-eng": r"C:\game\NS7-MT-TEST\extract\anomaly_full\configs\text\eng",
    "Anomaly-rus": r"C:\game\NS7-MT-TEST\extract\anomaly_full\configs\text\rus",
    "MT-CN": r"C:\game\ANTHOLOGY\Modpack-1.5.3-Anthology 2.1\mods\MT-CN\gamedata\configs\text",
    "Monolith-MT": r"C:\game\xray-monolith-mt\gamedata\configs\text",
    "百科大全": r"C:\game\ANTHOLOGY\Modpack-1.5.3-Anthology 2.1\mods\[AlzHighMr] 百科大全 — Anthology Encyclopedia\gamedata\configs\text",
    "IWP": r"C:\Users\Admin\Desktop\IWP\gamedata\configs\text\eng",
}


def ents(path):
    with contextlib.redirect_stdout(io.StringIO()):
        es = pkg.parse_xray_text_xml(
            path, list(m.read_encoding_chain(path))) or []
    out = {}
    for e in es:
        t = (e.texts or {}).get("text") or (e.texts or {}).get("rus") \
            or (e.texts or {}).get("eng")
        if e.id is not None and t:
            out[e.id] = t
    return out


def classify(rel, src):
    zh_n = len(ZH.findall(src))
    if zh_n * 5 > len(src):
        return "target"
    if m.looks_like_code_or_debug(rel, src):
        if not m.looks_like_hard_protected_file(rel) \
                and m._is_model_code_form(rel, src):
            mc = m.termbase_exact_translation(src)
            if mc is not None and mc != src:
                return "code_tb"
        return "code"
    hard_f = m.looks_like_hard_protected_file(rel)
    if not hard_f:
        ex = m.termbase_exact_translation(src)
        if ex is not None and ex != src:
            return "tb"
        pt = m.pattern_translate(src, m.termbase_exact_translation)
        if pt is not None:
            return "pat"
    if m.looks_like_generated_noise(rel, src):
        return "noise"
    if m.looks_like_technical_text(src):
        return "tech"
    if m.looks_like_identifier_text(src) and not m.is_player_facing_label(rel, src):
        return "ident"
    if m.looks_like_proper_text(rel, src):
        pp = m.partial_glossary_translation(src, rel)
        if pp is not None and pp != src:
            return "prop"
        return "proper"
    return "send"


tot = Counter()
rows = []
t0 = time.time()
for mod, root in MODS.items():
    st = Counter()
    if not os.path.isdir(root):
        continue
    for dp, _d, fs in os.walk(root):
        for fn in fs:
            if not fn.lower().endswith(".xml"):
                continue
            rel = "configs/text/" + os.path.relpath(
                os.path.join(dp, fn), root).replace("\\", "/")
            try:
                S = ents(os.path.join(dp, fn))
            except Exception:
                continue
            for k, v in S.items():
                cat = classify(rel, v)
                st[cat] += 1
                if cat != "send":
                    continue
                st["send_chars"] += len(v)
                spans = idx.find(v)
                h = n = r = 0
                for s, e2, key in spans:
                    if key in QR and '"' not in v[max(0, s - 2):e2 + 2] \
                            and "«" not in v[max(0, s - 2):e2 + 2]:
                        continue
                    if key in hard:
                        h += e2 - s
                    elif key in names:
                        n += e2 - s
                    elif key in reference:
                        r += e2 - s
                st["hard_hits_entries"] += 1 if h else 0
                st["name_hits_entries"] += 1 if n else 0
                st["ref_hits_entries"] += 1 if r else 0
                st["hard_chars"] += h
                st["name_chars"] += n
                st["ref_chars"] += r
                if h and h >= 0.9 * len(v):
                    st["full_cover"] += 1
    rows.append((mod, dict(st)))
    for k2, v2 in st.items():
        tot[k2] += v2

hdr = "%-14s %6s %7s %6s %6s %6s %6s %6s" % (
    "MOD", "send", "字符", "硬层覆盖%", "names覆盖%", "ref覆盖%", "硬字符占比%", "机械整译")
print(hdr)
for mod, st in rows:
    send = st.get("send", 0)
    if not send:
        continue
    print("%-14s %6d %7d %10.1f %10.1f %10.1f %12.1f %8d" % (
        mod, send, st.get("send_chars", 0),
        100.0 * st.get("hard_hits_entries", 0) / send,
        100.0 * st.get("name_hits_entries", 0) / send,
        100.0 * st.get("ref_hits_entries", 0) / send,
        100.0 * st.get("hard_chars", 0) / st.get("send_chars", 1),
        st.get("full_cover", 0)))
send = tot.get("send", 0)
print("%-14s %6d %7d %10.1f %10.1f %10.1f %12.1f %8d" % (
    "TOTAL", send, tot.get("send_chars", 0),
    100.0 * tot.get("hard_hits_entries", 0) / send,
    100.0 * tot.get("name_hits_entries", 0) / send,
    100.0 * tot.get("ref_hits_entries", 0) / send,
    100.0 * tot.get("hard_chars", 0) / tot.get("send_chars", 1),
    tot.get("full_cover", 0)))
print()
print("分类分布 TOTAL:", dict(tot))
print("耗时 %.1f s" % (time.time() - t0))
