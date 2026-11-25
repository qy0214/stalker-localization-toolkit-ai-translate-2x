# -*- coding: utf-8 -*-
"""共享脚本文本上下文过滤。"""
from __future__ import annotations

import re


_XML_REF_TAG_RE = re.compile(
    r"<(?:text|name|bio|title)\b[^>]*>\s*([A-Za-z0-9_][A-Za-z0-9_.\-]{0,100})\s*</",
    re.I)

_XML_REF_ATTR_RE = re.compile(
    r"\b(?:name|hint|bio|caption|descr|title)\s*=\s*[\"']"
    r"([A-Za-z0-9_][A-Za-z0-9_.\-]{0,100})[\"']",
    re.I)

_SCRIPT_TRANSLATE_RE = re.compile(
    r"translate_string\s*\(\s*[\"']([^\"']+)[\"']\s*\)")

_SCRIPT_QUOTED_RE = re.compile(r"[\"']([A-Za-z0-9_][A-Za-z0-9_.\-]{1,100})[\"']")

_SCRIPT_LOG_CALL_RE = re.compile(
    r"\b(?:log1?|log2|log3|log_message|printf|print|debug|error|assert|"
    r"trace|console|exec)\s*\(", re.I)

_SCRIPT_LOGIC_CALL_RE = re.compile(
    r"\b(?:string_find|string_gsub|string_match|string_gmatch|"
    r"string\.find|string\.gsub|string\.match|string\.gmatch)\s*\(",
    re.I)

_SCRIPT_UI_CALL_RE = re.compile(
    r"\b(?:SetText|SetName|SetTextTip|InitStatic|InitButton|AddText|AddLine|"
    r"send_tip|hud_msg|add_spot_on_map|give_talk_message|map_add_user_spot|"
    r"show_message|show_tip|talk_message|send_message|add_news|add_tip|"
    r"message_box|set_level_changer_invitation)\s*\(", re.I)

_SCRIPT_ASSIGN_RE = re.compile(
    r"^\s*(?:local\s+)?(?:self\.|this\.|obj\.)?([A-Za-z_]\w*)\s*=\s*[^=]")

_SCRIPT_TEXT_VAR_RE = re.compile(
    r"(?:^|_)(?:text|txt|msg|message|hint|caption|title|descr|description|"
    r"name|news|label|tip)(?:_|$)", re.I)

def strip_script_comment(line):
    """去掉 -- 行注释，保留引号内的 --（如 "a--b"）。"""
    out = []
    quote = None
    escape = False
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
        else:
            if ch in "\"'":
                quote = ch
                out.append(ch)
            elif ch == "-" and i + 1 < len(line) and line[i + 1] == "-":
                break
            else:
                out.append(ch)
        i += 1
    return "".join(out)

_SCRIPT_EXTRA_DISPLAY_RES = (
    re.compile(
        r"(?:set_level_changer_invitation|set_description)\s*\(\s*"
        r"([\"'])(.*?)\1", re.S),
    re.compile(
        r"AddItem\s*\(\s*game\.translate_string\s*\(\s*([\"'])(.*?)\1",
        re.S),
)

_SCRIPT_TRANSLATE_ARG_RE = re.compile(
    r"game\.translate_string\s*\(\s*([\"'])(.*?)\1", re.S)

def extra_script_display_literals(whole):
    """补上游漏掉的显示调用字面串（只收西里尔文本，避免碰 ID/代码）。"""
    out = set()
    for pattern in _SCRIPT_EXTRA_DISPLAY_RES:
        for m in pattern.finditer(whole or ""):
            inner = m.group(2)
            if re.search(r"[А-Яа-яЁё]", inner):
                out.add(m.group(1) + inner + m.group(1))
    return out

def script_translate_literal_args(whole):
    """已包在 game.translate_string(...) 里的字面串：替换时不能再套一层。"""
    return {m.group(1) + m.group(2) + m.group(1)
            for m in _SCRIPT_TRANSLATE_ARG_RE.finditer(whole or "")}

def script_literal_allowed(line, literal):
    """这一行的这个字面串是否允许改写/送翻。"""
    code = strip_script_comment(line)
    if not code.strip():
        return False
    if _SCRIPT_LOG_CALL_RE.search(code):
        return False
    if _SCRIPT_LOGIC_CALL_RE.search(code):
        return False
    # 比较/连接判断里的字符串通常是标签、档位、状态名，不能换。
    if re.search(r"==|~=|<=|>=", code):
        return False
    if _SCRIPT_UI_CALL_RE.search(code):
        return True
    m = _SCRIPT_ASSIGN_RE.match(code)
    if m and _SCRIPT_TEXT_VAR_RE.search(m.group(1)):
        return True
    return False

def script_literal_allowed_in_file(whole, cand):
    """候选串在脚本里的**每一次**出现都必须是安全上下文，才允许改写。

    任何一次出现在 log/string_find/比较里就整条跳过；全局替换无法区分
    同一字面串的不同出现位置，保守跳过比改坏逻辑好。
    """
    if len(cand) < 2 or cand[0] not in "\"'" or cand[-1] != cand[0]:
        return False
    inner = cand[1:-1]
    if not inner.strip():
        return False
    for needle in (cand, inner):
        found = False
        start = 0
        while True:
            idx = whole.find(needle, start)
            if idx < 0:
                break
            found = True
            line_start = whole.rfind("\n", 0, idx) + 1
            line_end = whole.find("\n", idx)
            if line_end < 0:
                line_end = len(whole)
            if not script_literal_allowed(whole[line_start:line_end], inner):
                return False
            start = idx + len(needle)
        if found:
            return True
    return False
