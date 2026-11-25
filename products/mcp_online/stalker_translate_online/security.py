# -*- coding: utf-8 -*-
"""本地文件系统安全边界。"""
from __future__ import annotations

from pathlib import Path

from .config import Config


class SecurityError(PermissionError):
    pass


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def allowed_path(value: str | Path, cfg: Config, require_dir: bool = False) -> Path:
    path = Path(value).expanduser().resolve()
    if require_dir and not path.is_dir():
        raise SecurityError("目录不存在：%s" % path)
    roots = [Path(root).expanduser().resolve() for root in cfg.allowed_roots]
    if not any(_within(path, root) for root in roots):
        raise SecurityError("路径不在 allowed_roots 内：%s" % path)
    return path


def output_path(value: str | Path, input_dir: Path, cfg: Config) -> Path:
    path = Path(value).expanduser().resolve()
    allowed_path(path, cfg)
    if path == input_dir or _within(input_dir, path):
        raise SecurityError("输出目录不能等于输入目录或成为其祖先")
    return path
