# -*- coding: utf-8 -*-
"""通用在线翻译配置；密钥只从环境变量读取。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP_DIR = "STALKER Translate Online"


@dataclass
class Config:
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = ""
    api_key_env: str = "STALKER_TRANSLATE_API_KEY"
    timeout_sec: float = 120.0
    ctx_size: int = 8192
    max_retries: int = 2
    max_concurrency: int = 4
    rpm: int = 60
    tpm: int = 120000
    allowed_roots: list[str] = field(default_factory=list)
    max_files: int = 5000
    max_file_bytes: int = 8 * 1024 * 1024
    max_text_chars: int = 20000
    cache_dir: str = ""
    audit_dir: str = ""
    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0
    budget: dict[str, Any] = field(default_factory=dict)

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()

    @property
    def cache_path(self) -> Path:
        if self.cache_dir:
            return Path(self.cache_dir)
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / APP_DIR / "cache"

    @property
    def audit_path(self) -> Path:
        if self.audit_dir:
            return Path(self.audit_dir)
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / APP_DIR / "audit"

    def public_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "api_key_configured": bool(self.api_key),
            "timeout_sec": self.timeout_sec,
            "ctx_size": self.ctx_size,
            "max_concurrency": self.max_concurrency,
            "rpm": self.rpm,
            "tpm": self.tpm,
            "allowed_roots": self.allowed_roots,
            "max_files": self.max_files,
            "max_file_bytes": self.max_file_bytes,
            "max_text_chars": self.max_text_chars,
            "budget": self.budget,
        }


def default_config_path() -> Path:
    override = os.environ.get("STALKER_TRANSLATE_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / APP_DIR / "config.json"


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    cfg.allowed_roots = [os.getcwd()]
    target = Path(path) if path else default_config_path()
    if target.is_file():
        data = json.loads(target.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("配置顶层必须是对象")
        if data.get("api_key"):
            raise ValueError("禁止在配置文件中保存 api_key；请使用 api_key_env 指向环境变量")
        for key, value in data.items():
            if hasattr(cfg, key) and not key.startswith("_"):
                setattr(cfg, key, value)
    env_map = {
        "STALKER_TRANSLATE_BASE_URL": "base_url",
        "STALKER_TRANSLATE_MODEL": "model",
        "STALKER_TRANSLATE_API_KEY_ENV": "api_key_env",
    }
    for env_name, field_name in env_map.items():
        if os.environ.get(env_name):
            setattr(cfg, field_name, os.environ[env_name])
    if not cfg.model:
        raise ValueError("未配置模型：设置 STALKER_TRANSLATE_MODEL 或配置文件 model")
    if not cfg.base_url.startswith(("http://", "https://")):
        raise ValueError("base_url 必须是 http:// 或 https://")
    return cfg
