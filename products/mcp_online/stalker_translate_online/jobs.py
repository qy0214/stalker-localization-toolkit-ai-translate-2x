# -*- coding: utf-8 -*-
"""项目任务：共享全链 OfflineTranslationJob + 对话 AI 批次协议。

- translate_project（轨道 A）：全部筛选/直取/闸门/回写/终检/断点账本由核心
  共享链完成，本层只做 MCP 语义映射（状态、进度、用量、取消）。
- pending / apply-batch（轨道 B）：收集遍用 MODE_COLLECT 引擎跑同一条链，
  可直译条目照常落盘、零请求；真正需要模型的条目以请求键入批次存储。
  对话 AI 作答后写回同一批次存储，第二遍运行零 HTTP 补齐（键完全一致）。
"""
from __future__ import annotations

import contextlib
import json
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from stalker_translate_core import (
    JobState,
    JobStatus,
    OfflineTranslationJob,
    QualityIssue,
    TokenUsage,
)

from .config import Config
from .engine import MODE_COLLECT, MODE_ONLINE
from .security import allowed_path, output_path
from .service import TranslationService, quality_issues
from .xray import project_xml_files, scan_text_file

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
VERSION = "0.3.1"


@dataclass
class JobRecord:
    job_id: str
    state: JobState = JobState.QUEUED
    progress: int = 0
    total: int = 0
    message: str = ""
    output_dir: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    issues: list[QualityIssue] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def status(self) -> JobStatus:
        return JobStatus(
            job_id=self.job_id, state=self.state, progress=self.progress,
            total=self.total, message=self.message, output_dir=self.output_dir,
            usage=TokenUsage(input_tokens=self.input_tokens,
                             output_tokens=self.output_tokens),
            issues=tuple(self.issues),
        )


class JobManager:
    def __init__(self, cfg: Config, service: TranslationService):
        self.cfg = cfg
        self.service = service
        self.jobs: dict[str, JobRecord] = {}
        self.lock = threading.Lock()

    # ── 扫描（只读预估，不请求模型） ────────────────────────────────
    def scan(self, input_dir: str) -> dict[str, Any]:
        root = allowed_path(input_dir, self.cfg, require_dir=True)
        out = output_path(root / "translated_online_mcp", root, self.cfg)
        files = project_xml_files(root, out, self.cfg.max_files,
                                  self.cfg.max_file_bytes)
        total = 0
        chars = 0
        zh_heavy = 0
        for path in files:
            tf = scan_text_file(path)
            if tf:
                for entry in tf.entries:
                    total += 1
                    chars += len(entry.source)
                    zh = len(CJK_RE.findall(entry.source))
                    if total and zh * 5 > len(entry.source):
                        zh_heavy += 1
        return {"input_dir": str(root), "files": len(files),
                "entries": total, "characters": chars,
                "already_chinese_like": zh_heavy,
                "estimated_input_tokens": max(0, chars // 2),
                "estimated_cost": round(
                    (chars // 2) * self.cfg.input_price_per_million / 1_000_000, 6),
                "note": "上界估计；共享全链会先做筛选与直取，实际请求通常显著更低",
                "mode": "xray_shared_core"}

    # ── 作业装配 ────────────────────────────────────────────────────
    def _build_job(self, root: Path, out: Path, source_lang: str,
                   target_lang: str, concurrency: int,
                   record: JobRecord,
                   callback: Callable[[dict[str, Any], ], None] | None):
        from . import resources
        parser = resources.get_parser()
        termbase = resources.get_termbase()
        engine = self.service.get_engine(record.engine_mode)
        merged = resources.get_merged_gloss(self.cfg.cache_path)
        hard_keys = None
        try:
            hard_keys = set(termbase.load_layers().get("names") or ())
        except Exception:
            hard_keys = None

        def log(msg: str = "", level: str = "info", *args: Any,
                **kwargs: Any) -> None:
            text = str(msg)[:400]
            if text:
                record.message = text
                sys.stderr.write("[stalker-translate-online] %s\n" % text)
                sys.stderr.flush()

        def progress(payload: dict[str, Any]) -> None:
            try:
                record.progress = int(payload.get("file_idx") or 0)
                record.total = int(payload.get("files") or record.total)
            except (TypeError, ValueError):
                pass
            self._emit(callback, phase="translate", job_id=record.job_id,
                       file=payload.get("file"), file_idx=payload.get("file_idx"),
                       files=payload.get("files"), reqs=payload.get("reqs"))

        job = OfflineTranslationJob(
            parser, engine, str(root), "auto", source_lang, target_lang,
            glossary=merged or None, force_glossary=True,
            ctx_size=int(self.cfg.ctx_size or 8192), out_dir=str(out),
            log=log, detail=log, progress=progress,
            cancel_event=record.cancel_event,
            reference_mode=True, copy_reference=True, only_referenced=False,
            protect_proper=True, preview_reference=False,
            hard_glossary_keys=hard_keys, translate_literals=True,
            # 轨道 B 关闭动态词层（会话词表/高频专名定译）：两次运行的
            # 提示词与请求键必须完全确定，批次答案才能按键命中。
            auto_session_terms=record.engine_mode == MODE_ONLINE,
            session_cache_dir=str(Path(self.cfg.cache_path) / "sessions"),
            tm_lite=True, freq_names=record.engine_mode == MODE_ONLINE,
            parallel=max(1, min(concurrency, 8)), pending_files=0,
            termbase=termbase, data_root=str(self.cfg.cache_path),
            pipeline_version="mcp_online@%s" % VERSION,
            parallel_cap=8, is_writable=lambda _p: True)
        return job, engine

    def _execute(self, record: JobRecord, root: Path, out: Path,
                 source_lang: str, target_lang: str, concurrency: int,
                 callback: Callable[[dict[str, Any]], None] | None) -> None:
        record.state = JobState.RUNNING
        record.message = "扫描并翻译（共享全链）"
        self._emit(callback, phase="scan", job_id=record.job_id)
        error = ""
        job_ok = False
        job = None
        engine = None
        try:
            out.mkdir(parents=True, exist_ok=True)
            job, engine = self._build_job(root, out, source_lang, target_lang,
                                          concurrency, record, callback)
            started = time.monotonic()
            with contextlib.redirect_stdout(sys.stderr):
                job_ok = bool(job.run())
            record.message = "%s（%.1fs）" % (record.message,
                                              time.monotonic() - started)
        except Exception as exc:
            error = "%s: %s" % (type(exc).__name__, exc)
            self.service.audit.record("job_exception", job_id=record.job_id,
                                      error_type=type(exc).__name__)
        usage = engine.usage_snapshot() if engine else {}
        record.input_tokens = int(usage.get("input_tokens", 0))
        record.output_tokens = int(usage.get("output_tokens", 0))
        failed_strings = int(getattr(job, "failed_strings", 0) or 0)
        audit = getattr(job, "audit_result", None) if job else None
        audit_bad = bool(audit and not audit.get("ok"))
        if record.cancel_event.is_set():
            record.state = JobState.CANCELLED
            record.message = "任务已取消"
        elif error:
            record.state = JobState.FAILED
            record.message = "任务失败：" + error
        elif not job_ok or failed_strings or audit_bad:
            record.state = JobState.NEEDS_REVIEW
            record.message = ("完成，%d 个字符串待复核" % failed_strings
                              if failed_strings else
                              "完成，终检发现问题（详见状态文件）")
        else:
            record.state = JobState.SUCCEEDED
            record.message = "完成，全部通过"
        self._write_status(record, out, engine, audit, failed_strings, error)
        self.service.audit.record("job_finished", job_id=record.job_id,
                                  state=record.state.value,
                                  input_tokens=record.input_tokens,
                                  output_tokens=record.output_tokens,
                                  engine_usage=usage)
        self._emit(callback, phase="done", job_id=record.job_id,
                   state=record.state.value, message=record.message)

    def _write_status(self, record: JobRecord, out: Path, engine,
                      audit: dict | None, failed_strings: int,
                      error: str) -> None:
        try:
            (out / ".online_translate_status.json").write_text(json.dumps({
                "job_id": record.job_id, "state": record.state.value,
                "progress": record.progress, "total": record.total,
                "message": record.message,
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "requests": engine.usage_snapshot()["requests"] if engine else 0,
                "cache_hits": (engine.usage_snapshot()["cache_hits"]
                               if engine else 0),
                "failed_strings": failed_strings,
                "audit_ok": bool(audit.get("ok")) if audit else None,
                "error": error,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _emit(self, callback: Callable[[dict[str, Any]], None] | None,
              **payload: Any) -> None:
        if callback:
            try:
                callback(payload)
            except Exception:
                pass

    # ── 轨道 A：异步全链作业 ────────────────────────────────────────
    def start(self, input_dir: str, output_dir: str | None = None,
              source_lang: str = "auto", target_lang: str = "chs",
              concurrency: int | None = None,
              progress_callback: Callable[[dict[str, Any]], None] | None = None,
              engine_mode: str = MODE_ONLINE) -> str:
        root = allowed_path(input_dir, self.cfg, require_dir=True)
        out = output_path(output_dir or root / "translated_online_mcp", root,
                          self.cfg)
        job_id = uuid.uuid4().hex[:12]
        record = JobRecord(job_id=job_id, output_dir=str(out))
        record.engine_mode = engine_mode  # type: ignore[attr-defined]
        with self.lock:
            self.jobs[job_id] = record
        record.thread = threading.Thread(target=self._execute, args=(
            record, root, out, source_lang, target_lang,
            max(1, min(int(concurrency or self.cfg.max_concurrency), 16)),
            progress_callback), daemon=True)
        record.thread.start()
        return job_id

    def status(self, job_id: str) -> JobStatus:
        with self.lock:
            record = self.jobs.get(job_id)
        if record is None:
            raise KeyError("未知 job_id：%s" % job_id)
        return record.status()

    def cancel(self, job_id: str) -> JobStatus:
        with self.lock:
            record = self.jobs.get(job_id)
        if record is None:
            raise KeyError("未知 job_id：%s" % job_id)
        record.cancel_event.set()
        return record.status()

    # ── 轨道 B：对话 AI 批次协议 ────────────────────────────────────
    def collect_pending(self, input_dir: str, output_dir: str | None = None,
                        source_lang: str = "auto",
                        target_lang: str = "chs") -> dict[str, Any]:
        """跑收集遍：可直译的照常落盘，需模型的入 pending。"""
        record = JobRecord(job_id=uuid.uuid4().hex[:12])
        record.engine_mode = MODE_COLLECT  # type: ignore[attr-defined]
        root = allowed_path(input_dir, self.cfg, require_dir=True)
        out = output_path(output_dir or root / "translated_online_mcp", root,
                          self.cfg)
        store = self.service.get_store()
        store.begin_round()
        self._execute(record, root, out, source_lang, target_lang,
                      max(1, min(self.cfg.max_concurrency, 8)), None)
        pruned = store.prune_stale()
        rows = sorted(store.pending_snapshot().values(),
                      key=lambda r: r.get("recorded_at") or 0)
        items = []
        for idx, row in enumerate(rows, 1):
            items.append({
                "n": idx, "key": row["key"], "kind": row.get("kind", "single"),
                "sources": row.get("sources") or [],
                "hint": self._hint_for(row),
            })
        return {"output_dir": str(out), "pending_total": len(items),
                "stale_pruned": pruned, "state": record.state.value,
                "batch_file_hint": str(
                    Path(self.cfg.cache_path) / "agent_batches.jsonl"),
                "items": items}

    @staticmethod
    def _normalize_batch(text: str, sources: list[str]) -> str:
        """批量答案编号归一：未带 [n] 前缀时按行数补全，保证 core 可解析。"""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return text
        if all(re.match(r"\[\d+\]", ln) for ln in lines):
            return text
        out = []
        for i, ln in enumerate(lines, 1):
            out.append("[%d] %s" % (i, ln))
        return "\n".join(out)

    def _hint_for(self, row: dict[str, Any]) -> list[list[str]]:
        try:
            from . import resources
            index = resources.get_text_index(self.cfg.cache_path)
            merged_hints: dict[str, str] = {}
            for source in row.get("sources") or []:
                for k, v in index.hits(str(source), limit=8) or []:
                    merged_hints.setdefault(str(k), str(v))
            return [[k, v] for k, v in list(merged_hints.items())[:16]]
        except Exception:
            return []

    def apply_answers(self, answers: list[dict[str, Any]]) -> dict[str, Any]:
        """答案合入批次存储；n 序号按当前 pending 顺序解析。"""
        store = self.service.get_store()
        rows = sorted(store.pending_snapshot().values(),
                      key=lambda r: r.get("recorded_at") or 0)
        by_n = {idx: row["key"] for idx, row in enumerate(rows, 1)}
        applied, unknown, rejected = 0, 0, 0
        for row in answers:
            key = str(row.get("key") or "") or \
                by_n.get(int(row.get("n") or 0), "")
            text = str(row.get("translation") or "").strip()
            if not key:
                unknown += 1
                continue
            if not text:
                rejected += 1
                continue
            sources = (store.pending_snapshot().get(key) or {}).get("sources") \
                or ([row["source"]] if row.get("source") else [])
            if (sources and row.get("kind", "single") != "batch"
                    and text == str(sources[0]).strip()):
                rejected += 1  # 明显回显不入库，保持 pending
                continue
            if row.get("kind", "single") == "batch" and sources:
                text = self._normalize_batch(text, [str(s) for s in sources])
            store.set_answer(key, text, [str(s) for s in sources] if sources else None)
            applied += 1
        self.service.audit.record("batch_apply", applied=applied,
                                  unknown=unknown, rejected=rejected,
                                  submitted=len(answers))
        return {"applied": applied, "unknown_keys": unknown,
                "rejected_or_echo": rejected,
                "pending_remaining": len(store.pending_snapshot()) - applied}

    # ── 输出校验（与旧工具语义兼容） ────────────────────────────────
    def validate(self, input_dir: str, output_dir: str) -> dict[str, Any]:
        root = allowed_path(input_dir, self.cfg, require_dir=True)
        out = output_path(output_dir, root, self.cfg)
        issues: list[QualityIssue] = []
        checked = 0
        missing_files = 0
        for src_path in project_xml_files(root, out, self.cfg.max_files,
                                          self.cfg.max_file_bytes):
            rel = src_path.relative_to(root).as_posix()
            dest = out / rel
            if not dest.is_file():
                missing_files += 1
                continue
            src = scan_text_file(src_path)
            dst = scan_text_file(dest)
            if src is None or dst is None:
                issues.append(QualityIssue("parse_failed", "源或输出无法读取",
                                           "error", rel))
                continue
            src_ids = {e.source_id for e in src.entries}
            dst_ids = {e.source_id for e in dst.entries}
            if src_ids != dst_ids:
                issues.append(QualityIssue("id_set_mismatch", "条目 ID 集合不一致",
                                           "error", rel))
            dst_map = {e.source_id: e for e in dst.entries}
            for entry in src.entries:
                checked += 1
                translated = dst_map.get(entry.source_id)
                if translated is None:
                    continue
                for issue in quality_issues(entry.source, translated.source,
                                            "chs"):
                    issues.append(QualityIssue(issue.code, issue.message,
                                               issue.severity,
                                               rel + ":" + entry.source_id))
        return {"ok": not issues and not missing_files,
                "checked_entries": checked, "missing_files": missing_files,
                "issues": [{"code": i.code, "message": i.message,
                            "source_id": i.source_id} for i in issues[:200]]}
