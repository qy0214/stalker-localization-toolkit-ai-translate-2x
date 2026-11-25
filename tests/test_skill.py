# -*- coding: utf-8 -*-
"""通用在线翻译技能包与包装脚本测试。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from mock_openai import MockOpenAI  # noqa: E402

BUILD = ROOT / "tools/gen_release_manifest_skill.py"


class SkillTests(unittest.TestCase):
    def test_package_wrapper_and_workflow(self):
        with tempfile.TemporaryDirectory(prefix="ait_skill_") as td:
            base = Path(td)
            dist = base / "dist"
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            subprocess.run(
                [sys.executable, str(BUILD), "--build", "--out-dir", str(dist)],
                cwd=str(ROOT), env=env, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            versions = json.loads((ROOT / "versions.json").read_text(encoding="utf-8"))
            package = dist / ("stalker-translate-skill-%s.zip" % versions["skill"])
            self.assertTrue(package.is_file())
            extract = base / "skill"
            with zipfile.ZipFile(package) as archive:
                names = set(archive.namelist())
                self.assertIn("SKILL.md", names)
                self.assertIn("scripts/stalker_translate.py", names)
                self.assertIn("runtime/cli.py", names)
                self.assertIn("runtime/stalker_translate_core/mcp.py", names)
                self.assertIn("runtime/assets/stalker_termbase.json", names)
                archive.extractall(extract)

            skill_text = (extract / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("Hermes", skill_text)
            self.assertNotIn("C:\\Users", skill_text)
            # 随包 SKILL.md 必须携带 0.2.0 批量协议与护栏（禁本地小模型代答、不做 API 编排）
            self.assertIn("硬性规则", skill_text)
            self.assertIn("本地小模型", skill_text)
            self.assertIn("apply-batch", skill_text)
            self.assertIn("不做 API 编排", skill_text)
            wrapper = extract / "scripts/stalker_translate.py"
            help_result = subprocess.run(
                [sys.executable, str(wrapper), "--help"], cwd=str(extract),
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", timeout=30)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            # 0.2.0 曾漏发：wrapper 白名单必须转发批量协议子命令（0.2.1 回归）
            wrapper_src = wrapper.read_text(encoding="utf-8")
            self.assertIn('"pending"', wrapper_src)
            self.assertIn('"apply-batch"', wrapper_src)
            wrapper_source_cmd = subprocess.run(
                [sys.executable, str(wrapper), "--help"], cwd=str(extract),
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", timeout=30)
            self.assertIn("pending", wrapper_source_cmd.stdout)

            mod = base / "mod"
            (mod / "text").mkdir(parents=True)
            (mod / "text/st_hello.xml").write_text(
                '<string_table><string id="hello"><rus>Привет, сталкер.</rus>'
                '</string></string_table>', encoding="utf-8")
            mock = MockOpenAI("mock-model").start()
            try:
                cfg = base / "config.json"
                cfg.write_text(json.dumps({
                    "base_url": mock.base_url, "model": "mock-model",
                    "allowed_roots": [str(base)], "rpm": 0, "tpm": 0,
                    "cache_dir": str(base / "cache"),
                    "audit_dir": str(base / "audit"),
                }), encoding="utf-8")
                prefix = [sys.executable, str(wrapper), "--config", str(cfg)]
                scan = subprocess.run(
                    prefix + ["scan", str(mod)], cwd=str(extract), env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", timeout=60)
                self.assertEqual(scan.returncode, 0, scan.stderr)
                self.assertEqual(json.loads(scan.stdout)["entries"], 1)
                text = subprocess.run(
                    prefix + ["translate-text", "Pripyat"], cwd=str(extract),
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", timeout=60)
                self.assertEqual(text.returncode, 0, text.stderr)
                self.assertEqual(json.loads(text.stdout)["translation"], "普里皮亚季")
            finally:
                mock.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
