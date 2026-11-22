# -*- coding: utf-8 -*-
"""筛选器重放审计：IWP 全部源文逐条跑插件判定链，与产物对照。

判定链（text 表逐条路径，与 _translate_table 一致）：
  code_or_debug → generated_noise → technical → identifier(非 player_label)
  → proper/protect（含 2.0.21 partial 替换）→ 送翻

对照产物判定每条的实际结局：
  A 送翻且有中文 = 正常
  B 送翻但译文无中文 = 漏译（筛选器放行但模型/闸门失败）
  C 被筛选跳过 = 预期保护（核对是否合理：源含实词而产物无中文且非专名形态 = 疑漏）
  D proper 保护 + partial 替换 = 2.0.21 新路径
输出：判定分布 + 每类样本 + 疑漏清单。只读。
"""
import os
import re
import sys
import glob
import json
import importlib.util
import xml.etree.ElementTree as ET

NEW = r"C:\STALKER-AiTranslate-2x\plugins\ai_translate\plugin.py"
SRC = r"C:\Users\Admin\Desktop\IWP\gamedata\configs\text\eng"
OUT = r"C:\Users\Admin\Desktop\IWP\gamedata\translated_hy_mt2\configs\text\eng"

spec = importlib.util.spec_from_file_location("m", NEW)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ZH = m.ZH if hasattr(m, "ZH") else re.compile(r"[\u4e00-\u9fff]")


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
    s = re.sub(r"<!-+", "<!--", s)
    s = re.sub(r"<!--(.*?)(-{2,})>", lambda x: "<!--" + x.group(1) + "-->", s)
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
    dist = {"A_sent_zh": 0, "B_sent_nozh": [], "C_skip_ok": 0,
            "C_skip_susp": [], "D_partial": 0, "code": 0, "noise": 0,
            "tech": 0, "ident": 0, "proper": 0}
    rel = "configs/text/eng/st_x.xml"           # IWP 源 rel 形态（eng 表）
    for name in sorted(set(src_files) & set(out_files)):
        S = entries(src_files[name])
        O = entries(out_files[name])
        if S is None or O is None:
            continue
        for sid, sv in S.items():
            ov = O.get(sid)
            src = sv.strip()
            if not src or not src.strip():
                continue
            # 重放判定链（顺序与 _translate_table 相同）
            if m.looks_like_code_or_debug(rel, src):
                dist["code"] += 1
                cat = "code"
            elif m.looks_like_generated_noise(rel, src):
                dist["noise"] += 1
                cat = "noise"
            elif m.looks_like_technical_text(src):
                dist["tech"] += 1
                cat = "tech"
            elif (m.looks_like_identifier_text(src)
                  and not m.is_player_facing_label(rel, src)):
                dist["ident"] += 1
                cat = "ident"
            elif m.looks_like_proper_text(rel, src):
                dist["proper"] += 1
                cat = "proper"
            else:
                cat = "send"
            has_zh = bool(ov) and bool(ZH.search(ov))
            if cat == "send":
                if has_zh:
                    dist["A_sent_zh"] += 1
                else:
                    dist["B_sent_nozh"].append((name, sid, src[:70],
                                                (ov or "")[:50]))
            elif cat == "proper":
                p = m.partial_glossary_translation(src)
                if p:
                    dist["D_partial"] += 1
                # 保护条目若产物有中文 = 历史版本已翻过（正常）
                if has_zh:
                    dist["C_skip_ok"] += 1
                elif len(src) > 30 and not re.search(
                        r"[\"«].*[\"»]|[A-Z]{2,}", src):
                    dist["C_skip_susp"].append((name, sid, src[:70]))
            else:
                if has_zh:
                    dist["C_skip_ok"] += 1
                elif len(src) > 40 and re.search(r"[A-Za-z]{3,}\s+[A-Za-z]{3,}",
                                                 src) and src == (ov or "").strip():
                    dist["C_skip_susp"].append(
                        (name, sid, cat + ": " + src[:70]))

    print("== 重放判定分布 ==")
    print("  送翻且译文含中文: %d" % dist["A_sent_zh"])
    print("  送翻但译文无中文（漏译候选）: %d" % len(dist["B_sent_nozh"]))
    print("  保护跳过（partial 替换 %d 条）" % dist["D_partial"])
    print("  筛选器分类: code %d / noise %d / tech %d / ident %d / proper %d"
          % (dist["code"], dist["noise"], dist["tech"], dist["ident"],
             dist["proper"]))
    print("  跳过但有中文（历史译文，正常）: %d" % dist["C_skip_ok"])
    print("\n== 送翻漏译样本（前 15）==")
    for name, sid, sv, ov in dist["B_sent_nozh"][:15]:
        print("  %s|%s\n    SRC: %s\n    OUT: %s" % (name, sid, sv, ov))
    print("\n== 疑漏（跳过但产物无中文且形似正文，前 15）==")
    for name, sid, sv in dist["C_skip_susp"][:15]:
        print("  %s|%s\n    %s" % (name, sid, sv))


if __name__ == "__main__":
    main()
