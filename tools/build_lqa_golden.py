#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从本机真实 MOD 人工/机器产物构建可复现的 LQA 金标集。"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from stalker_translate_core.pipeline import (  # noqa: E402
    _RUNTIME_TOKEN_RE,
    _has_translatable_text,
    detect_text_lang,
    looks_like_code_or_debug,
    looks_like_generated_noise,
    looks_like_identifier_text,
    looks_like_technical_text,
    missing_big_numbers,
    missing_model_tokens,
    runtime_tokens_match,
    semantic_role_issues,
    is_player_facing_label,
)
from stalker_translate_core.quality import detect_lqa_issues  # noqa: E402
from stalker_translate_core.vendor import load_vendor  # noqa: E402
from stalker_translate_core.xray import read_encoding_chain  # noqa: E402


def read_tables(root, parser):
    tables = {}
    text_root = Path(root) / "configs/text"
    if not text_root.is_dir():
        return tables
    for path in sorted(text_root.rglob("*.xml")):
        rel = path.relative_to(Path(root)).as_posix()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                entities = parser.parse_xray_text_xml(
                    str(path), read_encoding_chain(str(path))) or []
        except Exception:
            continue
        values = {}
        for entity in entities:
            texts = getattr(entity, "texts", None) or {}
            value = texts.get("text") or texts.get("rus") or texts.get("eng")
            if entity.id and isinstance(value, str):
                values[entity.id] = value
        if values:
            tables[rel] = values
    return tables


def translatable(rel, source):
    if not 20 <= len(source) <= 800 or detect_text_lang(source) not in ("eng", "rus"):
        return False
    if not _has_translatable_text(source) or looks_like_code_or_debug(rel, source):
        return False
    if looks_like_generated_noise(rel, source) or looks_like_technical_text(source):
        return False
    return not (looks_like_identifier_text(source)
                and not is_player_facing_label(rel, source))


def select_diverse(items, count, seed):
    rng = random.Random(seed)
    rng.shuffle(items)
    by_file = defaultdict(int)
    selected = []
    for item in sorted(items, key=lambda row: by_file[row["file"]]):
        if by_file[item["file"]] >= 3:
            continue
        selected.append(item)
        by_file[item["file"]] += 1
        if len(selected) == count:
            return selected
    raise ValueError("真实语料不足：需要 %d 条，只有 %d 条" % (count, len(selected)))


def case_id(prefix, rel, source_id):
    digest = hashlib.sha1((rel + "::" + source_id).encode(
        "utf-8"), usedforsecurity=False).hexdigest()[:12]
    return "%s-%s-%s" % (prefix, digest, source_id)


def build(aera_root, aera_chs_root):
    parser = load_vendor(str(ROOT / "assets/xray_pkg.zip"))
    human_src = read_tables(aera_root, parser)
    human_out = read_tables(aera_chs_root, parser)
    pool = []
    for rel, entries in human_src.items():
        outputs = human_out.get(rel, {})
        for source_id, source in entries.items():
            candidate = outputs.get(source_id)
            if not translatable(rel, source) or not candidate:
                continue
            issues = detect_lqa_issues(source, candidate)
            if any(issue["severity"] == "error" for issue in issues):
                continue
            pool.append({
                "id": case_id("aera-human", rel, source_id),
                "dataset": "real_mod_lqa_v1",
                "file": rel,
                "source_id": source_id,
                "source": source,
                "candidate": candidate,
                "human_correction": candidate,
                "expected_issues": [],
                "tags": ["human", "aera", "clean"],
                "review_status": "human_gold",
            })
    human = select_diverse(pool, 200, 20261126)

    quotas = {
        "untranslated": 40,
        "number_fidelity": 40,
        "model_fidelity": 30,
        "runtime_token_damage": 20,
        "glue_residue": 15,
        "semantic_role_conflict": 15,
    }
    buckets = defaultdict(list)
    for item in pool:
        source = item["source"]
        candidate = item["candidate"]
        mutations = {"untranslated": source}
        if "%c[" not in source and runtime_tokens_match(source, candidate):
            changed = re.sub(r"\d{2,}", "0", candidate)
            if changed != candidate and "number_fidelity" in {
                    issue["code"] for issue in detect_lqa_issues(source, changed)}:
                mutations["number_fidelity"] = changed
        models = missing_model_tokens(source, "中文占位译文。")
        if models:
            changed = candidate
            for model in models:
                changed = changed.replace(model, "X-99")
            if changed != candidate and "model_fidelity" in {
                    issue["code"] for issue in detect_lqa_issues(source, changed)}:
                mutations["model_fidelity"] = changed
        tokens = _RUNTIME_TOKEN_RE.findall(source)
        if tokens and tokens[0] in candidate:
            mutations["runtime_token_damage"] = candidate.replace(tokens[0], "", 1)
        glued = candidate + "测试Glue"
        if "glue_residue" in {issue["code"] for issue in detect_lqa_issues(source, glued)}:
            mutations["glue_residue"] = glued
        semantic_candidates = [
            candidate.replace("你", "我"), candidate.replace("我", "你"),
            candidate.replace("付给你", "付给我").replace("交给你", "交给我"),
            candidate.replace("付给我", "付给你").replace("交给我", "交给你"),
        ]
        for changed in semantic_candidates:
            if changed != candidate and semantic_role_issues(source, changed):
                mutations["semantic_role_conflict"] = changed
                break
        for rule, changed in mutations.items():
            codes = {issue["code"] for issue in detect_lqa_issues(source, changed)
                     if issue["severity"] == "error"}
            if rule not in codes:
                continue
            buckets[rule].append({
                "id": case_id("aera-derived-%s" % rule, item["file"], item["source_id"]),
                "dataset": "real_mod_lqa_v1",
                "file": item["file"],
                "source_id": item["source_id"],
                "source": source,
                "candidate": changed,
                "expected_issues": sorted(codes),
                "tags": ["derived", "aera", rule],
                "review_status": "derived_gold",
            })
    machine = []
    for index, (rule, count) in enumerate(quotas.items()):
        machine.extend(select_diverse(buckets[rule], count, 20261127 + index))
    return human + machine


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aera", default=r"C:\Users\Admin\Desktop\aera\gamedata")
    parser.add_argument("--aera-chs",
                        default=r"C:\Users\Admin\Desktop\aera\CHS\gamedata")
    parser.add_argument("--output", default=str(ROOT / "tests/lqa_golden.jsonl"))
    args = parser.parse_args(argv)
    cases = build(args.aera, args.aera_chs)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False, sort_keys=True)
                  for case in cases) + "\n", encoding="utf-8", newline="\n")
    print("已构建真实 MOD 金标 %d 条（人工 200 / 受控缺陷 160）：%s"
          % (len(cases), output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
