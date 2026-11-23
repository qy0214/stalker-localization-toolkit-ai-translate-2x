# -*- coding: utf-8 -*-
"""术语库交叉一致性审计（新方法，不依赖旧审计脚本）。

维度：
  S1 同源跨类冲突：剥引号/首尾空白后，同一源词在不同分类译名不同
  S2 同译多源：同一中文译名被 >=8 个不同源词共用（可能过度泛化）
  S3 词干族不一致：西里尔词按前 6 字符分组，组内出现多个不同译名
  S4 译文卫生：半角句读、无中文、混入西里尔、双空格、首尾空白
  S5 源=译 或 译名含源词未译
输出：控制台 + audit-reports/TERMBASE_CROSS_AUDIT_20261124.md
"""
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TB = os.path.join(ROOT, "plugins", "ai_translate", "assets",
                  "stalker_termbase.json")
OUT = os.path.join(ROOT, "audit-reports", "TERMBASE_CROSS_AUDIT_20261124.md")
CJK = re.compile(r"[\u4e00-\u9fff]")
CYR = re.compile(r"[\u0400-\u04ff]")
QUOTES = "\"'«»“”‘’"

tb = json.load(open(TB, encoding="utf-8"))
items = []          # (cat, key, value)
for cat, tbl in tb.items():
    if cat.startswith("_") or not isinstance(tbl, dict):
        continue
    for k, v in tbl.items():
        k = str(k).strip()
        if k and not k.startswith("_"):
            items.append((cat, k, str(v)))


def norm_src(s):
    s = s.strip().strip(QUOTES).strip()
    s = re.sub(r"\s+", " ", s)
    return s.casefold()


# S1 同源跨类冲突（译名剥引号后比较；纯引号风格差异不算冲突）
def nv(v):
    s = str(v).strip()
    for a, b in (("「", ""), ("」", ""), ("『", ""), ("』", "")):
        s = s.replace(a, b)
    return s.strip(QUOTES).strip()


by_src = defaultdict(list)
for cat, k, v in items:
    by_src[norm_src(k)].append((cat, k, v))
s1 = []
for ns, lst in by_src.items():
    vals = {nv(v) for _c, _k, v in lst}
    cats = {c for c, _k, _v in lst}
    if len(vals) > 1 and len(cats) > 1:
        s1.append((ns, lst))

# S2 同译多源
by_val = defaultdict(set)
for _cat, k, v in items:
    if CJK.search(v) and len(v.strip()) >= 2:
        by_val[v.strip()].add(norm_src(k))
s2 = sorted(((v, len(ks), sorted(ks)[:6]) for v, ks in by_val.items()
             if len(ks) >= 8), key=lambda x: -x[1])

# S3 词干族不一致（西里尔）
by_stem = defaultdict(list)
for cat, k, v in items:
    if not CYR.search(k):
        continue
    stem = re.sub(r"[^А-Яа-яЁё]", "", k.casefold())[:6]
    if len(stem) >= 5:
        by_stem[stem].append((cat, k, v))
s3 = []
for stem, lst in by_stem.items():
    vals = {v.strip() for _c, _k, v in lst}
    if len(vals) >= 2 and len(lst) >= 2:
        s3.append((stem, lst))
s3.sort(key=lambda x: -len(x[1]))

# S4 译文卫生
HALF = re.compile(r"[,.!?;:](?!\d)")
s4_half, s4_nozh, s4_cyr, s4_space = [], [], [], []
for cat, k, v in items:
    if not CJK.search(v):
        s4_nozh.append((cat, k, v))
        continue
    if HALF.search(v) and not re.search(r"[A-Za-z0-9]", v):
        s4_half.append((cat, k, v))
    if CYR.search(v) and not CYR.search(k):
        s4_cyr.append((cat, k, v))
    if "  " in v or v != v.strip():
        s4_space.append((cat, k, v))

# S5 源=译 / 未译残留
s5_same, s5_resid = [], []
for cat, k, v in items:
    if k.strip() == v.strip():
        s5_same.append((cat, k, v))
    elif CYR.search(k) and CYR.search(v):
        s5_resid.append((cat, k, v))

lines = ["# 术语库交叉一致性审计（2026-11-24，新方法）", "",
         "范围：%d 条 / %d 分类。方法与旧审计脚本不同（同源跨类/同译多源/"
         "词干族/文本卫生/未译残留）。" % (len(items), len(
             [c for c in tb if not c.startswith('_')])), ""]


def sec(title, n, rows, fmt, limit=12):
    lines.append("## %s：%d 组" % (title, n))
    lines.append("")
    if not n:
        lines.append("（无）")
        lines.append("")
        return
    for r in rows[:limit]:
        lines.append(fmt(r))
    if n > limit:
        lines.append("… 共 %d 组，完整清单见控制台输出" % n)
    lines.append("")


sec("S1 同源跨类冲突", len(s1), s1,
    lambda r: "- `%s`：" % r[0] + "；".join(
        "%s→`%s`(%s)" % (v, k, c) for c, k, v in r[1][:4]))
sec("S2 同译多源（≥8 源共用一个译名）", len(s2), s2,
    lambda r: "- `%s` ← %d 个源词：%s" % (r[0], r[1],
                                          "、".join(r[2])))
sec("S3 词干族不一致", len(s3), s3,
    lambda r: "- 词干 `%s`（%d 条）：%s" % (
        r[0], len(r[1]), "；".join("%s→%s" % (k, v) for c, k, v in r[1][:4])))
sec("S4a 中文译文含半角标点", len(s4_half), s4_half,
    lambda r: "- [%s] `%s` → `%s`" % r)
sec("S4b 译文无中文", len(s4_nozh), s4_nozh,
    lambda r: "- [%s] `%s` → `%s`" % r)
sec("S4c 译文混入西里尔", len(s4_cyr), s4_cyr,
    lambda r: "- [%s] `%s` → `%s`" % r)
sec("S4d 译文空格异常", len(s4_space), s4_space,
    lambda r: "- [%s] `%s` → `%s`" % r)
sec("S5a 源=译", len(s5_same), s5_same,
    lambda r: "- [%s] `%s`" % (r[0], r[1]))
sec("S5b 源与译均含西里尔（未译嫌疑）", len(s5_resid), s5_resid,
    lambda r: "- [%s] `%s` → `%s`" % r)

counts = [("S1 同源跨类冲突", len(s1)), ("S2 同译多源", len(s2)),
          ("S3 词干族不一致", len(s3)), ("S4a 半角标点", len(s4_half)),
          ("S4b 无中文", len(s4_nozh)), ("S4c 混入西里尔", len(s4_cyr)),
          ("S4d 空格异常", len(s4_space)), ("S5a 源=译", len(s5_same)),
          ("S5b 未译嫌疑", len(s5_resid))]
lines.insert(4, "| 维度 | 组数 |\n|---|---:|")
for name, n in counts:
    lines.insert(5 + counts.index((name, n)),
                 "| %s | %d |" % (name, n))

os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="utf-8").write("\n".join(lines))

print("== 交叉一致性审计 ==")
for name, n in counts:
    print("  %-16s %d" % (name, n))
print("报告:", OUT)

# 控制台补足每组前 8 条，供人工复核
for title, rows, fmt in (
        ("S1 同源跨类冲突", s1[:8],
         lambda r: "  %s -> %s" % (r[0], [(c, k, v) for c, k, v in r[1][:3]])),
        ("S3 词干族不一致", s3[:8],
         lambda r: "  %s -> %s" % (r[0], [(k, v) for c, k, v in r[1][:4]])),
        ("S4b 无中文", s4_nozh[:8], lambda r: "  [%s] %s -> %s" % r),
        ("S5b 未译嫌疑", s5_resid[:8], lambda r: "  [%s] %s -> %s" % r)):
    print("\n-- %s --" % title)
    for r in rows:
        print(fmt(r))
