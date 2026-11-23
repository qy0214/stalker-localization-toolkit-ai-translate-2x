# -*- coding: utf-8 -*-
"""IWP 已翻译产物全量深度审计（v2.0.23 起，覆盖全部切片，非抽样）。

切片：configs/text/eng、configs/text/rus、configs/gameplay（character_desc）、
      configs/ui、scripts/*.script。
判定链复用插件本体（parse_xray_text_xml + read_encoding_chain + looks_like_*），
保证审计口径与产出链路一致。

条目级检查（源↔译逐条对照，仅"应译"判定类）：
  miss_id      源有译无（丢条目）              BLOCK
  empty        译文为空                        BLOCK
  untranslated 应译未译（输出==源文）          BLOCK
  note_leak    重试指令注记泄漏（原文…）        BLOCK
  amp_lost     源含 & 译文丢失（保真哨兵）      BLOCK
  ph           占位符集合不一致                BLOCK
  lit_nl       字面 \\n 数量不一致              BLOCK
  tag          [方括号]/<标签> token 不一致     BLOCK
  cyr_residue  译文残留西里尔                  BLOCK
  latin_residue 译文残留英文长词（非型号/词库）  WARN
  num_lost     源数字（≥2位）译文缺失           WARN
  trunc        译文长度 < 源 50%（源>300字）     WARN

文件级：parse_fail（解析失败）BLOCK；copied（与源字节相同=引用层原样复制）跳过；
        zh_rate（应译条目中文率）<95% WARN。
脚本级（.script）：Lua 字符串字面量对照——条数、注记泄漏、西里尔残留、占位符。

输出：audit-reports/IWP_FULL_AUDIT_<stamp>.json + 控制台摘要。只读，不改产物。
"""
import contextlib
import glob
import importlib.util
import io
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugins", "ai_translate", "plugin.py")
# 用法：python tools/audit_iwp_full.py [gamedata 目录] [产出子目录名]
# 默认 IWP；对任意 MOD 产出可传参（如 .../aera/gamedata）。
GAME = (sys.argv[1] if len(sys.argv) > 1
        else r"C:\Users\Admin\Desktop\IWP\gamedata")
OUT = os.path.join(GAME, (sys.argv[2] if len(sys.argv) > 2
                          else "translated_hy_mt2"))

spec = importlib.util.spec_from_file_location("m", PLUGIN)
m = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(m)
pkg = m._load_vendor()

ZH = re.compile(r"[\u4e00-\u9fff]")
CYR = re.compile(r"[А-Яа-яЁё]")
PH = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*|%[-\d.]*[sdifuc]|%c\[[^\]]+\]")
TAG = re.compile(r"\[[^\[\]\n]{1,40}\]|</?[a-zA-Z_][^>\n]{0,40}>")
NUM = re.compile(r"\d{2,}")
NOTE = re.compile(r"（原文[^）]{0,40}）")
LATIN_WORD = re.compile(r"[A-Za-z]{4,}")

SHOULD_CATS = ("send", "tb", "pat", "code_tb", "prop")


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
    if not m.looks_like_hard_protected_file(rel):
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
    if m.looks_like_identifier_text(src) \
            and not m.is_player_facing_label(rel, src):
        return "ident"
    if m.looks_like_proper_text(rel, src):
        pp = m.partial_glossary_translation(src, rel)
        if pp is not None and pp != src:
            return "prop"
        return "proper"
    return "send"


def entry_checks(name, sid, sv, ov, cat, F):
    """返回该条目的问题标签列表；F=发现累加器。"""
    tags = []
    if not ov.strip():
        tags.append("empty")
        F["empty"].append((name, sid, sv[:60], ""))
        return tags
    if ov.strip() == sv.strip():
        tags.append("untranslated")
        F["untranslated"].append((name, sid, cat, sv[:80]))
        return tags
    mt = NOTE.search(ov)
    if mt:
        tags.append("note_leak")
        F["note_leak"].append((name, sid, mt.group(0), ov[:80]))
    if "&" in sv and "&" not in ov:
        # ★ 2.0.25：Q&A→「问答」类意译不算吞 &；仅品牌 & 形态才点名
        if not (re.search(r"Q\s*&\s*A", sv) and re.search(r"问答|问与答", ov)):
            tags.append("amp_lost")
            F["amp_lost"].append((name, sid, sv[:60], ov[:60]))
    if sorted(PH.findall(sv)) != sorted(PH.findall(ov)):
        tags.append("ph")
        F["ph"].append((name, sid, PH.findall(sv), PH.findall(ov), ov[:60]))
    if sv.count("\\n") != ov.count("\\n"):
        tags.append("lit_nl")
        F["lit_nl"].append((name, sid, sv.count("\\n"), ov.count("\\n")))
    st, ot = sorted(TAG.findall(sv)), sorted(TAG.findall(ov))
    if st != ot:
        tags.append("tag")
        F["tag"].append((name, sid, st, ot, ov[:60]))
    if CYR.search(ov):
        tags.append("cyr_residue")
        F["cyr_residue"].append((name, sid, sv[:50], ov[:50]))
    if ZH.search(ov):
        # ★ 2.0.25：先剥色码/控制符再扫拉丁残留（实测 %c[ui_cyan] 的
        # cyan/purple 占 latin_residue 5594+ 条全是噪声）
        probe = re.sub(r"%c\[[^\]]*\]|\$clr_\d+|\$[A-Za-z_]\w*", " ", ov)
        words = [w for w in LATIN_WORD.findall(probe)
                 if not m._MODEL_LIKE_RE.search(w)
                 and m.termbase_exact_translation(w) is None
                 and w.lower() not in ("pda", "rad", "tv", "pc", "id", "ok")]
        if len(words) >= 2:
            tags.append("latin_residue")
            F["latin_residue"].append((name, sid, words[:5], ov[:70]))
    for n in NUM.findall(sv):
        if n not in ov:
            tags.append("num_lost")
            F["num_lost"].append((name, sid, n, sv[:50], ov[:50]))
            break
    if len(sv) > 300 and len(ov) < len(sv) * 0.5:
        tags.append("trunc")
        F["trunc"].append((name, sid, len(sv), len(ov), sv[:70], ov[:70]))
    return tags


LUA_STR = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'')


def audit_script(sp, op, F):
    """Lua 字面量对照：条数/注记/西里尔/占位符。"""
    def strings(p):
        raw = open(p, "rb").read()
        for enc in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                s = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return None
        return [x.group(0)[1:-1] for x in LUA_STR.finditer(s)]

    name = os.path.relpath(op, OUT).replace("\\", "/")
    S, O = strings(sp), strings(op)
    if S is None or O is None:
        F["parse_fail"].append((name, "decode", "", ""))
        return
    if len(S) != len(O):
        F["lua_count"].append((name, len(S), len(O)))
        return
    n_tr = 0
    for sv, ov in zip(S, O):
        if ov == sv:
            continue
        if ZH.search(ov):
            n_tr += 1
        mt = NOTE.search(ov)
        if mt:
            F["note_leak"].append((name, "-", mt.group(0), ov[:80]))
        if CYR.search(ov) and not CYR.search(sv):
            F["cyr_residue"].append((name, "-", sv[:50], ov[:50]))
        if sorted(PH.findall(sv)) != sorted(PH.findall(ov)):
            F["ph"].append((name, "-", PH.findall(sv), PH.findall(ov), ov[:60]))
    F["_script_translated"].append((name, len(S), n_tr))


def main():
    F = defaultdict(list)
    F["_script_translated"] = []
    slices = defaultdict(lambda: {"files": 0, "copied": 0, "entries": 0,
                                  "should": 0, "zh": 0, "bad": 0})
    xml_outs = sorted(glob.glob(OUT + "/**/*.xml", recursive=True))
    script_outs = sorted(glob.glob(OUT + "/**/*.script", recursive=True))
    print("产出 XML %d | script %d" % (len(xml_outs), len(script_outs)))

    for op in xml_outs:
        rel = os.path.relpath(op, OUT).replace("\\", "/")
        sp = os.path.join(GAME, rel.replace("/", os.sep))
        if not os.path.isfile(sp):
            F["orphan_out"].append((rel, ""))
            continue
        if os.path.getsize(op) == os.path.getsize(sp) and \
                open(op, "rb").read() == open(sp, "rb").read():
            continue  # 引用层原样复制，无需内容审计
        sl = rel.split("/")[1] if rel.startswith("configs/") else rel.split("/")[0]
        if rel.startswith("configs/text/"):
            sl = "text_" + rel.split("/")[2]
        rec = slices[sl]
        rec["files"] += 1
        try:
            S, O = ents(sp), ents(op)
        except Exception as e:
            F["parse_fail"].append((rel, type(e).__name__ + ":" + str(e)[:60]))
            continue
        for sid, sv in S.items():
            rec["entries"] += 1
            ov = O.get(sid)
            if ov is None:
                cat = classify(rel, sv)
                if cat in SHOULD_CATS:
                    F["miss_id"].append((rel, sid, cat, sv[:60]))
                continue
            if ZH.search(sv) and not ZH.search(ov):
                continue  # 源已是中文目标语
            cat = classify(rel, sv)
            if cat in ("noise", "tech", "ident", "code", "target", "proper"):
                continue
            rec["should"] += 1
            if ZH.search(ov):
                rec["zh"] += 1
            tags = entry_checks(rel, sid, sv, ov, cat, F)
            if tags:
                rec["bad"] += 1

    for op in script_outs:
        rel = os.path.relpath(op, OUT).replace("\\", "/")
        sp = os.path.join(GAME, rel.replace("/", os.sep))
        if not os.path.isfile(sp) or \
                os.path.getsize(op) == os.path.getsize(sp):
            continue
        audit_script(sp, op, F)

    n_files = sum(r["files"] for r in slices.values())
    n_should = sum(r["should"] for r in slices.values())
    n_zh = sum(r["zh"] for r in slices.values())
    print("\n翻译文件 %d | 应译条目 %d | 已出中文 %d (%.2f%%)"
          % (n_files, n_should, n_zh, n_zh * 100 / max(n_should, 1)))
    print("\n== 切片汇总 ==")
    for sl, r in sorted(slices.items()):
        rate = r["zh"] / r["should"] if r["should"] else 0
        print("  %-10s 文件%-4d 条目%-7d 应译%-6d 中文率%5.1f%% 问题条%5d"
              % (sl, r["files"], r["entries"], r["should"], rate * 100, r["bad"]))
        if rate < 0.95 and r["should"]:
            F["low_zh_file"].append((sl, r["files"], r["should"], round(rate, 4)))

    print("\n== 发现汇总 ==")
    order = ["parse_fail", "miss_id", "empty", "untranslated", "note_leak",
             "amp_lost", "ph", "lit_nl", "tag", "cyr_residue", "lua_count",
             "orphan_out", "latin_residue", "num_lost", "trunc"]
    for k in order:
        v = F.get(k, [])
        print("  %-14s %6d" % (k, len(v)))
        for row in v[:8]:
            print("      ", row)
    print("\n  script 翻译详情:", F["_script_translated"][:10])

    rep = {k: v for k, v in F.items()}
    rep["_slices"] = {k: v for k, v in slices.items()}
    rep["_meta"] = {"time": time.strftime("%F %T"),
                    "plugin": m.PLUGIN_INFO.get("version", "?"),
                    "out": OUT}
    outdir = os.path.join(ROOT, "audit-reports")
    stamp = time.strftime("%Y%m%d")
    _g = os.path.normpath(GAME)
    mod = (os.path.basename(os.path.dirname(_g))
           if os.path.basename(_g).lower() == "gamedata"
           else os.path.basename(_g))
    path = os.path.join(outdir, "%s_FULL_AUDIT_%s.json" % (mod.upper(), stamp))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print("\n报告:", path)


if __name__ == "__main__":
    main()
