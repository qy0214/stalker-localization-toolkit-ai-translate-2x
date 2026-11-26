# -*- coding: utf-8 -*-
"""zh_voice —— 潜行者中文语音工坊 2.x 插件壳（骨架版 0.1.0）。

本文件只做三件事，严格对应骨架期目标：
  1. PLUGIN_INFO / PLUGIN_API=1 —— 与 ai_translate 同一宿主契约；
  2. register(api) → api.register_tool(...) 注册「中文语音」工具页；
  3. 页内管理 stalker_voice_core worker 子进程：启动/停止/心跳轮询/echo
     自检。TTS 管线**不在本文件、也不在本进程**——torch 等重运行时只在
     worker 子进程里（硬约束：插件宿主进程永不 import 重库）。

线程纪律（照抄 ai_translate 的经验）：
  * api.ui.* 只许在宿主 UI 线程调用；其余线程一律经 ui_call() 投递；
  * api.log 单参、线程安全，可从任意线程直接调。

HTTP 纪律：本地回环请求用裸 socket 直连 worker（127.0.0.1，无 TLS），
不 import urllib.request/ssl/http.client——遵守 2.x 源码级禁用面（P2）。

核心目录解析（单一源码纪律）：
  1. 环境变量 STALKER_VOICE_CORE_DIR；
  2. 插件目录内的同步副本（构建期 sync_core.py 产物）；
  3. 开发态：仓库根/core（本文件位于 plugins/zh_voice/ 时成立）。
"""

import importlib
import json
import os
import socket
import subprocess
import sys
import threading
import time

PLUGIN_INFO = {
    "id": "zh_voice",
    "name": "中文语音工坊（Qwen3-TTS 本地克隆）",
    "version": "0.1.0",
    "build": "skeleton-heartbeat",
    "repo": "https://github.com/qy0214/stalker-localization-toolkit-ai-translate-2x",
    "description": "以俄/英/乌原声为样本克隆音色生成中文语音；"
                   "解包经 xrkit、产物落游戏根 MODS\\ 由 JSGME 装卸；"
                   "本骨架版仅打通 worker 进程心跳与任务总线。",
}

# 宿主插件 API 版本（2.x `kPyHostApiVersion = 1`）
PLUGIN_API = 1

APP_NAME = "zh_voice"
_UI = None
_HOST = None
_API = None

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_HANDSHAKE_TIMEOUT = 10.0   # 等 worker 握手行的上限（秒）
_POLL_INTERVAL = 1.0        # 心跳轮询周期（秒）
_MISS_LIMIT = 3             # 连续取不到 /health 的判定阈值
_PING_TIMEOUT = 6.0         # echo 自检整体上限（秒）


# --------------------------------------------------------------------------
# 核心目录与 Python 解释器解析
# --------------------------------------------------------------------------
def resolve_core_dir():
    env = os.environ.get("STALKER_VOICE_CORE_DIR")
    if env and os.path.isdir(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    synced = os.path.join(here, "stalker_voice_core")
    if os.path.isdir(synced):
        return here
    dev = os.path.abspath(os.path.join(here, "..", "..", "core"))
    if os.path.isdir(os.path.join(dev, "stalker_voice_core")):
        return dev
    raise RuntimeError(
        "找不到 stalker_voice_core：设 STALKER_VOICE_CORE_DIR，"
        "或确认同步副本/仓库结构完整")


def resolve_python():
    """找真实 python.exe。

    ★ 真机教训（B-RC.1 实测）：xrkit 是嵌入式 Python，插件进程里
    `sys.executable` 指向 **xrkit_app.exe 本身** —— 直接拿它当子进程
    会再拉起一个平台实例，stdout 里永远没有握手行（表现为「点了启动
    worker 又开一次平台 + 握手失败」）。解析顺序：
      1. 环境变量 STALKER_VOICE_PYTHON（必须指向真实文件）；
      2. sys.executable 本身就是 python*.exe（开发态/普通解释器）；
      3. 宿主 exe 旁边的捆绑解释器：python/python314/python.exe 等；
      4. PATH 上的 python / python3。
    都找不到则抛错——宁可显式失败，绝不把宿主 exe 当解释器拉起。
    """
    env = os.environ.get("STALKER_VOICE_PYTHON")
    if env and os.path.isfile(env):
        return env
    exe = sys.executable or ""
    base = os.path.basename(exe).lower()
    if base.startswith("python") and base.endswith(".exe"):
        return exe  # 普通解释器：dev 态直用
    host_dir = os.path.dirname(os.path.abspath(exe)) if exe else ""
    candidates = []
    if host_dir:
        candidates += [
            os.path.join(host_dir, "python", "python.exe"),
            os.path.join(host_dir, "python314", "python.exe"),
            os.path.join(host_dir, "python.exe"),
        ]
        import glob
        candidates += sorted(
            glob.glob(os.path.join(host_dir, "python*", "python.exe")))
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    import shutil
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found and os.path.basename(found).lower().startswith("python"):
            return found
    raise RuntimeError(
        "找不到可用的 python.exe（宿主为嵌入式 Python）："
        "请设置环境变量 STALKER_VOICE_PYTHON 指向真实解释器")


# --------------------------------------------------------------------------
# 本地回环 HTTP（裸 socket；遵守源码级禁用面 P2）
# --------------------------------------------------------------------------
def local_http(method, host, port, path, payload=None, timeout=3.0):
    """对 127.0.0.1 上的 worker 发一个 JSON 请求，返回 (status, dict|None)。"""
    body = b""
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = ("%s %s HTTP/1.1\r\nHost: %s:%d\r\n"
           "Content-Type: application/json; charset=utf-8\r\n"
           "Content-Length: %d\r\nConnection: close\r\n\r\n"
           % (method, path, host, port, len(body))).encode("ascii")
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.sendall(req + body)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    raw = b"".join(chunks)
    head, _, rest = raw.partition(b"\r\n\r\n")
    status = 0
    if head:
        first = head.split(b"\r\n", 1)[0]
        parts = first.split(b" ")
        if len(parts) >= 2:
            try:
                status = int(parts[1])
            except ValueError:
                status = 0
    data = None
    if rest:
        try:
            data = json.loads(rest.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = None
    return status, data


# --------------------------------------------------------------------------
# 页面
# --------------------------------------------------------------------------
class ZhVoiceTab(object):
    def __init__(self, api, parent):
        self.api = api
        self.ui = api.ui
        self.host = getattr(api, "host", None)

        self.proc = None          # subprocess.Popen
        self.handshake = None     # parse_handshake 结果
        self.endpoint = None      # (host, port)
        self.misses = 0
        self.stop_flag = threading.Event()
        self.poller = None
        self._queue = []          # ui_call 回退队列（老宿主无 post 时用）
        self._queue_lock = threading.Lock()
        self._use_host_post = False

        p = parent
        # 说明行：骨架版边界写进页面，避免误期待 TTS
        self.lb_title = self.ui.label(
            p, "中文语音工坊（骨架版）：本页只管理 worker 进程与心跳，"
               "TTS/解包未接入")
        self.lb_status = self.ui.label(p, "worker：未启动")
        self.lb_detail = self.ui.label(p, "—")
        self.btn_start = self.ui.button(p, "启动 worker", self.on_start)
        self.btn_stop = self.ui.button(p, "停止 worker", self.on_stop,
                                       enabled=False)
        self.btn_ping = self.ui.button(p, "心跳自检（echo 任务）",
                                       self.on_ping, enabled=False)
        self.btn_detect = self.ui.button(p, "体检（选游戏根目录）",
                                         self.on_detect)
        self.btn_scan = self.ui.button(p, "扫描语音（worker 任务）",
                                       self.on_scan)
        self.path_entry = self.ui.entry(p, "")

        # 老宿主无 ui.post 时，用 ui.timer 泵回退队列（B249+ 完全不激活）
        try:
            probe = bool(self.ui.on_ui_thread())
            del probe
            self._use_host_post = hasattr(self.ui, "post")
        except Exception:
            self._use_host_post = False
        if not self._use_host_post:
            try:
                self.ui.timer(p, 30, self._pump)
            except Exception:
                pass

    # ---- 线程纪律：ui_call（精简自 ai_translate 同名实现）---------------
    def ui_call(self, fn, *a, **k):
        if self.ui is None:
            return
        try:
            if self.ui.on_ui_thread():
                fn(*a, **k)
                return
        except Exception:
            pass
        call = lambda: fn(*a, **k)
        if self._use_host_post:
            try:
                self.ui.post(call)
                return
            except Exception:
                pass
        with self._queue_lock:
            self._queue.append(call)

    def _pump(self):
        with self._queue_lock:
            pending, self._queue = self._queue[:], []
        for call in pending:
            try:
                call()
            except Exception as exc:
                self.api.log("UI 泵回调异常: %s — %s: %s"
                             % (call, type(exc).__name__, exc))

    # ---- 状态展示 --------------------------------------------------------
    def _set_status(self, text):
        try:
            self.ui.set_text(self.lb_status, text)
        except Exception:
            pass

    def _set_detail(self, text):
        try:
            self.ui.set_text(self.lb_detail, text)
        except Exception:
            pass

    def _set_buttons(self, started):
        self.ui.set_enabled(self.btn_start, not started)
        self.ui.set_enabled(self.btn_stop, started)
        self.ui.set_enabled(self.btn_ping, started)

    # ---- 启动 / 停止 ------------------------------------------------------
    def on_start(self):
        self.ui.set_enabled(self.btn_start, False)
        threading.Thread(target=self._starter, daemon=True).start()

    def _starter(self):
        try:
            core_dir = resolve_core_dir()
            py = resolve_python()
            # 防线：绝不把宿主 exe 当解释器拉起（否则=再开一次平台）
            if not os.path.basename(py).lower().startswith("python"):
                raise RuntimeError("解析到的不是 python.exe：%s" % py)
        except Exception as exc:
            self.api.log("worker 启动失败：%s" % exc)
            self.ui_call(self._set_status, "worker：启动失败（核心目录解析）")
            self.ui_call(self._set_buttons, False)
            return
        try:
            creationflags = CREATE_NO_WINDOW if os.name == "nt" else 0
            # ★ 嵌入式 Python 带 python314._pth（sys.path 只含 stdlib zip +
            #   解释器目录，cwd 与 PYTHONPATH 都被忽略）—— `-m 包名.模块`
            #   找不到插件侧代码。统一改用 -c 显式注入核心目录，
            #   对嵌入式与普通解释器同样成立。
            boot = ("import sys; sys.path.insert(0, %r); "
                    "from stalker_voice_core.worker import main; "
                    "sys.exit(main())" % core_dir)
            self.proc = subprocess.Popen(
                [py, "-c", boot],
                cwd=core_dir, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                creationflags=creationflags)
        except Exception as exc:
            self.api.log("worker 进程创建失败：%s: %s"
                         % (type(exc).__name__, exc))
            self.ui_call(self._set_status, "worker：创建失败")
            self.ui_call(self._set_buttons, False)
            return

        from stalker_voice_core import protocol  # noqa: 延迟 import
        line = None
        deadline = time.time() + _HANDSHAKE_TIMEOUT
        while time.time() < deadline:
            raw = self.proc.stdout.readline()
            if not raw:
                if self.proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            line = raw.strip()
            break
        if not line:
            err = ""
            try:
                err = self.proc.stderr.read() or ""
            except Exception:
                pass
            self.api.log("worker 未握手（退出码 %s）%s"
                         % (self.proc.poll(), err[-300:]))
            self.ui_call(self._set_status, "worker：握手失败")
            self.ui_call(self._set_buttons, False)
            return
        try:
            self.handshake = protocol.parse_handshake(line)
        except ValueError as exc:
            self.api.log("握手解析失败：%s — %r" % (exc, line[:200]))
            self.ui_call(self._set_status, "worker：握手协议不符")
            self.ui_call(self._set_buttons, False)
            return

        self.endpoint = ("127.0.0.1", self.handshake["port"])
        self.misses = 0
        self.stop_flag.clear()
        self.poller = threading.Thread(target=self._poll_loop, daemon=True)
        self.poller.start()
        self.api.log("zh_voice worker 已接线：pid=%d port=%d v%s"
                     % (self.handshake["pid"], self.handshake["port"],
                        self.handshake["version"]))
        self.ui_call(self._set_status,
                     "worker：运行中 pid=%d port=%d"
                     % (self.handshake["pid"], self.handshake["port"]))
        self.ui_call(self._set_buttons, True)

    def _poll_loop(self):
        while not self.stop_flag.is_set():
            status, data = (None, None)
            try:
                status, data = local_http(
                    "GET", *self.endpoint, "/health",
                    timeout=_POLL_INTERVAL)
            except OSError:
                pass
            if status == 200 and data and data.get("ok"):
                self.misses = 0
                self.ui_call(self._set_detail,
                             "beat_age=%.2fs uptime=%.1fs 开放任务=%d"
                             % (data.get("beat_age_sec", -1),
                                data.get("uptime_sec", -1),
                                data.get("jobs_open", 0)))
            else:
                self.misses += 1
                if self.misses >= _MISS_LIMIT:
                    self.api.log("worker 连续 %d 次无心跳，判死"
                                 % self.misses)
                    self.ui_call(self._set_status, "worker：已失联（判死）")
                    self.ui_call(self._set_buttons, False)
                    self._reap()
                    return
            self.stop_flag.wait(_POLL_INTERVAL)

    def on_stop(self):
        self.ui.set_enabled(self.btn_stop, False)
        threading.Thread(target=self._stopper, daemon=True).start()

    def _stopper(self):
        self.stop_flag.set()
        if self.endpoint is not None:
            try:
                local_http("POST", *self.endpoint, "/shutdown", timeout=2.0)
            except OSError:
                pass
        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=3)
            except Exception:
                # 骨架期用 terminate 兜底；正式版换成 ai_translate 同款
                # Job Object 管理以覆盖孙进程（如未来的 torch 起的子进程）。
                try:
                    proc.terminate()
                except OSError:
                    pass
        self._reap()
        self.ui_call(self._set_status, "worker：已停止")
        self.ui_call(self._set_buttons, False)
        self.api.log("zh_voice worker 已停止")

    def _reap(self):
        self.endpoint = None
        self.handshake = None

    # ---- 心跳自检（echo 任务总线验证）------------------------------------
    def on_ping(self):
        if self.endpoint is None:
            return
        self.ui.set_enabled(self.btn_ping, False)
        threading.Thread(target=self._ping, daemon=True).start()

    def _ping(self):
        ep = self.endpoint
        try:
            status, data = local_http(
                "POST", *ep, "/job",
                payload={"kind": "echo",
                         "payload": {"hello": "zh_voice",
                                     "ts": time.time()}},
                timeout=2.0)
        except OSError as exc:
            self.api.log("echo 提交失败：%s" % exc)
            self.ui_call(self._set_detail, "echo：提交失败")
            self.ui_call(self.ui.set_enabled, self.btn_ping, True)
            return
        if status != 202 or not (data and data.get("ok")):
            self.api.log("echo 提交被拒：HTTP %s %s" % (status, data))
            self.ui_call(self._set_detail, "echo：提交被拒（HTTP %d）" % status)
            self.ui_call(self.ui.set_enabled, self.btn_ping, True)
            return
        job_id = data["job_id"]
        deadline = time.time() + _PING_TIMEOUT
        while time.time() < deadline:
            status, data = local_http(
                "GET", *ep, "/job/%s" % job_id, timeout=2.0)
            job = (data or {}).get("job") or {}
            if job.get("status") in ("done", "error"):
                break
            time.sleep(0.2)
        if job.get("status") == "done":
            result = job.get("result") or {}
            self.api.log("echo 完成：worker_pid=%s echo=%r"
                         % (result.get("worker_pid"), result.get("echo")))
            self.ui_call(self._set_detail,
                         "echo：OK（worker_pid=%s）" % result.get("worker_pid"))
        else:
            self.api.log("echo 未按时完成：%s" % (job.get("status"),))
            self.ui_call(self._set_detail,
                         "echo：%s" % (job.get("status") or "超时"))
        self.ui_call(self.ui.set_enabled, self.btn_ping, True)

    # ---- 体检（骨架版：直接调 core/detect，走 worker 之外的最小路径）------
    def on_detect(self):
        root = ""
        try:
            root = (self.ui.get_text(self.path_entry) or "").strip()
        except Exception:
            pass
        if not root:
            self.api.log("体检：请先在输入框填游戏根目录")
            return
        try:
            from stalker_voice_core import detect  # 延迟 import
            survey = detect.survey(root)
        except Exception as exc:
            self.api.log("体检失败：%s: %s" % (type(exc).__name__, exc))
            return
        game = survey["game"]
        self.api.log("体检：%s | evidence=%s"
                     % (game["label"], "; ".join(game["evidence"]) or "-"))
        self.api.log("体检：$game_data$ 开关=%s | JSGME exe=%s MODS=%s "
                     "| 存档 %d 个"
                     % (survey["game_data_on"],
                        survey["jsgme"]["jsgme_exe"],
                        survey["jsgme"]["mods_dir"],
                        len(survey["archives"])))
        self.ui_call(self._set_detail,
                     "体检：%s（$game_data$=%s）"
                     % (game["kind"], survey["game_data_on"]))

    # ---- 扫描语音（M2：经 worker 任务总线走 scan）-------------------------
    def on_scan(self):
        if self.endpoint is None:
            self.api.log("扫描：worker 未启动，先点「启动 worker」")
            return
        root = ""
        try:
            root = (self.ui.get_text(self.path_entry) or "").strip()
        except Exception:
            pass
        if not root:
            self.api.log("扫描：请先在输入框填游戏根目录")
            return
        self.ui.set_enabled(self.btn_scan, False)
        threading.Thread(target=self._scan, args=(root,),
                         daemon=True).start()

    def _scan(self, game_root):
        ep = self.endpoint
        try:
            status, data = local_http(
                "POST", *ep, "/job",
                payload={"kind": "scan", "payload": {"game_root": game_root}},
                timeout=2.0)
        except OSError as exc:
            self.api.log("扫描提交失败：%s" % exc)
            self.ui_call(self.ui.set_enabled, self.btn_scan, True)
            return
        if status != 202 or not (data and data.get("ok")):
            self.api.log("扫描提交被拒：HTTP %s %s" % (status, data))
            self.ui_call(self.ui.set_enabled, self.btn_scan, True)
            return
        job_id = data["job_id"]
        deadline = time.time() + 60.0
        job = {}
        while time.time() < deadline:
            try:
                status, data = local_http(
                    "GET", *ep, "/job/%s" % job_id, timeout=2.0)
            except OSError:
                status, data = 0, None
            job = (data or {}).get("job") or {}
            if job.get("status") in ("done", "error"):
                break
            time.sleep(0.3)
        if job.get("status") == "done":
            result = job.get("result") or {}
            speech = sum(s.get("speech_total", 0)
                         for s in result.get("extracted_sources", []))
            loose = sum(s.get("speech_total", 0)
                        for s in result.get("loose_gamedata", []))
            pending = len(result.get("archives_pending", []))
            eng = result.get("engine") or {}
            self.api.log("扫描完成：VOICE_SRC 语音 %d 条 | 散文件 gamedata "
                         "语音 %d 条 | 待解包存档 %d 个 | 引擎 ok=%s"
                         % (speech, loose, pending, eng.get("ok")))
            self.ui_call(self._set_detail,
                         "扫描：VOICE_SRC=%d 散文件=%d 待解=%d"
                         % (speech, loose, pending))
        else:
            self.api.log("扫描未完成：%s — %s"
                         % (job.get("status"), job.get("error")))
            self.ui_call(self._set_detail,
                         "扫描：%s" % (job.get("status") or "超时"))
        self.ui_call(self.ui.set_enabled, self.btn_scan, True)


# --------------------------------------------------------------------------
# 宿主入口
# --------------------------------------------------------------------------
def register(api):
    """2.x 入口：注入 api.ui 后注册「中文语音」工具页。

    控件照官方插件惯例传 parent=None，宿主 resolve_parent() 回落到
    当前面板根（与 ai_translate 相同，避免 builder 多参 TypeError）。
    """
    global _UI, _HOST, _API
    _UI = api.ui
    _HOST = getattr(api, "host", None)
    _API = api
    try:
        # 部署态：插件目录自带 stalker_voice_core/ 同步副本（sync_core.py
        # 产物），必须把插件目录挂上 sys.path，后续线程里的 import 才成立。
        _core_dir = resolve_core_dir()
        if _core_dir not in sys.path:
            sys.path.insert(0, _core_dir)
        core_note = ""
    except RuntimeError as exc:
        core_note = "（警告：%s）" % exc
    ok = api.register_tool(
        "zh_voice", lambda: ZhVoiceTab(api, None),
        order=7, subtitle="Qwen3-TTS 俄语原声克隆中文语音",
        title="中文语音")
    api.log("中文语音插件 %s 已注册%s（宿主 UI 门面%s）"
            % (PLUGIN_INFO.get("version", "?"), core_note,
               "" if ok else "，注册被宿主拒绝"))
    return ok
