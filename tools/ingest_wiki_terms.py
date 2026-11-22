# -*- coding: utf-8 -*-
"""bilibili wiki《潜行者专有名词》表入库（rus+eng → 简中）。

来源：wiki.biligame.com/gscstalker 潜行者专有名词（约定成俗译名，
俄/英/简中对照）。解析产物 wiki_pairs.json -> 术语库。

守卫与 harvest_termbase_from_human 同口径：
norm 撞键跳过、canon 词干冲突跳过、占位符守卫、垃圾译文过滤。
wiki 译文含"、"变体合并的不收。
"""
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harvest_termbase_from_human import (  # noqa: E402
    _norm_key, canon_stem_group, garbage_translation, PH, ROOT)

TB_PATH = os.path.join(ROOT, "plugins", "ai_translate",
                       "assets", "stalker_termbase.json")
GB_PATH = os.path.join(ROOT, "plugins", "ai_translate",
                       "assets", "glossary_builtin.json")
PAIRS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "tmp", "web", "wiki_pairs.json")

SEC_CAT = {
    "二.神器": "物品·武器·装备·神器",
    "一.游戏标题": "百科与术语",
}


def flatten(d):
    flat = {}
    for cat, dd in d.items():
        if cat != "_meta" and isinstance(dd, dict):
            flat.update({k: v for k, v in dd.items()})
    return flat


def split_rus(field):
    """wiki 俄文格 = 「西里尔原文 转写」混排：只取西里尔词序列。

    全拉丁字段（如 WarCraft 2000）原样返回。
    """
    toks = field.split()
    if not re.search(r"[А-Яа-яЁё]", field):
        return field
    out = []
    for t in toks:
        if re.search(r"[A-Za-z]", t) and not re.search(r"[А-Яа-яЁё]", t):
            break
        out.append(t)
    return " ".join(out) if out else field


def main():
    apply = "--apply" in sys.argv
    tb = json.load(open(TB_PATH, encoding="utf-8"))
    flat = flatten(tb)
    pairs = json.load(open(PAIRS, encoding="utf-8"))

    canon_cats = tb["_meta"]["layers"]["canon"]
    stem_grp = {}
    for c in canon_cats:
        for k, v in (tb.get(c) or {}).items():
            g = canon_stem_group(k)
            if g:
                stem_grp.setdefault(g, set()).add(re.sub(r"\s+", "", str(v)))

    flat_low = {}
    for k, v in flat.items():
        flat_low.setdefault(_norm_key(k), (k, v))

    new = {}
    drop_norm, drop_stem, drop_var, drop_garbage = [], [], [], []
    for sec, rus, eng, zh, _translit in pairs:
        cat = SEC_CAT.get(sec, "百科与术语")
        rus = split_rus(rus)
        for src in (rus, eng):
            if not src or not re.search(r"[A-Za-zА-Яа-яЁё]", src):
                continue
            if src == zh:
                continue
            if not (4 <= len(src) <= 60):
                continue
            if "、" in zh:                      # 变体合并，语义不确定
                drop_var.append((src, zh))
                continue
            if re.search(r"[（）()]", zh):       # 括号注记噪声（版本/年份说明）
                drop_var.append((src, zh))
                continue
            if sorted(PH.findall(src)) != sorted(PH.findall(zh)):
                continue
            if garbage_translation(src, zh):
                drop_garbage.append((src, zh))
                continue
            if src in flat:
                continue
            low = _norm_key(src)
            if low in flat_low or low in {_norm_key(x) for x in new}:
                drop_norm.append((src, zh))
                continue
            if cat in canon_cats:
                g = canon_stem_group(src)
                if g and g in stem_grp and \
                        re.sub(r"\s+", "", zh) not in stem_grp[g]:
                    drop_stem.append((src, zh))
                    continue
                if g:
                    stem_grp.setdefault(g, set()).add(re.sub(r"\s+", "", zh))
            new[(cat, src)] = zh

    print("wiki 对：%d；入库：%d；norm撞键 %d；词干 %d；变体 %d；垃圾 %d"
          % (len(pairs), len(new), len(drop_norm), len(drop_stem),
             len(drop_var), len(drop_garbage)))
    for (cat, src), v in sorted(new.items()):
        print("  %s | %s -> %s" % (cat, src, v))
    if not apply:
        print("（预览模式；加 --apply 写入）")
        return 0

    for (cat, src), v in new.items():
        tb.setdefault(cat, {})[src] = v
    cats = tb["_meta"].setdefault("categories", {})
    for cat in {c for c, _s in new}:
        cats[cat] = len(tb.get(cat, {}))
    tb["_meta"]["total_entries"] = sum(
        len(v) for k, v in tb.items() if k != "_meta" and isinstance(v, dict))
    json.dump(tb, open(TB_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    gb = json.load(open(GB_PATH, encoding="utf-8"))
    n = 0
    for (_cat, src), v in new.items():
        if gb.get(src) != v:
            gb[src] = v
            n += 1
    json.dump(gb, open(GB_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print("已写入 termbase（total %d）/ builtin +%d"
          % (tb["_meta"]["total_entries"], n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
