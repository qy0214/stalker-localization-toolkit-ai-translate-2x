# -*- coding: utf-8 -*-
"""全库错误翻译清理（用户指令 2026-11-23：错的就不要存在）。

确定性错误判定（可机械复核，全部逐条留痕）：
  1 空译文（源非空）
  2 译文=原文（norm 口径，非技术层且含实词——纯型号/缩写保留是正确的）
  3 译文无中文（非技术层、非 PDA/КПК 类、源含实词）
  4 西里尔残留（源不含西里尔而译文含——未翻完；源含西里尔的保留，
    如改装槽位 [А]/[НАТО] 标记）
  5 语义级错译：
    a) 载具名词误植——译文含坦克/直升机等，源不含任何载具词根
       （верт/helicopter/tank/БТР/самол/корабл... 豁免 вертушка 类俚语）
    b) 型号漂移——源/译双方都提取到设计型号 token 且交集为空
       （M433→M406 类；design_tokens 西里尔转拉丁比对）
  6 纯符号译文（源有实词）

删除全部留痕：报告 audit-reports/PURGE_20261123.md +
备份 audit-reports/purged_backup_20261123.json（可整包回滚）。
用法: python tools/purge_bad_translations.py [--apply]
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from harvest_termbase_from_human import (  # noqa: E402
    _norm_key, garbage_translation, _SRC_VEHICLE_RE)

TB = os.path.join(ROOT, "plugins", "ai_translate", "assets", "stalker_termbase.json")
GB = os.path.join(ROOT, "plugins", "ai_translate", "assets", "glossary_builtin.json")
REPORT = os.path.join(ROOT, "audit-reports", "PURGE_20261123.md")
BACKUP = os.path.join(ROOT, "audit-reports", "purged_backup_20261123.json")

ZH = re.compile(r"[\u4e00-\u9fff]")
CYR = re.compile(r"[А-Яа-яЁё]")
VEH_ZH = re.compile(r"坦克|直升机|战斗机|轰炸机|驱逐舰|巡洋舰|潜艇|军舰|装甲车|航母|运输机|武装直升机")
VEH_SRC = re.compile(
    r"верт|helicopter|chopper|tank|танк|БТР|БРДМ|бтр|брдм|armored|брон|"
    r"корабл|ship|boat|самол|aircraft|plane|jet|Ми-|Mi-|Ан-|An-|Су-|Su-|"
    r"ЛА|la\b|airship|dirigib|дирижаб|грузовик|truck|boone|car\b|автомобил|"
    r"машина|vehicle|transport",
    re.I)
TECH_OK = ("PDA", "КПК", "ПДА")
REALWORD = re.compile(r"[A-Za-zА-Яа-яЁё\u4e00-\u9fff]{2,}")

# ★ 人工排除清单：别名/代号翻译，机械口径无法判定（2026-11-23 复核定稿）。
# Chaser 13/Tunder S14 是 SoC 武器的英文别名，中文按另一名称定名（温彻斯特
# M1300/雷电 14）——设计如此，非错译。Walker P9m→P99 是别名链（P9m 为
# Walther P99 的游戏别名，译名沿用 P99）。
KEEP_EXACT = {
    ("Chaser 13", "温彻斯特M1300"),
    ("Tunder S14", "雷电 14"),
    ("Walker P9m", "沃尔特 P99"),
    ("Chaser 13", "温彻斯特1300"),
}


def is_bad(cat, k, v):
    """返回 (级别, 原因) 或 None。"""
    k, v = str(k), str(v)
    if (k, v) in KEEP_EXACT:
        return None
    meta_layer = None
    if not v.strip():
        return ("错误", "空译文")
    if not REALWORD.search(k) and not ZH.search(v) and len(v) <= 3:
        return None                          # 双方皆符号（进度条等），保留
    if _norm_key(k) == _norm_key(v) and cat not in ("技术缩写",) \
            and k.upper() not in TECH_OK and len(k) > 3 \
            and REALWORD.search(k):
        return ("错误", "译文=原文（未翻）")
    if not ZH.search(v) and cat not in ("技术缩写",) \
            and k.upper() not in TECH_OK and REALWORD.search(k) \
            and len(v.strip()) > 1 and not v.strip().isdigit():
        return ("错误", "译文无中文")
    if CYR.search(v) and not CYR.search(k):
        return ("错误", "译文西里尔残留")
    # 载具误植（带词根豁免）——复用 harvest 修正版源侧正则（含 BRDM/heli 拉丁形态）
    if VEH_ZH.search(v) and not _SRC_VEHICLE_RE.search(k) \
            and cat != "技术缩写":
        return ("错误", "载具名词误植")
    # 型号漂移 / 其余垃圾口径（harvest 过滤器同款）
    if cat not in ("技术缩写",):
        g = garbage_translation(k, v)
        if g:
            return ("错误", "垃圾口径(%s)" % g)
    return None


def main():
    apply = "--apply" in sys.argv
    tb = json.load(open(TB, encoding="utf-8"))
    meta = tb["_meta"]
    purged = {}
    for cat, tbl in tb.items():
        if cat == "_meta" or not isinstance(tbl, dict):
            continue
        for k in list(tbl.keys()):
            r = is_bad(cat, k, tbl[k])
            if r:
                purged.setdefault(cat, {})[k] = {
                    "v": tbl[k], "reason": r[1]}
                del tbl[k]
    total = sum(len(x) for x in purged.values())
    print("删除 %d 条：" % total)
    for cat, rows in purged.items():
        print("  %s: %d" % (cat, len(rows)))
        for k, info in list(rows.items())[:12]:
            print("    [%s] %r -> %r" % (info["reason"], k[:56], info["v"][:48]))
        if len(rows) > 12:
            print("    ... 共 %d 条" % len(rows))
    if not apply:
        print("\n（预览模式；加 --apply 执行删除）")
        return 0

    # 重算 meta
    cats = meta.setdefault("categories", {})
    for cat in purged:
        cats[cat] = len(tb.get(cat, {}))
    meta["total_entries"] = sum(
        len(v) for k, v in tb.items() if k != "_meta" and isinstance(v, dict))
    json.dump(tb, open(TB, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    gb = json.load(open(GB, encoding="utf-8"))
    ngb = 0
    for cat, rows in purged.items():
        for k in rows:
            if str(k) in gb and gb[str(k)] == rows[k]["v"]:
                del gb[str(k)]
                ngb += 1
            elif str(k) in gb:
                ngb += 1
                del gb[str(k)]
    json.dump(gb, open(GB, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    json.dump(purged, open(BACKUP, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open(REPORT, "w", encoding="utf-8", newline="\n") as f:
        f.write("# 全库错误翻译清理（2026-11-23）\n\n")
        f.write("词库 %d → %d 条；删除 %d 条；builtin 同步删 %d 条。\n\n"
                % (meta["total_entries"] + total, meta["total_entries"],
                   total, ngb))
        f.write("判定口径：空译文/译文=原文/译文无中文/西里尔残留/"
                "载具名词误植（载具词根豁免）/型号漂移/纯符号译文。\n")
        f.write("备份（可整包回滚）：purged_backup_20261123.json\n\n")
        for cat, rows in purged.items():
            f.write("## %s（%d 条）\n\n| 原因 | 原文 | 译文 |\n|---|---|---|\n"
                    % (cat, len(rows)))
            for k, info in rows.items():
                f.write("| %s | `%s` | `%s` |\n"
                        % (info["reason"], str(k).replace("|", "\\|")[:70],
                           str(info["v"]).replace("|", "\\|")[:56]))
    print("已写库：termbase %d / builtin 删 %d" % (meta["total_entries"], ngb))
    print("备份：%s\n报告：%s" % (BACKUP, REPORT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
