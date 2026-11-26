# -*- coding: utf-8 -*-
"""zh_voice 骨架期契约与链路测试。

跑法：
    python tests/test_zh_voice_shell.py

覆盖：
  W1 worker 握手行形状与协议校验
  W2 worker /health 心跳、echo 任务全链路、/shutdown 优雅退出
  W3 预留任务类型（tts 等）显式 501，不静默
  W4 scan 任务：survey + 语音枚举 + 待解包清单（经 worker HTTP 全链路）
  W5 extract 任务：引擎解包 → VOICE_SRC 镜像树（经 worker HTTP 全链路）
  D1 detect：假游戏目录的识别 / fsgame.ltx 解析 / JSGME 检查 / 存档枚举
  E1 解包引擎（vendor 真引擎）：合成 .db 存档 + sounds 前缀过滤 + 镜像树
  E2 引擎可用性状态（vendor 副本）
  A1 audio 枚举分类：characters_voice 计语音 / weapons 不计 / 语言启发式
  P1 插件契约：PLUGIN_INFO 形状、PLUGIN_API=1、register(api) 经假宿主
     注册成功，页面构建零 Warn（线程纪律）
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request  # 仅测试进程用；插件源码仍禁（P2 不受影响）

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORE_DIR = os.path.join(ROOT, "core")
PLUGIN = os.path.join(ROOT, "plugins", "zh_voice", "plugin.py")

sys.path.insert(0, HERE)
sys.path.insert(0, CORE_DIR)
from fake_host import FakeApi          # noqa: E402
from stalker_voice_core import protocol  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + ((" — " + str(detail)) if detail and not cond else ""))


def _load_plugin():
    spec = importlib.util.spec_from_file_location("zh_voice_plugin", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _start_worker():
    env = dict(os.environ)
    proc = subprocess.Popen(
        [sys.executable, "-m", "stalker_voice_core.worker", "--port", "0"],
        cwd=CORE_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env)
    line = proc.stdout.readline().strip()
    hs = protocol.parse_handshake(line)
    return proc, hs


def case_worker():
    print("== W1/W2/W3 worker 链路 ==")
    proc, hs = _start_worker()
    try:
        check("W1 握手字段", hs["service"] == "stalker_voice_core"
              and hs["protocol"] == protocol.PROTOCOL_VERSION
              and hs["port"] > 0, hs)

        base = "http://127.0.0.1:%d" % hs["port"]
        with urllib.request.urlopen(base + "/health", timeout=3) as r:
            health = json.loads(r.read().decode("utf-8"))
        check("W2a health", health.get("ok") and health.get("pid") == hs["pid"]
              and "beat_age_sec" in health, health)

        # echo 任务全链路
        req = urllib.request.Request(
            base + "/job",
            data=json.dumps({"kind": "echo",
                             "payload": {"hi": 1}}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            submit = json.loads(r.read().decode("utf-8"))
        check("W2b echo 提交 202", r.status == 202 and submit.get("ok"),
              (r.status, submit))

        job_id = submit["job_id"]
        done = None
        for _ in range(50):
            with urllib.request.urlopen(base + "/job/" + job_id,
                                        timeout=3) as r:
                job = json.loads(r.read().decode("utf-8"))["job"]
            if job["status"] in ("done", "error"):
                done = job
                break
            time.sleep(0.05)
        check("W2c echo 完成", done and done["status"] == "done"
              and done["result"]["echo"] == {"hi": 1}, done)

        # 预留类型显式 501
        req = urllib.request.Request(
            base + "/job",
            data=json.dumps({"kind": "tts", "payload": {}}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=3)
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        check("W3 预留类型 501", code == 501, code)

        # 优雅退出
        req = urllib.request.Request(base + "/shutdown", data=b"",
                                     method="POST")
        urllib.request.urlopen(req, timeout=3)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
        check("W2d shutdown 退出", proc.poll() is not None, proc.poll())
    finally:
        if proc.poll() is None:
            proc.kill()


def case_detect():
    print("== D1 detect 假目录 ==")
    from stalker_voice_core import detect
    tmp = tempfile.mkdtemp(prefix="zhv_fake_game_")
    os.makedirs(os.path.join(tmp, "bin"), exist_ok=True)
    for fn in ("xr_3da.exe", "JSGME.exe"):
        open(os.path.join(tmp, fn), "wb").close()
    open(os.path.join(tmp, "gamedata.db0"), "wb").close()
    with open(os.path.join(tmp, "fsgame.ltx"), "w",
              encoding="cp1251", errors="replace") as f:
        f.write("$game_data$ = false|true|gamedata\\\r\n"
                "$game_sounds$ = false|true|gamedata\\sounds\\\r\n")

    s = detect.survey(tmp)
    check("D1a 识别 soc", s["game"]["kind"] == "soc", s["game"])
    check("D1b fsgame 解析", s["game_data_on"] is False
          and (s["fsgame"] or {}).get("game_sounds", {}).get("path")
          .endswith("sounds\\"), s["fsgame"])
    check("D1c JSGME 就位", s["jsgme"]["jsgme_exe"] is True
          and s["jsgme"]["mods_dir"] is False, s["jsgme"])
    check("D1d 存档枚举", s["archives"] == ["gamedata.db0"], s["archives"])

    empty = tempfile.mkdtemp(prefix="zhv_empty_")
    s2 = detect.survey(empty)
    check("D1e 空目录不猜", s2["game"]["kind"] == "unknown"
          and s2["fsgame"] is None, s2)


def _fake_game(with_audio=False):
    """造一个假游戏根：xr_3da.exe + fsgame.ltx + JSGME + （可选）假语音树。"""
    tmp = tempfile.mkdtemp(prefix="zhv_fake_game_")
    os.makedirs(os.path.join(tmp, "bin"), exist_ok=True)
    for fn in ("xr_3da.exe", "JSGME.exe"):
        open(os.path.join(tmp, fn), "wb").close()
    with open(os.path.join(tmp, "fsgame.ltx"), "w",
              encoding="cp1251", errors="replace") as f:
        f.write("$game_data$ = false|true|gamedata\\\r\n"
                "$game_sounds$ = false|true|gamedata\\sounds\\\r\n")
    if with_audio:
        sounds = os.path.join(tmp, "gamedata", "sounds", "characters_voice",
                              "stalker")
        os.makedirs(sounds, exist_ok=True)
        open(os.path.join(sounds, "hello.ogg"), "wb").write(b"OggS-fake")
        wp = os.path.join(tmp, "gamedata", "sounds", "weapons")
        os.makedirs(wp, exist_ok=True)
        open(os.path.join(wp, "shot.ogg"), "wb").write(b"OggS-fake")
    return tmp


def _make_db(dest, entries):
    """用 vendor 引擎的 pack_db 造一个真实格式的 .db 存档（测试专用）。"""
    import importlib
    eng_dir = os.path.join(ROOT, "core", "stalker_voice_core", "vendor")
    if eng_dir not in sys.path:
        sys.path.insert(0, eng_dir)
    stalker_fs = importlib.import_module("stalker_fs")
    files = [(path, data, False) for path, data in entries]
    raw = stalker_fs.pack_db(files, fmt="xdb")
    with open(dest, "wb") as f:
        f.write(raw)
    return dest


def case_extract_engine():
    print("== E1/E2 解包引擎（vendor 副本） ==")
    from stalker_voice_core import extract
    st = extract.engine_status()
    check("E2 引擎可用", st.get("ok") and "xdb" in st.get("formats", []),
          st)

    tmp = tempfile.mkdtemp(prefix="zhv_x1_")
    db = _make_db(os.path.join(tmp, "gamedata_sounds.db"), [
        ("sounds\\dialog\\barman\\bar_1.ogg", b"OggS" + b"\x00" * 100),
        ("sounds\\characters_voice\\wolf\\wolf_1.ogg", b"OggS" + b"\x01" * 50),
        ("configs\\gameplay\\dialog_test.xml", b"<xml/>"),
        ("sounds\\empty\\placeholder.ogg", b""),   # 0 字节占位（合法条目）
    ])
    out = os.path.join(tmp, "VOICE_SRC")
    rep = extract.extract_archive(db, out, prefix="sounds")
    check("E1a 匹配与提取", rep["matched"] == 3 and rep["extracted"] == 3
          and rep["failed"] == 0, rep)
    rels = []
    for base, _dirs, files in os.walk(out):
        for fn in files:
            rels.append(os.path.relpath(os.path.join(base, fn), out)
                        .replace("\\", "/"))
    rels.sort()
    check("E1b 镜像树+来源层", rels == [
        "gamedata_sounds/sounds/characters_voice/wolf/wolf_1.ogg",
        "gamedata_sounds/sounds/dialog/barman/bar_1.ogg",
        "gamedata_sounds/sounds/empty/placeholder.ogg",
    ], rels)
    check("E1c configs 被过滤", not any("configs" in r for r in rels),
          rels)
    # 0 字节占位文件也必须真实落盘（引擎三态语义）
    zero = os.path.join(out, "gamedata_sounds", "sounds", "empty",
                        "placeholder.ogg")
    check("E1d 0字节占位落盘", os.path.isfile(zero)
          and os.path.getsize(zero) == 0, os.path.getsize(zero))


def case_audio():
    print("== A1 audio 枚举分类 ==")
    from stalker_voice_core import audio
    tmp = _fake_game(with_audio=True)
    sounds = os.path.join(tmp, "gamedata", "sounds")
    st = audio.enumerate_audio(sounds)
    check("A1a 总数与语音数", st["files_total"] == 2
          and st["speech_total"] == 1, st)
    check("A1b 语言启发式", st["by_lang"] == {}, st["by_lang"])
    os.makedirs(os.path.join(sounds, "dialog", "rus"), exist_ok=True)
    open(os.path.join(sounds, "dialog", "rus", "d_1.ogg"), "wb").write(b"x")
    st = audio.enumerate_audio(sounds)
    check("A1c dialog+rus 分类", st["speech_total"] == 2
          and st["by_lang"] == {"ru": 1}, st)
    scan = audio.scan_voice_sources(tmp)
    check("A1d scan 汇总", scan["survey"]["game"]["kind"] == "soc"
          and len(scan["loose_gamedata"]) == 1
          and scan["loose_gamedata"][0]["speech_total"] == 2
          and len(scan["archives_pending"]) == 0, scan)


def _submit_and_wait(base, kind, payload, timeout=20.0):
    req = urllib.request.Request(
        base + "/job", data=json.dumps({"kind": kind,
                                        "payload": payload}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        body = json.loads(r.read().decode("utf-8"))
    assert r.status == 202 and body.get("ok"), (r.status, body)
    job_id = body["job_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        with urllib.request.urlopen(base + "/job/" + job_id,
                                    timeout=5) as r:
            job = json.loads(r.read().decode("utf-8"))["job"]
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.1)
    return job


def case_worker_m2():
    print("== W4/W5 worker scan/extract 全链路 ==")
    # 场景：假游戏根 + 一颗真实格式 .db 存档 + 散文件语音树
    tmp = _fake_game(with_audio=True)
    db = _make_db(os.path.join(tmp, "gamedata_sounds.db"), [
        ("sounds\\dialog\\wolf\\wolf_1.ogg", b"OggS" + b"\x02" * 40),
        ("configs\\test.ltx", b"; cfg"),
    ])
    proc, hs = _start_worker()
    try:
        base = "http://127.0.0.1:%d" % hs["port"]

        job = _submit_and_wait(base, "scan",
                               {"game_root": tmp})
        result = job.get("result") or {}
        check("W4a scan 体检", job["status"] == "done"
              and result["survey"]["game"]["kind"] == "soc", job)
        check("W4b scan 待解包清单",
              len(result.get("archives_pending", [])) == 1
              and result["archives_pending"][0]["source"]
              == "gamedata_sounds", result.get("archives_pending"))
        check("W4c scan 散文件语音",
              (result.get("loose_gamedata") or [{}])[0].get("speech_total")
              == 1, result.get("loose_gamedata"))
        check("W4d scan 引擎状态", (result.get("engine") or {}).get("ok")
              is True, result.get("engine"))

        out_dir = os.path.join(tmp, "VOICE_SRC")
        job = _submit_and_wait(base, "extract", {"game_root": tmp,
                                                 "out_dir": out_dir})
        result = job.get("result") or {}
        check("W5a extract 完成", job["status"] == "done"
              and result.get("extracted_total") == 1
              and result.get("failed_total") == 0, job)
        got = os.path.join(out_dir, "gamedata_sounds", "sounds", "dialog", "wolf", "wolf_1.ogg")
        check("W5b VOICE_SRC 镜像树", os.path.isfile(got)
              and open(got, "rb").read().startswith(b"OggS"), got)
        cfg = os.path.join(out_dir, "gamedata_sounds.db", "configs")
        check("W5c configs 未漏入", not os.path.isdir(cfg), cfg)

        # 再 scan：VOICE_SRC 语音应被枚举出来
        job = _submit_and_wait(base, "scan", {"game_root": tmp})
        result = job.get("result") or {}
        src = (result.get("extracted_sources") or [{}])
        check("W5d 解包后再 scan", job["status"] == "done" and len(src) == 1
              and src[0].get("speech_total") == 1
              and src[0].get("source") == "gamedata_sounds", src)
    finally:
        try:
            urllib.request.urlopen(base + "/shutdown", timeout=3)
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def case_plugin():
    print("== P1 插件契约（假宿主） ==")
    mod = _load_plugin()
    check("P1a PLUGIN_API", mod.PLUGIN_API == 1, mod.PLUGIN_API)
    check("P1b PLUGIN_INFO 形状", isinstance(mod.PLUGIN_INFO, dict)
          and mod.PLUGIN_INFO.get("id") == "zh_voice"
          and "version" in mod.PLUGIN_INFO, mod.PLUGIN_INFO)

    os.environ["STALKER_VOICE_CORE_DIR"] = CORE_DIR
    api = FakeApi()
    ok = api.install(mod)
    check("P1c register(api)", ok is True and len(api.tools) == 1
          and api.tools[0]["name"] == "zh_voice", api.tools)
    check("P1d 注册日志单参", any("中文语音插件" in m for m in api.logs),
          api.logs)

    tab = api.build()
    check("P1e 页面构建零 Warn", not api.ui.warns, api.ui.warns)
    check("P1f 页面控件就位", tab.lb_status is not None
          and tab.btn_start is not None and tab.path_entry is not None)

    # 线程纪律：非 UI 线程经 ui_call 投递（假宿主无 post → 回退队列+timer）
    seen = []
    threading.Thread(target=lambda: tab.ui_call(seen.append, "x"),
                     daemon=True).start()
    time.sleep(0.2)
    api.pump(times=3)
    check("P1g ui_call 入队并经泵执行", seen == ["x"], seen)
    api.ui.assert_no_warns()

    # P2 嵌入式 Python 场景：sys.executable 指向宿主 exe 时，
    # resolve_python 必须找到真实 python.exe（真机教训：否则会再拉起平台）
    print("== P2 嵌入式解释器解析 ==")
    real_exe = sys.executable
    try:
        sys.executable = r"C:\FakeHost\xrkit_app.exe"
        fake_py = os.path.join(os.path.dirname(sys.executable), "python",
                               "python.exe")
        os.makedirs(os.path.dirname(fake_py), exist_ok=True)
        open(fake_py, "wb").close()
        got = mod.resolve_python()
        check("P2a 捆绑解释器优先", os.path.normcase(got)
              == os.path.normcase(fake_py), got)
        os.remove(fake_py)
        got = mod.resolve_python()
        base = os.path.basename(got).lower()
        check("P2b 回退 PATH python", base.startswith("python")
              and base.endswith(".exe"), got)
    finally:
        sys.executable = real_exe
        os.environ.pop("STALKER_VOICE_PYTHON", None)


def main():
    case_worker()
    case_extract_engine()
    case_audio()
    case_worker_m2()
    case_detect()
    case_plugin()
    fails = [r for r in RESULTS if not r[1]]
    print("\n%d/%d 通过" % (len(RESULTS) - len(fails), len(RESULTS)))
    if fails:
        for name, _, detail in fails:
            print("  FAIL: %s — %s" % (name, detail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
