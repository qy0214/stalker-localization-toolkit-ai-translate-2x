"""AI 离线翻译插件（Hy-MT2 / llama.cpp）

为 STALKER Localization Toolkit 增加一个「AI 翻译」栏目：
  * 两档本地离线模型：Hy-MT2-7B Q4_K_M（约 4.4 GB）/ Hy-MT2-1.8B Q8_0（约 1.8 GB）
  * 模型与 llama.cpp 运行库均不内置：插件内下载管理，自动探测网络环境、
    并发实测各镜像下载速度、自动选择最优源；支持断点续传 / 取消 / 失败换源
  * 本地拉起 llama-server（OpenAI 兼容 HTTP），子进程与主程序隔离
  * 翻译 X-Ray 文本 XML / cfgxml / ltx / script，支持术语表、译文复用、
    临时断点、冗余 ID 报告

纯标准库实现（urllib / threading / subprocess / zipfile），可在工具集的
PyInstaller 冻结环境（无第三方包）中运行；界面只用 api.ui，Tk / Qt 双后端。

第三方来源：X-Ray 解析器（ai_translate_assets/xray_pkg.zip）与
翻译流程参考 wzyddg/stalker_auto-trans-tool_remake（上游未声明
许可证）；详见仓库 THIRD_PARTY_NOTICES.md 与随包
THIRD_PARTY_NOTICES.txt。
"""

import os
import sys
import re
import json
import time
import io
import csv
import codecs
import hashlib
import socket
import queue
import threading
import subprocess
import zipfile
import tempfile
import shutil
import atexit
from collections import Counter
import urllib.request
import urllib.error
import urllib.parse

PLUGIN_INFO = {
    "id": "ai_translate",
    "name": "AI 离线翻译（Hy-MT2 / 自选模型）",
    "version": "1.9.25",
    "author": "local",
    "description": "本地离线模型翻译 X-Ray 文本；解压即用、零环境依赖；"
                   "混元两档内置模型自动测速下载；支持自选任意 GGUF 模型；"
                   "CPU / CUDA 12.4 / CUDA 13.4 / Vulkan 四后端并自动检测显卡；"
                   "内置无限制强制术语库",
}

APP_NAME = "ai_translate"
_ATOMIC_REPLACE_LOCK = threading.Lock()


_HERE = os.path.dirname(os.path.abspath(__file__))
_core_dirs = (
    _HERE,
    os.path.join(os.path.dirname(os.path.dirname(_HERE)), "core"),
    os.path.join(os.getcwd(), "plugins"),
    os.path.join(os.getcwd(), "core"),
    os.getcwd(),
)
for _core_dir in _core_dirs:
    if os.path.isdir(os.path.join(_core_dir, "stalker_translate_core")):
        if _core_dir not in sys.path:
            sys.path.insert(0, _core_dir)
        break
from stalker_translate_core import (  # noqa: E402
    OfflineTranslationJob,
    OutputTruncated,
    Termbase,
    resolve_asset_root,
    sha256_file as _core_sha256_file,
)
_ASSETS = str(resolve_asset_root(__file__, "ai_translate_assets"))
_VENDOR_ZIP = os.path.join(_ASSETS, "xray_pkg.zip")
_SHIPPED_BIN_DIR = os.path.join(_ASSETS, "bin")
_SHIPPED_MODELS_DIR = os.path.join(_ASSETS, "models")


def _writable(path):
    """目录是否可写（Program Files 等只读安装位置会失败）。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe")
        with open(probe, "wb") as f:
            f.write(b"1")
        os.remove(probe)
        return True
    except Exception:
        return False


def _has_runtime_data(path):
    """插件目录里是否已有运行数据（老用户把模型/运行库/配置下在了这里）。"""
    try:
        if os.path.isfile(os.path.join(path, "config.json")):
            return True
        for d in ("models", "bin"):
            sub = os.path.join(path, d)
            if os.path.isdir(sub) and os.listdir(sub):
                return True
    except OSError:
        pass
    return False


# ★ 1.9.19（同步 2.x 的 portable.txt 闸门，兼容式）：旧逻辑只要随包目录可写
# 就把 models/bin/logs/downloads/config.json 全写进插件目录——**导入即污染**
# 分享包，本仓库自己的发布门禁 A6 就会报"分享包含运行产物"（实测每次跑测试
# 都新建 4 个空目录）。2.x 的做法是默认落 %LOCALAPPDATA%、只有显式放
# portable.txt 才自包含。这里采用兼容式闸门，避免老用户已下载到插件目录的
# GB 级模型失效重下：
#   1) 显式 portable.txt + 可写        → 数据跟着插件走（绿色版）
#   2) 插件目录已有运行数据 + 可写     → 保持原样（老用户不重下）
#   3) 其余（含只读安装位置）          → %LOCALAPPDATA%
_PORTABLE_MARK = os.path.join(_HERE, "portable.txt")
_LOCAL_ROOT = os.path.join(
    os.environ.get("LOCALAPPDATA")
    or os.path.expanduser("~"),
    "STALKER Localization Toolkit", "ai_translate")

if _writable(_ASSETS) and (os.path.isfile(_PORTABLE_MARK)
                           or _has_runtime_data(_ASSETS)):
    _DATA_ROOT = _ASSETS
else:
    _DATA_ROOT = _LOCAL_ROOT

_MODELS_DIR = os.path.join(_DATA_ROOT, "models")
_BIN_DIR = os.path.join(_DATA_ROOT, "bin")
_LOGS_DIR = os.path.join(_DATA_ROOT, "logs")
_CONFIG = os.path.join(_DATA_ROOT, "config.json")
_DOWNLOAD_DIR = os.path.join(_DATA_ROOT, "downloads")

# 术语分两层，避免"升级覆盖 plugins/ 把用户自定义术语一起冲掉"：
#   builtin（随包，只读）      ai_translate_assets/glossary_builtin.json
#   用户覆盖层（可变）          <_DATA_ROOT>/glossary_user.json
# 1.6.0 之前两者是同一个文件（assets/glossary_user.json），升级即丢用户词条；
# 这里保留对旧文件名/旧扁平格式的读取兼容，见 _read_user_layer()。
_GLOSS_BUILTIN = os.path.join(_ASSETS, "glossary_builtin.json")
_GLOSS_BUILTIN_LEGACY = os.path.join(_ASSETS, "glossary_user.json")
_GLOSS_USER = os.path.join(_DATA_ROOT, "glossary_user.json")
_GLOSS_LAYER_FORMAT = "ai-translate-glossary-user/1"

for _d in (_DATA_ROOT, _MODELS_DIR, _BIN_DIR, _LOGS_DIR, _DOWNLOAD_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception:
        pass


def _bin_dirs():
    """查找运行库时同时看随包目录与用户数据目录。"""
    return [_SHIPPED_BIN_DIR, _BIN_DIR]


# ══════════════════════════════════════════════════════════════════
# Windows 进程生命周期：Job Object + 遗留引擎清理
#
# 背景（2026-10-30 实测）：工具集被"任务管理器结束任务"后，llama-server
# 不会收到任何通知（atexit 不执行），于是留在后台继续占着 ~3 GB 显存和
# 数 GB 内存。连续几次强杀后 6 GB 显卡被 4 个残留引擎塞满，驱动把模型
# 溢到内存，下一次翻译连窗口一起卡死 —— 用户看到的就是"又卡死了"。
#
# 这里做两件事：
#   1. 启动时扫一遍 llama-server.exe：只认本插件自己的 exe 路径，且父
#      进程已经不在的（真孤儿），杀掉并回收显存；
#   2. 新拉起的引擎放进 kill-on-close 的 Job Object：宿主进程无论怎么
#      死（含 TerminateProcess），内核都会连带终止它，不再产生新孤儿。
# ══════════════════════════════════════════════════════════════════

_WIN = False          # False=尚未初始化；None=初始化失败；dict=已就绪
_KILL_JOB = None      # kill-on-close Job 句柄，保持到进程结束
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_WAIT_TIMEOUT = 0x00000102
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_TH32CS_SNAPPROCESS = 0x00000002


def _win():
    """Windows 进程/作业对象 API 的轻量封装；非 Windows 返回 None。"""
    global _WIN
    if os.name != "nt":
        return None
    if _WIN not in (False, None):
        return _WIN
    try:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMIT),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD,
                                                 wintypes.DWORD]
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.Process32FirstW.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32FirstW.restype = wintypes.BOOL
        k32.Process32NextW.argtypes = [wintypes.HANDLE,
                                       ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.restype = wintypes.BOOL
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                    wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateProcess.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE,
                                                 wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL

        _WIN = {
            "ctypes": ctypes,
            "wintypes": wintypes,
            "k32": k32,
            "EXTENDED_LIMIT": EXTENDED_LIMIT,
            "PROCESSENTRY32W": PROCESSENTRY32W,
            "INVALID_HANDLE": ctypes.c_void_p(-1).value,
        }
    except Exception:
        _WIN = None
    return _WIN


def _norm_path(p):
    try:
        return os.path.normcase(os.path.abspath(str(p)))
    except Exception:
        return ""


def _server_exe_paths(cfg=None):
    """本插件可能使用的 llama-server.exe 全路径集合。

    只认这些**精确路径**，避免误杀用户自己跑的 llama.cpp 或别的工具集。
    布局：<随包或用户数据>/bin/<变体>/llama-server.exe；外加手工指定的
    exe_paths（用户可选任意本地运行库）。
    """
    paths = set()
    for base in _bin_dirs():
        for variant in ("cpu", "cuda", "cuda13", "vulkan"):
            paths.add(_norm_path(os.path.join(base, variant,
                                              "llama-server.exe")))
        paths.add(_norm_path(os.path.join(base, "llama-server.exe")))
    for p in ((cfg or {}).get("exe_paths") or {}).values():
        if p:
            paths.add(_norm_path(p))
    paths.discard("")
    return paths


def _win_process_image(pid):
    """进程完整 exe 路径；取不到返回空串。"""
    w = _win()
    if not w:
        return ""
    k32, ctypes, wintypes = w["k32"], w["ctypes"], w["wintypes"]
    h = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
    except Exception:
        pass
    finally:
        k32.CloseHandle(h)
    return ""


def _win_pid_alive(pid):
    """父进程是否还活着（只用于判孤儿；PID 复用会偏保守地算"活着"）。"""
    w = _win()
    if not w:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    k32 = w["k32"]
    h = k32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(h, 0) == _WAIT_TIMEOUT
    finally:
        k32.CloseHandle(h)


def _win_terminate_pid(pid):
    w = _win()
    if not w:
        return False
    k32 = w["k32"]
    h = k32.OpenProcess(_PROCESS_TERMINATE, False, int(pid))
    if not h:
        return False
    try:
        return bool(k32.TerminateProcess(h, 1))
    finally:
        k32.CloseHandle(h)


def _win_llama_servers():
    """[(pid, ppid, exe_path)]，仅 llama-server.exe。"""
    w = _win()
    if not w:
        return []
    k32, ctypes, wintypes = w["k32"], w["ctypes"], w["wintypes"]
    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == w["INVALID_HANDLE"]:
        return []
    out = []
    try:
        entry = w["PROCESSENTRY32W"]()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            name = (entry.szExeFile or "").lower()
            if name == "llama-server.exe":
                pid = int(entry.th32ProcessID)
                out.append((pid, int(entry.th32ParentProcessID),
                            _win_process_image(pid)))
            entry.dwSize = ctypes.sizeof(entry)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def _kill_orphan_servers(cfg=None):
    """杀掉**本插件路径下**且父进程已退出的 llama-server，返回 PID 列表。"""
    if os.name != "nt":
        return []
    allowed = _server_exe_paths(cfg)
    killed = []
    for pid, ppid, exe in _win_llama_servers():
        if not exe or _norm_path(exe) not in allowed:
            continue
        if _win_pid_alive(ppid):
            continue                      # 还归某个活着的宿主，别动
        if _win_terminate_pid(pid):
            killed.append(pid)
    return killed


def _create_kill_job():
    """创建 kill-on-close 作业对象；失败返回 None（不影响翻译）。"""
    w = _win()
    if not w:
        return None
    k32, ctypes = w["k32"], w["ctypes"]
    job = k32.CreateJobObjectW(None, None)
    if not job or job == w["INVALID_HANDLE"]:
        return None
    info = w["EXTENDED_LIMIT"]()
    info.BasicLimitInformation.LimitFlags = \
        _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(
            job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(job)
        return None
    return job


def _assign_pid_to_job(job, pid):
    w = _win()
    if not w or not job:
        return False
    k32 = w["k32"]
    h = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
                        False, int(pid))
    if not h:
        return False
    try:
        return bool(k32.AssignProcessToJobObject(job, h))
    finally:
        k32.CloseHandle(h)


def _close_handle(h):
    w = _win()
    if w and h:
        w["k32"].CloseHandle(h)


def _kill_job():
    """进程级共享的 Job（保持句柄到退出；宿主一死内核连带收掉引擎）。"""
    global _KILL_JOB
    if os.name != "nt":
        return None
    if _KILL_JOB is None:
        _KILL_JOB = _create_kill_job()
    return _KILL_JOB


def _assign_server_to_kill_job(proc):
    if proc is None:
        return False
    return _assign_pid_to_job(_kill_job(), proc.pid)


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) STALKER-Localization-Toolkit/ai-translate"

LANG_FULL = {"chs": "简体中文", "eng": "英语", "rus": "俄语", "ukr": "乌克兰语"}
LANG_CODE = {"chs": "zh", "eng": "en", "rus": "ru", "ukr": "uk"}

# 通用技术精确表：只做整条精确匹配，绝不子串替换。
# 这些短标签模型通常保留原文；不放进核心硬词，避免影响正文。
_TECHNICAL_EXACT = {
    "ч.": "小时", "ч": "小时", "час": "小时", "часа": "小时",
    "сек": "秒", "сек.": "秒", "м/с": "米/秒", "в/мин": "发/分钟",
    "шт": "个", "шт.": "个", "р.": "卢布", "руб.": "卢布",
    "дБм.": "分贝毫瓦", "дБм": "分贝毫瓦", "м.": "米", "м": "米",
    "RU": "俄语", "FX": "特效",
}

# 会话词表：当前 MOD 的人名/物品/地点候选，自动翻译并缓存。
# 它不是随包术语库，不需要为每个 MOD 手工建档；缓存只在本机。
_SESSION_TERM_NOTE = (
    "这是游戏名词表。逐行译成简体中文，保持行数与顺序；"
    "人名音译，绰号/物品/地点按游戏习惯意译；"
    "不要编号、不要解释、不要保留俄文或英文原文。")
_SESSION_QUOTES = "\"'«»“”‘’"
_SESSION_NAME_DIRS = ("character_name", "snames", "fnames", "bio")
_SESSION_ITEM_DIRS = ("item", "weapon", "equipment", "artefact",
                      "artifact", "arsenal")
_SESSION_LOC_DIRS = ("mapspot", "level", "place", "zone")
_SESSION_SKIP_DIRS = ("dialog", "news", "task", "tip", "stash", "secret",
                      "credits", "ui", "gameplay", "spawn", "npc")
_SESSION_LIMITS = {"人物与军衔": 1500, "物品·武器·装备·神器": 500,
                   "地点与区域": 400, "阵营与组织": 150, "变异生物": 100}

# TM-lite：只在“同一批文本里重复句比例高”时启用**精确同源**复用。
# 不做模糊匹配、不建跨 MOD 持久库、不把上一次任务的译文混进来。
# 实测 NS7 182 张表：全局重复率 7.4%，5 张表 ≥20%（1,482 条）；
# 阈值 20% 正好把“名字/任务/新闻模板”这类高重复表挑出来，普通对话表
# 不会因为零星重复而改变翻译路径。
TM_LITE_MIN_RATIO = 0.08
TM_LITE_MIN_ENTRIES = 40


def session_category(rel, eid):
    """会话词表只收“名字型”条目；对话框/新闻/任务/署名不碰。"""
    s = (rel or "").lower().replace("\\", "/")
    k = (eid or "").lower()
    if any(x in s for x in _SESSION_SKIP_DIRS):
        return None
    if any(x in s for x in _SESSION_NAME_DIRS):
        if k.endswith(("_bio", "_descr", "_desc", "_text", "_hint")):
            return None
        if k.endswith("_name") or "snames" in s or "fnames" in s:
            return "人物与军衔"
        return None
    if any(x in s for x in ("faction", "community")):
        return "阵营与组织"
    if any(x in s for x in _SESSION_ITEM_DIRS):
        if k.endswith(("_descr", "_desc", "_bio", "_text", "_hint")):
            return None
        return "物品·武器·装备·神器"
    if any(x in s for x in ("creature", "mutant", "monster")):
        return "变异生物"
    if any(x in s for x in _SESSION_LOC_DIRS):
        return "地点与区域"
    return None


def session_term_shape(src, cat):
    """名词形状：短、无句末标点；人名允许缩写点。"""
    s = (src or "").strip().strip(_SESSION_QUOTES).strip()
    if not s or len(s) > 60 or "\n" in s or "\r" in s:
        return ""
    words = s.split()
    if not (1 <= len(words) <= 5):
        return ""
    if cat == "人物与军衔":
        if re.search(r"[。！？!?；;:]", s):
            return ""
        if re.match(r"^[А-ЯЁA-Z]\.\s*[А-ЯЁA-Z]?\.?\s*[А-ЯЁA-Z]", s):
            return s
        if s[0].isupper():
            return s
        return ""
    if re.search(r"[.!?。！？；;:，,]", s):
        return ""
    if not re.search(r"[А-Яа-яЁёA-Za-z]", s):
        return ""
    if cat == "物品·武器·装备·神器" and looks_like_model_name(s):
        return ""
    return s

TRANSLATION_SYS_PROMPT = (
    "你是《潜行者 S.T.A.L.K.E.R.》后末日游戏的专业译员，"
    "输入为对话、任务或物品文本。严格遵守：\n"
    "1. 下列内容逐字保留、数量与顺序不变：占位符（%s、%d、%1$s）、"
    "控制符（$$ACTION$$、$变量）、脚本与条目ID（如 zat_b101_x、"
    "sgtat_gen_text_1_x、a.b.c）、\\n；任何疑似代码或标记一律照抄勿译；\n"
    "2. 人名、阵营、地名、怪物、武器物品等专名使用游戏约定译名，"
    "前后统一，不另译、不音译成多个版本；\n"
    "3. 忠实完整，不增译、不漏译、不脑补剧情；\n"
    "4. 译文必须是地道的中文口语，读起来要像中国玩家自己在说话："
    "用中文的语序、短句和中文习惯的省略与断句，不要翻译腔"
    "（不要「我不得不说」「就……而言」「如此」「因此」「并且」这类欧化说法，"
    "不要生硬的被动句，不要把从句堆成长句，不要连缀多余的「的」）；"
    "称呼、亲疏、口气与情绪贴合说话人身份，"
    "自然使用「啊、吧、嘛、呢、唉、喂」等语气词；"
    "但物品名称、按钮、菜单项这类短标签保持简洁直白，不口语化、不加语气词；\n"
    "5. 粗话、脏话、辱骂、俚语、威胁、黑色幽默与残酷描写一律按原文原意直译，"
    "保留同等粗鲁力度与情绪强度：不得弱化、省略、删除或改写，"
    "不得替换成书面语、委婉语或文雅说法（дерьмо 就是「屎／狗屎」，"
    "不是「糟糕」；заткнись 就是「闭嘴」，不是「请安静」；"
    "башку оторву 就是「拧下你的脑袋」，不是「我不会放过你」）；"
    "原文粗俗则译文粗俗，不得因内容而回避或粉饰；\n"
    "6. 只输出译文本身，不加任何解释或说明；原文已是目标语言时原样返回。\n"
    "7. 术语提示只在语义相符时采用：同一拼写在普通语境（例如 крот=鼹鼠）\n"
    "不要硬套人名译名；同一人物、物品、地名全篇保持同一个译名。\n"
    "8. 输入是格变、复数、单数片段或半句时，按词库和上下文还原语义后\n"
    "译成中文，不要保留原文，也不要只译一半。\n"
    "9. 保留原文的换行数量与段落数；不要把两段合并成一段，也不要多加空行。\n"
    "10. 反问、讽刺、威胁按说话人的意图译，不要逐字翻转否定："
    "«Ну а на хрен тебе не пойти?» 是「你他妈怎么还不滚？」，"
    "不是「你别去了吧」。\n"
    "11. 引号不是保留原文的理由：引号内的阵营、组织、地名、物品、人名"
    "同样要译成中文；只有代码、型号、条目ID、文件路径才保留原文，"
    "译文里不得残留整词或整段的俄文。\n"
    "\n"
    "口语对照示例（左为生硬译法，右为地道中文；照右边的路子写）：\n"
    "· 「听着，我有件事要告诉你。」→「哎，我有事找你。」\n"
    "· 「我们无法再继续留在这里了。」→「这儿咱待不下去了。」\n"
    "· 「我不知道你该如何做到这一点。」→「我不知道你咋弄，反正东西我得要。」\n"
    "· 「你明白正在发生什么吗？」→「你懂现在发生啥了吗？」\n"
    "· 「如果你因失血过多而死，我概不负责。」→「你要是流血死了，我可不负责。」"
)

# 英语源文专用提示词。规则与俄语版相同，但把术语/粗口/口语锚例换成
# 英语里最常见的误译点：小模型容易把英语委婉化、书面化，或把习语逐词直译。
TRANSLATION_SYS_PROMPT_EN = (
    "你是《潜行者 S.T.A.L.K.E.R.》后末日游戏的专业译员，"
    "输入可能是英语的对话、任务、物品或界面文本。严格遵守：\n"
    "1. 下列内容逐字保留、数量与顺序不变：占位符（%s、%d、%1$s）、"
    "控制符（$$ACTION$$、$变量）、脚本与条目ID（如 zat_b101_x、"
    "sgtat_gen_text_1_x、a.b.c）、\\n；任何疑似代码或标记一律照抄勿译；\n"
    "2. 人名、阵营、地名、怪物、武器物品等专名使用游戏约定译名；"
    "有官方中文名的用官方名，没有的按常见音译，首次出现后保持统一；"
    "缩写、型号、代号（AK-74、PDA、SEVA）保留原文，不要硬造中文；\n"
    "3. 忠实完整，不增译、不漏译、不脑补剧情；英语的冠词、助动词、"
    "重复主语不要逐词搬进中文；\n"
    "4. 译文必须是地道的中文口语，读起来要像中国玩家自己在说话："
    "用中文的语序、短句和中文习惯的省略与断句，不要翻译腔"
    "（不要「我不得不说」「就……而言」「如此」「因此」「并且」这类欧化说法，"
    "不要生硬的被动句，不要把从句堆成长句，不要连缀多余的「的」）；"
    "英语习语按中文口语意译（例如 It's a piece of cake → 小菜一碟），"
    "不要逐词硬译；称呼、亲疏、口气与情绪贴合说话人身份，"
    "自然使用「啊、吧、嘛、呢、唉、喂」等语气词；"
    "物品描述、百科正文虽不是对话，同样不许有翻译腔：用自然的中文"
    "说明文，短句直说（「low radiation won't kill you」是「低剂量辐射"
    "死不了人」，不是「你不会因低辐射而死」），「并」「其」「则」这类"
    "书面连接能免则免；"
    "但物品名称、按钮、菜单项这类短标签保持简洁直白，不口语化、不加语气词；\n"
    "5. 粗话、脏话、辱骂、俚语、威胁、黑色幽默与残酷描写一律按原文原意直译，"
    "保留同等粗鲁力度与情绪强度：不得弱化、省略、删除或改写，"
    "不得替换成书面语、委婉语或文雅说法（fuck 就是「操／妈的」，"
    "shit 就是「屎／狗屎」，不是「糟糕」；shut up 就是「闭嘴」，"
    "不是「请安静」；I'll rip your head off 就是「我拧下你的脑袋」，"
    "不是「我不会放过你」）；原文粗俗则译文粗俗，不得因内容而回避或粉饰；\n"
    "6. 只输出译文本身，不加任何解释或说明；原文已是中文时原样返回；"
    "不要把英文原文抄进译文，也不要把规则复述出来。\n"
    "7. 术语提示只在语义相符时采用；同一人物、物品、地名全篇保持同一个译名；"
    "普通语境里的同形词不要硬套专名译名。\n"
    "8. 输入是复数、缩写、单数片段或半句时，按词库和上下文还原语义后译成中文，"
    "不要保留英文原文。\n"
    "9. 保留原文的换行数量与段落数；不要把两段合并成一段，也不要多加空行。\n"
    "10. 反问、讽刺、威胁按说话人的意图译，不要逐字翻转否定："
    "「Why don't you just leave?」是「你他妈怎么还不滚？」，"
    "不是「你为什么不离开」。\n"
    "11. 引号不是保留原文的理由：引号内的阵营、组织、地名、物品、人名"
    "同样要译成中文；只有代码、型号、条目ID、文件路径才保留原文，"
    "译文里不得残留整词或整段的英文或俄文原文。\n"
    "\n"
    "英语口语对照示例（左为生硬译法，右为地道中文；照右边的路子写）：\n"
    "· 「Listen, I've got something to tell you.」→「哎，我有事找你。」\n"
    "· 「We can't stay here anymore.」→「这儿咱待不下去了。」\n"
    "· 「I don't know how you're going to do it.」→「我不知道你咋弄，反正东西我得要。」\n"
    "· 「Do you understand what's going on?」→「你懂现在发生啥了吗？」\n"
    "· 「Get out of my sight.」→「滚，别让我看见你。」\n"
    "· 「You're a dead man.」→「你死定了。」"
)

# 对话场景批量翻译：只用于连续短对话。1.8B 实测能保留 [n] 行号。
# 短标签/UI/物品名：不要套用对话口语规则。
TRANSLATION_SYS_PROMPT_LABEL = (
    "你是《潜行者 S.T.A.L.K.E.R.》的游戏本地化译者。输入是物品、按钮、"
    "菜单或界面短标签。译成简洁、直白、统一的简体中文；不加语气词，"
    "不扩写，不解释。占位符、控制符、代码/条目ID逐字保留。"
    "专名使用游戏约定译名；只输出译文本身。"
)

TRANSLATION_BATCH_SYS_PROMPT = (
    "你是《潜行者 S.T.A.L.K.E.R.》的场景对话译者。输入是多行连续对话，"
    "每行以 [n] 开头。逐行翻译成自然、口语化的简体中文；必须保留每行"
    "开头的 [n] 编号、行数和顺序，不合并、不拆分、不解释、不加角色名。"
    "上下文里前后句只帮助理解，不要翻译没有编号的内容。"
    "术语提示只在语义相符时采用；引号内的阵营、地名、物品、人名也要译成中文。"
    "输入里的 $$R0$$、$$R1$$ 等控制符必须逐字原样保留，不得翻译、"
    "拆分或改动。"
    "只输出带编号的译文行。"
)

# 模型偶尔会把整段系统提示词当译文回显。用固定锚点做硬校验，
# 不依赖提示词本身（换提示词时只需保留这些锚点）。
_PROMPT_ECHO_MARKERS = (
    "你是《潜行者",
    "S.T.A.L.K.E.R.》后末日游戏的专业译员",
    "只输出译文本身",
    "口语对照示例",
    "术语(用给定译名)",
)


def looks_like_prompt_echo(text, source=None):
    s = text or ""
    markers = [marker for marker in _PROMPT_ECHO_MARKERS if marker in s]
    if not markers:
        return False
    return not all(marker in (source or "") for marker in markers)


# ★ 1.9.11 场景上下文串入：模型（尤其 1.8B）会把 user prompt 前面那段
# 「前一句/后一句」当成正文一起翻出来，连标签都照抄。这些标签绝不该出现在
# 译文里，用固定锚点硬校验，与提示词无关。
_CONTEXT_ECHO_MARKERS = (
    "前一句：", "后一句：", "上一句：", "下一句：",
    "上下文（仅供理解", "上下文(仅供理解",
)


def looks_like_context_echo(text):
    s = text or ""
    return any(marker in s for marker in _CONTEXT_ECHO_MARKERS)


def looks_like_context_bleed(src, out):
    """译文串入场景上下文：显式标签，或极短源文吐出超长段落。

    后者兜住模型只翻了上下文正文、却没照抄「前一句：」标签的情况：
    例如源文 `Я не готов.`（11 字符）却输出一整段上一条的译文。
    """
    if not out:
        return False
    if looks_like_context_echo(out):
        return True
    return len((src or "").strip()) < 24 and len((out or "").strip()) > 150


# ★ 1.9.11 数字保真：金额/年份/坐标等大额数字绝不能被模型改写。
# 实测 dm_lin_treasure_33 源文「30000 рублей」被译成「15,000卢布」（报酬腰斩）。
# 只检 >=3 位的数字（<3 位多为编号/序号，且 2006-м→2006年 等屈折会误报）。
_NUMBER_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(\d[\d,]*(?:\.\d+)?)(?![A-Za-z0-9_])")
_ZH_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _zh_number_values(text):
    """把译文里的中文计数法还原成数值集合，用于核对原文大额数字。

    支持：1.5万 / 3万 / 两万 / 三万 / 十五万 / 100,000 / 100 000。
    中文汉字数字只覆盖万位以内的常见写法，够核对金额即可。
    """
    vals = set()
    if not text:
        return vals
    # 阿拉伯数字 + 万/千（含千分位逗号、空格）
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*([万千])", text):
        try:
            base = float(m.group(1).replace(",", ""))
            mult = 10000 if m.group(2) == "万" else 1000
            v = base * mult
            vals.add(int(v) if v == int(v) else v)
        except ValueError:
            pass
    # 纯汉字数字串，如 三万 / 一万五千 / 两万 / 十五万 / 一百二十五
    # 一次性解析整串（含万/千/百/十多级），避免「一万五千」被拆成 1万+5千。
    for m in re.finditer(r"[零〇一二两三四五六七八九十百千万]+", text):
        seg = m.group(0)
        total, section, num = 0, 0, 0
        for ch in seg:
            if ch in _ZH_DIGITS:
                num = _ZH_DIGITS[ch]
            elif ch in ("十", "百", "千", "万"):
                unit = {"十": 10, "百": 100, "千": 1000, "万": 10000}[ch]
                if ch == "万":
                    total += (section + num or 1) * unit
                    section, num = 0, 0
                else:
                    section += (num or 1) * unit
                    num = 0
        value = total + section + num
        if value:
            vals.add(value)
    return vals


def _canonical_number(raw):
    text = str(raw).replace(",", "").replace(" ", "")
    try:
        value = float(text) if "." in text else int(text)
    except ValueError:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value


def _big_number_counts(text):
    counts = Counter()
    for match in _NUMBER_TOKEN_RE.finditer(text or ""):
        raw = match.group(1)
        digits = raw.replace(",", "").split(".", 1)[0]
        if len(digits.lstrip("0")) < 3:
            continue
        value = _canonical_number(raw)
        if value is not None and abs(value) >= 100:
            counts[value] += 1
    return counts


def missing_big_numbers(src, out):
    """源文大额数字在译文中找不到（原样/千分位/中文计数）时返回缺失列表。"""
    if not src or not out:
        return []
    s = re.sub(r"%c\[[^\]]*\]", " ", str(src))
    s = re.sub(r"\b(\d{4})s\b", r"\1", s, flags=re.I)
    src_counts = _big_number_counts(s)
    for match in re.finditer(
            r"[A-Za-z][A-Za-z0-9]*[-‐‑‒–—]\s*(\d[\d,]*(?:\.\d+)?)", s):
        value = _canonical_number(match.group(1))
        if value is not None:
            src_counts.pop(value, None)
    for match in re.finditer(
            r"\b(\d{4})\s*[-‐‑‒–—]\s*(\d{4})\b", s):
        value = _canonical_number(match.group(2))
        if value is not None:
            src_counts.pop(value, None)
    if not src_counts:
        return []
    out_counts = _big_number_counts(str(out))
    out_vals = _zh_number_values(out)
    missing = []
    for value, required in src_counts.items():
        deficit = required - out_counts.get(value, 0)
        if deficit <= 0:
            continue
        if value in out_vals:
            deficit -= 1
        if (isinstance(value, int) and len(str(value)) == 4
                and value % 10 == 0
                and re.search(r"(?:(?:1[5-9]|20)\s*世纪\s*)?"
                              + str(value)[2:4] + r"\s*年代|"
                              + str(value) + r"年代", str(out))):
            deficit -= 1
        label = (str(int(value))
                 if isinstance(value, float) and value.is_integer()
                 else str(value))
        missing.extend([label] * max(0, deficit))
    return sorted(missing, key=lambda x: (len(x), x))


_RU_PROFANITY_RE = re.compile(
    r"дерьм|заткн|хрен|чёрт|черт|бля|хуй|пизд|ебат|еб[ао]|говн|суч|"
    r"сдох|жоп|муд[ао]|уёб|уеб|нахуй|иди ты|пошёл ты|пошел ты", re.I)
# ★ 1.9.12：英文粗口（实测 "Weird shit." 结尾的独白被漏检，粗口力度闸门不触发）。
_EN_PROFANITY_RE = re.compile(
    r"\b(?:shit|fuck(?:ing|ed|er)?|bitch|bastard|asshole|dick|piss(?:ed|ing)?|"
    r"crap|damn|bullshit|motherfucker|wtf)\b", re.I)
# 中文译文里出现连续两个以上西里尔字母，基本就是模型漏译/原样保留。
_CYR_WORD_RE = re.compile(r"[А-Яа-яЁё]{2,}")
_ZH_PROFANITY_RE = re.compile(
    r"他妈|妈的|操(?:你|他|她|它|死|蛋|逼|屄)|我操|"
    r"靠[！!。.，,]|滚(?:蛋|开|！|!|。|，)|闭嘴|混蛋|畜生|王八|去死|"
    r"该死|见鬼|鸡巴|屌|日你|找死|活腻|屎|狗屎|放屁")
_PROFANITY_RETRY_NOTE = (
    "本句原文含粗口、辱骂或威胁，必须保留同等粗鲁力度：不得弱化、"
    "删除或改成书面语。按原文直译，例如 на хрен→他妈/滚，"
    "дерьмо→屎/狗屎，заткнись→闭嘴。")
_PLACEHOLDER_RETRY_NOTE = (
    "必须逐字保留输入里的 $$R0$$、$$R1$$ 等控制占位符，"
    "数量、顺序和写法都不能改；只翻译占位符以外的正文。")
_RESIDUAL_CYRILLIC_NOTE = (
    "译文里仍夹着俄文（%s）。引号里的阵营、组织、地名、物品、人名"
    "同样要译成中文；只有代码、型号、条目ID、文件路径保留原文，"
    "不要保留俄文单词。")


def has_source_profanity(text):
    """源文含粗口（俄语/英语/中文任一）——决定是否启用粗口力度闸门。"""
    return bool(_RU_PROFANITY_RE.search(text or "")
                or _EN_PROFANITY_RE.search(text or "")
                or _ZH_PROFANITY_RE.search(text or ""))


def has_zh_profanity(text):
    return bool(_ZH_PROFANITY_RE.search(text or ""))


def has_cjk(text):
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

GLOSSARY_HINT_HEADER = "术语(用给定译名)：\n"
GLOSSARY_HINT_BUDGET = 240
GLOSSARY_HINT_STOPWORDS = frozenset((
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "нет", "есть", "был", "была", "были", "быть", "это", "этот",
    "для", "или", "если", "уже", "ещё", "еще", "тут", "там", "тогда", "чем",
    "кто", "где", "когда", "почти", "всего", "всегда", "очень", "надо",
    "можно", "нужно", "теперь", "потом", "себя", "себе", "свой", "свою",
    "один", "два", "три", "без", "под", "над", "про", "при", "между",
)) | frozenset((
    # 英语源文同样要过滤功能词，否则术语提示预算会被 the/a/of 吃掉。
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "and", "or",
    "is", "are", "was", "were", "be", "been", "being", "with", "by",
    "from", "this", "that", "these", "those", "it", "its", "as", "not",
    "no", "yes", "you", "your", "yours", "he", "she", "they", "we", "i",
    "me", "my", "our", "their", "his", "her", "them", "us", "do", "does",
    "did", "can", "could", "will", "would", "should", "may", "might",
    "must", "have", "has", "had", "so", "but", "if", "then", "than",
    "also", "very", "just", "there", "here", "what", "which", "who",
    "whom", "whose", "when", "where", "why", "how", "all", "any", "some",
))


def glossary_hint_block(pairs, budget=GLOSSARY_HINT_BUDGET):
    """把命中术语压成尽可能小的提示块，返回 (文本, 计费字符数)。

    计费字符数是**保守上界**（每行按含换行计、另预留与正文之间的空行），
    略大于实际块长；预算判断因此偏安全，不会算少。

    规则：跳过功能词、大小写去重、按预算截断；超预算的长术语直接跳过而不是
    截半（半截术语反而会误导模型）。实测把原来的 15 条上限换成 240 字符预算
    后，每条请求少注入约 25% 字符，且长专名不再被短词挤掉。
    """
    seen = set()
    lines = []
    used = len(GLOSSARY_HINT_HEADER) + 2
    for k, v in pairs:
        k = str(k).strip()
        lk = k.lower()
        if not k or lk in seen or lk in GLOSSARY_HINT_STOPWORDS:
            continue
        v = str(v)
        cost = len(k) + len(v) + 2
        if used + cost > budget:
            continue
        seen.add(lk)
        lines.append("%s=%s" % (k, v))
        used += cost
    if not lines:
        return "", 0
    return GLOSSARY_HINT_HEADER + "\n".join(lines), used

EXTRA_PROMPT_DEMO = (
    "· 语气语域：译文用冷峻简短的军事简报口吻，不用敬语、不加句末语气词。\n"
    "· 译名风格：怪物与武器名称一律音译，不意译。\n"
    "· 世界观补充：Зона=禁区，аномалия=异常点，мутант=变异体。\n"
    "· 粗口：原文脏话按原意直译并保留同等力度，不要改写成书面语。"
)


_LLAMA_TAG = "b11612"
_GH_BASE = ("https://github.com/ggml-org/llama.cpp/releases/download/"
            + _LLAMA_TAG + "/llama-" + _LLAMA_TAG + "-bin-win-{variant}-x64.zip")
_GH_CUDART = ("https://github.com/ggml-org/llama.cpp/releases/download/"
              + _LLAMA_TAG + "/cudart-llama-bin-win-cuda-12.4-x64.zip")
_GH_CUDART13 = ("https://github.com/ggml-org/llama.cpp/releases/download/"
                + _LLAMA_TAG + "/cudart-llama-bin-win-cuda-13.4-x64.zip")

# 官方 SHA-256（2026-10-23 取自发布方元数据）：
# 运行库 = llama.cpp b11612 GitHub Release asset digest；
# 模型 = HuggingFace LFS oid（sha256），并核对官方字节数。
_LLAMA_SHA256 = {
    "cpu": "b8456ccb1be98365a5a2227efa5c495508a6a3ebce881c6189a1dbeaaa170b69",
    "cuda-12.4": "4c6d8afd23475f6eded74c4a3019a6746aab83183a3158f3fc4de1cb7c4f5f12",
    "vulkan": "e62f0674970a70b007fa3c425d191ceacabbf02406d6c9ac8b6f27cd23abe9e9",
    "cudart-12.4": "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
    # CUDA 13.4（2026-10-29 取自同一 Release 的 asset digest）
    "cuda-13.4": "e1c59ee10ead069dee12bf5a1f5a3ed294b144ea3f9c5006aa89675c5306de38",
    "cudart-13.4": "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668",
}
_MODEL_HASHES = {
    "Hy-MT2-7B-Q4_K_M.gguf": (
        "9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b",
        4624648896),
    "Hy-MT2-1.8B-Q8_0.gguf": (
        "5c3fe0b1408a5ceb0143184ef247b11b579c525f4b02b060e6c851bb76fef1a4",
        1908528192),
}
_GH_PROXIES = [
    ("GitHub 直连", "{url}"),
    ("ghproxy.net 代理", "https://ghproxy.net/{url}"),
    ("gh.h233 代理", "https://gh.h233.eu.org/{url}"),
    ("ghfast 代理", "https://ghfast.top/{url}"),
    ("geekertao 代理", "https://gh.geekertao.top/{url}"),
    ("dpik 代理", "https://gh.dpik.top/{url}"),
    ("llkk 代理", "https://gh.llkk.cc/{url}"),
    ("moeyy 代理", "https://github.moeyy.xyz/{url}"),
    ("ghgo 代理", "https://ghgo.xyz/{url}"),
    ("gh-proxy 代理", "https://gh-proxy.com/{url}"),
    ("imciel 代理", "https://ghproxy.imciel.com/{url}"),
    ("ddlc 代理", "https://gh.ddlc.top/{url}"),
    ("idayer 代理", "https://gh.idayer.com/{url}"),
    ("nuaa 镜像", "https://download.nuaa.cf/{url}"),
    ("mirror.ghproxy 代理", "https://mirror.ghproxy.com/{url}"),
    ("ghproxy.cc 代理", "https://ghproxy.cc/{url}"),
    ("960316 代理", "https://gh.960316.xyz/{url}"),
    ("99988866 代理", "https://gh.api.99988866.xyz/{url}"),
    # 2026-10-29 实测可用（不同网络下可用性会变；插件逐个测速择优）
    ("gh-proxy.org 代理", "https://gh-proxy.org/{url}"),
    ("gh.nxnow 代理", "https://gh.nxnow.top/{url}"),
    ("catmak 代理", "https://gh.catmak.name/{url}"),
    ("monlor 代理", "https://gh.monlor.com/{url}"),
]
_GH_DOMAIN_MIRRORS = [
    ("kkgithub 域名镜像", "https://kkgithub.com/{path}"),
]


def _gh_sources(url):
    path = url.split("github.com/", 1)[-1]
    sources = [(n, t.format(url=url)) for n, t in _GH_PROXIES]
    sources += [(n, t.format(path=path)) for n, t in _GH_DOMAIN_MIRRORS]
    return sources

_CUDART_MARKS = ("cudart64", "cublas", "nvrtc")
_CUDART_MARK = ".cudart_installed.json"

def _model_sources(repo_ms, repo_hf, filename):
    return [
        ("ModelScope 主站",
         "https://modelscope.cn/models/%s/resolve/master/%s" % (repo_ms, filename)),
        ("ModelScope 主站(www)",
         "https://www.modelscope.cn/models/%s/resolve/master/%s" % (repo_ms, filename)),
        ("ModelScope CDN",
         "https://modelscope.cn/api/v1/models/%s/repo?Revision=master"
         "&FilePath=%s" % (repo_ms, filename)),
        ("ModelScope CDN(www)",
         "https://www.modelscope.cn/api/v1/models/%s/repo?Revision=master"
         "&FilePath=%s" % (repo_ms, filename)),
        ("ModelScope CDN(download)",
         "https://modelscope.cn/api/v1/models/%s/repo?Revision=master"
         "&FilePath=%s&download=true" % (repo_ms, filename)),
        ("hf-mirror（HF 国内镜像）",
         "https://hf-mirror.com/%s/resolve/main/%s" % (repo_hf, filename)),
        ("hf-mirror.net（HF 国内镜像 2）",
         "https://hf-mirror.net/%s/resolve/main/%s" % (repo_hf, filename)),
        ("HuggingFace 官方",
         "https://huggingface.co/%s/resolve/main/%s" % (repo_hf, filename)),
        ("HuggingFace 官方(hf.co)",
         "https://hf.co/%s/resolve/main/%s" % (repo_hf, filename)),
    ]


TIER_PROFILES = {
    "7b": {
        "task": "model_7b",
        "file": "Hy-MT2-7B-Q4_K_M.gguf",
        "repo_ms": "Tencent-Hunyuan/Hy-MT2-7B-GGUF",
        "repo_hf": "tencent/Hy-MT2-7B-GGUF",
        "size_gb": 4.4,
        "ram_gb": 8,
        "ctx": 8192,
        "combo": "精雕 · Hy-MT2 7B Q4_K_M｜最精确·质量首选｜速度中等｜上下文8K｜约8GB内存",
        "download": "① 精雕 Hy-MT2-7B Q4_K_M（4.4GB · 质量最高/最精确 · 速度中等）",
        "pos": ("质量：最高（复杂长难句、专业领域、多语言指令遵循最强）\n"
                "精度：最精确，专名/语气/占位符保持最好\n"
                "速度：中等（CPU 短句约数秒/条）\n"
                "上下文：8192 token 质量窗（约 3800 字符/段，超长自动分句）\n"
                "定位：终校级译文，NS7 这类超长日记/大段任务文本首选"),
    },
    "mt2": {
        "task": "model_mt2",
        "file": "Hy-MT2-1.8B-Q8_0.gguf",
        "repo_ms": "Tencent-Hunyuan/Hy-MT2-1.8B-GGUF",
        "repo_hf": "tencent/Hy-MT2-1.8B-GGUF",
        "size_gb": 1.8,
        "ram_gb": 4,
        "ctx": 16384,
        "combo": "精准 · Hy-MT2 1.8B Q8_0｜高质量·快速｜上下文16K｜约4GB内存",
        "download": "② 精准 Hy-MT2-1.8B Q8_0（1.8GB · 质量高/精确 · 速度快速）",
        "pos": ("质量：高（新一代小模型，官方称整体超越主流商业翻译 API）\n"
                "精度：精确，较上代小模型在真实场景与指令遵循上明显提升\n"
                "速度：快速（1.8B 轻量，CPU 也明显快于 7B）\n"
                "上下文：16384 token 质量窗（约 7600 字符/段，超长自动分句）\n"
                "定位：日常汉化主力，质量/速度最均衡的一档"),
    },
    "custom": {
        "task": None,
        "file": None,
        "size_gb": 0,
        "ram_gb": 0,
        "ctx": 8192,
        "combo": "自选 · 任意 GGUF 离线模型｜本机自选文件·自定上下文",
        "pos": ("用法：点「选择已有模型」指定本机任意 .gguf 文件（拖入亦可），\n"
                "并在右侧选择该模型要使用的上下文长度。\n"
                "要求：指令/对话微调的聊天模型（GGUF 内置 chat template）；\n"
                "非混元模型的翻译风格与术语遵循由模型本身决定。\n"
                "上下文：按所选长度生效，超长文本仍自动分句；请按模型大小与\n"
                "内存选择（经验：7B/Q4 约 8GB，1.8B/Q8 约 4GB，上下文越大越吃内存）"),
    },
}
TIER_ORDER = ("7b", "mt2", "custom")
BUILTIN_TIERS = ("7b", "mt2")
CUSTOM_CTX_CHOICES = (4096, 8192, 16384, 32768)
TIER_SHORT = {
    "7b": "精雕7B｜质量最高最精确｜速度中等｜8K上下文",
    "mt2": "精准1.8B｜高质量精确｜快速｜16K上下文",
    "custom": "自选模型｜本机任意 GGUF｜上下文自定",
}

MODE_LABELS = {
    "auto": "auto 自动识别全部",
    "text": "text 文本XML",
    "cfgxml": "cfgxml 配置XML",
    "ltx": "ltx 配置",
    "scriptL": "scriptL 脚本就地替换",
    "scriptE": "scriptE 脚本提取",
}
MODE_ORDER = ("auto", "text", "cfgxml", "ltx", "scriptL", "scriptE")


def _model_task(tier):
    p = TIER_PROFILES[tier]
    digest, size_bytes = _MODEL_HASHES[p["file"]]
    return {
        "label": p["download"],
        "kind": "model", "tier": tier,
        "filename": p["file"],
        "size_hint": p["size_gb"],
        "size_bytes": size_bytes,
        "sha256": digest,
        "sources": _model_sources(p["repo_ms"], p["repo_hf"], p["file"]),
    }


DOWNLOAD_TASKS = {
    TIER_PROFILES[t]["task"]: _model_task(t) for t in BUILTIN_TIERS
}
DOWNLOAD_TASKS.update({
    "bin_cpu": {
        "label": "llama.cpp CPU 运行库（约 19 MB，兼容性最好）",
        "kind": "zip", "variant": "cpu",
        "filename": "llama-server.exe",
        "size_hint": 0.019,
        "sha256": _LLAMA_SHA256["cpu"],
        "sources": _gh_sources(_GH_BASE.format(variant="cpu")),
    },
    "bin_cuda": {
        "label": "llama.cpp CUDA 12 运行库（约 630 MB，含运行时，NVIDIA 最快）",
        "kind": "zip", "variant": "cuda",
        "filename": "llama-server.exe",
        "size_hint": 0.63,
        "sha256": _LLAMA_SHA256["cuda-12.4"],
        "extra_runtime": {
            "url": _GH_CUDART,
            "marks": _CUDART_MARKS,
            "label": "CUDA 运行时（cudart/cublas）",
            "sha256": _LLAMA_SHA256["cudart-12.4"],
        },
        "sources": _gh_sources(_GH_BASE.format(variant="cuda-12.4")),
    },
    "bin_cuda13": {
        "label": "llama.cpp CUDA 13.4 运行库（约 580 MB，需 580+ 驱动 / Turing+）",
        "kind": "zip", "variant": "cuda13",
        "filename": "llama-server.exe",
        "size_hint": 0.58,
        "sha256": _LLAMA_SHA256["cuda-13.4"],
        "extra_runtime": {
            "url": _GH_CUDART13,
            "marks": _CUDART_MARKS,
            "label": "CUDA 13 运行时（cudart/cublas）",
            "sha256": _LLAMA_SHA256["cudart-13.4"],
        },
        "sources": _gh_sources(_GH_BASE.format(variant="cuda-13.4")),
    },
    "bin_vulkan": {
        "label": "llama.cpp Vulkan GPU 运行库（约 31 MB，A/N/I 显卡通用）",
        "kind": "zip", "variant": "vulkan",
        "filename": "llama-server.exe",
        "size_hint": 0.031,
        "sha256": _LLAMA_SHA256["vulkan"],
        "sources": _gh_sources(_GH_BASE.format(variant="vulkan")),
    },
})

VARIANT_LABELS = {
    "cpu": "CPU（兼容性好，不占显存）",
    "cuda": "CUDA 12.4（NVIDIA 显卡，兼容性最好）",
    "cuda13": "CUDA 13.4（需 580+ 驱动 / Turing+，可选）",
    "vulkan": "Vulkan（A/N/I 显卡通用加速）",
}
VARIANT_ORDER = ("cpu", "cuda", "cuda13", "vulkan")
GPU_VARIANTS = ("cuda", "cuda13", "vulkan")

TIER_LABELS = [TIER_SHORT[t] for t in TIER_ORDER]
_LABEL_TO_TIER = {TIER_SHORT[t]: t for t in TIER_ORDER}
_LABEL_TO_TIER.update({TIER_PROFILES[t]["combo"]: t for t in TIER_ORDER})
_MODEL_TASK_ORDER = [TIER_PROFILES[t]["task"] for t in BUILTIN_TIERS]
TASK_KEY_ORDER = _MODEL_TASK_ORDER + ["bin_cpu", "bin_cuda",
                                    "bin_cuda13", "bin_vulkan"]
TIER_BY_TASK = {TIER_PROFILES[t]["task"]: t for t in BUILTIN_TIERS}
TIER_FILE = {t: TIER_PROFILES[t]["file"] for t in BUILTIN_TIERS}
TIER_ALIAS = {"1b8": "mt2", "mt15": "mt2"}


def _fmt_size(n):
    try:
        n = float(n)
    except Exception:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.2f %s" % (n, unit)
        n /= 1024.0


def _fmt_eta(sec):
    if sec is None or sec < 0 or sec > 3600 * 99:
        return "--:--:--"
    sec = int(sec)
    return "%02d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)


def detect_nvidia_gpu():
    """通过 nvidia-smi 探测 NVIDIA 独显。

    返回 (名称, 显存MB, 驱动版本, compute_cap)；无 N 卡/取不到返回 None。
    纯本地、无网络；nvidia-smi 随驱动安装。旧版只取前两项，1.7.0 起带上
    驱动版本与计算能力，用于判断能否使用 CUDA 13.4 构建。
    """
    def query(fields):
        return subprocess.run(
            ["nvidia-smi", "--query-gpu=" + fields,
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)))

    try:
        r = query("name,memory.total,driver_version,compute_cap")
        legacy = False
        if r.returncode != 0:
            # 老驱动不认识 compute_cap：退回 1.x 的两字段查询，至少别把 N 卡
            # 误判成"没有独显"（那样会白白降到 Vulkan/CPU）。
            r = query("name,memory.total")
            legacy = True
        if r.returncode != 0:
            return None
        lines = (r.stdout or "").strip().splitlines()
        if not lines:
            return None
        parts = [x.strip() for x in lines[0].split(",")]
        name = parts[0] if parts else ""
        mem = "".join(ch for ch in (parts[1] if len(parts) > 1 else "")
                      if ch.isdigit())
        driver = "" if legacy or len(parts) <= 2 else parts[2]
        try:
            cc = 0.0 if legacy or len(parts) <= 3 else float(parts[3])
        except (TypeError, ValueError):
            cc = 0.0
        return (name, int(mem) if mem else 0, driver, cc)
    except Exception:
        return None


def cuda13_supported(driver, compute_cap):
    """CUDA 13.x 构建的门槛：驱动主版本 ≥ 580 且计算能力 ≥ 7.5（Turing+）。

    这是"可尝试"的判据，不是保证：OEM/笔记本驱动、Optimus、UMD 与 Toolkit
    版本差异都可能让实际初始化失败，所以调用方还要用真实启动 + GPU 层验证兜底。
    """
    try:
        major = int(str(driver).strip().split(".")[0])
    except (TypeError, ValueError):
        return False
    try:
        cc = float(compute_cap)
    except (TypeError, ValueError):
        return False
    return major >= 580 and cc >= 7.5


_IGPU_MARKERS = ("microsoft basic", "basic display", "remote", "citrix",
                 "parsec", "vmware", "virtual", "hyper-v", "meta display",
                 "oray", "sunlogin", "todesk", "gameviewer", "usb display")


def _gpu_registry_vram():
    """从注册表取真实显存（字节）：{驱动描述: 字节数}，取不到返回 {}。

    为什么不用 Win32_VideoController.AdapterRAM：它是 uint32，≥4GB 就回绕
    （本机 RTX 3060 6GB 报 4293918720 字节 → 被算成 4095MB 再截成 4096）。
    HardwareInformation.qwMemorySize 是 64 位，能拿到真实值（本机实测 6.0GiB）。
    """
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
          "Get-ChildItem 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\"
          "{4d36e968-e325-11ce-bfc1-08002be10318}' -ErrorAction SilentlyContinue"
          " | ForEach-Object { $p=Get-ItemProperty $_.PSPath"
          " -ErrorAction SilentlyContinue;"
          " if ($p.DriverDesc -and $p.'HardwareInformation.qwMemorySize')"
          " { \"$($p.DriverDesc)|$($p.'HardwareInformation.qwMemorySize')\" } }")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=12,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)))
        out = r.stdout.decode("utf-8", "ignore")
    except Exception:
        return {}
    table = {}
    for line in out.splitlines():
        name, _, val = line.strip().partition("|")
        val = val.strip()
        if name and val.isdigit() and int(val) > 0:
            table[name] = int(val)
    return table


def _is_integrated_gpu(low_name):
    """判断是不是集显/虚拟卡（入参已小写）。

    保守策略：拿不准就按集显处理 → 最终退回 CPU。相比"猜成独显去开
    Vulkan、结果比 CPU 还慢"，退回 CPU 是安全得多的错法。
    """
    if any(k in low_name for k in _IGPU_MARKERS):
        return True
    if "intel" in low_name and "arc" not in low_name:
        return True
    if "radeon" in low_name or "amd" in low_name:
        if ("rx " in low_name or " rx" in low_name or "pro " in low_name
                or "instinct" in low_name or "vii" in low_name):
            return False
        return True
    return False


def detect_all_gpus():
    """列出全部显卡（CIM 取名 + 注册表取真实显存），返回 [(名称, 显存MB)]；
    显存未知记为 0，不编造近似值。"""
    vram_table = _gpu_registry_vram()
    try:
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "Get-CimInstance Win32_VideoController | ForEach-Object {"
              "$n=$_.Name; $m=[int64]($_.AdapterRAM); \"$n|$m\"}")
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=12,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)))
        out = r.stdout.decode("utf-8", "ignore")
        gpus = []
        for line in out.splitlines():
            name, _, mem = line.strip().partition("|")
            if not name:
                continue
            mb = 0
            real = vram_table.get(name)
            if real:
                mb = int(real // (1024 * 1024))
            elif mem.isdigit():
                mb = int(mem) // 1024 // 1024
                if mb > 4095:
                    mb = 0
            gpus.append((name, mb))
        return gpus
    except Exception:
        return []


def detect_hardware():
    """整机硬件勘察 → 推荐后端与模型档。返回 dict：
    variant / tier / gpu_name / vram / short（一句话）。"""
    nv = detect_nvidia_gpu()
    others = detect_all_gpus()
    discrete_other = None
    for name, vram in others:
        low = name.lower()
        if "nvidia" in low:
            continue
        if _is_integrated_gpu(low):
            continue
        discrete_other = (name, vram)
    if nv:
        name, vram, driver, cc = nv
        variant = "cuda"          # 默认 12.4；13.4 需用户显式选择
        cuda13_ok = cuda13_supported(driver, cc)
        tier = "7b" if vram >= 5120 else "mt2"
        short = "%s %dGB → CUDA 12.4 + %s%s" % (
            name.replace("NVIDIA GeForce ", ""), round(vram/1024, 1),
            {"7b": "精雕7B", "mt2": "精准1.8B"}[tier],
            ("（CUDA 13.4 可用）" if cuda13_ok else ""))
        return {"variant": variant, "tier": tier, "gpu_name": name,
                "vram": vram, "short": short, "driver": driver,
                "compute_cap": cc, "cuda13_ok": cuda13_ok}
    if discrete_other:
        name, vram = discrete_other
        tier = "mt2"
        return {"variant": "vulkan", "tier": tier, "gpu_name": name,
                "vram": vram,
                "short": "%s → Vulkan + 精准1.8B" % name}
    return {"variant": "cpu", "tier": "mt2", "gpu_name": "",
            "vram": 0, "short": "未检测到独显 → CPU + 精准1.8B"}



_ENC_FALLBACK = ("cp1251", "cp1252")
_ENC_FORCED = "cp1251"
_ENC_UTF8 = ("utf-8", "utf-8-sig")
_BOM_UTF8 = b"\xef\xbb\xbf"
_XML_DECL_ENC = re.compile(rb"<\?xml[^>]*?encoding\s*=\s*[\"']([\w.\-]+)", re.I)
_DECL_SCAN_BYTES = 2048


def _is_valid_utf8(raw):
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _declared_encoding(raw):
    """取 XML 声明里写明的编码名；未声明 / 名字不被 Python 认识时返回 None。"""
    m = _XML_DECL_ENC.search(raw[:_DECL_SCAN_BYTES])
    if not m:
        return None
    name = m.group(1).decode("ascii", "ignore").strip().lower()
    try:
        codecs.lookup(name)
    except (LookupError, ValueError):
        return None
    return name


def encoding_chain(raw):
    """给出该字节流的解码候选次序（首个能严格解码成功者胜出）。

    次序规则，按优先级：
      1. UTF-8 BOM          → utf-8-sig（唯一的权威信号）
      2. 字节流是合法 UTF-8 → utf-8
      3. XML 声明写明的编码 → 提到最前（声明与实际不符时严格解码会失败，
         自动落到下一档，不会误判）
      4. 其余按 cp1251 → cp1252（STALKER 俄版为主流），末位固定 _ENC_FORCED

    返回元组；末位是"尽力而为"档，供解析器的 errors="ignore" 兜底使用。

    已知限制：无 BOM、无声明、又非 UTF-8 的文件，若同时是合法 cp1251 与
    合法 cp1252 字节流（极常见），此处按 STALKER 主流取 cp1251；西文 ww
    版这类文件请把「源语言」选为 eng 或在文件头补 XML 声明。
    """
    if raw.startswith(_BOM_UTF8):
        cands = ["utf-8-sig"]
    elif _is_valid_utf8(raw):
        cands = ["utf-8"]
    else:
        cands = []
        decl = _declared_encoding(raw)
        if decl and decl not in _ENC_UTF8:
            cands.append(decl)
        cands.extend(n for n in _ENC_FALLBACK if n not in cands)
    if not cands or cands[-1] != _ENC_FORCED:
        cands.append(_ENC_FORCED)
    return tuple(cands)


def read_encoding_chain(path, max_bytes=64 * 1024 * 1024):
    """读取文件并给出其解码候选次序；读不了时返回兜底次序。
    仅供解析前判定编码用，不改变调用方自己的读取逻辑。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            raw = f.read(max_bytes if size > max_bytes else size)
    except OSError:
        return (_ENC_FORCED,)
    if size > max_bytes:
        while raw and (raw[-1] & 0xC0) == 0x80:
            raw = raw[:-1]
        if raw and raw[-1] >= 0x80:
            raw = raw[:-1]
    return encoding_chain(raw)


def read_text_any(path):
    """按 UTF-8（含 BOM）→ cp1251 顺序读取脚本/XML 原文，读不出时替换。"""
    try:
        raw = open(path, "rb").read()
    except OSError:
        return ""
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _files_equal(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                x = fa.read(1024 * 1024)
                y = fb.read(1024 * 1024)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError:
        return False


_MOJIBAKE_CHARS = "ÐÑÃÂâ€™Ð„ÑŽ"


def looks_like_mojibake(text):
    """粗判"UTF-8 被单字节码表解出来的乱码"，只用于告警、不自动改判。

    正常俄文/中文文本里不会成片出现 Ð/Ñ/Ã/Â；而 UTF-8 的俄文（Д→D0 94）
    或中文被 cp1251 解码后，特征就是成片的拉丁扩充字符。
    """
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return False
    bad = sum(1 for c in letters if c in _MOJIBAKE_CHARS)
    return bad / len(letters) > 0.3


_VENDOR_IO_LOCK = threading.Lock()


class capture_stdout(object):
    """把 vendor 解析器的 print() 诊断收进插件日志。

    vendor 包（wzyddg fork）用 print 报告"某文件用哪个编码解码"这类诊断，
    而冻结后的 exe 是 GUI 子系统（PE Subsystem=2），stdout 为 None —— 这些
    诊断在用户侧完全不可见，编码猜错时没有任何线索。这里把它收进
    api.log_detail（只落盘），让"到底用了哪个编码"事后可复查。

    stdout 是进程级全局对象，故重定向期间加锁串行；解析只在作业线程发生，
    且按钮互斥保证同时只有一个作业，冲突面很小。拿不到锁则退化为不重定向
    （宁可少一行日志，也不阻塞翻译主流程）。
    """

    def __init__(self):
        self._buf = io.StringIO()
        self._old = None
        self._held = False

    def __enter__(self):
        self._held = _VENDOR_IO_LOCK.acquire(timeout=30)
        if self._held:
            self._old = sys.stdout
            sys.stdout = self._buf
        return self

    def __exit__(self, *exc_info):
        if self._held:
            sys.stdout = self._old
            _VENDOR_IO_LOCK.release()
        return False

    def lines(self):
        return [ln.strip() for ln in self._buf.getvalue().splitlines()
                if ln.strip()]



DEFAULT_CONFIG = {
    "model_paths": {},
    "model_dir": _MODELS_DIR,
    "exe_paths": {},
    "custom_model_path": "",
    "custom_ctx": 8192,
    "tier": "7b",
    "variant": "cpu",
    "in_dir": "",
    "out_dir": "",
    "reuse_dir": "",
    "glossary": "",
    "force_glossary": True,
    "glossary_force": True,
    "protect_proper_v2": True,
    "src_lang": "auto",
    "mode": "auto",
    "session_glossary": True,
    "tm_lite": True,
    "output_anyway": False,
    "prompt_extra": "",
    "artifact_hashes": {},
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(_CONFIG, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    if cfg.get("tier") in TIER_ALIAS:
        cfg["tier"] = TIER_ALIAS[cfg["tier"]]
    if cfg.get("tier") not in TIER_PROFILES:
        cfg["tier"] = "7b"
    mp = cfg.get("model_paths")
    if isinstance(mp, dict) and "1b8" in mp and "mt2" not in mp:
        mp["mt2"] = mp.pop("1b8")
    for k in ("in_dir", "out_dir", "reuse_dir", "glossary"):
        v = cfg.get(k)
        if isinstance(v, str) and v and not os.path.exists(v):
            cfg[k] = ""
    md = cfg.get("model_dir")
    if not (isinstance(md, str) and md and os.path.isdir(md)):
        cfg["model_dir"] = _MODELS_DIR
    if isinstance(mp, dict):
        cfg["model_paths"] = {
            k: v for k, v in mp.items()
            if isinstance(v, str) and v and os.path.isfile(v)}
    ep = cfg.get("exe_paths")
    if isinstance(ep, dict):
        cfg["exe_paths"] = {
            k: v for k, v in ep.items()
            if isinstance(v, str) and v and os.path.isfile(v)}
    cp = cfg.get("custom_model_path")
    if cp and (not isinstance(cp, str) or not os.path.isfile(cp)):
        cfg["custom_model_path"] = ""
    return cfg


_JSON_WRITE_LOCK = threading.Lock()


_LAST_CONFIG_ERROR = ""


def save_config(cfg):
    """原子写配置（tmp → os.replace），并发调用串行化。返回是否写成功。

    写失败**不再静默**：调用方据此提示用户（只读目录/磁盘满时，设置只在内存
    生效）。并发修改 dict 可能让 json.dumps 抛 RuntimeError，同样要捕获。
    """
    global _LAST_CONFIG_ERROR
    try:
        payload = json.dumps(cfg, ensure_ascii=False, indent=1)
    except (TypeError, ValueError, RuntimeError) as e:
        _LAST_CONFIG_ERROR = "%s: %s" % (type(e).__name__, e)
        return False
    with _JSON_WRITE_LOCK:
        try:
            tmp = _CONFIG + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, _CONFIG)
            _LAST_CONFIG_ERROR = ""
            return True
        except Exception as e:
            _LAST_CONFIG_ERROR = "%s: %s" % (type(e).__name__, e)
            return False



def load_builtin_glossary():
    """随包术语（只读）。优先 glossary_builtin.json，兼容旧版文件名。"""
    for path in (_GLOSS_BUILTIN, _GLOSS_BUILTIN_LEGACY):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return flatten_glossary(json.load(f))
        except Exception:
            continue
    return {}


def _read_user_layer(builtin=None):
    """读用户覆盖层，返回 (set_map, deleted, clear_builtin)。

    兼容 1.6.0 之前的扁平格式：那份文件其实是"整张术语库的快照"，若直接当
    覆盖层会把 builtin 的后续更新全部遮蔽。这里按**与 builtin 的差异**提取，
    只保留用户真正改过/新增的条目。
    """
    try:
        with open(_GLOSS_USER, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}, set(), False
    if isinstance(data, dict) and data.get("_format") == _GLOSS_LAYER_FORMAT:
        raw_set = data.get("set") if isinstance(data.get("set"), dict) else {}
        set_map = {str(k): str(v) for k, v in raw_set.items()
                   if str(k).strip() and not str(k).startswith("_")}
        try:
            deleted = {str(k) for k in (data.get("del") or [])
                       if str(k).strip()}
        except TypeError:
            deleted = set()
        return set_map, deleted, bool(data.get("clear_builtin"))
    flat = flatten_glossary(data) if isinstance(data, dict) else {}
    base = load_builtin_glossary() if builtin is None else builtin
    return {k: v for k, v in flat.items() if base.get(k) != v}, set(), False


def load_glossary_layers():
    """返回 (合并后的术语表, 用户 set 层, 用户删除集, 是否清空 builtin)。"""
    builtin = load_builtin_glossary()
    set_map, deleted, clear = _read_user_layer(builtin)
    merged = {} if clear else dict(builtin)
    for key in deleted:
        merged.pop(key, None)
    merged.update(set_map)
    return merged, set_map, deleted, clear


# 只把真正跨语境稳定的专名当硬词。界面与提示（Здоровье→生命值）、
# 任务、对话/百科类会随语境变化，一律只做软参考或不注入，避免
# “Здоровье не шалит?”被硬塞成“生命值”这类语境错误。
_HARD_TERM_CATEGORIES = (
    # 人物/军衔不再全局硬替换：不同 MOD 的同名人物可能不是同一人。
    # 人物名由“当前 MOD 会话词表 + 人物表精确命中”处理，见 _build_session_glossary。
    "阵营与组织", "地点与区域", "变异生物", "物品·武器·装备·神器")


_TERMBASE_PATH = os.path.join(_ASSETS, "stalker_termbase.json")
_TERMBASE_CACHE = {"key": None, "data": {}}
_DEFAULT_LAYERS = {
    "canon": list(_HARD_TERM_CATEGORIES),
    "names": ["人物与军衔"],
    "technical": ["技术缩写"],
    # ★ 1.9.19（同步 2.0.22）：Gunslinger/GAMMA 两个软参考层整体退役——
    # 逐条语义抽检明确错率约 31%（HK→香港、Find %s for Cardan 丢信息、
    # .45 Hydra-shock→.45 AP弹、Остров валунов→大沼泽. 露营点 ID 错配、
    # 绰号↔姓氏系统性错配、'.'→统计）。数据留档在 2.x 仓库
    # corpus/soft_reference_retired.json；本仓库随包资产已同步为退役后版本。
    "reference": ["ANTHOLOGY 参考"],
}
_RISKY_LATIN_RE = re.compile(r"^[a-z][a-z0-9._\-/&()]{1,18}$")


def load_termbase_data():
    """读随包分类术语库（带 mtime 缓存，避免每次命中都重读 350 KB）。"""
    try:
        st = os.stat(_TERMBASE_PATH)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _TERMBASE_CACHE.get("key") == key:
        return _TERMBASE_CACHE.get("data") or {}
    try:
        with open(_TERMBASE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    _TERMBASE_CACHE["key"] = key
    _TERMBASE_CACHE["data"] = data
    return data


# ★ 1.9.11 专名直取：库里已有的译名（БАРМЕН→酒保、ПРИЗРАК→幽灵、
# SMS 名字表等）在"保护专名"跳过之前先精确取出，直接落盘，不再当
# "不可译专名"原样保留。只做整串精确匹配，避免正文普通词被误换。
_BUILTIN_FLAT_CACHE = {"key": None, "data": {}}
_TB_LOW_INDEX = {"key": None, "low": {}}
_PARTIAL_GLOSS_CACHE = {"key": None, "words": []}


def termbase_lookup(text):
    """精确查随包术语库（分类表 + 扁平表全层），返回译名或 None。

    大小写与首尾引号不敏感（SMS 名字表多为全大写，词库多为首字母大写）。
    只做整串精确匹配，不做子串。分类表与扁平表都走 mtime 缓存——终检对
    几千条目逐条调用，不能每次重读 350KB JSON。
    """
    raw = (text or "").strip()
    s = raw.strip('"\u00ab\u00bb\u201c\u201d\'')
    if not raw:
        return None
    # ★ 1.9.19（同步 2.0.22）：首尾引号不敏感必须是**双向**的——旧实现只把
    # 输入剥引号后查，键本身带引号的条目永远查不到（实测 50 条不可达，含
    # `Учёный «О-Сознания»`→`“共同意识”科学家`、`"Tears of Electra"`→
    # `“电之泪”`、`"Monolith" Chief`→`黑石首领`）。原串优先、剥引号兜底。
    cands = []
    for _form in (raw, s):
        if not _form:
            continue
        for _c in (_form, _form.casefold()):
            if _c not in cands:
                cands.append(_c)
    # 分类表：termbase 原始 structures（load_term_layers 的 canon/names 是
    # set，只有 technical/reference 带映射），这里直接读原始表拿全量映射。
    data = load_termbase_data()
    # ★ 全量审计 2026-11-23：3.6 万条后逐分类 casefold 全扫 O(N) 达 3.6ms/次，
    #   改为模块级 casefold 归一索引（O(1)，首分类优先，与旧遍历顺序一致）。
    global _TB_LOW_INDEX
    try:
        _st = os.stat(_TERMBASE_PATH)
        _tkey = (_st.st_mtime_ns, _st.st_size)
    except OSError:
        _tkey = None
    if _TB_LOW_INDEX.get("key") != _tkey:
        idx = {}
        for _cat, table in data.items():
            if _cat.startswith("_") or not isinstance(table, dict):
                continue
            for k, v in table.items():
                kl = str(k).casefold()
                if kl not in idx:
                    idx[kl] = str(v)
        _TB_LOW_INDEX = {"key": _tkey, "low": idx}
    idx = _TB_LOW_INDEX.get("low") or {}
    for _c in cands:
        if _c in idx:
            return idx[_c]
    # 扁平表（glossary_builtin.json，运行时真正用的那张），带 mtime 缓存
    try:
        _bst = os.stat(_GLOSS_BUILTIN)
        _bkey = (_bst.st_mtime_ns, _bst.st_size)
    except OSError:
        _bkey = None
    global _BUILTIN_FLAT_CACHE
    if _BUILTIN_FLAT_CACHE.get("key") != _bkey:
        _bflat = load_builtin_glossary() or {}
        _BUILTIN_FLAT_CACHE = {"key": _bkey, "data": _bflat,
                               "low": {str(k).casefold(): str(v)
                                       for k, v in _bflat.items()}}
    bflat = _BUILTIN_FLAT_CACHE.get("data") or {}
    blow = _BUILTIN_FLAT_CACHE.get("low") or {}
    for _c in cands:
        if _c in bflat:
            return str(bflat[_c])
        if _c in blow:
            return blow[_c]
    return None


_PARTIAL_MODEL_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])(?:[A-Z]+[A-Za-z]*[-/]\d[\w\-/]*"
    r"|\d[\w\-/]*[A-Z]+[A-Za-z]*|[A-Z]{2,})(?![a-z])")


def partial_glossary_translation(src, rel=None):
    """专名保护命中但整串无词库键时，对串内硬层专名词做子串替换。

    ★ 1.9.18（IWP 产物审计）：`Vasya "Boar"`（人物名+引号绰号）整串
    不在词库 → 走专名保护原样保留英文，但 "Boar" 在 canon 有权威译名
    "野猪"。约束（保守）：
      * 只替换 canon/names 硬层词（reference/technical 不参与）；
      * 词长 ≥3 且为 Title Case/全大写（专名形态，避免 doctor/case 类
        普通词误换）；
      * 整串 ≤60 字符（人物/物品短名条目），长句正文不适用；
      * 精确词边界替换，一个词都不命中返回 None。
    """
    s = (src or "").strip()
    if not s or len(s) > 60:
        return None
    # ★ 1.9.19（同步 2.0.22）：制作名单/音乐表整文件不参与——与
    # "连核心术语也不替换"同口径；2.0.21/1.9.18 新增本子串替换时漏了这层豁免，
    # 实测全库 133 条写出里 103 条在 credits，产物如 `Executive 台钳 President`。
    if rel is not None and looks_like_hard_protected_file(rel):
        return None
    global _PARTIAL_GLOSS_CACHE
    data = load_termbase_data()
    meta_layers = (data.get("_meta") or {}).get("layers") or {}
    try:
        _st = os.stat(_TERMBASE_PATH)
        _k = (_st.st_mtime_ns, _st.st_size)
    except OSError:
        _k = None
    if _PARTIAL_GLOSS_CACHE.get("key") != _k:
        # load_term_layers 的 canon/names set 装的是词条键不是分类名，
        # 硬层分类名要从 _meta.layers 取。
        layers = set(meta_layers.get("canon") or []) | \
            set(meta_layers.get("names") or [])
        words = []
        for cat, tbl in data.items():
            if cat.startswith("_") or not isinstance(tbl, dict) \
                    or cat not in layers:
                continue
            for k, v in tbl.items():
                k = str(k)
                if len(k) < 3 or not re.search(r"[A-Za-z]", k):
                    continue
                if not (k[:1].isupper() or k.isupper()):
                    continue
                words.append((k, str(v)))
        _PARTIAL_GLOSS_CACHE = {"key": _k, "words": words}
    out = s
    changed = False
    for k, v in _PARTIAL_GLOSS_CACHE.get("words") or []:
        if k in out:
            out2 = re.sub(rf"(?<!\w){re.escape(k)}(?!\w)", v, out)
            if out2 != out:
                out = out2
                changed = True
    if not changed:
        return None
    # ★ 1.9.19（同步 2.0.22）：残留普通英文词 = 半中半英破碎产物
    # （`Senior Sergeant "Mangun" Potryko` → `Senior 中士 "曼古恩" Potryko`、
    # `Database Applications Engineer` → `Database Applications 工程师`），
    # 人工汉化对同条目是全译。回落原样保留，宁可不翻也不写破碎文本。
    # 型号/全大写缩写（PSO-1、AKMS、SIG、OTs-14-4A）是合法残留，先剥掉再判。
    for w in re.findall(r"[A-Za-z]{3,}", _PARTIAL_MODEL_TOKEN_RE.sub(" ", out)):
        if any(c.islower() for c in w):
            return None
    return out


def termbase_exact_translation(text):
    """整条命中术语库时返回可直接落盘的译名（含首尾引号处理），否则 None。

    源文在引号内时去掉词库译名的外层引号，避免 ““自由团”” 双引号嵌套；
    与 _inline_term_value 同口径。只处理整条精确匹配。
    """
    value = termbase_lookup(text)
    if value is None:
        return None
    s = (text or "").strip()
    quotes = "\"'«»“”‘’"
    if (len(s) >= 2 and s[0] in quotes and s[-1] in quotes
            and len(value) >= 2 and value[0] in quotes
            and value[-1] in quotes):
        value = value[1:-1]
    lead = text[:len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return lead + value + trail


def _layer_categories(meta, name):
    layers = meta.get("layers") if isinstance(meta, dict) else None
    cats = layers.get(name) if isinstance(layers, dict) else None
    if isinstance(cats, str):
        cats = [cats]
    if not isinstance(cats, list):
        cats = _DEFAULT_LAYERS.get(name, [])
    return [str(c) for c in cats if str(c).strip()]


def load_term_layers():
    """返回 v2 四层索引：canon/names/technical/reference + 变体/大小写策略。

    旧版术语库没有 `_meta.layers` 时按原分类回退，导入别人的分类词库也能用。
    """
    data = load_termbase_data()
    meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
    canon_cats = _layer_categories(meta, "canon")
    name_cats = _layer_categories(meta, "names")
    tech_cats = _layer_categories(meta, "technical")
    ref_cats = _layer_categories(meta, "reference")

    def collect(cats):
        keys, table = set(), {}
        for cat in cats:
            t = data.get(cat)
            if not isinstance(t, dict):
                continue
            for k, v in t.items():
                name = str(k).strip()
                if not name or name.startswith("_"):
                    continue
                keys.add(name)
                table[name] = str(v)
        return keys, table

    canon, _ = collect(canon_cats)
    names, _ = collect(name_cats)
    _, technical = collect(tech_cats)
    _, reference = collect(ref_cats)
    variants = meta.get("variants") if isinstance(meta.get("variants"),
                                                 dict) else {}
    variants = {str(k): str(v) for k, v in variants.items()
                if str(k).strip() and str(v).strip()}
    explicit = meta.get("case_sensitive")
    case_sensitive = ({str(k) for k in explicit if str(k).strip()}
                      if isinstance(explicit, list) else set())
    # 人物名与内容型专名按“首字母大小写一致”匹配：英文正文里的
    # doctor/bar/crystal 这类普通词不会被当成专名。
    case_sensitive |= names
    case_sensitive |= {k for k in technical if re.search(r"[A-Za-z]", k)}
    case_sensitive |= {k for k in canon if _RISKY_LATIN_RE.match(k)}
    quote_required = meta.get("quote_required")
    quote_required = ({str(k) for k in quote_required if str(k).strip()}
                      if isinstance(quote_required, list) else set())
    return {"canon": canon, "names": names, "technical": technical,
            "reference": reference, "variants": variants,
            "case_sensitive": case_sensitive,
            "quote_required": quote_required}


def load_hard_glossary_keys():
    """只把 canon 层（阵营/地点/变异体/物品）当硬词；人物名与对话类只做软参考。

    builtin 术语表是扁平表，混入了大量新闻/对话里的普通词和屈折形
    （例如 Информацию→信息、Искренне→火花）。这些词强制替换会把
    句子改坏；按 v2 的 canon 层筛出硬词，普通词不再注入/强制。
    """
    return set(load_term_layers()["canon"])


def load_technical_exact():
    """数据化技术精确表；随包术语库优先，旧包回退到内置常量。"""
    out = dict(_TECHNICAL_EXACT)
    out.update(load_term_layers()["technical"])
    return out


_TERMBASE_STORE = {"key": None, "value": None}


def _termbase_store():
    key = (_TERMBASE_PATH, _GLOSS_BUILTIN, _GLOSS_BUILTIN_LEGACY)
    if _TERMBASE_STORE.get("key") != key:
        _TERMBASE_STORE["value"] = Termbase(
            _TERMBASE_PATH,
            (_GLOSS_BUILTIN, _GLOSS_BUILTIN_LEGACY),
            _TECHNICAL_EXACT,
        )
        _TERMBASE_STORE["key"] = key
    return _TERMBASE_STORE["value"]


def save_user_glossary(set_map, deleted=None, clear_builtin=False):
    """只写用户覆盖层；builtin 永远是随包只读文件。"""
    payload = json.dumps({
        "_format": _GLOSS_LAYER_FORMAT,
        "clear_builtin": bool(clear_builtin),
        "set": dict(set_map or {}),
        "del": sorted(deleted or ()),
    }, ensure_ascii=False, indent=1, sort_keys=True)
    with _JSON_WRITE_LOCK:
        tmp = _GLOSS_USER + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, _GLOSS_USER)


def flatten_glossary(data):
    """把术语表归一成 {原文: 译文}。

    兼容三种文件形态并允许**混合**：
    1) 扁平 {"原文": "译文"}；
    2) 分类名词库 {"分类名": {"原文": "译文"}, ...}（可多层嵌套）；
    3) 上面两种混在一张表里（如 {"a": "b", "武器": {...}}）。

    以 "_" 开头的段（如 _meta）整段跳过。

    注意：旧实现只要表里出现**任何**一个 dict 值，就把整张表当分类表解析，
    于是同一张表里的扁平条目被静默丢弃 —— 实测
    {"普通原文":"普通译文","分类A":{"原文1":"译文1"}} 只剩 {"原文1":...}，
    导入后无提示少条目。这里改成逐键判定，两侧都保留。
    """
    out = {}

    def walk(table, depth=0):
        for key, value in table.items():
            name = str(key).strip()
            if not name or name.startswith("_"):
                continue
            if isinstance(value, dict) and depth < 8:
                walk(value, depth + 1)
            elif not isinstance(value, (dict, list, tuple)):
                out[name] = str(value)

    if isinstance(data, dict):
        walk(data)
    return out



_VENDOR_READY = False


def _load_vendor():
    # 解析器来源: wzyddg/stalker_auto-trans-tool_remake (license: null, 见 THIRD_PARTY_NOTICES.md 第 1 节)
    global _VENDOR_READY
    import types
    if _VENDOR_READY:
        return sys.modules["xRayXmlParser"]
    if not os.path.isfile(_VENDOR_ZIP):
        raise FileNotFoundError("缺少解析器包：%s" % _VENDOR_ZIP)

    pkg_name = "xRayXmlParser"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = []
    pkg.__package__ = pkg_name
    sys.modules[pkg_name] = pkg

    order = ["entityDefinition", "xRayTextAnalyzer",
             "xmlFileGenerator", "xmlxReader"]
    with zipfile.ZipFile(_VENDOR_ZIP) as z:
        sources = {n: z.read("%s/%s.py" % (pkg_name, n)).decode("utf-8")
                   for n in order}
        init_src = z.read("%s/__init__.py" % pkg_name).decode("utf-8")

    for name in order:
        mod = types.ModuleType("%s.%s" % (pkg_name, name))
        mod.__package__ = pkg_name
        sys.modules["%s.%s" % (pkg_name, name)] = mod
        code = compile(sources[name], "%s!%s.py" % (_VENDOR_ZIP, name), "exec")
        exec(code, mod.__dict__)
        setattr(pkg, name, mod)

    code = compile(init_src, "%s!__init__.py" % _VENDOR_ZIP, "exec")
    exec(code, pkg.__dict__)
    _VENDOR_READY = True
    return pkg



class ProbeResult(object):
    def __init__(self, name, url):
        self.name = name
        self.url = url
        self.ok = False
        self.speed = 0.0
        self.got = 0
        self.secs = 0.0
        self.range_ok = False
        self.note = ""
        self.kind = ""


def _host_kind(url):
    host = urllib.parse.urlparse(url).hostname or ""
    if host in ("huggingface.co", "hf.co", "github.com"):
        return "直连"
    return "镜像"


def probe_source(name, url, max_bytes=2 * 1024 * 1024, max_secs=8.0):
    """对单个源做 Range 小样本测速。失败不抛异常，全部塞进 ProbeResult。"""
    res = ProbeResult(name, url)
    res.kind = _host_kind(url)
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Range": "bytes=0-%d" % (max_bytes - 1)})
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=8)
        with resp:
            res.range_ok = (resp.status == 206)
            ctype = resp.headers.get("Content-Type", "")
            if "html" in ctype.lower():
                res.note = "返回网页而非文件"
                return res
            while res.got < max_bytes and (time.time() - t0) < max_secs:
                chunk = resp.read(65536)
                if not chunk:
                    break
                res.got += len(chunk)
        res.secs = max(time.time() - t0, 0.001)
        res.speed = (res.got / 1048576.0) / res.secs
        res.ok = res.got > 256 * 1024
        if not res.ok:
            res.note = "样本过小（%s）" % _fmt_size(res.got)
    except urllib.error.HTTPError as e:
        res.note = "HTTP %s" % e.code
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        res.note = "连接失败：%s" % getattr(e, "reason", e)
    except Exception as e:
        res.note = "%s: %s" % (type(e).__name__, e)
    finally:
        if res.secs == 0.0:
            res.secs = max(time.time() - t0, 0.001)
    return res


def run_probe(sources, log=None, max_workers=12):
    """sources: [(name, url)]；并发测速（上限 max_workers，
    源很多时避免一次性打开几十条连接互相抢带宽），按速度降序返回。"""
    if log:
        log("正在并发实测 %d 个下载源（每源取样 2 MB / 8 秒，最多 %d 并发）…"
            % (len(sources), max_workers))
    results = [None] * len(sources)
    sem = threading.Semaphore(max_workers)

    def worker(i, name, url):
        with sem:
            results[i] = probe_source(name, url)

    threads = []
    for i, (name, url) in enumerate(sources):
        t = threading.Thread(target=worker, args=(i, name, url), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    ok = [r for r in results if r and r.ok]
    bad = [r for r in results if r and not r.ok]
    ok.sort(key=lambda r: r.speed, reverse=True)
    if log:
        for r in ok:
            tag = "直连" if r.kind == "直连" else "镜像"
            log("  ✓ [%s] %s：%.2f MB/s（%s）" % (tag, r.name, r.speed,
                "支持断点" if r.range_ok else "不支持 Range"))
        for r in bad:
            log("  ✗ %s：%s" % (r.name, r.note))
    return ok + bad


def detect_environment(results):
    """根据测速结果归纳网络环境。"""
    official_ok = any(r.ok and r.kind == "直连" for r in results)
    mirror_ok = any(r.ok and r.kind == "镜像" for r in results)
    if official_ok and not mirror_ok:
        return "直连网络：官方源可达，已按实测速度选源"
    if official_ok and mirror_ok:
        return "混合网络：官方源与镜像均可达，已自动选择最快源"
    if mirror_ok:
        return "受限网络：官方源不可达，已自动切换国内镜像"
    return "所有下载源均不可达，请检查网络或代理设置"



class DownloadCancelled(Exception):
    pass




def download_one(sources, target, log, progress=None, cancel_event=None,
                 retries_per_source=3, verify=None):
    """按 sources 顺序尝试；.part 断点续传；全部源失败才抛异常。

    progress(done, total, speed_mbps, eta_sec, src_name)
    verify(part_path) -> digest：**每个源下载完成后**立即校验，失败即删文件并
    换下一个源（文档一直这么写，1.6.0 之前实际只是删文件让用户手动重试）。
    返回校验得到的 digest；未提供 verify 时返回 True。
    """
    part = target + ".part"
    total = None
    last_err = None
    last_invalid = None

    for name, url in sources:
        for attempt in range(1, retries_per_source + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled()
            have = os.path.getsize(part) if os.path.isfile(part) else 0
            try:
                headers = {"User-Agent": UA}
                if have:
                    headers["Range"] = "bytes=%d-" % have
                req = urllib.request.Request(url, headers=headers)
                resp = urllib.request.urlopen(req, timeout=30)
                with resp:
                    ctype = resp.headers.get("Content-Type", "")
                    if "html" in ctype.lower():
                        raise IOError("%s 返回了网页而非文件" % name)
                    if have and resp.status == 206:
                        cr = resp.headers.get("Content-Range", "")
                        if "/" in cr:
                            total = int(cr.rsplit("/", 1)[1])
                        mode = "ab"
                    else:
                        if have:
                            log("  %s 不支持断点，重新开始下载" % name)
                        total = int(resp.headers.get("Content-Length", 0)) or None
                        have = 0
                        mode = "wb"
                    done = have
                    t0 = time.time()
                    last_ui = 0.0
                    with open(part, mode) as f:
                        while True:
                            if cancel_event is not None and cancel_event.is_set():
                                raise DownloadCancelled()
                            chunk = resp.read(1024 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            done += len(chunk)
                            now = time.time()
                            if progress and (now - last_ui) > 0.4:
                                speed = (done - have) / 1048576.0 / max(now - t0, 0.001)
                                eta = ((total - done) / 1048576.0 / speed
                                       if total and speed > 0 else None)
                                progress(done, total, speed, eta, name)
                                last_ui = now
                    if progress:
                        progress(done, total, 0, 0, name)
                final_size = os.path.getsize(part)
                if total and final_size != total:
                    raise IOError("下载不完整：%d / %d 字节" % (final_size, total))
                digest = None
                if verify is not None:
                    digest = verify(part)
                os.replace(part, target)
                return digest if verify is not None else True
            except DownloadCancelled:
                raise
            except InvalidArtifact as e:
                # verify 内部已删除坏文件；本源的断点不再有意义，换下一个源
                last_invalid = "%s（第 %d 次）：校验未通过：%s" % (
                    name, attempt, e)
                log("  下载产物校验未通过（%s），已删除，切换下一个源…" % e)
                break
            except Exception as e:
                last_err = "%s（第 %d 次）：%s" % (name, attempt, e)
                log("  下载中断，%s" % last_err)
                if isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500:
                    log("  该源返回 HTTP %s，不再重试，直接换源" % e.code)
                    break
                if attempt < retries_per_source:
                    time.sleep(min(2 * attempt, 5))
        log("  切换下一个下载源（已保留断点 %s）…" % _fmt_size(
            os.path.getsize(part) if os.path.isfile(part) else 0))
    if last_invalid:
        raise InvalidArtifact("所有下载源均未通过校验；最后错误：%s"
                              % last_invalid)
    raise IOError("所有下载源均失败；最后错误：%s" % last_err)


def _extract_zip_into(zip_path, dest_dir):
    """解压到临时目录，再逐项移入 dest_dir（同名项先删）。

    为什么不在 dest_dir 上直接 extractall：中断会留下"半套文件"，而按文件名
    前缀做的存在性检查会把它当成已安装（CUDA 运行时就是这么坏掉的）。
    """
    os.makedirs(dest_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="ai_translate_zip_", dir=dest_dir)
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        for item in sorted(os.listdir(tmp)):
            src = os.path.join(tmp, item)
            dst = os.path.join(dest_dir, item)
            if os.path.exists(dst):
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                else:
                    os.remove(dst)
            shutil.move(src, dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def extract_zip(zip_path, dest_dir, marker, log):
    """解压运行库 zip，返回 marker 文件（llama-server.exe）的完整路径。"""
    if os.path.isdir(dest_dir):
        for root, _dirs, files in os.walk(dest_dir):
            if marker in files:
                return os.path.join(root, marker)
    _extract_zip_into(zip_path, dest_dir)
    marker_path = None
    for root, _dirs, files in os.walk(dest_dir):
        if marker in files:
            marker_path = os.path.join(root, marker)
            break
    if not marker_path:
        raise IOError("压缩包中找不到 %s" % marker)
    log("  解压完成：%s" % marker_path)
    return marker_path



class InvalidArtifact(Exception):
    """下载产物校验未通过（不是预期文件，或与上次记录的内容不一致）。"""
    pass


_GGUF_TYPES = {0: ("B", 1), 1: ("b", 1), 2: ("H", 2), 3: ("h", 2),
               4: ("I", 4), 5: ("i", 4), 6: ("f", 4), 7: ("?", 1),
               10: ("Q", 8), 11: ("q", 8), 12: ("d", 8)}


def read_gguf_meta(path, want=("tokenizer.chat_template",
                               "tokenizer.ggml.eos_token_id",
                               "general.name", "general.architecture"),
                   max_elements=4000000, head_bytes=96 * 1024 * 1024):
    """读 GGUF 头部 KV 元数据（只读头部，不加载张量），返回 dict。

    为什么要读它：某些 GGUF 的 tokenizer 元数据不完整 —— 实测
    `hy-mt2-1.8b-q8_0.gguf` 既没有 `tokenizer.chat_template`，EOS 又是 3
    这个不在 EOG 集合里的值，llama.cpp 会打
    `special_eos_id is not in special_eog_ids`，于是模型**永远不吐 EOS**：
    一句 "Hello." 也能连生成 1285 个 token（CPU 上 178 秒）直到客户端超时。
    这种模型拿去翻几千条文本，结果就是每条都超时、整批白跑；启动时读一眼
    元数据就能提前告警。

    实现要点（都踩过坑）：
      * KV 必须**从头顺序走**（值是变长的），无法靠偏移量直接跳到某个键；
        而 `tokenizer.chat_template` 通常排在词表数组之后，所以要把头部
        整块读进内存再解析 —— 逐字段读文件在 10 万级词表上会明显变慢；
      * 读到的结果带一个 `"__partial__"` 标记：解析中途失败/缓冲区不够时
        置 True。**调用方必须据此区分"确实没有模板"与"没解析到"** ——
        早先版本预算耗尽后静默返回部分结果，导致有模板的 7B 被误报
        "未内置对话模板"。
    """
    import struct
    out = {}

    def rd_str(f):
        n = struct.unpack("<Q", f.read(8))[0]
        if n > 100 * 1024 * 1024:
            raise ValueError("KV 字符串长度异常")
        return f.read(n).decode("utf-8", "replace")

    budget = [max_elements]

    def rd_val(f, t):
        if t == 8:
            return rd_str(f)
        if t == 9:
            et = struct.unpack("<I", f.read(4))[0]
            n = struct.unpack("<Q", f.read(8))[0]
            if budget[0] - n < 0:
                raise ValueError("KV 数组元素数超出预算")
            budget[0] -= n
            if n > 8:
                for _ in range(n):
                    rd_val(f, et)
                return "<array %d>" % n
            return [rd_val(f, et) for _ in range(n)]
        fmt, size = _GGUF_TYPES[t]
        return struct.unpack("<" + fmt, f.read(size))[0]

    complete = False
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(min(size, head_bytes))
        f = io.BytesIO(head)
        if f.read(4) != b"GGUF":
            return out
        struct.unpack("<I", f.read(4))[0]
        struct.unpack("<Q", f.read(8))[0]
        n_kv = struct.unpack("<Q", f.read(8))[0]
        for _ in range(n_kv):
            key = rd_str(f)
            t = struct.unpack("<I", f.read(4))[0]
            if key in want:
                out[key] = rd_val(f, t)
            else:
                rd_val(f, t)
        complete = True
    except Exception:
        pass
    if not complete:
        out["__partial__"] = True
    return out




def sha256_file(path):
    return _core_sha256_file(path)


def verify_artifact(path, task, log, known_hashes=None):
    """校验刚下载完的产物，并把它的 sha256 返回（由调用方记进配置）。

    校验顺序与依据：
      * 官方 SHA-256：任务里登记了 sha256 时，必须逐字节一致。运行库哈希来自
        llama.cpp b11612 GitHub Release 的 asset digest，模型哈希来自 HuggingFace
        LFS oid；镜像只负责传输，不能改变校验值。
      * GGUF：magic 必须是 b"GGUF"、版本字段合理、体积与官方字节数一致；
      * 运行库 zip：必须是合法 zip、内部含 llama-server.exe、且那是个 PE(MZ)；
      * 跨次一致性：把 sha256 记进 config；配置里的旧哈希只作提示，官方哈希优先。

    没有登记官方哈希的产物退化为结构校验 + 跨次一致性，并在日志里明确写出
    这一边界。校验失败的文件一律删除并抛 InvalidArtifact。
    """

    def fail(msg):
        try:
            os.remove(path)
        except OSError:
            pass
        raise InvalidArtifact(msg)

    try:
        size = os.path.getsize(path)
    except OSError as e:
        fail("文件读不到：%s" % e)
    if size <= 0:
        fail("文件为空")

    if task.get("kind") == "zip":
        marker = task.get("filename") or ""
        prefix = (task.get("name_prefix") or "").lower()
        problem = None
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                names = [n.replace("\\", "/") for n in z.namelist()]
                if marker:
                    hits = [n for n in names if n.endswith(marker)]
                else:
                    hits = [n for n in names
                            if os.path.basename(n).lower().startswith(prefix)]
                if not hits:
                    problem = "压缩包内找不到 %s" % (marker or prefix + "*")
                else:
                    with z.open(hits[0]) as fp:
                        head = fp.read(2)
                    if marker and head != b"MZ":
                        problem = "%s 不是可执行文件（PE 头缺失）" % marker
        else:
            problem = "不是有效的 zip 文件"
        if problem:
            fail(problem)
    elif task.get("kind") == "model":
        try:
            with open(path, "rb") as f:
                head = f.read(8)
        except OSError as e:
            fail("文件读不到：%s" % e)
        if head[:4] != b"GGUF":
            fail("不是 GGUF 模型（magic=%r）" % head[:4])
        version = int.from_bytes(head[4:8], "little")
        if version not in (1, 2, 3):
            fail("GGUF 版本号异常：%d" % version)
        official_size = int(task.get("size_bytes") or 0)
        if official_size and size != official_size:
            fail("体积与官方记录不一致（官方 %d B，实际 %d B）"
                 % (official_size, size))
        hint = float(task.get("size_hint") or 0)
        if hint and not (0.5 * hint <= size / float(1 << 30) <= 2.5 * hint):
            log("  注意：%s 体积 %.2f GB 与预期 %.2f GB 差异较大，"
                "请确认下载的是同一版本" % (os.path.basename(path),
                                    size / float(1 << 30), hint), "warn")

    try:
        digest = sha256_file(path)
    except OSError as e:
        fail("计算 sha256 失败：%s" % e)
    key = task.get("filename") or os.path.basename(path)
    prev = (known_hashes or {}).get(key)
    official = (task.get("sha256") or "").strip().lower()
    if official:
        if digest != official:
            fail("官方 SHA-256 不匹配（期望 %s，实际 %s）"
                 % (official, digest))
        if prev and prev != digest:
            log("  配置里的旧哈希与官方不一致，已按官方值更新", "warn")
        log("  官方 SHA-256 校验通过：%s · %s…"
            % (os.path.basename(path), digest[:16]))
    else:
        if prev and prev != digest:
            fail("与上次记录的哈希不一致（上次 %s…，本次 %s…）"
                 % (prev[:12], digest[:12]))
        log("  未登记官方哈希：仅做结构校验与跨次一致性", "warn")
    return digest



class LlamaServer(object):
    def __init__(self, exe, model, variant, log, ctx_size=8192, tier="",
                 gpu_layers=99, parallel=1):
        self.exe = exe
        self.model = model
        self.variant = variant
        self.log = log
        self.ctx_size = int(ctx_size)
        self.parallel = max(1, min(4, int(parallel)))
        self.tier = tier
        self.gpu_layers = 0 if variant == "cpu" else int(gpu_layers)
        self.proc = None
        self.port = None
        self.base = None
        self._atexit = False
        self.log_file = None
        self._log_fp = None
        self.gpu_verified = False
        self.health_warn = ""
        self.prompt_extra = ""

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def _verify_gpu(self):
        """从运行日志核实 GPU 是否真的在工作（offload 层数 / 后端名）。"""
        if self.variant == "cpu" or not self.log_file:
            return True
        try:
            with open(self.log_file, "rb") as f:
                f.seek(max(0, os.path.getsize(self.log_file) - 1048576))
                tail = f.read().decode("utf-8", "ignore")
        except OSError:
            return False
        low = tail.lower()
        self.gpu_offloaded = None
        on_gpu = total = None
        m = re.search(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers?\s+to\s+gpu", low)
        if m:
            on_gpu, total = int(m.group(1)), int(m.group(2))
        else:
            dev_pat = {"cuda": r"assigned to device cuda\d+",
                       "cuda13": r"assigned to device cuda\d+",
                       "vulkan": r"assigned to device vulkan"}.get(self.variant)
            if dev_pat:
                on_gpu = len(re.findall(dev_pat, low))
                total = on_gpu + len(re.findall(
                    r"assigned to device cpu", low))
        dev_ok = False
        if self.variant in ("cuda", "cuda13"):
            dev_ok = ("using device cuda" in low
                      or "assigned to device cuda" in low)
        elif self.variant == "vulkan":
            dev_ok = ("vulkan" in low and "device" in low)
        self.gpu_verified = dev_ok and (on_gpu or 0) > 0
        if self.gpu_verified:
            if total:
                self.gpu_offloaded = (on_gpu, total)
            self.log("GPU 加速已生效（%s：%s 层在显卡上）"
                     % (self.variant,
                        ("%d/%d" % (on_gpu, total)) if total else str(on_gpu)))
        else:
            self.log("警告：选择了 %s 后端，但未确认层已上 GPU；"
                     "若推理仍很慢，通常是缺 CUDA 运行时或驱动问题，"
                     "请改选 CPU/Vulkan 或在下载管理补装运行库"
                     % self.variant, "warn")
        return self.gpu_verified

    def _pick_port(self):
        """取一个本机空闲端口。

        做法是"绑定 0 → 读回端口 → 立刻关闭"，随后交由子进程监听，两步之间
        存在端口被别的进程抢走的窗口（TOCTOU）。这里不做复杂规避，而是让
        start() 在子进程提前退出时换端口重试一次 —— 比信号量/句柄继承方案
        简单且足够。
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
        finally:
            s.close()

    def start(self, ready_event_log=None, timeout_sec=900):
        """启动引擎并等 /health 就绪。

        失败路径**必须**把已拉起的子进程收掉：否则健康检查超时/提前退出后
        会留下一个占着显存和内存的孤儿 llama-server，"再点一次启动"就变成
        两个进程抢显存。端口被抢占属可恢复失败，换端口重试一次。
        """
        if self.is_running():
            return True
        last_err = None
        for attempt in (1, 2):
            try:
                return self._start_once(timeout_sec, attempt)
            except IOError as e:
                last_err = e
                self._kill_proc()
                if attempt == 1 and "提前退出" in str(e):
                    self.log("端口可能被占用，换端口重试一次…", "warn")
                    continue
                raise
        raise last_err

    def _start_once(self, timeout_sec, attempt):
        self.port = self._pick_port()
        self.base = "http://127.0.0.1:%d" % self.port

        meta = read_gguf_meta(self.model)
        if meta:
            name = meta.get("general.name") or os.path.basename(self.model)
            if meta.get("__partial__"):
                self.log("模型元数据未完整读出（%s），跳过对话模板检查" % name,
                         "warn")
            elif not meta.get("tokenizer.chat_template"):
                self.log("警告：该 GGUF 未内置对话模板（tokenizer.chat_template），"
                         "翻译请求会退化为纯文本续写，可能出现停不下来/串话；"
                         "建议改用官方发布的 GGUF", "warn")
            else:
                self.log("模型 %s：内置对话模板 %d 字符，EOS=%s"
                         % (name, len(meta["tokenizer.chat_template"]),
                            meta.get("tokenizer.ggml.eos_token_id")), "dim")

        args = [self.exe,
                "--model", self.model,
                "--host", "127.0.0.1",
                "--port", str(self.port),
                "--ctx-size", str(self.ctx_size),
                "--n-gpu-layers", str(self.gpu_layers),
                # 翻译请求是串行的：1 个槽位足够。新版 llama-server 的
                # auto 会给 4 个槽位（每槽一套 16K 上下文），白占显存/内存。
                "--parallel", str(self.parallel),
                "--temp", "0.1"]
        if self.variant in GPU_VARIANTS:
            # 只要 GPU 识别所需的 I 级行（using device / offloaded N/M layers）。
            # 原来的 --verbose 把 verbosity 开到最大：实测一次运行 25 MB / 28 万行，
            # 请求级 debug 还持续写盘，拖慢整机。
            args += ["-lv", "4"]
        log_file = os.path.join(_LOGS_DIR, "server_%d.log" % int(time.time()))
        self.log_file = log_file
        self._log_fp = open(log_file, "wb")
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags = subprocess.CREATE_NO_WINDOW
        backend_name = {"cpu": "CPU", "cuda": "CUDA 12.4/GPU",
                        "cuda13": "CUDA 13.4/GPU",
                        "vulkan": "Vulkan/GPU"}.get(self.variant, self.variant)
        self.log("启动 llama-server（%s，%s 档，%d 层 GPU offload，"
                 "上下文 %d token），端口 %d"
                 % (backend_name, self.tier or "-", self.gpu_layers,
                    self.ctx_size, self.port))
        self.log("运行日志：%s" % log_file)
        self.proc = subprocess.Popen(
            args, cwd=os.path.dirname(self.exe), stdout=self._log_fp,
            stderr=subprocess.STDOUT, creationflags=flags)
        # 把引擎放进 kill-on-close 作业：工具集被任务管理器强杀时，
        # 内核会连带终止它，不再留下占着显存的孤儿（见 _win 注释）。
        if os.name == "nt" and not _assign_server_to_kill_job(self.proc):
            self.log("提示：未能把引擎纳入系统作业托管；若工具集被强杀，"
                     "引擎可能残留（下次启动会自动清理）", "dim")
        if not self._atexit:
            atexit.register(self.stop)
            self._atexit = True

        t0 = time.time()
        health_url = self.base + "/health"
        last_err = ""
        while time.time() - t0 < timeout_sec:
            if self.proc.poll() is not None:
                raise IOError("llama-server 提前退出（返回码 %s），详见 %s"
                              % (self.proc.returncode, log_file))
            try:
                with urllib.request.urlopen(health_url, timeout=5) as r:
                    if r.status == 200:
                        self.log("引擎就绪（用时 %.0f 秒）" % (time.time() - t0))
                        self._verify_gpu()
                        self.self_check()
                        return True
                    try:
                        data = json.loads(r.read().decode("utf-8", "ignore"))
                        last_err = str(data.get("error", ""))[:120]
                    except Exception:
                        pass
            except Exception as e:
                last_err = str(e)
            time.sleep(1.5)
        raise IOError("等待引擎就绪超时（%s）" % (last_err or "health 无响应"))

    def translate(self, text, src_lang, tgt_lang, glossary_hits=None,
                  timeout_sec=180, max_tokens=None, use_extra=True,
                  extra_sys=None, temperature=None, context=None,
                  style=None):
        """单条翻译请求。被 max_tokens 截断时抛 OutputTruncated（不重试）。

        提示词里的语言用**全名**（"俄语"）而不是语言码（"ru"）：小模型对
        "将下面的ru文本翻译为简体中文"这类混排会偶发把 ru 当成待译内容。
        """
        if not self.is_running():
            raise IOError("引擎未运行")
        if src_lang in ("auto", "text"):
            src_lang = detect_text_lang(text) or src_lang
        src = LANG_FULL.get(src_lang)
        tgt = LANG_FULL.get(tgt_lang, tgt_lang)
        sys_msg = (TRANSLATION_SYS_PROMPT_EN if src_lang == "eng"
                   else TRANSLATION_SYS_PROMPT)
        extra = (getattr(self, "prompt_extra", "") or "").strip() \
            if use_extra else ""
        if extra:
            sys_msg = sys_msg + "\n补充要求（优先遵守）：\n" + extra
        if extra_sys:
            sys_msg = sys_msg + "\n补充要求（优先遵守）：\n" + extra_sys
        if src and src_lang not in ("auto", "text"):
            user_msg = "将下面的%s文本翻译为%s：\n%s" % (src, tgt, text)
        else:
            user_msg = "将下面文本翻译为%s：\n%s" % (tgt, text)
        if context:
            user_msg = ("上下文（仅供理解，不要翻译）：" + chr(10)
                        + str(context) + chr(10) + chr(10) + user_msg)
        if glossary_hits:
            block, _used = glossary_hint_block(glossary_hits)
            if block:
                user_msg = block + "\n\n" + user_msg
        if max_tokens is None:
            # ★ 1.9.12：输出预算按本次输入长度估算（短句不虚设、
            # 长段不截断、上限受窗口 0.6 硬顶），不再固定 ctx*0.6。
            max_tokens = budget_max_tokens(len(text or ""), self.ctx_size)
        payload = json.dumps({
            "model": "hy-mt2",
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
            "temperature": (0.1 if temperature is None
                            else float(temperature)),
            "max_tokens": max_tokens,
            "stream": False,
        }).encode("utf-8")
        last_err = None
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    self.base + "/v1/chat/completions",
                    data=payload,
                    headers={"Content-Type": "application/json",
                             "User-Agent": UA},
                    method="POST")
                with urllib.request.urlopen(req, timeout=timeout_sec) as r:
                    data = json.loads(r.read().decode("utf-8", "ignore"))
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
                time.sleep(1.0)
                continue
            choices = data.get("choices") or []
            if not choices:
                last_err = "响应缺少 choices"
                time.sleep(1.0)
                continue
            choice = choices[0]
            out = (choice.get("message") or {}).get("content", "").strip()
            # 小模型代码端加固：剥外壳（栅栏/标签/引号），不依赖提示词自觉
            out, _wrap = strip_output_wrappers(out)
            if not out:
                last_err = "空输出"
                time.sleep(1.0)
                continue
            if choice.get("finish_reason") == "length":
                raise OutputTruncated(
                    "输出达到 max_tokens(%d) 被截断" % max_tokens)
            return out
        raise IOError("翻译请求失败：%s" % last_err)

    def self_check(self):
        """启动自检：真跑一条极短翻译，确认模型能正常收尾。

        实测背景：tokenizer 元数据不完整的 GGUF 永远不吐 EOS，一句 "Hello."
        能连生成上千 token 直到请求超时；真拿它去翻几千条文本，就是全部
        超时、白跑一整轮。这里用 16 个 token 的上限提前把它试出来，并把
        原因讲清楚。

        **不阻断启动**（只告警）：避免误伤行为特殊但可用的模型；用户看到
        告警后可以自行换模型或改用其他后端。
        """
        tokenizer_warn = (
            "模型无法正常收尾（输出撞满上限仍未结束）：该 GGUF 的 tokenizer "
            "元数据可能不完整（缺 EOS/EOG 或对话模板），直接翻译会让每条"
            "文本都超时")

        def probe(max_tokens, timeout):
            return self.translate("Hello.", "eng", "chs",
                                  timeout_sec=timeout, max_tokens=max_tokens,
                                  use_extra=False)

        try:
            out = probe(16, 120)
        except OutputTruncated:
            self.health_warn = tokenizer_warn
        except Exception:
            # 16 token 的"空输出"不等于模型坏了：推理型 GGUF 会把预算全花在
            # reasoning_content 上，content 为空（MiniCPM5-2B 实测如此，
            # 256 token 时能正常返回"你好。"）。用更大预算复核一次再下结论。
            try:
                out = probe(256, 180)
            except OutputTruncated:
                self.health_warn = tokenizer_warn
            except Exception as e:
                self.health_warn = (
                    "启动自检请求失败：%s: %s（不影响启动；推理型模型常把小预算"
                    "耗在思考上而返回空内容，可忽略此告警）"
                    % (type(e).__name__, e))
            else:
                self.health_warn = ""
                self.log("启动自检通过（16 token 不足，256 token 复核成功）：%r"
                         % (out[:24] or "(空)"))
                return True
        else:
            self.health_warn = ""
            self.log("启动自检通过：%r" % (out[:24] or "(空)"))
            return True
        self.log("启动自检未通过：" + self.health_warn, "warn")
        return False

    def _kill_proc(self):
        """杀掉子进程（含其子进程树）并关闭日志句柄。可重复调用。"""
        if self.proc is not None and self.proc.poll() is None:
            pid = self.proc.pid
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        if self.proc is not None:
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass
            self.proc = None
        fp, self._log_fp = self._log_fp, None
        if fp is not None:
            try:
                fp.close()
            except Exception:
                pass

    def translate_batch(self, lines, src_lang, tgt_lang, glossary_hits=None,
                        timeout_sec=180, max_tokens=None,
                        temperature=None):
        """连续对话批量翻译：输入若干源文行，输出带 [n] 编号的译文。"""
        if not self.is_running():
            raise IOError("引擎未运行")
        if src_lang in ("auto", "text"):
            src_lang = detect_text_lang(lines[0] if lines else "") or src_lang
        tgt = LANG_FULL.get(tgt_lang, tgt_lang)
        numbered = chr(10).join("[%d] %s" % (i + 1, line)
                                for i, line in enumerate(lines))
        sys_msg = TRANSLATION_BATCH_SYS_PROMPT
        extra = (getattr(self, "prompt_extra", "") or "").strip()
        if extra:
            sys_msg += chr(10) + "补充要求（优先遵守）：" + chr(10) + extra
        user_msg = ("把下面的连续对话逐行翻译为%s：" % tgt
                    + chr(10) + numbered)
        if glossary_hits:
            block, _used = glossary_hint_block(glossary_hits)
            if block:
                user_msg = block + chr(10) + chr(10) + user_msg
        if max_tokens is None:
            # ★ 1.9.12：输出预算按本次输入长度估算（批量按最长行计）。
            max_tokens = budget_max_tokens(
                max((len(x) for x in lines), default=0), self.ctx_size)
        payload = json.dumps({
            "model": "hy-mt2",
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
            "temperature": (0.1 if temperature is None
                            else float(temperature)),
            "max_tokens": max_tokens,
            "stream": False,
        }).encode("utf-8")
        last_err = None
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    self.base + "/v1/chat/completions",
                    data=payload,
                    headers={"Content-Type": "application/json",
                             "User-Agent": UA},
                    method="POST")
                with urllib.request.urlopen(req, timeout=timeout_sec) as r:
                    data = json.loads(r.read().decode("utf-8", "ignore"))
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
                time.sleep(1.0)
                continue
            choices = data.get("choices") or []
            if not choices:
                last_err = str(data.get("error", ""))[:120]
                time.sleep(1.0)
                continue
            choice = choices[0]
            content = ((choice.get("message") or {}).get("content") or "").strip()
            # 小模型代码端加固：剥外壳（栅栏/标签），不依赖提示词自觉
            content, _wrap = strip_output_wrappers(content)
            if choice.get("finish_reason") == "length":
                raise OutputTruncated("批量输出被截断")
            return content
        raise IOError("批量翻译请求失败（%s）" % (last_err or "无响应"))

    def stop(self):
        if self.proc is None and self._log_fp is None:
            return
        self._kill_proc()
        self.log("引擎已停止")



def estimate_tokens(text):
    """保守的多语言 token 估算（混元 12 万词表）：
    汉字/假名/谚文约 1 token/字；拉丁/西里尔字母文本约 0.45 token/字符，
    按 0.55 取偏安全上界；标点空白计入长度。"""
    if not text:
        return 0
    cjk = 0
    for c in text:
        o = ord(c)
        if (0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF
                or 0xAC00 <= o <= 0xD7AF):
            cjk += 1
    other = len(text) - cjk
    return int(cjk * 1.0 + other * 0.55) + 1


def safe_chunk_chars(ctx_size):
    """单次请求可送的最大源文字符数（与画像中的"约 N 字符/段"一致）：
    8192 → 约 3768；16384 → 约 7536。"""
    return int(ctx_size * 0.46)


_SENT_SPLIT = re.compile(r"([。！？!?…\n；;])")
_CLAUSE_SPLIT = re.compile(r"([，,：:])")


_PLACEHOLDER_RE = re.compile(r"\$\$[TR]\d+\$\$")
_ANY_PLACEHOLDER_RE = re.compile(r"\$\$[A-Za-z0-9_]+\$\$")

# 运行期占位符：脚本变量/格式符/控制符。**必须先 mask 再送模型**：
# 提示词要求保留并不可靠，实测模型会把 $level/$name 当成普通英文词译成
# $地点/$名称，导致脚本变量替换失效（news_data.script 等直接引用这些条目）。
_RUNTIME_TOKEN_RE = re.compile(
    r"(\$\$[A-Za-z0-9_]+\$\$"          # $$ACTION$$
    r"|\$[A-Za-z_][A-Za-z0-9_]*"        # $level / $name
    r"|%c\[[^\]]*\]"                    # %c[255,1,255,255]
    r"|%[-+ #0-9.*$]*[A-Za-z]"          # %s / %d / %1$s
    r"|\b[rsg][0-9]?_[a-z0-9_]+\b)")    # r2_details_opt / s_light 等技术后缀
# 换行/制表不再当“严格占位符”：模型少写一个 \n 就把整条退回原文，
# 实测 138 条多行描述因此漏译。送翻前换成真实换行，译后统一写回 \n。


def mask_runtime_tokens(text):
    """把运行期占位符换成 $$R0$$…，返回 (masked, mapping)。"""
    mapping = {}

    def repl(m):
        ph = "$$R%d$$" % len(mapping)
        mapping[ph] = m.group(0)
        return ph

    return _RUNTIME_TOKEN_RE.sub(repl, text), mapping


def restore_runtime_tokens(text, mapping):
    """回填占位符；任何一个不是恰好出现一次就返回 None（调用方保留原文）。"""
    for ph in mapping:
        if text.count(ph) != 1:
            return None
    for ph, tok in mapping.items():
        text = text.replace(ph, tok)
    return text


# ★ 小模型代码端加固：提示词"只输出译文本身"守不住——小模型常给输出加壳
# （代码栅栏、"译文："前缀、整串引号包裹）。壳不剥，占位符/术语闸门会把
# $$R0$$ 连壳一起判失败，或让译文带栅栏直接落盘。在拿到响应的第一时刻
# 做确定性剥离，属于代码级闸门，不依赖模型自觉。（与 core/pipeline.py
# strip_output_wrappers 同构，1.9x 包为自包含 vendored 副本。）
_FENCE_RE_19 = re.compile(
    r"^\s*```[A-Za-z0-9_-]*[ \t]*\r?\n(.*?)[ \t]*\r?\n?```\s*$", re.DOTALL)
_LABEL_RE_19 = re.compile(
    r"^\s*(?:译文|翻译|翻译结果|结果|输出|译文如下|翻译如下|以下是译文|"
    r"以下是翻译|翻译结果如下|"
    r"Translation(?: result| output)?|Output|"
    r"Here(?:'s| is)(?: the)? translation(?: of[^:：\n]{0,40})?)\s*[:：]\s*",
    re.IGNORECASE)
_WRAP_PAIRS_19 = {"“": "”", "«": "»", "「": "」", "『": "』",
                  '"': '"', "'": "'"}


def _quote_class_19(ch):
    if ch in "“”\"«»「」『』":
        return "dquote"
    if ch in "‘’'":
        return "squote"
    return ""


def strip_output_wrappers(text, source=""):
    """剥离小模型在译文外常加的确定性外壳，返回 (cleaned, notes)。

    只做零风险剥离：整体代码栅栏、"译文："类前缀（最多两层）、
    整串引号包裹（源文以同类引号开头时不动）。不做内容级改写。
    """
    if not text:
        return text, []
    cleaned = text
    notes = []
    fence = _FENCE_RE_19.match(cleaned)
    if fence:
        cleaned = fence.group(1)
        notes.append("fence")
    for _ in range(2):
        stripped = _LABEL_RE_19.sub("", cleaned)
        if stripped == cleaned:
            break
        cleaned = stripped
        notes.append("label")
    if len(cleaned) >= 2:
        first = cleaned[0]
        closing = _WRAP_PAIRS_19.get(first)
        if closing and cleaned[-1] == closing:
            if _quote_class_19((source or "").strip()[:1]) != _quote_class_19(first):
                cleaned = cleaned[1:-1]
                notes.append("quote")
    return cleaned, notes


def runtime_token_counts(text):
    return Counter(_RUNTIME_TOKEN_RE.findall(text or ""))


def runtime_tokens_match(source, output):
    return (_RUNTIME_TOKEN_RE.findall(source or "")
            == _RUNTIME_TOKEN_RE.findall(output or ""))


def partial_source_review(source, output):
    if not has_cjk(output or ""):
        return []
    src = _RUNTIME_TOKEN_RE.sub(" ", source or "")
    src = re.sub(r"\b[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+\b", " ", src)
    words = set(re.findall(r"[A-Za-z]{2,}|[А-Яа-яЁё]{2,}", src.lower()))
    if len(words) < 2:
        return []
    dst = (output or "").lower()
    retained = {word for word in words
                if re.search(r"(?<![a-zа-яё])" + re.escape(word)
                             + r"(?![a-zа-яё])", dst)}
    return sorted(words - retained) if retained else []


def semantic_role_issues(source, output, lang=""):
    src = re.sub(r"\s+", " ", source or "").strip()
    dst = re.sub(r"\s+", " ", output or "").strip()
    if not src or not dst:
        return []
    low = src.lower()
    issues = []
    src_first = bool(re.search(
        r"(?<![a-zа-яё])(?:i|me|my|mine|we|us|our|я|мне|меня|мой|мы|нам|нас)"
        r"(?![a-zа-яё])", low))
    src_second = bool(re.search(
        r"(?<![a-zа-яё])(?:you|your|yours|ты|тебе|тебя|твой|вы|вам|вас)"
        r"(?![a-zа-яё])", low))
    dst_first = bool(re.search(r"我|我们|咱", dst))
    dst_second = bool(re.search(r"你|你们|您", dst))
    if src_first and not src_second and dst_second and not dst_first:
        issues.append("人称从第一方反转成第二方")
    if src_second and not src_first and dst_first and not dst_second:
        issues.append("人称从第二方反转成第一方")

    def _direction(text, language):
        s = text.lower()
        if language == "eng" or re.search(r"[a-z]", s):
            if re.search(r"\b(?:i|we)(?:['’]ll|\s+will)?\s+"
                         r"(?:pay|give|bring|hand)\s+you\b", s):
                return "1_to_2"
            if re.search(r"\byou\s+(?:(?:must|should|need to|have to)\s+)?"
                         r"(?:pay|give|bring|hand)\s+(?:me|us)\b", s):
                return "2_to_1"
            if re.search(r"\b(?:please\s+)?(?:pay|give|bring|hand)\s+"
                         r"(?:me|us)\b", s):
                return "2_to_1"
        if re.search(r"\bя\s+(?:(?:должен|должна|должно)\s+)?"
                     r"(?:заплачу|заплатить|дам|дать|принесу|принести|"
                     r"отдам|отдать)\s+тебе\b", s):
            return "1_to_2"
        if re.search(r"\bты\s+(?:(?:должен|должна|должно|должны)\s+)?"
                     r"(?:заплатишь|заплатить|дашь|дать|принесешь|принести|"
                     r"отдашь|отдать)\s+мне\b", s):
            return "2_to_1"
        if re.search(r"\b(?:отнеси|принеси|отдай)\s+мне\b", s):
            return "2_to_1"
        if re.search(r"我(?:会|将|要)?(?:付给|交给|带给)你", text):
            return "1_to_2"
        if re.search(r"你(?:必须|需要|要)?(?:付给我|交给我|带给我)", text):
            return "2_to_1"
        if re.search(r"(?:付给我|交给我|带给我)", text):
            return "2_to_1"
        return ""

    src_dir = _direction(src, lang)
    dst_dir = _direction(dst, "zh")
    if src_dir and dst_dir and src_dir != dst_dir:
        issues.append("交接/付款方向反转")
    return issues


_GLUE_STRIP_RE = re.compile(
    r"\$\$R\d+\$\$|%c\[[^\]]*\]|\$clr_\d+|\$[A-Za-z_][A-Za-z0-9_]*"
    r"|%[-+ #0-9.*$]*[A-Za-z]|\\n")
_GLUE_RE = re.compile(r"[\u4e00-\u9fff][A-Za-z][a-z]+|[A-Za-z][a-z]+[\u4e00-\u9fff]")


def find_glue_residue(text):
    if not text:
        return []
    probe = _GLUE_STRIP_RE.sub(" ", text)
    hits = []
    for match in _GLUE_RE.finditer(probe):
        if re.search(r"[\d.]\s*$", probe[:match.start()]):
            continue
        hits.append(match.group(0))
    return hits[:3]


def unescape_newlines(text):
    """送翻前把字面 \\n/\\t 变成真实换行，让模型看到段落结构。"""
    return (text or "").replace("\\r\\n", "\n").replace("\\n", "\n") \
        .replace("\\t", "\t")


def escape_newlines(text):
    """译后统一写回字面 \\n/\\t，保持 X-Ray 文本表的存储约定。"""
    s = (text or "").replace("\r\n", "\n")
    return s.replace("\n", "\\n").replace("\t", "\\t")


def restore_linebreaks(text, count):
    """模型改动换行数时的确定性兜底：把译文的换行数对齐到原文。

    ★ 1.9.12：旧版只在译文**一个换行都没有**时才补，实测大量
    「原文 10 行 → 译文 8 行」的部分丢失被当成首次译文原样写回，
    游戏里表现为整段错行。新逻辑同时处理换行不足（按句末/从句标点补）
    和换行过多（把多余换行并回相邻行），且只在缺/多的量不大
    （≤ 原文行数）时动手，避免把模型正常分行打乱。
    """
    if count <= 0 or not text:
        return text
    cur = text.count("\n")
    if cur == count:
        return text
    # 差异过大时不动手：原文 3 行译文 30 行属于模型自由分段，
    # 强行对齐只会破坏本就可读的译文。
    if abs(cur - count) > max(count, 1):
        return text

    if cur < count:
        # 缺行：优先按句末标点切，不够再退到从句标点。
        parts = [p for p in re.split(r"(?<=[。！？!?…])", text) if p]
        if len(parts) < count + 1:
            parts = [p for p in
                     re.split(r"(?<=[。！？!?…；;，,、])", text) if p]
        if len(parts) < 2:
            return text
        need = min(count, len(parts) - 1)
        out = []
        for i, part in enumerate(parts[:-1]):
            out.append(part + ("\n" if i < need else ""))
        out.append(parts[-1])
        fixed = "".join(out)
        return fixed if fixed.count("\n") == count else text

    # 多行：把多余的换行符号并回（删除多余 \n，保留非空行内容）。
    lines = text.split("\n")
    keep = []
    for ln in lines:
        if len(keep) < count + 1:
            keep.append(ln)
        elif ln.strip():
            # 已够行数又有正文：并入最后一行，不丢字。
            keep[-1] = keep[-1].rstrip() + ln
        # 纯空行直接丢弃
    fixed = "\n".join(keep)
    # 行数还多（原文就是空行多）：从尾部合并空行
    while fixed.count("\n") > count:
        m = re.search(r"\n\s*\n", fixed)
        if m:
            fixed = fixed[:m.start()] + "\n" + fixed[m.end():]
        else:
            fixed = fixed.replace("\n", "", 1)
    return fixed if fixed.count("\n") == count else text


# ★ 1.9.12 确定性后修复辅助（零模型请求，全部通用型）：

# 输出 token 预算按本次输入长度估算：短句不虚设、长段不截断、
# 上限仍受窗口 0.6 硬顶约束（与旧行为一致），任何 MOD 自适应。
def budget_max_tokens(src_chars, ctx_size):
    est = int(src_chars * 1.2) + 256
    return max(1024, min(est, int(ctx_size * 0.6)))


_BULLET = "\u2022"


def clean_injected_bullets(src, out):
    """源文无圆点而译文有 → 判定为模型注入，删除所有圆点并整理空格。

    保留 round-trip 安全性：只删圆点本身与其后紧邻的一个空格，
    不触碰颜色码/占位符/换行，返回 (清洗后文本, 是否改动)。
    """
    if not out or _BULLET not in out:
        return out, False
    if _BULLET in (src or ""):
        return out, False   # 源文本来就有圆点，可能是合法列表，不动
    cleaned = out
    # "• " / "•" 连同其后紧邻空白一起删（颜色码后常见 %c[...]• %c[...]）
    cleaned = re.sub(re.escape(_BULLET) + r"[ \t]?", "", cleaned)
    # 删完后若颜色码之间留下双空格，收敛为一个（源文该处本无空格）
    cleaned = re.sub(r"(%c\[[^\]]*\])([ \t]{2,})", r"\1 ", cleaned)
    return cleaned, cleaned != out


# 冠词粘连：小模型偶尔把英文冠词 An/A/The 贴在中文译文前（"An神器..."）。
# ★ 1.9.13：冠词不只出现在行首——颜色码后也会紧贴中文（实测
# %c[default]The科学家）。匹配「行首或颜色码之后」，替换保留颜色码。
_LEADING_ARTICLE_RE = re.compile(
    r"(%c\[[^\]]*\]|^)(?:An|A|The)(?=[\u4e00-\u9fff])", re.I)


def clean_stuck_article(out):
    """剥离紧贴中文译文的英文冠词（行首/颜色码后），返回 (文本, 是否改动)。"""
    if not out:
        return out, False
    cleaned = _LEADING_ARTICLE_RE.sub(lambda m: m.group(1) or "", out,
                                      count=1)
    return cleaned, cleaned != out


# 通用新闻句式替换：STALKER 各版本的动态广播/新闻存在大量固定英文句式
# （"We were attacked by a <怪物>" 等）。句式通用 + 名词查词库，任何 MOD
# 只要名词在库里就能零成本命中；库里没有的名词返回 None，照常送模型。
# 只处理纯 ASCII 英文短句，不含占位符/控制符。
_NEWS_PATTERNS = (
    # (正则, 模板)  —— 组1是名词（含可选冠词/复数由正则吃掉）
    (re.compile(r"^We (?:were|got) (?:attacked|ambushed) by (?:a |an |some )?(.+?)[.!]?$", re.I),
     "我们被%s袭击了。"),
    (re.compile(r"^(?:A |An |The )?(.+?) ambushed us[.!]?$", re.I),
     "%s伏击了我们。"),
    (re.compile(r"^(?:A |An |The )?(.+?) attacked us[.!]?$", re.I),
     "%s袭击了我们。"),
    (re.compile(r"^We (?:ran into|encountered|met) (?:a |an |some )?(.+?)[.!]?$", re.I),
     "我们遇到了%s。"),
    (re.compile(r"^We got attacked by (?:a |an |some )?(.+?)[.!]?$", re.I),
     "我们被%s袭击了。"),
)


def pattern_translate(src, lookup):
    """通用句式 + 词库名词 → 确定性译文；无法覆盖返回 None。

    lookup: 单调函数 term -> 译名 或 None（由调用方注入词库查询）。
    """
    s = (src or "").strip()
    if not s or not s.isascii():
        return None
    for pat, tpl in _NEWS_PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        noun = m.group(1).strip()
        zh = lookup(noun)
        if not zh:
            return None
        return tpl % zh
    return None


def count_literal_newlines(text):
    """统计文本里字面 ``\\n`` 转义序列的个数（不是真实换行符）。"""
    return (text or "").count("\\n")


_REPEATED_BANG_RE = re.compile(r"([！!?？])\1{2,}")


def normalize_cn_punctuation(text):
    """中文语境标点规范化（确定性）：半角句读转全角、叠用标点收敛。

    只在含中文的文本里做；用正则的上下文锚定避免误伤英文缩写、
    小数点和代码里的标点。不改术语/数字/占位符。
    """
    if not text or not has_cjk(text):
        return text, False
    out = text
    # 中文字符后紧跟的半角句读 → 全角（排除小数点：前面是数字时跳过）
    out = re.sub(r"(?<=[\u4e00-\u9fff]),", "，", out)
    out = re.sub(r"(?<=[\u4e00-\u9fff]);", "；", out)
    out = re.sub(r"(?<=[\u4e00-\u9fff])!", "！", out)
    out = re.sub(r"(?<=[\u4e00-\u9fff])\?", "？", out)
    # 省略号：连续 3 个及以上半角点 → 中文省略号（前面是数字的不动）
    out = re.sub(r"(?<![\d])\.{3,}(?![\d])", "……", out)
    # 半角点跟在中文后（句末）→ 句号
    out = re.sub(r"(?<=[\u4e00-\u9fff])\.(?=$|[^\dA-Za-z])", "。", out)
    # 叠用收敛：！！！→！！、？？？→？？
    out = _REPEATED_BANG_RE.sub(lambda m: m.group(1) * 2, out)
    # 已有全角省略号旁边多余的半角点（……. → ……）
    out = re.sub(r"…\.(?![.\d])", "…", out)
    # ★ 1.9.21（同步 2.0.24 / IWP 全量审计 F5 定量）：汉字之间的空格
    # 收敛。长文分段/模型风格会产出「强烈的 禁区 环境中」这类词间空格
    # ——中文没有词间空格，两侧都是汉字即可安全删除；占位符/色码/数字
    # 两侧必有一侧非汉字，不受影响（「12.3 米」「$start $mid」保留）。
    out = re.sub(r"(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])", "", out)
    return out, out != text


def reconcile_literal_newlines(src, out):
    """译文字面 \\n 数与源文不符时的确定性兜底：按源文数量补齐/收敛。

    模型常把字面 \n 吞成空格（$clr_1A\\n$clr_2B→空格），或凭空加 \n\\n。
    只做数量对齐：缺则按句末/颜色码边界补回，多则收敛到源数量。
    差异过大（源 0 → 译 >2）交给 context_bleed 闸门处理，这里不动。
    """
    if not src or not out:
        return out, False
    want = count_literal_newlines(src)
    have = count_literal_newlines(out)
    if want == have:
        return out, False
    # 源无字面换行却冒出多个：多为串段，交给上层闸门，不在数量层硬删。
    # ★ 1.9.22（同步 2.0.25，aera 全量审计）：实测 11 条「源 0 → 译 2」
    # 漏过串段闸门原样落盘——模型平白加的断行在游戏里把整句拆碎。
    # 少量（≤2 个）时确定性收敛为空格；更多仍交串段闸门点名。
    if want == 0:
        if 0 < have <= 2:
            out2 = out.replace("\\n", " ")
            while "  " in out2:
                out2 = out2.replace("  ", " ")
            return out2, True
        return out, False
    # 源有换行、译文全丢或少了：在颜色码切换/句末处补回缺失数量。
    if have < want:
        need = want - have
        # 优先在 $clr_N 颜色码切换处补（动态新闻常是颜色分段）
        def _add_color(m):
            nonlocal need
            if need > 0:
                need -= 1
                return "\\n" + m.group(0)
            return m.group(0)
        out2 = re.sub(r"\$clr_\d+", _add_color, out)
        if need > 0:
            # 其次在句末标点后补
            def _add_sentence(m):
                nonlocal need
                if need > 0:
                    need -= 1
                    return m.group(0) + "\\n"
                return m.group(0)
            out2 = re.sub(r"(?<=[。！？!?…])", _add_sentence, out2)
        if need > 0:
            out2 = "\\n" * need + out2
        return out2, out2 != out
    # have > want：收敛多余的换行（删最靠后的空行/连续换行）。
    # 注意：re.sub 的替换串是模板，"\\n" 会被解释成真实换行——
    # 必须用 lambda 返回字面 \n，否则收敛反而制造真换行。
    out2 = out
    while count_literal_newlines(out2) > want:
        out2 = re.sub(r"\\n\\n", lambda _m: "\\n", out2, count=1)
        if "\\n\\n" not in out2:
            break
    return out2, out2 != out


# 大数字/型号保真增强：源里的「字母-数字型号串」（FNX-45 / MP-153 等）
# 若被模型整体替换，属于型号篡改，比数字缺失更误导玩家。
_MODEL_TOKEN_RE = re.compile(r"[A-Za-z]{2,}-?\d{2,4}[A-Za-z]?")


def missing_model_tokens(src, out):
    """返回源文里「明确型号串」在译文中消失的列表（型号被换掉）。

    型号需紧邻引号或武器/物品语境词，减少误报；年份区间不算型号。
    """
    if not src or not out:
        return []
    missing = []
    seen = set()
    # ★ 1.9.18：色码 %c[r,g,b,a] 内数字不是型号，先剥离再扫。
    src = re.sub(r"%c\[[^\]]*\]", " ", str(src))
    for m in _MODEL_TOKEN_RE.finditer(src):
        tok = m.group(0).strip()
        if tok in seen or len(tok) < 4:
            continue
        seen.add(tok)
        if re.fullmatch(r"\d+", tok):
            continue
        # ★ 1.9.21（同步 2.0.24 / IWP 全量审计 D2）：英文年代短语不是
        # 型号——2.x 实测 "mid-1990s" 被当型号，译文「1990年代中期」
        # 判缺失补出注记。
        if re.fullmatch(r"(?:early|mid|late)[-\s]?\d{4}s?", tok, re.I):
            continue
        # 型号需紧邻引号或武器语境词，减少误报。
        # ★ 1.9.15：语境窗口 ±24→±48 + 全源兜底（武器描述里型号与语境词
        # 相距很远，窄窗口漏掉 AK-100→AK-74M 的型号替换）。
        s, e = m.span()
        near = src[max(0, s - 48):min(len(src), e + 48)]
        if not (any(q in near for q in "\"'«“”")
                or re.search(r"rifle|pistol|gun|shotgun|carbine|weapon|ammo|"
                             r"cartridge|sniper|caliber|series|barrel|"
                             r"караб|пистол| оружи|патрон",
                             near if near else src, re.I)):
            continue
        if tok not in out:
            missing.append(tok)
    return missing


def patch_missing_numbers(src, out, skip=()):
    """数字闸门两次重试仍缺失时的兜底：不整段回退原文，而是把缺失的
    关键数字按出现顺序补进译文，保留已经译好的正文。

    实测武器长段描述（FNX-45 段落）因金额 2800 两次重试都没带上，
    旧逻辑整段保留英文——玩家看到的是一整段看不懂的外文，比数字
    缺失严重得多。这里改为：译文已有中文时，把缺失金额以「（2800）」
    形式补到对应位置附近；找不到锚点则追加到末尾。

    ★ 1.9.21（同步 2.0.24）：注记字样「原文」退役，补的是裸数字；
    skip 传入缺失型号串——数字若是某个缺失型号的一部分（AK-103 里的
    103），由型号保真闸门整串补「（AK-103）」即可，再补裸「（103）」
    就是双注记（2.x 回归测试抓出的交互缺陷）。
    """
    if not src or not out or not any("\u4e00" <= c <= "\u9fff" for c in out):
        return None                      # 无中文的译文不能补，交给调用方回退
    missed = missing_big_numbers(src, out)
    if not missed:
        return out
    if skip:
        _sk = [str(t) for t in skip]
        missed = [n for n in missed
                  if not any(n in t for t in _sk)]
        if not missed:
            return out
    fixed = out
    for n in missed:
        # 优先插到译文里出现的货币/单位词之后（卢布/美元/格里夫纳等）
        m = re.search(r"(卢布|美元|元|格雷|格里夫纳|coin| coins)", fixed)
        if m:
            pos = m.end()
            fixed = fixed[:pos] + "（%s）" % n + fixed[pos:]
        else:
            fixed = fixed.rstrip() + "（%s）" % n
    return fixed


# 专名/歌名/型号保护：只按**结构**判断，不写死任何 MOD 文件名。
# 文件名的 credits/ogg/music/songs/enc_weapons 只是通用提示；用户可关闭。
# ID 引用扫描（跨 MOD 通用；只认结构，不认具体文件名）
_XML_REF_TAG_RE = re.compile(
    r"<(?:text|name|bio|title)\b[^>]*>\s*([A-Za-z0-9_][A-Za-z0-9_.\-]{0,100})\s*</",
    re.I)
_XML_REF_ATTR_RE = re.compile(
    r"\b(?:name|hint|bio|caption|descr|title)\s*=\s*[\"']"
    r"([A-Za-z0-9_][A-Za-z0-9_.\-]{0,100})[\"']",
    re.I)
_SCRIPT_TRANSLATE_RE = re.compile(
    r"translate_string\s*\(\s*[\"']([^\"']+)[\"']\s*\)")
_SCRIPT_QUOTED_RE = re.compile(r"[\"']([A-Za-z0-9_][A-Za-z0-9_.\-]{1,100})[\"']")

_PROTECT_FILE_HINTS = ("credits", "ogg", "music", "songs", "enc_weapons",
                       "snames", "fnames", "character_name")
# ★ 1.9.14：标题规则收紧——冒号前段含逗号/引号的是叙述句
# （"Men, take note: ..."），标签形态（Name:/Radio:）无逗号。
_TITLE_LIKE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9 .\-'/]{0,40}[:：]\s*[A-Za-z0-9]")
_TITLE_DASH_RE = re.compile(
    r"^[A-Za-z0-9][^\n]{0,80}[—–]\s*[A-Za-z0-9]")
_MODEL_LIKE_RE = re.compile(
    r'^[A-Z0-9][A-Za-z0-9 .\-/]*["“][^"”]{1,80}["”]')
_CODE_LIKE_RE = re.compile(r"^[A-Z0-9][A-Z0-9 .\-/_]{1,30}$")
_COPYRIGHT_RE = re.compile(r"^(?:©|\(c\)|copyright\b)", re.I)
# ★ 1.9.14 误伤修正：`FAILED\b` 在 re.I 下命中叙述文里的 "failed"，
# `teleport\w*` 命中叙事词 "teleport closer"（实测正常文本被当调试行跳过）。
# 改为 FAILED 大小写敏感、teleport 只认 `teleport_…`/`teleport:` 形态。
_DEBUG_LIKE_RE = re.compile(
    r"(?:onItemDrop|npcObj|\bFAILED\b|sect\s*-|obj\s*:|gvid\s*:|"
    r"^(?:~|(?i:\[[a-z0-9_:.\- ]+\]$))|"
    r"\bteleport_[a-z0-9_]+|\bteleport\s*[:=]|"
    r"\b(?:reinit|kamps|npcItems|isVisual\w*)\b|"
    r"content\s*:)")
_DEBUG_PHRASES = {"dead", "none", "null", "true", "false",
                  "avail end", "start here", "reinit timer"}

# 口径/型号不是“正文”：9x18 PM、5.56x45 M995 AP、12.7x99mm、.44 Magnum
# 这类短技术串必须整串保留，不能被模型当时间/自动步枪翻译。
_TECH_SPEC_RE = re.compile(
    r"^\s*(?:\d+\s*[x×]\s*)?\d+(?:[.,]\d+)?\s*[x×]\s*"
    r"\d+(?:[.,]\d+)?(?:\s*[A-Za-z0-9.+/\-]{0,12})?\s*$", re.I)
_TECH_CALIBER_RE = re.compile(
    r"^\s*\.\d{2,3}\s+[A-Za-z][A-Za-z0-9 .\-/]{0,24}$", re.I)


def looks_like_technical_text(text):
    """口径/弹药/尺寸类整串：只做技术串保护，不当普通正文。

    ★ 1.9.18（全 MOD 重放审计）：含句末标点的串是正文（实测 NS7
    'Магазин на 10 патронов калибра 10.3x45мм.' 弹匣描述被整条跳过）。
    """
    s = str(text or "").strip()
    if not s or len(s) > 60 or re.search(r"[。！？!?；;]", s):
        return False
    if re.search(r"[.。！？!?]\s*$", s) and len(s) > 20:
        return False
    if _TECH_SPEC_RE.match(s) or _TECH_CALIBER_RE.match(s):
        return True
    return bool(re.search(r"\d+(?:[.,]\d+)?\s*[x×]\s*\d+(?:[.,]\d+)?", s))


def looks_like_generated_noise(rel, text):
    """旧的 ___scriptE__ / ___cfgxml__ 生成表：短 ASCII 多是日志/键名。"""
    low = (rel or "").lower().replace("\\", "/")
    if "___scripte__" not in low and "___cfgxml__" not in low:
        return False
    s = str(text or "").strip()
    if not s or len(s) > 80 or re.search(r"[。！？!?；;]", s):
        return False
    if s.isascii():
        return True
    return bool(re.search(
        r"^(?:gulag|ignored|selected|actor\s+(?:start|end)|spawn|id):",
        s, re.I))


def looks_like_model_name(text):
    """纯拉丁武器/物品型号名：会话词表不应把它们当人名/普通物品翻译。"""
    s = str(text or "").strip()
    if not s or not re.search(r"[A-Za-z]", s):
        return False
    if re.search(r"\d", s) or re.search(r"[“”\"'«»/]", s):
        return True
    if re.search(r"[A-Z]{2,}", s):
        return True
    if "-" in s and len(s.split()) >= 2:
        return True
    caps = sum(1 for w in s.split() if w[:1].isupper())
    return s.isascii() and caps >= 2


def _has_translatable_text(text):
    """剔除运行期占位符后仍有 2 个以上连续字母，才值得送翻。"""
    clean = _RUNTIME_TOKEN_RE.sub("", text or "")
    return bool(re.search(r"[A-Za-zА-Яа-яЁё]{2,}", clean))


def detect_text_lang(text):
    """按字母构成判断单条源文是俄语还是英语。

    X-Ray 的 `<rus>` 字段在不少 MOD 里实际放的是英语；只按字段名选源语言
    会把英语提示词成俄语。这里只做粗判：西里尔字母占优→rus，拉丁占优→eng；
    混合专名/缩写不改变主语言；太短（<2 个字母）返回空，交给文件级提示。
    """
    s = text or ""
    cyr = len(re.findall(r"[А-Яа-яЁё]", s))
    lat = len(re.findall(r"[A-Za-z]", s))
    if cyr >= 2 and cyr >= lat:
        return "rus"
    if lat >= 3 and lat > cyr:
        return "eng"
    return ""


def looks_like_code_or_debug(rel, text):
    """结构性噪声：调试日志/路径/URL/代号。**总是**跳过，不占“专名保护”。"""
    s = (text or "").strip()
    if not s or len(s) <= 1:
        return True
    if s.lower() in _DEBUG_PHRASES or _COPYRIGHT_RE.match(s):
        return True
    if _DEBUG_LIKE_RE.search(s):
        return True
    if s.lower().startswith(("http://", "https://", "www.")):
        return True
    # 短代号/弹药规格：只有“全大写且没有任何小写字母”才跳过。
    # 旧规则把“Набор 1 / Патроны 9х18мм ПМ / 30 х 5.45x39мм”都当型号，
    # 实测 459 条正文因此漏译；含小写词的串一律按正文送翻。
    name_file = any(x in (rel or "").lower() for x in (
        "character_name", "snames", "fnames", "bio"))
    zone_file = any(x in (rel or "").lower() for x in (
        "string_table_enc_zone", "string_table_levels", "mapspots"))
    if not (name_file or zone_file):
        if (any(c.isdigit() for c in s)
                and re.fullmatch(
                    r"[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9 .\-/_]{0,40}", s)):
            return True
        if _CODE_LIKE_RE.match(s) and any(c.isdigit() for c in s):
            return True
    return False


# ★ 1.9.18（全 MOD 重放审计，对齐 2.0.11/2.0.21）：以下目录的短串是
# 玩家可见正文，identifier/保护判定命中时放行送翻（WOT 武器升级名、
# 新闻碎片 'a merc'、UI 'Dismissable'/'March'、任务名 'Psy-storm: …'）。
_PLAYER_LABEL_DIRS = ("achievement", "game_relations", "binoc", "dialog",
                      "relation", "reputation", "rank", "quick_menu",
                      "companion", "news", "dynamic_news", "pda", "options",
                      "equipment", "quests", "loadscreen", "land_names",
                      "weapons_upgrades", "upgrades", "barter", "cryray",
                      # ★ 1.9.19（同步 2.0.22）：藏宝点/储物点名是 PDA 里玩家
                      # 可见正文，全库 679 条被 identifier 跳过（'Backpack under
                      # tree'、'Chest on the roof'、'Safe under the bush'）。
                      "treasure", "secrets")
_UPPER_LABEL_DIRS = ("achievement", "game_relations", "relation",
                     "reputation", "rank", "quick_menu", "companion",
                     "dialog", "news", "dynamic_news", "quests",
                     "loadscreen")


def is_player_facing_label(rel, text=""):
    low = (rel or "").lower().replace("\\", "/")
    if not any(x in low for x in _PLAYER_LABEL_DIRS):
        return False
    s = (text or "").strip()
    return bool(s) and len(s) <= 90


# ★ 1.9.22（同步 2.0.25，aera 与 CHS 人工包全量对照 P3）：条目 id 后缀是
# 引擎命名惯例里的"玩家可见字段"标记——st_xxx_name / xxx_descr /
# ui_yyy_text 这类 id 的内容就是物品名/描述/界面文案（实测 AREA 闪存盘名
# "Flash drive with alloys data" 等 2206 条被 identifier 过滤整类跳过，
# 人工包全译）。后缀白名单是跨 MOD 通用的引擎约定，不含任何具体 MOD 词。
_PLAYER_ID_SUFFIX_RE = re.compile(
    r"(?:_|^)(?:name|descr|description|text|title|hint|info|tips|caption)"
    r"(?:_?\d+)?$",
    re.I)


def _id_player_visible(eid):
    """条目 id 以玩家可见字段后缀结尾 → 不按 identifier/technical 跳过。"""
    return bool(eid) and bool(_PLAYER_ID_SUFFIX_RE.search(str(eid)))


def _is_model_code_form(rel, text):
    """code 过滤里"全大写型号/弹药规格"那一支是否为命中原因。

    ★ 1.9.19（同步 2.0.22）：全大写型号短名被 code 整条拦掉、而词库有整串
    中文译名（`PPSH-41`→`PPSH-41冲锋枪`、`BT MP9`→`BT MP9冲锋枪`）时按
    "词库整串命中 = 明确翻译意图"放行；调试词/URL/版权行/单字符
    （dead、True、None、ч）不是型号形态，仍跳过。
    """
    s = (text or "").strip()
    if len(s) <= 1 or s.lower() in _DEBUG_PHRASES or _COPYRIGHT_RE.match(s):
        return False
    if _DEBUG_LIKE_RE.search(s):
        return False
    if s.lower().startswith(("http://", "https://", "www.")):
        return False
    low = (rel or "").lower()
    if any(x in low for x in ("character_name", "snames", "fnames", "bio",
                              "string_table_enc_zone", "string_table_levels",
                              "mapspots")):
        return False
    return bool(any(c.isdigit() for c in s)
                and re.fullmatch(r"[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9 .\-/_]{0,40}", s))


def looks_like_identifier_text(text):
    """纯小写 ASCII 的“段落/任务/节名”不是用户正文（如 resque information）。"""
    s = (text or "").strip()
    if not s:
        return True
    if re.fullmatch(r"[a-z0-9_.\- /]{1,90}", s) and not re.search(
            r"[.!?,;:()\[\]{}]", s):
        return True
    # 纯 ASCII、无句末标点、<=6 个词：多为任务/段落/角色/资源名
    # （Courier document / My map task / Gans Wiberg / Karta Name），
    # 不是用户正文；但「Disable Info」这类多个首字母大写的界面标签
    # 是给玩家看的，不能当标识符跳过。
    if (s.isascii() and len(s.split()) <= 6
            and not re.search(r"[.!?。！？]", s)):
        caps = sum(1 for w in s.split() if w[:1].isupper())
        if caps <= 1:
            return True
    return False


def _is_prose_sentence(s):
    """整句正文（不是标题/型号名）：句末标点结尾且够长，或很长且多词。"""
    n = len(s)
    if n >= 40 and re.search(r"[.!?。！？]\s*[\"”»']?\s*$", s):
        return True
    return n > 90 and len(re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", s)) >= 8


_TRACK_LIST_RE = re.compile(r"(?:ТРЕК|Трек|TRACK|Track|曲目)\s*[:：]?\s*\d+",
                            re.I)


def looks_like_hard_protected_file(rel):
    """制作名单/音乐列表：连核心术语也不替换；武器短名只保护非词库型号。"""
    low = (rel or "").lower().replace("\\", "/")
    return any(x in low for x in ("credits", "ogg", "music", "songs"))


def looks_like_proper_text(rel, text):
    """专名/歌名/型号：只在用户勾选“保护专名”时跳过。"""
    s = (text or "").strip()
    if not s:
        return False
    low = (rel or "").lower().replace("\\", "/")
    # 文件级强保护：制作名单、音乐/歌曲列表整张表都不翻；人物/武器/名字表
    # 只保护短名，长描述仍交给模型（例如武器说明）。
    hint = next((h for h in _PROTECT_FILE_HINTS if h in low), "")
    if looks_like_hard_protected_file(rel):
        # ★ 1.9.19（同步 2.0.22）："ogg/music" 命中的其实是物品表——
        # `string_table_ogg_player.xml` 是 MP3 播放器/音乐闪存盘的名字与描述，
        # 玩家在物品栏就能看到，人工汉化对长描述全部翻译。只放行这类长正文；
        # 制作名单与人物名字表仍整表保护。**含曲目列表的条目不放行**：
        # ogg_flash_* 描述后面跟着 `ТРЕК 01: Алиса — Веретено` 这类真实
        # 歌名/歌手（全库 54 条），整串送翻会译错歌名。
        if (hint in ("ogg", "music", "songs") and "credits" not in low
                and _is_prose_sentence(s) and not _TRACK_LIST_RE.search(s)):
            return False
        return True
    if hint == "enc_weapons" and len(s) <= 40 and len(s.split()) <= 6:
        return True
    # ★ 1.9.18（全 MOD 重放审计）：长句不是标题/型号——'The situation
    # is this: ...' 长正文、引号紧跟 Title 词的整段描述被误跳，实测漏译。
    if (_TITLE_LIKE_RE.match(s) and len(s) <= 60
            and not re.search(r"[.!?。！？]\s*$", s)):
        return True
    if _TITLE_DASH_RE.match(s) and len(s) <= 60:
        return True
    if (_MODEL_LIKE_RE.match(s) and len(s) <= 90
            and not re.search(r'[.!?。！？]\s*["”»]?\s*$', s)):
        return True
    if len(s) <= 20 and s.isupper() and any(c.isalpha() for c in s):
        # ★ 1.9.11：只保护拉丁字母的型号/缩写（NVIDIA DLSS、AK-74）。
        # 西里尔全大写短名（БАРМЕН、ПРИЗРАК、ЗВЕРОБОЙ…）在人物表/SMS
        # 名字表里是**普通身份称呼**，旧规则把它们整批原样保留、连模型
        # 都不调用。这里放行为"不自动保护"：调用方先用 termbase_lookup
        # 取库里既有译名，没有的送模型翻译。
        if re.search(r"[A-Za-z]", s):
            return True
        return False
    return False


def looks_like_protected_text(rel, text):
    """兼容旧接口：结构性噪声或专名。"""
    return (looks_like_code_or_debug(rel, text)
            or looks_like_proper_text(rel, text))


# ----------------------------------------------------------------------
# 脚本字面文本上下文过滤（1.7.6）
#
# 上游 getScriptPotentialTexts 会把脚本里**所有**带字母的引号串都抽出来。
# 1.7.5 实测：63 个脚本、1890 处赋值、46 处 string_find、11 处 string_gsub
# 被换成裸 ID，逻辑串变成 nil。这里按“这一行在干什么”做保守白名单：
#   * log/print/debug/error/assert/trace 一律不动；
#   * string_find/string_gsub/比较运算一律不动（逻辑串）；
#   * 只改显示类调用和明确的文本变量赋值。
# 宁可漏几条界面文本，也不能把脚本逻辑改坏。
# ----------------------------------------------------------------------
_SCRIPT_LOG_CALL_RE = re.compile(
    r"\b(?:log1?|log2|log3|log_message|printf|print|debug|error|assert|"
    r"trace|console|exec)\s*\(", re.I)
_SCRIPT_LOGIC_CALL_RE = re.compile(
    r"\b(?:string_find|string_gsub|string_match|string_gmatch|"
    r"string\.find|string\.gsub|string\.match|string\.gmatch)\s*\(",
    re.I)
_SCRIPT_UI_CALL_RE = re.compile(
    r"\b(?:SetText|SetName|SetTextTip|InitStatic|InitButton|AddText|AddLine|"
    r"send_tip|hud_msg|add_spot_on_map|give_talk_message|map_add_user_spot|"
    r"show_message|show_tip|talk_message|send_message|add_news|add_tip|"
    r"message_box|set_level_changer_invitation)\s*\(", re.I)
_SCRIPT_ASSIGN_RE = re.compile(
    r"^\s*(?:local\s+)?(?:self\.|this\.|obj\.)?([A-Za-z_]\w*)\s*=\s*[^=]")
_SCRIPT_TEXT_VAR_RE = re.compile(
    r"(?:^|_)(?:text|txt|msg|message|hint|caption|title|descr|description|"
    r"name|news|label|tip)(?:_|$)", re.I)


def strip_script_comment(line):
    """去掉 -- 行注释，保留引号内的 --（如 "a--b"）。"""
    out = []
    quote = None
    escape = False
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
        else:
            if ch in "\"'":
                quote = ch
                out.append(ch)
            elif ch == "-" and i + 1 < len(line) and line[i + 1] == "-":
                break
            else:
                out.append(ch)
        i += 1
    return "".join(out)


_SCRIPT_EXTRA_DISPLAY_RES = (
    re.compile(
        r"(?:set_level_changer_invitation|set_description)\s*\(\s*"
        r"([\"'])(.*?)\1", re.S),
    re.compile(
        r"AddItem\s*\(\s*game\.translate_string\s*\(\s*([\"'])(.*?)\1",
        re.S),
)
_SCRIPT_TRANSLATE_ARG_RE = re.compile(
    r"game\.translate_string\s*\(\s*([\"'])(.*?)\1", re.S)


def extra_script_display_literals(whole):
    """补上游漏掉的显示调用字面串（只收西里尔文本，避免碰 ID/代码）。"""
    out = set()
    for pattern in _SCRIPT_EXTRA_DISPLAY_RES:
        for m in pattern.finditer(whole or ""):
            inner = m.group(2)
            if re.search(r"[А-Яа-яЁё]", inner):
                out.add(m.group(1) + inner + m.group(1))
    return out


def script_translate_literal_args(whole):
    """已包在 game.translate_string(...) 里的字面串：替换时不能再套一层。"""
    return {m.group(1) + m.group(2) + m.group(1)
            for m in _SCRIPT_TRANSLATE_ARG_RE.finditer(whole or "")}


def script_literal_allowed(line, literal):
    """这一行的这个字面串是否允许改写/送翻。"""
    code = strip_script_comment(line)
    if not code.strip():
        return False
    if _SCRIPT_LOG_CALL_RE.search(code):
        return False
    if _SCRIPT_LOGIC_CALL_RE.search(code):
        return False
    # 比较/连接判断里的字符串通常是标签、档位、状态名，不能换。
    if re.search(r"==|~=|<=|>=", code):
        return False
    if _SCRIPT_UI_CALL_RE.search(code):
        return True
    m = _SCRIPT_ASSIGN_RE.match(code)
    if m and _SCRIPT_TEXT_VAR_RE.search(m.group(1)):
        return True
    return False


def script_literal_allowed_in_file(whole, cand):
    """候选串在脚本里的**每一次**出现都必须是安全上下文，才允许改写。

    任何一次出现在 log/string_find/比较里就整条跳过；全局替换无法区分
    同一字面串的不同出现位置，保守跳过比改坏逻辑好。
    """
    if len(cand) < 2 or cand[0] not in "\"'" or cand[-1] != cand[0]:
        return False
    inner = cand[1:-1]
    if not inner.strip():
        return False
    for needle in (cand, inner):
        found = False
        start = 0
        while True:
            idx = whole.find(needle, start)
            if idx < 0:
                break
            found = True
            line_start = whole.rfind("\n", 0, idx) + 1
            line_end = whole.find("\n", idx)
            if line_end < 0:
                line_end = len(whole)
            if not script_literal_allowed(whole[line_start:line_end], inner):
                return False
            start = idx + len(needle)
        if found:
            return True
    return False


def split_protected(text, max_chars, pattern):
    """按 split_long_text 切分，但**绝不切断 pattern 的匹配**。

    强制术语模式下原文被换成 $$T1$$ 这类占位符；按字符硬切若切在占位符中间，
    模型会看到半截标记，回填必然失败并退回旧的切片做法（质量下降且难排查）。
    做法：先把每个匹配替换成一个私有区单字符，切分后再还原 —— 匹配永远完整。
    """
    spans = list(pattern.finditer(text))
    if not spans or len(spans) > 5000:
        return split_long_text(text, max_chars)
    if any(0xE000 <= ord(c) <= 0xF8FF for c in text):
        # 源文本已含私有区字符时不能用 PUA 做哨兵（会被误还原）；
        # 退回普通切分：占位符可能被切断，但 _translate_force 会校验并回退，
        # 不会产出坏译文。
        return split_long_text(text, max_chars)
    mapping = {}

    def repl(m):
        ch = chr(0xE000 + len(mapping))
        mapping[ch] = m.group(0)
        return ch

    protected = pattern.sub(repl, text)
    parts = split_long_text(protected, max_chars)
    if not mapping:
        return parts
    out = []
    for part in parts:
        for ch, orig in mapping.items():
            if ch in part:
                part = part.replace(ch, orig)
        out.append(part)
    return out


def split_long_text(text, max_chars):
    """把超长文本切成 ≤ max_chars 的段：先按句末标点（。！？!?…换行；分号），
    再按从句标点（逗号等），最后硬切。分隔符随前文保留，绝不丢字、绝不截断。"""
    if len(text) <= max_chars:
        return [text]

    def split_keep(pattern, s):
        """按捕获组切分，分隔符并回前一片段。"""
        parts = pattern.split(s)
        units, buf = [], ""
        for i, piece in enumerate(parts):
            buf += piece
            if i % 2 == 1:
                if buf:
                    units.append(buf)
                buf = ""
        if buf:
            units.append(buf)
        return units

    def hard_wrap(s, limit):
        return [s[i:i + limit] for i in range(0, len(s), limit)]

    units = split_keep(_SENT_SPLIT, text)
    fine = []
    for u in units:
        if len(u) <= max_chars:
            fine.append(u)
            continue
        for clause in split_keep(_CLAUSE_SPLIT, u):
            fine.extend(hard_wrap(clause, max_chars))

    chunks, cur = [], ""
    for u in fine:
        if len(cur) + len(u) <= max_chars:
            cur += u
        else:
            if cur:
                chunks.append(cur)
            cur = u
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


_TAG_TEXT = re.compile(
    r"<(?:text|rus|eng|ukr|ger|fra)\b[^>]*>(.*?)</(?:text|rus|eng|ukr|ger|fra)>",
    re.I | re.S)

_SCAN_KIND_MAP = {"text": "text", "cfg": "cfgxml", "ltx": "ltx",
                  "script": "scriptL"}


def _iter_candidate_strings(txt, kind):
    """按文件类型粗提"可能被翻译的字符串"，只用于规模估计与分档推荐。"""
    if kind in ("ltx", "cfgxml", "scriptL", "scriptE"):
        for m in re.finditer(r'"([^"\n]{2,})"', txt):
            s = m.group(1).strip()
            if s:
                yield s
    else:
        for m in _TAG_TEXT.finditer(txt):
            s = re.sub(r"\s+", " ", m.group(1)).strip()
            if s:
                yield s


def classify_xray_file(full, rel):
    """判定 X-Ray 文件类型：'text' / 'cfg' / 'ltx' / 'script' / None。

    勘察（扫描选档）与执行（真正翻哪些文件）**必须共用这一处判定**，
    否则"扫描说有 3000 条"和"实际翻了哪些文件"不是同一批 —— 旧实现两条
    口径各写一套（勘察只看路径里有没有 text 目录，执行还会嗅探 <string>
    头），统计与行为会分歧。
    """
    low = full.lower()
    parts = [x.lower() for x in rel.replace("\\", "/").split("/")]
    if low.endswith(".ltx"):
        return "ltx"
    if low.endswith(".script"):
        return "script"
    if low.endswith(".xml"):
        if "text" in parts[:-1]:
            return "text"
        try:
            with open(full, "rb") as f:
                head = f.read(65536).lower()
        except OSError:
            return "cfg"
        if b"<string" in head and b"id=" in head and (
                b"<text" in head or b"<rus" in head
                or b"<eng" in head or b"<ukr" in head):
            return "text"
        return "cfg"
    return None


def analyze_input_dir(in_dir, mode="text", log=None, cancel=None,
                      max_files=200000):
    """勘察待翻译目录：文件数 / 体积 / 最大文件 / 最长单条文本，
    并按内置档位给出超长条数与推荐档位。只读，不修改任何文件。

    要逐文件读内容才能统计"最长可译文本"，所以超大目录会明显耗时：
      * 每 500 个文件写一条进度，用户知道它在动；
      * 超过 max_files 停止统计并在结果里标 truncated（不再默默卡住）；
      * cancel 为 threading.Event 时支持中途取消。
    长度统计只保留"直方图 + 最长项"，不再把所有 (长度, 文件名) 元组堆在
    内存里（数万条文本就是几十万个元组）。
    """
    suffixes = {
        "text": (".xml",), "cfgxml": (".xml",),
        "ltx": (".ltx",), "scriptL": (".script",), "scriptE": (".script",),
        "auto": (".xml", ".ltx", ".script"),
    }
    want = suffixes.get(mode, (".xml",))
    n_files = 0
    total_bytes = 0
    max_file = (0, "")
    hist = {}
    n_strings = 0
    max_str = 0
    max_str_file = ""
    truncated = False
    cancelled = False
    for root0, dirs, files in os.walk(in_dir):
        dirs[:] = [d for d in dirs
                   if d not in ("translated_hy_mt2", "__temp__")
                   and not d.endswith(".git")]
        if cancel is not None and cancel.is_set():
            cancelled = True
            break
        parent_name = os.path.basename(root0).lower()
        if mode == "cfgxml" and parent_name == "text":
            continue
        for fn in files:
            if not fn.lower().endswith(want):
                continue
            full = os.path.join(root0, fn)
            rel = os.path.relpath(full, in_dir)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > 20 * 1024 * 1024:
                continue
            n_files += 1
            total_bytes += size
            if size > max_file[0]:
                max_file = (size, full)
            if n_files > max_files:
                truncated = True
                break
            try:
                with open(full, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            txt = None
            for enc in encoding_chain(raw):
                try:
                    txt = raw.decode(enc)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if txt is None:
                continue
            kind = mode
            if mode == "auto":
                kind = _SCAN_KIND_MAP.get(
                    classify_xray_file(full, rel), "cfgxml")
            for s in _iter_candidate_strings(txt, kind):
                length = len(s)
                hist[length] = hist.get(length, 0) + 1
                n_strings += 1
                if length > max_str:
                    max_str, max_str_file = length, full
            if log and n_files % 500 == 0:
                log("  勘察中…已读 %d 个文件" % n_files)
        if truncated:
            if log:
                log("  文件数超过 %d，勘察提前结束（统计口径见结果说明）"
                    % max_files)
            break
    per_tier = {}
    for t in BUILTIN_TIERS:
        cap = safe_chunk_chars(TIER_PROFILES[t]["ctx"])
        per_tier[t] = sum(c for L, c in hist.items() if L > cap)
    if max_str > safe_chunk_chars(TIER_PROFILES["mt2"]["ctx"]):
        recommend = "7b"
    else:
        recommend = "mt2"
    return {
        "files": n_files,
        "bytes": total_bytes,
        "max_file": max_file,
        "max_str": max_str,
        "max_str_file": max_str_file,
        "n_strings": n_strings,
        "overlong": per_tier,
        "recommend": recommend,
        "truncated": truncated,
        "cancelled": cancelled,
    }


def decide_ctx(tier, info=None, cap=None):
    """按实测文本长度自动设定服务上下文：
    够短就用 4096 省内存/提速；按需要升档，但不超过该档上限。
    cap 可显式传入（自选档用用户设置）；否则取画像中的档位窗。

    自选档（custom）例外：上下文是用户自己选的，这里**不做收缩** ——
    用户选 8192 却被自动降到 4096，等于工具替用户改了他明确指定过的参数。
    """
    if cap is None:
        cap = TIER_PROFILES[tier]["ctx"]
    if tier == "custom":
        return cap
    if not info or not info.get("max_str"):
        return cap
    need_tokens = int(estimate_tokens("x" * info["max_str"]) * 2.2) + 512
    for ctx in (4096, 8192, 16384, 32768):
        if ctx >= cap:
            return cap
        if need_tokens <= ctx:
            return min(ctx, cap)
    return cap



def _is_word_char(ch):
    """术语两侧的"词字符"判定：西里尔/拉丁按词边界锚定，CJK 视为边界。

    为什么要区分（原实现只用正则 \\w，两者都没分开）：
      * 西里尔/拉丁文字有词的概念，术语两侧必须锚定词边界，否则
        "No" 会误伤 "Now"、"Кот" 会误伤 "Который"；
      * 中文没有词间空格，而 Python 的 \\w 把汉字也算词字符 —— 于是术语表里
        任何中文条目在中文文本里都会因为"左右都是词字符"而永不命中。
        把 CJK 当边界即可命中（如术语「神器」在「一个神器装备」中）。
    """
    if not (ch.isalnum() or ch == "_"):
        return False
    o = ord(ch)
    if (0x3400 <= o <= 0x9FFF
            or 0x3040 <= o <= 0x30FF
            or 0xAC00 <= o <= 0xD7AF):
        return False
    return True


class GlossaryIndex(object):
    """术语匹配索引：首字符分桶 + 同位置长词优先 + 非重叠单向扫描。

    为什么不用 "|".join(...) 的巨型交替正则：实测 8208 条术语拼出的
    204 KB 正则单条 150 字符文本要 43.70 ms，而同样的数据用朴素子串
    扫描只要 1.22 ms（慢 36 倍）；两万条量级的任务上就是几十分钟纯 CPU
    空转，GPU 全程在等。分桶后每个字符只与"以该字符开头"的词条比较，
    比较次数降两个数量级，匹配语义与原正则逐字符一致（有等价性属性测试）。

    v2 增补：
    * `variants`：拼写变体映射到规范词条，避免 ё/е 两个译名打架；
    * `case_sensitive`：人物名/技术缩写/小写单单词拉丁硬词要求首字母
      大小写一致（大写专名仍命中，句子里的普通名词不再误伤）；
    * 查找时统一做 ё→е 折叠，俄文里漏写两点的名字也能命中规范词条。
    """

    def __init__(self, mapping, case_sensitive=None, variants=None):
        raw = {}
        for k, v in (mapping or {}).items():
            name = str(k)
            if name.strip():
                raw[name] = str(v)
        self._case_sensitive = {str(k) for k in (case_sensitive or ())}
        var = {str(k): str(v) for k, v in (variants or {}).items()
               if str(k).strip() and str(v).strip()}

        # 变体只做别名，不单独成为规范条目：否则硬词过滤会拿到变体键，
        # 把“当前 MOD 专名”误判成软词。
        self._map = {}
        # 规范词条自身的译文优先；变体只在规范词条缺席时补位。
        for key, value in raw.items():
            if key not in var or var[key] == key:
                self._map[key] = value
        for key, value in raw.items():
            canon = var.get(key, key)
            if canon in raw:
                self._map.setdefault(canon, value)
            else:
                self._map.setdefault(key, value)
        self._alias = {}
        for key in raw:
            canon = var.get(key, key)
            if canon not in self._map:
                canon = key
            self._alias.setdefault(self._fold(key), set()).add(canon)

        self._by2 = {}
        self._by1 = {}
        for key in self._map:
            kl = self._fold(key)
            if len(key) == 1:
                t = self._by1.setdefault(kl, self._new_table())
            else:
                t = self._by2.setdefault(kl[:2], self._new_table())
            L = len(key)
            t["e"].setdefault(L, set()).add(key)
            for alias in self._alias.get(kl, (key,)):
                if len(alias) != L:
                    continue
                t["c"].setdefault(L, {}).setdefault(
                    self._fold(alias), []).append(key)
        for t in list(self._by1.values()) + list(self._by2.values()):
            t["lens"] = sorted(t["e"].keys(), reverse=True)

    @staticmethod
    def _new_table():
        return {"e": {}, "c": {}, "lens": []}

    @staticmethod
    def _fold(s):
        return s.casefold().replace("ё", "е")

    def _case_ok(self, seg, key):
        """大小写敏感词条：全大写缩写必须逐字一致，其余只要求首字母一致。

        首字母一致而不是全等，是为了让 `Гришачев`/`Гришачёв`、
        `Санёк`/`Санек` 这类拼写变体仍能命中；`crystal` 与 `Crystal`
        的首字母不同，正好被挡在正文普通名词之外。
        """
        if key not in self._case_sensitive:
            return True
        if key.isupper():
            return seg == key
        if key[:1].islower():
            # 小写单单词 canon（bar/crystal/star）只认大写专名：
            # 正文里的普通名词保持原样，交给模型按语境处理。
            return bool(seg) and seg[:1].isupper()
        return bool(seg) and bool(key) and seg[0] == key[0]

    def __len__(self):
        return len(self._map)

    def _probe(self, table, text, i, low):
        """按"位置升序 + 长词优先 + 同长度精确优先"取命中，返回 key 或 None。"""
        if not table:
            return None
        n = len(text)
        for L in table["lens"]:
            end = i + L
            if end > n:
                continue
            seg = text[i:end]
            key = None
            pool = table["e"].get(L)
            if pool and seg in pool:
                key = seg
            else:
                segl = low[i:end] if low is not None else self._fold(seg)
                cand = table["c"].get(L, {}).get(segl)
                if cand:
                    cand = list(dict.fromkeys(cand))
                    exact = [k for k in cand if k[:1] == seg[:1]]
                    if exact:
                        cand = exact
                    key = max(cand, key=lambda k: sum(
                        1 for a, b in zip(seg, k) if a == b))
            if key is None:
                continue
            if not self._case_ok(seg, key):
                continue
            if i and _is_word_char(text[i - 1]):
                continue
            if end < n and _is_word_char(text[end]):
                continue
            return key
        return None

    def find(self, text):
        """返回 [(start, end, key)]：按位置升序、同位置最长优先、互不重叠。"""
        out = []
        if not text or not self._map:
            return out
        n = len(text)
        low = self._fold(text)
        if len(low) != n:
            # 极少数字符 casefold 后长度变化（如 ß→ss）：退回逐段折叠，
            # 保证位置与原文一一对应。
            low = None
        i = 0
        while i < n:
            p2 = low[i:i + 2] if low is not None else self._fold(
                text[i:i + 2])
            key = self._probe(self._by2.get(p2), text, i, low)
            if key is None and self._by1:
                key = self._probe(self._by1.get(p2[:1]), text, i, low)
            if key is None:
                i += 1
                continue
            end = i + len(key)
            out.append((i, end, key))
            i = end
        return out

    def hits(self, text):
        """命中的 (原文, 译文)，长词优先 —— 与旧 _glossary_hits 同序。"""
        pairs = [(key, self._map[key]) for _, _, key in self.find(text)]
        pairs.sort(key=lambda kv: len(kv[0]), reverse=True)
        return pairs



_DUP_FUNCTION_RE = re.compile(r"(有有|的的|了了|是是|在在)")


def clean_translation_output(text):
    """清掉模型常见的功能词重复（有有/的的/了了…），不动正常叠词。"""
    if not text:
        return text
    return _DUP_FUNCTION_RE.sub(lambda m: m.group(1)[0], text)

_STYLE_REPLACEMENTS = (
    ("我不得不说", "说实话"),
    ("就目前而言", "现在"),
    ("就目前来看", "现在看"),
    ("并且", "而且"),
    ("此外", "还有"),
    ("然而", "可"),
    ("因此", "所以"),
    ("进行了一次", "做了一次"),
    ("作出了", "做了"),
    ("给予了", "给了"),
)


def style_cleanup_cn(text):
    """对话专用：保守去掉翻译腔词和高频空格，不改术语、数字、代码。"""
    if not text:
        return text
    out = text
    for old, new in _STYLE_REPLACEMENTS:
        out = out.replace(old, new)
    # 中文之间的半角空格；中文与中文标点之间的空格。
    out = re.sub(r"(?<=[一-鿿])[ 	]+(?=[一-鿿])", "", out)
    out = re.sub(r"[ 	]+(?=[，。！？；：、])", "", out)
    out = re.sub(r"(?<=[，。！？；：、])[ 	]+", "", out)
    # 口语里常见的三个点统一成中文省略号（代码/占位符已在上层排除）。
    out = out.replace("...", "……")
    return out


class TranslationJob(OfflineTranslationJob):
    def __init__(self, parser, server, in_dir, mode, src_lang, tgt_lang,
                 reuse_dir=None, glossary=None, output_anyway=False,
                 force_glossary=True, ctx_size=8192, out_dir=None,
                 log=None, detail=None, progress=None, cancel_event=None,
                 reference_mode=False, copy_reference=True,
                 only_referenced=False, protect_proper=True,
                 preview_reference=False, hard_glossary_keys=None,
                 translate_literals=False, auto_session_terms=False,
                 session_cache_dir=None, tm_lite=True):
        super().__init__(
            parser, server, in_dir, mode, src_lang, tgt_lang,
            reuse_dir=reuse_dir, glossary=glossary, output_anyway=output_anyway,
            force_glossary=force_glossary, ctx_size=ctx_size, out_dir=out_dir,
            log=log, detail=detail, progress=progress, cancel_event=cancel_event,
            reference_mode=reference_mode, copy_reference=copy_reference,
            only_referenced=only_referenced, protect_proper=protect_proper,
            preview_reference=preview_reference,
            hard_glossary_keys=hard_glossary_keys,
            translate_literals=translate_literals,
            auto_session_terms=auto_session_terms,
            session_cache_dir=session_cache_dir, tm_lite=tm_lite,
            parallel=1, pending_files=0, freq_names=False,
            termbase=_termbase_store(), data_root=_DATA_ROOT,
            pipeline_version=PLUGIN_INFO["version"], parallel_cap=1,
            is_writable=_writable, tm_min_entries=TM_LITE_MIN_ENTRIES,
            tm_min_ratio=TM_LITE_MIN_RATIO,
        )



def _combo_get(w):
    try:
        return w.currentText()
    except Exception:
        try:
            return w.get()
        except Exception:
            return ""


def _combo_set(w, values, idx=0):
    try:
        w.configure(values=values)
        w.current(idx)
        return
    except Exception:
        pass
    try:
        w.clear()
        w.addItems(values)
        if values:
            w.setCurrentIndex(idx)
    except Exception:
        pass


def _entry_get(w):
    """跨后端读单行输入框（Tk: ttk.Entry.get / Qt: QLineEdit.text）。"""
    for name in ("get", "text"):
        fn = getattr(w, name, None)
        if callable(fn):
            try:
                return fn() or ""
            except Exception:
                continue
    return ""


def _entry_set(w, value):
    try:
        w.delete(0, "end")
        w.insert(0, value)
        return
    except Exception:
        pass
    try:
        w.setText(value)
    except Exception:
        pass


def _multiline_get(w):
    """跨后端读多行输入框（Tk: tk.Text / Qt: QPlainTextEdit）。"""
    fn = getattr(w, "get", None)
    if callable(fn):
        try:
            return fn("1.0", "end-1c") or ""
        except Exception:
            pass
    fn = getattr(w, "toPlainText", None) or getattr(w, "get_text", None)
    if callable(fn):
        try:
            return fn() or ""
        except Exception:
            pass
    return ""


def _multiline_set(w, value):
    try:
        w.delete("1.0", "end")
        w.insert("1.0", value)
        return
    except Exception:
        pass
    for name in ("setPlainText", "set_text", "setText"):
        fn = getattr(w, name, None)
        if callable(fn):
            try:
                fn(value)
                return
            except Exception:
                continue


def _chk_set(w, on):
    try:
        w.state(["selected" if on else "!selected"])
        return
    except Exception:
        pass
    try:
        w.setChecked(bool(on))
    except Exception:
        pass


def _chk_get(w):
    try:
        return bool(w.instate(["selected"]))
    except Exception:
        pass
    try:
        return bool(w.isChecked())
    except Exception:
        return False


class AiTranslateTab(object):
    def __init__(self, api, parent):
        self.api = api
        self.ui = api.ui
        self.parent = parent
        self.cfg = load_config()
        self.server = None
        self.parser = None
        self.dl_cancel = None
        self.tr_cancel = None
        self.scan_cancel = None
        self.probe_cache = {}
        self._last_ui_byte = -1
        self.log_lines = []
        # 术语分两层：builtin（随包只读）+ 用户覆盖层（可变），见 load_glossary_layers
        (self.gloss_user, self._gloss_set, self._gloss_deleted,
         self._gloss_clear) = load_glossary_layers()
        # 附加提示词必须由 **UI 线程**读控件后缓存到这里；工作线程只读这个
        # 字符串（1.6.0 之前工作线程直接读 tk.Text，异常被吞成空串，
        # 导致该功能实际从未生效）。
        self._prompt_extra_text = str(self.cfg.get("prompt_extra") or "")
        self._starting = False
        self._cfg_save_warned = False
        self._hw_cuda13_ok = False
        self._log_pending = False
        self.scan_info = None
        self.scan_dir = None
        self._mode_plain = self.cfg.get("mode", "auto")
        self._q = queue.Queue()
        self._tk = None

        try:
            self.parser = _load_vendor()
        except Exception:
            self.parser = None

        self._build()
        self.refresh_gloss_view()
        self.refresh_local_status()
        self.on_hw_check(silent=True)
        self._sweep_orphan_servers()

    def _sweep_orphan_servers(self):
        """后台清理上次异常退出遗留的引擎（只认本插件 exe + 父进程已死）。

        不阻塞启动：Toolhelp32 枚举通常只要几毫秒；即使失败也绝不影响
        正常流程。清到东西才写一行日志，避免每次启动都刷屏。
        """
        def work():
            try:
                killed = _kill_orphan_servers(self.cfg)
            except Exception:
                return
            if killed:
                self.ui_call(
                    self.log,
                    "已清理 %d 个上次异常退出遗留的引擎进程（%s），"
                    "显存/内存已回收"
                    % (len(killed), ", ".join("PID %d" % p for p in killed)),
                    "ok")
        threading.Thread(target=work, daemon=True).start()

    def on_hw_check(self, silent=False):
        """① 硬件检测：后台执行，给出一句话推荐并预选后端/档位。"""
        def work():
            try:
                hw = detect_hardware()
            except Exception as e:
                self.ui_call(self.log, "硬件检测失败：%s" % e, "err")
                return
            self.ui_call(self._apply_hw, hw, silent)
        threading.Thread(target=work, daemon=True).start()

    def _apply_hw(self, hw, silent=False):
        self._set_label(self.hw_label, hw["short"])
        self._hw_cuda13_ok = bool(hw.get("cuda13_ok"))
        if not self.cfg.get("variant_chosen"):
            vals = [VARIANT_LABELS[v] for v in VARIANT_ORDER]
            vi = VARIANT_ORDER.index(hw["variant"])
            _combo_set(self.variant_combo, vals, vi)
            self._on_variant(save_chosen=False)
        if not self.cfg.get("tier_chosen"):
            idx = TIER_ORDER.index(hw["tier"]) if hw["tier"] in TIER_ORDER else 0
            _combo_set(self.tier_combo, [TIER_SHORT[t] for t in TIER_ORDER], idx)
            self._on_tier(save_chosen=False)
        if not silent:
            self.log("硬件：%s" % hw["short"], "ok")
        self.refresh_local_status()

    def ui_call(self, fn, *a, **k):
        if self._tk is None:
            try:
                fn(*a, **k)
            except Exception as e:
                self._log_ui_error(fn, e)
        else:
            self._q.put((fn, a, k))

    def _log_ui_error(self, fn, exc):
        """UI 回调异常写详细日志：否则只表现为"点了没反应"。"""
        try:
            self.api.log_detail("UI 回调异常: %s — %s: %s"
                                % (getattr(fn, "__name__", fn),
                                   type(exc).__name__, exc), "err")
        except Exception:
            pass

    def _pump(self):
        # 一次把队列抽干会让主线程长时间回不到事件循环 —— 几千条日志排队时
        # 窗口表现为"卡死"。这里限制单次预算（200 项 / 15ms），剩下的下个
        # tick 继续，保证重绘与输入事件始终有机会执行。
        t0 = time.time()
        for _ in range(200):
            try:
                fn, a, k = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*a, **k)
            except Exception as e:
                self._log_ui_error(fn, e)
            if time.time() - t0 > 0.015:
                break
        top = getattr(self, "_top", None)
        if top is None:
            return
        try:
            top.after(30, self._pump)
        except Exception:
            self._tk = None
            self._top = None

    def _reg(self, w):
        """登记首个真实控件，用它拿到主窗口句柄以便启动 UI 更新泵。"""
        if self._tk is None:
            native = getattr(w, "widget", w)
            if hasattr(native, "winfo_toplevel"):
                self._tk = native
                try:
                    self._top = native.winfo_toplevel()
                    self._top.after(30, self._pump)
                except Exception:
                    pass
        return w

    def log(self, msg, tag="info"):
        line = "%s  %s" % (time.strftime("%H:%M:%S"), msg)
        self.log_lines.append(line)
        del self.log_lines[:-400]
        self.api.log(msg, tag)
        # 每条日志都整块 set_text（最多 400 行）在批量文件处理时会把主线程
        # 压死；这里只置脏标记，一个泵周期内合并成一次重绘。
        if not self._log_pending:
            self._log_pending = True
            self.ui_call(self._flush_log)

    def _flush_log(self):
        self._log_pending = False
        try:
            if self.log_view.alive():
                self.log_view.set_text("\n".join(self.log_lines))
        except Exception:
            pass

    def dl_log(self, msg):
        self.log(msg)

    def _build(self):
        ui = self.ui
        p = self.parent

        head = self._reg(ui.label(
            p, "AI 离线翻译（本地推理，不联网翻译）· 按①→⑤操作"))
        ui.pack(head, fill="x", pady=(2, 4))

        self._build_step_hw(p)
        self._build_step_runtime(p)
        self._build_step_model(p)
        self._build_step_job(p)

        self.log_view = self._reg(ui.text_view(p, height=9, mono=True))
        ui.pack(self.log_view.widget, fill="both", expand=True, pady=(4, 0))

        if self.parser is None:
            self.log("XML 解析模块加载失败（xray_pkg.zip 缺失？）", "err")

    def _section(self, p, text):
        lab = self._reg(self.ui.label(p, text))
        self.ui.pack(lab, fill="x", pady=(6, 1))
        return lab

    def _build_step_hw(self, p):
        ui = self.ui
        self._section(p, "① 硬件检测")
        r = self._reg(ui.row(p))
        ui.pack(r, fill="x", pady=1)
        self.btn_hw = self._reg(ui.button(r, "检测硬件", on_click=self.on_hw_check))
        ui.pack(self.btn_hw, side="left", padx=(0, 6))
        self.hw_label = self._reg(ui.label(r, "尚未检测"))
        ui.pack(self.hw_label, side="left")

    def _build_step_runtime(self, p):
        ui = self.ui
        self._section(p, "② 运行环境（显卡驱动 / CPU）")
        r = self._reg(ui.row(p))
        ui.pack(r, fill="x", pady=1)
        ui.pack(ui.label(r, "后端："), side="left")
        variant_vals = [VARIANT_LABELS[v] for v in VARIANT_ORDER]
        self.variant_combo = self._reg(ui.combo(
            r, values=variant_vals, width=46,
            on_change=lambda v: self._on_variant()))
        ui.pack(self.variant_combo, side="left", padx=(0, 6))
        vi = VARIANT_ORDER.index(self.cfg["variant"]) \
            if self.cfg["variant"] in VARIANT_ORDER else 0
        _combo_set(self.variant_combo, variant_vals, vi)
        self.btn_probe_rt = self._reg(
            ui.button(r, "测速选源", on_click=lambda: self.on_probe("rt")))
        ui.pack(self.btn_probe_rt, side="left", padx=(0, 4))
        self.btn_dl_rt = self._reg(
            ui.button(r, "下载运行库", on_click=lambda: self.on_download("rt")))
        ui.pack(self.btn_dl_rt, side="left", padx=(0, 4))
        self.btn_dl_cancel = self._reg(ui.button(
            r, "取消", on_click=self.on_cancel_download, state="disabled"))
        ui.pack(self.btn_dl_cancel, side="left")

        r2 = self._reg(ui.row(p))
        ui.pack(r2, fill="x", pady=1)
        ui.pack(ui.label(r2, "源："), side="left")
        self.source_combo = self._reg(ui.combo(
            r2, values=["先测速，自动选最快"], width=44))
        ui.pack(self.source_combo, side="left", padx=(0, 6))
        self.env_label = self._reg(ui.label(r2, ""))
        ui.pack(self.env_label, side="left")

    def _build_step_model(self, p):
        ui = self.ui
        self._section(p, "③ 离线模型（四选一）")
        r = self._reg(ui.row(p))
        ui.pack(r, fill="x", pady=1)
        self.tier_combo = self._reg(ui.combo(
            r, values=[TIER_SHORT[t] for t in TIER_ORDER], width=46,
            on_change=lambda v: self._on_tier()))
        ui.pack(self.tier_combo, side="left", padx=(0, 6))
        idx = TIER_ORDER.index(self.cfg["tier"]) if \
            self.cfg["tier"] in TIER_ORDER else 0
        _combo_set(self.tier_combo, [TIER_SHORT[t] for t in TIER_ORDER], idx)
        self.btn_probe_model = self._reg(ui.button(
            r, "测速选源", on_click=lambda: self.on_probe("model")))
        ui.pack(self.btn_probe_model, side="left", padx=(0, 4))
        self.btn_dl_model = self._reg(ui.button(
            r, "下载模型", on_click=lambda: self.on_download("model")))
        ui.pack(self.btn_dl_model, side="left", padx=(0, 6))
        ui.pack(ui.label(r, "自选上下文："), side="left")
        self.ctx_combo = self._reg(ui.combo(
            r, values=["4096", "8192", "16384", "32768"], width=8,
            on_change=lambda v: self._on_custom_ctx()))
        ui.pack(self.ctx_combo, side="left")
        cur_ctx = str(self.cfg.get("custom_ctx", 8192))
        _combo_set(self.ctx_combo, ["4096", "8192", "16384", "32768"],
                   ["4096", "8192", "16384", "32768"].index(cur_ctx)
                   if cur_ctx in ("4096", "8192", "16384", "32768") else 1)

        rs = self._reg(ui.row(p))
        ui.pack(rs, fill="x", pady=1)
        ui.pack(ui.label(rs, "源："), side="left")
        self.model_source_combo = self._reg(ui.combo(
            rs, values=["先测速，自动选最快"], width=44))
        ui.pack(self.model_source_combo, side="left", padx=(0, 6))
        self.model_env_label = self._reg(ui.label(rs, ""))
        ui.pack(self.model_env_label, side="left")

        r2 = self._reg(ui.row(p))
        ui.pack(r2, fill="x", pady=1)
        self.model_box = self._reg(ui.drop_box(
            r2, on_file=self._pick_model,
            placeholder="模型 .gguf（可拖入）"))
        ui.pack(self.model_box.widget, side="left", fill="x", expand=True,
                padx=(0, 6))
        self.btn_choose_model = self._reg(ui.button(
            r2, "选择模型", on_click=self.on_choose_model))
        ui.pack(self.btn_choose_model, side="left", padx=(0, 6))
        self.btn_choose_exe = self._reg(ui.button(
            r2, "自选exe", on_click=self.on_choose_exe))
        ui.pack(self.btn_choose_exe, side="left")

        self._section(p, "④ 启动引擎")
        r3 = self._reg(ui.row(p))
        ui.pack(r3, fill="x", pady=1)
        self.btn_start = self._reg(
            ui.button(r3, "启动引擎", on_click=self.on_start))
        ui.pack(self.btn_start, side="left", padx=(0, 6))
        self.btn_stop = self._reg(ui.button(
            r3, "停止", on_click=self.on_stop, state="disabled"))
        ui.pack(self.btn_stop, side="left", padx=(0, 8))
        self.engine_status = self._reg(ui.label(r3, "引擎未启动"))
        ui.pack(self.engine_status, side="left")
        self._on_tier()

    def _build_step_job(self, p):
        ui = self.ui
        self._section(p, "⑤ 翻译（自动识别全部格式，输出保持原目录结构）")

        r1 = self._reg(ui.row(p))
        ui.pack(r1, fill="x", pady=1)
        ui.pack(ui.label(r1, "输入："), side="left")
        self.in_box = self._reg(ui.drop_box(
            r1, on_file=self._pick_in, placeholder="待翻译目录（如 gamedata）"))
        ui.pack(self.in_box.widget, side="left", fill="x", expand=True,
                padx=(0, 6))
        self.btn_in = self._reg(ui.button(r1, "选择", on_click=self.on_choose_in))
        ui.pack(self.btn_in, side="left", padx=(0, 4))
        self.btn_scan = self._reg(ui.button(r1, "扫描选档", on_click=self.on_scan_dir))
        ui.pack(self.btn_scan, side="left")
        if self.cfg.get("in_dir"):
            self.in_box.set_text(self.cfg["in_dir"])

        r2 = self._reg(ui.row(p))
        ui.pack(r2, fill="x", pady=1)
        ui.pack(ui.label(r2, "输出："), side="left")
        self.out_box = self._reg(ui.drop_box(
            r2, on_file=self._pick_out,
            placeholder="输出目录（默认 输入目录/translated_hy_mt2）"))
        ui.pack(self.out_box.widget, side="left", fill="x", expand=True,
                padx=(0, 6))
        self.btn_out = self._reg(ui.button(r2, "选择", on_click=self.on_choose_out))
        ui.pack(self.btn_out, side="left")
        if self.cfg.get("out_dir"):
            self.out_box.set_text(self.cfg["out_dir"])

        r3 = self._reg(ui.row(p))
        ui.pack(r3, fill="x", pady=1)
        ui.pack(ui.label(r3, "类型："), side="left")
        mode_vals = [MODE_LABELS[m] for m in MODE_ORDER]
        self.mode_combo = self._reg(ui.combo(r3, values=mode_vals, width=22))
        ui.pack(self.mode_combo, side="left", padx=(0, 8))
        mi = MODE_ORDER.index(self.cfg["mode"]) if \
            self.cfg["mode"] in MODE_ORDER else 0
        _combo_set(self.mode_combo, mode_vals, mi)
        ui.pack(ui.label(r3, "源语言："), side="left")
        lang_vals = ["auto（按文件语言）", "eng", "rus", "ukr"]
        self.lang_combo = self._reg(ui.combo(r3, values=lang_vals, width=18))
        ui.pack(self.lang_combo, side="left", padx=(0, 6))
        li = {"auto": 0, "eng": 1, "rus": 2, "ukr": 3}.get(
            self.cfg.get("src_lang", "auto"), 0)
        _combo_set(self.lang_combo, lang_vals, li)
        self.force_chk = self._reg(ui.checkbox(r3, "强制术语", on_toggle=None))
        ui.pack(self.force_chk, side="left")
        _chk_set(self.force_chk, self.cfg.get("glossary_force", True))

        # 选项区（★ 1.9.20 精简）：只保留真正需要用户取舍的开关，每项下
        # 挂一行开/关后果说明。引用层/完整补丁/TM-lite/会话词表已收编为
        # 内部固定开启（关闭只有坏处，见 on_run 与各收编点注释）。
        r4 = self._reg(ui.row(p))
        ui.pack(r4, fill="x", pady=1)
        self.only_ref_chk = self._reg(ui.checkbox(
            r4, "只译被引用条目", on_toggle=None))
        ui.pack(self.only_ref_chk, side="left")
        _chk_set(self.only_ref_chk, self.cfg.get("only_referenced", False))
        h4 = self._reg(ui.row(p))
        ui.pack(h4, fill="x", pady=(0, 1))
        ui.pack(ui.label(h4, "　开：游戏没引用到的条目不送模型（省请求）；关：全量翻译，动态引用更保险"),
                side="left")

        r5 = self._reg(ui.row(p))
        ui.pack(r5, fill="x", pady=(0, 1))
        self.protect_chk = self._reg(ui.checkbox(
            r5, "保护专名/歌名/型号", on_toggle=None))
        ui.pack(self.protect_chk, side="left", padx=(0, 6))
        _chk_set(self.protect_chk, self.cfg.get("protect_proper_v2", True))
        self.preview_chk = self._reg(ui.checkbox(
            r5, "先预览（不写盘）", on_toggle=None))
        ui.pack(self.preview_chk, side="left")
        _chk_set(self.preview_chk, self.cfg.get("preview_reference", False))
        self.literal_chk = self._reg(ui.checkbox(
            r5, "翻译脚本/XML 字面文本", on_toggle=None))
        ui.pack(self.literal_chk, side="left", padx=(6, 0))
        _chk_set(self.literal_chk, self.cfg.get("translate_literals", True))
        h5 = self._reg(ui.row(p))
        ui.pack(h5, fill="x", pady=(0, 1))
        ui.pack(ui.label(h5, "　保护专名：开=人名/歌名/型号整条跳过模型只做子串替换；关=全部送模型可能被改写。"
                         "字面文本：关=脚本/UI 里写死的文字不翻。先预览：试运行只统计不写盘"),
                side="left")

        ex1 = self._reg(ui.row(p))
        ui.pack(ex1, fill="x", pady=(3, 0))
        ui.pack(ui.label(ex1, "附加提示词/约束（可选，追加到内置规则后）："),
                side="left")
        self.btn_extra_demo = self._reg(ui.button(
            ex1, "填入示例", on_click=self.on_extra_demo))
        ui.pack(self.btn_extra_demo, side="left", padx=(6, 0))
        ex2 = self._reg(ui.row(p))
        ui.pack(ex2, fill="x", pady=(0, 1))
        self.extra_prompt = self._reg(ui.text(ex2, height=2, wrap="word"))
        ui.pack(self.extra_prompt, side="left", fill="x", expand=True)
        _multiline_set(self.extra_prompt, self.cfg.get("prompt_extra", ""))

        g1 = self._reg(ui.row(p))
        ui.pack(g1, fill="x", pady=(3, 1))
        ui.pack(ui.label(g1, "术语："), side="left")
        self.gk_entry = self._reg(ui.entry(g1, width=16))
        ui.pack(self.gk_entry, side="left", padx=(0, 3))
        ui.pack(ui.label(g1, "→"), side="left", padx=(0, 3))
        self.gv_entry = self._reg(ui.entry(g1, width=16))
        ui.pack(self.gv_entry, side="left", padx=(0, 4))
        self.btn_gloss_add = self._reg(ui.button(
            g1, "添加", on_click=self.on_gloss_add))
        ui.pack(self.btn_gloss_add, side="left", padx=(0, 3))
        self.btn_gloss_del = self._reg(ui.button(
            g1, "删除", on_click=self.on_gloss_del))
        ui.pack(self.btn_gloss_del, side="left", padx=(0, 4))
        self.gloss_count = self._reg(ui.label(g1, "共 0 条"))
        ui.pack(self.gloss_count, side="left")

        g2 = self._reg(ui.row(p))
        ui.pack(g2, fill="x", pady=1)
        self.btn_gloss_import = self._reg(ui.button(
            g2, "导入JSON", on_click=self.on_gloss_import))
        ui.pack(self.btn_gloss_import, side="left", padx=(0, 4))
        self.btn_gloss_export = self._reg(ui.button(
            g2, "导出JSON", on_click=self.on_gloss_export))
        ui.pack(self.btn_gloss_export, side="left", padx=(0, 4))
        self.btn_gloss_clear = self._reg(ui.button(
            g2, "清空", on_click=self.on_gloss_clear))
        ui.pack(self.btn_gloss_clear, side="left")

        r5 = self._reg(ui.row(p))
        ui.pack(r5, fill="x", pady=(4, 1))
        self.btn_run = self._reg(
            ui.button(r5, "开始翻译", on_click=self.on_run))
        ui.pack(self.btn_run, side="left", padx=(0, 6))
        self.btn_run_cancel = self._reg(ui.button(
            r5, "取消", on_click=self.on_cancel_run, state="disabled"))
        ui.pack(self.btn_run_cancel, side="left")

    def current_tier(self):
        label = _combo_get(self.tier_combo)
        return _LABEL_TO_TIER.get(label, "7b")

    def current_variant(self):
        label = _combo_get(self.variant_combo)
        if label.startswith("CUDA 13"):
            return "cuda13"
        if label.startswith("CUDA"):
            return "cuda"
        if label.startswith("Vulkan"):
            return "vulkan"
        return "cpu"

    def _on_variant(self, save_chosen=True):
        v = self.current_variant()
        if v == "cuda13" and not getattr(self, "_hw_cuda13_ok", False):
            self.log("当前驱动/显卡可能不支持 CUDA 13.4（需 580+ 驱动 / "
                     "Turing+）；仍可尝试，启动失败会自动回退 CUDA 12.4", "warn")
        self.cfg["variant"] = v
        if save_chosen:
            self.cfg["variant_chosen"] = True
        save_config(self.cfg)
        if getattr(self, "source_combo", None) is not None:
            _combo_set(self.source_combo, ["先测速，自动选最快"], 0)
            try:
                self.env_label.configure(text="")
            except Exception:
                pass
        self.refresh_local_status()
        if v in GPU_VARIANTS and not self.find_exe(v):
            self.log("%s 运行库未安装，点②「下载运行库」"
                     % VARIANT_LABELS[v].split("（")[0], "warn")

    def model_candidates(self, tier=None):
        tier = tier or self.current_tier()
        cands = []
        if tier == "custom":
            p = self.cfg.get("custom_model_path", "")
            return [p] if p else []
        cfg_path = self.cfg.get("model_paths", {}).get(tier)
        if cfg_path:
            cands.append(cfg_path)
        cands.append(os.path.join(self.cfg.get("model_dir", _MODELS_DIR),
                                  TIER_FILE[tier]))
        cands.append(os.path.join(_MODELS_DIR, TIER_FILE[tier]))
        cands.append(os.path.join(_SHIPPED_MODELS_DIR, TIER_FILE[tier]))
        return cands

    def find_model(self, tier=None):
        for c in self.model_candidates(tier):
            if c and os.path.isfile(c):
                return c
        return None

    def find_exe(self, variant=None):
        """严格按 variant 查找，**绝不跨后端回退**：
        否则选了 CUDA/Vulkan 却静默跑 CPU（GPU 不工作且难察觉）。"""
        variant = variant or self.current_variant()
        manual = self.cfg.get("exe_paths", {}).get(variant)
        if manual and os.path.isfile(manual):
            if self._exe_matches_variant(manual, variant):
                return manual
        for base in _bin_dirs():
            direct = os.path.join(base, variant, "llama-server.exe")
            if not os.path.isfile(direct):
                continue
            if variant in ("cuda", "cuda13") and not self._cudart_present(
                    os.path.dirname(direct)):
                continue
            return direct
        return None

    @staticmethod
    def _cudart_present(d):
        """CUDA 运行时是否**完整**安装。

        1.6.0 之前只要任意一个文件以 cudart/cublas/nvrtc 开头就算装好 ——
        半套解压也会被判为可用，随后 ggml-cuda 初始化失败、静默回退 CPU。
        现在要求 3 组标记**全部**命中；安装成功时会额外写一个 marker。
        """
        try:
            files = [f.lower() for f in os.listdir(d)]
        except OSError:
            return False
        has_core = any(f.startswith("cudart64") for f in files)
        if _CUDART_MARK in files:
            return has_core          # marker 在但核心 DLL 被删 → 仍需重装
        return all(any(f.startswith(m) for f in files) for m in _CUDART_MARKS)

    @staticmethod
    def _exe_matches_variant(exe_path, variant):
        """校验手动指定的 llama-server.exe 所在目录确实是该后端构建。"""
        d = os.path.dirname(exe_path)
        if os.path.basename(os.path.dirname(d)).lower() == variant:
            return True
        try:
            files = {f.lower() for f in os.listdir(d)}
        except OSError:
            return False
        if variant in ("cuda", "cuda13") and any(
                f.startswith("ggml-cuda") for f in files):
            return True
        if variant == "vulkan" and any("vulkan" in f for f in files):
            return True
        if variant == "cpu":
            has_gpu_backend = any(
                f.startswith("ggml-cuda") or "vulkan" in f
                for f in files)
            return not has_gpu_backend
        return False

    def refresh_local_status(self):
        model = self.find_model()
        exe = self.find_exe()

        def upd():
            if getattr(self, "model_box", None) is not None:
                self.model_box.set_text(model or "")
            if self.server and self.server.is_running():
                self._set_label(self.engine_status, "运行中（端口 %d）"
                                % self.server.port)
            else:
                bits = ["模型就绪" if model else "模型缺失",
                        "运行库就绪" if exe else "运行库缺失"]
                self._set_label(self.engine_status,
                                "未启动（%s）" % " · ".join(bits))
            env = ("运行库已装 · %s" % _DATA_ROOT) if exe else "运行库未安装"
            self._set_label(self.env_label, env)
        self.ui_call(upd)
        try:
            self._persist_cfg()
        except Exception:
            pass

    def _set_label(self, w, text):
        try:
            w.configure(text=text)
            return
        except Exception:
            pass
        try:
            w.setText(text)
        except Exception:
            pass

    def _push_prompt_extra(self):
        """把「附加提示词/约束」推给引擎对象，并留痕一次。

        **只读 `_prompt_extra_text` 缓存，不碰控件**：本方法由 on_start/on_run
        的**工作线程**调用；tk.Text 在非主线程读取会抛
        `RuntimeError: main thread is not in main loop`（1.6.0 之前就是这样被
        吞成空串，功能实际从未生效）。缓存由 UI 线程的 _persist_cfg 更新。
        """
        if self.server is None:
            return
        txt = str(getattr(self, "_prompt_extra_text", "") or "").strip()
        self.server.prompt_extra = txt
        if txt:
            self.ui_call(
                self.log, "已启用附加提示词（%d 字符），作为「补充要求」"
                          "追加在内置规则后" % len(txt))

    def _persist_cfg(self):
        self.cfg["tier"] = self.current_tier()
        self.cfg["variant"] = self.current_variant()
        try:
            self.cfg["in_dir"] = self.in_box.get_text()
            self.cfg["out_dir"] = self.out_box.get_text()
        except Exception:
            pass
        lang = _combo_get(self.lang_combo)
        self.cfg["src_lang"] = re.split(r"[\s（(]", lang, 1)[0]
        for k in MODE_ORDER:
            if _combo_get(self.mode_combo).startswith(k):
                self.cfg["mode"] = k
                break
        self._mode_plain = self.cfg["mode"]
        try:
            self.cfg["glossary_force"] = _chk_get(self.force_chk)
        except Exception:
            pass
        for key, widget in (
                ("only_referenced", self.only_ref_chk),
                ("protect_proper_v2", self.protect_chk),
                ("preview_reference", self.preview_chk),
                ("translate_literals", self.literal_chk)):
            try:
                self.cfg[key] = _chk_get(widget)
            except Exception:
                pass
        try:
            self.cfg["prompt_extra"] = _multiline_get(
                self.extra_prompt).strip()
            # UI 线程读好 → 缓存给工作线程用（见 _push_prompt_extra）
            self._prompt_extra_text = self.cfg["prompt_extra"]
        except Exception:
            pass
        if not save_config(self.cfg) and not self._cfg_save_warned:
            self._cfg_save_warned = True
            self.log("配置保存失败（%s）：本次设置只在内存生效，重启后会丢失"
                     % (_LAST_CONFIG_ERROR or "未知原因"), "warn")
        elif _LAST_CONFIG_ERROR == "":
            self._cfg_save_warned = False

    def on_extra_demo(self):
        """填入示例模板：说明这个框是用来放哪类要求的（可整段删改）。"""
        _multiline_set(self.extra_prompt, EXTRA_PROMPT_DEMO)
        self.cfg["prompt_extra"] = EXTRA_PROMPT_DEMO
        self._prompt_extra_text = EXTRA_PROMPT_DEMO
        self.log("已填入示例模板（可整段删改）。注意此处是软引导："
                 "模型多数情况会遵守，但不保证 100%；"
                 "必须强制统一的译名请用「强制术语」放进术语库。", "warn")

    def refresh_gloss_view(self):
        n = len(self.gloss_user)

        def upd():
            self._set_label(self.gloss_count, "共 %d 条" % n)
        self.ui_call(upd)

    def on_gloss_add(self):
        k = _entry_get(self.gk_entry).strip()
        v = _entry_get(self.gv_entry).strip()
        if not k or not v:
            self.log("请填写原文与强制译文两项", "warn")
            return
        self.gloss_user[k] = v
        self._gloss_set[k] = v
        self._gloss_deleted.discard(k)
        try:
            save_user_glossary(self._gloss_set, self._gloss_deleted,
                               self._gloss_clear)
        except Exception as e:
            self.log("术语库保存失败：%s" % e, "err")
            return
        _entry_set(self.gk_entry, "")
        _entry_set(self.gv_entry, "")
        self.refresh_gloss_view()
        self.log("已保存术语：%s => %s（共 %d 条）"
                 % (k, v, len(self.gloss_user)), "ok")

    def on_gloss_del(self):
        k = _entry_get(self.gk_entry).strip()
        if not k:
            self.log("在左侧输入框填写要删除的原文", "warn")
            return
        if k in self.gloss_user:
            del self.gloss_user[k]
            if k in self._gloss_set:
                del self._gloss_set[k]          # 删的是自己加的
            else:
                self._gloss_deleted.add(k)      # 删的是内置词条 → 记墓碑
            try:
                save_user_glossary(self._gloss_set, self._gloss_deleted,
                                   self._gloss_clear)
                self.log("已删除术语：%s" % k)
            except Exception as e:
                self.log("术语库保存失败：%s" % e, "err")
        else:
            self.log("术语库中没有该原文：%s" % k, "warn")
        _entry_set(self.gk_entry, "")
        _entry_set(self.gv_entry, "")
        self.refresh_gloss_view()

    def on_gloss_clear(self):
        if not self.gloss_user:
            return
        ok = self.ui.ask_yes_no(
            self._tk, "清空术语库",
            "确定清空内置术语库的全部 %d 条记录吗？" % len(self.gloss_user))
        if not ok:
            return
        self.gloss_user = {}
        self._gloss_set = {}
        self._gloss_deleted = set()
        self._gloss_clear = True               # 连内置词条一起隐藏
        try:
            save_user_glossary(self._gloss_set, self._gloss_deleted,
                               self._gloss_clear)
        except Exception as e:
            self.log("术语库保存失败：%s" % e, "err")
            return
        self.refresh_gloss_view()
        self.log("术语库已清空（内置词条被用户层隐藏；重新导入即可恢复）")

    def on_gloss_import(self):
        try:
            f = self.ui.ask_open_file(
                self._tk, "导入术语表 JSON（合并，同名词条以导入文件为准）",
                filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        except Exception:
            f = None
        if not f:
            return
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            self.log("术语表读取失败：%s" % e, "err")
            return
        if not isinstance(data, dict):
            self.log("术语表格式应为 {\"原文\": \"译文\"} 的 JSON 对象", "err")
            return
        incoming = flatten_glossary(data)
        if not incoming:
            self.log("术语表为空或格式无法识别（支持扁平表与 {\"分类\": {...}} 分类表）",
                     "err")
            return
        before = len(self.gloss_user)
        self.gloss_user.update(incoming)
        self._gloss_set.update(incoming)
        # 导入的条目优先生效：把它们从"墓碑"里移除
        self._gloss_deleted.difference_update(incoming)
        try:
            save_user_glossary(self._gloss_set, self._gloss_deleted,
                               self._gloss_clear)
        except Exception as e:
            self.log("术语库保存失败：%s" % e, "err")
            return
        self.refresh_gloss_view()
        self.log("已从 %s 合并 %d 条（术语库 %d → %d 条）"
                 % (os.path.basename(f), len(incoming), before,
                    len(self.gloss_user)), "ok")

    def on_gloss_export(self):
        try:
            f = self.ui.ask_save_file(
                self._tk, "导出术语表 JSON",
                initialfile="glossary_export.json",
                filetypes=[("JSON", "*.json")])
        except Exception:
            f = None
        if not f:
            return
        if not f.lower().endswith(".json"):
            f += ".json"
        try:
            tmp = f + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.gloss_user, fh, ensure_ascii=False,
                          indent=1, sort_keys=True)
            os.replace(tmp, f)
        except Exception as e:
            self.log("导出失败：%s" % e, "err")
            return
        self.log("已导出 %d 条术语：%s" % (len(self.gloss_user), f), "ok")

    def _pick_model(self, path):
        if path.lower().endswith(".gguf"):
            tier = self.current_tier()
            if tier == "custom":
                self.cfg["custom_model_path"] = path
            else:
                self.cfg.setdefault("model_paths", {})[tier] = path
            save_config(self.cfg)
            self.refresh_local_status()

    def _pick_in(self, path):
        if os.path.isdir(path):
            self.cfg["in_dir"] = path
            self._persist_cfg()
            self._default_out(path)

    def _default_out(self, in_dir):
        """输出目录默认 输入目录/translated_hy_mt2（用户可改）。"""
        cur = self.out_box.get_text() if getattr(self, "out_box", None) else ""
        if not cur:
            d = os.path.join(in_dir, "translated_hy_mt2")
            self.out_box.set_text(d)
            self.cfg["out_dir"] = d
            save_config(self.cfg)

    def _pick_out(self, path):
        if os.path.isdir(path):
            self.cfg["out_dir"] = path
            self._persist_cfg()

    def _ask_dir(self, title):
        try:
            from tkinter import filedialog
            d = filedialog.askdirectory(title=title, parent=self._tk)
            return d or None
        except Exception:
            self.log("无法弹出目录选择框，请把文件夹直接拖入输入框", "warn")
            return None

    def on_choose_model(self):
        try:
            f = self.ui.ask_open_file(
                self._tk, "选择 GGUF 模型",
                filetypes=[("GGUF 模型", "*.gguf"), ("所有文件", "*.*")])
        except Exception:
            f = None
        if f:
            self._pick_model(f)

    def on_choose_exe(self):
        try:
            f = self.ui.ask_open_file(
                self._tk, "选择 llama-server.exe",
                filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        except Exception:
            f = None
        if f and os.path.basename(f).lower() == "llama-server.exe":
            self.cfg.setdefault("exe_paths", {})[self.current_variant()] = f
            save_config(self.cfg)
            self.refresh_local_status()

    def on_choose_in(self):
        d = self._ask_dir("选择待翻译目录")
        if d:
            self.in_box.set_text(d)
            self._pick_in(d)

    def on_choose_out(self):
        d = self._ask_dir("选择译文输出目录")
        if d:
            self.out_box.set_text(d)
            self._pick_out(d)

    def _on_custom_ctx(self):
        try:
            v = int(_combo_get(self.ctx_combo))
        except Exception:
            return
        if v in CUSTOM_CTX_CHOICES:
            self.cfg["custom_ctx"] = v
            save_config(self.cfg)
            if self.current_tier() == "custom":
                self.refresh_local_status()

    def _on_tier(self, save_chosen=True):
        """切档：更新状态；引擎运行中切档提示需重启。"""
        tier = self.current_tier()
        self.cfg["tier"] = tier
        if save_chosen:
            self.cfg["tier_chosen"] = True
        save_config(self.cfg)
        try:
            self.ui.set_state(self.ctx_combo,
                              "normal" if tier == "custom" else "disabled")
        except Exception:
            pass
        if getattr(self, "model_box", None) is not None:
            self.refresh_local_status()
        if getattr(self, "model_source_combo", None) is not None:
            _combo_set(self.model_source_combo,
                       ["先测速，自动选最快"], 0)
            try:
                self.model_env_label.configure(text="")
            except Exception:
                pass
        if self.server and self.server.is_running():
            self.log("已切换模型档，运行中的引擎仍是旧模型，重启引擎生效",
                     "warn")

    def _probe_task_key(self, kind):
        if kind == "rt":
            return "bin_" + self.current_variant()
        tier = self.current_tier()
        return TIER_PROFILES[tier]["task"] or \
            TIER_PROFILES["mt2"]["task"]

    def _sources_for(self, kind):
        key = self._probe_task_key(kind)
        return DOWNLOAD_TASKS[key], key

    def _source_combo(self, kind):
        return self.source_combo if kind == "rt" else self.model_source_combo

    def _populate_sources(self, kind, results):
        """把测速结果填入对应区的“源”下拉框：可达在前（按速度），
        不可达在后供人工尝试。"""
        combo = self._source_combo(kind)
        labels = []
        for r in results:
            if r.ok:
                labels.append("%s · %.2f MB/s" % (r.name, r.speed))
            else:
                labels.append("%s · 不可达" % r.name)
        if not labels:
            labels = ["测速失败，请重试"]
        _combo_set(combo, labels, 0)

    def on_probe(self, kind):
        """测速：kind='rt' 当前后端运行库；kind='model' 当前模型档。
        未测速过时，下载会自动先测速再拉取。"""
        if kind == "model" and self.current_tier() == "custom":
            self.log("自选档使用本机 .gguf，无需测速下载", "warn")
            return
        task, key = self._sources_for(kind)
        btn = self.btn_probe_rt if kind == "rt" else self.btn_probe_model
        self.ui.set_state(btn, "disabled")
        self.log("测速：%s" % task["label"])

        def work():
            try:
                results = run_probe(
                    task["sources"],
                    log=lambda m, tag="info": self.ui_call(self.log, m, tag))
                self.probe_cache[key] = results
                env = detect_environment(results)

                def done():
                    self._populate_sources(kind, results)
                    label = self.model_env_label if kind == "model" \
                        else self.env_label
                    self._set_label(label, env)
                    self.log(env)
                    self.ui.set_state(btn, "normal")
                self.ui_call(done)
            except Exception as e:
                self.ui_call(self.log, "测速失败：%s" % e, "err")
                self.ui_call(self.ui.set_state, btn, "normal")
        threading.Thread(target=work, daemon=True).start()

    def _resolve_download_sources(self, kind, key, task, chosen_label=""):
        """按用户在“源”下拉框的选择决定下载顺序。
        返回 (ordered[(name,url)], auto_probed, chosen_result|None)。
        选中源排第一，其余可达源按测速排序依次备援，失败自动切换。

        chosen_label 由 **UI 线程**读好再传进来：本方法跑在下载工作线程，
        直接去读 Tk 控件（_combo_get）属于跨线程访问 GUI，会出现偶发异常或
        读到半更新状态（文件自己的 _plan_engine 就立了"控件值必须由 UI 线程
        预先传入"的规矩，这里是唯一漏网点）。
        """
        results = self.probe_cache.get(key)
        auto = False
        if results is None:
            results = run_probe(
                task["sources"],
                log=lambda m, tag="info": self.ui_call(self.log, m, tag))
            self.probe_cache[key] = results
            auto = True
            self.ui_call(lambda: self._populate_sources(kind, results))
        ok = sorted((r for r in results if r.ok),
                    key=lambda r: r.speed, reverse=True)
        chosen = None
        if chosen_label and " · " in chosen_label:
            sel_name = chosen_label.split(" · ", 1)[0]
            chosen = next((r for r in results if r.name == sel_name), None)
        ordered = []
        if chosen is not None:
            ordered.append((chosen.name, chosen.url))
        for r in ok:
            if chosen is None or r.name != chosen.name:
                ordered.append((r.name, r.url))
        seen = {n for n, _ in ordered}
        for n, u in task["sources"]:
            if n not in seen:
                ordered.append((n, u))
        return ordered, auto, chosen

    def on_download(self, kind):
        if self.dl_cancel is not None:
            self.log("已有下载任务在进行", "warn")
            return
        task, key = self._sources_for(kind)
        if kind == "model" and self.current_tier() == "custom":
            self.log("自选档请点「选择模型」指定本机 .gguf，无需下载", "warn")
            return
        self.dl_cancel = threading.Event()
        chosen_label = _combo_get(self._source_combo(kind))

        def progress(done, total, speed, eta, name):
            if total and done - getattr(self, "_last_ui_byte", -1) > 8*1024*1024:
                self._last_ui_byte = done
                self.ui_call(self.log,
                             "  %.0f%%（%s/%s）%.1f MB/s 剩余 %s"
                             % (100.0*done/total, _fmt_size(done),
                                _fmt_size(total), speed, _fmt_eta(eta)))

        def work():
            try:
                ordered, auto, chosen = self._resolve_download_sources(
                    kind, key, task, chosen_label)
                if auto:
                    self.ui_call(self.log, "自动测速完成，可在“源”下拉框改选")
                if chosen is not None and not chosen.ok:
                    self.ui_call(self.log,
                                 "你选择的 %s 测速不可达，将先尝试它、"
                                 "失败后自动切换其他源" % chosen.name, "warn")
                if task["kind"] == "model":
                    model_dir = self.cfg.get("model_dir") or _MODELS_DIR
                    os.makedirs(model_dir, exist_ok=True)
                    target = os.path.join(model_dir, task["filename"])
                else:
                    target = os.path.join(
                        _DOWNLOAD_DIR, task["variant"] + "_runtime.zip")
                if os.path.isfile(target) and task["kind"] == "model":
                    self.ui_call(self.log, "模型已存在：%s" % target, "warn")
                    return
                if task["kind"] == "zip":
                    self.ui_call(self.log,
                                 "提示：运行库将从 GitHub 及其公共镜像下载，"
                                 "下载后会按官方 SHA-256 校验；原始包保留在 %s"
                                 % _DOWNLOAD_DIR, "warn")
                self.ui_call(self.log, "下载：%s（首选 %s，共 %d 个源备援）"
                             % (task["label"], ordered[0][0], len(ordered) - 1))
                # 校验放在 download_one 内部逐个源进行：某个镜像给了坏文件时
                # 删掉它并自动换下一个源（1.6.0 之前只是删文件让用户手动重试）。
                digest = download_one(
                    ordered, target,
                    log=lambda m, tag="info": self.ui_call(self.log, m, tag),
                    progress=progress,
                    cancel_event=self.dl_cancel,
                    verify=lambda p: verify_artifact(
                        p, task,
                        log=lambda m, tag="info": self.ui_call(self.log, m, tag),
                        known_hashes=self.cfg.get("artifact_hashes")))
                self.cfg.setdefault("artifact_hashes", {})[
                    os.path.basename(target)] = digest
                save_config(self.cfg)
                if task["kind"] == "zip":
                    dest = os.path.join(_BIN_DIR, task["variant"])
                    exe = extract_zip(
                        target, dest, "llama-server.exe",
                        log=lambda m, tag="info": self.ui_call(self.log, m, tag))
                    try:
                        os.remove(target)
                    except OSError:
                        pass
                    extra = task.get("extra_runtime")
                    if extra:
                        self._install_extra_runtime(extra, dest)
                    self.ui_call(self.log, "运行库就绪：%s" % exe, "ok")
                else:
                    tier = TIER_BY_TASK[key]
                    self.cfg.setdefault("model_paths", {})[tier] = target
                    save_config(self.cfg)
                    self.ui_call(self.log, "模型下载完成：%s" % target, "ok")
            except DownloadCancelled:
                self.ui_call(self.log, "已取消，断点已保留")
            except InvalidArtifact as e:
                self.ui_call(self.log,
                             "下载产物校验未通过（已依次尝试所有源）：%s" % e,
                             "err")
            except Exception as e:
                self.ui_call(self.log, "下载失败：%s" % e, "err")
            finally:
                self.ui_call(self._after_download)
        threading.Thread(target=work, daemon=True).start()
        self.ui.set_state(self.btn_dl_rt, "disabled")
        self.ui.set_state(self.btn_dl_model, "disabled")
        self.ui.set_state(self.btn_dl_cancel, "normal")

    def _after_download(self):
        self.dl_cancel = None
        self._last_ui_byte = -1
        self.ui.set_state(self.btn_dl_rt, "normal")
        self.ui.set_state(self.btn_dl_model, "normal")
        self.ui.set_state(self.btn_dl_cancel, "disabled")
        self.refresh_local_status()

    def _install_extra_runtime(self, extra, dest):
        """CUDA 后端第二步：下载并解压 cudart 运行时；完整安装则跳过。"""
        if self._cudart_present(dest):
            self.ui_call(self.dl_log,
                         "%s 已存在，跳过" % extra["label"])
            return
        self.ui_call(self.dl_log,
                     "继续下载%s（CUDA 必需，否则会回退 CPU）…"
                     % extra["label"])
        sources = _gh_sources(extra["url"])
        rt_zip = os.path.join(_DOWNLOAD_DIR, "cudart_runtime.zip")
        results = run_probe(sources)
        ok = [(r.name, r.url) for r in results if r.ok]
        if not ok:
            raise IOError("无法下载 %s：所有源不可达" % extra["label"])

        def rt_progress(done, total, speed, eta, name):
            if done % (20 * 1024 * 1024) >= 1048576:
                return
            self.ui_call(self.dl_log, "  cudart：%.0f%% %.1f MB/s"
                         % (100.0 * done / total if total else 0, speed))

        rt_task = {"kind": "zip", "name_prefix": "cudart64",
                   "size_hint": 0.37, "sha256": extra.get("sha256", "")}
        digest = download_one(
            ok, rt_zip,
            log=lambda m: self.ui_call(self.dl_log, m),
            progress=rt_progress,
            cancel_event=self.dl_cancel,
            verify=lambda p: verify_artifact(
                p, rt_task,
                log=lambda m, tag="info": self.ui_call(self.dl_log, m),
                known_hashes=self.cfg.get("artifact_hashes")))
        self.cfg.setdefault("artifact_hashes", {})["cudart_runtime.zip"] = digest
        save_config(self.cfg)
        _extract_zip_into(rt_zip, dest)
        try:
            with open(os.path.join(dest, _CUDART_MARK), "w",
                      encoding="utf-8") as f:
                json.dump({"sha256": digest, "installed": time.strftime(
                    "%Y-%m-%d %H:%M:%S")}, f)
        except OSError:
            pass
        try:
            os.remove(rt_zip)
        except OSError:
            pass
        self.ui_call(self.dl_log, "%s 安装完成" % extra["label"])

    def on_cancel_download(self):
        if self.dl_cancel:
            self.dl_cancel.set()
            self.dl_log("正在取消…")

    def _selected_mode(self):
        for k in ("text", "cfgxml", "ltx", "scriptE", "scriptL"):
            if _combo_get(self.mode_combo).startswith(k):
                return k
        return "text"

    def on_scan_dir(self):
        if self.scan_cancel is not None:
            self.scan_cancel.set()
            self.log("正在取消勘察…", "warn")
            return
        d = self.in_box.get_text()
        if not d or not os.path.isdir(d):
            self.log("请先选择有效的待翻译目录", "warn")
            return
        if os.path.dirname(os.path.abspath(d)) == os.path.abspath(d):
            try:
                ok = self.ui.ask_yes_no(
                    self._tk, "目录看起来是磁盘根目录",
                    "「%s」是磁盘根目录，勘察会遍历整盘、耗时可能很长。\n"
                    "通常应选游戏或模组的 gamedata / configs 目录。\n"
                    "仍要继续吗？" % d)
            except Exception:
                ok = False
            if not ok:
                self.log("已取消勘察（建议改选 gamedata 或 configs 目录）")
                return
        mode = self._selected_mode()
        self._mode_plain = mode
        self.scan_cancel = threading.Event()
        self._set_label(self.btn_scan, "取消勘察")
        self.log("正在勘察目录（文件/最长文本/上下文需求）…再点一次可取消")

        def work():
            try:
                info = analyze_input_dir(
                    d, mode,
                    log=lambda m: self.ui_call(self.log, m),
                    cancel=self.scan_cancel)
                if not info.get("cancelled"):
                    self.scan_info = info
                    self.scan_dir = d
                self.ui_call(self._report_scan, info, mode)
            except Exception as e:
                self.ui_call(self.log, "勘察失败：%s" % e, "err")
            finally:
                self.scan_cancel = None
                self.ui_call(self._set_label, self.btn_scan, "扫描选档")
        threading.Thread(target=work, daemon=True).start()

    def _report_scan(self, info, mode):
        if info.get("cancelled"):
            self.log("已取消勘察（统计不完整，档位保持不变）", "warn")
            return
        rec_tier = info["recommend"]
        if info.get("truncated"):
            self.log("勘察达到文件数上限提前结束，结果只覆盖已读部分，"
                     "仅供参考", "warn")
        ready = [t for t in TIER_ORDER if self.find_model(t)]
        if rec_tier not in ready:
            pref = [t for t in (rec_tier, "mt2", "7b", "custom")
                    if t in ready]
            if pref:
                self.log("推荐的 %s 档模型尚未下载，已改选本机就绪的 %s 档"
                         "（下载对应模型后可手动切回）"
                         % (TIER_PROFILES[rec_tier]["combo"][:2],
                            TIER_PROFILES[pref[0]]["combo"][:2]), "warn")
                rec_tier = pref[0]
        rec = TIER_PROFILES[rec_tier]
        self.log("勘察结果：文件 %d 个 / %.1f MB；最大单文件 %.0f KB；"
                 "可翻译文本约 %d 条，最长 %d 字符（%s）"
                 % (info["files"], info["bytes"] / 1048576.0,
                    info["max_file"][0] / 1024.0, info["n_strings"],
                    info["max_str"],
                    os.path.basename(info["max_str_file"]) or "-"))
        for t in BUILTIN_TIERS:
            n = info["overlong"][t]
            if n:
                self.log("  %s 档将对 %d 条超长文本自动分句翻译"
                         % (TIER_PROFILES[t]["combo"][:2], n))
        self.log("推荐档位：%s" % rec["combo"])
        idx = TIER_ORDER.index(rec_tier)
        _combo_set(self.tier_combo, TIER_LABELS, idx)
        self._on_tier()
        ctx = decide_ctx(rec_tier, info,
                         cap=(int(self.cfg.get("custom_ctx", 8192))
                              if rec_tier == "custom" else None))
        self.log("该目录在推荐档的自动上下文：%d token（引擎启动时生效）" % ctx)

    def _plan_engine(self, allow_scan=False, tier=None, variant=None,
                     in_dir=None, interactive=False):
        """返回 (model, exe, tier, ctx)；缺组件时返回 None。
        所有 Tk 控件值必须由调用方在 UI 线程预先传入，本函数其余部分
        只用 os/已缓存数据，可安全在工作线程运行。
        interactive=True（UI 线程调用）才弹缺组件对话框；工作线程只返回 None。
        allow_scan=False（UI 线程）只复用已勘察结果，不做大目录扫描。"""
        tier = tier or self.current_tier()
        variant = variant or self.current_variant()
        if in_dir is None:
            in_dir = self.in_box.get_text()
        model = self.find_model(tier)
        exe = self.find_exe(variant)
        if not model or not exe:
            if interactive:
                missing = "模型文件" if not model else "llama-server 运行库"
                self.ui.ask_yes_no(
                    self._tk, "缺少组件",
                    "%s未就绪。请在下方「下载管理」中下载（可先测速选源）。" % missing)
            return None
        info = None
        if in_dir and os.path.isdir(in_dir):
            if self.scan_dir == in_dir and self.scan_info is not None:
                info = self.scan_info
            elif allow_scan:
                info = analyze_input_dir(
                    in_dir, self._mode_plain,
                    log=lambda m: self.ui_call(self.log, m),
                    cancel=self.tr_cancel)
                self.scan_info, self.scan_dir = info, in_dir
        cap = (int(self.cfg.get("custom_ctx", 8192))
               if tier == "custom" else None)
        ctx = decide_ctx(tier, info, cap=cap)
        return model, exe, tier, ctx

    def on_start(self):
        self._persist_cfg()
        plan = self._plan_engine(allow_scan=False, interactive=True)
        if not plan:
            return
        model, exe, tier, ctx = plan
        variant = self.current_variant()
        if self._starting:
            self.log("正在启动引擎，请稍候", "warn")
            return
        need_stop = False
        if self.server and self.server.is_running():
            sig = (self.server.model, self.server.variant, self.server.ctx_size)
            if sig == (model, variant, ctx):
                self.log("引擎已在运行（相同模型/上下文）")
                return
            need_stop = True
        self._starting = True
        self.ui.set_state(self.btn_start, "disabled")
        self.log("正在加载 %s，上下文 %d token，首次加载较慢，请稍候…"
                 % (TIER_PROFILES[tier]["combo"][:2], ctx))

        def work():
            try:
                if need_stop:
                    # 停旧引擎会 taskkill + wait（最长约 15 秒）——必须在工作线程，
                    # 否则切模型时整个界面冻结（1.6.0 之前就是在 Tk 回调里同步停的）。
                    self.ui_call(self.log, "模型或上下文已变更，先停止旧引擎…")
                    old = self.server
                    if old is not None:
                        old.stop()
                    self.server = None
                self._spawn_server(exe, model, variant, ctx, tier)
                self.ui_call(self._engine_ui, True)
            except Exception as e:
                self.ui_call(self.log, "引擎启动失败：%s" % e, "err")
                self.ui_call(self._engine_ui, False)
            finally:
                self._starting = False
                self.ui_call(self._engine_ui,
                             bool(self.server and self.server.is_running()))
        threading.Thread(target=work, daemon=True).start()

    def _spawn_server(self, exe, model, variant, ctx, tier):
        """创建并启动引擎；CUDA 13.4 起不来或没上卡时自动回退 CUDA 12.4。

        13.4 的可用性只有"驱动 ≥ 580 / 计算能力 ≥ 7.5"这个门槛判据，
        实际能否初始化还受 OEM 驱动、Optimus、cudart DLL 等影响，所以这里
        用真实启动 + gpu_verified 兜底，失败就退回兼容面更广的 12.4。
        """
        def build(v, e):
            return LlamaServer(
                e, model, v,
                log=lambda m, tag="info": self.ui_call(self.log, m, tag),
                ctx_size=ctx, tier=tier)

        def fallback(reason):
            self.ui_call(self.log, "CUDA 13.4 %s，尝试回退 CUDA 12.4…"
                         % reason, "warn")
            try:
                srv.stop()
            except Exception:
                pass
            exe12 = self.find_exe("cuda")
            if not exe12:
                self.ui_call(self.log,
                             "未安装 CUDA 12.4 运行库，无法回退；"
                             "请下载运行库或改用 Vulkan/CPU", "err")
                return None
            srv12 = build("cuda", exe12)
            self.server = srv12
            self._push_prompt_extra()
            srv12.start()
            self.ui_call(self.log, "已回退到 CUDA 12.4", "ok")
            return srv12

        srv = build(variant, exe)
        self.server = srv
        self._push_prompt_extra()
        try:
            srv.start()
        except Exception:
            if variant != "cuda13":
                raise
            fb = fallback("启动失败")
            if fb is not None:
                return fb
            raise
        if variant == "cuda13" and not getattr(srv, "gpu_verified", False):
            fb = fallback("未能在 GPU 上初始化")
            if fb is not None:
                return fb
        return srv

    def _engine_ui(self, on):
        self.ui.set_state(self.btn_start, "normal" if not on else "disabled")
        self.ui.set_state(self.btn_stop, "normal" if on else "disabled")
        self.refresh_local_status()

    def on_stop(self):
        if self.server:
            def work():
                self.server.stop()
                self.ui_call(self._engine_ui, False)
            threading.Thread(target=work, daemon=True).start()

    def on_run(self):
        self._persist_cfg()
        if self.tr_cancel is not None:
            self.log("已有翻译任务在进行", "warn")
            return
        if self._starting:
            self.log("引擎正在启动，请稍候再开始翻译", "warn")
            return
        in_dir = self.in_box.get_text()
        if not in_dir or not os.path.isdir(in_dir):
            self.log("请先选择有效的待翻译目录", "warn")
            return
        if self.parser is None:
            self.log("XML 解析模块未就绪", "err")
            return
        out_dir = self.out_box.get_text() or \
            os.path.join(in_dir, "translated_hy_mt2")
        self.out_box.set_text(out_dir)
        self.cfg["out_dir"] = out_dir
        save_config(self.cfg)
        glossary = dict(self.gloss_user)
        force_glossary = _chk_get(self.force_chk)
        # 只给模型/强制路径提供专名类硬术语；对话/百科类普通词不注入。
        hard_glossary = load_hard_glossary_keys() | set(self._gloss_set)
        hard_glossary = hard_glossary or None
        ref_mode = True      # ★ 1.9.20：引用层/完整补丁/TM-lite/会话词表
        copy_ref = True      #   收编固定开启（关闭只有坏处），不再读勾选。
        only_ref = False
        protect_proper = False
        preview_ref = False
        translate_literals = True
        session_glossary = True
        tm_lite = True
        try:
            only_ref = bool(_chk_get(self.only_ref_chk))
            protect_proper = bool(_chk_get(self.protect_chk))
            preview_ref = bool(_chk_get(self.preview_chk))
            translate_literals = bool(_chk_get(self.literal_chk))
        except Exception:
            pass

        self.tr_cancel = threading.Event()
        self.ui.set_state(self.btn_run, "disabled")
        self.ui.set_state(self.btn_run_cancel, "normal")

        mode = self.cfg["mode"]
        src_lang = self.cfg["src_lang"]
        if ref_mode and mode not in ("auto", "text"):
            self.log("引用层模式只在「自动」或「文本XML」类型下生效；"
                     "当前类型按原流程执行", "warn")
        cap_tier = self.current_tier()
        cap_variant = self.current_variant()

        def work():
            try:
                plan = self._plan_engine(
                    allow_scan=True, tier=cap_tier,
                    variant=cap_variant, in_dir=in_dir)
                if not plan:
                    if not self.find_model(cap_tier):
                        prof = TIER_PROFILES[cap_tier]
                        want = self.cfg.get("model_paths", {}).get(cap_tier)
                        if not want:
                            fname = TIER_FILE.get(cap_tier, "*.gguf")
                            want = os.path.join(
                                self.cfg.get("model_dir", _MODELS_DIR), fname)
                        self.ui_call(self.log,
                            "「%s」档模型未就绪：%s。请下载该档模型或点「选择已有模型」"
                            "指定文件；不要直接用其他档位的引擎代替。"
                            % (prof["combo"][:2], want), "err")
                    if not self.find_exe(cap_variant):
                        self.ui_call(self.log,
                            "%s运行库未就绪，请在「下载管理」安装"
                            % VARIANT_LABELS.get(cap_variant, cap_variant),
                            "err")
                    self.ui_call(self._run_done)
                    return
                model, exe, tier, ctx = plan
                running = bool(self.server and self.server.is_running())
                reusable = running and self.server.model == model and (
                    self.server.variant == cap_variant
                    or (cap_variant == "cuda13"
                        and self.server.variant == "cuda"))
                if running and not reusable:
                    self.ui_call(self.log,
                        "正在切换到 %s 档（停止旧引擎并以新模型重启）…"
                        % TIER_PROFILES[tier]["combo"][:2], "warn")
                    self.server.stop()
                    self.server = None
                if not reusable:
                    self._spawn_server(exe, model, cap_variant, ctx, tier)
                    if getattr(self.server, "health_warn", ""):
                        self.ui_call(self.log,
                                     "注意：" + self.server.health_warn, "warn")
                    self.ui_call(self._engine_ui, True)
                self._push_prompt_extra()
                if self.scan_info and self.scan_dir == in_dir:
                    self.ui_call(self.log,
                                 "上下文 %d token；最长文本 %d 字符，超长自动分句"
                                 % (self.server.ctx_size,
                                    self.scan_info["max_str"]))

                job = TranslationJob(
                    self.parser, self.server, in_dir, mode, src_lang, "chs",
                    glossary=glossary,
                    force_glossary=force_glossary,
                    output_anyway=False,
                    ctx_size=self.server.ctx_size,
                    out_dir=out_dir,
                    log=lambda m, tag="info": self.ui_call(self.log, m, tag),
                    detail=lambda m, tag="info": self.api.log_detail(m, tag),
                    # 条目级进度只写运行日志：实测 6122 个文件时这一路
                    # 会达到 470 条/秒并全部进界面日志，是 1.7.1 后仍卡死的
                    # 主因。界面只保留每 50 个文件一条的文件进度。
                    progress=lambda d: self.api.log_detail(
                        "  进度：[%d/%d] %s（%d/%d 条，累计 %d 段）"
                        % (d.get("file_idx", 0), d.get("files", 0),
                           d.get("file", ""), d.get("ids", 0),
                           d.get("total_ids", 0), d.get("reqs", 0))),
                    cancel_event=self.tr_cancel,
                    reference_mode=ref_mode,
                    copy_reference=copy_ref,
                    only_referenced=only_ref,
                    protect_proper=protect_proper,
                    preview_reference=preview_ref,
                    hard_glossary_keys=hard_glossary,
                    translate_literals=translate_literals,
                    auto_session_terms=session_glossary,
                    tm_lite=tm_lite)
                job.run()
            except Exception as e:
                self.ui_call(self.log, "翻译失败：%s" % e, "err")
            finally:
                self.ui_call(self._run_done)
        threading.Thread(target=work, daemon=True).start()

    def _run_done(self):
        self.tr_cancel = None
        self.ui.set_state(self.btn_run, "normal")
        self.ui.set_state(self.btn_run_cancel, "disabled")

    def on_cancel_run(self):
        if self.tr_cancel:
            self.tr_cancel.set()
            self.log("正在取消（当前段落翻译完后停止）…", "warn")



def register(api):
    api.register_tool("AI 翻译", lambda parent: AiTranslateTab(api, parent),
                      order=6)
