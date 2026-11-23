# -*- coding: utf-8 -*-
"""软层高频词升硬 + 错译修正（2026-11-24）。

做两件事（默认只打印计划，--apply 才写盘）：
  1) 错译修正：8 个值改写 + 1 条删除（S.T.A.L.K.E.R.→潜行者）
  2) 升硬：51 个高频专名/固定词从 reference 软层**移动**到新分类「高频硬词」，
     并把分类挂进 _meta.layers.canon（移动而非复制，避免同键双分类）

写盘范围：plugins/ai_translate/assets/{stalker_termbase,glossary_builtin}.json
          tools/stalker_termbase.json（仓库内镜像）
用法：
  python tools/promote_soft_terms.py          # 预览
  python tools/promote_soft_terms.py --apply  # 写入
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "plugins", "ai_translate", "assets")
TB = os.path.join(ASSETS, "stalker_termbase.json")
GB = os.path.join(ASSETS, "glossary_builtin.json")
TOOLS_TB = os.path.join(ROOT, "tools", "stalker_termbase.json")
CAT = "高频硬词"

# ── 1) 错译修正（key -> 新值；None = 删除） ──
FIXES = {
    "Russian": "俄语",                    # 原“中文”
    "НИИ": "研究所",                      # 原“南部建筑群”
    "Director": "主管",                   # 原“导演”
    "Darkscape": "幽暗地带",              # 原“峡谷”（人工对照层为幽暗地带）
    "Исполнитель желаний": "许愿石",      # 原“蒙娜丽许愿石”
    "Крота": "鼹鼠",                      # 原“莫尔”（主格 Крот→鼹鼠）
    "Шурупа": "螺丝",                     # 原“拧巴”（主格 Шуруп→螺丝）
    "Стрелка": "史特列洛克",              # 原“神射手”（主格 Стрелок→史特列洛克）
    "Стрелком": "史特列洛克",
    "Стрелку": "史特列洛克",
    "S.T.A.L.K.E.R.": None,               # 缩写不应译成“潜行者”，删除
}

# ── 2) 升硬名单（词条 -> 期望的现有译名，作漂移校验） ──
PROMOTE = {
    # 地名/区域（含俄语格变）
    "Зона": "禁区",
    "Припяти": "普里皮亚季",
    "Болотах": "沼泽",
    "Кордоне": "封锁线", "Кордона": "封锁线",
    "Агропроме": "农业研究所", "Агропрома": "农业研究所",
    "Янтаре": "杨塔", "Янтаря": "杨塔",
    "Радаре": "雷达", "Радара": "雷达",
    "Юпитере": "木星", "Юпитера": "木星",
    "Лощине": "峡谷", "Лощину": "峡谷",
    "Лощина": "峡谷", "Лощины": "峡谷",
    "Red Forest": "红森林",
    "Eastern Pripyat": "普里皮亚季东区",
    "Central Pripyat": "普里皮亚季中心区",
    "Agroprom Underground": "农业研究所地下设施",
    "Old Village": "旧村庄",
    "Forgotten Forest": "遗忘森林",
    "Undiscovered Land": "未探索之地",
    "Unexplored Land": "未探索之地",
    "Jupiter Underpass": "木星立交桥",
    "Варлаб": "瓦拉布", "War Lab": "瓦拉布",
    "Лиманске": "利曼斯克",
    "Kopachi": "科帕奇",
    # 设施/机组
    "CNPP-1": "切尔诺贝利核电站1号机组",
    "CNPP-2": "切尔诺贝利核电站2号机组",
    "ЧАЭС-1": "切尔诺贝利核电站1号机组",
    "ЧАЭС-2": "切尔诺贝利核电站2号机组",
    "Lab X-8": "X-8实验室", "Lab X-10": "X-10实验室",
    "Lab X-16": "X-16实验室", "Lab X-18": "X-18实验室",
    "Laboratory X-10": "X-10实验室", "Laboratory X-16": "X-16实验室",
    # 物品/装备/界面
    "PPSh-41": "PPSH-41冲锋枪",
    "seva suit": "塞瓦防护服",
    "Оазис": "“绿洲”",
    "КПП": "检查站",
    "Statistics": "统计",
    "General Information": "概况",
    "Cancel": "取消",
    "Entries encrypted": "记录已加密",
    "Записи зашифрованы": "记录已加密",
    "Найти тайник": "找到藏匿点",
    "Рассказать о тайнике": "汇报藏匿点情况",
}


def find_key(tb, key):
    for cat, tbl in tb.items():
        if cat.startswith("_") or not isinstance(tbl, dict):
            continue
        if key in tbl:
            return cat, tbl[key]
    return None, None


def main():
    apply = "--apply" in sys.argv
    tb = json.load(open(TB, encoding="utf-8"))
    gb = json.load(open(GB, encoding="utf-8"))
    errors = []

    # 1) 修正
    print("== 错译修正 ==")
    for key, new in FIXES.items():
        cat, old = find_key(tb, key)
        if cat is None:
            errors.append("找不到词条：%s" % key)
            continue
        if new is None:
            tb[cat].pop(key, None)
            gb.pop(key, None)
            print("  删除 [%s] %r（原 %r）" % (cat, key, old))
        else:
            tb[cat][key] = new
            gb[key] = new
            print("  %r: %r -> %r [%s]" % (key, old, new, cat))

    # 2) 升硬（移动）
    print("\n== 升硬（移入「%s」）==" % CAT)
    moved = 0
    tbl = tb.setdefault(CAT, {})
    for key, expect in PROMOTE.items():
        cat, old = find_key(tb, key)
        if cat is None:
            errors.append("找不到待升词条：%s" % key)
            continue
        if cat == CAT:
            errors.append("已在硬词分类：%s" % key)
            continue
        if str(old).strip() != expect:
            errors.append("译名漂移：%s 现为 %r，期望 %r" % (key, old, expect))
            continue
        tbl[key] = old
        tb[cat].pop(key, None)
        gb.pop(key, None)          # gb 里是扁平条目，先删再在分类里补
        moved += 1
        print("  %-22s %-12s  <- %s" % (key, old, cat))

    # gb 的「高频硬词」分类（gb 允许混合结构）
    gb[CAT] = {k: str(v) for k, v in tbl.items()}

    # 3) 元数据
    cats = [c for c in tb if not c.startswith("_") and isinstance(tb[c], dict)]
    tb["_meta"]["categories"] = {c: len(tb[c]) for c in cats}
    tb["_meta"]["total_entries"] = sum(len(tb[c]) for c in cats)
    layers = tb["_meta"].setdefault("layers", {})
    canon = layers.setdefault("canon", [])
    if CAT not in canon:
        canon.append(CAT)

    print("\n== 结果 ==")
    print("  移动 %d 条；total_entries=%d；分类数=%d"
          % (moved, tb["_meta"]["total_entries"], len(cats)))
    print("  canon 层: %s" % canon)
    print("  高频硬词 %d 条" % len(tbl))
    if errors:
        print("\n!! 校验失败 %d 条，未写盘：" % len(errors))
        for e in errors:
            print("   - " + e)
        return 1
    if not apply:
        print("\n（预览模式，未写盘；加 --apply 执行）")
        return 0

    def dump(path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    dump(TB, tb)
    dump(GB, gb)
    if os.path.isfile(TOOLS_TB):
        dump(TOOLS_TB, tb)
        print("已同步 tools/stalker_termbase.json")
    print("已写入：%s\n        %s" % (TB, GB))
    return 0


if __name__ == "__main__":
    sys.exit(main())
