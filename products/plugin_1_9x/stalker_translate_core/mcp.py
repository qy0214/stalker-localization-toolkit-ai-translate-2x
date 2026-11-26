# -*- coding: utf-8 -*-
"""跨产品共享的 MCP 工具契约；不包含 transport 或 Hermes 注册逻辑。"""
from __future__ import annotations

from typing import Any

PROTOCOL_VERSION = "2024-11-05"

SERVER_INSTRUCTIONS = (
    "STALKER 汉化翻译。用户要汉化 MOD 时先调用 translation_plan 了解模型与路数，"
    "再调用 translate_mod；整目录任务由工具自行识别，不要先用 terminal 扫描。"
    "translate_mod 返回 job_id 后用 job_status 轮询；用户要求停止时调用 job_cancel。"
    "只有 status=done 才能报告完成，needs_review 必须报告待复核。"
    "单段文本使用 translate_text；翻译报错时才调用 check_env。"
)

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "check_env",
        "description": (
            "检查 STALKER 汉化环境：模型端点、术语库、X-Ray 解析器、"
            "硬件与路由。首次使用或翻译报错时先调用；deep=true 会真发"
            "一句测试翻译。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "deep": {"type": "boolean", "default": False,
                         "description": "是否真发一句测试翻译"},
            },
        },
    },
    {
        "name": "translation_plan",
        "description": "查询可用翻译模型、端点槽位与推荐并发路数；不启动翻译。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "translate_mod",
        "description": (
            "汉化完整 X-Ray / STALKER MOD：识别文本 XML、cfgxml、ltx、"
            "script，套用术语库并写出汉化补丁。用户给出 MOD 目录后直接"
            "调用；工具自行扫描，不覆盖源文件，可重复运行续译。"
            "只有 status=done 表示通过终检；needs_review 必须查看 audit "
            "与 log_tail。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_dir": {"type": "string",
                              "description": "MOD 根目录绝对路径"},
                "output_dir": {"type": "string",
                               "description": "输出目录；默认 input_dir/translated_hy_mt2"},
                "parallel": {"type": "integer", "minimum": 1, "maximum": 16,
                             "description": "并发路数；省略时由适配器选择"},
                "model": {"type": "string",
                          "description": "翻译模型名；省略时使用配置默认值"},
                "mode": {"type": "string", "enum": ["auto", "text"],
                         "default": "auto",
                         "description": "auto=全部文本类型，text=只译文本 XML"},
                "source_lang": {"type": "string",
                                "enum": ["auto", "rus", "eng", "ukr"],
                                "default": "auto"},
            },
            "required": ["input_dir"],
        },
    },
    {
        "name": "job_status",
        "description": "查询 translate_mod 启动的异步翻译任务状态、进度与终检结果。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "job_cancel",
        "description": "停止指定翻译任务；当前请求结束后不再发送新请求。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "translate_text",
        "description": "翻译一小段 STALKER 文本，自动套用术语库；长文本使用 translate_mod。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待翻译文本"},
                "context": {"type": "string", "description": "可选场景上下文"},
                "source_lang": {"type": "string",
                                "enum": ["auto", "rus", "eng", "ukr"],
                                "default": "auto"},
                "target_lang": {"type": "string",
                                "enum": ["chs", "eng", "rus", "ukr"],
                                "default": "chs"},
            },
            "required": ["text"],
        },
    },
]


def tool_result(obj: Any, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": _json_text(obj)}],
        "isError": bool(is_error),
    }


def _json_text(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2)
