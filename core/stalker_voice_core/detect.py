# -*- coding: utf-8 -*-
"""游戏根目录体检（纯标准库，只读，绝不写游戏目录）。

骨架版实现三件事，供 scan 任务与 UI 体检页直接调用：
  1. detect_game(root)：识别 X-Ray 系游戏类型（启发式，真机校验点已在
     注释标明——三作引擎 exe 命名有重叠，宁可报 unknown_xray 也不猜）；
  2. read_fsgame(root)：解析 fsgame.ltx 的 $game_data$/$game_sounds$ 行，
     读出双布尔开关与路径段；
  3. check_jsgme(root) / list_archives(root)：JSGME 就位检查与存档枚举。

磁盘纪律：本模块全部 os.path / open 只读操作，无任何写入。
"""

import os
import re

# 识别表：exe 文件名（小写）→ 游戏类型。
# 注意：晴空与普Call 共用 xrEngine.exe，二者靠附加证据区分；
# 证据不足时返回 unknown_xray，绝不猜。
GAME_EXE_HINTS = (
    ("xr_3da.exe", "soc"),
    ("stalker-cop.exe", "cop"),
    ("stalker_cop.exe", "cop"),
    ("xrengine.exe", "xray_modern"),  # 晴空/普Call/IX-Ray 待附加证据
)

# 晴空的附加证据（真机校验点：需在真实安装目录上复核文件名）
CS_HINTS = ("bin\\xrEngine.exe", "gamedata.dbc")
COP_HINTS = ("gamedata.db0", "gamedata.db1", "gamedata.db2")

KIND_LABELS = {
    "soc": "潜行者：切尔诺贝利的阴影 (SoC)",
    "cs": "潜行者：晴空 (Clear Sky)",
    "cop": "潜行者：普里皮亚季的召唤 (CoP)",
    "xray_modern": "X-Ray 系（晴空/普Call/IX-Ray，需附加证据细分）",
    "unknown_xray": "疑似 X-Ray 系（证据不足）",
    "unknown": "未识别",
}

_FSGAME_RE = re.compile(
    r"^\s*\$(game_data|game_sounds|game_configs)\$\s*=\s*"
    r"(true|false)\s*\|\s*(true|false)\s*\|\s*(.*?)\s*$",
    re.IGNORECASE)


def detect_game(root):
    """识别游戏类型。返回 dict(kind, label, evidence)。"""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return {"kind": "unknown", "label": KIND_LABELS["unknown"],
                "evidence": ["root 不存在"]}

    lower_files = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in
                       ("mods", "voice_src", "__pycache__")]
        for fn in filenames:
            lower_files.setdefault(fn.lower(),
                                   os.path.relpath(dirpath, root))
        if len(lower_files) > 20000:  # 防御异常目录
            break

    evidence = []
    kind = "unknown"
    for exe, hint in GAME_EXE_HINTS:
        if exe in lower_files:
            evidence.append("%s @ %s" % (exe, lower_files[exe]))
            kind = hint
            break

    if kind == "xray_modern":
        hit_cs = [h for h in CS_HINTS
                  if h.replace("\\", os.sep).lower() in
                  {p.replace("\\", os.sep) for p in lower_files}]
        hit_cop = [h for h in COP_HINTS if h in lower_files]
        if hit_cop and not hit_cs:
            kind = "cop"
            evidence += ["普Call 证据: %s" % ", ".join(hit_cop)]
        elif hit_cs and not hit_cop:
            kind = "cs"
            evidence += ["晴空证据: %s" % ", ".join(hit_cs)]
        else:
            evidence.append("晴空/普Call 证据冲突或均缺，保持 xray_modern")

    return {"kind": kind,
            "label": KIND_LABELS.get(kind, kind),
            "evidence": evidence}


def read_fsgame(root):
    """解析 fsgame.ltx 中三个关键变量行。

    返回 dict(var -> {on1: bool, on2: bool, path: str})；
    文件缺失返回 None；某行缺失则该变量缺位于结果中。
    """
    path = os.path.join(root, "fsgame.ltx")
    if not os.path.isfile(path):
        return None
    result = {}
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        return {"_error": str(exc)}
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("latin-1")
    for line in text.splitlines():
        m = _FSGAME_RE.match(line)
        if not m:
            continue
        var, on1, on2, seg = m.groups()
        result[var.lower()] = {
            "on1": on1.lower() == "true",
            "on2": on2.lower() == "true",
            "path": seg.strip().strip('"'),
        }
    return result


def check_jsgme(root):
    """JSGME 就位检查：exe 在游戏根、MODS 目录（存在与否都算就位）。"""
    root = os.path.abspath(root)
    exe = os.path.join(root, "JSGME.exe")
    mods = os.path.join(root, "MODS")
    return {
        "jsgme_exe": os.path.isfile(exe),
        "mods_dir": os.path.isdir(mods),
        "mods_dir_exists_with_content": (
            os.path.isdir(mods) and bool(os.listdir(mods))),
    }


def list_archives(root):
    """枚举游戏根与 bin 下的 X-Ray 存档（.db* / .xdb）。"""
    found = []
    for sub in ("", "bin"):
        base = os.path.join(root, sub) if sub else root
        if not os.path.isdir(base):
            continue
        for fn in sorted(os.listdir(base)):
            low = fn.lower()
            if low.endswith((".db", ".db0", ".db1", ".db2", ".db3",
                             ".db4", ".db5", ".db6", ".db7", ".db8",
                             ".db9", ".dbc", ".xdb", ".sqfs")):
                found.append(os.path.normpath(os.path.join(sub, fn))
                             if sub else fn)
    return found


def survey(root):
    """体检汇总：一次调用出全部骨架期可判定的结论。只读。"""
    fsg = read_fsgame(root)
    gd = (fsg or {}).get("game_data")
    return {
        "game": detect_game(root),
        "fsgame": fsg,
        "game_data_on": bool(gd and gd["on1"] and gd["on2"]),
        "jsgme": check_jsgme(root),
        "archives": list_archives(root),
    }
