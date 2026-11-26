# -*- coding: utf-8 -*-
"""术语库深度审计（只读）。维度：

  1 原始 JSON 重复键（json.load 会静默折叠，用 object_pairs_hook 抓）
  2 无效条目：空键/空值、非字符串值、下划线元键混入数据
  3 值健康：译文无 CJK、值==键、首尾空白、乱码（looks_like_mojibake）
  4 跨类目冲突：同 casefold 键不同值（lookup 索引首中优先，须确认 canon 赢）
  5 canon/names 层超长键（partial_translation 会子串替换，>60 字符危险）
  6 _meta 一致性：variants/case_sensitive/quote_required 引用了不存在的键
  7 内置词表冲突：glossary_builtin 与 termbase 同键不同值（termbase 优先，须人工核对）
  8 覆盖缺口：IWP 复检工单中 latin_residue token 不在词库（候选新增）
  9 值占位符异常：值含 %s/$var 等源侧标记

输出：audit-reports/TERMBASE_DEEP_AUDIT_<stamp>.json + 控制台摘要。只读。
"""
import glob
import json
import os
import re
import sys
from collections import Counter, OrderedDict, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
from stalker_translate_core.xray import looks_like_mojibake  # noqa: E402

REPORT_DIR = os.path.join(ROOT, "audit-reports")
TB = os.environ.get("STALKER_TERMBASE") or os.path.join(
    ROOT, "assets", "stalker_termbase.json")
GL = os.environ.get("STALKER_GLOSSARY") or os.path.join(
    ROOT, "assets", "glossary_builtin.json")

CJK = re.compile(r"[\u4e00-\u9fff]")
PH = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*|%[sdif]|%c\[|\{[a-z_]+\}")


def dup_hook(pairs):
    seen = {}
    dups = []
    for k, v in pairs:
        if k in seen:
            dups.append(k)
        seen[k] = v
    return seen, dups


def load_raw(path):
    """返回 (data, dup_keys:list[(context, key)])。"""
    dups = []

    def hook(pairs):
        obj, d = dup_hook(pairs)
        dups.extend(d)
        return obj

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh, object_pairs_hook=hook)
    return data, dups


def run_audit(tb_path=TB, gl_path=GL):
    data, raw_dups = load_raw(tb_path)
    meta = data.get("_meta", {})
    findings = defaultdict(list)

    categories = [k for k in data if k != "_meta"]
    for key in raw_dups:
        findings["raw_dup_key"].append({"key": key})

    layer_of = {}
    for layer, cats in (meta.get("layers") or {}).items():
        for c in cats:
            layer_of[c] = layer

    low_index = {}  # casefold -> (category, key, value) 首中（模拟 lookup）
    for cat in categories:
        table = data.get(cat)
        if not isinstance(table, dict):
            findings["non_dict_category"].append({"category": cat})
            continue
        for key, value in table.items():
            name = str(key).strip()
            if not name:
                findings["empty_key"].append({"category": cat})
                continue
            if isinstance(value, (dict, list)):
                findings["non_scalar_value"].append(
                    {"category": cat, "key": name})
                continue
            value = str(value)
            if not value.strip():
                findings["empty_value"].append({"category": cat, "key": name})
                continue
            if value != value.strip():
                findings["value_whitespace"].append(
                    {"category": cat, "key": name})
            if name.startswith("_"):
                findings["meta_key_in_data"].append(
                    {"category": cat, "key": name})
            low = name.casefold()
            if low not in low_index:
                low_index[low] = (cat, name, value)
            elif low_index[low][2] != value:
                findings["cross_conflict"].append({
                    "key": low_index[low][1], "winner_cat": low_index[low][0],
                    "winner": low_index[low][2],
                    "loser_cat": cat, "loser": value})
            # 值健康
            src_is_wordish = re.search(r"[A-Za-zА-Яа-яЁё]", name)
            if src_is_wordish and not CJK.search(value):
                findings["value_no_cjk"].append(
                    {"category": cat, "key": name, "value": value[:40]})
            if value == name:
                findings["value_eq_key"].append({"category": cat, "key": name})
            if looks_like_mojibake(value):
                findings["value_mojibake"].append(
                    {"category": cat, "key": name, "value": value[:40]})
            if PH.search(value):
                # 占位符语义：值新增源侧标记=缺陷；值比键少占位符=丢失缺陷
                added = set(PH.findall(value)) - set(PH.findall(name))
                missing = set(PH.findall(name)) - set(PH.findall(value))
                if added:
                    findings["value_placeholder_added"].append(
                        {"category": cat, "key": name, "value": value[:40]})
                if missing:
                    findings["value_placeholder_lost"].append(
                        {"category": cat, "key": name, "value": value[:40]})
            # 层级危险键
            if layer_of.get(cat) in ("canon", "names") and len(name) > 60:
                findings["long_partial_key"].append(
                    {"category": cat, "key": name[:70]})

    # _meta 引用一致性
    for field in ("variants",):
        for k in (meta.get(field) or {}):
            if str(k).casefold() not in low_index:
                findings["meta_ref_missing"].append(
                    {"field": field, "key": str(k)})
    for field in ("case_sensitive", "quote_required"):
        for k in (meta.get(field) or []):
            if str(k).casefold() not in low_index:
                findings["meta_ref_missing"].append(
                    {"field": field, "key": str(k)})

    # 内置词表冲突
    gl_data, gl_dups = load_raw(gl_path)

    def walk(table, out, depth=0):
        for k, v in table.items():
            name = str(k).strip()
            if not name or name.startswith("_"):
                continue
            if isinstance(v, dict) and depth < 8:
                walk(v, out, depth + 1)
            elif not isinstance(v, (dict, list)):
                out[name] = str(v)

    gl_flat = {}
    walk(gl_data, gl_flat)
    for k, v in gl_flat.items():
        hit = low_index.get(k.casefold())
        if hit and hit[2] != v:
            findings["builtin_conflict"].append(
                {"key": k, "termbase": hit[2][:40], "builtin": v[:40]})

    # 覆盖缺口：IWP 复检工单 latin_residue token
    gaps = Counter()
    for path in sorted(glob.glob(os.path.join(
            REPORT_DIR, "IWP_REWORK_LIST_*.json"))):
        with open(path, encoding="utf-8") as handle:
            wl = json.load(handle)
        for e in wl.get("rework_warn", []):
            if e.get("kind") != "latin_residue":
                continue
            for tok in e.get("tokens", []):
                if tok.casefold() not in low_index:
                    gaps[tok] += 1

    summary = {k: len(v) for k, v in findings.items()}
    out = {
        "_meta": {"termbase": tb_path, "glossary": gl_path,
                  "categories": len(categories), "summary": summary},
        "findings": {k: v for k, v in findings.items()},
        "coverage_gap_candidates": [
            {"token": t, "refs": n} for t, n in gaps.most_common()],
    }
    return out


def main():
    result = run_audit(TB, GL)
    print("== 术语库深度审计 ==")
    print("词库: %s" % TB)
    for k, v in sorted(result["_meta"]["summary"].items(), key=lambda x: -x[1]):
        print("  %-18s %d" % (k, v))
    print("词库外高频缺词 top30: %s"
          % [(c["token"], c["refs"]) for c in result["coverage_gap_candidates"][:30]])
    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M")
    out_path = os.path.join(REPORT_DIR, "TERMBASE_DEEP_AUDIT_%s.json" % stamp)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print("报告: %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
