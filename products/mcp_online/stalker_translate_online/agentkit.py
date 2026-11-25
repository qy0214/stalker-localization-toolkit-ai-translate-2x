# -*- coding: utf-8 -*-
"""0.3.0 知识技能薄工具：plan / extract / merge（对话 AI 的三拍舞步）。

设计契约（憨憨 Agent 友好）：
- AI 永远不见 64 位请求键：句子以 sid（源文哈希前 8 位，跨轮稳定）寻址；
- 清单不倾倒：每批只落一个紧凑 TSV 文件，stdout 仅 ≤400B 摘要；
- 无认领状态机：extract 幂等——已作答/已直取的句子自动从后续批次消失；
- 词库三层执法：GLOSSARY-CORE.md（事前）+ 每行 hint（事中）+ merge
  术语回填闸门（事后），统一性不依赖 AI 自觉。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .security import allowed_path

NL_MARK = "\\n"  # TSV 内的换行占位，merge 端还原
DIALOG_MODE_LIMIT_UNIQUE = 3000     # 超过建议引擎轨
DIALOG_MODE_HARD_UNIQUE = 20000     # 超过必须熔断


def sid_for(sources: list[str]) -> str:
    joined = "|||".join(str(s) for s in sources)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:8]


def esc(text: str) -> str:
    return str(text).replace("\r\n", "\n").replace("\n", NL_MARK).replace("\t", " ")


def unesc(text: str) -> str:
    return text.replace(NL_MARK, "\n")


class AgentKit:
    def __init__(self, jobs, cfg):
        self.jobs = jobs
        self.cfg = cfg

    # ── 内部：跑一遍收集/填充，按唯一源文聚合 ──────────────────────
    def _collect_unique(self, input_dir: str, output_dir: str | None,
                        source_lang: str, target_lang: str) -> dict[str, Any]:
        result = self.jobs.collect_pending(input_dir, output_dir,
                                           source_lang, target_lang)
        uniques: dict[str, dict[str, Any]] = {}
        for item in result["items"]:
            srcs = [str(s) for s in (item.get("sources") or [])]
            joined = "|||".join(srcs)
            rec = uniques.get(joined)
            if rec is None:
                hints: dict[str, str] = {}
                for k, v in (item.get("hint") or []):
                    hints[str(k)] = str(v)
                uniques[joined] = {
                    "sid": sid_for(srcs), "kind": item.get("kind", "single"),
                    "sources": srcs, "keys": [item["key"]],
                    "hint": hints,
                }
            else:
                rec["keys"].append(item["key"])
        rows = sorted(uniques.values(),
                      key=lambda r: (-len(r["keys"]),
                                     0 if r["kind"] == "single" else 1,
                                     -max(len(s) for s in r["sources"])))
        result["uniques"] = rows
        return result

    def kit_dir(self) -> Path:
        return Path(self.cfg.cache_path) / "kit"

    # ── plan：全链收集第一遍 + 报价（直取/筛选当场落盘） ──────────
    def plan(self, input_dir: str, output_dir: str | None = None,
             source_lang: str = "auto", target_lang: str = "chs") -> dict:
        base = self._collect_unique(input_dir, output_dir, source_lang,
                                    target_lang)
        uniques = base["uniques"]
        pending_chars = sum(len(s) for r in uniques for s in r["sources"])
        total_keys = sum(len(r["keys"]) for r in uniques)
        n = max(1, len(uniques))
        mode = "dialog"
        if len(uniques) > DIALOG_MODE_HARD_UNIQUE:
            mode = "engine_required"
        elif len(uniques) > DIALOG_MODE_LIMIT_UNIQUE:
            mode = "engine_recommended"
        return {
            "output_dir": base["output_dir"],
            "unique_sentences": len(uniques),
            "pending_keys": total_keys,
            "pending_chars": pending_chars,
            "avg_keys_per_sentence": round(total_keys / n, 2),
            "dialog_batches_at_200": (len(uniques) + 199) // 200,
            "mode": mode,
            "note": ("对话技能适用" if mode == "dialog" else
                     "超界：主体请走引擎轨（mcp_online translate_project "
                     "配 API/本地引擎），本技能只做尾巴精修"),
            "state": base["state"],
        }

    # ── extract：落一批紧凑 TSV，stdout 只给指针 ───────────────────
    def extract(self, input_dir: str, chunk: int = 200, part: int = 1,
                output_dir: str | None = None, source_lang: str = "auto",
                target_lang: str = "chs") -> dict:
        base = self._collect_unique(input_dir, output_dir, source_lang,
                                    target_lang)
        uniques = base["uniques"]
        chunk = max(1, min(int(chunk), 1000))
        part = max(1, int(part))
        sel = uniques[(part - 1) * chunk: part * chunk]
        kit = self.kit_dir()
        kit.mkdir(parents=True, exist_ok=True)
        batch_file = kit / ("batch_%03d.tsv" % part)
        lines = ["# sid<TAB>源文(\\n为换行占位)<TAB>术语提示  |  作答写"
                 " sid<TAB>译文 的 tsv 交 merge"]
        for rec in sel:
            hint = "|".join("%s→%s" % (k, v)
                            for k, v in list(rec["hint"].items())[:8])
            if rec["kind"] == "batch":
                for idx, src in enumerate(rec["sources"], 1):
                    lines.append("%s#%d\t%s\t%s" % (rec["sid"], idx,
                                                    esc(src), hint if idx == 1
                                                    else ""))
            else:
                lines.append("%s\t%s\t%s" % (rec["sid"], esc(rec["sources"][0]),
                                             hint))
        batch_file.write_text("\n".join(lines) + "\n", encoding="utf-8",
                              newline="\n")
        total_batches = (len(uniques) + chunk - 1) // chunk if uniques else 0
        return {
            "batch_file": str(batch_file),
            "part": part, "of": total_batches,
            "rows": len(sel), "unique_remaining_total": len(uniques),
            "state": base["state"],
        }

    # ── merge：交回作答 → 全键展开入库 → 填充一遍落盘 ──────────────
    def merge(self, input_dir: str, answers_file: str,
              output_dir: str | None = None, source_lang: str = "auto",
              target_lang: str = "chs") -> dict:
        if answers_file == "-":
            import sys
            answers_raw = sys.stdin.read()
        else:
            path = Path(answers_file)
            if not path.is_file():
                raise ValueError("答案文件不存在：%s" % answers_file)
            answers_raw = path.read_text(encoding="utf-8-sig")
        base = self._collect_unique(input_dir, output_dir, source_lang,
                                    target_lang)
        by_sid: dict[str, dict[str, Any]] = {}
        for rec in base["uniques"]:
            by_sid[rec["sid"]] = rec
        pending_by_sid = self.jobs.service.get_store().pending_snapshot()
        applied, sids_hit = 0, set()
        batch_parts: dict[str, dict[int, str]] = {}
        for line in answers_raw.splitlines():
            line = line.rstrip("\r")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            sid_raw, text = parts[0].strip(), unesc(parts[1].strip())
            if "#" in sid_raw:
                sid, _, idx_s = sid_raw.partition("#")
                batch_parts.setdefault(sid, {})[int(idx_s)] = text
            else:
                if sid_raw not in by_sid or not text:
                    continue
                applied += self._apply_sid(by_sid[sid_raw], [text])
                sids_hit.add(sid_raw)
        for sid, lines_map in batch_parts.items():
            rec = by_sid.get(sid)
            if rec is None:
                continue
            ordered = [lines_map.get(i, "") for i in range(1, len(rec["sources"]) + 1)]
            applied += self._apply_sid(rec, ordered)
            sids_hit.add(sid)
        summary = self._collect_unique(input_dir, output_dir, source_lang,
                                       target_lang)
        remaining = len(summary["uniques"])
        return {
            "sentences_applied": applied, "keys_answered": len(sids_hit),
            "unique_remaining": remaining,
            "output_dir": summary["output_dir"], "state": summary["state"],
            "note": "未作答/被闸门拒收的句子仍在下一批 extract 中",
        }

    def _apply_sid(self, rec: dict[str, Any], parts: list[str]) -> int:
        store = self.jobs.service.get_store()
        sources = [str(s) for s in rec["sources"]]
        if rec["kind"] == "batch":
            lines = [(parts[i] if i < len(parts) else "")
                     for i in range(len(sources))]
            payload = "\n".join("[%d] %s" % (i + 1, (lines[i] or sources[i]))
                               for i in range(len(sources)))
        else:
            payload = str(parts[0]) if parts else ""
            sources = sources[:1]
        if not payload.strip() or payload.strip() == str(sources[0]).strip():
            return 0  # 空答/整句回显不入库，保持 pending 下批复现
        for key in rec["keys"]:
            store.set_answer(key, payload, sources)
        return 1

    def _fill(self, input_dir, output_dir, source_lang, target_lang) -> dict:
        return self._collect_unique(input_dir, output_dir, source_lang,
                                    target_lang)

