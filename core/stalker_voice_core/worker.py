# -*- coding: utf-8 -*-
"""语音工坊 worker 进程（骨架版）。

职责边界（硬约束，来自宿主插件纪律）：
  * torch / TTS 等重运行时只允许活在**本进程**，插件宿主进程永不 import；
  * 只监听 127.0.0.1；不写除自身 work 目录外的任何路径；
  * 本骨架版实现：握手、心跳、任务总线、echo 自证任务；tts 等预留类型
    显式 501。

跑法：
    python -m stalker_voice_core.worker --port 0
  port=0 表示由操作系统分配临时端口；握手行打到 stdout 供插件读取。

HTTP 面（全部 JSON）：
    GET  /health            心跳与进程状态
    POST /job               提交任务 {"kind": "echo", "payload": ...}
    GET  /job/<id>          查询任务状态
    POST /shutdown          优雅退出
"""

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from stalker_voice_core import __version__
    from stalker_voice_core import protocol
else:
    from . import __version__
    from . import protocol


class _State(object):
    """worker 全局状态：无锁化到最小面（beat 用 GIL 原子读写的 float）。"""

    def __init__(self):
        self.started = time.time()
        self.beat = time.time()
        self.jobs = {}           # id -> dict(kind/status/result/error/times)
        self.jobs_lock = threading.Lock()
        self._next_id = 0

    def touch_beat(self):
        self.beat = time.time()

    def beat_age(self):
        return round(time.time() - self.beat, 3)

    def uptime(self):
        return round(time.time() - self.started, 3)

    def new_job(self, kind, payload):
        with self.jobs_lock:
            self._next_id += 1
            job_id = "%06d" % self._next_id
            self.jobs[job_id] = {
                "kind": kind, "status": "queued", "result": None,
                "error": None, "created": time.time(),
                "payload": payload,
            }
        return job_id

    def set_status(self, job_id, status, result=None, error=None):
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            if job is not None:
                job["status"] = status
                job["result"] = result
                job["error"] = error

    def get_job(self, job_id):
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            return dict(job) if job is not None else None


STATE = _State()


def _run_echo(job_id, payload):
    """echo：把 payload 原样带回，附带一个小的回环证明。"""
    STATE.set_status(job_id, "running")
    STATE.set_status(job_id, "done", result={
        "echo": payload,
        "worker_pid": os.getpid(),
        "beat_age": STATE.beat_age(),
    })


def _set_progress(job_id, done, total, note=None):
    """进度写进任务 dict（读侧经 GET /job/<id> 可见）。"""
    with STATE.jobs_lock:
        job = STATE.jobs.get(job_id)
        if job is not None:
            job["progress"] = {"done": done, "total": total,
                               "note": note or ""}


def _run_scan(job_id, payload):
    """scan：游戏根体检 + 已有语音枚举 + 待解包存档清单。只读。"""
    STATE.set_status(job_id, "running")
    from stalker_voice_core import audio
    game_root = str((payload or {}).get("game_root") or "").strip()
    voice_src = (payload or {}).get("voice_src_root") or None
    if not game_root or not os.path.isdir(game_root):
        STATE.set_status(job_id, "error",
                         error="game_root 无效：%r" % game_root)
        return
    _set_progress(job_id, 1, 2, "survey+enumerate")
    result = audio.scan_voice_sources(game_root, voice_src)
    _set_progress(job_id, 2, 2, "done")
    STATE.set_status(job_id, "done", result=result)


def _run_extract(job_id, payload):
    """extract：xrkit 引擎把存档里的 sounds 树解进 VOICE_SRC。"""
    STATE.set_status(job_id, "running")
    from stalker_voice_core import extract
    p = payload or {}
    game_root = str(p.get("game_root") or "").strip()
    out_dir = str(p.get("out_dir") or "").strip()
    prefix = str(p.get("prefix") or "sounds")
    archives = p.get("archives")  # [(path, source_name)]；缺省=体检出的全部
    if not out_dir:
        STATE.set_status(job_id, "error", error="out_dir 无效")
        return
    if not archives:
        if not game_root or not os.path.isdir(game_root):
            STATE.set_status(job_id, "error",
                             error="未指定 archives 且 game_root 无效")
            return
        archives = extract.find_archives(game_root)
    if not archives:
        STATE.set_status(job_id, "done", result={
            "results": [], "note": "没有可解包的存档"})
        return

    cancel = threading.Event()

    def progress(done, total):
        _set_progress(job_id, done, total, "extract")

    results = []
    n_total = len(archives)
    for i, (path, name) in enumerate(archives, 1):
        if cancel.is_set():
            break
        _set_progress(job_id, i, n_total, "extract:%s" % name)
        results.append(extract.extract_archive(
            path, out_dir, prefix=prefix, progress=progress,
            cancel=cancel, source_name=name))
    STATE.set_status(job_id, "done", result={
        "results": results,
        "extracted_total": sum(r["extracted"] for r in results),
        "failed_total": sum(r["failed"] for r in results),
    })


def _dispatch(job_id, kind, payload):
    try:
        if kind == "echo":
            _run_echo(job_id, payload)
        elif kind == "scan":
            _run_scan(job_id, payload)
        elif kind == "extract":
            _run_extract(job_id, payload)
        else:  # protocol.job_supported 已在上游挡过；这里只兜底
            STATE.set_status(job_id, "error", error="unknown kind %r" % kind)
    except Exception as exc:  # 任务线程兜底：worker 绝不因任务崩进程
        STATE.set_status(job_id, "error", error="%s: %s"
                         % (type(exc).__name__, exc))


def _submit(kind, payload):
    """提交任务；返回 (job_id, http_status)。"""
    if not protocol.job_supported(kind):
        return None, 501
    job_id = STATE.new_job(kind, payload)
    threading.Thread(target=_dispatch, args=(job_id, kind, payload),
                     daemon=True).start()
    return job_id, 202


class _Handler(BaseHTTPRequestHandler):
    server_version = "stalker_voice_core/" + __version__

    # ---- 基础设施 -------------------------------------------------------
    def log_message(self, fmt, *args):  # 静默默认访问日志（stderr 只留异常）
        pass

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 4 * 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # ---- 路由 -----------------------------------------------------------
    def do_GET(self):
        STATE.touch_beat()
        if self.path == "/health":
            self._send(200, {
                "ok": True, "service": protocol.SERVICE_NAME,
                "version": __version__, "pid": os.getpid(),
                "uptime_sec": STATE.uptime(), "beat_age_sec": STATE.beat_age(),
                "jobs_open": sum(1 for j in STATE.jobs.values()
                                 if j["status"] in ("queued", "running")),
            })
            return
        if self.path.startswith("/job/"):
            job = STATE.get_job(self.path[len("/job/"):].strip("/"))
            if job is None:
                self._send(404, {"ok": False, "error": "no such job"})
            else:
                self._send(200, {"ok": True, "job": job})
            return
        self._send(404, {"ok": False, "error": "unknown path"})

    def do_POST(self):
        STATE.touch_beat()
        body = self._body()
        if self.path == "/shutdown":
            self._send(200, {"ok": True, "bye": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if self.path == "/job":
            kind = str(body.get("kind") or "")
            if kind not in protocol.JOB_KINDS:
                self._send(400, {"ok": False, "error": "missing/unknown kind"})
                return
            if not protocol.job_supported(kind):
                self._send(501, {
                    "ok": False,
                    "error": "kind %r 预留未实现（骨架版）" % kind,
                })
                return
            job_id, code = _submit(kind, body.get("payload"))
            self._send(code, {"ok": True, "job_id": job_id})
            return
        self._send(404, {"ok": False, "error": "unknown path"})


def _heartbeat_loop():
    """独立心跳线程：即使没有任何请求，beat 也在走——插件可据此区分
    “进程假死”与“空闲”。"""
    while True:
        STATE.touch_beat()
        time.sleep(protocol.HEARTBEAT_SEC)


def main(argv=None):
    ap = argparse.ArgumentParser(description="stalker_voice_core worker")
    ap.add_argument("--port", type=int, default=0,
                    help="监听端口；0=系统分配临时端口")
    args = ap.parse_args(argv)

    # stdout 只留给握手行；访问日志已静默，异常走 stderr。
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    sys.stdout.write(protocol.handshake_line(os.getpid(),
                                             server.server_address[1],
                                             __version__))
    sys.stdout.flush()
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
