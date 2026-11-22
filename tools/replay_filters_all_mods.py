# -*- coding: utf-8 -*-
"""筛选器重放审计（全 MOD）：对本机全部 MOD 的 text 源文跑 2.0.21 判定链。

每个 MOD 输出：条目数 × 判定分布（send/code/noise/tech/ident/proper）+
每类抽样。判定链顺序与 _translate_table 逐条路径一致。只读。
"""
import os
import re
import sys
import glob
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
SAMPLE_N = 8

spec = importlib.util.spec_from_file_location("m", PLUGIN)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ZH = re.compile(r"[\u4e00-\u9fff]")


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
    s = read(path)
    if not s:
        return {}
    # ★ 原文优先、注释归一回退：归一化会把空注释 `<!---->` 破坏成 `<!-->`
    # （`<!-+` 吞掉全部短横），实测 OLR25 4 个文件因此在归一后才解析失败；
    # 反过来 IWP/OP/Anomaly 有 14 个文件只有归一后才能解析。两种都试。
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
            t = e.find("rus")          # NS7 系用 <rus> 槽
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


def main():
    random.seed(11)
    only = sys.argv[1] if len(sys.argv) > 1 else None
    report = {}
    for mod, root in MODS.items():
        if only and only != mod:
            continue
        if not os.path.isdir(root):
            print("跳过（不存在）:", mod)
            continue
        files = xml_files(root)
        dist = {"send": 0, "code": 0, "noise": 0, "tech": 0,
                "ident": 0, "proper": 0, "empty": 0, "parse_fail": 0,
                "target_lang": 0}
        samples = {k: [] for k in dist}
        total = 0
        for p in files:
            rel = os.path.relpath(p, root).replace("\\", "/")
            E = entries(p)
            if E is None:
                dist["parse_fail"] += 1
                continue
            for sid, sv in E.items():
                total += 1
                src = sv.strip()
                if not src:
                    dist["empty"] += 1
                    continue
                # 已是目标语言（中文占比>40%）→ 翻译流程会跳过，不计判定
                zh_n = sum(1 for c in src if "\u4e00" <= c <= "\u9fff")
                if zh_n * 5 > len(src):
                    dist["target_lang"] += 1
                    continue
                if m.looks_like_code_or_debug(rel, src):
                    cat = "code"
                elif m.looks_like_generated_noise(rel, src):
                    cat = "noise"
                elif m.looks_like_technical_text(src):
                    cat = "tech"
                elif (m.looks_like_identifier_text(src)
                      and not m.is_player_facing_label(rel, src)):
                    cat = "ident"
                elif m.looks_like_proper_text(rel, src):
                    cat = "proper"
                else:
                    cat = "send"
                dist[cat] += 1
                if len(samples[cat]) < 200:
                    samples[cat].append((rel, sid, src))
        report[mod] = (total, dist, samples)
        print("%-14s 条目 %6d | 已中文 %5d | send %5d code %4d noise %3d "
              "tech %3d ident %5d proper %4d | 空 %4d 失败 %d"
              % (mod, total, dist["target_lang"], dist["send"],
                 dist["code"], dist["noise"], dist["tech"], dist["ident"],
                 dist["proper"], dist["empty"], dist["parse_fail"]))

    # 抽样人工审：每 MOD 每类取样本
    print("\n== 抽样（每 MOD 每类最多 %d 条）==" % SAMPLE_N)
    for mod, (total, dist, samples) in report.items():
        for cat in ("ident", "proper", "tech", "code", "noise"):
            rows = samples[cat]
            if not rows:
                continue
            pick = random.sample(rows, min(SAMPLE_N, len(rows)))
            print("\n[%s] %s（%d 条）" % (mod, cat, len(rows)))
            for rel, sid, src in pick:
                print("   %s|%s: %s" % (os.path.basename(rel), sid,
                                        src[:88].replace("\n", " ")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
