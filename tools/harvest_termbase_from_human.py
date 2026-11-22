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
    GA    : OGSR Gunslinger 人工汉化（corpus/gunslinger_rus|_eng，auth=1）
            原文由 xrkit_cli --unpack-package 从 configs.db 解包：
            --unpack-package "C:/game/OGSR_GA/OGSR Gunslinger Addon/gamedata/configs.db"
                <输出根> --only <清单>   （清单=configs.db dump 中 config/text/* 文件行）
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

# 语料源: (源文目录, 译文目录, 说明, 权威级[, 分类覆盖])
# 权威级：2 = 官方人工译文，冲突时**覆盖**词库旧值（用户确认以 NS7 为准）；
#         1 = 仅补缺（词库已有译法不覆盖）。
# 分类覆盖：非空字符串时该源全部条目进这一个分类（软参考层），
#           不按 CAT_MAP 分散进硬词分类。
# 注意：NS7 本体 gamedata 是俄文原文；cccc 解包数据已应用人工补丁（<rus> 即中文），
# 不能作为"原文"源。
SOURCES = [
    (r"C:/Users/Admin/Desktop/aera/gamedata/configs/text",
     r"C:/Users/Admin/Desktop/aera/CHS/gamedata/configs/text",
     "AREA 人工汉化（eng→中文）", 1),
    (r"C:/game/NS7/gamedata/configs/text",
     r"C:/game/NS7/MODS/NS OGSR 2026年9月6日版本汉化补丁/gamedata/configs/text",
     "NS7 9月6日官方汉化（rus→中文）", 2),
    # Gunslinger Addon 人工汉化：中文放在 eng 槽位，原文（rus/eng）从
    # configs.db 解包后按槽位分目录存放。人工水准但抽查发现存在语义级
    # 错译（榴弹→"重型坦克"、手枪→"直升机"），全部进"Gunslinger 参考"
    # 软层（仅精确整串匹配兜底，不进硬词、不注入 prompt、不写 builtin）。
    (r"C:/STALKER-AiTranslate-2x/corpus/gunslinger_rus/text",
     r"C:/game/OGSR_GA/OGSR Gunslinger Addon/MODS/汉化/gamedata/config/text/eng",
     "OGSR Gunslinger 人工汉化（rus→中文）", 1, "Gunslinger 参考"),
    (r"C:/STALKER-AiTranslate-2x/corpus/gunslinger_eng/text",
     r"C:/game/OGSR_GA/OGSR Gunslinger Addon/MODS/汉化/gamedata/config/text/eng",
     "OGSR Gunslinger 人工汉化（eng→中文）", 1, "Gunslinger 参考"),
    # CoP 官方中文（主机版官中 + 贴吧汉化整合，PastaAnchovy 存档）。
    # rus 原文取自 CoP 1.6.02 补丁包 xpatch_02.db，eng 取自 xenglish.db
    # （均由 xrkit_cli --unpack-package 解包）。官方译名质量最高，
    # 但按用户口径 NS7 冲突仍以 NS7 为准，故 auth=1 仅补缺。
    (r"C:/STALKER-AiTranslate-2x/corpus/cop_rus",
     r"C:/STALKER-AiTranslate-2x/corpus/cop_chs",
     "CoP 官方中文（rus→中文）", 1),
    (r"C:/STALKER-AiTranslate-2x/corpus/cop_eng",
     r"C:/STALKER-AiTranslate-2x/corpus/cop_chs",
     "CoP 官方中文（eng→中文）", 1),
    # NS2016 汉化（NS 系 SoC 版旧汉化，与 NS7 同 ID 体系）。
    # chs 槽单语文件，原文复用 NS7 的 rus 文本按 ID 配对。
    (r"C:/game/NS7/gamedata/configs/text",
     r"D:/CN_Pack_Generator/NS2016/gamedata/config/text/chs",
     "NS2016 汉化（rus→中文，NS7 rus 配对）", 1),
    # GAMMA 汉化组 GSCTP 简中 × Anomaly 1.5.3 eng 原文（corpus 内持久化
    # 副本，原取自 Tosox 仓库 sparse clone）。GAMMA 为 eng 基座，同 ID 配对。
    (r"C:/STALKER-AiTranslate-2x/corpus/anomaly_eng",
     r"C:/STALKER-AiTranslate-2x/corpus/gamma_chs/Simplified/gamedata/configs/text/chs",
     "GAMMA 汉化组（eng→中文，Anomaly eng 配对）", 1, "GAMMA 参考"),
    # Anomaly rus 槽（官方俄文本地化，NS7-MT-TEST 解包）× GAMMA chs：
    # 同 ID 体系，为 Anomaly 内容补 rus→中文 对。
    (r"C:/game/NS7-MT-TEST/extract/anomaly_full/configs/text/rus",
     r"C:/STALKER-AiTranslate-2x/corpus/gamma_chs/Simplified/gamedata/configs/text/chs",
     "GAMMA 汉化组（rus→中文，Anomaly rus 配对）", 1, "GAMMA 参考"),
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
    """root 下所有 text/*.xml，按 'text' 之后的相对路径索引（精确键）。

    根目录本身不含 /text/ 段的语料（如 corpus/cop_chs 直接放 xml），
    退化为按文件名索引。
    """
    out = {}
    for p in glob.glob(root + "/**/*.xml", recursive=True):
        p = p.replace("\\", "/")
        low = p.lower()
        i = low.rfind("/text/")
        if i < 0:
            out[p.rsplit("/", 1)[-1]] = p      # 'xxx.xml' -> full path
        else:
            out[p[i + 1:]] = p          # 'text/xxx/yyy.xml' -> full path
    return out


def make_alias_index(idx):
    """去语言槽位的兜底索引：'text/eng/x.xml'/'text/rus/x.xml'/'x.xml' -> 'x.xml'。

    精确键（语言槽一致）优先命中；兜底用于跨槽位配对
    （Gunslinger 汉化把中文放 eng 槽、原文在独立解包目录）。
    """
    alias = {}
    for rel, p in idx.items():
        a = strip_lang(rel)
        alias.setdefault(a, p)
    # 文件名兜底：源/译文两侧目录结构不一致（如 NS7 有分类子目录、
    # NS2016 chs 直接平铺）时按 basename 配对。
    for rel, p in idx.items():
        base = rel.rsplit("/", 1)[-1].lower()
        alias.setdefault(base, p)
    return alias


def _norm_key(s):
    """与 audit_termbase.norm 同口径的键归一（NFKC、casefold、ё→е、
    引号/破折号折叠、空白归一、剥首尾引号）。撞键即死条目。"""
    import unicodedata
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = s.casefold().replace("ё", "е")
    s = s.replace("«", '"').replace("»", '"').replace("“", '"') \
         .replace("”", '"').replace("‘", "'").replace("’", "'")
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s).strip().strip("\"'").strip()
    return s


def strip_lang(rel):
    """精确 rel -> 去语言槽兜底键（'text/eng/x.xml'/'x.xml'/'text/x.xml' -> 'x.xml'）。"""
    a = re.sub(r"^(?:text/)?(?:eng|rus|chn|chs|cht|zh|ukr|pl|es|de|fr|it|spa)/",
               "", rel.lower())
    return re.sub(r"^text/", "", a)


# ★ 垃圾译文过滤器（低权威级语料的语义级错译拦截）
# GA 汉化抽查发现：榴弹译成"重型坦克"、手枪译成"直升机"、M433 译成 M406
# 这类错译若入词库，会在精确整串匹配时漏进成品。
# 物品/术语名里不可能出现的载具/大型兵器名词（译成它们=错译）：
_ZH_VEHICLE_RE = re.compile(
    r"坦克|直升机|战斗机|轰炸机|驱逐舰|巡洋舰|潜艇|军舰|装甲车|航母|火炮阵地")
# ★ 2026-11-23：源侧载具词根（俄/英/俚语）。此前载具误植检查只搜源文的
# **中文**载具词——俄/英文源（ВЕРТОЛЁТ/helicopter）的**正确翻译**全部被
# 误判错译（直升机飞行员、搜查直升机、вертушка→直升机 等数百条被错杀，
# GA/GAMMA 收割时已误滤一批）。载具名必须双语豁免。
_SRC_VEHICLE_RE = re.compile(
    r"верт|helicopter|chopper|heli\b|helis|tank|танк|БТР|БРДМ|бтр|брдм|"
    r"BRDM|BTR|armored|брон|корабл|ship\b|boat|самол|aircraft|plane|"
    r"Ми-|Mi-2|Mi-24|Ан-|Су-|дирижаб|грузовик|truck|автомобил|vehicle|"
    r"transport|бронемашин|вертушк|vertush", re.I)
# 西里尔字母 → 拉丁转写（型号比对用）：ПГ-7ВЛ ≈ PG-7VL
_CYR2LAT = str.maketrans({
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E",
    "Ж": "ZH", "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M",
    "Н": "N", "О": "O", "П": "P", "Р": "P", "С": "S", "Т": "T", "У": "U",
    "Ф": "F", "Х": "X", "Ц": "TS", "Ч": "CH", "Ш": "SH", "Щ": "SHCH",
    "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "YU", "Я": "YA"})


def _design_tokens(s):
    """抽取型号 token（西里尔转拉丁、去点号、小写），用于比对。

    ★ 2026-11-23 三处口径修正（IWP 清理预览暴露的误杀根因）：
      * mm/см 单位后缀剥离（Ствол 7.62 → 7.62mm 口径枪管）；
      * 整数 NxM 口径（fmj box (150x3) → FMJ弹药箱（150发x3））；
      * 中文量词剥离（150发x3 → 150x3）。
    """
def _design_tokens(s, merge_space=False):
    """抽取型号 token（西里尔转拉丁、去点号、小写），用于比对。

    ★ 2026-11-23 三处口径修正（IWP 清理预览暴露的误杀根因）：
      * mm/см 单位后缀剥离（Ствол 7.62 → 7.62mm 口径枪管）；
      * 整数 NxM 口径（fmj box (150x3) → FMJ弹药箱（150发x3））；
      * 中文量词剥离（150发x3 → 150x3）。
    merge_space=True 用于**译文侧**：空格分隔型号合并（.45 ACP→45ACP）、
    千分位含空格归一（15, 000→15000）——只对中文译文生效，源侧合并会
    产生 "10AND"/"3PROJECT" 类伪 token（10 and 2 roubles 案例）。
    """
    s = s.upper().translate(_CYR2LAT)
    # 中文量词/单位与拉丁单位后缀在 token 提取前剥离
    s = re.sub(r"(?<=\d)(?:发|枚|发装|_rounds?|rounds?|mm|cm|мм|см)(?=\b|[^A-Za-z0-9])",
               "", s, flags=re.I)
    # 150发x3 → 150X3：量词夹在数字与口径乘号之间时直接删除
    s = re.sub(r"(?<=\d)\s*(?:发|枚|发装)\s*(?=[xх×])", "", s, flags=re.I)
    if merge_space:
        # 千分位归一（含空格形态：15, 000 → 15000）
        s = re.sub(r"(\d),\s*(?=\d{3}\b)", r"\1", s)
        # 空格分隔型号合并（.45 ACP → 45ACP）
        s = re.sub(r"\b(\d{1,4})\s+(?=[A-Z]{2,}\b)", r"\1", s)
    else:
        s = re.sub(r"(\d),(?=\d{3}\b)", r"\1", s)
    toks = set()
    for m in re.finditer(r"[A-Z0-9]{2,}", s):
        t = m.group(0).replace(".", "").lower()
        if re.search(r"\d", t):
            # 前导零归一（0100→100；(0,100)/(0, 100) 两形态对齐；
            # 03svu→3svu 与源侧 03→3 对齐——token 内数字段统一去前导零）
            t = re.sub(r"\b0+(?=\d)", "", t)
            if t.isdigit():
                t = str(int(t))
            # 倍率 40x→4x（4.0x 断词形态主循环产生 0x 噪声，由口径正则补）
            t = re.sub(r"^([1-9])0(x)$", r"\1\2", t)
            toks.add(t)
    # 口径形 7.62x39 / 5.7x28 / 150x3 / 4.0x：点号去掉后统一
    for m in re.finditer(r"\d{1,3}[.,]?\d{0,2}\s*[xх×](?:\s*\d{1,3})?",
                         s, re.I):
        t = re.sub(r"\s|х|×", "x", m.group(0).lower()).replace(",", ".")
        t = t.replace(".", "")
        t = re.sub(r"\b0+(?=\d)", "", t)
        # 倍率 4.0x→40x→4x（Оптический прицел 4х → 4.0x 光学瞄准镜）
        t = re.sub(r"^([1-9])0(x)$", r"\1\2", t)
        toks.add(t)
    return toks


def _num_core(t):
    return re.sub(r"\D", "", t) or t


def _tokens_match(ks, zs):
    """型号 token 匹配判定（IWP 清理口径，逐条经 31 案例回归）。

    1) 精确交集 / p-r 形近互换（ГП-10Р→GP-10R）；
    2) token 前缀包含：aks74⊃ak74、18⊂18c、60⊂60hz（转写省略连字符/
       复数后缀变体；要求短侧 ≥2 字符且为长侧**前缀**——中缀不算，
       否则 7Н22→5点45分 会被 45⊂545x39 放行）；
    3) 单 token：数字核心一致（DX10.1→DirectX 10.1 的 101）且字母骨架
       为子序列（dx ⊂ directx），而 ПГ-7ВМ→OG-7V 骨架 pgvm∉ogv 仍拦；
    4) 多 token 覆盖率：源 ≥2 个型号时译文须逐一有数字核心前缀对应——
       5.45x39 M995→「5.56 美元」只保住一个口径，照样拦。
    """
    if ks & zs:
        return True

    def swap(t):
        return t.replace("p", "r") if "p" in t else t.replace("r", "p")

    ks2 = {swap(t) for t in ks}
    zs2 = {swap(t) for t in zs}
    if bool(ks2 & zs2) or bool(ks & zs2) or bool(ks2 & zs):
        return True
    # ★ 多 token（任一侧 ≥2）：必须逐 token 数字核心前缀对应——
    # 5.45x39+7Н22 →「5点45分」只保住 45、5.56x45+M995 →「5.56 美元」
    # 只保住 5.56，均拦截。优先于前缀包含（45⊂45x39 会把这类放行）。
    if len(ks) >= 2 or len(zs) >= 2:
        zcores = {_num_core(t) for t in zs if _num_core(t)}
        kcores = {_num_core(t) for t in ks if _num_core(t)}
        if len(zcores) >= 2 or len(kcores) >= 2:
            covered = sum(
                1 for ca in kcores
                if any(ca.startswith(z) or z.startswith(ca) for z in zcores))
            return covered >= max(len(kcores), len(zcores))
    # 单 token 对单 token：前缀包含 / 数字核心一致 + 字母骨架按序锚定
    if len(ks) == 1 and len(zs) == 1:
        a, b = next(iter(ks)), next(iter(zs))
        short, long = (a, b) if len(a) <= len(b) else (b, a)
        if len(short) >= 2 and long.startswith(short):
            return True
        na, nb = _num_core(a), _num_core(b)
        if na and nb and (na == nb or na.startswith(nb) or nb.startswith(na)):
            aa = re.sub(r"\d", "", a)
            bb = re.sub(r"\d", "", b)
            short_a, long_a = (aa, bb) if len(aa) <= len(bb) else (bb, aa)
            if not short_a or len(short_a) < 2:
                # 空骨架（DX10.1→101）放行；单字符骨架（vm vs v）无判别力，
                # ПГ-7ВМ→OG-7V 这类真漂移必须拦
                return not short_a
            # 骨架按序锚定长串首字符（dx ⊂ directx ✓；pgvm vs ogv ✗）
            if long_a[:1] == short_a[:1] and all(
                    c in long_a for c in short_a):
                return True
            return False
    return False


def garbage_translation(src, zh):
    """True = 译文存在可机械判定的语义级错译特征，跳过不入库。"""
    if _ZH_VEHICLE_RE.search(zh) and not _ZH_VEHICLE_RE.search(src) \
            and not _SRC_VEHICLE_RE.search(src):
        return True                     # 物品/术语被译成载具/大型兵器
    ks = _design_tokens(src)
    zs = _design_tokens(zh, merge_space=True)
    if ks and zs and not _tokens_match(ks, zs):
        return True                     # 型号完全对不上（M433→M406 型漂移）
    return False


# 占位符集合（与 audit_termbase.py 同口径）：$var、%s/d/i/f、%c[...]、字面 \n/\t
PH = re.compile(r"\$[A-Za-z_]\w*|%[sdif]|%c\[[^\]]+\]|\\[nt]")

# audit 同款屈折词干归一（俄语常见词尾），用于 canon 硬层冲突预防
_INFLECT_SUFFIXES = ("ыми", "ими", "ого", "его", "ому", "ему", "ами", "ями",
                     "ах", "ях", "их", "ых", "ов", "ев", "ый", "ий", "ой",
                     "ая", "ое", "ые", "ие", "ую", "юю", "ом", "ем", "ам",
                     "ям", "ы", "и", "а", "я", "у", "ю", "е", "о", "ь")


def stem_word(w):
    w = w.casefold().replace("ё", "е")
    if re.search(r"\d", w):
        return w            # 型号词（акс-74у 等）词尾不是语法屈折，不归一
    for suf in sorted(_INFLECT_SUFFIXES, key=len, reverse=True):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[:-len(suf)]
    return w


def canon_stem_group(src):
    words = [stem_word(x) for x in re.findall(
        r"[А-Яа-яЁёA-Za-z0-9-]+", src.strip("\"'«»“”‘’"))]
    return tuple(words) if words else None


def harvest(src_root, zh_root, auth=1, cat_override=None):
    """返回 (cat, src, 权威级) -> set(zh)。"""
    src_idx = index_text_files(src_root)
    src_alias = make_alias_index(src_idx)
    zh_idx = index_text_files(zh_root)
    pairs = defaultdict(set)
    for rel, zp in zh_idx.items():
        sp = src_idx.get(rel) or src_alias.get(strip_lang(rel))
        if not sp:
            continue
        cat = cat_override
        if not cat:
            low = rel.lower()
            for pat, c in CAT_MAP:
                if pat in low:
                    cat = c
                    break
            if not cat:
                # zh 侧路径无分类线索（平铺语料）时回退到源文件路径
                slow = sp.lower()
                for pat, c in CAT_MAP:
                    if pat in slow:
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
            # ★ 占位符守卫（所有源）：$var/%c[...]/字面\n 等集合必须一致，
            #   否则精确替换会破坏换行对齐/脚本变量。
            if sorted(PH.findall(sv)) != sorted(PH.findall(zv)):
                continue
            # ★ 2026-11-23：垃圾过滤全源生效（口径经 31 案例回归零误杀，
            # 用户指令"错的就不要存在"）。NS7 权威性体现在**冲突覆盖**，
            # 不体现在放行错译。
            if garbage_translation(sv, zv):
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
    soft_only = set()                 # (cat,src) 软参考源，不写 builtin
    for src in SOURCES:
        src_root, zh_root, note, auth = src[:4]
        cat_override = src[4] if len(src) > 4 else None
        pairs = harvest(src_root, zh_root, cat_override=cat_override)
        uniq = sum(1 for v in pairs.values() if len(v) == 1)
        print("%s：术语对 %d（唯一译名 %d，多译名 %d）"
              % (note, len(pairs), uniq, len(pairs) - uniq))
        if cat_override:
            for k in pairs:
                soft_only.add((k[0], k[1]))
        for k, v in pairs.items():
            all_pairs[k] |= v
            if auth >= auth_of.get(k, 0):
                auth_of[k] = auth

    # 键规整回 (cat, src)：auth 只用于覆盖优先级，不进入词库键
    def _k2(k):
        return (k[0], k[1])

    tb_path = os.path.join(ROOT, "plugins", "ai_translate",
                           "assets", "stalker_termbase.json")
    gb_path = os.path.join(ROOT, "plugins", "ai_translate",
                           "assets", "glossary_builtin.json")
    tb = load_termbase(tb_path)
    flat = flatten(tb)

    # ★ 键归一去重（audit norm 同口径）：查找大小写/ё 不敏感，同归一键
    #   只有一个生效——后来的就是死条目，还会触发审计同源冲突。
    #   精确键已在库中的走 override/conflict 通道（NS7 权威覆盖不受影响）。
    flat_low = {}
    for k, v in flat.items():
        flat_low.setdefault(_norm_key(k), (k, v))
    uniq_all = sorted({_k2(k): next(iter(v)) for k, v in all_pairs.items()
                       if len(v) == 1}.items())
    multi = {_k2(k): sorted(v) for k, v in all_pairs.items() if len(v) > 1}
    uniq = {}
    override, conflict = {}, {}
    case_drop = 0
    seen_norm = set()
    for (cat, src), v in uniq_all:
        if src in flat:
            if flat[src] != v:
                if auth_of.get((cat, src), 0) >= 2:
                    override[(cat, src)] = (v, flat[src])
                else:
                    conflict[(cat, src)] = (v, flat[src])
            continue    # 同值已存在，无需动作
        low = _norm_key(src)
        if low in flat_low or low in seen_norm:
            case_drop += 1
            continue
        seen_norm.add(low)
        uniq[(cat, src)] = v
    if case_drop:
        print("casefold/ё 撞键（已有/新内重复，跳过）：%d" % case_drop)

    # ★ canon 词干守卫：硬层新条目若与既有 canon 条目构成同一词干组
    #   且译名不同（Записка Стрелку/Стрелка 型冲突），新条目跳过——
    #   既防审计 inflection_conflict，也防硬替换语境混淆。
    canon_cats = tb["_meta"]["layers"]["canon"]
    stem_grp = {}
    for c in canon_cats:
        for k, v in (tb.get(c) or {}).items():
            g = canon_stem_group(k)
            if g:
                stem_grp.setdefault(g, set()).add(
                    re.sub(r"\s+", "", str(v)))
    new = {}
    stem_drop = []
    for (cat, src), v in sorted(uniq.items(), key=lambda x: (x[0][0], x[0][1])):
        if cat in canon_cats:
            g = canon_stem_group(src)
            if g and g in stem_grp and re.sub(r"\s+", "", v) not in stem_grp[g]:
                stem_drop.append((cat, src, v))
                continue
            if g:
                stem_grp.setdefault(g, set()).add(re.sub(r"\s+", "", v))
        new[(cat, src)] = v
    if stem_drop:
        print("canon 词干冲突（已有译名，新条目跳过）：%d" % len(stem_drop))
        for x in stem_drop[:8]:
            print("   %s:%s -> %s" % x)

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
    # 软参考分类补进 reference 层（_meta.layers），插件按层索引消费
    if soft_only:
        layers = tb["_meta"].setdefault("layers", {})
        ref = layers.setdefault("reference", [])
        for cat in {c for c, _s in soft_only}:
            if cat not in ref:
                ref.append(cat)
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
