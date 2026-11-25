# -*- coding: utf-8 -*-
"""X-Ray 文本 XML 的只读扫描与保留格式回写。"""
from __future__ import annotations

import codecs
import html
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

STRING_RE = re.compile(
    r"<string\b[^>]*\bid\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</string>",
    re.S | re.I,
)
SLOT_RE = re.compile(r"<(text|rus|eng)\b[^>]*>(.*?)</\1>", re.S | re.I)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
XML_DECL_RE = re.compile(r"^\ufeff?\s*<\?xml[^>]*encoding\s*=\s*[\"'][^\"']+[\"']",
                         re.I)


@dataclass(frozen=True)
class TextEntry:
    source_id: str
    lang: str
    source: str
    start: int
    end: int


@dataclass(frozen=True)
class TextFile:
    path: Path
    text: str
    encoding: str
    bom: bool
    entries: tuple[TextEntry, ...]


def read_encoded(path: Path) -> tuple[str, str, bool] | None:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(enc)
            return text, "utf-8-sig" if enc == "utf-8-sig" else enc, raw.startswith(
                codecs.BOM_UTF8)
        except UnicodeDecodeError:
            continue
    return None


def mask_comments(text: str) -> str:
    return COMMENT_RE.sub(lambda m: re.sub(r"[^\r\n]", " ", m.group(0)), text)


def scan_text_file(path: Path, source_lang: str = "auto") -> TextFile | None:
    loaded = read_encoded(path)
    if loaded is None:
        return None
    text, encoding, bom = loaded
    masked = mask_comments(text)
    preferred = {"auto": ("text", "rus", "eng"), "text": ("text",),
                 "rus": ("rus",), "eng": ("eng",)}.get(source_lang,
                                                        ("text", "rus", "eng"))
    entries: list[TextEntry] = []
    for match in STRING_RE.finditer(masked):
        body_start, body_end = match.span(2)
        body = masked[body_start:body_end]
        slots = list(SLOT_RE.finditer(body))
        chosen = None
        for lang in preferred:
            chosen = next((s for s in slots if s.group(1).lower() == lang), None)
            if chosen is not None:
                break
        if chosen is None:
            continue
        start = body_start + chosen.start(2)
        end = body_start + chosen.end(2)
        raw = text[start:end]
        entries.append(TextEntry(match.group(1), chosen.group(1).lower(),
                                 html.unescape(raw), start, end))
    return TextFile(path=path, text=text, encoding=encoding, bom=bom,
                    entries=tuple(entries))


def escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_text_file(source: TextFile, replacements: dict[int, str]) -> None:
    text = source.text
    for entry in sorted(source.entries, key=lambda e: e.start, reverse=True):
        if entry.start not in replacements:
            continue
        text = text[:entry.start] + escape_text(replacements[entry.start]) + text[entry.end:]
    enc = source.encoding
    if enc == "utf-8-sig":
        payload = text.lstrip("\ufeff").encode("utf-8-sig")
    else:
        try:
            text.encode(enc)
            payload = text.encode(enc)
        except UnicodeEncodeError:
            enc = "utf-8"
            payload = text.encode("utf-8")
            if XML_DECL_RE.match(text):
                text = XML_DECL_RE.sub(
                    lambda m: re.sub(r"encoding\s*=\s*[\"'][^\"']+[\"']",
                                     'encoding="utf-8"', m.group(0), flags=re.I),
                    text, count=1)
                payload = text.encode("utf-8")
    path = source.path
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def project_xml_files(root: Path, output_dir: Path, max_files: int,
                      max_file_bytes: int) -> list[Path]:
    result: list[Path] = []
    output_real = output_dir.resolve()
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs
                         if not d.startswith(".")
                         and (Path(base) / d).resolve() != output_real)
        for name in sorted(files):
            if not name.lower().endswith(".xml"):
                continue
            path = Path(base) / name
            try:
                if path.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
            result.append(path)
            if len(result) >= max_files:
                return result
    return result
