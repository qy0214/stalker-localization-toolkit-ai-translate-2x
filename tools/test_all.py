#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""运行共享 core 与当前已接入产品的回归测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from suite_common import run_python  # noqa: E402


def main() -> int:
    # 动态发现全部测试文件，避免新增测试忘记接入 CI 形成盲区。
    scripts = [path.relative_to(ROOT).as_posix()
               for path in sorted((ROOT / "tests").glob("test_*.py"))]
    for script in scripts:
        print("===", script)
        result = run_python(script, check=False)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode:
            return result.returncode
    print("OK 共享 core 与当前 active 产品回归全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
