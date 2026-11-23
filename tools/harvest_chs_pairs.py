# -*- coding: utf-8 -*-
"""从 CHS 人工对照包按条目 id 收割短条目术语对（v2.0.25，P1/P2/P4）。

只收：id 后缀为玩家可见字段（name/descr/text/title/hint/...）且源文 ≤48
字符的条目 + 弹药状态条目（口径+弹种+状态形态）。人工翻译质量 + 全守卫：
占位符集合一致、字面 \\n 数一致、norm 撞键跳过、译名含中文、非纯标点。

写入：新分类「人工对照」（硬层 names 族：整串直取 + 拉丁回填参与），
两随包 JSON 同步。产出候选留痕 audit-reports/CHS_HARVEST_<stamp>.csv。
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
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "m", os.path.join(ROOT, "plugins", "ai_translate", "plugin.py"))
m = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(m)

GAME = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\Admin\Desktop\aera\gamedata"
CHS = sys.argv[2] if len(sys.argv) > 2 else r"C:\Users\Admin\Desktop\aera\CHS"
CAT = "人工对照"
MAXLEN = 48
ZH = re.compile(r"[\u4e00-\u9fff]")
PH = re.compile(r"\$[A-Za-z_]\w*|%[-+ #0-9.]*[sdifuc]|%c\[[^\]]*\]")
ID_SUFFIX = re.compile(r"(?:_|^)(?:name|descr|description|text|title|hint|"
                       r"info|tips|caption|short|msg)(?:_?\d+)?$",
                       re.I)
AMMO = re.compile(
    r"^\s*[\d.]+\s*[x×]\s*\d+\s*\w*\s+(?:NATO\s+)?"
    r"(?:FMJ|AP|HP|JHP|SP)?\s*(?:Rounds?|Buckshot|Slug|Ammo?)"
    r"\s*\((?:wet|bad|verybad)\)\s*$", re.I)

# ★ 2.0.25（P2 单名补收）：CHS 里这些专名只出现在短语中，机械对齐词级
# 提取不可靠；以下单名逐条经短语对交叉验证（≥2 处 CHS 佐证一致）后
# 手工确认，作为整条键补进同层。空缺（Yoga/Hog 等）不猜，留真实运行。
EXTRA_SINGLE = {
    "Magpie": "喜鹊",
    "Slicer": "切割者",
    "Suitcase": "箱子",
    "Dubrov": "杜布罗夫",
    "Psyblock": "PSI阻断剂",
    "USS Command": "USS 指挥部",
}


def ents(path):
    with contextlib.redirect_stdout(io.StringIO()):
        es = m._load_vendor().parse_xray_text_xml(
            path, list(m.read_encoding_chain(path))) or []
    out = {}
    for e in es:
        t = (e.texts or {}).get("text") or (e.texts or {}).get("rus") \
            or (e.texts or {}).get("eng")
        if e.id is not None and t:
            out[e.id] = t
    return out


def norm_key(s):
    return m.GlossaryIndex._fold(s)


tb_path = os.path.join(ROOT, "plugins", "ai_translate", "assets",
                       "stalker_termbase.json")
gb_path = os.path.join(ROOT, "plugins", "ai_translate", "assets",
                       "glossary_builtin.json")
tb = json.load(open(tb_path, encoding="utf-8"))
gb = json.load(open(gb_path, encoding="utf-8"))

# 现有全部键（casefold+ё 折叠）→ 撞键守卫
existing = {}
for cat, tbl in list(tb.items()) + list(gb.items()):
    if cat.startswith("_") or not isinstance(tbl, dict):
        continue
    for k in tbl:
        existing.setdefault(norm_key(k), k)

pairs = {}          # src -> (zh, sid, file)
skips = {"len": 0, "nozh": 0, "eq": 0, "ph": 0, "nl": 0, "punct": 0,
         "collide": 0, "id": 0, "dup": 0}
n_seen = 0
for sp in sorted(glob.glob(os.path.join(GAME, "configs", "text", "eng",
                                        "*.xml"))):
    name = os.path.basename(sp)
    cp = os.path.join(CHS, "gamedata", "configs", "text", "eng", name)
    if not os.path.isfile(cp):
        continue
    try:
        S, C = ents(sp), ents(cp)
    except Exception:
        continue
    for sid, sv in S.items():
        sv = sv.strip()
        cv = C.get(sid)
        if cv is None or not ZH.search(cv):
            continue
        n_seen += 1
        if not (ID_SUFFIX.search(sid) or AMMO.match(sv)):
            skips["id"] += 1
            continue
        if len(sv) > MAXLEN or len(sv) < 3:
            skips["len"] += 1
            continue
        if sv.strip() == cv.strip() or not ZH.search(cv):
            skips["eq"] += 1
            continue
        if not ZH.search(cv) or ZH.search(sv):
            skips["nozh"] += 1
            continue
        if sorted(PH.findall(sv)) != sorted(PH.findall(cv)):
            skips["ph"] += 1
            continue
        if sv.count("\\n") != cv.count("\\n"):
            skips["nl"] += 1
            continue
        if re.fullmatch(r"[\W\d_]+", sv.strip()):
            skips["punct"] += 1
            continue
        k = norm_key(sv)
        if k in existing:
            skips["collide"] += 1
            continue
        if k in {norm_key(x) for x in pairs}:
            skips["dup"] += 1
            continue
        pairs[sv] = (cv.strip(), sid, name)

print("CHS 已译条目 %d，收割候选 %d，跳过 %s"
      % (n_seen, len(pairs), skips))

# 写入两 JSON（新分类 + _meta.layers.names 挂载）
for tgt in (tb, gb):
    tbl = tgt.setdefault(CAT, {})
    layers = tgt.setdefault("_meta", {}).setdefault("layers", {})
    names = layers.setdefault("names", [])
    n_cat = 0
    for src, (zh_v, sid, fname) in sorted(pairs.items()):
        if norm_key(src) in {norm_key(k) for k in tbl}:
            continue
        tbl[src.strip()] = zh_v
        n_cat += 1
    for k, v in EXTRA_SINGLE.items():
        if norm_key(k) not in {norm_key(x) for x in tbl}:
            tbl[k] = v
            n_cat += 1
    if CAT not in names:
        names.append(CAT)
    total = sum(len(v) for k, v in tgt.items()
                if not k.startswith("_") and isinstance(v, dict))
    tgt.setdefault("_meta", {})["total_entries"] = total
    print("%s: 本轮新增 %d，分类总数 %d，_meta.total_entries=%d"
          % (os.path.basename(tgt_path_local if False else "")
             or "json", n_cat, len(tbl), total))
json.dump(tb, open(tb_path, "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, sort_keys=False)
json.dump(gb, open(gb_path, "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, sort_keys=False)

# 候选留痕
stamp = time.strftime("%Y%m%d")
csv = os.path.join(ROOT, "audit-reports", "CHS_HARVEST_%s.csv" % stamp)
with open(csv, "w", encoding="utf-8-sig", newline="") as f:
    f.write("src,zh,sid,file\n")
    for src, (zh_v, sid, fname) in sorted(pairs.items()):
        f.write("%s,%s,%s,%s\n" % (src.replace(",", "，"),
                                   str(zh_v).replace(",", "，"),
                                   sid, fname))
print("留痕:", csv)
