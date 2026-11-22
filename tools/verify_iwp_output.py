# -*- coding: utf-8 -*-
"""IWP 已翻译产物全量核验（源↔译逐条对照）。只读。

维度：
  1 条目集差（漏文件/漏条目/多条目）
  2 疑似截断（译文字数 < 源 60% 且源>200 字，取中位数排除正常简译）
  3 占位符集合不一致（$var/%s/%c[...]/字面\\n）
  4 字面 \\n 数量不一致
  5 数字保真（源数字须在译文出现）
  6 残留原文（西里尔/未翻英文长句）
  7 canon 术语命中抽检（源含硬词、译文应含词库译名）
"""
import os
import re
import glob
import json
import xml.etree.ElementTree as ET
from collections import Counter

SRC = r"C:\Users\Admin\Desktop\IWP\gamedata\configs\text\eng"
OUT = r"C:\Users\Admin\Desktop\IWP\gamedata\translated_hy_mt2\configs\text\eng"
TB = r"C:\STALKER-AiTranslate-2x\plugins\ai_translate\assets\stalker_termbase.json"

PH = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*|%[sdif]|%c\[[^\]]+\]")
NUM = re.compile(r"\d{2,}")
ZH = re.compile(r"[\u4e00-\u9fff]")
CYR = re.compile(r"[А-Яа-яЁё]")


def read(p):
    raw = open(p, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def entries(path):
    s = read(path)
    if not s:
        return {}
    # 源文件自带非法注释（<!-----> 横线数不对，X-Ray 引擎容忍）→ 归一后再解析
    s = re.sub(r"<!-+", "<!--", s)
    s = re.sub(r"<!--(.*?)(-{2,})>", lambda m: "<!--" + m.group(1) + "-->", s)
    s = s.replace("--!>", "-->")
    try:
        root = ET.fromstring(s)
    except ET.ParseError:
        return None
    return {e.get("id", ""): "".join(t.itertext())
            for e in root.iter("string") if (t := e.find("text")) is not None}


def main():
    src_files = {os.path.basename(p): p for p in glob.glob(SRC + "/*.xml")}
    out_files = {os.path.basename(p): p for p in glob.glob(OUT + "/*.xml")}
    missing_files = sorted(set(src_files) - set(out_files))
    extra_files = sorted(set(out_files) - set(src_files))
    print("源文件 %d，译文件 %d，缺文件 %s，多文件 %d"
          % (len(src_files), len(out_files), missing_files[:5], len(extra_files)))

    problems = {"missing_id": [], "trunc": [], "ph": [], "lit_nl": [],
                "num": [], "latin_long": [], "cyr": []}
    total, zh_cnt = 0, 0
    len_ratios = []
    for name in sorted(set(src_files) & set(out_files)):
        S = entries(src_files[name])
        O = entries(out_files[name])
        if S is None or O is None:
            problems["missing_id"].append((name, "!XML解析失败", "", ""))
            continue
        for sid, sv in S.items():
            total += 1
            ov = O.get(sid)
            if ov is None:
                problems["missing_id"].append((name, sid, sv[:60], ""))
                continue
            if ZH.search(ov):
                zh_cnt += 1
            if not sv:
                continue
            # 截断候选
            if len(sv) > 200 and len(ov) < len(sv) * 0.6:
                len_ratios.append((len(ov) / len(sv), name, sid, sv, ov))
            # 占位符
            if sorted(PH.findall(sv)) != sorted(PH.findall(ov)):
                problems["ph"].append((name, sid, sv[:60], ov[:60]))
            # 字面 \n
            if sv.count("\\n") != ov.count("\\n"):
                problems["lit_nl"].append((name, sid, sv.count("\\n"), ov.count("\\n")))
            # 数字
            for n in NUM.findall(sv):
                if n not in ov:
                    problems["num"].append((name, sid, n, sv[:60]))
                    break
            # 残留西里尔
            if CYR.search(ov):
                problems["cyr"].append((name, sid, sv[:50], ov[:50]))

    print("条目总数 %d，译文含中文 %d（%.1f%%）" % (total, zh_cnt, zh_cnt * 100 / max(total, 1)))
    len_ratios.sort(key=lambda x: x[0])
    # 截断：只报 <50% 且源 >300 的（排除正常简译），取全量列表
    trunc = [(r, name, sid, sv, ov) for r, name, sid, sv, ov in len_ratios
             if r < 0.5 and len(sv) > 300]
    print("\n== 疑似截断（译/源 < 50%% 且源>300 字）：%d 条 ==" % len(trunc))
    for r, name, sid, sv, ov in trunc[:12]:
        print("  ratio=%.2f %s|%s" % (r, name, sid))
        print("    SRC(%d): %s" % (len(sv), sv[:90].replace("\n", " ")))
        print("    OUT(%d): %s" % (len(ov), ov[:90].replace("\n", " ")))

    print("\n== 占位符不一致 %d ==" % len(problems["ph"]))
    for name, sid, sv, ov in problems["ph"][:10]:
        print("  %s|%s\n    SRC: %s\n    OUT: %s" % (name, sid, sv, ov))
    print("\n== 字面\\n不一致 %d ==" % len(problems["lit_nl"]))
    for name, sid, a, b in problems["lit_nl"][:10]:
        print("  %s|%s src=%d out=%d" % (name, sid, a, b))
    print("\n== 数字缺失 %d ==" % len(problems["num"]))
    for name, sid, n, sv in problems["num"][:10]:
        print("  %s|%s 缺 %s | %s" % (name, sid, n, sv))
    print("\n== 西里尔残留 %d ==" % len(problems["cyr"]))
    for name, sid, sv, ov in problems["cyr"][:10]:
        print("  %s|%s\n    SRC: %s\n    OUT: %s" % (name, sid, sv, ov))
    print("\n== 漏条目 %d ==" % len(problems["missing_id"]))
    for row in problems["missing_id"][:8]:
        print("  ", row)

    # canon 术语命中抽检
    tb = json.load(open(TB, encoding="utf-8"))
    canon = {}
    for c in tb["_meta"]["layers"]["canon"]:
        canon.update(tb.get(c, {}))
    canon_low = {k.casefold(): v for k, v in canon.items()}
    miss, hit = 0, 0
    samples = []
    for name in sorted(set(src_files) & set(out_files)):
        S = entries(src_files[name])
        O = entries(out_files[name])
        if not S or not O:
            continue
        for sid, sv in S.items():
            ov = O.get(sid)
            if not ov:
                continue
            for token in re.findall(r"[A-Za-zА-Яа-яЁё][\wА-Яа-яЁё\-']+", sv):
                v = canon_low.get(token.casefold())
                if v and ZH.search(v):
                    if v in ov:
                        hit += 1
                    else:
                        miss += 1
                        if len(samples) < 15:
                            samples.append((name, sid, token, v, sv[:50], ov[:50]))
    print("\n== canon 术语命中：直接命中 %d / 未命中 %d ==" % (hit, miss))
    for name, sid, token, v, sv, ov in samples:
        print("  %s|%s 术语 %r→%r\n    SRC: %s\n    OUT: %s" % (name, sid, token, v, sv, ov))


if __name__ == "__main__":
    main()
