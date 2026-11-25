#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从主词库生成技能核心定译表 GLOSSARY-CORE.md（宇宙正字法）。

取材口径（2026-11-25 用户定稿）：AI 对话翻译只需要"全宇宙统一写法"的
高频专名/术语，整句语料由脚本直取消化、其余由 extract 逐行 hint 附带。
因此核心表 = 阵营与组织 + 地点与区域 + 变异生物 + 高频硬词 + 技术缩写。

用法：python tools/gen_glossary_core.py [--check]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from io import open as iopen
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "stalker_termbase.json"
DST = ROOT / "skills" / "stalker-translate" / "GLOSSARY-CORE.md"

CORE_CATEGORIES = (
    "高频硬词", "阵营与组织", "地点与区域", "变异生物", "技术缩写",
)
DECOR = "\u201c\u201d\"\u00ab\u00bb' \u201e\u201f"


def clean_trans(value: str) -> str:
    text = str(value).strip()
    # 剥离装饰引号（“晴空”→晴空），仅当整对出现
    while len(text) >= 2 and text[0] in "\u201c\u00ab\u201e\"'" and \
            text[-1] in "\u201d\u00bb\u201f\"'":
        text = text[1:-1].strip()
    return text.strip(DECOR).strip() or str(value).strip()


def clean_entry_key(key: str) -> str:
    return str(key).strip().strip(DECOR).strip()


def build() -> str:
    tb = json.loads(SRC.read_text(encoding="utf-8"))
    total = int((tb.get("_meta") or {}).get("total_entries") or 0)
    lines = [
        "# STALKER 宇宙核心定译表（自动生成，勿手改）",
        "",
        "> 来源: assets/stalker_termbase.json（%d 条）的核心层切片。" % total,
        "> 重新生成: `python tools/gen_glossary_core.py`。",
        "> 用法: 翻译任何 STALKER/X-Ray MOD 时的统一写法依据；",
        "> 库里没有专名的句子按 extract 每行 hint 附带，不必背全库。",
        "> 覆盖: %d 个原词形 → 见各分节计数。" % (
            sum(len(tb.get(c) or {}) for c in CORE_CATEGORIES)),
    ]
    total_groups = 0
    for cat in CORE_CATEGORIES:
        entries = tb.get(cat)
        if not isinstance(entries, dict) or not entries:
            continue
        grouped: dict[str, set] = defaultdict(set)
        for key, val in entries.items():
            trans = clean_trans(val)
            src = clean_entry_key(key)
            if not trans or not src:
                continue
            grouped[trans].add(src)
        lines.append("## %s（%d）" % (cat, len(grouped)))
        lines.append("")
        lines.append("| 定译 | 源语写法 |")
        lines.append("|---|---|")
        for trans in sorted(grouped):
            sources = sorted(grouped[trans], key=lambda s: (len(s), s))
            shown = " / ".join(sources[:4])
            if len(sources) > 4:
                shown += " 等%d形" % len(sources)
            safe_trans = trans.replace("|", "\\|")
            safe_src = shown.replace("|", "\\|")
            lines.append("| %s | %s |" % (safe_trans, safe_src))
            total_groups += 1
        lines.append("")
    body = "\n".join(lines)
    if not body.endswith("\n"):
        body += "\n"
    return body


def main() -> int:
    content = build()
    if "--check" in sys.argv:
        current = DST.read_text(encoding="utf-8") if DST.is_file() else ""
        if current == content:
            print("OK GLOSSARY-CORE.md 与主词库同步")
            return 0
        print("FAIL GLOSSARY-CORE.md 过期，请运行 tools/gen_glossary_core.py")
        return 1
    DST.write_text(content, encoding="utf-8", newline="\n")
    print("已生成 %s（%d 字符）" % (DST.name, len(content)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
