# -*- coding: utf-8 -*-
"""2.x 宿主的最小严格假件（只用于测试，不是产品代码）。

为什么要"严格"：真宿主有几个容易踩空的硬约束，简化版控件替身全都
不满足，测试会假绿：
  · `PyWidget` **只有** `__repr__` / `__bool__` —— 没有 `.widget`、没有
    `.configure()`、没有 `.get_text()`、没有 `.alive()`；插件要是还照
    旧式写法调控件，测试必须当场报错，而不是悄悄通过；
  · `api.ui.*` 有**线程闸门**：非 UI 线程调用返回 `None` 并记 Warn，**不抛异常**
    —— 假件照此实现（`FakeUi.warns`），这样"worker 直接调控件"会被断言抓到；
  · `api.log(msg)` **只有一个参数**（没有 tag、没有 log_detail）—— 假件用
    单参签名，多传一个参数就 TypeError；
  · `combo_set_items` **固定把第 0 项设为当前项**，没有"选中第 N 项"的入口；
  · `set_text` 不支持 QComboBox（这里直接拒绝，暴露误用）。

用法：
    from fake_host import FakeApi, build_page
    api = FakeApi()
    ok = api.install(plugin_module)      # 调 register(api)
    tab = api.build()                    # 调 builder()（真宿主零参），返回页面对象
"""

import inspect
import threading


class Widget(object):
    """2.x `PyWidget` 的等价物：只有身份，没有任何控件方法。"""

    __slots__ = ("kind", "value", "items", "index", "checked", "enabled",
                 "alive_flag", "callback", "placeholder", "logs", "visible")

    def __init__(self, kind, **kw):
        self.kind = kind
        self.value = kw.get("value", "")
        self.items = list(kw.get("items") or [])
        self.index = 0
        self.checked = bool(kw.get("checked", False))
        self.enabled = bool(kw.get("enabled", True))
        self.alive_flag = True
        self.callback = kw.get("callback")
        self.placeholder = kw.get("placeholder", "")
        self.logs = []
        self.visible = True

    # 宿主只暴露这两个魔法方法
    def __repr__(self):
        return "xrkit.Widget(valid)" if self.alive_flag else "xrkit.Widget(null)"

    def __bool__(self):
        return self.alive_flag


class FakeUi(object):
    def __init__(self):
        self.ui_thread = threading.current_thread()
        self.warns = []
        self.created = []
        self.dialogs = []          # (kind, title, extra)
        self.dialog_reply = None   # 供 ask_* 返回
        self.timers = []           # (parent, ms, callback)
        self.tab_titles = []       # tabs_add_page 依次登记的页签标题

    # ── 线程闸门 ──────────────────────────────────────────────
    def on_ui_thread(self):
        return threading.current_thread() is self.ui_thread

    def _gate(self, what):
        if not self.on_ui_thread():
            self.warns.append("非 UI 线程调用 ui.%s" % what)
            return False
        return True

    def assert_no_warns(self):
        assert not self.warns, "控件被非 UI 线程访问：%s" % self.warns

    # ── 控件创建 ──────────────────────────────────────────────
    def _new(self, widget):
        if widget is None:
            return None
        self.created.append(widget)
        return widget

    def label(self, parent=None, text="", style=""):
        if not self._gate("label"):
            return None
        return self._new(Widget("label", value=text))

    def button(self, parent=None, text="", on_click=None, width=0, style="",
               enabled=True):
        if not self._gate("button"):
            return None
        return self._new(Widget("button", value=text, callback=on_click,
                                enabled=enabled))

    def checkbox(self, parent=None, text="", on_toggle=None):
        if not self._gate("checkbox"):
            return None
        return self._new(Widget("checkbox", value=text, callback=on_toggle))

    def entry(self, parent=None, text=""):
        if not self._gate("entry"):
            return None
        return self._new(Widget("entry", value=text))

    def text(self, parent=None, height=0, width=0, wrap="word"):
        if not self._gate("text"):
            return None
        return self._new(Widget("text"))

    def combo(self, parent=None, values=None, on_change=None):
        if not self._gate("combo"):
            return None
        widget = Widget("combo", items=values or [], callback=on_change)
        return self._new(widget)

    def drop_box(self, parent=None, on_file=None, placeholder="", width=0):
        if not self._gate("drop_box"):
            return None
        return self._new(Widget("drop_box", callback=on_file,
                                placeholder=placeholder))

    def progress(self, parent=None):
        if not self._gate("progress"):
            return None
        return self._new(Widget("progress", value=0))

    def log_view(self, parent=None, height=0):
        if not self._gate("log_view"):
            return None
        return self._new(Widget("log_view"))

    def timer(self, parent, interval_ms, callback):
        if not self._gate("timer"):
            return None
        self.timers.append((parent, interval_ms, callback))
        return self._new(Widget("timer"))

    def tabs(self, parent=None):
        if not self._gate("tabs"):
            return None
        return self._new(Widget("tabs"))

    def tabs_add_page(self, tabs, title):
        if not self._gate("tabs_add_page"):
            return None
        self.tab_titles.append(str(title))
        return self._new(Widget("tab_page", value=str(title)))

    def tabs_page_count(self, tabs):
        if not self._gate("tabs_page_count"):
            return 0
        return len(self.tab_titles)

    def tabs_current_index(self, tabs):
        if not self._gate("tabs_current_index"):
            return 0
        return 0

    def tabs_set_current(self, tabs, index):
        if not self._gate("tabs_set_current"):
            return None
        return None

    def alive(self, widget):
        if not self._gate("alive"):
            return False
        return bool(widget) and getattr(widget, "alive_flag", False)

    # ── 控件读写（严格照 2.x 的可写面） ───────────────────────
    def set_text(self, widget, text):
        if not self._gate("set_text"):
            return None
        if widget is None:
            return None
        if widget.kind == "combo":
            # 2.x 的 set_text **不支持 QComboBox**：这里直接拒绝
            raise TypeError("set_text 不支持下拉框")
        widget.value = str(text)
        return None

    def get_text(self, widget):
        if not self._gate("get_text"):
            return ""
        if widget is None:
            return ""
        return str(widget.value or "")

    def combo_value(self, widget):
        if not self._gate("combo_value"):
            return ""
        if widget is None or widget.kind != "combo" or not widget.items:
            return ""
        return str(widget.items[widget.index])

    def combo_set_items(self, widget, values):
        if not self._gate("combo_set_items"):
            return None
        if widget is None:
            return None
        widget.items = [str(v) for v in (values or [])]
        widget.index = 0            # 宿主固定第 0 项为当前项
        return None

    def checkbox_state(self, widget):
        if not self._gate("checkbox_state"):
            return False
        return bool(widget and widget.checked)

    def set_checked(self, widget, checked):
        if not self._gate("set_checked"):
            return None
        if widget is not None:
            widget.checked = bool(checked)
        return None

    def set_enabled(self, widget, enabled):
        if not self._gate("set_enabled"):
            return None
        if widget is not None:
            widget.enabled = bool(enabled)
        return None

    def set_visible(self, widget, visible):
        if not self._gate("set_visible"):
            return None
        if widget is not None:
            widget.visible = bool(visible)
        return None

    def destroy(self, widget):
        if not self._gate("destroy"):
            return None
        if widget is not None:
            widget.alive_flag = False
            widget.visible = False
        return None

    def set_progress(self, widget, percent):
        if not self._gate("set_progress"):
            return None
        if widget is not None:
            widget.value = max(0, min(100, int(percent)))
        return None

    def progress_value(self, widget):
        if not self._gate("progress_value"):
            return 0
        return int(getattr(widget, "value", 0) or 0)

    def log_view_append(self, view, line):
        if not self._gate("log_view_append"):
            return None
        if view is not None:
            view.logs.append(str(line))
        return None

    def log_view_clear(self, view):
        if not self._gate("log_view_clear"):
            return None
        if view is not None:
            view.logs[:] = []
        return None

    def log_view_text(self, view):
        if not self._gate("log_view_text"):
            return ""
        return "\n".join(getattr(view, "logs", []))

    # ── 对话框 ────────────────────────────────────────────────
    def ask_yes_no(self, title, message):
        if not self._gate("ask_yes_no"):
            return False
        self.dialogs.append(("yes_no", title, message))
        return bool(self.dialog_reply)

    def ask_open_file(self, title, filetypes=None, initialdir=""):
        if not self._gate("ask_open_file"):
            return ""
        self.dialogs.append(("open", title, filetypes))
        return self.dialog_reply or ""

    def ask_save_file(self, title, filetypes=None, initialdir="",
                      initialfile=""):
        if not self._gate("ask_save_file"):
            return ""
        self.dialogs.append(("save", title, filetypes))
        return self.dialog_reply or ""

    def ask_directory(self, title):
        if not self._gate("ask_directory"):
            return ""
        self.dialogs.append(("dir", title, None))
        return self.dialog_reply or ""

    def copy_text(self, text):
        if not self._gate("copy_text"):
            return None
        return None


class FakeApi(object):
    """2.x `api`：`ui` / `log(msg)` / `register_tool(...)`。"""

    def __init__(self):
        self.ui = FakeUi()
        self.logs = []
        self.tools = []
        self.module = None

    def log(self, message):        # ★ 单参：多传 tag 会 TypeError
        self.logs.append(str(message))

    def register_tool(self, name, builder, order=0, replaces="", subtitle="",
                      title=""):
        assert callable(builder), "builder 必须可调用"
        # 严格对齐真宿主：wrap_python_builder 里是 `kept()`，不传 parent。
        required = [p for p in inspect.signature(builder).parameters.values()
                    if p.default is inspect.Parameter.empty
                    and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        assert not required, "builder 必须能零参调用（真宿主不传 parent）"
        self.tools.append({"name": name, "builder": builder, "order": order,
                           "replaces": replaces, "subtitle": subtitle,
                           "title": title})
        return True

    # ── 测试入口 ──────────────────────────────────────────────
    def install(self, module):
        self.module = module
        return module.register(self)

    def build(self, index=0):
        # 真宿主：UiPanelScope(parent) + builder() 零参；parent 由门面隐式解析。
        return self.tools[index]["builder"]()

    def pump(self, times=1):
        """手动驱动宿主 QTimer（假件不做真计时）。"""
        for _parent, _ms, callback in list(self.ui.timers):
            for _ in range(times):
                callback()
