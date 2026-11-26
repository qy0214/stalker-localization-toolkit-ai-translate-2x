# -*- coding: utf-8 -*-
"""语音文件枚举与初分类（M2）。

scan 任务的数据面：对一棵已落盘的树（VOICE_SRC 产出或游戏散文件
gamedata*/sounds）枚举 .ogg，统计 + 粗分类。**不做语言识别**（那是
Whisper 的活，属文本链 M3+），这里只做零成本的路径启发式。

X-Ray 语音组织常识（用于分类，均为启发式，真机可复核）：
  * sounds/characters_voice/<阵营>/<角色profile>/... —— 按角色组织的
    台词库，是音色档案的主数据源；
  * sounds/dialog/... —— 对话语音；
  * 其余（music/ambient/weapons 等物体音）默认非语音，TTS 阶段跳过。
"""

import os

AUDIO_EXT = ".ogg"

SPEECH_PATH_HINTS = (
    "characters_voice",
    "dialog",
    "speech",
)

# 语言猜测：仅按目录名（SoC 系俄/英/乌多语包常按语言分目录或分存档）。
LANG_HINTS = (
    ("rus", "ru"), ("russian", "ru"),
    ("eng", "en"), ("english", "en"),
    ("ukr", "uk"), ("ukrainian", "uk"),
)


def _guess_lang(rel_lower):
    for hint, lang in LANG_HINTS:
        if hint in rel_lower:
            return lang
    return None


def _is_speech(rel_lower):
    return any(h in rel_lower for h in SPEECH_PATH_HINTS)


def enumerate_audio(root_dir, max_files=200000):
    """枚举一棵树里的 .ogg。

    返回 dict(files_total, speech_total, by_lang, by_top_dir,
              speech_samples[:50], truncated, root)
    只读，只统计，不读文件内容（内容级识别交给 ASR 阶段）。
    """
    root_dir = os.path.abspath(root_dir)
    result = {
        "root": root_dir, "files_total": 0, "speech_total": 0,
        "by_lang": {}, "by_top_dir": {}, "speech_samples": [],
        "truncated": False,
    }
    if not os.path.isdir(root_dir):
        result["error"] = "目录不存在"
        return result
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames
                       if not d.lower().endswith(".git")]
        rel_dir = os.path.relpath(dirpath, root_dir).replace("\\", "/")
        top = rel_dir.split("/")[0] if rel_dir != "." else "(root)"
        for fn in filenames:
            if not fn.lower().endswith(AUDIO_EXT):
                continue
            if result["files_total"] >= max_files:
                result["truncated"] = True
                return result
            result["files_total"] += 1
            result["by_top_dir"][top] = result["by_top_dir"].get(top, 0) + 1
            rel = os.path.relpath(os.path.join(dirpath, fn),
                                  root_dir).replace("\\", "/").lower()
            if _is_speech(rel):
                result["speech_total"] += 1
                lang = _guess_lang(rel)
                if lang:
                    result["by_lang"][lang] = \
                        result["by_lang"].get(lang, 0) + 1
                if len(result["speech_samples"]) < 50:
                    result["speech_samples"].append(
                        os.path.relpath(os.path.join(dirpath, fn),
                                        root_dir))
    return result


def scan_voice_sources(game_root, voice_src_root=None):
    """scan 任务主入口：体检 + 散文件语音枚举 + 待解包存档清单。

    voice_src_root：已解包产出目录（VOICE_SRC）；缺省为 <game_root>/VOICE_SRC，
    不存在则跳过（还没解包过）。
    返回 dict 供 UI 台账直接渲染。
    """
    from stalker_voice_core import detect
    survey = detect.survey(game_root)
    sources = []
    if voice_src_root is None:
        voice_src_root = os.path.join(game_root, "VOICE_SRC")
    if os.path.isdir(voice_src_root):
        for name in sorted(os.listdir(voice_src_root)):
            sub = os.path.join(voice_src_root, name)
            if os.path.isdir(sub):
                sources.append(dict(enumerate_audio(sub), source=name))
    loose = []
    for entry in os.listdir(game_root) if os.path.isdir(game_root) else []:
        if entry.lower().startswith("gamedata") and \
                os.path.isdir(os.path.join(game_root, entry)):
            sounds = os.path.join(game_root, entry, "sounds")
            if os.path.isdir(sounds):
                loose.append(dict(enumerate_audio(sounds),
                                  source="%s/sounds" % entry))
    return {
        "survey": survey,
        "extracted_sources": sources,
        "loose_gamedata": loose,
        "archives_pending": [
            {"path": p, "source": n}
            for p, n in detect_list_archives_safe(game_root)
        ],
        "engine": engine_status_safe(),
    }


def detect_list_archives_safe(game_root):
    try:
        from stalker_voice_core.extract import find_archives
        return find_archives(game_root)
    except Exception:
        return []


def engine_status_safe():
    try:
        from stalker_voice_core.extract import engine_status
        return engine_status()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
