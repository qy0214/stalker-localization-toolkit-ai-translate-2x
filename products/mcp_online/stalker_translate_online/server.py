#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用在线 AI 翻译 MCP（本地 stdio，OpenAI 兼容 Provider）。"""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from typing import Any

from stalker_translate_core import PROTOCOL_VERSION, tool_result
from stalker_translate_core.assets import resolve_asset_root

from .app import check_env, create_app
from .schema import ONLINE_TOOL_DEFINITIONS

SERVER_NAME = "stalker-translate-online"
SERVER_VERSION = "0.1.1"
_INSTRUCTIONS = (
    "STALKER 在线翻译。先 scan_project 估算规模，再 translate_project；"
    "长任务立即返回 job_id，使用 job_status 查询，job_cancel 取消。"
    "只有 state=succeeded 才能报告完成，needs_review 必须查看问题。"
    "单段文本使用 translate_text。密钥只从环境变量读取。"
)


class Server:
    def __init__(self):
        self.app = None
        self.send_lock = threading.Lock()

    def send(self, obj: dict[str, Any]) -> None:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        with self.send_lock:
            sys.stdout.write(data + "\n")
            sys.stdout.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def log(self, message: str) -> None:
        sys.stderr.write("[%s] %s\n" % (SERVER_NAME, message))
        sys.stderr.flush()

    def load(self):
        if self.app is None:
            self.app = create_app()
        return self.app

    def result(self, msg_id: Any, result: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def error(self, msg_id: Any, code: int, message: str) -> None:
        self.send({"jsonrpc": "2.0", "id": msg_id,
                   "error": {"code": code, "message": message}})

    def handle(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            self.result(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": _INSTRUCTIONS,
            })
        elif method in ("notifications/initialized", "initialized"):
            return
        elif method == "ping":
            self.result(msg_id, {})
        elif method == "tools/list":
            self.result(msg_id, {"tools": ONLINE_TOOL_DEFINITIONS})
        elif method == "notifications/cancelled":
            job_id = (params.get("requestId") or params.get("job_id"))
            if job_id and self.app:
                self.app[3].cancel(str(job_id))
            return
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                payload = self.call_tool(name, args, params.get("_meta"))
                self.result(msg_id, tool_result(payload))
            except Exception as exc:
                self.log("%s 失败：%s\n%s" % (name, exc, traceback.format_exc()))
                self.result(msg_id, tool_result(
                    {"error": "%s: %s" % (type(exc).__name__, exc)}, True))
        elif msg_id is not None:
            self.error(msg_id, -32601, "未实现的方法：%s" % method)

    def call_tool(self, name: str, args: dict[str, Any],
                  meta: dict[str, Any] | None) -> dict[str, Any]:
        cfg, provider, service, jobs = self.load()
        if name == "check_env":
            root = resolve_asset_root(os.path.join(os.path.dirname(__file__),
                                                   "..", "__init__.py"), "assets")
            return check_env(cfg, provider, root, bool(args.get("deep")), service)
        if name == "scan_project":
            return jobs.scan(str(args.get("input_dir") or ""))
        if name == "translate_text":
            result = service.translate(
                str(args.get("text") or ""),
                str(args.get("source_lang") or "auto"),
                str(args.get("target_lang") or "chs"),
                str(args.get("context") or ""),
            )
            return {
                "translation": result.text, "source": result.source_text,
                "provider": result.provider, "model": result.model,
                "cache_hit": result.cache_hit, "retained_source": result.retained_source,
                "usage": result.usage.__dict__,
                "quality_issues": [{"code": i.code, "message": i.message}
                                   for i in result.issues],
            }
        if name == "translate_project":
            token = ((meta or {}).get("progressToken")
                     or args.get("_progress_token"))

            def progress(payload: dict[str, Any]) -> None:
                if token:
                    self.notify("notifications/progress", {
                        "progressToken": token, **payload})

            job_id = jobs.start(
                str(args.get("input_dir") or ""),
                str(args.get("output_dir") or "") or None,
                str(args.get("source_lang") or "auto"),
                str(args.get("target_lang") or "chs"),
                args.get("concurrency"), progress,
            )
            return {"job_id": job_id, "state": "queued"}
        if name == "job_status":
            status = jobs.status(str(args.get("job_id") or ""))
            return {"job_id": status.job_id, "state": status.state.value,
                    "progress": status.progress, "total": status.total,
                    "message": status.message, "output_dir": status.output_dir,
                    "usage": status.usage.__dict__,
                    "issues": [{"code": i.code, "message": i.message,
                                "source_id": i.source_id}
                               for i in status.issues]}
        if name == "job_cancel":
            status = jobs.cancel(str(args.get("job_id") or ""))
            return {"job_id": status.job_id, "state": status.state.value,
                    "message": status.message}
        if name == "validate_output":
            return jobs.validate(str(args.get("input_dir") or ""),
                                 str(args.get("output_dir") or ""))
        if name == "glossary_lookup":
            query = str(args.get("query") or "")
            limit = max(1, min(int(args.get("limit") or 20), 50))
            return {"query": query, "exact": service.glossary.lookup(query),
                    "hits": [{"source": k, "translation": v}
                             for k, v in service.glossary.hits(query, limit)]}
        raise ValueError("未知工具：%s" % name)


def main() -> int:
    server = Server()
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if isinstance(msg, dict):
            try:
                server.handle(msg)
            except Exception as exc:
                server.log("处理请求异常：%s\n%s" % (exc, traceback.format_exc()))
                if msg.get("id") is not None:
                    server.error(msg["id"], -32603, "内部错误，详见 stderr")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
