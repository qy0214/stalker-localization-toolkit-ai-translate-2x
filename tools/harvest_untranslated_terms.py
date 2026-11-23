# -*- coding: utf-8 -*-
"""从未翻译/残留审计结果收割"待补译专名"候选清单（只出报告，不写词库）。

背景（v2.0.24 IWP 全量审计）：81+ 条未译短名与 316 条西里尔残留的根因
是词库没有这些词，不是替换管线不够狠。2.0.22 的教训：未经核验的整批
收割（GAMMA/Gunslinger 软层）明确错率约 31%，最终 13946 条整体退役。
所以这里只做**候选提取与归层建议**，译名由后续真实运行（定名指令）或
人工产出，逐条核验后才按来源层写入 termbase `_meta.layers`。

用法：
    python tools/harvest_untranslated_terms.py <audit_json> [--min-count N]

输出：audit-reports/UNTRANSLATED_TERMS_<stamp>.csv
列：term, count, kind(cyr|latin|shortname), sample_files, in_termbase
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "m", os.path.join(ROOT, "plugins", "ai_translate", "plugin.py"))
m = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(m)

CYR = re.compile(r"[А-Яа-яЁё]{4,}")
LATIN_NAME = re.compile(r"(?![A-Za-z]*[-\d])\b[A-Z][a-z]{3,}\b")
SHORT_OK = re.compile(
    r"^[0-9A-Za-zА-Яа-яЁё .,'’\-\"“”«»&()]+$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audit_json")
    ap.add_argument("--min-count", type=int, default=1)
    args = ap.parse_args()
    rep = json.load(open(args.audit_json, encoding="utf-8"))

    cand = Counter()
    kind = {}
    files = {}

    def add(term, k, f):
        term = term.strip().strip(".,;:!?«»“”\"'()（）")
        if len(term) < 4 or not SHORT_OK.search(term):
            return
        cand[term] += 1
        kind.setdefault(term, k)
        files.setdefault(term, set()).add(f)

    # 1. 未译短名条目（整串即专名）
    for name, sid, cat, sv in rep.get("untranslated", []):
        s = sv.strip()
        if 0 < len(s) <= 48 and not re.search(r"[.!?]$", s):
            add(s, "shortname", os.path.basename(name))
    # 2. 译文里的西里尔残留词（武器名/绰号/派系）
    for row in rep.get("cyr_residue", []):
        name, sid, sv, ov = row[0], row[1], row[2], row[3]
        for w in CYR.findall(ov):
            add(w, "cyr", os.path.basename(name))
    # 3. 粘连拉丁残词（克里门enko 类给出完整拉丁段）
    for row in rep.get("latin_residue", []):
        name = row[0]
        for w in row[2] or []:
            if LATIN_NAME.fullmatch(w):
                add(w, "latin", os.path.basename(name))

    # 词库已有键的排除（任何层任何槽）
    tb = m.load_termbase_data()
    flat = set()
    for cat, tbl in tb.items():
        if cat.startswith("_") or not isinstance(tbl, dict):
            continue
        for k in tbl:
            flat.add(str(k).casefold())

    rows = []
    for term, n in cand.most_common():
        if n < args.min_count:
            continue
        if term.casefold() in flat:
            continue
        rows.append((term, n, kind[term],
                     ";".join(sorted(files[term])[:3])))
    stamp = time.strftime("%Y%m%d")
    out = os.path.join(ROOT, "audit-reports",
                       "UNTRANSLATED_TERMS_%s.csv" % stamp)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        f.write("term,count,kind,sample_files\n")
        for term, n, k, fs in rows:
            f.write("%s,%d,%s,%s\n" % (str(term).replace(",", "，"), n,
                                       k, fs.replace(",", "，")))
    print("候选专名 %d 个（词库已有 %d 个已排除）→ %s"
          % (len(rows), len(cand) - len(rows), out))
    for term, n, k, fs in rows[:15]:
        print("  %-28s n=%-4d %s  %s" % (term, n, k, fs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
