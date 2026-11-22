# -*- coding: utf-8 -*-
"""从人工汉化补丁收割术语对，扩充随包术语库。

用途：AREA / NS7 等 MOD 的人工汉化包是最权威的译名语料。本工具抽取
「源文为术语形态的短条目」（人名/地名/物品/界面标签），按源文件路径
归类进术语库对应分类；只新增词库没有的键，已有键的译法冲突写入
审计报告供人工复核，不自动覆盖。

用法:
    python tools/harvest_termbase_from_human.py            # 采集并报告
    python tools/harvest_termbase_from_human.py --apply    # 写入词库
数据源（内置）:
    AREA  : aera/gamedata(text) -> aera/CHS/gamedata(text)   eng->中文
    NS7   : cccc/gamedata.sq_base_configs -> NS7 9月6日汉化补丁  rus->中文
"""
import glob
import html
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ZH = re.compile(r"[\u4e00-\u9fff]")

# 源文件路径 -> 术语库分类（按子路径关键字，取第一个命中）
CAT_MAP = [
    ("character_name", "人物与军衔"),
    ("mapspots", "地点与区域"),
    ("items", "物品·武器·装备·神器"),
    ("weapons", "物品·武器·装备·神器"),
    ("encyclopedia", "百科与术语"),
    ("ui", "界面与提示"),
    ("tips", "界面与提示"),
    ("tasks", "任务"),
    ("news", "新闻·对话·其他"),
    ("dialogs", "新闻·对话·其他"),
    ("other", "新闻·对话·其他"),
]

# 语料源: (源文目录, 译文目录, 说明, 权威级)
# 权威级：2 = 官方人工译文，冲突时**覆盖**词库旧值（用户确认以 NS7 为准）；
#         1 = 仅补缺（词库已有译法不覆盖）。
# 注意：NS7 本体 gamedata 是俄文原文；cccc 解包数据已应用人工补丁（<rus> 即中文），
# 不能作为"原文"源。
SOURCES = [
    (r"C:/Users/Admin/Desktop/aera/gamedata/configs/text",
     r"C:/Users/Admin/Desktop/aera/CHS/gamedata/configs/text",
     "AREA 人工汉化（eng→中文）", 1),
    (r"C:/game/NS7/gamedata/configs/text",
     r"C:/game/NS7/MODS/NS OGSR 2026年9月6日版本汉化补丁/gamedata/configs/text",
     "NS7 9月6日官方汉化（rus→中文）", 2),
]


def dec(path):
    raw = open(path, "rb").read()
    for e in ("utf-8-sig", "utf-8", "cp1251", "gbk"):
        try:
            return raw.decode(e)
        except Exception:
            pass
    return raw.decode("cp1251", "ignore")


def ents(path):
    """id -> (rus或eng或text 的源文)"""
    t = dec(path)
    d = {}
    for mm in re.finditer(
            r'<string\b[^>]*\bid=["\']([^"\']+)["\'][^>]*>(.*?)</string>',
            t, re.S):
        body = mm.group(2)
        src = ""
        for tg in ("rus", "eng", "text"):
            m2 = re.search(r"<%s\b[^>]*>(.*?)</%s>" % (tg, tg), body, re.S)
            if m2:
                src = m2.group(1)
                break
        d[mm.group(1)] = src
    return d


def term_like(s):
    """术语形态：短、词数少。长句/对话不是术语。"""
    s = s.strip()
    if s.startswith("_"):
        return False   # 运行时 collect 跳过下划线键，收进来就是死条目
    if not s or len(s) > 40:
        return False
    if len(re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", s)) > 4:
        return False
    # ★ T63 数据卫生：>=5 个 token（含数字/型号）的长串是描述不是术语，
    # 描述进硬层会被 canon 强制占位符化。
    if len(s.split()) >= 5:
        return False
    # ★ 含引号的短语键会遮蔽其组件术语的查找（实测 КСП "Автомобилист"
    # 遮蔽 Автомобилист，导致残留修复失效）——短语不进词库。
    if any(q in s for q in "\"'«»“”‘’"):
        return False
    return True


def clean(s):
    """解码 HTML 实体（&#xA0; 等），去首尾空白。"""
    return html.unescape(s or "").replace("\u00a0", " ").strip()


def is_fragment(s):
    """小写开头的西文片段 = 句中截断，不是术语。"""
    return bool(re.match(r"^[a-zа-яё]", s.strip()))


def index_text_files(root):
    """root 下所有 text/*.xml，按 'text' 之后的相对路径索引。"""
    out = {}
    for p in glob.glob(root + "/**/*.xml", recursive=True):
        low = p.replace("\\", "/").lower()
        i = low.rfind("/text/")
        if i < 0:
            continue
        out[p[i + 1:]] = p          # 'text/xxx/yyy.xml' -> full path
    return out


def harvest(src_root, zh_root, auth=1):
    """返回 (cat, src, 权威级) -> set(zh)。"""
    src_idx = index_text_files(src_root)
    zh_idx = index_text_files(zh_root)
    pairs = defaultdict(set)
    for rel, zp in zh_idx.items():
        sp = src_idx.get(rel)
        if not sp:
            continue
        cat = None
        low = rel.lower()
        for pat, c in CAT_MAP:
            if pat in low:
                cat = c
                break
        if not cat:
            continue
        S = ents(sp)
        Z = ents(zp)
        for k, zv in Z.items():
            sv = clean(S.get(k, ""))
            zv = clean(zv)
            if not sv or not zv:
                continue
            if not term_like(sv):
                continue
            if is_fragment(sv):
                continue
            if not ZH.search(zv):
                continue
            if sv == zv:
                continue
            pairs[(cat, sv, auth)].add(zv)
    return pairs


def load_termbase(path):
    return json.load(open(path, encoding="utf-8"))


def flatten(d):
    flat = {}
    for cat, dd in d.items():
        if isinstance(dd, dict) and cat != "_meta":
            for k, v in dd.items():
                flat[k] = v
    return flat


def main():
    apply = "--apply" in sys.argv
    all_pairs = defaultdict(set)      # (cat,src) -> set(zh)
    auth_of = {}                      # (cat,src) -> 最高权威级
    for src_root, zh_root, note, auth in SOURCES:
        pairs = harvest(src_root, zh_root)
        uniq = sum(1 for v in pairs.values() if len(v) == 1)
        print("%s：术语对 %d（唯一译名 %d，多译名 %d）"
              % (note, len(pairs), uniq, len(pairs) - uniq))
        for k, v in pairs.items():
            all_pairs[k] |= v
            if auth >= auth_of.get(k, 0):
                auth_of[k] = auth

    # 键规整回 (cat, src)：auth 只用于覆盖优先级，不进入词库键
    def _k2(k):
        return (k[0], k[1])
    uniq = {_k2(k): next(iter(v)) for k, v in all_pairs.items()
            if len(v) == 1}
    multi = {_k2(k): sorted(v) for k, v in all_pairs.items() if len(v) > 1}

    tb_path = os.path.join(ROOT, "plugins", "ai_translate",
                           "assets", "stalker_termbase.json")
    gb_path = os.path.join(ROOT, "plugins", "ai_translate",
                           "assets", "glossary_builtin.json")
    tb = load_termbase(tb_path)
    flat = flatten(tb)

    # ★ 权威级 2（NS7 官方人工译文）的冲突直接覆盖词库旧值；
    # 权威级 1（AREA）只补缺。覆盖动作记录进冲突报告。
    new = {k: v for k, v in uniq.items() if k[1] not in flat}
    override = {k: (v, flat[k[1]]) for k, v in uniq.items()
                if k[1] in flat and flat[k[1]] != v
                and auth_of.get(k, 0) >= 2}
    conflict = {k: (v, flat[k[1]]) for k, v in uniq.items()
                if k[1] in flat and flat[k[1]] != v
                and auth_of.get(k, 0) < 2}

    print("\n合并唯一术语对 %d；词库新增 %d；NS7 覆盖 %d；其余冲突 %d（留审）"
          % (len(uniq), len(new), len(override), len(conflict)))
    from collections import Counter
    cc = Counter(k[0] for k in new)
    print("新增分类分布:", dict(cc.most_common()))

    if multi:
        print("多译名冲突 %d 组（跳过）：" % len(multi))
        for k, v in sorted(multi.items())[:10]:
            print("   %s -> %s" % (k[1], v))

    report = os.path.join(ROOT, "audit-reports", "HARVEST_CONFLICTS.md")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8", newline="\n") as f:
        f.write("# 人工译文收割：译法冲突清单\n\n")
        f.write("## NS7 官方译文覆盖（已写入词库）\n\n")
        f.write("| 分类 | 源文 | 词库旧译 | NS7 译名 |\n|---|---|---|---|\n")
        for (cat, src), (human, old) in sorted(override.items()):
            f.write("| %s | %s | %s | %s |\n" % (cat, src, old, human))
        f.write("\n## 其余冲突（低权威级，未覆盖，留人工复核）\n\n")
        f.write("| 分类 | 源文 | 词库现译 | 人工译名 |\n|---|---|---|---|\n")
        for (cat, src), (human, old) in sorted(conflict.items()):
            f.write("| %s | %s | %s | %s |\n" % (cat, src, old, human))
        f.write("\n## 多译名（同一源文人工译法不一，全部跳过）\n\n")
        for k, v in sorted(multi.items()):
            f.write("- %s -> %s\n" % (k[1], v))
    print("冲突报告：%s" % report)

    if not apply:
        print("\n（预览模式；加 --apply 写入词库）")
        return 0

    for (cat, src), entry in list(new.items()) + list(override.items()):
        v = entry[0] if isinstance(entry, tuple) else entry
        tbl = tb.setdefault(cat, {})
        if tbl.get(src) != v:
            tbl[src] = v
    for cat in set(k[0] for k in new):
        tb["_meta"]["categories"][cat] = len(tb.get(cat, {}))
    tb["_meta"]["total_entries"] = sum(
        len(v) for k, v in tb.items() if k != "_meta" and isinstance(v, dict))
    json.dump(tb, open(tb_path, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))

    gb = load_termbase(gb_path)
    n = 0
    for (cat, src), entry in list(new.items()) + list(override.items()):
        v = entry[0] if isinstance(entry, tuple) else entry
        if gb.get(src) != v:
            gb[src] = v
            n += 1
    json.dump(gb, open(gb_path, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print("已写入：%s（total %d）/ builtin（total %d，+%d）"
          % (tb_path, tb["_meta"]["total_entries"], len(gb), n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
