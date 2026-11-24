# -*- coding: utf-8 -*-
"""页签显示完整性 / 紧凑布局 回归（2.0.4）。

真机（平台作者 BETA.249，Qt 6.8.3）两阶段的坑：
  1) 页签内套 viewport + viewport_set_min_body_height(80) ⇒ 每页只露一行；
  2) 去掉 viewport 直接 pack 到 page ⇒ 内容全，但 page 的 QVBoxLayout 把
     少量 Preferred 控件均分到整页 ⇒ 控件之间巨大空白。
最终修法（与平台内置 fs_page 同构）：直接 pack 到 page + 每页末尾放一个
Expanding 弹性占位（QSplitter 格）吃掉剩余高度，控件顶到顶部紧凑排列。

跑法：python tests/test_ui_fallback.py
"""

import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "plugins", "ai_translate", "plugin.py")

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ait_ui_fb_")
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)

from fake_host import FakeApi, FakeUi  # noqa: E402


class B249Ui(FakeUi):
    """模拟 B249+：有原生 ui.post，无 QTimer 泵。"""

    def __init__(self):
        super().__init__()
        self.pending_posts = []

    def post(self, fn, *args):
        self.post_calls = getattr(self, "post_calls", 0) + 1
        self.pending_posts.append((fn, args))
        return True

    def drain(self):
        pending, self.pending_posts = self.pending_posts, []
        for fn, args in pending:
            fn(*args)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + detail) if detail else ""))


spec = importlib.util.spec_from_file_location("ait_ui_fb", PLUGIN)
ait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ait)

STEPS = ("hw", "runtime", "model", "engine", "job", "glossary")


def build_and_capture(viewport_mode="ok"):
    api = FakeApi()
    orig = FakeUi.viewport
    if viewport_mode == "none":
        FakeUi.viewport = lambda self, parent=None: None
    elif viewport_mode == "raise":
        def _raise(self, parent=None):
            raise RuntimeError("no viewport facade")
        FakeUi.viewport = _raise
    seen = []
    originals = {}
    try:
        for key in STEPS:
            fn_name = "_build_step_" + key
            originals[key] = getattr(ait.AiTranslateTab, fn_name)

            def wrapper(self, parent, _key=key, _fn=originals[key]):
                seen.append((_key, parent))
                return _fn(self, parent)

            setattr(ait.AiTranslateTab, fn_name, wrapper)
        ait.register(api)
        api.build()
        return dict(seen), list(api.ui.tab_titles), api.ui
    finally:
        for key, fn in originals.items():
            setattr(ait.AiTranslateTab, "_build_step_" + key, fn)
        FakeUi.viewport = orig


def t_direct_pack():
    print("\n--- T1 直接 pack 到 page（六页内容完整的前提）---")
    parents, titles, ui = build_and_capture("ok")
    check("T1-1 六页都构建", len(parents) == 6)
    check("T1-2 parent 全非空", all(p is not None for p in parents.values()))
    check("T1-3 六个 parent 互不相同",
          len(set(id(p) for p in parents.values())) == 6)
    check("T1-4 页签标题 6 个", len(titles) == 6)
    check("T1-5 ★ 默认不建 viewport（避免只露一行）",
          not [w for w in ui.created if w.kind == "viewport"],
          "viewport 数=%d" % len([w for w in ui.created if w.kind == "viewport"]))


def t_spring():
    print("\n--- T2 每页末尾弹性占位（消除均分空白）---")
    _parents, _titles, ui = build_and_capture("ok")
    splits = [w for w in ui.created if w.kind == "split"]
    panes = [w for w in ui.created if w.kind == "split_pane"]
    check("T2-1 ★ 六个页各一个弹性 split", len(splits) == 6,
          "split 数=%d" % len(splits))
    check("T2-2 ★ 每个 split 至少一个 pane", len(panes) >= 6,
          "pane 数=%d" % len(panes))


def t_min_height():
    print("\n--- T3 完全不压最小高度（两个坑的共同根源）---")
    calls = []
    orig = FakeUi.viewport_set_min_body_height

    def spy(self, body, min_height):
        calls.append((body, min_height))
        return orig(self, body, min_height)

    FakeUi.viewport_set_min_body_height = spy
    try:
        build_and_capture("ok")
        check("T3-1 ★ 不调 viewport_set_min_body_height", not calls,
              "调用 %d 次" % len(calls))
    finally:
        FakeUi.viewport_set_min_body_height = orig


def t_host_variants():
    print("\n--- T4 宿主 viewport 门面缺失/抛异常仍正常 ---")
    for mode, tag in (("none", "返回None"), ("raise", "抛异常")):
        parents, titles, _ui = build_and_capture(mode)
        check("T4-%s 六页都构建" % tag, len(parents) == 6)
        check("T4-%s parent 全非空且各异" % tag,
              all(p is not None for p in parents.values())
              and len(set(id(p) for p in parents.values())) == 6)
        check("T4-%s 页签标题仍在" % tag, len(titles) == 6)


def t_host_post():
    print("\n--- T5 B249+ 宿主原生 ui.post 线程调度 ---")
    api = FakeApi()
    api.ui = B249Ui()
    ait.register(api)
    tab = api.build()
    check("T5-1 检测到 post 门面后不注册 QTimer 泵",
          getattr(tab, "_use_host_post", False) is True
          and not api.ui.timers,
          "timers=%d" % len(api.ui.timers))
    # 工作线程 ui_call → 应直接 post 回调，绝不碰自建队列
    called = []
    queued = []
    orig_put = tab._q.put

    def spy_put(item):
        queued.append(item)
        return orig_put(item)

    tab._q.put = spy_put
    import threading

    def worker():
        tab.ui_call(lambda: called.append("ui"))

    th = threading.Thread(target=worker)
    th.start()
    th.join(timeout=5)
    check("T5-2 ui_call 只经 post 投递且工作线程不执行",
          called == [] and len(api.ui.pending_posts) == 1,
          "called=%s pending=%d" % (called, len(api.ui.pending_posts)))
    api.ui.drain()
    check("T5-2b 宿主回到 UI 线程后执行投递回调", called == ["ui"],
          "called=%s" % called)
    check("T5-3 post 至少调用一次",
          getattr(api.ui, "post_calls", 0) >= 1,
          "post_calls=%d" % getattr(api.ui, "post_calls", 0))
    check("T5-4 ★ 自建队列 put 从未调用（跟随宿主、无二次实现）",
          not queued, "put 调用 %d 次" % len(queued))


def t_host_http():
    print("\n--- T6 B249+ 宿主 host.http_* 门面（跟随宿主，小请求不自建）---")
    # 模拟宿主 host 门面
    class FakeHostHttp:
        def __init__(self):
            self.calls = []

        def http_get(self, url, timeout_sec=20, max_bytes=1 << 20):
            self.calls.append(("GET", url))
            return {"ok": True, "status": 200, "body": '{"ok":true}',
                    "error": "", "bytes": 10, "truncated": False}

        def http_post(self, url, body, content_type="application/json",
                      timeout_sec=20, max_bytes=1 << 20):
            self.calls.append(("POST", url, body))
            return {"ok": True, "status": 200, "body": '{"choices":[]}',
                    "error": "", "bytes": 14, "truncated": False}

    old = ait._HOST
    try:
        host = FakeHostHttp()
        ait._HOST = host
        r = ait._http_get("http://127.0.0.1:9999/health", timeout=5)
        check("T6-1 GET 走宿主门面", ("GET", "http://127.0.0.1:9999/health")
              in host.calls and r.status == 200 and r.read() == b'{"ok":true}')
        r2 = ait._http_post("http://127.0.0.1:9999/v1/chat/completions",
                            b'{"q":1}', headers={"Content-Type": "application/json"})
        check("T6-2 POST 走宿主门面",
              any(c[0] == "POST" and c[2] == '{"q":1}' for c in host.calls)
              and r2.status == 200)
        # 流式下载必须绕过宿主门面
        before = len(host.calls)
        try:
            ait._http_request("GET", "http://127.0.0.1:9999/big",
                              headers={"Range": "bytes=0-99"}, timeout=2)
        except Exception:
            pass
        check("T6-3 流式下载绕过宿主门面（宿主只适合小文本）",
              len(host.calls) == before)
    finally:
        ait._HOST = old


def t_host_download():
    print("\n--- T7 B250+ 宿主 host.http_download（中小产物跟随宿主）---")
    import threading as _th

    class FakeHostDl:
        def __init__(self, behavior):
            self.calls = []
            self.behavior = behavior   # fn(url, path, timeout_sec, max_bytes) -> dict

        def http_download(self, url, path, timeout_sec=300, max_bytes=8 << 30):
            self.calls.append((url, path, timeout_sec, max_bytes))
            return self.behavior(url, path, timeout_sec, max_bytes)

    def run(behavior, target, total_hint, **kw):
        host = FakeHostDl(behavior)
        old_h, old_r = ait._HOST, ait._http_request
        ait._HOST = host
        try:
            return host, ait.download_one(
                [("m", "http://m/good.bin")], target,
                log=lambda m, tag="info": None,
                verify=None, total_hint=total_hint, **kw)
        finally:
            ait._HOST, ait._http_request = old_h, old_r

    import tempfile as _tf
    tmp = _tf.mkdtemp(prefix="ait_dl_")

    # T7-1 成功：宿主落文件 → 直接返回，不建 .part
    t1 = os.path.join(tmp, "ok.bin")
    host, r = run(
        lambda u, p, ts, mb: (_open_write(p, 1000),
                              {"ok": True, "status": 200, "bytes": 1000,
                               "truncated": False, "error": ""})[1],
        t1, 1000)
    check("T7-1 宿主下载成功返回 True", r is True)
    check("T7-2 目标文件存在且无 .part",
          os.path.isfile(t1) and not os.path.isfile(t1 + ".part"))
    check("T7-3 宿主被调用 1 次、带超时与上限", len(host.calls) == 1
          and host.calls[0][2] >= 300 and host.calls[0][3] >= 1000)

    # T7-4 失败：回退自建流式（这里用受控 _http_request 验证确实回退）
    t4 = os.path.join(tmp, "fail.bin")
    stream_called = []
    def _fake_stream(method, url, headers=None, body=None, timeout=30.0):
        stream_called.append(url)
        raise IOError("自建路径也失败（受控）")
    host = FakeHostDl(lambda u, p, ts, mb: {"ok": False, "status": 0,
                                            "error": "网络失败"})
    old_h, old_r = ait._HOST, ait._http_request
    ait._HOST, ait._http_request = host, _fake_stream
    try:
        try:
            ait.download_one([("m", "http://m/x.bin")], t4,
                             log=lambda m, tag="info": None,
                             verify=None, total_hint=1000,
                             retries_per_source=1)
            r4 = "no-raise"
        except Exception as e:
            r4 = type(e).__name__
    finally:
        ait._HOST, ait._http_request = old_h, old_r
    check("T7-4 宿主失败回退自建流式（同一次尝试内）",
          len(stream_called) == 1 and r4 in ("IOError", "OSError"),
          "stream=%d r=%s" % (len(stream_called), r4))
    check("T7-5 失败不留半文件", not os.path.isfile(t4)
          and not os.path.isfile(t4 + ".part"))

    # T7-6 超大产物（模型）不走宿主：仍自建流式
    t6 = os.path.join(tmp, "big.bin")
    host = FakeHostDl(lambda u, p, ts, mb: {"ok": True})
    old_h, old_r = ait._HOST, ait._http_request
    ait._HOST, ait._http_request = host, _fake_stream
    try:
        try:
            ait.download_one([("m", "http://m/big.bin")], t6,
                             log=lambda m, tag="info": None,
                             verify=None, total_hint=5 * 1024 * 1024 * 1024,
                             retries_per_source=1)
        except Exception:
            pass
    finally:
        ait._HOST, ait._http_request = old_h, old_r
    check("T7-6 GB 级产物绕过宿主下载（需进度+断点）", len(host.calls) == 0)

    # T7-7 已有 .part 断点：续传走自建，不试宿主
    t7 = os.path.join(tmp, "resume.bin")
    open(t7 + ".part", "wb").write(b"par")
    host = FakeHostDl(lambda u, p, ts, mb: {"ok": True})
    old_h, old_r = ait._HOST, ait._http_request
    ait._HOST, ait._http_request = host, _fake_stream
    try:
        try:
            ait.download_one([("m", "http://m/r.bin")], t7,
                             log=lambda m, tag="info": None,
                             verify=None, total_hint=1000,
                             retries_per_source=1)
        except Exception:
            pass
    finally:
        ait._HOST, ait._http_request = old_h, old_r
    check("T7-7 有断点时不走宿主（Range 续传保留）", len(host.calls) == 0
          and os.path.isfile(t7 + ".part"))

    # T7-8 老宿主（无 http_download）：返回 None，行为同 2.0.5
    old_h = ait._HOST
    ait._HOST = object()   # 没有 http_download 属性
    try:
        hr = ait._host_http_download("http://m/x", t7 + ".tmp2", 1000)
    finally:
        ait._HOST = old_h
    check("T7-8 老宿主无门面返回 None", hr is None)

    # T7-9 门面抛异常：吞掉回退（不向上冒泡）
    old_h = ait._HOST
    class BoomHost:
        def http_download(self, *a, **k):
            raise RuntimeError("boom")
    ait._HOST = BoomHost()
    try:
        hr = ait._host_http_download("http://m/x", t7 + ".tmp3", 1000)
    except Exception:
        hr = "raised"
    finally:
        ait._HOST = old_h
    check("T7-9 门面抛异常不冒泡", hr is None)


def _open_write(path, size):
    with open(path, "wb") as f:
        f.write(b"x" * size)


def t_on_ui_raise():
    print("\n--- T8 on_ui_thread 探测抛异常 → 默认走 post/队列（失败方向修正）---")
    api = FakeApi()
    api.ui = B249Ui()
    ait.register(api)
    tab = api.build()
    orig_probe = api.ui.on_ui_thread

    def boom():
        raise RuntimeError("probe broken")

    api.ui.on_ui_thread = boom
    called = []
    queued = []
    orig_put = tab._q.put

    def spy_put(item):
        queued.append(item)
        return orig_put(item)

    tab._q.put = spy_put
    import threading

    # 模拟工作线程：探测抛异常时应 post（或队列），绝不能直接执行 UI 调用
    th = threading.Thread(target=lambda: tab.ui_call(lambda: called.append(1)))
    th.start()
    th.join(timeout=5)
    direct = called and not getattr(api.ui, "post_calls", 0) and not queued
    check("T8-1 探测异常不直接执行 UI 调用", not direct,
          "direct=%s" % direct)
    pending = list(api.ui.pending_posts)
    check("T8-2 回调延迟投递且尚未执行",
          called == [] and len(pending) == 1
          and getattr(api.ui, "post_calls", 0) >= 1,
          "called=%s post=%s pending=%d"
          % (called, getattr(api.ui, "post_calls", 0), len(pending)))
    api.ui.drain()
    check("T8-3 宿主执行投递回调后更新状态", called == [1],
          "called=%s" % called)
    api.ui.on_ui_thread = orig_probe


def main():
    t_direct_pack()
    t_spring()
    t_min_height()
    t_host_variants()
    t_host_post()
    t_host_http()
    t_host_download()
    t_on_ui_raise()
    passed = sum(1 for _n, ok in RESULTS if ok)
    print("\n%d/%d passed" % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
