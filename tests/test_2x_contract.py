# -*- coding: utf-8 -*-
"""ai_translate 2.0.0 的宿主契约测试（严格假件）。

跑法：
    python tests/test_2x_contract.py

覆盖 P1–P8：
  P1 注册与页面构建（2.x register_tool 契约 / 页面只纵向声明控件）
  P2 源码级禁用面（urllib.request/ssl/http.client/tkinter/row/pack/set_state…）
  P3 线程纪律（worker 只入队，UI 线程泵执行；控件非 UI 线程不可写）
  P4 下拉缓存（combo 无 set_index ⇒ 轮转绕行仍然取得到值）
  P5 _persist_cfg 只从缓存读值并落盘
  P6 按钮/进度状态机（在不下网络的前提下）
  P7 对话框参数形状（ask_open_file 收两元组列表、无 parent）
  P8 日志（api.log 单参 + log_view 一次一行）
  P9 真 WinHTTP 本地回环（POST 必须带 dwTotalLength，GET 不能回归）
"""

import importlib.util
import inspect
import os
import sys
import tempfile
import threading

# 直接原地加载仓库里的 plugin.py：不要留 __pycache__（发布门禁会看到它）。
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "plugins", "ai_translate", "plugin.py")

# 测试隔离：插件默认把运行数据写 %LOCALAPPDATA%，这里指向临时目录。
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ait_localappdata_")

sys.path.insert(0, HERE)
from fake_host import FakeApi          # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + detail) if detail else ""))
    return ok


def load_module():
    spec = importlib.util.spec_from_file_location("ait2x", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_tab(module):
    """装进假宿主并构建页面；硬件检测换成固定值，避免测试依赖本机显卡。"""
    module.detect_hardware = lambda: {
        "variant": "cpu", "tier": "mt2", "gpu_name": "", "vram": 0,
        "short": "测试：CPU + 精准1.8B", "driver": "", "compute_cap": 0,
        "cuda13_ok": False}
    module._kill_orphan_servers = lambda cfg=None: []
    api = FakeApi()
    ok = api.install(module)
    tab = api.build()
    # 让 __init__ 里触发的后台线程（硬件检测/孤儿清理）跑完并回主线程执行
    for worker in threading.enumerate():
        if worker is not threading.current_thread():
            worker.join(timeout=10)
    api.pump()
    return api, tab, ok


# ── P1 注册与构建 ───────────────────────────────────────────
def t_p1(module, api, tab, ok):
    check("P1-1 register 返回 True", ok)
    check("P1-2 只注册一个工具页", len(api.tools) == 1,
          "tools=%d" % len(api.tools))
    tool = api.tools[0] if api.tools else {}
    check("P1-3 id 为 ASCII 且非空",
          bool(tool.get("name")) and tool["name"].isascii())
    check("P1-4 显示名走 title", tool.get("title") == "AI 翻译",
          repr(tool.get("title")))
    check("P1-5 builder 产出 AiTranslateTab",
          isinstance(tab, module.AiTranslateTab))
    check("P1-6 注册了日志视图", getattr(tab, "log_view", None) is not None)
    check("P1-7 注册了进度条", getattr(tab, "progress", None) is not None)
    check("P1-8 起了宿主定时器泵",
          any(ms == 30 for _p, ms, _c in api.ui.timers),
          str([ms for _p, ms, _c in api.ui.timers]))
    builder = tool.get("builder")
    required = [p for p in inspect.signature(builder).parameters.values()
                if p.default is inspect.Parameter.empty
                and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)] \
        if callable(builder) else ["<not callable>"]
    check("P1-10 builder 零参可调用（真宿主不传 parent）", not required,
          str(required))
    # 页面不要自带 viewport / 分割容器（宿主 PageBase 已经包好了）
    src = open(PLUGIN, encoding="utf-8").read()
    for name in ("viewport(", "split(", "tabs("):
        check("P1-9 页面不使用 ui.%s" % name.rstrip("("), "ui." + name not in src)


# ── P2 源码级禁用面（AST：只认真正的代码，不误伤注释/文档） ──
def t_p2(module):
    import ast
    tree = ast.parse(open(PLUGIN, encoding="utf-8").read())

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")

    def imported(prefix):
        return [m for m in imports if m == prefix or m.startswith(prefix + ".")]

    for prefix, why in (
            ("ssl", "2.x 不随 _ssl.pyd"),
            ("http", "http.client 会拖进 ssl"),
            ("urllib.request", "导入链会拖进 ssl"),
            ("urllib.error", "同上"),
            ("tkinter", "2.x 无 Tkinter")):
        hits = imported(prefix)
        check("P2 禁用导入：%s（%s）" % (prefix, why), not hits, str(hits))

    bad_attrs = {"set_state", "log_detail", "widget", "_tk",
                 "row", "pack", "viewport", "split", "tabs"}
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr not in bad_attrs:
            continue
        # `ui.*` / `self.ui.*` 的门面调用，或非门面的控件方法
        base = node.value
        if node.attr in ("row", "pack", "viewport", "split", "tabs"):
            if isinstance(base, ast.Attribute) and base.attr == "ui":
                hits.append("ui.%s" % node.attr)
            elif isinstance(base, ast.Name) and base.id == "ui":
                hits.append("ui.%s" % node.attr)
        else:
            hits.append(node.attr)
    check("P2 禁用属性/门面：%s" % ", ".join(sorted(set(hits))), not hits,
          ", ".join(sorted(set(hits))))
    src = open(PLUGIN, encoding="utf-8").read()
    check("P2 ctypes 内嵌 WinHTTP 存在", "WinHttpOpen" in src)


# ── P3 线程纪律 ─────────────────────────────────────────────
def t_p3(module, api, tab):
    ui = api.ui
    out = []

    def worker():
        # 工作线程调 ui.* 必须被闸门拦住（返回 None + 记 Warn，不抛）
        got = ui.set_text(tab.hw_label, "从 worker 写")
        out.append(("direct", got))
        # 正确做法：走 ui_call ⇒ 只入队，不动控件
        tab.ui_call(tab.log, "来自工作线程的一行")

    api.ui.warns[:] = []
    th = threading.Thread(target=worker)
    th.start()
    th.join(10)
    check("P3-1 worker 直接写控件被闸门拦住",
          len(api.ui.warns) == 1, str(api.ui.warns))
    check("P3-2 被拦的 set_text 返回 None", out and out[0][1] is None)
    before = len(tab.log_view.logs)
    check("P3-3 ui_call 在 worker 里只入队", tab._q.qsize() >= 1,
          "qsize=%d" % tab._q.qsize())
    check("P3-4 入队期间界面日志没被写", len(tab.log_view.logs) == before)
    api.pump(3)
    check("P3-5 泵之后日志落到 log_view",
          any("来自工作线程的一行" in line for line in tab.log_view.logs),
          str(tab.log_view.logs[-1:]))
    check("P3-6 泵把队列抽干", tab._q.qsize() == 0,
          "qsize=%d" % tab._q.qsize())
    # 上面那一条 Warn 是**故意制造**的（验证闸门生效），其后不许再有
    api.ui.warns[:] = []
    tab.ui_call(tab.log, "再次确认：UI 线程直接执行")
    ui.assert_no_warns()
    check("P3-7 UI 线程调用同步执行（不入队）", tab._q.qsize() == 0)


# ── P4 下拉缓存（无 set_index 的绕行） ─────────────────────
def t_p4(module, api, tab):
    labels = [module.TIER_SHORT[t] for t in module.TIER_ORDER]
    target = module.TIER_ORDER.index("7b")
    module._combo_set(tab.tier_combo, labels, target)
    got = module._UI.combo_value(tab.tier_combo)
    check("P4-1 _combo_set 后取值等于目标项", got == labels[target],
          "%r != %r" % (got, labels[target]))
    tab._on_tier(got)
    check("P4-2 回调值写进 sel 缓存", tab.sel["tier"] == "7b",
          tab.sel["tier"])
    check("P4-3 current_tier 读缓存", tab.current_tier() == "7b")

    vlabels = [module.VARIANT_LABELS[v] for v in module.VARIANT_ORDER]
    vi = module.VARIANT_ORDER.index("vulkan")
    module._combo_set(tab.variant_combo, vlabels, vi)
    tab._on_variant(module._UI.combo_value(tab.variant_combo))
    check("P4-4 后端下拉映射正确", tab.current_variant() == "vulkan",
          tab.current_variant())

    module._combo_set(tab.ctx_combo, ["4096", "8192", "16384", "32768"], 2)
    tab._on_custom_ctx(module._UI.combo_value(tab.ctx_combo))
    check("P4-5 自选上下文写回", tab.sel["ctx"] == "16384" and
          tab.cfg.get("custom_ctx") == 16384, str(tab.sel.get("ctx")))


# ── P5 _persist_cfg 只读缓存并落盘 ─────────────────────────
def t_p5(module, api, tab):
    module._UI.set_text(tab.in_box, r"D:\gamedata")
    module._UI.set_text(tab.out_box, r"D:\out")
    module._UI.set_checked(tab.ref_chk, False)
    module._UI.set_checked(tab.tm_chk, False)
    module._UI.set_text(tab.extra_prompt, "测试提示词")
    module._UI.combo_set_items(tab.mode_combo,
                               [module.MODE_LABELS[m] for m in module.MODE_ORDER])
    tab._persist_cfg()
    check("P5-1 输入目录落 cfg", tab.cfg["in_dir"] == r"D:\gamedata",
          tab.cfg.get("in_dir"))
    check("P5-2 勾选状态进 flags", tab.flags["reference_layer"] is False and
          tab.flags["tm_lite"] is False, str(tab.flags))
    check("P5-3 勾选状态同步进 cfg", tab.cfg["reference_layer"] is False)
    check("P5-4 多行提示词落 cfg", tab.cfg["prompt_extra"] == "测试提示词")
    check("P5-5 工作线程可见的缓存已更新",
          tab._prompt_extra_text == "测试提示词")
    ui = api.ui
    check("P5-6 全程没有跨线程访问", not ui.warns, str(ui.warns))


# ── P6 按钮/进度状态机 ─────────────────────────────────────
def t_p6(module, api, tab):
    tab._engine_ui(True)
    check("P6-1 引擎运行时禁用「启动」", tab.btn_start.enabled is False)
    check("P6-2 引擎运行时启用「停止」", tab.btn_stop.enabled is True)
    tab._engine_ui(False)
    check("P6-3 引擎停止后恢复", tab.btn_start.enabled is True and
          tab.btn_stop.enabled is False)

    tab._set_progress(37)
    check("P6-4 进度条写到 37", api.ui.progress_value(tab.progress) == 37,
          str(api.ui.progress_value(tab.progress)))
    tab._typeof = None

    tab.tr_cancel = object()
    tab._run_done()
    check("P6-5 收尾清掉取消标记", tab.tr_cancel is None)
    check("P6-6 收尾恢复按钮", tab.btn_run.enabled is True and
          tab.btn_run_cancel.enabled is False)
    check("P6-7 收尾把进度归零", api.ui.progress_value(tab.progress) == 0)


# ── P7 对话框参数形状 ──────────────────────────────────────
def t_p7(module, api, tab):
    api.ui.dialog_reply = ""
    tab.on_gloss_import()
    last = api.ui.dialogs[-1] if api.ui.dialogs else None
    check("P7-1 走宿主 ask_open_file", bool(last) and last[0] == "open",
          str(last))
    check("P7-2 filetypes 是两元组列表", bool(last) and
          isinstance(last[2], list) and all(
              isinstance(x, tuple) and len(x) == 2 for x in last[2]),
          str(last[2] if last else None))
    api.ui.dialog_reply = ""
    tab.on_choose_in()
    last = api.ui.dialogs[-1] if api.ui.dialogs else None
    check("P7-3 目录选择走 ask_directory", bool(last) and last[0] == "dir",
          str(last))
    check("P7-4 目录选择只传 title", bool(last) and last[2] is None)
    check("P7-5 没有残留 tkinter 分支", "tkinter" not in open(
        PLUGIN, encoding="utf-8").read())


# ── P8 日志 ────────────────────────────────────────────────
def t_p8(module, api, tab):
    before_host = len(api.logs)
    before_view = len(tab.log_view.logs)
    tab.log("契约测试一行")
    check("P8-1 api.log 单参调用成功", len(api.logs) == before_host + 1,
          str(api.logs[-1:]))
    check("P8-2 日志进界面视图", len(tab.log_view.logs) == before_view + 1,
          str(tab.log_view.logs[-1:]))
    check("P8-3 行首有时间戳", "契约测试一行" in tab.log_view.logs[-1] and
          tab.log_view.logs[-1][2] == ":")
    check("P8-4 内存环形缓冲有上限", len(tab.log_lines) <= 400)


# ── P9 真 WinHTTP 本地回环 ────────────────────────────────
def t_p9(module):
    """POST 曾经把 dwTotalLength 传成 0，WinHTTP 直接 ERROR_INVALID_PARAMETER(87)。"""
    if os.name != "nt":
        check("P9-1 WinHTTP POST 本地回环", True, "非 Windows 跳过")
        return
    import http.server
    import socketserver

    class Echo(http.server.BaseHTTPRequestHandler):
        def _reply(self, payload):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self._reply(b'{"get":true}')

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self._reply(self.rfile.read(length))

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Echo)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    try:
        response = module._http_post(
            url, b'{"ping":1}', {"Content-Type": "application/json"},
            timeout=5)
        body = response.read()
        response.close()
        check("P9-1 WinHTTP POST 本地回环", body == b'{"ping":1}',
              repr(body[:80]))
        response = module._http_get(url, timeout=5)
        body = response.read()
        response.close()
        check("P9-2 WinHTTP GET 本地回环", body == b'{"get":true}',
              repr(body[:80]))
    except Exception as exc:
        check("P9-1 WinHTTP POST 本地回环", False,
              "%s: %s" % (type(exc).__name__, exc))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def main():
    module = load_module()
    api, tab, ok = make_tab(module)
    t_p1(module, api, tab, ok)
    t_p2(module)
    t_p3(module, api, tab)
    t_p4(module, api, tab)
    t_p5(module, api, tab)
    t_p6(module, api, tab)
    t_p7(module, api, tab)
    t_p8(module, api, tab)
    t_p9(module)

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    failed = [(n, d) for n, ok, d in RESULTS if not ok]
    print("\n%d/%d 通过" % (passed, len(RESULTS)))
    if failed:
        print("失败：")
        for name, detail in failed:
            print("  - %s  %s" % (name, detail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
