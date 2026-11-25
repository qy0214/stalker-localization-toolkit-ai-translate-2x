#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""兼容入口：验证所有产品 manifest 与共享资产。"""
from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("check.py")), run_name="__main__")
