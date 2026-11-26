#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""运行 LQA 金标回归并输出 JSON、SARIF 2.1.0 与 JUnit。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from stalker_translate_core.quality import (  # noqa: E402
    gate_lqa,
    render_junit,
    render_sarif,
)


def load_cases(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if text.lstrip().startswith("["):
        cases = json.loads(text)
    else:
        cases = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(cases, list):
        raise ValueError("金标或预测文件必须是 JSON 数组/JSONL")
    return cases


def merge_predictions(cases, predictions):
    by_id = {}
    for item in predictions:
        case_id = str(item.get("id", ""))
        if not case_id or case_id in by_id:
            raise ValueError("预测 ID 为空或重复：%s" % case_id)
        by_id[case_id] = item
    merged = []
    for case in cases:
        item = dict(case)
        prediction = by_id.pop(str(case["id"]), None)
        if prediction is not None:
            item["candidate"] = prediction.get("candidate")
            if "usage" in prediction:
                item["usage"] = prediction["usage"]
            if "model_score" in prediction:
                item["model_score"] = prediction["model_score"]
        else:
            item["candidate"] = None
        merged.append(item)
    if by_id:
        raise ValueError("预测含金标之外的 ID：%s" % ", ".join(sorted(by_id)))
    return merged


def write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", default=str(ROOT / "tests/lqa_golden.jsonl"))
    parser.add_argument("--predictions")
    parser.add_argument("--sarif")
    parser.add_argument("--junit")
    parser.add_argument("--json")
    parser.add_argument("--min-precision", type=float, default=1.0)
    parser.add_argument("--min-recall", type=float, default=1.0)
    parser.add_argument("--max-false-positives", type=int, default=0)
    parser.add_argument("--max-false-negatives", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.golden)
        if args.predictions:
            cases = merge_predictions(cases, load_cases(args.predictions))
        report = gate_lqa(
            cases, min_precision=args.min_precision,
            min_recall=args.min_recall,
            max_false_positives=args.max_false_positives,
            max_false_negatives=args.max_false_negatives)
        if args.sarif:
            write_text(args.sarif, json.dumps(
                render_sarif(report), ensure_ascii=False, indent=2) + "\n")
        if args.junit:
            write_text(args.junit, render_junit(report) + "\n")
        if args.json:
            write_text(args.json, json.dumps(
                report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print("LQA 输入/报告失败：%s" % exc, file=sys.stderr)
        return 2
    metrics = report["metrics"]
    print("LQA %d 条：精确率 %.4f / 召回率 %.4f / FP %d / FN %d" % (
        metrics["cases"], metrics["precision"], metrics["recall"],
        metrics["false_positives"], metrics["false_negatives"]))
    print("闸门：%s（LLM 评分不参与硬门）" % ("通过" if report["ok"] else "未通过"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
