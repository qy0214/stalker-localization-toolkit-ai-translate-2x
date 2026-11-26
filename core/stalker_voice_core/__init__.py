# -*- coding: utf-8 -*-
"""stalker_voice_core —— 潜行者中文语音工坊共享内核（骨架版 0.1.0）。

与 stalker_translate_core 同级、同纪律：
  * 单一源码，构建期由 tools/sync_core.py 同步到各产品/插件目录（骨架期
    plugins/zh_voice/plugin.py 以 sys.path 直挂本目录，遵循"只 import 不复制"）；
  * 纯标准库起步，重依赖（torch / whisper / ffmpeg 调用）全部隔离在 worker
    子进程侧，宿主与插件进程永不 import 重库；
  * 本骨架版只打通：协议握手（protocol）→ worker 进程心跳与任务总线
    （worker）→ 游戏根目录体检（detect）。TTS/解包/文本链为预留任务类型，
    见 protocol.JOB_KINDS 中标 reserved 的条目。
"""

__version__ = "0.1.0"

PROTOCOL_VERSION = 1

__all__ = ["__version__", "PROTOCOL_VERSION"]
