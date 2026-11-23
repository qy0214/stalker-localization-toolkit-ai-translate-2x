# -*- coding: utf-8 -*-
"""交叉审计修复（2026-11-24，新方法）：同源冲突统一 + 中文标点规范化。

用法：
  python tools/fix_termbase_cross.py          # 预览
  python tools/fix_termbase_cross.py --apply  # 写入（2.x assets + 1.9.x + tools镜像 + 桌面副本）
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = [
    os.path.join(ROOT, "plugins", "ai_translate", "assets",
                 "stalker_termbase.json"),
    os.path.join(ROOT, "plugins", "ai_translate", "assets",
                 "glossary_builtin.json"),
    r"C:\STALKER-HH-2x\plugins\ai_translate_assets\stalker_termbase.json",
    r"C:\STALKER-HH-2x\plugins\ai_translate_assets\glossary_builtin.json",
    r"C:\STALKER-HH-2x\tools\stalker_termbase.json",
    r"C:\Users\Admin\Desktop\1\plugins\ai_translate\assets\stalker_termbase.json",
    r"C:\Users\Admin\Desktop\1\plugins\ai_translate\assets\glossary_builtin.json",
]
Q = "\"'«»“”‘’「」『』"
CJK = re.compile(r"[\u4e00-\u9fff]")

# 同源冲突统一（规范源小写 -> 目标译名，不含引号；引号风格沿用旧值）
CONFLICTS = {
    "loners": "独行者",
    "army": "军方",
    "kolobok": "科洛博克",
    "fanatic": "狂热者",
    "seriy": "灰",
    "skull": "骷髅",
    "chef": "大厨",
    "murk": "阴霾",
    "wolf": "狼",
    "cap": "帽子",
    "mama's beads": "妈妈的念珠",
    "wrenched": "扭曲物",
    "generator": "发电机",
    "battery": "电池",
    "izumrudnoye": "伊祖姆鲁德诺耶",
}
EXACT = {
    # 注：Установка экранирующего покрытия. 的句末「。」**不能加**——
    # 它与同干无句点键共用译名，加标点会被 strict 审计判 inflection_conflict
    #（2.0.22 已因同一原因回退过）。
    "Мамины бусы": "妈妈的念珠",
    "Мамины бусы из Карусели": "“旋转木马”的妈妈念珠",
    "Мамины бусы из Трамплина": "弹簧妈妈念珠",
}


def ns(s):
    return re.sub(r"\s+", " ", str(s).strip().strip(Q)).casefold()


def wrap_like(old, new):
    s = str(old).strip()
    pairs = (("“", "”"), ("「", "」"), ("『", "』"), ("«", "»"))
    for a, b in pairs:
        if s.startswith(a) and s.endswith(b):
            return a + new + b
    return new


def norm_punct(v):
    s = str(v)
    if not CJK.search(s) or re.search(r"[A-Za-z0-9]", s):
        return s
    # 幂等：ASCII 省略号与连续中文省略号统一收敛为单个「……」
    s = re.sub(r"\.{2,}", "……", s)
    s = re.sub(r"…{2,}", "……", s)
    s = s.replace("……", "\x00")
    for a, b in ((",", "，"), ("!", "！"), ("?", "？"), (";", "；"),
                 (":", "："), (".", "。")):
        s = s.replace(a, b)
    s = s.replace("\x00", "……")
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    return s


def fix_tree(data, stats):
    for cat, tbl in data.items():
        if cat.startswith("_") or not isinstance(tbl, dict):
            continue
        for k, v in list(tbl.items()):
            key = str(k)
            nk = ns(key)
            if key in EXACT:
                if str(v) != EXACT[key]:
                    stats["exact"] += 1
                    tbl[k] = EXACT[key]
                continue
            if nk in CONFLICTS:
                want = CONFLICTS[nk]
                new = wrap_like(v, want)
                if str(v).strip() != new:
                    stats["conflict"] += 1
                    tbl[k] = new
                continue
            nv = norm_punct(v)
            if nv != str(v):
                stats["punct"] += 1
                tbl[k] = nv
    # gb 顶层还可能有扁平条目
    for k, v in list(data.items()):
        if k.startswith("_") or isinstance(v, dict):
            continue
        if str(k) in EXACT:
            if str(v) != EXACT[str(k)]:
                stats["exact"] += 1
                data[k] = EXACT[str(k)]
            continue
        nk = ns(k)
        if nk in CONFLICTS:
            new = wrap_like(v, CONFLICTS[nk])
            if str(v).strip() != new:
                stats["conflict"] += 1
                data[k] = new
        else:
            nv = norm_punct(v)
            if nv != str(v):
                stats["punct"] += 1
                data[k] = nv


def main():
    apply_ = "--apply" in sys.argv
    total = {"conflict": 0, "punct": 0, "exact": 0}
    for path in TARGETS:
        if not os.path.isfile(path):
            print("  跳过（不存在）:", path)
            continue
        data = json.load(open(path, encoding="utf-8"))
        stats = {"conflict": 0, "punct": 0, "exact": 0}
        fix_tree(data, stats)
        print("%-58s 冲突 %3d / 标点 %4d / 定点 %d"
              % (os.path.basename(os.path.dirname(path)) + "/" +
                 os.path.basename(path), stats["conflict"],
                 stats["punct"], stats["exact"]))
        for k in total:
            total[k] += stats[k]
        # 重算 meta（仅 tb）
        if path.endswith("stalker_termbase.json"):
            cats = [c for c in data
                    if not c.startswith("_") and isinstance(data[c], dict)]
            data["_meta"]["categories"] = {c: len(data[c]) for c in cats}
            data["_meta"]["total_entries"] = sum(len(data[c]) for c in cats)
        if apply_:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False,
                          separators=(",", ":"))
    print("\n合计：同源冲突 %d / 标点规范化 %d / 定点 %d"
          % (total["conflict"], total["punct"], total["exact"]))
    if not apply_:
        print("（预览模式，未写盘；加 --apply 执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
