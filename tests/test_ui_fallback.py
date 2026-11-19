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


def main():
    t_direct_pack()
    t_spring()
    t_min_height()
    t_host_variants()
    passed = sum(1 for _n, ok in RESULTS if ok)
    print("\n%d/%d passed" % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
