#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STALKER 翻译 MCP 服务（Hermes Agent 客户端）。

只提供「翻译大脑」：术语库 + 提示词 + 翻译管线 + X-Ray 文件处理。
不启动、不下载、不管理任何模型——推理请求转发给用户自己的 OpenAI 兼容端点
（Hermes 部署的本地模型，或 llama.cpp / LM Studio / Ollama 等）。

协议：MCP stdio（换行分隔 JSON-RPC），零第三方依赖。
工具：check_env / translate_mod / translate_text
"""

import contextlib
import html
import http.server
import json
import os
import queue
import re
import secrets
import sys
import threading
import time
import traceback
import urllib.error
import uuid
import urllib.parse
import urllib.request
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
_core_dirs = (
    HERE,
    os.path.join(os.path.dirname(os.path.dirname(HERE)), "core"),
    os.path.join(os.getcwd(), "products", "mcp_hermes"),
    os.path.join(os.getcwd(), "core"),
)
for _core_dir in _core_dirs:
    if os.path.isdir(os.path.join(_core_dir, "stalker_translate_core")):
        if _core_dir not in sys.path:
            sys.path.insert(0, _core_dir)
        break
from stalker_translate_core import (  # noqa: E402
    PROTOCOL_VERSION,
    __version__ as CORE_VERSION,
    SERVER_INSTRUCTIONS,
    TOOL_DEFINITIONS,
    AssetBundle,
    GlossaryIndex,
    OfflineTranslationJob,
    OutputTruncated,
    Termbase,
    UserGlossaryStore,
    _has_translatable_text,
    budget_max_tokens,
    detect_text_lang,
    glossary_hint_block,
    layer_counts,
    runtime_tokens_match,
    semantic_role_issues,
    load_termbase_document,
    resolve_asset_root,
    tool_result,
)
from stalker_translate_core.matching import GlossaryIndex as TermIndex  # noqa: E402
from stalker_translate_core.pipeline import (  # noqa: E402
    TRANSLATION_BATCH_SYS_PROMPT,
    TRANSLATION_SYS_PROMPT,
    TRANSLATION_SYS_PROMPT_EN,
    _PLACEHOLDER_RETRY_NOTE,
    strip_output_wrappers,
)
from stalker_translate_core.vendor import load_vendor  # noqa: E402
from jobs import JobManager  # noqa: E402
ASSET_ROOT = str(resolve_asset_root(__file__, "kernel/assets"))
DATA_DIR = os.path.join(HERE, "data")
LOG_DIR = os.path.join(HERE, "logs")
CONFIG_PATH = (os.environ.get("STALKER_MCP_CONFIG")
               or os.path.join(HERE, "config.json"))
SERVER_NAME = "stalker-translate"
VERSION = "1.1.12"
LANG_FULL = {"chs": "简体中文", "eng": "英语", "rus": "俄语", "ukr": "乌克兰语"}
_SEMANTIC_ROLE_NOTE = "请核对付款、交付、雇佣与命令方向，严格按源文语义翻译，不得反转双方关系。"

DEFAULT_CONFIG = {
    # OpenAI 兼容端点。默认按 llama.cpp 的 8080；Hermes 安装脚本会写入它的
    # 18434 路由地址。
    "base_url": "http://127.0.0.1:8080/v1",
    "api_key": "",
    # 端点上的模型名；llama.cpp 会忽略该字段，vLLM/Ollama 等需要填对。
    "model": "local-model",
    # 每条文本可用的上下文预算；超出会自动切句。
    "ctx_size": 8192,
    # 并发请求数（llama-server 的 --parallel 槽位数）。
    "parallel": 1,
    # 翻译开始时自动打开实时进度页（显示当前文件、原文→译文）。
    "open_progress_page": True,
    # 单次请求超时（秒）。
    "timeout_sec": 300,
    "max_retries": 4,
    # 追加到系统提示词的要求，例如 “专有名词保留英文”。
    "prompt_extra": "",
    "official_prompts": False,
    "source_lang": "auto",
    "target_lang": "chs",
}

# ── 协议通道 ────────────────────────────────────────────────────────
# 先把真正的 stdout 复制出来专供 JSON-RPC，再把 fd 1 指向 stderr。
# 之后内核/解析器里的任何 print 都只会进日志，不会污染协议流。
_PROTO_OUT = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n",
                       buffering=1)
_PROTO_IN = sys.stdin.buffer
os.dup2(2, 1)
sys.stdout = sys.stderr
try:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.dont_write_bytecode = True

_SEND_LOCK = threading.Lock()
_LOG_LOCK = threading.Lock()
_PROGRESS_LOCK = threading.Lock()
_TERMBASE = {"obj": None}
_USER_GLOSS = {"obj": None}
_ENGINE = {"obj": None}
_JOBS = JobManager()
_TEXT_GLOSS = {"index": None, "pending": False}
_CORE_GLOSS = {"index": None}
_IN_Q = queue.Queue()
_CLIENT = {"caps": {}, "name": ""}
_REQ_SEQ = [0]
_PLAN_CACHE = {"key": None, "plan": None}
_CONTROL = {
    "server": None,
    "thread": None,
    "token": secrets.token_urlsafe(18),
    "lock": threading.Lock(),
}


# ── 基础工具 ────────────────────────────────────────────────────────

def _log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, "mcp.log")
        with _LOG_LOCK:
            if os.path.isfile(path) and os.path.getsize(path) > 5 * 1024 * 1024:
                try:
                    os.replace(path, path + ".1")
                except OSError:
                    pass
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass
    try:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def _send(obj):
    data = json.dumps(obj, ensure_ascii=False)
    with _SEND_LOCK:
        _PROTO_OUT.write(data + "\n")
        _PROTO_OUT.flush()


def _ok(msg_id, result):
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _err(msg_id, code, message):
    _send({"jsonrpc": "2.0", "id": msg_id,
           "error": {"code": code, "message": message}})


def _notify(method, params):
    _send({"jsonrpc": "2.0", "method": method, "params": params})


_NOTIFY_LOG = {"ts": 0.0}


def _mcp_log(level, text):
    """把关键日志作为 MCP logging 通知发给客户端。

    Hermes 会把 notifications/message 写进 agent.log（MCP server log [...]），
    比只写本地 mcp.log 更容易被用户看到；info 限频 1 秒，避免刷屏。
    """
    text = str(text or "").strip()
    if not text:
        return
    if level == "info":
        now = time.time()
        if now - _NOTIFY_LOG["ts"] < 1.0:
            return
        _NOTIFY_LOG["ts"] = now
    _notify("notifications/message", {
        "level": level,
        "logger": "stalker-translate",
        "data": text[:1500],
    })


def _open_local_file(path):
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            import subprocess
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, path], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _control_url(job_id):
    with _CONTROL["lock"]:
        if _CONTROL["server"] is None:
            manager = _JOBS

            class Handler(http.server.BaseHTTPRequestHandler):
                def do_POST(self):
                    parsed = urllib.parse.urlparse(self.path)
                    if parsed.query != "":
                        query = urllib.parse.parse_qs(parsed.query)
                    else:
                        query = {}
                    if query.get("token") != [_CONTROL["token"]]:
                        self.send_error(403)
                        return
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                        payload = json.loads(self.rfile.read(length) or b"{}")
                        job_id = str(payload.get("job_id") or "")
                        action = str(payload.get("action") or "")
                        ok = {
                            "pause": manager.pause,
                            "resume": manager.resume,
                            "stop": manager.cancel,
                        }.get(action, lambda _job_id: False)(job_id)
                        body = json.dumps({"ok": ok, "state": action}).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    except Exception:
                        self.send_error(400)

                def log_message(self, _format, *args):
                    pass

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            _CONTROL["server"] = server
            _CONTROL["thread"] = thread
        port = _CONTROL["server"].server_address[1]
    return "http://127.0.0.1:%d/control?token=%s" % (port, _CONTROL["token"])


def _write_progress_page(path, d, pairs, status="running", elapsed=0.0, note="",
                         control_url="", job_id=""):
    """写实时进度页：浏览器每 2 秒刷新。

    总进度 = 已译条目 / 开跑前统计出的可译条目；当前文件单独一根细进度条。
    旧版把「已处理文件累计到的条目数」当分母，会出现 20/53 这种假进度。
    """
    data = dict(d or {})
    files = int(data.get("files") or 0)
    idx = int(data.get("file_idx") or 0)
    phase = str(data.get("phase") or "")
    reqs = int(data.get("reqs") or 0)
    entries_total = int(data.get("entries_total") or 0)
    translatable = int(data.get("entries_translatable") or 0)
    translated = int(data.get("ids") or 0)
    file_name = str(data.get("file") or "")
    file_done = int(data.get("file_done") or 0)
    file_total = int(data.get("file_total") or 0)
    scan_done = int(data.get("prescan_done") or 0)
    scan_total = int(data.get("prescan_total") or 0)

    state_label = {"running": "翻译中", "done": "已完成", "error": "出错",
                   "stopped": "已停止", "paused": "已暂停",
                   "stopping": "正在停止",
                   "needs_review": "待复核"}.get(status, status)

    denom = max(translatable, translated)
    if status == "done":
        pct = 100
    elif phase == "prescan" and scan_total > 0:
        pct = int(100.0 * min(scan_done, scan_total) / scan_total)
    elif translatable > 0:
        pct = int(100.0 * min(translated, translatable) / translatable)
    elif files > 0:
        pct = int(100.0 * min(idx, files) / files)
    else:
        pct = 0
    pct = max(0, min(99 if status != "done" else 100, pct))
    file_pct = (int(100.0 * min(file_done, file_total) / file_total)
                if file_total > 0 else 0)

    def _num(n):
        return format(int(n), ",")

    if phase == "prescan":
        overall = "正在统计可翻译条目 · 已扫 %s / %s 张文本表" % (
            _num(scan_done), _num(scan_total))
        overall_num = "%s / %s 张" % (_num(scan_done), _num(scan_total))
        cur_num = "统计中"
        cur_name = "先统计整个 MOD 的条目总量，再开始翻译"
    elif denom > 0:
        overall = "已译 %s / 可译 %s 条" % (_num(translated), _num(denom))
        overall_num = "%s / %s 条" % (_num(translated), _num(denom))
        cur_num = ("%s / %s 条" % (_num(file_done), _num(file_total))
                   if file_total else "—")
        cur_name = file_name or "—"
    else:
        overall = "文件 %s / %s" % (_num(idx), _num(files))
        overall_num = "%s / %s 个" % (_num(idx), _num(files))
        cur_num = ("%s / %s 条" % (_num(file_done), _num(file_total))
                   if file_total else "—")
        cur_name = file_name or "—"

    if elapsed:
        mins, secs = divmod(int(elapsed), 60)
        time_text = ("%d 分 %02d 秒" % (mins, secs)) if mins else ("%d 秒" % secs)
    else:
        time_text = ""

    chips = []
    if files:
        chips.append("文件 %s / %s" % (_num(idx), _num(files)))
    if entries_total:
        chips.append("总条目 %s" % _num(entries_total))
    if translated:
        chips.append("已译 %s" % _num(translated))
    if reqs:
        chips.append("请求 %s" % _num(reqs))
    if time_text:
        chips.append("用时 %s" % time_text)
    chips_html = "".join("<span>%s</span>" % html.escape(c) for c in chips)

    bars = (
        '<div class="bars">'
        '<div class="blk"><div class="lbl"><span>总进度</span>'
        '<span class="num">%s</span></div>'
        '<div class="bar"><i style="width:%d%%"></i></div></div>'
        '<div class="blk"><div class="lbl"><span>当前文件</span>'
        '<span class="num">%s</span></div>'
        '<div class="bar cur"><i style="width:%d%%"></i></div>'
        '<div class="fn">%s</div></div>'
        '</div>'
        % (html.escape(overall_num), pct, html.escape(cur_num), file_pct,
           html.escape(cur_name)))

    rows = []
    for src, dst in list(pairs or ())[-20:][::-1]:
        rows.append('<tr><td class="src">%s</td><td class="dst">%s</td></tr>'
                    % (html.escape(str(src)), html.escape(str(dst))))
    if not rows:
        rows.append('<tr><td colspan="2" class="empty">等待第一批译文……</td></tr>')
    controls_html = ""
    if control_url and job_id:
        controls_html = (
            '<div class="controls">'
            '<button onclick="ctl(\'pause\')">暂停</button>'
            '<button onclick="ctl(\'resume\')">继续</button>'
            '<button onclick="ctl(\'stop\')">停止</button></div>'
            '<script>const CONTROL_URL=%s;const JOB_ID=%s;'
            'async function ctl(action){await fetch(CONTROL_URL,{method:"POST",'
            'headers:{"Content-Type":"application/json"},body:JSON.stringify('
            '{action:action,job_id:JOB_ID})});location.reload();}</script>'
            % (json.dumps(control_url), json.dumps(job_id))
        )
    page = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<title>STALKER 汉化进度</title>
<style>
:root{color-scheme:dark}
body{margin:0;padding:16px 20px;background:#12151a;color:#e6e9ef;font:13px/1.55 "Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:1080px}
h1{font-size:16px;font-weight:600;margin:0 0 4px}
h1 .state{color:#3fa9f5;font-weight:500;margin-left:4px}
h1 .pct{color:#8ef0c6;font-size:16px;margin-left:8px}
.meta{color:#c9d4e3;font-size:12px;margin:0 0 8px}
.bar{height:8px;background:#232833;border-radius:4px;overflow:hidden;max-width:560px}
.bar>i{display:block;height:100%%;background:linear-gradient(90deg,#3fa9f5,#38d39f);transition:width .4s}
.bar.cur{max-width:420px;height:6px;margin-bottom:12px}
.row{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin:12px 0 4px}
.row .k{color:#9aa4b2;font-size:12px}
.row .v{color:#d7dee8;font-size:12px;word-break:break-all}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 14px}
.stats span{background:#1b2029;border:1px solid #2a3140;border-radius:6px;padding:3px 8px;font-size:12px;color:#c9d4e3}
table{width:100%%;border-collapse:collapse;table-layout:fixed}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #232833;vertical-align:top;font-size:12px;line-height:1.45}
th{color:#9aa4b2;font-weight:500}
td.src{color:#aab4c3;width:48%%;word-break:break-word}
td.dst{color:#8ef0c6;word-break:break-word}
.empty{color:#6b7686;text-align:center}
.note{color:#f0b35b;margin-top:12px;white-space:pre-wrap;font-size:12px}
</style></head><body><div class="wrap">
<h1>STALKER 汉化进度 · <span class="state">%s</span><span class="pct">%d%%</span></h1>
<div class="meta">%s</div>
<div class="bar"><i style="width:%d%%"></i></div>
<div class="stats">%s</div>
%s
%s
<table><thead><tr><th>原文</th><th>译文</th></tr></thead><tbody>%s</tbody></table>
%s
</div></body></html>""" % (
        html.escape(state_label), pct, html.escape(overall), pct,
        chips_html, bars, controls_html, "".join(rows),
        ('<div class="note">%s</div>' % html.escape(str(note))) if note else "",
    )
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(page)
        os.replace(tmp, path)
    except Exception:
        pass


def _stdin_reader():
    """后台读 stdin，主循环与“服务端发起的请求”（如弹窗）共用同一个队列。"""
    try:
        for line in _PROTO_IN:
            _IN_Q.put(line)
    except Exception:
        pass
    finally:
        _IN_Q.put(None)


def _client_request(method, params, timeout=1800):
    """向客户端发一个 JSON-RPC 请求并等它的响应（如 elicitation/create）。

    只在工具调用线程里使用；期间收到的通知忽略，客户端反向请求回 -32601。
    返回 result dict；超时/客户端报错/连接断开返回 None。
    """
    _REQ_SEQ[0] += 1
    req_id = "srv-%d" % _REQ_SEQ[0]
    _send({"jsonrpc": "2.0", "id": req_id, "method": method,
           "params": params})
    deadline = time.time() + timeout
    while True:
        remain = deadline - time.time()
        if remain <= 0:
            _log("等待客户端响应超时：%s（%.0fs）" % (method, timeout))
            return None
        try:
            raw = _IN_Q.get(timeout=remain)
        except queue.Empty:
            return None
        if raw is None:
            _IN_Q.put(None)
            _log("等待 %s 时 stdin 已关闭" % method)
            return None
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        if msg.get("id") == req_id:
            if "error" in msg:
                _log("客户端拒绝 %s：%s" % (method, msg["error"]))
                return None
            return msg.get("result")
        if msg.get("id") is not None and msg.get("method"):
            _send({"jsonrpc": "2.0", "id": msg["id"],
                   "error": {"code": -32601,
                             "message": "服务端不支持该请求"}})
        elif msg.get("method"):
            _log("等待表单时收到通知：%s" % msg.get("method"))


class ToolError(Exception):
    """工具可预期的失败（参数/环境问题），直接回给客户端。"""


# ── 配置与内核 ──────────────────────────────────────────────────────

def _hermes_defaults(cfg):
    """作为 Hermes 插件运行时，自动补齐端点/模型/API Key，做到零配置。"""
    home = os.environ.get("LOCALAPPDATA") or ""
    path = os.path.join(home, "Hermes", "config.yaml")
    if not os.path.isfile(path):
        return cfg
    base = model = ""
    try:
        in_model = False
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
            for line in f:
                if not in_model:
                    if line.strip() == "model:":
                        in_model = True
                    continue
                if line and not line[0].isspace():
                    break
                stripped = line.strip()
                if stripped.startswith("base_url:"):
                    base = stripped.split(":", 1)[1].strip().strip("\"'")
                elif stripped.startswith("default:"):
                    model = stripped.split(":", 1)[1].strip().strip("\"'")
    except OSError:
        return cfg
    if base and (not cfg.get("base_url")
                 or cfg["base_url"] == "http://127.0.0.1:8080/v1"):
        cfg["base_url"] = base
    # Hermes Agent 模型不一定擅长翻译；模型选择交给 translation_plan。
    if not cfg.get("api_key"):
        key_path = os.path.join(home, "hermes", "runtimes", "llamacpp",
                                ".api_key")
        try:
            with open(key_path, "r", encoding="utf-8") as f:
                cfg["api_key"] = f.read().strip()
        except OSError:
            pass
    return cfg


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    path = CONFIG_PATH
    if not os.path.isfile(path):
        example = os.path.join(HERE, "config.example.json")
        if os.path.isfile(example):
            path = example
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            cfg.update(loaded)
    except FileNotFoundError:
        pass
    except Exception as e:
        _log("读取配置失败（用默认值）：%s: %s" % (type(e).__name__, e))
    cfg = _hermes_defaults(cfg)
    cfg["base_url"] = str(cfg.get("base_url") or "").strip()
    cfg["model"] = str(cfg.get("model") or "").strip()
    cfg["api_key"] = str(cfg.get("api_key") or "").strip()
    return cfg


def _get_termbase():
    if _TERMBASE["obj"] is None:
        _TERMBASE["obj"] = Termbase(
            os.path.join(ASSET_ROOT, "stalker_termbase.json"),
            (os.path.join(ASSET_ROOT, "glossary_builtin.json"),),
        )
    return _TERMBASE["obj"]


def _get_user_gloss():
    if _USER_GLOSS["obj"] is None:
        _USER_GLOSS["obj"] = UserGlossaryStore(
            os.path.join(DATA_DIR, "glossary_user.json"),
            _get_termbase().load_builtin,
        )
    return _USER_GLOSS["obj"]


def _load_parser():
    # 解析器来源: wzyddg/stalker_auto-trans-tool_remake (license: null, 见 THIRD_PARTY_NOTICES.md 第 1 节)
    return load_vendor(os.path.join(ASSET_ROOT, "xray_pkg.zip"))


def _is_writable(path):
    target = os.path.abspath(path)
    while not os.path.exists(target) and os.path.dirname(target) != target:
        target = os.path.dirname(target)
    return os.path.isdir(target) and os.access(target, os.W_OK)


def _strip_v1(base):
    base = str(base or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


class RemoteEngine(object):
    """OpenAI 兼容端点适配器，实现共享离线作业所需的文本接口。"""

    def __init__(self, cfg):
        self.base = _strip_v1(cfg.get("base_url"))
        self.model = cfg.get("model") or "local-model"
        self.model_name = self.model
        self.ctx_size = max(512, int(cfg.get("ctx_size") or 8192))
        self.parallel = max(1, int(cfg.get("parallel") or 1))
        self.timeout_sec = max(10, int(cfg.get("timeout_sec") or 300))
        self.max_retries = max(0, int(cfg.get("max_retries") or 4))
        self.prompt_extra = str(cfg.get("prompt_extra") or "")
        self.official_prompts = bool(cfg.get("official_prompts", False))
        self.tier = "remote"
        self.variant = "remote"
        self.health_warn = ""
        self.gpu_verified = True
        self._auth = str(cfg.get("api_key") or "").strip()
        self.pause_event = None
        self.stop_event = None
        self.recent = deque(maxlen=10)

    def is_running(self):
        return True

    def stop(self):
        pass

    def _gate(self):
        event = self.pause_event
        stopped = self.stop_event
        while event is not None and event.is_set() and not (stopped is not None and stopped.is_set()):
            time.sleep(0.25)

    def _post(self, payload, timeout_sec):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._auth:
            headers["Authorization"] = "Bearer " + self._auth
        request = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers, method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            return json.loads(response.read().decode("utf-8", "ignore"))

    def _request(self, system, user, max_tokens, temperature, timeout_sec):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1 if temperature is None else float(temperature),
            "max_tokens": max_tokens,
            "stream": False,
        }
        last_error = None
        for attempt in range(self.max_retries + 1):
            if self.stop_event is not None and self.stop_event.is_set():
                raise IOError("翻译已停止")
            try:
                data = self._post(payload, timeout_sec)
                choices = data.get("choices") or []
                if not choices:
                    last_error = str(data.get("error") or "响应缺少 choices")[:160]
                    delay = min(8.0, 0.5 * (2 ** attempt))
                else:
                    choice = choices[0]
                    content = ((choice.get("message") or {}).get("content") or "").strip()
                    content, _wrap = strip_output_wrappers(content)
                    if choice.get("finish_reason") == "length":
                        raise OutputTruncated("输出达到 max_tokens(%d) 被截断" % max_tokens)
                    if not content:
                        last_error = "空输出"
                        delay = min(8.0, 0.5 * (2 ** attempt))
                    else:
                        return content
            except OutputTruncated:
                raise
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                last_error = "HTTP %d" % exc.code
                if not retryable or attempt >= self.max_retries:
                    raise
                try:
                    delay = max(0.1, float(exc.headers.get("Retry-After") or 0))
                except (TypeError, ValueError):
                    delay = min(8.0, 0.5 * (2 ** attempt))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = "%s: %s" % (type(exc).__name__, exc)
                if attempt >= self.max_retries:
                    raise
                delay = min(8.0, 0.5 * (2 ** attempt))
            if self.stop_event is not None:
                self.stop_event.wait(delay)
            else:
                time.sleep(delay)
        raise IOError("翻译请求失败：%s" % (last_error or "无响应"))

    def translate(self, text, src_lang, tgt_lang, glossary_hits=None,
                  timeout_sec=None, max_tokens=None, use_extra=True,
                  extra_sys=None, temperature=None, context=None, style=None):
        self._gate()
        if src_lang in ("auto", "text"):
            src_lang = detect_text_lang(text) or src_lang
        source_name = LANG_FULL.get(src_lang)
        target_name = LANG_FULL.get(tgt_lang, tgt_lang)
        system = TRANSLATION_SYS_PROMPT_EN if src_lang == "eng" else TRANSLATION_SYS_PROMPT
        extra = (self.prompt_extra or "").strip() if use_extra else ""
        if extra:
            system += "\n补充要求（优先遵守）：\n" + extra
        if extra_sys:
            system += "\n补充要求（优先遵守）：\n" + extra_sys
        if self.official_prompts:
            user = "将以下文本翻译为%s，注意只需要输出翻译后的结果，不要额外解释：\n%s" % (target_name, text)
        elif source_name and src_lang not in ("auto", "text"):
            user = "将下面的%s文本翻译为%s：\n%s" % (source_name, target_name, text)
        else:
            user = "将下面文本翻译为%s：\n%s" % (target_name, text)
        if context:
            user = "上下文（仅供理解，不要翻译）：\n%s\n\n%s" % (context, user)
        if glossary_hits:
            block, _used = glossary_hint_block(glossary_hits, official=self.official_prompts)
            if block:
                user = block + "\n\n" + user
        if max_tokens is None:
            max_tokens = budget_max_tokens(len(text or ""), self.ctx_size)
        out = self._request(system, user, max_tokens, temperature,
                            timeout_sec or self.timeout_sec)
        self.recent.append((str(text)[:240], str(out)[:240]))
        return out

    def translate_batch(self, lines, src_lang, tgt_lang, glossary_hits=None,
                        timeout_sec=None, max_tokens=None, temperature=None):
        self._gate()
        if src_lang in ("auto", "text"):
            src_lang = detect_text_lang(lines[0] if lines else "") or src_lang
        target_name = LANG_FULL.get(tgt_lang, tgt_lang)
        numbered = "\n".join("[%d] %s" % (i + 1, line) for i, line in enumerate(lines))
        system = TRANSLATION_BATCH_SYS_PROMPT
        if self.prompt_extra:
            system += "\n补充要求（优先遵守）：\n" + self.prompt_extra
        user = "把下面的连续对话逐行翻译为%s：\n%s" % (target_name, numbered)
        if glossary_hits:
            block, _used = glossary_hint_block(glossary_hits)
            if block:
                user = block + "\n\n" + user
        if max_tokens is None:
            max_tokens = budget_max_tokens(sum(len(x) for x in lines), self.ctx_size)
        out = self._request(system, user, max_tokens, temperature,
                            timeout_sec or self.timeout_sec)
        for match in re.finditer(r"\[(\d+)\]\s*([^\n]*)", str(out or "")):
            index = int(match.group(1))
            translated = (match.group(2) or "").strip()
            if 1 <= index <= len(lines) and translated:
                self.recent.append((str(lines[index - 1])[:240], translated[:240]))
        return out


def _get_engine():
    if _ENGINE["obj"] is None:
        _ENGINE["obj"] = RemoteEngine(load_config())
    return _ENGINE["obj"]


def _get_core_gloss():
    if _CORE_GLOSS["index"] is None:
        _CORE_GLOSS["index"] = GlossaryIndex.from_json(
            os.path.join(ASSET_ROOT, "stalker_termbase.json"))
    return _CORE_GLOSS["index"]


def _get_text_gloss():
    """整句翻译用的增强术语索引（与离线插件同一份分层策略）。"""
    if _TEXT_GLOSS["index"] is None:
        layers = _get_termbase().load_layers()
        merged, _user_set, _deleted, _clear = _get_user_gloss().layers()
        _TEXT_GLOSS["index"] = TermIndex(
            merged, case_sensitive=layers.get("case_sensitive"),
            variants=layers.get("variants"))
    return _TEXT_GLOSS["index"]


# ── 端点探测 ────────────────────────────────────────────────────────

def _http_json(url, api_key="", timeout=8):
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    if api_key:
        req.add_header("Authorization", "Bearer " + api_key)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "ignore")
        try:
            return r.status, json.loads(body)
        except Exception:
            return r.status, body


def _probe_endpoint(cfg):
    base = _strip_v1(cfg.get("base_url"))
    if not base:
        return {"ok": False, "error": "config.json 未填写 base_url"}
    key = cfg.get("api_key") or ""
    info = {"base_url": base + "/v1", "models": [], "ok": False}
    try:
        status, data = _http_json(base + "/v1/models", key)
        if status == 200 and isinstance(data, dict):
            info["ok"] = True
            info["models"] = [str(m.get("id")) for m in (data.get("data") or [])
                              if isinstance(m, dict)]
    except urllib.error.HTTPError as e:
        info["error"] = "HTTP %d：%s" % (e.code, base + "/v1/models")
    except Exception as e:
        info["error"] = "%s: %s" % (type(e).__name__, e)
    if not info["ok"]:
        # llama.cpp 的 /health 也能判断端点是否活着（老版本没有 /v1/models）。
        try:
            status, _data = _http_json(base + "/health", key, timeout=5)
            info["ok"] = status == 200
            info["health"] = status
            info.pop("error", None)
        except Exception:
            pass
    want = cfg.get("model") or ""
    if info["ok"] and want and info["models"] and want not in info["models"]:
        info["model_mismatch"] = want
    return info


# ── 并发路数规划（显卡/显存/端点槽位）──────────────────────────────

def _fmt_mb(mib):
    mib = int(mib or 0)
    return ("%.1f GB" % (mib / 1024.0)) if mib >= 1024 else ("%d MB" % mib)




def _parse_llama_args(args):
    """把 llama-server 的启动参数列表解析成 dict（只认 --key value 形式）。"""
    opts = {}
    i = 0
    while i < len(args or []):
        item = str(args[i])
        if item.startswith("--"):
            key = item[2:]
            if i + 1 < len(args) and not str(args[i + 1]).startswith("--"):
                opts[key] = str(args[i + 1])
                i += 2
                continue
            opts[key] = True
        i += 1
    return opts


def _endpoint_facts(cfg):
    """从 /v1/models、/props、/slots 读端点真实槽位、每槽上下文与模型路径。

    Hermes 的 18434 是路由：/v1/models 的 args 里带子进程 --port，需要再向
    子进程查 /props 才能拿到 total_slots / model_path。
    """
    base = _strip_v1(cfg.get("base_url"))
    key = cfg.get("api_key") or ""
    facts = {"base": base, "slots": 0, "ctx": 0, "model_path": "",
             "port": 0, "approx": True}
    try:
        status, data = _http_json(base + "/v1/models", key)
        entries = data.get("data") if isinstance(data, dict) else []
        entry = None
        for ent in entries or []:
            if isinstance(ent, dict) and str(ent.get("id")) == cfg.get("model"):
                entry = ent
                break
        if entry is None:
            for ent in entries or []:
                if (isinstance(ent, dict)
                        and (ent.get("status") or {}).get("value") == "loaded"):
                    entry = ent
                    break
        raw_args = None
        if isinstance(entry, dict):
            status = entry.get("status")
            if isinstance(status, dict) and isinstance(status.get("args"), list):
                raw_args = status["args"]
            elif isinstance(entry.get("args"), list):
                raw_args = entry["args"]
        opts = _parse_llama_args(raw_args)
        port = opts.get("port")
        if str(port or "").isdigit():
            facts["port"] = int(port)
        for field, name in (("ctx-size", "ctx"), ("parallel", "slots")):
            val = opts.get(field)
            if str(val or "").isdigit():
                facts[name] = int(val)
        if isinstance(opts.get("model"), str):
            facts["model_path"] = opts["model"]
    except Exception:
        pass

    # 子进程端口与主端口不同时，直接问子进程（Hermes 的 llama-server 子进程
    # 通常没开鉴权；带 key 也不会被拒绝）。
    host = urllib.parse.urlsplit(base).hostname or "127.0.0.1"
    main_port = urllib.parse.urlsplit(base).port or 80
    props_base = base
    if facts["port"] and facts["port"] != main_port:
        props_base = "http://%s:%d" % (host, facts["port"])
    try:
        status, props = _http_json(props_base + "/props", key, timeout=5)
        if isinstance(props, dict):
            if str(props.get("total_slots") or "").isdigit():
                facts["slots"] = int(props["total_slots"])
            mp = props.get("model_path")
            if (isinstance(mp, str) and mp
                    and mp.lower() not in ("none", "null")
                    and os.path.isfile(mp)):
                facts["model_path"] = mp
            n_ctx = (props.get("default_generation_settings") or {}).get("n_ctx")
            if str(n_ctx or "").isdigit() and int(n_ctx) > 0:
                facts["ctx"] = int(n_ctx)
    except Exception:
        pass
    try:
        status, slots = _http_json(props_base + "/slots", key, timeout=5)
        if isinstance(slots, list) and slots:
            facts["slots"] = len(slots)
    except Exception:
        pass
    return facts


def _model_path_hint(cfg, model):
    """尽量找到某个模型名的本地 gguf 路径（用于按模型估算显存）。"""
    if not model:
        return ""
    base = _strip_v1(cfg.get("base_url"))
    key = cfg.get("api_key") or ""
    try:
        status, data = _http_json(base + "/v1/models", key, timeout=5)
        for ent in (data.get("data") if isinstance(data, dict) else []) or []:
            if not isinstance(ent, dict) or str(ent.get("id")) != model:
                continue
            args = None
            st = ent.get("status")
            if isinstance(st, dict) and isinstance(st.get("args"), list):
                args = st["args"]
            elif isinstance(ent.get("args"), list):
                args = ent["args"]
            path = _parse_llama_args(args).get("model")
            if isinstance(path, str) and os.path.isfile(path):
                return path
    except Exception:
        pass
    home = os.environ.get("LOCALAPPDATA") or ""
    presets = os.path.join(home, "hermes", "runtimes", "llamacpp", "presets.ini")
    if os.path.isfile(presets):
        try:
            section = ""
            with open(presets, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("[") and s.endswith("]"):
                        section = s[1:-1]
                    elif section == model and "=" in s and s.lower().startswith("model"):
                        path = s.split("=", 1)[1].strip()
                        if os.path.isfile(path):
                            return path
        except Exception:
            pass
    path = os.path.join(home, "hermes", "models", str(model) + ".gguf")
    return path if os.path.isfile(path) else ""


def _translation_model_choices(cfg):
    """端点上的可选翻译模型：优先 Hy-MT2/translate，返回 (names, labels, default)。"""
    models = []
    try:
        models = [str(m) for m in (_probe_endpoint(cfg).get("models") or []) if m]
    except Exception:
        pass
    seen = set()
    models = [m for m in models if not (m in seen or seen.add(m))]
    prefer = [m for m in models
              if any(k in m.lower() for k in ("hy-mt", "hy_mt", "translate"))]
    pool = prefer or models
    if not pool:
        return [], [], cfg.get("model") or ""
    agent = _hermes_agent_model()
    labels = []
    for name in pool:
        low = name.lower()
        if "1.8b" in low:
            labels.append("%s · 快，推荐日常" % name)
        elif "7b" in low:
            labels.append("%s · 质量更好，更慢、更占显存" % name)
        elif agent and name == agent:
            labels.append("%s · Hermes 对话模型，不建议翻译" % name)
        else:
            labels.append(name)
    default = ""
    for marker in ("hy-mt2-1.8b", "hy_mt2_1.8b", "hy-mt2-7b", "hy_mt2_7b"):
        default = next((name for name in pool if marker in name.lower()), "")
        if default:
            break
    if not default:
        default = next((name for name in pool if name != agent), "")
    if not default and cfg.get("model") in pool:
        default = cfg["model"]
    if not default:
        default = pool[0]
    return pool, labels, default


def _route_plan(cfg):
    """按远端端点真实槽位生成并发选项；资源管理交给端点。"""
    base = _strip_v1(cfg.get("base_url"))
    cache_key = (base, cfg.get("model"), int(cfg.get("ctx_size") or 8192))
    if _PLAN_CACHE["key"] == cache_key and _PLAN_CACHE["plan"]:
        return _PLAN_CACHE["plan"]
    facts = _endpoint_facts(cfg)
    slots = int(facts.get("slots") or 0)
    assumed = slots <= 0
    if assumed:
        slots = 4
    max_now = max(1, min(16, slots))
    try:
        recommended = int(cfg.get("parallel") or 1)
    except (TypeError, ValueError):
        recommended = 1
    recommended = max(1, min(max_now, recommended))
    options = []
    for count in range(1, max_now + 1):
        reason = "端点并发槽位 %d；单路最稳，多路提高吞吐" % slots
        label = "%d 路（%s）" % (count, "推荐" if count == recommended else ("最稳" if count == 1 else ""))
        options.append({"parallel": count, "label": label, "reason": reason})
    notes = [
        "模型：%s；每路上下文 %d token" % (cfg.get("model") or "?", int(cfg.get("ctx_size") or 8192)),
        "端点槽位：%d%s" % (slots, "（未报告，按默认 4 槽估算）" if assumed else ""),
        "显存与内存由远端端点管理；路数超过端点槽位时会排队",
    ]
    plan = {
        "gpu": {"name": "", "vram_mb": 0},
        "model": {"name": cfg.get("model") or "", "path": _model_path_hint(cfg, cfg.get("model")),
                  "weights_mb": 0, "ctx_per_route": int(cfg.get("ctx_size") or 8192),
                  "approx": True},
        "endpoint": {"base_url": cfg.get("base_url") or "", "slots": slots,
                     "slots_assumed": assumed},
        "budget_mb": 0,
        "max_by_vram": max_now,
        "recommended": recommended,
        "options": options,
        "notes": notes,
    }
    _PLAN_CACHE["key"] = cache_key
    _PLAN_CACHE["plan"] = plan
    return plan


def _supports_elicitation():
    return "elicitation" in (_CLIENT.get("caps") or {})


def _hermes_agent_model():
    """尽力读取 Hermes config.yaml 的 model.default（用于提示模型混用风险）。"""
    home = os.environ.get("LOCALAPPDATA") or ""
    path = os.path.join(home, "Hermes", "config.yaml")
    if not os.path.isfile(path):
        return ""
    try:
        in_model = False
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
            for line in f:
                if not in_model:
                    if line.startswith("model:") and line.strip() == "model:":
                        in_model = True
                    continue
                if line and not line[0].isspace():
                    break
                stripped = line.strip()
                if stripped.startswith("default:"):
                    return stripped.split(":", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


def _ask_start_options(plan, cfg):
    """弹窗让用户选翻译模型和并发路数。返回 (路数, 模型, 备注)。"""
    options = plan.get("options") or []
    rec = int(plan.get("recommended") or 1)
    allowed = [int(o["parallel"]) for o in options]
    models, model_labels, default_model = _translation_model_choices(cfg)
    has_parallel = len(options) >= 2
    has_model = len(models) >= 2

    if not _supports_elicitation():
        try:
            n = int(cfg.get("parallel"))
        except (TypeError, ValueError):
            n = 0
        if n not in allowed:
            n = rec
        note = ("当前客户端不支持选择弹窗，已使用 %d 路%s；"
                "可在调用时直接传 parallel / model 覆盖"
                % (n, ("、模型 " + default_model) if default_model else ""))
        return n, default_model, note
    if not has_parallel and not has_model:
        return rec, default_model, ""

    lines = list(plan.get("notes") or [])
    lines.append("")
    props = {}
    required = []
    if has_model:
        lines.append("选择翻译模型：")
        for name, label in zip(models, model_labels):
            lines.append("  · " + label)
        lines.append("（切换模型会让本地端点重新加载，首次可能要等几十秒；"
                     "Hermes 对话模型可能被换出）")
        props["model"] = {
            "type": "string",
            "title": "翻译模型",
            "description": "7B 质量更好但更慢、更占显存；1.8B 更快",
            "enum": models,
            "enumNames": model_labels,
            "default": default_model,
        }
        required.append("model")
    if has_parallel:
        if has_model:
            lines.append("（路数按默认模型估算；选 7B 会自动收窄）")
        lines.append("选择并发路数：")
        for opt in options:
            lines.append("  · %d 路 — %s" % (opt["parallel"], opt["reason"]))
        props["parallel"] = {
            "type": "integer",
            "title": "并发路数",
            "description": "路数越多吞吐越高，显存/上下文占用也越大",
            "enum": [o["parallel"] for o in options],
            "enumNames": [o["label"] for o in options],
            "default": rec,
        }
        required.append("parallel")

    result = _client_request("elicitation/create", {
        "message": "\n".join(lines),
        "requestedSchema": {"type": "object", "properties": props,
                            "required": required},
    })
    if result is None:
        note = ("客户端未完成模型/路数选择，已使用安全默认值：%s、%d 路"
                % (default_model, rec))
        return rec, default_model, note
    if str(result.get("action") or "") != "accept":
        raise ToolError("已取消：未选择翻译参数，翻译未启动")
    content = result.get("content") or {}
    chosen_model = default_model
    if has_model:
        value = content.get("model")
        if value in models:
            chosen_model = value
    chosen_parallel = rec
    if has_parallel:
        try:
            chosen_parallel = int(content.get("parallel"))
        except (TypeError, ValueError):
            chosen_parallel = rec
        if chosen_parallel not in allowed:
            chosen_parallel = rec
    return chosen_parallel, chosen_model, ""


# ── 工具实现 ────────────────────────────────────────────────────────

def _json_text(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)


def tool_translation_plan(args):
    cfg = load_config()
    plan = _route_plan(cfg)
    models, labels, default_model = _translation_model_choices(cfg)
    return {
        "models": [{"name": name, "label": label}
                   for name, label in zip(models, labels)],
        "default_model": default_model,
        "recommended_parallel": int(plan.get("recommended") or 1),
        "endpoint": plan.get("endpoint") or {},
        "options": plan.get("options") or [],
        "notes": plan.get("notes") or [],
    }


def tool_check_env(args):
    cfg = load_config()
    document = load_termbase_document(
        os.path.join(ASSET_ROOT, "stalker_termbase.json"))
    layers = layer_counts(document)
    bundle = AssetBundle.load(ASSET_ROOT)
    out = {
        "ready": False,
        "server": {"name": SERVER_NAME, "version": VERSION},
        "core": CORE_VERSION,
        "pipeline": VERSION,
        "python": sys.version.split()[0],
        "config_file": CONFIG_PATH,
        "endpoint": _probe_endpoint(cfg),
        "model": cfg.get("model") or "",
        "termbase": layers,
        "asset_sha256": dict(bundle.hashes),
    }
    missing = []
    if not out["endpoint"].get("ok"):
        missing.append("本地模型端点不可达（%s）。请先在 Hermes 中启动本地模型"
                       "（参考安装教程），或修改 config.json 的 base_url。"
                       % (out["endpoint"].get("error") or "连接失败"))
    try:
        plan = _route_plan(cfg)
        out["gpu"] = plan["gpu"]
        out["endpoint_slots"] = plan["endpoint"]["slots"]
        out["routes"] = {
            "recommended": plan["recommended"],
            "max_by_vram": plan["max_by_vram"],
            "options": [o["parallel"] for o in plan["options"]],
            "notes": plan["notes"],
        }
    except Exception as e:
        out["routes_error"] = "%s: %s" % (type(e).__name__, e)
    agent_model = _hermes_agent_model()
    if agent_model:
        out["hermes_agent_model"] = agent_model
        if agent_model == (cfg.get("model") or ""):
            out.setdefault("warnings", []).append(
                "翻译模型与 Hermes agent 模型相同（%s）：翻译模型通常不擅长"
                "工具调用，建议把 agent 换成指令模型" % agent_model)
    try:
        _load_parser()
    except Exception as e:
        missing.append("X-Ray 解析器不可用：%s" % e)
    if out["termbase"]["canon"] < 100:
        missing.append("术语库为空或损坏，请重新运行 install.ps1")
    if args.get("deep") and out["endpoint"].get("ok"):
        try:
            eng = _get_engine()
            out["probe_translation"] = eng.translate(
                "Hello, stalker.", "eng", "chs", timeout_sec=60)
        except Exception as e:
            missing.append("模型可用但翻译探测失败：%s: %s"
                           % (type(e).__name__, e))
    out["ready"] = not missing
    if missing:
        out["problems"] = missing
    return out


def tool_translate_text(args):
    text = str(args.get("text") or "").strip()
    if not text:
        raise ToolError("text 不能为空")
    if len(text) > 20000:
        raise ToolError("单次文本过长（%d 字符）。MOD 请用 translate_mod"
                        % len(text))
    cfg = load_config()
    tgt = str(args.get("target_lang") or cfg.get("target_lang") or "chs")
    src = str(args.get("source_lang") or cfg.get("source_lang") or "auto")
    exact = _get_core_gloss().lookup(text)
    if exact is None:
        exact = _get_termbase().exact_translation(text)
    if exact is not None:
        return {"translation": exact, "source": text, "term_exact": True}
    eng = _get_engine()
    hints = _get_text_gloss().hits(text)
    try:
        out = eng.translate(text, src, tgt, glossary_hits=hints or None,
                            context=str(args.get("context") or "") or None)
        quality_issues = []
        if out == text and _has_translatable_text(text):
            quality_issues.append("模型未产生可用译文")
        if not runtime_tokens_match(text, out):
            out = eng.translate(text, src, tgt, glossary_hits=hints or None,
                                extra_sys=_PLACEHOLDER_RETRY_NOTE)
            if not runtime_tokens_match(text, out):
                quality_issues.append("颜色码/占位符未能原样保留")
        if tgt == "chs":
            role_issues = semantic_role_issues(text, out, src)
            if role_issues:
                out = eng.translate(text, src, tgt, glossary_hits=hints or None,
                                    extra_sys=_SEMANTIC_ROLE_NOTE)
                role_issues = semantic_role_issues(text, out, src)
                if role_issues:
                    quality_issues.extend(role_issues)
        if not runtime_tokens_match(text, out):
            if "颜色码/占位符未能原样保留" not in quality_issues:
                quality_issues.append("颜色码/占位符未能原样保留")
            out = text
        elif quality_issues:
            out = text
    except Exception as e:
        raise ToolError("翻译请求失败：%s: %s。%s"
                        % (type(e).__name__, e, _endpoint_hint(cfg)))
    result = {"translation": out, "source": text, "model": eng.model,
              "term_hits": [[k, v] for k, v in (hints or [])]}
    if quality_issues:
        result["retained_source"] = True
        result["quality_issues"] = quality_issues
    return result


def _endpoint_hint(cfg):
    return ("请确认本地模型已在 Hermes 中启动且 %s 可访问"
            % (cfg.get("base_url") or "端点"))


def tool_translate_mod(args):
    cfg = load_config()
    in_dir = str(args.get("input_dir") or "").strip().strip('"')
    if not in_dir or not os.path.isdir(in_dir):
        raise ToolError("input_dir 不是有效目录：%s" % (in_dir or "(空)"))
    out_dir = str(args.get("output_dir") or "").strip()
    if not out_dir:
        out_dir = os.path.join(in_dir, "translated_hy_mt2")
    roots = cfg.get("allowed_roots") or []
    if roots:
        for label, value in (("input_dir", in_dir), ("output_dir", out_dir)):
            real = os.path.realpath(os.path.abspath(value))
            allowed = False
            for root in roots:
                root_real = os.path.realpath(os.path.abspath(str(root)))
                try:
                    allowed = os.path.commonpath([real, root_real]) == root_real
                except ValueError:
                    allowed = False
                if allowed:
                    break
            if not allowed:
                raise ToolError("%s 不在 allowed_roots 内：%s" % (label, value))
    mode = str(args.get("mode") or "auto")
    if mode not in ("auto", "text", "cfgxml", "ltx", "scriptL", "scriptE"):
        raise ToolError("不支持的 mode：%s" % mode)

    plan = _route_plan(cfg)
    models, _labels, default_model = _translation_model_choices(cfg)
    chosen_model = str(args.get("model") or default_model or cfg.get("model") or "")
    plan_note = ""
    if args.get("parallel") is None and args.get("model") is None:
        parallel, chosen_model, plan_note = _ask_start_options(plan, cfg)
    else:
        try:
            parallel = int(args.get("parallel") or cfg.get("parallel") or 1)
        except (TypeError, ValueError):
            raise ToolError("parallel 必须是 1-16 的整数")
        parallel = max(1, min(16, parallel))
        if not args.get("model"):
            plan_note = "未指定翻译模型，已选择 %s" % chosen_model
    slots = int((plan.get("endpoint") or {}).get("slots") or 0)
    if slots:
        parallel = max(1, min(parallel, slots))
    job_id = uuid.uuid4().hex[:12]

    def target(cancel_event, pause_event):
        return _run_translation(
            args, cfg, chosen_model, parallel, plan, plan_note,
            job_id, cancel_event, pause_event,
        )

    record = _JOBS.start(
        job_id, target, output_dir=out_dir, model=chosen_model, parallel=parallel,
    )
    return {
        "status": "running",
        "job_id": record.job_id,
        "input_dir": in_dir,
        "output_dir": out_dir,
        "model": chosen_model,
        "parallel": parallel,
        "route": {
            "slots": int((plan.get("endpoint") or {}).get("slots") or 0),
            "recommended": int(plan.get("recommended") or 1),
            "options": [option["parallel"] for option in plan.get("options") or []],
        },
        "hint": "使用 job_status 查询进度；停止时调用 job_cancel。",
        "route_note": plan_note,
    }


def tool_job_status(args):
    job_id = str(args.get("job_id") or "").strip()
    record = _JOBS.get(job_id)
    if record is None:
        raise ToolError("job_id 不存在：%s" % (job_id or "(空)"))
    return record.snapshot()


def tool_job_cancel(args):
    job_id = str(args.get("job_id") or "").strip()
    if not _JOBS.cancel(job_id):
        raise ToolError("任务不存在或已结束：%s" % (job_id or "(空)"))
    return {"job_id": job_id, "state": "stopping"}


def _run_translation(args, cfg, chosen_model, parallel, plan, plan_note,
                     job_id, cancel_event, pause_event):
    in_dir = str(args.get("input_dir") or "").strip().strip('"')
    out_dir = str(args.get("output_dir") or "").strip()
    if not out_dir:
        out_dir = os.path.join(in_dir, "translated_hy_mt2")
    mode = str(args.get("mode") or "auto")
    src = str(args.get("source_lang") or cfg.get("source_lang") or "auto")
    tgt = str(cfg.get("target_lang") or "chs")
    eng = _get_engine()
    if chosen_model:
        eng.model = chosen_model
        eng.model_name = chosen_model
    parser = _load_parser()
    logs = []

    def log(msg, tag="info"):
        line = str(msg)
        logs.append(line)
        if len(logs) > 400:
            del logs[:200]
        _log("[job] " + line)
        if tag in ("err", "warn"):
            _mcp_log("error" if tag == "err" else "warning", line)
        elif (not line.startswith("  ├──")
              and any(k in line for k in ("进度", "文本表：", "引用层完成",
                                          "全部完成", "多路翻译", "会话词表",
                                          "高频专名", "译文目录"))):
            _mcp_log("info", line)

    token = args.get("_progress_token")
    last_emit = {"t": 0.0}
    started_at = time.time()
    progress_page = os.path.join(out_dir, ".progress.html")
    control_url = _control_url(job_id)
    last_state = {"d": {}}
    accepted_pairs = deque(maxlen=20)
    accepted_lock = threading.Lock()

    def accepted(source, translated):
        with accepted_lock:
            accepted_pairs.append((str(source)[:240], str(translated)[:240]))

    def progress(d):
        with _PROGRESS_LOCK:
            now = time.time()
            if now - last_emit["t"] < 1.5:
                return
            last_emit["t"] = now
            last_state["d"] = dict(d or {})
            record = _JOBS.get(job_id)
            if record is not None:
                record.progress = dict(d or {})
            total = int(d.get("files") or 0)
            idx = int(d.get("file_idx") or 0)
            ids = int(d.get("ids") or 0)
            total_ids = int(d.get("entries_translatable")
                            or d.get("total_ids") or 0)
            phase = str(d.get("phase") or "")
            if phase == "prescan":
                msg = "统计可翻译条目 %d/%d 张文本表" % (
                    int(d.get("prescan_done") or 0),
                    int(d.get("prescan_total") or 0))
            else:
                msg = "%d/%d %s" % (idx, total, d.get("file") or "")
                if total_ids:
                    msg += " | 已译 %d / 可译 %d 条" % (ids, total_ids)
            with accepted_lock:
                pairs = list(accepted_pairs)
            pair = pairs[-1] if pairs else None
            if pair:
                msg += " | %s → %s" % (str(pair[0])[:60], str(pair[1])[:60])
            if token:
                _notify("notifications/progress", {
                    "progressToken": token,
                    "progress": idx, "total": total,
                    "message": msg,
                })
            _mcp_log("info", "进度 " + msg)
            try:
                _write_progress_page(progress_page, d, pairs, "running",
                                     elapsed=now - started_at,
                                     control_url=control_url, job_id=job_id)
            except Exception:
                pass

    merged, user_set, _deleted, _clear = _get_user_gloss().layers()
    hard = set(_get_termbase().hard_keys()) | set(user_set)

    # 暂停/停止：进度板写控制文件，这里轮询；状态写回 out_dir 供页面显示。
    os.makedirs(out_dir, exist_ok=True)
    try:
        _write_progress_page(progress_page, {"files": 0, "file_idx": 0, "file": ""},
                             [], "running", note="正在扫描 MOD……",
                             control_url=control_url, job_id=job_id)
        if bool(cfg.get("open_progress_page", True)):
            _open_local_file(progress_page)
    except Exception:
        pass
    control_path = os.path.join(out_dir, ".ai_translate_control.json")
    status_path = os.path.join(out_dir, ".ai_translate_status.json")
    stop_event = cancel_event
    eng.pause_event = pause_event
    eng.stop_event = stop_event

    def _write_status(state):
        try:
            tmp = status_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"state": state, "updated": time.time()}, f,
                          ensure_ascii=False)
            os.replace(tmp, status_path)
        except OSError:
            pass

    _write_status("running")
    try:
        os.remove(control_path)
    except OSError:
        pass
    watcher_stop = threading.Event()

    def _watch_control():
        last = 0.0
        while not watcher_stop.is_set():
            try:
                if os.path.isfile(control_path):
                    mt = os.path.getmtime(control_path)
                    if mt != last:
                        last = mt
                        with open(control_path, "r", encoding="utf-8") as f:
                            action = str((json.load(f) or {}).get("action") or "")
                        if action == "pause":
                            _JOBS.pause(job_id)
                            _write_status("paused")
                            _mcp_log("info", "已暂停：不再发送新的翻译请求")
                        elif action == "resume":
                            _JOBS.resume(job_id)
                            _write_status("running")
                            _mcp_log("info", "已继续翻译")
                        elif action == "stop":
                            _JOBS.cancel(job_id)
                            _write_status("stopping")
                            _mcp_log("warning", "已请求停止：当前请求结束后停下")
            except Exception:
                pass
            time.sleep(0.3)

    threading.Thread(target=_watch_control, daemon=True).start()

    job = OfflineTranslationJob(
        parser, eng, in_dir, mode, src, tgt,
        glossary=dict(merged),
        force_glossary=True,
        out_dir=out_dir,
        log=log, detail=log, progress=progress,
        on_translation=accepted,
        cancel_event=cancel_event,
        reference_mode=True, copy_reference=True,
        only_referenced=False, protect_proper=True,
        preview_reference=False,
        hard_glossary_keys=hard or None,
        translate_literals=True, auto_session_terms=True,
        tm_lite=True, freq_names=True,
        parallel=parallel,
        termbase=_get_termbase(), data_root=DATA_DIR,
        pipeline_version=VERSION, parallel_cap=16, is_writable=_is_writable)
    t0 = time.time()
    error = ""
    job_ok = False
    try:
        with contextlib.redirect_stdout(sys.stderr):
            job_ok = bool(job.run())
    except Exception as e:
        error = "%s: %s" % (type(e).__name__, e)
        _log("翻译任务异常：\n" + traceback.format_exc())
    elapsed = time.time() - t0
    watcher_stop.set()
    eng.pause_event = None
    eng.stop_event = None
    audit = getattr(job, "audit_result", None)
    failed_strings = int(getattr(job, "failed_strings", 0) or 0)
    audit_bad = bool(audit and not audit.get("ok"))
    if stop_event.is_set():
        status = "stopped"
    elif error:
        status = "error"
    elif not job_ok or failed_strings or audit_bad:
        status = "needs_review"
    else:
        status = "done"
    _write_status(status)
    try:
        with accepted_lock:
            final_pairs = list(accepted_pairs)
        _write_progress_page(progress_page, last_state["d"], final_pairs,
                             status, elapsed=elapsed, note=error,
                             control_url=control_url, job_id=job_id)
    except Exception:
        pass
    warns = [x for x in logs if "错误" in x or "失败" in x or "警告" in x
             or "⚠" in x]
    result = {
        "status": status,
        "input_dir": in_dir,
        "output_dir": job.out_dir,
        "progress_page": progress_page,
        "elapsed_sec": round(elapsed, 1),
        "requests": int(getattr(job, "req_count", 0) or 0),
        "translated_chars": int(getattr(job, "trans_chars", 0) or 0),
        "failed_strings": failed_strings,
        "parse_failures": list(getattr(job, "_parse_failures", []) or []),
        "audit": audit,
        "files": (job.batch_stats or {}).get("batches"),
        "model": eng.model,
        "parallel": parallel,
        "log_tail": logs[-30:],
    }
    if plan:
        result["route"] = {
            "gpu": plan["gpu"],
            "slots": plan["endpoint"]["slots"],
            "recommended": plan["recommended"],
            "max_by_vram": plan["max_by_vram"],
        }
    if plan_note:
        result["route_note"] = plan_note
    if error:
        result["error"] = error
        result["hint"] = _endpoint_hint(cfg)
    if status == "stopped":
        result["hint"] = ("已停止。重新运行同一命令会从断点继续，"
                          "已完成文件会自动跳过。")
    if status == "needs_review":
        result["hint"] = ("处理已结束，但终检或翻译失败统计未通过；"
                          "请查看 audit 和 log_tail，修正后重新运行续译。")
    if warns:
        result["warnings"] = warns[-10:]
    result["job_id"] = job_id
    return result


# ── MCP 协议 ────────────────────────────────────────────────────────

TOOLS = TOOL_DEFINITIONS


def _tool_result(obj, is_error=False):
    return tool_result(obj, is_error)


def _handle_tool_call(params, request_id=None):
    name = params.get("name")
    args = dict(params.get("arguments") or {})
    token = (params.get("_meta") or {}).get("progressToken")
    if token:
        args["_progress_token"] = token
    handlers = {
        "check_env": tool_check_env,
        "translation_plan": tool_translation_plan,
        "translate_mod": tool_translate_mod,
        "job_status": tool_job_status,
        "job_cancel": tool_job_cancel,
        "translate_text": tool_translate_text,
    }
    fn = handlers.get(name)
    if fn is None:
        return _tool_result({"error": "未知工具：%s" % name}, True)
    try:
        result = fn(args)
        if name == "translate_mod" and result.get("job_id"):
            _JOBS.bind_request(request_id, result["job_id"])
        return _tool_result(result)
    except ToolError as e:
        return _tool_result({"error": str(e)}, True)
    except Exception as e:
        _log("工具 %s 异常：\n%s" % (name, traceback.format_exc()))
        return _tool_result({"error": "%s: %s" % (type(e).__name__, e)}, True)


def _handle(msg):
    method = msg.get("method")
    msg_id = msg.get("id", None)
    params = msg.get("params") or {}
    is_notification = "id" not in msg
    _log("<- %s" % (method or "?"))
    if method == "initialize":
        _CLIENT["caps"] = params.get("capabilities") or {}
        info = params.get("clientInfo") or {}
        _CLIENT["name"] = str(info.get("name") or "")
        _ok(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": VERSION},
            "instructions": SERVER_INSTRUCTIONS,
        })
    elif method in ("notifications/initialized", "initialized"):
        return
    elif method == "ping":
        _ok(msg_id, {})
    elif method == "tools/list":
        _ok(msg_id, {"tools": TOOLS})
    elif method == "tools/call":
        _ok(msg_id, _handle_tool_call(params, msg_id))
    elif method == "notifications/cancelled":
        request_id = params.get("requestId")
        if request_id is not None and _JOBS.cancel_request(request_id):
            _log("已处理 MCP 取消通知：%s" % request_id)
    elif is_notification:
        return
    else:
        _err(msg_id, -32601, "未实现的方法：%s" % method)


def main():
    _log("MCP 服务启动 pid=%d python=%s" % (os.getpid(),
                                            sys.version.split()[0]))
    try:
        _load_parser()
        _log("共享 core 与 X-Ray 解析器就绪")
    except Exception as e:
        _log("共享 core 加载失败（工具调用时会再试）：%s: %s" % (type(e).__name__, e))
    threading.Thread(target=_stdin_reader, daemon=True).start()
    while True:
        try:
            raw = _IN_Q.get()
        except KeyboardInterrupt:
            break
        if raw is None:
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception as e:
            _log("无法解析的请求：%s" % e)
            continue
        if not isinstance(msg, dict):
            continue
        try:
            _handle(msg)
        except Exception:
            _log("处理请求异常：\n" + traceback.format_exc())
            msg_id = msg.get("id")
            if msg_id is not None:
                _err(msg_id, -32603, "内部错误，详见 logs/mcp.log")
    _log("MCP 服务退出")


if __name__ == "__main__":
    main()
