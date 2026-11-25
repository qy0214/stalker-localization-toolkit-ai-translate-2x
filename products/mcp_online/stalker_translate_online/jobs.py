# -*- coding: utf-8 -*-
"""项目扫描、异步翻译任务、断点账本与输出校验。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from stalker_translate_core import JobState, JobStatus, QualityIssue, TokenUsage

from .config import Config
from .security import SecurityError, allowed_path, output_path
from .service import TranslationService, quality_issues
from .xray import TextFile, project_xml_files, scan_text_file, write_text_file

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


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

    def scan(self, input_dir: str) -> dict[str, Any]:
        root = allowed_path(input_dir, self.cfg, require_dir=True)
        out = output_path(root / "translated_online_mcp", root, self.cfg)
        files = project_xml_files(root, out, self.cfg.max_files,
                                  self.cfg.max_file_bytes)
        total = 0
        chars = 0
        for path in files:
            tf = scan_text_file(path)
            if tf:
                total += len(tf.entries)
                chars += sum(len(e.source) for e in tf.entries)
        return {"input_dir": str(root), "files": len(files),
                "entries": total, "characters": chars,
                "estimated_input_tokens": max(0, chars // 2),
                "estimated_cost": round(
                    (chars // 2) * self.cfg.input_price_per_million / 1_000_000, 6),
                "mode": "text_xml"}

    def start(self, input_dir: str, output_dir: str | None = None,
              source_lang: str = "auto", target_lang: str = "chs",
              concurrency: int | None = None,
              progress_callback: Callable[[dict[str, Any]], None] | None = None) -> str:
        root = allowed_path(input_dir, self.cfg, require_dir=True)
        out = output_path(output_dir or root / "translated_online_mcp", root,
                          self.cfg)
        job_id = uuid.uuid4().hex[:12]
        record = JobRecord(job_id=job_id, output_dir=str(out))
        with self.lock:
            self.jobs[job_id] = record
        record.thread = threading.Thread(target=self._run, args=(
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

    def _emit(self, callback: Callable[[dict[str, Any]], None] | None,
              **payload: Any) -> None:
        if callback:
            try:
                callback(payload)
            except Exception:
                pass

    def _run(self, record: JobRecord, root: Path, out: Path,
             source_lang: str, target_lang: str, concurrency: int,
             callback: Callable[[dict[str, Any]], None] | None) -> None:
        record.state = JobState.RUNNING
        record.message = "扫描项目"
        self._emit(callback, phase="scan", job_id=record.job_id)
        try:
            out.mkdir(parents=True, exist_ok=True)
            files = project_xml_files(root, out, self.cfg.max_files,
                                      self.cfg.max_file_bytes)
            scanned = [(path, scan_text_file(path, source_lang)) for path in files]
            scanned = [(path, tf) for path, tf in scanned if tf is not None]
            record.total = sum(len(tf.entries) for _path, tf in scanned)
            ledger_path = out / ".online_translate_ledger.json"
            ledger = {"pipeline": self.service.pipeline, "files": {}}
            if ledger_path.is_file():
                try:
                    old = json.loads(ledger_path.read_text(encoding="utf-8"))
                    if old.get("pipeline") == self.service.pipeline:
                        ledger = old
                except (OSError, ValueError):
                    pass
            failed = 0
            done = 0
            for file_idx, (src_path, tf) in enumerate(scanned, 1):
                if record.cancel_event.is_set():
                    record.state = JobState.CANCELLED
                    record.message = "任务已取消"
                    break
                rel = src_path.relative_to(root).as_posix()
                prev = ledger.get("files", {}).get(rel, {})
                src_hash = hashlib.sha256(src_path.read_bytes()).hexdigest()
                pending = []
                for entry in tf.entries:
                    if target_lang == "chs":
                        zh = len(CJK_RE.findall(entry.source))
                        if zh * 5 > len(entry.source):
                            continue
                    old_entry = prev.get("entries", {}).get(entry.source_id, {})
                    if (prev.get("source_sha256") == src_hash
                            and old_entry.get("source_sha256") == hashlib.sha256(
                                entry.source.encode("utf-8")).hexdigest()):
                        continue
                    pending.append(entry)
                replacements: dict[int, str] = {}
                file_issues: list[QualityIssue] = []
                if pending:
                    with ThreadPoolExecutor(max_workers=concurrency) as pool:
                        results = list(pool.map(
                            lambda e: self.service.translate(
                                e.source, source_lang, target_lang), pending))
                    for entry, result in zip(pending, results):
                        record.input_tokens += result.usage.input_tokens
                        record.output_tokens += result.usage.output_tokens
                        if result.retained_source or result.issues:
                            failed += 1
                            file_issues.extend(result.issues)
                        else:
                            replacements[entry.start] = result.text
                        done += 1
                        record.progress = done
                        self._emit(callback, phase="translate", job_id=record.job_id,
                                   file=rel, file_idx=file_idx, files=len(scanned),
                                   progress=done, total=record.total)
                if replacements:
                    dest = out / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    write_text_file(TextFile(path=dest, text=tf.text,
                                             encoding=tf.encoding, bom=tf.bom,
                                             entries=tf.entries), replacements)
                entry_ledger = prev.get("entries", {}) if prev.get(
                    "source_sha256") == src_hash else {}
                for entry, result in zip(pending, results if pending else []):
                    entry_ledger[entry.source_id] = {
                        "source_sha256": hashlib.sha256(
                            entry.source.encode("utf-8")).hexdigest(),
                        "output_sha256": hashlib.sha256(
                            result.text.encode("utf-8")).hexdigest(),
                        "retained_source": result.retained_source,
                    }
                ledger.setdefault("files", {})[rel] = {
                    "source_sha256": src_hash, "entries": entry_ledger}
                if file_issues:
                    record.issues.extend(file_issues[:20])
                tmp = ledger_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2),
                               encoding="utf-8")
                os.replace(tmp, ledger_path)
            if record.state != JobState.CANCELLED:
                record.state = JobState.NEEDS_REVIEW if failed else JobState.SUCCEEDED
                record.message = ("完成，%d 条待复核" % failed if failed
                                  else "完成，全部通过")
            status_path = out / ".online_translate_status.json"
            status_path.write_text(json.dumps({
                "job_id": record.job_id, "state": record.state.value,
                "progress": record.progress, "total": record.total,
                "message": record.message,
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "issue_codes": sorted({i.code for i in record.issues}),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            self.service.audit.record("job_finished", job_id=record.job_id,
                                      state=record.state.value,
                                      input_tokens=record.input_tokens,
                                      output_tokens=record.output_tokens,
                                      issue_codes=sorted({i.code for i in record.issues}))
        except Exception as exc:
            record.state = JobState.FAILED
            record.message = "%s: %s" % (type(exc).__name__, exc)
            self.service.audit.record("job_failed", job_id=record.job_id,
                                      error_type=type(exc).__name__)
        self._emit(callback, phase="done", job_id=record.job_id,
                   state=record.state.value, message=record.message)

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
                issues.append(QualityIssue("parse_failed", "源或输出无法读取", "error", rel))
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
                for issue in quality_issues(entry.source, translated.source, "chs"):
                    issues.append(QualityIssue(issue.code, issue.message,
                                               issue.severity,
                                               rel + ":" + entry.source_id))
        return {"ok": not issues and not missing_files, "checked_entries": checked,
                "missing_files": missing_files, "issues": [
                    {"code": i.code, "message": i.message, "source_id": i.source_id}
                    for i in issues[:200]]}
