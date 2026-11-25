# -*- coding: utf-8 -*-
"""X-Ray 编码、文件分类与项目勘察。"""
from __future__ import annotations

import codecs
import os
import re

from .models import BUILTIN_TIERS, TIER_PROFILES
from .pipeline import estimate_tokens, safe_chunk_chars


_ENC_FALLBACK = ("cp1251", "cp1252")


_ENC_FORCED = "cp1251"


_ENC_UTF8 = ("utf-8", "utf-8-sig")


_BOM_UTF8 = b"\xef\xbb\xbf"


_XML_DECL_ENC = re.compile(rb"<\?xml[^>]*?encoding\s*=\s*[\"']([\w.\-]+)", re.I)


_DECL_SCAN_BYTES = 2048


def _is_valid_utf8(raw):
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _declared_encoding(raw):
    """取 XML 声明里写明的编码名；未声明 / 名字不被 Python 认识时返回 None。"""
    m = _XML_DECL_ENC.search(raw[:_DECL_SCAN_BYTES])
    if not m:
        return None
    name = m.group(1).decode("ascii", "ignore").strip().lower()
    try:
        codecs.lookup(name)
    except (LookupError, ValueError):
        return None
    return name


def encoding_chain(raw):
    """给出该字节流的解码候选次序（首个能严格解码成功者胜出）。

    次序规则，按优先级：
      1. UTF-8 BOM          → utf-8-sig（唯一的权威信号）
      2. 字节流是合法 UTF-8 → utf-8
      3. XML 声明写明的编码 → 提到最前（声明与实际不符时严格解码会失败，
         自动落到下一档，不会误判）
      4. 其余按 cp1251 → cp1252（STALKER 俄版为主流），末位固定 _ENC_FORCED

    返回元组；末位是"尽力而为"档，供解析器的 errors="ignore" 兜底使用。

    已知限制：无 BOM、无声明、又非 UTF-8 的文件，若同时是合法 cp1251 与
    合法 cp1252 字节流（极常见），此处按 STALKER 主流取 cp1251；西文 ww
    版这类文件请把「源语言」选为 eng 或在文件头补 XML 声明。
    """
    if raw.startswith(_BOM_UTF8):
        cands = ["utf-8-sig"]
    elif _is_valid_utf8(raw):
        cands = ["utf-8"]
    else:
        cands = []
        decl = _declared_encoding(raw)
        if decl and decl not in _ENC_UTF8:
            cands.append(decl)
        cands.extend(n for n in _ENC_FALLBACK if n not in cands)
    if not cands or cands[-1] != _ENC_FORCED:
        cands.append(_ENC_FORCED)
    return tuple(cands)


def read_encoding_chain(path, max_bytes=64 * 1024 * 1024):
    """读取文件并给出其解码候选次序；读不了时返回兜底次序。
    仅供解析前判定编码用，不改变调用方自己的读取逻辑。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            raw = f.read(max_bytes if size > max_bytes else size)
    except OSError:
        return (_ENC_FORCED,)
    if size > max_bytes:
        while raw and (raw[-1] & 0xC0) == 0x80:
            raw = raw[:-1]
        if raw and raw[-1] >= 0x80:
            raw = raw[:-1]
    return encoding_chain(raw)


_TAG_TEXT = re.compile(
    r"<(?:text|rus|eng|ukr|ger|fra)\b[^>]*>(.*?)</(?:text|rus|eng|ukr|ger|fra)>",
    re.I | re.S)


_SCAN_KIND_MAP = {"text": "text", "cfg": "cfgxml", "ltx": "ltx",
                  "script": "scriptL"}


def _iter_candidate_strings(txt, kind):
    """按文件类型粗提"可能被翻译的字符串"，只用于规模估计与分档推荐。"""
    if kind in ("ltx", "cfgxml", "scriptL", "scriptE"):
        for m in re.finditer(r'"([^"\n]{2,})"', txt):
            s = m.group(1).strip()
            if s:
                yield s
    else:
        for m in _TAG_TEXT.finditer(txt):
            s = re.sub(r"\s+", " ", m.group(1)).strip()
            if s:
                yield s


def classify_xray_file(full, rel):
    """判定 X-Ray 文件类型：'text' / 'cfg' / 'ltx' / 'script' / None。

    勘察（扫描选档）与执行（真正翻哪些文件）**必须共用这一处判定**，
    否则"扫描说有 3000 条"和"实际翻了哪些文件"不是同一批 —— 旧实现两条
    口径各写一套（勘察只看路径里有没有 text 目录，执行还会嗅探 <string>
    头），统计与行为会分歧。
    """
    low = full.lower()
    parts = [x.lower() for x in rel.replace("\\", "/").split("/")]
    if low.endswith(".ltx"):
        return "ltx"
    if low.endswith(".script"):
        return "script"
    if low.endswith(".xml"):
        if "text" in parts[:-1]:
            return "text"
        try:
            with open(full, "rb") as f:
                head = f.read(65536).lower()
        except OSError:
            return "cfg"
        if b"<string" in head and b"id=" in head and (
                b"<text" in head or b"<rus" in head
                or b"<eng" in head or b"<ukr" in head):
            return "text"
        return "cfg"
    return None


def analyze_input_dir(in_dir, mode="text", log=None, cancel=None,
                      max_files=200000):
    """勘察待翻译目录：文件数 / 体积 / 最大文件 / 最长单条文本，
    并按内置档位给出超长条数与推荐档位。只读，不修改任何文件。

    要逐文件读内容才能统计"最长可译文本"，所以超大目录会明显耗时：
      * 每 500 个文件写一条进度，用户知道它在动；
      * 超过 max_files 停止统计并在结果里标 truncated（不再默默卡住）；
      * cancel 为 threading.Event 时支持中途取消。
    长度统计只保留"直方图 + 最长项"，不再把所有 (长度, 文件名) 元组堆在
    内存里（数万条文本就是几十万个元组）。
    """
    suffixes = {
        "text": (".xml",), "cfgxml": (".xml",),
        "ltx": (".ltx",), "scriptL": (".script",), "scriptE": (".script",),
        "auto": (".xml", ".ltx", ".script"),
    }
    want = suffixes.get(mode, (".xml",))
    n_files = 0
    total_bytes = 0
    max_file = (0, "")
    hist = {}
    n_strings = 0
    n_files_with_text = 0
    max_str = 0
    max_str_file = ""
    truncated = False
    cancelled = False
    for root0, dirs, files in os.walk(in_dir):
        dirs[:] = [d for d in dirs
                   if d not in ("translated_hy_mt2", "__temp__")
                   and not d.endswith(".git")]
        if cancel is not None and cancel.is_set():
            cancelled = True
            break
        parent_name = os.path.basename(root0).lower()
        if mode == "cfgxml" and parent_name == "text":
            continue
        for fn in files:
            if not fn.lower().endswith(want):
                continue
            full = os.path.join(root0, fn)
            rel = os.path.relpath(full, in_dir)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > 20 * 1024 * 1024:
                continue
            n_files += 1
            total_bytes += size
            if size > max_file[0]:
                max_file = (size, full)
            if n_files > max_files:
                truncated = True
                break
            try:
                with open(full, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            txt = None
            for enc in encoding_chain(raw):
                try:
                    txt = raw.decode(enc)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if txt is None:
                continue
            kind = mode
            if mode == "auto":
                kind = _SCAN_KIND_MAP.get(
                    classify_xray_file(full, rel), "cfgxml")
            file_strings = 0
            for s in _iter_candidate_strings(txt, kind):
                length = len(s)
                hist[length] = hist.get(length, 0) + 1
                n_strings += 1
                file_strings += 1
                if length > max_str:
                    max_str, max_str_file = length, full
            if file_strings:
                n_files_with_text += 1
            if log and n_files % 500 == 0:
                log("  勘察中…已读 %d 个文件" % n_files)
        if truncated:
            if log:
                log("  文件数超过 %d，勘察提前结束（统计口径见结果说明）"
                    % max_files)
            break
    per_tier = {}
    for t in BUILTIN_TIERS:
        cap = safe_chunk_chars(TIER_PROFILES[t]["ctx"])
        per_tier[t] = sum(c for L, c in hist.items() if L > cap)
    if max_str > safe_chunk_chars(TIER_PROFILES["mt2"]["ctx"]):
        recommend = "7b"
    else:
        recommend = "mt2"
    return {
        "files": n_files,
        "bytes": total_bytes,
        "max_file": max_file,
        "max_str": max_str,
        "max_str_file": max_str_file,
        "n_strings": n_strings,
        "files_with_text": n_files_with_text,
        "overlong": per_tier,
        "recommend": recommend,
        "truncated": truncated,
        "cancelled": cancelled,
    }


def decide_ctx(tier, info=None, cap=None):
    """按实测文本长度自动设定服务上下文：
    够短就用 4096 省内存/提速；按需要升档，但不超过该档上限。
    cap 可显式传入（自选档用用户设置）；否则取画像中的档位窗。

    自选档（custom）例外：上下文是用户自己选的，这里**不做收缩** ——
    用户选 8192 却被自动降到 4096，等于工具替用户改了他明确指定过的参数。
    """
    if cap is None:
        cap = TIER_PROFILES[tier]["ctx"]
    if tier == "custom":
        return cap
    if not info or not info.get("max_str"):
        return cap
    need_tokens = int(estimate_tokens("x" * info["max_str"]) * 2.2) + 512
    for ctx in (4096, 8192, 16384, 32768):
        if ctx >= cap:
            return cap
        if need_tokens <= ctx:
            return min(ctx, cap)
    return cap


def looks_like_ref_value(value):
    v = (value or "").strip()
    if not (2 <= len(v) <= 120):
        return False
    if re.search(r"\s", v):
        return False
    if v.lower().endswith((".xml", ".ltx", ".script", ".dds", ".ogg",
                           ".wav", ".txt", ".ini")):
        return False
    if "/" in v or "\\" in v or ":" in v:
        return False
    # ★ 2.0.24（IWP 全量审计 D9）：未解析报告要求下划线形态。旧判据
    # 把一切标识符形态字符串都算"未解析引用"，实测 CSV 里
    # ammo/gr/ap/fmj/OK/third/dummy 等 8 类全是 XML 配置参数值，
    # 不是条目 ID，稀释真正待确认项。能在索引里解析到的 ID 走
    # add_ref 第一条分支，不受本判据影响——只收紧报告，零覆盖变化。
    # XRay 文本表 ID 绝大多数带下划线（st_/ui_/mgr_/GENERATE_NAME_…）；
    # 无下划线的裸词 ID（controls/ok）本来就解析得到，不会进报告。
    if "_" not in v:
        return False
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_.\-]*$", v))


_XML_ILLEGAL_RE = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _xml_safe(value):
    """移除 XML 1.0 不能表示的控制字符/孤立代理项，避免写回后整表解析失败。"""
    return _XML_ILLEGAL_RE.sub("", value or "")


def patch_table_text(text, eid, lang, new_text):
    # 源表若含非法控制字符，原文整张 XML 就无法被 ElementTree 解析；
    # 写回前统一清理，保证产物始终是合法 XML。
    text = _xml_safe(text)
    block_re = re.compile(
        r"(<string\b[^>]*\bid\s*=\s*[\"']%s[\"'][^>]*>)(.*?)"
        r"(</string>)" % re.escape(eid), re.S)
    m = block_re.search(text)
    if not m:
        return text, False
    inner = m.group(2)
    tag = lang if isinstance(lang, str) and re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_.-]*", lang) else "text"
    if not re.search(r"<%s\b" % re.escape(tag), inner):
        tag = "text"
    tag_re = re.compile(r"(<%s\b[^>]*>)(.*?)(</%s>)" % (tag, tag), re.S)
    tm = tag_re.search(inner)
    # ★ 2.0.13 空白保真：源 <text> 内容的前导/尾随空白（缩进、换行）
    # 属于排版约定，模型译文常把它们抹平（实测 ui_hint_localization_rus
    # 丢前导空格）。这里把源内容的首尾空白套回译文，避免游戏内错位。
    src_body = tm.group(2) if tm else ""
    lead = src_body[:len(src_body) - len(src_body.lstrip())]
    trail = src_body[len(src_body.rstrip()):]
    new_body = _xml_safe(lead + str(new_text).strip() + trail)
    esc = (new_body.replace("&", "&amp;").replace("<", "&lt;")
           .replace(">", "&gt;"))
    if tm:
        inner2 = inner[:tm.start(2)] + esc + inner[tm.end(2):]
    else:
        inner2 = (inner.rstrip() + "\n\t\t<%s>%s</%s>\n\t"
                  % (tag, esc, tag))
    return text[:m.start(2)] + inner2 + text[m.end(2):], True


def merge_generated_extract(target, source):
    for key, value in source.items():
        if key == "chars_all_in":
            target.setdefault(key, set()).update(value or ())
        else:
            target[key] = value


_MOJIBAKE_CHARS = "ÐÑÃÂâ€™Ð„ÑŽ"

def files_equal(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                x = fa.read(1024 * 1024)
                y = fb.read(1024 * 1024)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError:
        return False

def looks_like_mojibake(text):
    """粗判"UTF-8 被单字节码表解出来的乱码"，只用于告警、不自动改判。

    正常俄文/中文文本里不会成片出现 Ð/Ñ/Ã/Â；而 UTF-8 的俄文（Д→D0 94）
    或中文被 cp1251 解码后，特征就是成片的拉丁扩充字符。
    """
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return False
    bad = sum(1 for c in letters if c in _MOJIBAKE_CHARS)
    return bad / len(letters) > 0.3

def read_text_any(path):
    """按 UTF-8（含 BOM）→ cp1251 顺序读取脚本/XML 原文，读不出时替换。"""
    try:
        raw = open(path, "rb").read()
    except OSError:
        return ""
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")
