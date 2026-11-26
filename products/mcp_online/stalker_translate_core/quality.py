# -*- coding: utf-8 -*-
"""确定性 LQA 金标评估与 SARIF/JUnit 报告。"""
from __future__ import annotations

import json
import re
from collections import Counter
from xml.sax.saxutils import escape, quoteattr

from .pipeline import (
    find_glue_residue,
    has_cjk,
    missing_big_numbers,
    missing_model_tokens,
    runtime_tokens_match,
    semantic_role_issues,
)

RULE_SEVERITY = {
    "missing_prediction": "error",
    "empty_output": "error",
    "untranslated": "error",
    "target_missing": "error",
    "mask_residue": "error",
    "runtime_token_damage": "error",
    "literal_newline_damage": "error",
    "number_fidelity": "error",
    "model_fidelity": "error",
    "glue_residue": "error",
    "semantic_role_conflict": "error",
    "glossary_missing": "error",
    "forbidden_term": "error",
}
RULE_MESSAGES = {
    "missing_prediction": "缺少候选译文",
    "empty_output": "候选译文为空",
    "untranslated": "候选译文与源文相同",
    "target_missing": "候选译文不含目标语言文字",
    "mask_residue": "候选译文残留未还原掩码",
    "runtime_token_damage": "运行期占位符或顺序不一致",
    "literal_newline_damage": "字面换行符数量不一致",
    "number_fidelity": "关键数字缺失或改写",
    "model_fidelity": "明确型号缺失或改写",
    "glue_residue": "检测到中英或中俄粘连残词",
    "semantic_role_conflict": "检测到人称或付款语义方向冲突",
    "glossary_missing": "金标要求的术语译名缺失",
    "forbidden_term": "候选译文包含金标禁止的变体",
}


def _issue(code, evidence=()):
    return {
        "code": code,
        "severity": RULE_SEVERITY[code],
        "message": RULE_MESSAGES[code],
        "evidence": [str(item) for item in evidence][:8],
    }


def detect_lqa_issues(source, candidate, *, source_lang="eng", target_lang="chs",
                      required_terms=None, forbidden_terms=None):
    source = "" if source is None else str(source)
    candidate = "" if candidate is None else str(candidate)
    if not candidate.strip():
        return [_issue("empty_output")]
    if candidate.strip() == source.strip():
        return [_issue("untranslated")]
    issues = []
    if target_lang == "chs" and not has_cjk(candidate):
        issues.append(_issue("target_missing"))
    if re.search(r"\$\$[TR]\d+\$\$", candidate):
        issues.append(_issue("mask_residue"))
    if not runtime_tokens_match(source, candidate):
        issues.append(_issue("runtime_token_damage"))
    if source.count("\\n") != candidate.count("\\n"):
        issues.append(_issue("literal_newline_damage", [
            source.count("\\n"), candidate.count("\\n")]))
    numbers = missing_big_numbers(source, candidate)
    if numbers:
        issues.append(_issue("number_fidelity", numbers))
    models = missing_model_tokens(source, candidate)
    if models:
        issues.append(_issue("model_fidelity", models))
    glue = find_glue_residue(candidate)
    if glue:
        issues.append(_issue("glue_residue", glue))
    roles = semantic_role_issues(source, candidate, source_lang)
    if roles:
        issues.append(_issue("semantic_role_conflict", roles))
    for term, expected in (required_terms or {}).items():
        if str(expected).casefold() not in candidate.casefold():
            issues.append(_issue("glossary_missing", [term]))
    for term in forbidden_terms or ():
        if str(term).casefold() in candidate.casefold():
            issues.append(_issue("forbidden_term", [term]))
    return issues


def _validate_cases(cases):
    seen = set()
    for case in cases:
        case_id = str(case.get("id", ""))
        if not case_id or case_id in seen:
            raise ValueError("金标 ID 为空或重复：%s" % case_id)
        seen.add(case_id)
        if not case.get("file") or not case.get("source_id"):
            raise ValueError("金标缺少 file/source_id：%s" % case_id)
        if not isinstance(case.get("source"), str):
            raise ValueError("金标 source 必须是字符串：%s" % case_id)
        expected = case.get("expected_issues", [])
        if not isinstance(expected, list) or any(
                not isinstance(rule, str) or rule not in RULE_SEVERITY
                for rule in expected):
            raise ValueError("金标 expected_issues 非法：%s" % case_id)


def evaluate_lqa(cases):
    _validate_cases(cases)
    results = []
    counts = Counter()
    per_rule = {}
    usage = Counter()
    correction_total = correction_survived = 0
    for case in cases:
        candidate = case.get("candidate")
        detected = ([] if candidate is None else detect_lqa_issues(
            case["source"], candidate,
            source_lang=case.get("source_lang", "eng"),
            target_lang=case.get("target_lang", "chs"),
            required_terms=case.get("required_terms"),
            forbidden_terms=case.get("forbidden_terms")))
        expected = set(case.get("expected_issues", []))
        actual = {item["code"] for item in detected}
        matched = expected & actual
        false_positives = actual - expected
        false_negatives = expected - actual
        missing_prediction = candidate is None
        status = "missing_prediction" if missing_prediction else (
            "matched" if not false_positives and not false_negatives else "mismatch")
        counts["true_positives"] += len(matched)
        counts["false_positives"] += len(false_positives)
        counts["false_negatives"] += len(false_negatives)
        counts["cases"] += 1
        counts["matched_cases"] += status == "matched"
        for rule in expected | actual:
            stats = per_rule.setdefault(rule, Counter())
            stats["expected"] += rule in expected
            stats["detected"] += rule in actual
            stats["true_positives"] += rule in matched
            stats["false_positives"] += rule in false_positives
            stats["false_negatives"] += rule in false_negatives
        case_usage = case.get("usage") or {}
        for key in ("input_tokens", "output_tokens", "cached_input_tokens", "cost"):
            value = case_usage.get(key, 0)
            if isinstance(value, (int, float)) and value >= 0:
                usage[key] += value
        human = case.get("human_correction")
        if isinstance(human, str):
            correction_total += 1
            human_issues = detect_lqa_issues(
                case["source"], human,
                source_lang=case.get("source_lang", "eng"),
                target_lang=case.get("target_lang", "chs"),
                required_terms=case.get("required_terms"),
                forbidden_terms=case.get("forbidden_terms"))
            correction_survived += not any(
                item["severity"] == "error" for item in human_issues)
        results.append({
            "id": case["id"],
            "file": str(case["file"]).replace("\\", "/"),
            "source_id": str(case["source_id"]),
            "expected": sorted(expected),
            "detected": detected,
            "false_positives": sorted(false_positives),
            "false_negatives": sorted(false_negatives),
            "status": status,
            "tags": list(case.get("tags") or ())[:16],
            "review_status": case.get("review_status", "unreviewed"),
            "model_score": case.get("model_score"),
        })
    tp = counts["true_positives"]
    fp = counts["false_positives"]
    fn = counts["false_negatives"]
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "_format": "ai-translate-lqa/1",
        "metrics": {
            **dict(counts),
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall)
                  if precision + recall else 0.0,
            "human_corrections": correction_total,
            "human_correction_survival": (
                correction_survived / correction_total if correction_total else None),
        },
        "per_rule": per_rule,
        "usage": dict(usage),
        "results": results,
    }


def gate_lqa(cases, *, min_precision=1.0, min_recall=1.0,
             max_false_positives=0, max_false_negatives=0):
    report = evaluate_lqa(cases)
    metrics = report["metrics"]
    report["ok"] = bool(cases) and (
        metrics["precision"] >= min_precision
        and metrics["recall"] >= min_recall
        and metrics["false_positives"] <= max_false_positives
        and metrics["false_negatives"] <= max_false_negatives
        and metrics["matched_cases"] == metrics["cases"])
    report["gate"] = {
        "min_precision": min_precision,
        "min_recall": min_recall,
        "max_false_positives": max_false_positives,
        "max_false_negatives": max_false_negatives,
        "llm_scores_are_gates": False,
    }
    return report


def render_sarif(report):
    rules = sorted({rule for result in report["results"]
                    for rule in result["expected"] + result["false_positives"]
                    + result["false_negatives"]} | {"missing_prediction"})
    results = []
    for case in report["results"]:
        detected = {item["code"]: item for item in case["detected"]}
        for rule in case["false_positives"]:
            item = detected[rule]
            results.append({
                "ruleId": rule,
                "level": item["severity"],
                "message": {"text": "金标未标注：%s" % item["message"]},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": case["file"]},
                        "logicalLocations": [{"name": case["source_id"]}],
                    },
                }],
                "properties": {"caseId": case["id"], "evidence": item["evidence"]},
            })
        for rule in case["false_negatives"]:
            results.append({
                "ruleId": rule,
                "level": RULE_SEVERITY[rule],
                "message": {"text": "金标期望问题未检出：%s" % RULE_MESSAGES[rule]},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": case["file"]},
                        "logicalLocations": [{"name": case["source_id"]}],
                    },
                }],
                "properties": {"caseId": case["id"]},
            })
        if case["status"] == "missing_prediction":
            results.append({
                "ruleId": "missing_prediction",
                "level": "error",
                "message": {"text": RULE_MESSAGES["missing_prediction"]},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": case["file"]},
                        "logicalLocations": [{"name": case["source_id"]}],
                    },
                }],
                "properties": {"caseId": case["id"]},
            })
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "STALKER AI Translate LQA",
                "rules": [{
                    "id": rule,
                    "shortDescription": {"text": RULE_MESSAGES[rule]},
                    "defaultConfiguration": {"level": RULE_SEVERITY[rule]},
                } for rule in rules],
            }},
            "results": results,
            "properties": {
                "metrics": report["metrics"],
                "llmScoresAreGates": False,
            },
        }],
    }


def render_junit(report):
    total = len(report["results"])
    failures = sum(case["status"] != "matched" for case in report["results"])
    lines = ["<?xml version=\"1.0\" encoding=\"utf-8\"?>",
             "<testsuites tests=\"%d\" failures=\"%d\">" % (total, failures),
             "  <testsuite name=\"lqa\" tests=\"%d\" failures=\"%d\">"
             % (total, failures)]
    for case in report["results"]:
        name = "%s::%s" % (case["file"], case["source_id"])
        lines.append("    <testcase name=%s classname=%s>"
                     % (quoteattr(name), quoteattr("lqa")))
        if case["status"] != "matched":
            message = ("缺少候选译文" if case["status"] == "missing_prediction"
                       else "LQA 金标不一致")
            lines.append("      <failure message=%s type=\"lqa-gate\">%s</failure>"
                         % (quoteattr(message), escape(case["status"])))
        lines.append("      <system-out>%s</system-out>" % escape(json.dumps({
            "id": case["id"], "status": case["status"],
            "false_positives": case["false_positives"],
            "false_negatives": case["false_negatives"],
        }, ensure_ascii=False, sort_keys=True)))
        lines.append("    </testcase>")
    lines.extend(["  </testsuite>", "</testsuites>"])
    return "\n".join(lines)
