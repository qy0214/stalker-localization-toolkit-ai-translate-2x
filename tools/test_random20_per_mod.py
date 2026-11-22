# -*- coding: utf-8 -*-
"""每 MOD 随机 20 文件测试（只读，不写任何游戏文件）。

对 16 个 MOD 源各随机抽 20 个 xml，按 2.0.21 当前判定链逐条重放：
  target(已中文) → code → 词库直取 → 句式直译 → noise/tech/ident
  → proper(硬层子串替换) → send(需模型)
离线可判定的结果做确定性核验：
  A. 离线直取/直译产物：译文≠原文、含中文、占位符集合与源一致
  B. 跳过类（noise/tech/ident/proper）假跳过红旗：含西里尔≥3 且以
     句末标点结尾且长度≥40 —— 疑似被误跳的正文
  C. 每文件解析失败/空条目计数
输出：汇总表 + 问题条目抽样（每 MOD 每类最多 10 条）。
用法：python tools/test_random20_per_mod.py [MOD名] [种子] [抽样记录.json] [每MOD文件数|all]
给了抽样记录就排除记录里的文件（跨轮不重复），并把本轮抽样追加写回。
第 4 个参数给 all（或 0）= 全量模式：每 MOD 跑全部文件，不做抽样、不读排除记录。
"""
import os
import re
import sys
import json
import random
import importlib.util
import xml.etree.ElementTree as ET

PLUGIN = r"C:\STALKER-AiTranslate-2x\plugins\ai_translate\plugin.py"
MODS = {
    "NS7": r"C:\game\NS7\gamedata\configs\text",
    "NS7-SQ基线": r"C:\game\CCCC\gamedata.sq_base_configs\configs\text",
    "NS219汉化": r"C:\game\NS\MODS\NS OGSR 2026年2月19日版本汉化补丁\gamedata\configs\text",
    "NS619汉化": r"C:\game\NS7\MODS\NS OGSR 2026年6月19日版本汉化补丁\gamedata\configs\text",
    "OP": r"C:\game\OP\gamedata\config\text",
    "OLR25": r"C:\game\STALKER-OLR25\gamedata\config\text",
    "疤脸归来2": r"C:\game\S.T.A.L.K.E.R\gamedata\config\text",
    "WOT汉化": r"C:\game\WOT\MODS\汉化\gamedata\configs\text",
    "GA汉化": r"C:\game\OGSR_GA\OGSR Gunslinger Addon\MODS\汉化\gamedata\config\text",
    "SoC1.0006原版": r"C:\game\OGSR-Engine\Game\Resources_SoC_1.0006\gamedata\config\text",
    "Anomaly-eng": r"C:\game\NS7-MT-TEST\extract\anomaly_full\configs\text\eng",
    "Anomaly-rus": r"C:\game\NS7-MT-TEST\extract\anomaly_full\configs\text\rus",
    "MT-CN": r"C:\game\ANTHOLOGY\Modpack-1.5.3-Anthology 2.1\mods\MT-CN\gamedata\configs\text",
    "Monolith-MT": r"C:\game\xray-monolith-mt\gamedata\configs\text",
    "百科大全": r"C:\game\ANTHOLOGY\Modpack-1.5.3-Anthology 2.1\mods\[AlzHighMr] 百科大全 — Anthology Encyclopedia\gamedata\configs\text",
    "IWP": r"C:\Users\Admin\Desktop\IWP\gamedata\configs\text\eng",
}
FILES_N = 20
SAMPLE_N = 10

spec = importlib.util.spec_from_file_location("m", PLUGIN)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ZH = re.compile(r"[\u4e00-\u9fff]")
CYR = re.compile(r"[А-Яа-яЁё]")
PH = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*|%(?:\d+\$)?[sdifgexX%]|%c\[[^\]]*\]")
SENT_END = ".!?…。！？"


def read(p):
    raw = open(p, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _norm_comments(s):
    s = re.sub(r"<!-+", "<!--", s)
    s = re.sub(r"<!--(.*?)(-{2,})>", lambda x: "<!--" + x.group(1) + "-->", s)
    return s.replace("--!>", "-->")


def entries(path):
    """原文优先、注释归一回退。

    归一化会把空注释 `<!---->` 破坏成 `<!-->`（`<!-+` 吞掉全部短横），
    实测 OLR25 4 个文件因此在归一后才解析失败；反过来 IWP/OP/Anomaly
    有 14 个文件只有归一后才能解析。两种都试，谁通用谁。
    """
    s = read(path)
    if not s:
        return None
    root = None
    for cand in (s, _norm_comments(s)):
        try:
            root = ET.fromstring(cand)
            break
        except ET.ParseError:
            continue
    if root is None:
        return None
    out = {}
    for e in root.iter("string"):
        t = e.find("text")
        if t is None:
            t = e.find("rus")
        if t is None:
            t = e.find("eng")
        if t is None:
            continue
        out[e.get("id", "")] = "".join(t.itertext())
    return out


def xml_files(root):
    out = []
    for r, _d, fs in os.walk(root):
        for f in fs:
            if f.lower().endswith(".xml"):
                out.append(os.path.join(r, f))
    return sorted(set(out))


def ph_set(s):
    return sorted(PH.findall(s))


def classify(rel, src):
    """返回 (结局, 产物或None)，顺序与 plugin._translate_table 一致。"""
    zh_n = len(ZH.findall(src))
    if zh_n * 5 > len(src):
        return "target", None
    if m.looks_like_code_or_debug(rel, src):
        # ★ 2.0.22：型号形态 + 词库整串命中 → 直取（不再整条拦掉）
        if not m.looks_like_hard_protected_file(rel) \
                and m._is_model_code_form(rel, src):
            mc = m.termbase_exact_translation(src)
            if mc is not None and mc != src:
                return "code_tb", mc
        return "code", None
    hard = m.looks_like_hard_protected_file(rel)
    if not hard:
        ex = m.termbase_exact_translation(src)
        if ex is not None and ex != src:
            return "tb", ex
        pt = m.pattern_translate(src, m.termbase_exact_translation)
        if pt is not None:
            return "pat", pt
    if m.looks_like_generated_noise(rel, src):
        return "noise", None
    if m.looks_like_technical_text(src):
        return "tech", None
    if m.looks_like_identifier_text(src) and not m.is_player_facing_label(rel, src):
        return "ident", None
    if m.looks_like_proper_text(rel, src):
        pp = m.partial_glossary_translation(src, rel)
        if pp is not None and pp != src:
            return "prop", pp
        return "proper", None
    return "send", None


def false_skip_flag(src):
    """跳过类假跳过红旗：西里尔正文句 / 长英文句。"""
    n = len(src)
    if n < 40 or src.rstrip()[-1:] not in SENT_END:
        return False
    if len(CYR.findall(src)) >= 3:
        return True
    words = re.findall(r"[A-Za-z]{2,}", src)
    return len(words) >= 6 and not CYR.search(src)


BARE_AMP = re.compile(r"&(?!#?\w+;)")


def main():
    only = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else random.SystemRandom().randint(0, 10**9)
    rec_path = sys.argv[3] if len(sys.argv) > 3 else None
    # 第 4 个参数：每 MOD 抽几个文件；"all"/0 = 全部文件（全量模式）
    n_arg = sys.argv[4] if len(sys.argv) > 4 else str(FILES_N)
    files_n = 0 if n_arg.lower() in ("all", "0") else int(n_arg)
    prior = {}
    if rec_path and os.path.exists(rec_path):
        prior = json.load(open(rec_path, encoding="utf-8"))
    rng = random.Random(seed)
    print("种子:", seed, "| 排除记录:", rec_path or "无")
    bad_a = []   # 离线产物核验失败
    bad_b = []   # 假跳过红旗（西里尔/长英文正文句）
    bad_c = []   # 跳过类里本可硬层子串替换（判定链顺序丢失）
    amp = []     # 裸 & 条目（宿主解析风险面）
    picks = dict(prior)
    for mod, root in MODS.items():
        if only and only != mod:
            continue
        if not os.path.isdir(root):
            print("跳过（不存在）:", mod)
            continue
        files = xml_files(root)
        seen = set() if not files_n else set(prior.get(mod, []))
        pool = [p for p in files
                if os.path.relpath(p, root).replace("\\", "/") not in seen]
        if not pool:
            print("%-14s 无未测文件（全部 %d 个已在前轮覆盖）" % (mod, len(files)))
            continue
        pick = pool if not files_n else rng.sample(pool, min(files_n, len(pool)))
        picks.setdefault(mod, list(seen))
        for p in pick:
            rel = os.path.relpath(p, root).replace("\\", "/")
            if rel not in picks[mod]:
                picks[mod].append(rel)
        dist = {k: 0 for k in ("target", "code", "code_tb", "tb", "pat",
                               "noise", "tech", "ident", "proper", "prop",
                               "send", "empty", "parse_fail")}
        for p in pick:
            rel = os.path.relpath(p, root).replace("\\", "/")
            E = entries(p)
            if E is None:
                dist["parse_fail"] += 1
                continue
            for sid, sv in E.items():
                src = (sv or "").strip()
                if not src:
                    dist["empty"] += 1
                    continue
                if BARE_AMP.search(src):
                    amp.append((mod, rel, sid))
                cat, out = classify(rel, src)
                dist[cat] += 1
                if cat in ("tb", "pat", "prop", "code_tb"):
                    if (out == src or not ZH.search(out)
                            or ph_set(out) != ph_set(src)):
                        bad_a.append((mod, rel, sid, cat, src[:60], out[:60]))
                elif cat in ("noise", "tech", "ident", "code"):
                    if false_skip_flag(src):
                        bad_b.append((mod, rel, sid, cat,
                                      src[:88].replace("\n", " ")))
                    # C 类是诊断项（跳过条目本可子串替换 → 判定链顺序），
                    # 每条要对硬层词表做子串扫描；全量模式下开销过大，
                    # 且该风险已由 tests/test_2022_soft_retire.py 固化覆盖。
                    if files_n:
                        pp = m.partial_glossary_translation(src, rel)
                        if pp is not None and pp != src and ZH.search(pp):
                            bad_c.append((mod, rel, sid, cat,
                                          src[:60].replace("\n", " "),
                                          pp[:60].replace("\n", " ")))
                elif cat == "proper" and false_skip_flag(src):
                    bad_b.append((mod, rel, sid, cat,
                                  src[:88].replace("\n", " ")))
        print("%-14s 文件 %2d(新%d) | target %5d code %4d(型号直取%4d) "
              "词库 %4d 句式 %4d noise %3d tech %3d ident %4d "
              "proper %4d(替%d) send %5d | 空 %3d 解析失败 %d"
              % (mod, len(pick), len(pick), dist["target"], dist["code"],
                 dist["code_tb"], dist["tb"], dist["pat"], dist["noise"],
                 dist["tech"], dist["ident"], dist["proper"], dist["prop"],
                 dist["send"], dist["empty"], dist["parse_fail"]))

    if rec_path:
        json.dump(picks, open(rec_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("\n抽样记录已写入:", rec_path,
              "累计文件数:", sum(len(v) for v in picks.values()))

    def dump(title, rows, fields, limit=30):
        print("\n== %s: %d ==" % (title, len(rows)))
        for r in rows[:limit]:
            print(fields % r)

    dump("A. 离线产物核验失败（译文=源/无中文/占位符不一致）", bad_a,
         "   [%s] %s|%s (%s)\n      源: %s\n      译: %s")
    dump("C. 跳过类本可硬层子串替换（判定链顺序丢失）", bad_c,
         "   [%s] %s|%s (%s)\n      源: %s\n      可译: %s")

    print("\n== B. 跳过类假跳过红旗: %d ==" % len(bad_b))
    by_mod = {}
    for r in bad_b:
        by_mod.setdefault(r[0], []).append(r)
    for mod, rows in by_mod.items():
        show = rng.sample(rows, min(SAMPLE_N, len(rows)))
        print("\n[%s] %d 条，抽 %d:" % (mod, len(rows), len(show)))
        for _m, rel, sid, cat, s in show:
            print("   (%s) %s|%s: %s" % (cat, os.path.basename(rel), sid, s))

    print("\n== D. 裸 & 条目（宿主解析风险面，不断言）: %d ==" % len(amp))
    by_mod = {}
    for r in amp:
        by_mod[r[0]] = by_mod.get(r[0], 0) + 1
    for mod, n in sorted(by_mod.items(), key=lambda x: -x[1]):
        print("   %-14s %5d" % (mod, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
