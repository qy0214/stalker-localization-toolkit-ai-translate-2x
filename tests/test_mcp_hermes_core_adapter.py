# -*- coding: utf-8 -*-
"""Hermes MCP 共享 core 适配与独立分享包全流程回归。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "tools/gen_release_manifest_mcp_hermes.py"
SMOKE = ROOT / "tests/mcp_hermes_smoke.py"


class HermesAdapterTests(unittest.TestCase):
    def test_progress_percentage_uses_global_total(self):
        script = r'''
import os, sys, tempfile
root = sys.argv[1]
sys.path.insert(0, root)
import server
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "progress.html")
    server._write_progress_page(path, {
        "files": 165, "file_idx": 8, "ids": 704,
        "entries_total": 19813, "entries_translatable": 19813,
    }, [], "running")
    page = open(path, encoding="utf-8").read()
    assert '<span class="pct">3%</span>' in page, page[:500]
    heading = page.split('<h1>', 1)[1].split('</h1>', 1)[0]
    assert '100%' not in heading
'''
        result = subprocess.run(
            [sys.executable, "-c", script, str(ROOT / "products/mcp_hermes")],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout[-3000:])

    def test_package_layout_and_stdio_smoke(self):
        with tempfile.TemporaryDirectory(prefix="ait_mcp_hermes_") as td:
            base = Path(td)
            dist = base / "dist"
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["PYTHONUTF8"] = "1"
            subprocess.run(
                [sys.executable, str(BUILD), "--build", "--out-dir", str(dist)],
                cwd=str(ROOT), env=env, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            package = dist / "stalker-translate-MCP-1.1.12.zip"
            self.assertTrue(package.is_file())
            extract = base / "hermes-plugin"
            with zipfile.ZipFile(package) as archive:
                names = set(archive.namelist())
                self.assertIn("server.py", names)
                self.assertIn("jobs.py", names)
                self.assertIn("stalker_translate_core/mcp.py", names)
                self.assertIn("kernel/assets/stalker_termbase.json", names)
                self.assertIn("stalker_translate_core/offline_job.py", names)
                self.assertIn("stalker_translate_core/vendor.py", names)
                self.assertNotIn("kernel/plugin.py", names)
                self.assertNotIn("kernel_legacy.py", names)
                self.assertIn("runtime/python.exe", names)
                self.assertIn("skills/stalker-translate/SKILL.md", names)
                self.assertIn("config.json", names)
                self.assertFalse(any(name.startswith("logs/") for name in names))
                self.assertFalse(any("__pycache__" in name for name in names))
                archive.extractall(extract)

            env["STALKER_MCP_PLUGIN_ROOT"] = str(extract)
            result = subprocess.run(
                [sys.executable, str(SMOKE)],
                cwd=str(ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                timeout=600,
            )
            self.assertEqual(result.returncode, 0, result.stdout[-6000:])
            self.assertIn("通过", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
