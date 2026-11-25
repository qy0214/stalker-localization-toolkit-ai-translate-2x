# -*- coding: utf-8 -*-
"""共享运行时辅助：捕获 vendor 解析器诊断。"""
from __future__ import annotations

import io
import sys
import threading

_VENDOR_IO_LOCK = threading.Lock()


class capture_stdout:
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
        return [line.strip() for line in self._buf.getvalue().splitlines()
                if line.strip()]
