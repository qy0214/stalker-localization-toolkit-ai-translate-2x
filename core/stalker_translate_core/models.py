# -*- coding: utf-8 -*-
"""内置离线模型画像。"""
from __future__ import annotations


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


BUILTIN_TIERS = ("7b", "mt2")
