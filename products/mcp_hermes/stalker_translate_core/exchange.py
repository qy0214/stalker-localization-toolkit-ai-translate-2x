# -*- coding: utf-8 -*-
"""TBX / TMX 最小无损交换；不替代项目内 JSON 主格式。"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path


def _write(root: ET.Element, path: str | Path) -> None:
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(str(path), encoding="utf-8", xml_declaration=True)


def export_tbx(entries: Mapping[str, str], path: str | Path,
               details: Mapping[str, Mapping] | None = None) -> None:
    root = ET.Element("martif", {"type": "TBX-Basic", "xml:lang": "en"})
    header = ET.SubElement(root, "martifHeader")
    desc = ET.SubElement(header, "fileDesc")
    statement = ET.SubElement(desc, "fileStmt")
    ET.SubElement(statement, "name").text = "STALKER AI Translate termbase"
    text = ET.SubElement(root, "text")
    body = ET.SubElement(text, "body")
    for index, (source, target) in enumerate(entries.items(), 1):
        concept = ET.SubElement(body, "conceptEntry", {"id": "term-%d" % index})
        meta = dict((details or {}).get(source) or {})
        if meta.get("status"):
            ET.SubElement(concept, "descrip", {"type": "status"}).text = str(meta["status"])
        for lang, term in (("ru", source), ("zh-Hans", target)):
            language = ET.SubElement(concept, "languageSet", {"lang": lang})
            tig = ET.SubElement(language, "tig")
            ET.SubElement(tig, "term").text = str(term)
            if lang == "ru" and meta.get("provenance"):
                ET.SubElement(tig, "note").text = str(meta["provenance"])
    _write(root, path)


def import_tbx(path: str | Path) -> list[dict]:
    root = ET.parse(str(path)).getroot()
    result = []
    for concept in root.iter("conceptEntry"):
        terms = {}
        meta = {"id": concept.get("id") or ""}
        for descrip in concept.findall("descrip"):
            if descrip.get("type"):
                meta[descrip.get("type")] = (descrip.text or "").strip()
        for language in concept.findall("languageSet"):
            lang = language.get("lang") or ""
            tig = language.find("tig")
            term = tig.find("term") if tig is not None else None
            if term is not None and term.text:
                terms[lang] = term.text.strip()
            if tig is not None:
                note = tig.find("note")
                if note is not None and note.text:
                    meta["provenance"] = note.text.strip()
        source = terms.get("ru") or terms.get("en")
        target = terms.get("zh-Hans") or terms.get("zh")
        if source and target:
            result.append({"source": source, "target": target, **meta})
    return result


def export_tmx(entries: Mapping[str, str], path: str | Path,
               source_lang: str = "ru", target_lang: str = "zh-Hans") -> None:
    root = ET.Element("tmx", {"version": "1.4"})
    body = ET.SubElement(root, "body")
    for index, (source, target) in enumerate(entries.items(), 1):
        unit = ET.SubElement(body, "tu", {"id": "tu-%d" % index})
        for lang, value in ((source_lang, source), (target_lang, target)):
            variant = ET.SubElement(unit, "tuv", {"xml:lang": lang})
            ET.SubElement(variant, "seg").text = str(value)
    _write(root, path)


def import_tmx(path: str | Path) -> list[dict]:
    root = ET.parse(str(path)).getroot()
    result = []
    for unit in root.iter("tu"):
        variants = {}
        for tuv in unit.findall("tuv"):
            lang = tuv.get("{http://www.w3.org/XML/1998/namespace}lang") \
                or tuv.get("lang") or ""
            seg = tuv.find("seg")
            if seg is not None:
                variants[lang] = "".join(seg.itertext())
        source = variants.get("ru") or variants.get("en") or variants.get("*")
        target = variants.get("zh-Hans") or variants.get("zh")
        if source and target:
            result.append({"source": source, "target": target,
                           "id": unit.get("id") or ""})
    return result
