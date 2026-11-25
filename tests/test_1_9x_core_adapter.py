# -*- coding: utf-8 -*-
"""1.9.x 共享 core 适配器与独立发布包回归。"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "products/plugin_1_9x/ai_translate.py"
BUILD = ROOT / "tools/gen_release_manifest_1_9x.py"


def load_plugin(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self._old_local = os.environ.get("LOCALAPPDATA")
        self._tmp = tempfile.TemporaryDirectory(prefix="ait19_core_")
        os.environ["LOCALAPPDATA"] = self._tmp.name

    def tearDown(self):
        if self._old_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._old_local
        self._tmp.cleanup()

    def test_source_adapter_uses_canonical_assets_and_core_hash(self):
        module = load_plugin(PLUGIN, "ait19_source")
        self.assertEqual(module.PLUGIN_INFO["version"], "1.9.26")
        self.assertEqual(Path(module._ASSETS), ROOT / "assets")
        sample = ROOT / "assets/stalker_termbase.json"
        expected = hashlib.sha256(sample.read_bytes()).hexdigest()
        self.assertEqual(module.sha256_file(sample), expected)
        self.assertEqual(module.termbase_lookup("Pripyat"), "普里皮亚季")

    def test_package_contains_core_assets_and_loads_after_extract(self):
        out = Path(self._tmp.name) / "dist"
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        subprocess.run(
            [sys.executable, str(BUILD), "--build", "--out-dir", str(out)],
            cwd=str(ROOT), env=env, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        package = out / "ai_translate_plugin_v1.9.26.zip"
        self.assertTrue(package.is_file())
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            self.assertIn("plugins/ai_translate.py", names)
            self.assertIn("plugins/stalker_translate_core/__init__.py", names)
            self.assertIn("plugins/ai_translate_assets/stalker_termbase.json", names)
            self.assertFalse(any("__pycache__" in name for name in names))
            self.assertFalse(any("/models/" in name or "/bin/" in name
                                 for name in names))
            archive.extractall(Path(self._tmp.name) / "extract")

        extracted = Path(self._tmp.name) / "extract/plugins/ai_translate.py"
        module = load_plugin(extracted, "ait19_package")
        self.assertEqual(module.PLUGIN_INFO["version"], "1.9.26")
        self.assertEqual(Path(module._ASSETS),
                         extracted.parent / "ai_translate_assets")
        self.assertEqual(module.termbase_lookup("КПК"), "PDA")

        manifest = json.loads(
            (out / "ai_translate_plugin_v1.9.26.manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["version"], "1.9.26")
        self.assertEqual(manifest["package_sha256"],
                         hashlib.sha256(package.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main(verbosity=2)
