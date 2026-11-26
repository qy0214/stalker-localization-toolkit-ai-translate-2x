# -*- coding: utf-8 -*-
"""xrkit 解包适配层（M2）。

引擎来源：STALKER-Localization-Toolkit（github.com/ALittleHuman，GPL-3.0）
的纯 Python 解包引擎 `file_system/stalker_fs.py`，**原样 vendor** 于
`vendor/`（含 lzhuf_dll 与许可证副本）——不做任何修改，只 import。
解析顺序：
  1. 环境变量 STALKER_VOICE_TOOLKIT_DIR 指向的工具包（file_system 子目录）；
  2. 本目录 vendor/ 副本（分享包默认形态）。

职责：把「.db*/.xdb/.sqfs 存档 → VOICE_SRC 镜像树」封装成一个带进度、
可取消、只写自己输出目录的函数。磁盘纪律：除 out_dir 外不写任何路径
（DLL 的 %TEMP% 回落是引擎内部行为，见 vendor 注释）。

条目结构（引擎 unpack_db 返回）：{path(正斜杠), offset, size_real,
size_comp, crc, is_dir}。路径过滤统一用正斜杠前缀比较。
"""

import os
import sys
import threading

_ENGINE = None      # 惰性加载的 stalker_fs 模块
_ENGINE_LOCK = threading.Lock()
_ENGINE_ERROR = None


def resolve_engine_dir():
    """引擎目录：环境变量 > vendor 副本。"""
    env = os.environ.get("STALKER_VOICE_TOOLKIT_DIR")
    if env:
        cand = os.path.join(env, "file_system")
        if os.path.isfile(os.path.join(cand, "stalker_fs.py")):
            return cand
    here = os.path.dirname(os.path.abspath(__file__))
    vend = os.path.join(here, "vendor")
    if os.path.isfile(os.path.join(vend, "stalker_fs.py")):
        return vend
    raise RuntimeError(
        "找不到 stalker_fs 引擎：设 STALKER_VOICE_TOOLKIT_DIR，"
        "或确认 vendor/ 副本完整")


def _engine():
    """惰性 import 引擎（线程安全；失败信息缓存，避免每次任务重试）"""
    global _ENGINE, _ENGINE_ERROR
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            return _ENGINE
        try:
            eng_dir = resolve_engine_dir()
            if eng_dir not in sys.path:
                sys.path.insert(0, eng_dir)
            _ENGINE = __import__("stalker_fs")
        except Exception as exc:
            _ENGINE_ERROR = "%s: %s" % (type(exc).__name__, exc)
            raise RuntimeError("解包引擎加载失败：%s" % _ENGINE_ERROR)
    return _ENGINE


def engine_status():
    """供 scan/体检页展示引擎可用性。"""
    try:
        eng = _engine()
        return {"ok": True, "dir": os.path.dirname(os.path.abspath(eng.__file__)),
                "formats": sorted(getattr(eng, "FORMATS", {}).keys())}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# --------------------------------------------------------------------------
# 存档提取
# --------------------------------------------------------------------------
def _norm_prefix(prefix):
    return (prefix or "").replace("\\", "/").strip("/").lower()


def extract_archive(archive_path, out_dir, prefix="sounds",
                    progress=None, cancel=None, source_name=None):
    """解一个存档，把命中前缀的条目落盘到 out_dir（保留相对路径树）。

    返回 dict(total, matched, extracted, failed, skipped_dirs, bytes,
              errors[:20], truncated_errors)。
    progress(done, total) 每处理 50 条回调一次（在调用线程内）；
    cancel 为 threading.Event，置位后尽快返回（结果标 cancelled）。
    """
    eng = _engine()
    src_name = source_name or os.path.splitext(
        os.path.basename(archive_path))[0]
    # VOICE_SRC\<来源名>\ 是镜像根：条目路径相对它重建
    root_out = os.path.join(out_dir, src_name)
    pref = _norm_prefix(prefix)
    stats = {"archive": archive_path, "source": src_name, "total": 0,
             "matched": 0, "extracted": 0, "failed": 0, "skipped_dirs": 0,
             "bytes": 0, "errors": [], "truncated_errors": False,
             "cancelled": False, "format": None}

    # SquashFS 单独通道（引擎自带外部工具探测与逐条 --cat 提取）
    try:
        sqfs_kind = eng.sqfs_check(archive_path)
    except Exception:
        sqfs_kind = ""
    if sqfs_kind == "plain":
        return _extract_sqfs(eng, archive_path, root_out, pref, stats,
                             progress, cancel)

    raw = eng.load_db(archive_path)
    fmt = eng.auto_detect(raw)
    stats["format"] = fmt or "unknown"
    entries = eng.unpack_db(raw, fmt)
    stats["total"] = len(entries)
    if not entries:
        stats["errors"].append("条目表为空或无法解析（fmt=%r）" % fmt)
        stats["truncated_errors"] = True
        return stats

    done = 0
    for entry in entries:
        if cancel is not None and cancel.is_set():
            stats["cancelled"] = True
            break
        done += 1
        if progress is not None and done % 50 == 0:
            progress(done, stats["total"])
        path = entry.get("path") or ""
        if entry.get("is_dir"):
            stats["skipped_dirs"] += 1
            continue
        if pref and not path.lower().startswith(pref + "/"):
            continue
        stats["matched"] += 1
        data = eng.extract_file(raw, entry)
        dest = eng.safe_out_path(root_out, path)
        if data is None or dest is None:
            stats["failed"] += 1
            if len(stats["errors"]) < 20:
                stats["errors"].append("提取失败: %s" % path)
            else:
                stats["truncated_errors"] = True
            continue
        if eng.write_extracted_file(dest, data):
            stats["extracted"] += 1
            stats["bytes"] += len(data)
        else:
            stats["failed"] += 1
            if len(stats["errors"]) < 20:
                stats["errors"].append("落盘失败: %s" % path)
            else:
                stats["truncated_errors"] = True
    return stats


def _extract_sqfs(eng, archive_path, root_out, pref, stats,
                  progress=None, cancel=None):
    """SquashFS 通道：sqfs_list 选中命中前缀的条目，sqfs_extract 逐条落盘。"""
    stats["format"] = "SquashFS"
    items = eng.sqfs_list(archive_path)
    if items is None:
        stats["errors"].append("sqfs_list 失败（缺外部工具或镜像损坏）")
        stats["truncated_errors"] = True
        return stats
    stats["total"] = len(items)
    selected = []
    for it in items:
        path = (it.get("path") or it.get("name") or ""
                ).replace("\\", "/")
        if it.get("is_dir"):
            stats["skipped_dirs"] += 1
            continue
        if pref and not path.lower().startswith(pref + "/"):
            continue
        stats["matched"] += 1
        selected.append({"path": path})
    # 逐条 --cat 由引擎负责（注释见引擎 sqfs_extract：有真实 8.3GB 教训）
    if selected:
        done = 0
        for sel in selected:
            if cancel is not None and cancel.is_set():
                stats["cancelled"] = True
                break
            done += 1
            if progress is not None and done % 50 == 0:
                progress(done, len(selected))
            dest = eng.safe_out_path(root_out, sel["path"])
            if dest is None:
                stats["failed"] += 1
                continue
            try:
                # files 参数按引擎签名：list of dicts with path
                eng.sqfs_extract(archive_path, os.path.dirname(dest),
                                 [{"path": sel["path"]}])
                stats["extracted"] += 1
            except Exception as exc:
                stats["failed"] += 1
                if len(stats["errors"]) < 20:
                    stats["errors"].append("sqfs 提取失败: %s (%s)"
                                           % (sel["path"], exc))
                else:
                    stats["truncated_errors"] = True
    return stats


def extract_many(archives, out_dir, prefix="sounds",
                 progress=None, cancel=None):
    """批量解包：archives 为 [(path, source_name)]；source_name 缺省用文件名。"""
    results = []
    for path, name in archives:
        if cancel is not None and cancel.is_set():
            break
        results.append(extract_archive(path, out_dir, prefix=prefix,
                                       progress=progress, cancel=cancel,
                                       source_name=name))
    return results


def find_archives(game_root):
    """体检结果里挑出可解包的存档（绝对路径 + 默认来源名）。"""
    from stalker_voice_core.detect import list_archives
    out = []
    for rel in list_archives(game_root):
        full = os.path.join(game_root, rel)
        name = os.path.splitext(os.path.basename(rel))[0]
        out.append((full, name))
    return out
