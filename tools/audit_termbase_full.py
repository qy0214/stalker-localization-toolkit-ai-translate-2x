# -*- coding: utf-8 -*-
"""术语库全量深度审计（v2）。

在 audit_termbase.py --strict 之上的增量维度：
  A 结构层：分类/层级清单、三副本哈希一致性（新库 assets / 旧库 assets / tools 副本）
  B 质量层：HTML 实体残留、标签残留、引号不平衡、键首尾空白、纯符号键
  C 译文层：西里尔残留（未翻完）、长度比异常、纯数字/纯符号译文
  D 软层专项：GAMMA/Gunslinger 参考全量复检（垃圾过滤器口径 + 新增启发式）
  E 语义抽检：canon 硬层逐条扫描可疑模式（硬替换风险最高）
  F 性能层：加载耗时、单查耗时、JSON 体积

只读诊断，不修改任何文件。输出 audit-reports/AUDIT_FULL_<date>.md
用法: python tools/audit_termbase_full.py [--report]
"""
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TB = os.path.join(ROOT, "plugins", "ai_translate", "assets", "stalker_termbase.json")
GB = os.path.join(ROOT, "plugins", "ai_translate", "assets", "glossary_builtin.json")
OLD_ASSETS = r"C:/STALKER-HH-2x/plugins/ai_translate_assets"
OLD_TOOLS = r"C:/STALKER-HH-2x/tools"
DESKTOP = r"C:/Users/Admin/Desktop/1/plugins/ai_translate/assets"
REPORT_DIR = os.path.join(ROOT, "audit-reports")

ZH = re.compile(r"[\u4e00-\u9fff]")
CYR = re.compile(r"[А-Яа-яЁё]")
HTML_ENT = re.compile(r"&[a-zA-Z]+;|&#\d+;")
TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*>")
QPAIR = "\"'«»“”‘’"
ZH_VEHICLE = re.compile(
    r"坦克|直升机|战斗机|轰炸机|驱逐舰|巡洋舰|潜艇|军舰|装甲车|航母")
_CYR2LAT = str.maketrans({
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E",
    "Ж": "ZH", "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M",
    "Н": "N", "О": "O", "П": "P", "Р": "P", "С": "S", "Т": "T", "У": "U",
    "Ф": "F", "Х": "X", "Ц": "TS", "Ч": "CH", "Ш": "SH", "Щ": "SHCH",
    "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "YU", "Я": "YA"})


from harvest_termbase_from_human import (  # noqa: E402
    _norm_key, garbage_translation, _SRC_VEHICLE_RE,
    _design_tokens, _tokens_match)


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]


def ratio_len(k, v):
    lk, lv = len(k.strip()), len(v.strip())
    return lv / max(lk, 1)


def main():
    t0 = time.time()
    data = json.load(open(TB, encoding="utf-8"))
    meta = data["_meta"]
    layers = meta["layers"]
    canon_cats = set(layers["canon"])
    name_cats = set(layers["names"])
    tech_cats = set(layers["technical"])
    ref_cats = set(layers["reference"])
    soft_cats = ref_cats | name_cats

    findings = defaultdict(list)   # (severity, check) -> [(cat, k, v, note)]

    def add(sev, check, cat, k, v, note=""):
        findings[(sev, check)].append((cat, k, v, note))

    items = []   # (cat, k, v)
    for cat, tbl in data.items():
        if cat == "_meta" or not isinstance(tbl, dict):
            continue
        for k, v in tbl.items():
            items.append((cat, str(k), str(v)))

    total = len(items)
    layer_of = {}
    for cat, k, v in items:
        layer_of[cat] = ("canon" if cat in canon_cats else
                         "names" if cat in name_cats else
                         "technical" if cat in tech_cats else
                         "reference" if cat in ref_cats else "未分层")

    # ── A 结构层 ────────────────────────────────────────────────
    print("== A 结构层 ==")
    cat_counts = Counter(c for c, _k, _v in items)
    for c in sorted(cat_counts):
        print("  %-14s %6d  [%s]" % (c, cat_counts[c], layer_of[c]))
    print("  合计:", total, " / _meta.total_entries:", meta.get("total_entries"))
    if total != meta.get("total_entries"):
        add("medium", "meta_total", "_meta", "", "",
            "_meta.total_entries=%s 实际=%d" % (meta.get("total_entries"), total))

    copies = {"new_assets": TB, "old_assets": os.path.join(OLD_ASSETS, "stalker_termbase.json"),
              "old_tools": os.path.join(OLD_TOOLS, "stalker_termbase.json"),
              "desktop": os.path.join(DESKTOP, "stalker_termbase.json")}
    hashes = {n: (sha(p) if os.path.exists(p) else "缺失") for n, p in copies.items()}
    print("  termbase 副本哈希:", hashes)
    uniq_h = set(hashes.values())
    if len(uniq_h) > 1:
        add("high", "copy_sync_termbase", "_meta", "", "", str(hashes))
    gbs = {"new": GB, "old_assets": os.path.join(OLD_ASSETS, "glossary_builtin.json"),
           "desktop": os.path.join(DESKTOP, "glossary_builtin.json")}
    gh = {n: (sha(p) if os.path.exists(p) else "缺失") for n, p in gbs.items()}
    if len(set(gh.values())) > 1:
        add("high", "copy_sync_builtin", "_meta", "", "", str(gh))
    print("  builtin 副本哈希:", gh)

    # 层级完整性：每个分类都应归属一个层
    for cat in cat_counts:
        if cat not in canon_cats | name_cats | tech_cats | ref_cats:
            add("medium", "layer_missing", cat, "", "", "分类未写入 _meta.layers")

    # ── B 质量层（全库） ───────────────────────────────────────
    print("== B 质量层 ==")
    for cat, k, v in items:
        if HTML_ENT.search(k) or HTML_ENT.search(v):
            add("medium", "html_entity", cat, k, v)
        if TAG.search(k):
            add("high", "tag_in_key", cat, k, v)
        if TAG.search(v) and cat not in tech_cats:
            add("medium", "tag_in_value", cat, k, v)
        if k != k.strip():
            add("low", "key_ws", cat, k, v)
        if not re.search(r"[0-9A-Za-zА-Яа-яЁё\u4e00-\u9fff]", k):
            add("low", "key_symbol_only", cat, k, v)
        for q in "\"«“‘":
            if k.count(q) and k.count(q) % 2 != 0 and q not in "\"'":
                add("low", "quote_unbalanced_key", cat, k, v, q)
        if CYR.search(v) and cat in canon_cats and not CYR.search(k):
            add("high", "cyr_leftover_canon", cat, k, v)

    # ── C 译文层 ───────────────────────────────────────────────
    print("== C 译文层 ==")
    for cat, k, v in items:
        if cat in tech_cats:
            continue
        r = ratio_len(k, v)
        if ZH.search(v):
            if r > 6 and len(k) > 8:
                add("low", "value_too_long", cat, k, v, "ratio=%.1f" % r)
            if not ZH.search(v.replace("％", "")) and len(v) > 2 and \
                    not re.fullmatch(r"[0-9%s\s./%%-]+" % "".join(chr(x) for x in range(0x2460, 0x2474)), v):
                pass
        core = re.sub(r"[0-9\s./%%\-()（）A-Za-z]", "", v)
        if not ZH.search(v) and core and cat not in tech_cats and \
                k.upper() not in ("PDA", "КПК", "ПДА"):
            add("medium", "value_no_cjk_v2", cat, k, v)

    # ── D 软层专项（GAMMA/Gunslinger/ANTHOLOGY 参考） ──────────
    print("== D 软层专项 ==")
    soft_new_cats = [c for c in ("GAMMA 参考", "Gunslinger 参考") if c in data]
    for cat in soft_new_cats:
        n_bad = 0
        for k, v in data.get(cat, {}).items():
            if ZH_VEHICLE.search(v) and not _SRC_VEHICLE_RE.search(k):
                add("high", "soft_vehicle", cat, k, v)
                n_bad += 1
            ks = _design_tokens(k)
            zs = _design_tokens(v, merge_space=True)
            if ks and zs and not _tokens_match(ks, zs):
                add("medium", "soft_design_drift", cat, k, v)
                n_bad += 1
            if CYR.search(v) and not CYR.search(k):
                add("medium", "soft_cyr_leftover", cat, k, v)
                n_bad += 1
        print("  %s: %d 条, 复检命中 %d" % (cat, len(data.get(cat, {})), n_bad))

    # ── E canon 硬层语义抽检 ───────────────────────────────────
    print("== E canon 硬层抽检 ==")
    for cat in sorted(canon_cats):
        tbl = data.get(cat, {})
        bad = 0
        for k, v in tbl.items():
            # 硬层禁句式（句末标点 / 超长）
            if len(k.split()) > 8 or re.search(r"[.!?]$", k.strip()):
                add("medium", "canon_sentence", cat, k, v)
                bad += 1
            if ZH_VEHICLE.search(v) and not _SRC_VEHICLE_RE.search(k):
                add("high", "canon_vehicle", cat, k, v)
                bad += 1
        print("  %s: %d 条, 抽检命中 %d" % (cat, len(tbl), bad))

    # ── F 性能层 ───────────────────────────────────────────────
    print("== F 性能层 ==")
    sys.path.insert(0, os.path.join(ROOT, "plugins", "ai_translate"))
    t1 = time.time()
    import importlib
    import plugin as P
    importlib.reload(P)
    layers_rt = P.load_term_layers()
    t_load = (time.time() - t1) * 1000
    print("  load_term_layers: %.0f ms (canon %d / names %d / reference %d)"
          % (t_load, len(layers_rt["canon"]), len(layers_rt["names"]),
             len(layers_rt["reference"])))
    probe = ["Бармен", "VELES DETECTOR", "FN F2000", "酒保", "Strelok",
             "АКМ-74/2У", "G3A3", "Пустышк"]
    t2 = time.time()
    hits = 0
    for _ in range(50):
        for s in probe:
            if P.termbase_lookup(s) is not None:
                hits += 1
    t_q = (time.time() - t2) / (50 * len(probe)) * 1000
    print("  termbase_lookup 平均: %.3f ms/次 (50×%d 次命中 %d)" % (t_q, len(probe), hits))
    size_mb = os.path.getsize(TB) / 1024 / 1024
    print("  JSON 体积: %.2f MB" % size_mb)
    if t_load > 2000:
        add("medium", "perf_load", "_meta", "", "", "%.0f ms" % t_load)
    if t_q > 1.0:
        add("medium", "perf_lookup", "_meta", "", "", "%.3f ms" % t_q)

    # ── 汇总 ───────────────────────────────────────────────────
    print("\n== 发现汇总 ==")
    sev_count = Counter()
    for (sev, check), rows in sorted(findings.items(), key=lambda x: (x[0][0], x[0][1])):
        sev_count[sev] += 1
        print("  [%s] %s: %d 条" % (sev, check, len(rows)))
        for cat, k, v, note in rows[:6]:
            print("      %s | %r -> %r %s" % (cat, k[:50], v[:40], note))
        if len(rows) > 6:
            print("      ... 共 %d 条" % len(rows))
    print("  发现维度: %d；总体严重度: %s" %
          (len(sev_count), dict(sev_count) or "全部通过"))
    print("  耗时 %.1f s" % (time.time() - t0))

    if "--report" in sys.argv:
        os.makedirs(REPORT_DIR, exist_ok=True)
        out = os.path.join(REPORT_DIR, "AUDIT_FULL_20261123.md")
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write("# 术语库全量审计（深度 v2）\n\n")
            f.write("日期：2026-11-23　词库：%d 条　体积：%.2f MB\n\n" % (total, size_mb))
            f.write("## 分层清单\n\n| 分类 | 条数 | 层 |\n|---|---:|---|\n")
            for c in sorted(cat_counts):
                f.write("| %s | %d | %s |\n" % (c, cat_counts[c], layer_of[c]))
            f.write("\n## 副本一致性\n\n- termbase: `%s`\n- builtin: `%s`\n\n"
                    % (hashes, gh))
            f.write("## 发现明细\n\n")
            if not findings:
                f.write("（无发现）\n")
            for (sev, check), rows in sorted(findings.items(),
                                             key=lambda x: (x[0][0], x[0][1])):
                f.write("### [%s] %s（%d 条）\n\n" % (sev, check, len(rows)))
                f.write("| 分类 | 原文 | 译文 | 备注 |\n|---|---|---|---|\n")
                for cat, k, v, note in rows:
                    f.write("| %s | `%s` | `%s` | %s |\n"
                            % (cat, k.replace("|", "\\|")[:80],
                               v.replace("|", "\\|")[:60], note))
            f.write("\n## 性能\n\n- load_term_layers: %.0f ms\n- lookup: %.3f ms/次\n"
                    % (t_load, t_q))
        print("报告：%s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
