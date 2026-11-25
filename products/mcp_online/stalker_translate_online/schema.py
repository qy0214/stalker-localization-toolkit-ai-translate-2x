# -*- coding: utf-8 -*-
"""通用在线 MCP 工具集。"""
from __future__ import annotations

from stalker_translate_core.mcp import TOOL_DEFINITIONS

_BY_NAME = {tool["name"]: tool for tool in TOOL_DEFINITIONS}

ONLINE_TOOL_DEFINITIONS = [
    _BY_NAME["check_env"],
    {
        "name": "scan_project",
        "description": "只读扫描 X-Ray 文本 XML，统计文件、条目、字符与预估 token/费用，不请求模型。",
        "inputSchema": {
            "type": "object",
            "properties": {"input_dir": {"type": "string", "description": "MOD 根目录"}},
            "required": ["input_dir"],
        },
    },
    _BY_NAME["translate_text"],
    {
        "name": "translate_project",
        "description": "异步翻译 X-Ray 文本 XML；立即返回 job_id，不覆盖源文件。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_dir": {"type": "string"},
                "output_dir": {"type": "string"},
                "source_lang": {"type": "string", "enum": ["auto", "rus", "eng", "ukr"]},
                "target_lang": {"type": "string", "enum": ["chs", "eng", "rus", "ukr"]},
                "concurrency": {"type": "integer", "minimum": 1, "maximum": 16},
            },
            "required": ["input_dir"],
        },
    },
    {
        "name": "job_status",
        "description": "查询异步翻译任务状态、进度、token 与问题。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "job_cancel",
        "description": "取消异步翻译任务；已写出文件保留。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "validate_output",
        "description": "核对源与输出的 ID 集合、占位符、换行和中文产出。",
        "inputSchema": {
            "type": "object",
            "properties": {"input_dir": {"type": "string"},
                           "output_dir": {"type": "string"}},
            "required": ["input_dir", "output_dir"],
        },
    },
    {
        "name": "glossary_lookup",
        "description": "查询共享术语库；不请求模型。",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
            "required": ["query"],
        },
    },
    {
        "name": "pending_entries",
        "description": ("对话 AI 批量协议第一遍：跑共享全链收集筛选与直取后仍需模型的"
                        "条目（零 API 费用），返回编号条目与术语提示；可翻译部分照常落盘。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_dir": {"type": "string"},
                "output_dir": {"type": "string"},
                "source_lang": {"type": "string", "enum": ["auto", "rus", "eng", "ukr"]},
                "target_lang": {"type": "string", "enum": ["chs", "eng", "rus", "ukr"]},
            },
            "required": ["input_dir"],
        },
    },
    {
        "name": "apply_batch",
        "description": ("对话 AI 批量协议第二输入：把批量答案按键或按 n 序号交回；随后 "
                        "translate_project 复用答案零请求补全。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "answers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "n": {"type": "integer"},
                            "key": {"type": "string"},
                            "translation": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["answers"],
        },
    },
]
