# -*- coding: utf-8 -*-
"""IWP 存量译文质量闸门（外部数据治理）。

定位：tools/audit_iwp_full.py 产出的是*原始证据*（全部疑点无差别列出）；
本工具在其上应用*治理策略*，区分"真缺陷（重译工单）"与"合规豁免"，
并可对产物目录做确定性修复（符号表透传）。只改 tools 侧产物，不碰 NS7 数据。

策略（对 audit JSON 后处理）：
  BLOCK 级（必须重译，进工单）：
    untranslated  应译未译（输出==源文）
    ph            占位符集合不一致（.script 字符串）
    miss_id/empty/note_leak/amp_lost/parse_fail  其余 BLOCK 类
  豁免（合规，不进工单）：
    orphan_out    sgtat_* 工具自生成文件
    cyr_residue   残留西里尔仅为短专有名词（<=3 个 token、每个 <=14 字母）
    trunc         CJK 校准：中文信息密度约为英文 2 倍，
                  有效译长 = 中文字符*2 + 其它字符；仅 eff < 50%*源长(源>300) 记缺陷
    num_lost      年代换算（1990s→"20世纪90年代" 等） digit 重叠或 (18|19|20)xx 前缀命中即豁免
    latin_residue 词库内词条 / 型号类 token（大写+数字）豁免，其余进 WARN 工单
    tag           [方括号] 内译文差异属风格差异，WARN 观察项，不进工单

用法：
  python tools/iwp_quality_gate.py                     # 纯闸门：读最新 audit JSON，出工单
  python tools/iwp_quality_gate.py --copy-missing      # 先把 SRC 独有文件按字节透传到 OUT
  python tools/iwp_quality_gate.py --report PATH.json  # 指定 audit 报告
退出码：0=闸门通过（BLOCK=0）；1=存在 BLOCK 缺陷（工单已生成）。
"""
import glob
import json
import os
import re
import shutil
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(ROOT, "audit-reports")

GAME = os.environ.get("IWP_GAME") or r"C:\Users\Admin\Desktop\IWP\gamedata"
OUT_NAME = "translated_hy_mt2"

ZH = re.compile(r"[\u4e00-\u9fff]")
CYR_TOK = re.compile(r"[А-Яа-яЁё][А-Яа-яЁё\-']*")
YEAR_RE = re.compile(r"\b(18|19|20)\d{2}\b")
NUM2 = re.compile(r"\d{2,}")
MODEL_TOK = re.compile(r"^[A-Z0-9][A-Za-z0-9\-]*\d[A-Za-z0-9\-]*$")


def latest_report():
    paths = sorted(glob.glob(os.path.join(REPORT_DIR, "IWP_FULL_AUDIT_*.json")))
    if not paths:
        print("未找到 audit-reports/IWP_FULL_AUDIT_*.json，请先运行 tools/audit_iwp_full.py")
        return None
    return paths[-1]


def load_termbase_keys():
    keys = set()
    tb_path = os.path.join(ROOT, "plugins", "ai_translate", "assets",
                           "stalker_termbase.json")
    try:
        tb = json.load(open(tb_path, encoding="utf-8"))
    except (OSError, ValueError):
        return keys
    for layer, mapping in tb.items():
        if layer == "_meta" or not isinstance(mapping, dict):
            continue
        for k in mapping:
            keys.add(str(k).casefold())
    return keys


def copy_missing(game, out_name):
    """源侧独有文件按字节透传到产出目录（符号表/无需翻译文件）。"""
    src_root, out_root = os.path.join(game, "configs"), \
        os.path.join(game, out_name, "configs")
    copied = []
    for kind in ("text/eng", "text/rus"):
        s_dir = os.path.join(src_root, kind.replace("/", os.sep))
        o_dir = os.path.join(out_root, kind.replace("/", os.sep))
        if not os.path.isdir(s_dir) or not os.path.isdir(o_dir):
            continue
        for name in sorted(os.listdir(s_dir)):
            if not name.lower().endswith(".xml"):
                continue
            s_p, o_p = os.path.join(s_dir, name), os.path.join(o_dir, name)
            if not os.path.isfile(o_p):
                shutil.copy2(s_p, o_p)
                copied.append("%s/%s" % (kind, name))
    return copied


def _out_entries(rel_path, out_root):
    """读产物 XML 全文条目（注释归一同 verify_iwp_output）。"""
    import xml.etree.ElementTree as ET
    p = os.path.join(out_root, rel_path.replace("/", os.sep))
    try:
        raw = open(p, "rb").read()
    except OSError:
        return {}
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            s = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return {}
    s = re.sub(r"<!-+", "<!--", s)
    s = re.sub(r"<!--(.*?)(-{2,})>", lambda m: "<!--" + m.group(1) + "-->", s)
    s = s.replace("--!>", "-->")
    try:
        root = ET.fromstring(s)
    except ET.ParseError:
        return {}
    return {e.get("id", ""): "".join(t.itertext())
            for e in root.iter("string") if (t := e.find("text")) is not None}


def classify(rep, tb_keys, out_root):
    """返回 (rework_blocks, rework_warns, waived, tag_watch)。"""
    cache = {}

    def full_out(rel, sid):
        key = rel
        if key not in cache:
            cache[key] = _out_entries(rel, out_root)
        return cache[key].get(sid, "")
    blocks, warns, waived = [], [], Counter()
    tag_watch = []

    for e in rep.get("untranslated", []):
        blocks.append({"kind": "untranslated", "file": e[0], "id": e[2],
                       "src": e[3], "out": e[3]})
    for e in rep.get("ph", []):
        blocks.append({"kind": "ph", "file": e[0], "id": e[4],
                       "src_ph": e[2], "out_ph": e[3]})
    for kind in ("miss_id", "empty", "note_leak", "amp_lost"):
        for e in rep.get(kind, []):
            blocks.append({"kind": kind, "file": e[0], "id": e[1],
                           "src": e[2] if len(e) > 2 else "", "detail": str(e[3:])})
    for e in rep.get("parse_fail", []):
        blocks.append({"kind": "parse_fail", "file": e[0], "id": ""})

    for e in rep.get("orphan_out", []):
        name = os.path.basename(e[0])
        if name.startswith("sgtat_"):
            waived["orphan_out_generated"] += 1
        else:
            blocks.append({"kind": "orphan_out", "file": e[0], "id": e[1]})

    for e in rep.get("cyr_residue", []):
        out = e[3] if len(e) > 3 else ""
        toks = CYR_TOK.findall(out)
        if len(toks) <= 3 and all(len(t) <= 14 for t in toks):
            waived["cyr_proper_noun"] += 1
        else:
            blocks.append({"kind": "cyr_sentence", "file": e[0], "id": e[1],
                           "src": e[2], "out": out})

    for e in rep.get("num_lost", []):
        # e: [file, id, missing_num, src, out]
        missing = e[2]
        out = full_out(e[0], e[1])
        if missing in out:
            waived["num_present"] += 1
            continue
        m_clean = re.sub(r"[sS]$", "", str(missing))  # 1990s → 1990
        if YEAR_RE.search(m_clean) and YEAR_RE.search(out):
            waived["num_year_converted"] += 1
            continue
        # 源两位数出现在译文中（如 1990s → "90 年代"）
        tail = m_clean[-2:]
        if len(m_clean) >= 3 and tail in out:
            waived["num_partial"] += 1
            continue
        warns.append({"kind": "num_lost", "file": e[0], "id": e[1],
                      "missing": missing, "src": e[3] if len(e) > 3 else ""})

    for e in rep.get("latin_residue", []):
        # e: [file, id, tokens, out]
        toks = e[2] if len(e) > 2 and isinstance(e[2], list) else []
        bad = [t for t in toks
               if t.casefold() not in tb_keys and not MODEL_TOK.match(t)
               and len(t) > 3]
        if not bad:
            waived["latin_termbase_or_model"] += 1
        else:
            warns.append({"kind": "latin_residue", "file": e[0], "id": e[1],
                          "tokens": bad})

    src_len_pad = 300
    for e in rep.get("trunc", []):
        # e: [file, id, src_len, out_len, src_preview, out_preview]
        src_len, sid = e[2], e[1]
        out = full_out(e[0], sid)
        if src_len <= src_len_pad:
            waived["trunc_below_min_src"] += 1
            continue
        eff = len(ZH.findall(out)) * 2 + (len(out) - len(ZH.findall(out)))
        if eff >= 0.5 * src_len:
            waived["trunc_cjk_calibrated"] += 1
            continue
        warns.append({"kind": "trunc", "file": e[0], "id": sid,
                      "src_len": src_len, "eff_len": eff, "src": e[4][:120]})

    for e in rep.get("tag", []):
        tag_watch.append({"file": e[0], "id": e[1]})
    return blocks, warns, waived, tag_watch


def main():
    args = sys.argv[1:]
    report_path = None
    do_copy = "--copy-missing" in args
    if "--report" in args:
        report_path = args[args.index("--report") + 1]

    if do_copy:
        copied = copy_missing(GAME, OUT_NAME)
        print("透传缺失文件 %d 个：%s" % (len(copied), copied))

    path = report_path or latest_report()
    if not path:
        return 2
    rep = json.load(open(path, encoding="utf-8"))
    tb_keys = load_termbase_keys()
    out_root = os.path.join(GAME, OUT_NAME)
    blocks, warns, waived, tag_watch = classify(rep, tb_keys, out_root)

    print("== IWP 质量闸门（策略层）==")
    print("证据报告: %s" % os.path.basename(path))
    print("BLOCK（重译工单）: %d" % len(blocks))
    print("  " + str(Counter(b["kind"] for b in blocks)))
    print("WARN（复检工单）: %d" % len(warns))
    print("  " + str(Counter(w["kind"] for w in warns)))
    print("豁免: %s" % dict(waived))
    print("tag 观察项: %d（风格差异，不阻断）" % len(tag_watch))

    stamp = os.path.splitext(os.path.basename(path))[0].rsplit("_", 2)[-1]
    out_json = os.path.join(REPORT_DIR, "IWP_REWORK_LIST_%s.json" % stamp)
    payload = {
        "_meta": {"report": os.path.basename(path), "blocks": len(blocks),
                  "warns": len(warns), "waived": dict(waived)},
        "rework_block": blocks,
        "rework_warn": warns,
    }
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print("工单: %s" % out_json)

    return 1 if blocks else 0


if __name__ == "__main__":
    sys.exit(main())
