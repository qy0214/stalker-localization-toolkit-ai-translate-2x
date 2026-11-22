# -*- coding: utf-8 -*-
"""2.0.2 动态多路翻译 + 整 MOD 终检 回归测试

用法:
    python test_dynamic_parallel.py [plugin.py 路径]

约定（与 test_ai_translate_fixes.py 相同）:
    * 不依赖 pytest，自带极简断言器；退出码 0 = 全通过
    * 在临时副本上跑，避免插件导入时在仓库里生成 bin/models/logs/config
    * 页面对象用 `AiTranslateTab.__new__` 跳过重构造，只给被测方法打桩

覆盖:
    T1 parallel_max_routes 边界（预算/权重超支/上限 16/含 off-by-one 回归）
    T2 parallel_of_label（动态标签解析；"16 路" 不许误匹配 "1 路"）
    T3 physical_ram_mb（Win API 探测；取不到返回 0 不编造）
    T4 load_config 校验放宽（旧 1/2/4 兼容、动态 1..16 保留、垃圾回 1）
    T5 LlamaServer / 翻译器的路数上限放宽到 PARALLEL_CAP
    T6 _refresh_parallel_ui 动态重建 + 超预算收窄（点名日志 + 写回设置）
    T7 _on_parallel / _persist_cfg 从动态标签还原路数
    T8 on_start 启动前钳制（配置在别处被改/检测未完成的窗口期兜底）
    T9 终检：掩码残留判定 + _final_audit（账本在盘 / 解析失败 / 漏 ID / 残留）
"""

import atexit
import importlib.util
import json
import os
import queue
import re
import shutil
import sys
sys.dont_write_bytecode = True   # 别把 .pyc 写进随包插件目录（发布门禁 A6 会判为运行产物）
import tempfile
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
PLUGIN = (sys.argv[1] if len(sys.argv) > 1 else
          os.path.join(ROOT, "plugins", "ai_translate", "plugin.py"))
_ASSET_SRC = os.path.join(os.path.dirname(PLUGIN), "assets")

# 运行数据指到临时目录，绝不写真实用户目录（与 fixes 套件同一口径）。
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ait_par_localappdata_")
atexit.register(shutil.rmtree, os.environ["LOCALAPPDATA"], ignore_errors=True)
_ORIG_PLUGIN = PLUGIN
_TEST_ROOT = tempfile.mkdtemp(prefix="ait_par_plugin_")
_PLUGIN_DIR = os.path.join(_TEST_ROOT, "ai_translate")
os.makedirs(os.path.join(_PLUGIN_DIR, "assets"), exist_ok=True)
shutil.copy2(_ORIG_PLUGIN, os.path.join(_PLUGIN_DIR, "plugin.py"))
for _name in ("stalker_termbase.json", "glossary_builtin.json"):
    _src = os.path.join(_ASSET_SRC, _name)
    if os.path.isfile(_src):
        shutil.copy2(_src, os.path.join(_PLUGIN_DIR, "assets", _name))
PLUGIN = os.path.join(_PLUGIN_DIR, "plugin.py")
atexit.register(shutil.rmtree, _TEST_ROOT, ignore_errors=True)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + detail) if detail else ""))
    return ok


def load_module(path, name="ait_par"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


print("=" * 78)
print("加载被测插件:", PLUGIN)
ait = load_module(PLUGIN)
print("版本:", ait.PLUGIN_INFO["version"])
print("=" * 78)

TMP = tempfile.mkdtemp(prefix="ait_par_verify_")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)


# ----------------------------------------------------------------------
# T1 parallel_max_routes 边界
# ----------------------------------------------------------------------
def t1_max_routes():
    print("\n--- T1 parallel_max_routes 边界 ---")
    f = ait.parallel_max_routes
    check("T1-1 无预算信息返回 0（回落固定档，不编造）", f(0, 100, 50) == 0)
    check("T1-2 权重本身就超预算返回 1（单路仍可用）",
          f(1000, 2000, 50, 68) == 1)
    # 8GB 卡 / 7B(Q4≈2200MiB) / 8K ctx(512MiB KV·路)：
    #   budget=7168, fixed=2268, (7168-2268)//512=9, total(9)=6876≤7168
    n = f(7168, 2200, 512, 68)
    check("T1-3 8GB/7B/8K → 9 路（总占用仍 ≤ 预算）", n == 9,
          "max_n=%d total(%d)=%d budget=7168" % (n, n, 2268 + 512 * n))
    check("T1-4 最高档不超预算（off-by-one 回归：曾多算 1 路）",
          2268 + 512 * f(7168, 2200, 512, 68) <= 7168)
    # 16K ctx(1024/路)：(7168-2268)//1024=4；total(4)=6364≤7168, total(5)=7388 超支
    n = f(7168, 2200, 1024, 68)
    check("T1-5 8GB/7B/16K → 4 路", n == 4 and 2268 + 1024 * n <= 7168,
          "max_n=%d" % n)
    # mt2(≈1100MiB) 16K ctx：(7168-1168)//1024=5
    check("T1-6 8GB/mt2/16K → 5 路", f(7168, 1100, 1024, 68) == 5)
    # 32GB 内存 CPU 预算=32452×0.7≈22716：mt2/8K → 42 路 → 封顶 16
    check("T1-7 大内存 CPU 封顶 PARALLEL_CAP=16",
          f(22716, 1100, 512, 68) == ait.PARALLEL_CAP == 16)
    check("T1-8 巨大预算封顶 16（收益递减上限生效）",
          f(1 << 20, 100, 512, 68) == 16)


# ----------------------------------------------------------------------
# T2 parallel_of_label
# ----------------------------------------------------------------------
def t2_label():
    print("\n--- T2 parallel_of_label 动态标签解析 ---")
    f = ait.parallel_of_label
    check("T2-1 常规标签", f("3 路（推荐，约 5.80 GB）") == 3)
    check("T2-2 两位数路数", f("16 路（推荐，约 9.10 GB）") == 16)
    check("T2-3 1 路缺省档", f("1 路（最稳，默认，约 2.27 GB / 显卡 8.00 GB）") == 1)
    check("T2-4 旧固定档文案仍可解析", f("4 路（需 ≥8GB 显存）") == 4)
    check("T2-5 '16 路' 不误匹配 '1 路'", f("16 路（…）") != 1)
    check("T2-6 空/垃圾文本返回 0",
          f("") == 0 and f("垃圾") == 0 and f(None) == 0)


# ----------------------------------------------------------------------
# T3 physical_ram_mb
# ----------------------------------------------------------------------
def t3_ram():
    print("\n--- T3 physical_ram_mb ---")
    ram = ait.physical_ram_mb()
    ok = (ram == 0) or ram > 1024
    check("T3-1 物理内存探测合理（>1GB 或不可用=0）", ok,
          "ram=%s MiB" % ram)


# ----------------------------------------------------------------------
# T4 load_config 校验放宽
# ----------------------------------------------------------------------
def t4_config():
    print("\n--- T4 load_config 校验放宽 ---")
    tmp = tempfile.mkdtemp(prefix="ait_par_cfg_")
    cfg_path = os.path.join(tmp, "config.json")
    old = ait._CONFIG
    ait._CONFIG = cfg_path
    try:
        cases = [
            (1, 1), (2, 2), (4, 4),          # 旧固定档原样兼容
            (8, 8), (16, 16), (9, 9),        # 动态档保留
            (0, 1), (-3, 1), (17, 1),        # 越界回 1
            ("5", 5), ("abc", 1), (None, 1),
        ]
        all_ok = True
        details = []
        for raw, expect in cases:
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({"parallel": raw}, f)
            got = ait.load_config()["parallel"]
            all_ok = all_ok and got == expect
            details.append("%r→%d" % (raw, got))
        check("T4-1 旧 1/2/4 兼容 + 动态 1..16 保留 + 越界/垃圾回 1",
              all_ok, " ".join(details))
        def _vge(v, base):
            return tuple(int(x) for x in v.split(".")) >= tuple(
                int(x) for x in base.split("."))
        check("T4-2 版本号 ≥ 2.0.2（动态多路功能已随版本保留）",
              _vge(ait.PLUGIN_INFO["version"], "2.0.2"),
              ait.PLUGIN_INFO["version"])
    finally:
        ait._CONFIG = old
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------
# T5 路数上限放宽（LlamaServer 实例 + 翻译器源码核对）
# ----------------------------------------------------------------------
def t5_cap():
    print("\n--- T5 路数上限放宽到 PARALLEL_CAP ---")
    srv = ait.LlamaServer("x.exe", "m.gguf", "cpu", lambda *a, **k: None,
                          ctx_size=8192, parallel=9)
    check("T5-1 LlamaServer 接受 9 路（旧实现写死上限 4）",
          srv.parallel == 9, "parallel=%d" % srv.parallel)
    srv2 = ait.LlamaServer("x.exe", "m.gguf", "cpu", lambda *a, **k: None,
                           ctx_size=8192, parallel=99)
    check("T5-2 LlamaServer 超过 CAP 仍封顶", srv2.parallel == ait.PARALLEL_CAP)
    import inspect
    src = inspect.getsource(ait.TranslationJob.__init__)
    n_dyn = src.count("min(PARALLEL_CAP")
    check("T5-3 翻译器路数钳制也用 PARALLEL_CAP（无写死的 4）",
          n_dyn >= 1 and "min(4," not in src, "命中 %d 处" % n_dyn)


# ----------------------------------------------------------------------
# 页面对象打桩工具
# ----------------------------------------------------------------------
def make_tab(parallel_sel=8, choices=None, variant="cuda"):
    from fake_host import FakeUi
    ui = FakeUi()
    tab = ait.AiTranslateTab.__new__(ait.AiTranslateTab)
    tab.ui = ui
    tab.api = types.SimpleNamespace(log=lambda m: None)
    tab._q = queue.Queue()          # ui_call 的跨线程投递队列（跳过 __init__）
    tab.sel = {"parallel": parallel_sel, "variant": variant, "tier": "mt2",
               "ctx": "8192"}
    tab.cfg = {"parallel": parallel_sel}
    tab.flags = {}                  # _persist_cfg 需要（T8）
    tab.btn_start = None            # on_start 里 set_enabled(None) 合法（T8）
    logs = []
    tab.log = lambda msg, tag="info": logs.append((tag, str(msg)))
    tab._logs = logs
    tab.find_model = lambda tier=None: os.path.join(TMP, "fake.gguf")
    tab._route_ctx = lambda: 8192
    tab.current_variant = lambda: variant
    tab._hw_vram_mb = 8192
    if choices is None:
        choices = [(1, "1 路（最稳，默认，约 2.27 GB / 显卡 8.00 GB）"),
                   (2, "2 路（约 2.78 GB）"),
                   (3, "3 路（推荐，约 3.29 GB）")]
    tab._parallel_choices = lambda: choices
    tab.parallel_combo = ui.combo(None, ["旧档位"], on_change=None)
    return tab


# ----------------------------------------------------------------------
# T6 _refresh_parallel_ui：动态重建 + 超预算收窄
# ----------------------------------------------------------------------
def t6_refresh():
    print("\n--- T6 _refresh_parallel_ui 动态重建 + 收窄 ---")
    old_ui = ait._UI
    from fake_host import FakeUi
    ait._UI = FakeUi()
    try:
        # ① 超预算收窄：选 8 路、预算内最高 3 路 → 收窄 + 日志 + 写回
        tab = make_tab(parallel_sel=8)
        tab._refresh_parallel_ui()
        items = tab.parallel_combo.items
        check("T6-1 下拉按动态选项重建（1..3）",
              len(items) == 3 and all("路（" in x for x in items),
              " | ".join(items))
        check("T6-2 超预算 8 路收窄到最高档 3 路",
              tab.sel["parallel"] == 3 and tab.cfg["parallel"] == 3,
              "sel=%s cfg=%s" % (tab.sel["parallel"], tab.cfg["parallel"]))
        check("T6-3 收窄有日志点名（不静默丢弃）",
              any("收窄" in m or "超出" in m for _t, m in tab._logs),
              tab._logs[-1][1] if tab._logs else "(无日志)")
        check("T6-4 收窄后的档位是「推荐」档",
              "推荐" in tab.parallel_combo.items[
                  [i for i, x in enumerate(tab.parallel_combo.items)
                   if x.startswith("3 ")][0]])

        # ② 已选路数在预算内：原样保留
        tab2 = make_tab(parallel_sel=2)
        tab2._refresh_parallel_ui()
        check("T6-5 预算内路数原样保留（不重置）",
              tab2.sel["parallel"] == 2 and tab2.cfg["parallel"] == 2 and
              tab2.parallel_combo.items[0].startswith("2 "))

        # ③ 信息不足回落固定档（choices=None）且旧超预算值收窄到 4
        tab3 = make_tab(parallel_sel=8, choices=[])
        tab3._parallel_choices = lambda: None
        tab3._refresh_parallel_ui()
        fixed = [ait.PARALLEL_LABELS[v] for v in ait.PARALLEL_ORDER]
        check("T6-6 信息不足回落固定档 1/2/4",
              sorted(tab3.parallel_combo.items) == sorted(fixed),
              " | ".join(tab3.parallel_combo.items))
        check("T6-7 回落档里超预算值收窄到 4 路并写回",
              tab3.sel["parallel"] == 4 and tab3.cfg["parallel"] == 4)
    finally:
        ait._UI = old_ui


# ----------------------------------------------------------------------
# T7 标签 → 路数还原（_on_parallel / _persist_cfg）
# ----------------------------------------------------------------------
def t7_parse():
    print("\n--- T7 标签还原路数 ---")
    old_ui = ait._UI
    from fake_host import FakeUi
    ait._UI = FakeUi()
    try:
        tab = make_tab(parallel_sel=1)
        tab._on_parallel("3 路（推荐，约 3.29 GB）")
        check("T7-1 _on_parallel 从动态标签还原路数并写配置",
              tab.sel["parallel"] == 3 and tab.cfg["parallel"] == 3)
        tab2 = make_tab(parallel_sel=1)
        tab2.sel["parallel"] = 12
        n = ait.parallel_of_label("12 路（推荐，约 8.59 GB）")
        check("T7-2 两位数路数可从标签还原", n == 12)
        # _persist_cfg 的下拉还原路径
        tab3 = make_tab(parallel_sel=1)
        ait._combo_set(tab3.parallel_combo, ["5 路（约 4.83 GB）"], 0)
        par_text = ait._combo_get(tab3.parallel_combo)
        got = ait.parallel_of_label(par_text)
        check("T7-3 _persist_cfg 同款解析（_combo_set 轮转后仍成立）",
              got == 5, "combo 当前项=%s → %d" % (par_text, got))
    finally:
        ait._UI = old_ui


# ----------------------------------------------------------------------
# T8 on_start 启动前钳制
# ----------------------------------------------------------------------
def t8_start_clamp():
    print("\n--- T8 on_start 启动前钳制 ---")
    old_ui = ait._UI
    from fake_host import FakeUi
    ait._UI = FakeUi()
    try:
        tab = make_tab(parallel_sel=9)          # sel 里被塞了 9 路
        tab._parallel_max_n = lambda: 3         # 硬件只允许 3 路
        spawned = []
        tab._spawn_server = lambda *a, **k: spawned.append(a)
        tab.server = None
        tab._starting = False
        tab._plan_engine = lambda allow_scan=False, interactive=True: \
            (os.path.join(TMP, "m.gguf"), "x.exe", "mt2", 16384)
        tab._engine_ui = lambda on: None
        tab.ui.set_enabled = lambda w, on: None
        tab.on_start()
        deadline = time.time() + 5
        while not spawned and time.time() < deadline:
            time.sleep(0.02)
        check("T8-1 启动前把超预算路数钳到预算内最高档",
              spawned and spawned[0][5] == 3,
              "spawn=(exe,model,variant,ctx,tier,par) par=%s"
              % (spawned[0][5] if spawned else None))
        check("T8-2 钳制写回缓存与配置",
              tab.sel["parallel"] == 3 and tab.cfg["parallel"] == 3)
        check("T8-3 钳制有日志点名",
              any("超出当前硬件预算" in m for _t, m in tab._logs))
    finally:
        ait._UI = old_ui


# ----------------------------------------------------------------------
# T9 终检
# ----------------------------------------------------------------------
def t9_audit():
    print("\n--- T9 整 MOD 终检 ---")
    check("T9-1 掩码残留正则识别 $$R0$$ / $$R12$$",
          bool(ait._MASK_RESIDUE_RE.search("开了 $$R0$$ 的门"))
          and bool(ait._MASK_RESIDUE_RE.search("$$R12$$")))
    check("T9-2 掩码正则不误伤运行期 token / 格式符",
          not ait._MASK_RESIDUE_RE.search("把 %s 给 $name 和 $$ACTION$$")
          and not ait._MASK_RESIDUE_RE.search("%c[255,1,255,255]"))

    # 构造一次终检：账本 3 个文件 + 1 张改写表（漏 1 条 ID + 有掩码残留）
    done = os.path.join(TMP, "out")
    os.makedirs(os.path.join(done, "configs", "text"), exist_ok=True)
    inp = os.path.join(TMP, "in", "configs", "text")
    os.makedirs(inp, exist_ok=True)

    def wtable(path, ids, text, residue=False):
        body = "".join('<string id="%s"><rus>%s</rus></string>'
                       % (i, (text + (" $$R0$$" if residue else "")))
                       for i in ids)
        with open(path, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="utf-8"?>'
                    "<string_table>%s</string_table>" % body)

    rel = "configs/text/stable.xml"
    wtable(os.path.join(inp, "stable.xml"), ["a", "b"], "оригинал")
    wtable(os.path.join(done, "configs", "text", "stable.xml"),
           ["a"], "译文", residue=True)
    gone = "configs/text/vanished.xml"
    with open(os.path.join(done, "configs", "text", "vanished.xml"),
              "w", encoding="utf-8") as f:
        f.write("")                      # 账本说非空，盘上是空
    missing_rel = "configs/text/ghost.xml"  # 账本有、盘上没有

    tab = ait.TranslationJob.__new__(ait.TranslationJob)
    lock = threading.RLock()

    class _Lock(object):
        def __enter__(self):
            return lock.__enter__()

        def __exit__(self, *a):
            return lock.__exit__(*a)

    class _P(object):
        @staticmethod
        def parse_xray_text_xml(path, enc=None):
            raw = open(path, "r", encoding="utf-8").read()
            ents = []
            for m in re.finditer(r'<string id="([^"]+)">', raw):
                ents.append(types.SimpleNamespace(id=m.group(1)))
            return ents

    tab._ledger_lock = _Lock()
    tab._ledger = {
        rel: {"out_size": 128},
        gone: {"out_size": 128},
        missing_rel: {"out_size": 128},
    }
    tab._audit_tables = {rel}
    tab.in_dir = os.path.join(TMP, "in")
    tab.p = _P()
    logs = []
    tab.log = lambda msg, tag="info": logs.append((tag, str(msg)))
    tab._logs = logs

    def _parse_stub(fn, *args):
        return fn(*args)

    tab._parse = _parse_stub
    tab._final_audit(done)
    joined = "\n".join(m for _t, m in logs)
    check("T9-3 账本在盘检查：空文件被点名",
          "输出为空文件" in joined and "vanished.xml" in joined, "")
    check("T9-4 账本在盘检查：缺失文件被点名",
          "输出缺失" in joined and "ghost.xml" in joined, "")
    check("T9-5 改写表 ID 覆盖：漏 b 条被点名",
          "疑似漏条目" in joined and "stable.xml" in joined, "")
    check("T9-6 掩码残留被点名（疑似口径）",
          "疑似未还原掩码" in joined, "")
    check("T9-7 终检以 warn 汇报（不删改、不影响输出）",
          any(t == "warn" for t, _m in logs))
    check("T9-8 有问题的终检不输出「终检通过」",
          "终检通过" not in joined)

    # 全绿路径：独立干净输出目录（不复用上面带问题的输出）
    clean = os.path.join(TMP, "clean_out")
    os.makedirs(os.path.join(clean, "configs", "text"), exist_ok=True)
    wtable(os.path.join(inp, "good.xml"), ["a"], "оригинал")
    wtable(os.path.join(clean, "configs", "text", "good.xml"),
           ["a"], "хороший перевод")
    wtable(os.path.join(clean, "configs", "text", "stable.xml"),
           ["a", "b"], "перевод")
    tab2 = ait.TranslationJob.__new__(ait.TranslationJob)
    tab2._ledger_lock = _Lock()
    tab2._ledger = {rel: {"out_size": 128},
                    "configs/text/good.xml": {"out_size": 64}}
    tab2._audit_tables = {"configs/text/good.xml"}
    tab2.in_dir = os.path.join(TMP, "in")
    tab2.p = _P()
    logs2 = []
    tab2.log = lambda msg, tag="info": logs2.append((tag, str(msg)))
    tab2._parse = _parse_stub
    tab2._final_audit(clean)
    check("T9-9 干净输出输出「终检通过」",
          any("终检通过" in m for _t, m in logs2),
          logs2[0][1] if logs2 else "(无日志)")


t1_max_routes()
t2_label()
t3_ram()
t4_config()
t5_cap()
t6_refresh()
t7_parse()
t8_start_clamp()
t9_audit()

print("\n" + "=" * 78)
fails = [r for r in RESULTS if not r[1]]
print("总计 %d 项，失败 %d 项" % (len(RESULTS), len(fails)))
if fails:
    print("失败清单：")
    for name, _ok, detail in fails:
        print("  -", name, ("| " + detail) if detail else "")
sys.exit(1 if fails else 0)
