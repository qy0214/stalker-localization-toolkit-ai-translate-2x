# -*- coding: utf-8 -*-
"""Hermes MCP 异步作业注册表。"""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class JobRecord:
    job_id: str
    output_dir: str = ""
    model: str = ""
    parallel: int = 1
    state: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)
    pause_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        data = {
            "job_id": self.job_id,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "output_dir": self.output_dir,
            "model": self.model,
            "parallel": self.parallel,
            "progress": dict(self.progress),
        }
        if self.result is not None:
            data["result"] = self.result
        if self.error:
            data["error"] = self.error
        return data


class JobManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._records: dict[str, JobRecord] = {}
        self._request_jobs: dict[str, str] = {}

    def start(self, job_id: str, target: Callable[[threading.Event, threading.Event], dict[str, Any]],
              output_dir: str = "", model: str = "", parallel: int = 1) -> JobRecord:
        with self._lock:
            if any(record.state in ("queued", "running", "paused", "stopping")
                   for record in self._records.values()):
                raise RuntimeError("已有翻译任务正在运行")
            record = JobRecord(
                job_id=job_id, output_dir=output_dir, model=model, parallel=parallel,
            )
            self._records[job_id] = record

        def run():
            with self._lock:
                record.started_at = time.time()
                record.state = "running"
            try:
                result = target(record.cancel_event, record.pause_event)
                with self._lock:
                    record.result = result
                    if record.cancel_event.is_set():
                        record.state = "stopped"
                    else:
                        state = str(result.get("status") or "done")
                        record.state = state if state in {
                            "done", "needs_review", "error", "stopped",
                        } else "done"
            except Exception as exc:
                with self._lock:
                    record.error = "%s: %s" % (type(exc).__name__, exc)
                    record.state = "stopped" if record.cancel_event.is_set() else "error"
                try:
                    import sys
                    traceback.print_exc(file=sys.stderr)
                except Exception:
                    pass
            finally:
                with self._lock:
                    record.finished_at = time.time()

        record.thread = threading.Thread(target=run, name="stalker-job-" + job_id, daemon=True)
        record.thread.start()
        return record

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._records.get(job_id)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            record = self._records.get(job_id)
            if record is None or record.state in {"done", "error", "stopped", "needs_review"}:
                return False
            record.cancel_event.set()
            record.pause_event.clear()
            record.state = "stopping"
            return True

    def pause(self, job_id: str) -> bool:
        with self._lock:
            record = self._records.get(job_id)
            if record is None or record.state not in {"running", "queued"}:
                return False
            record.pause_event.set()
            record.state = "paused"
            return True

    def resume(self, job_id: str) -> bool:
        with self._lock:
            record = self._records.get(job_id)
            if record is None or record.state != "paused":
                return False
            record.pause_event.clear()
            record.state = "running"
            return True

    def bind_request(self, request_id: Any, job_id: str) -> None:
        with self._lock:
            self._request_jobs[str(request_id)] = job_id

    def cancel_request(self, request_id: Any) -> bool:
        with self._lock:
            job_id = self._request_jobs.pop(str(request_id), None)
        return self.cancel(job_id) if job_id else False
