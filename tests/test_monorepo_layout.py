# -*- coding: utf-8 -*-
"""Monorepo 骨架、版本与资产单一来源门禁。"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from stalker_translate_core import AssetBundle  # noqa: E402


class MonorepoLayoutTests(unittest.TestCase):
    def test_required_layout_exists(self):
        required = (
            "core/stalker_translate_core/__init__.py",
            "assets/stalker_termbase.json",
            "assets/glossary_builtin.json",
            "assets/xray_pkg.zip",
            "products/plugin_2x/manifest.json",
            "products/plugin_1_9x/manifest.json",
            "products/mcp_hermes/manifest.json",
            "products/mcp_online/manifest.json",
            "products/voice_plugin/manifest.json",
            "plugins/zh_voice/plugin.py",
            "core/stalker_voice_core/__init__.py",
            "skills/stalker-translate/manifest.json",
            "skills/stalker-translate/SKILL.md",
            "skills/stalker-translate/scripts/stalker_translate.py",
            "versions.json",
            "compatibility.json",
        )
        missing = [name for name in required if not (ROOT / name).is_file()]
        self.assertEqual(missing, [])

    def test_versions_match_product_manifests(self):
        versions = json.loads((ROOT / "versions.json").read_text(encoding="utf-8"))
        for product in ("plugin_2x", "plugin_1_9x", "mcp_hermes", "mcp_online"):
            manifest = json.loads(
                (ROOT / "products" / product / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["version"], versions[product], product)
            self.assertEqual(manifest["core"], versions["core"], product)

    def test_skill_version_matches(self):
        versions = json.loads((ROOT / "versions.json").read_text(encoding="utf-8"))
        manifest = json.loads(
            (ROOT / "skills/stalker-translate/manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["version"], versions["skill"])
        self.assertEqual(manifest["runtime"], "mcp_online")
        self.assertEqual(manifest["status"], "active")

    def test_voice_plugin_is_pending_and_not_release_ready(self):
        versions = json.loads((ROOT / "versions.json").read_text(encoding="utf-8"))
        manifest = json.loads(
            (ROOT / "products/voice_plugin/manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["version"], versions["voice_plugin"])
        self.assertEqual(manifest["status"], "pending")
        self.assertIsNone(manifest["core"])
        self.assertIsNone(manifest["package"])
        self.assertIn("Qwen3-TTS 原音色克隆合成", manifest["reserved"])

    def test_2x_asset_mirror_matches_canonical_assets(self):
        canonical = AssetBundle.load(ROOT / "assets").hashes
        mirror = AssetBundle.load(ROOT / "plugins/ai_translate/assets").hashes
        self.assertEqual(canonical, mirror)

    def test_2x_core_mirror_matches_canonical_core(self):
        source = ROOT / "core/stalker_translate_core"
        mirror = ROOT / "plugins/ai_translate/stalker_translate_core"
        source_files = {str(path.relative_to(source)): path.read_bytes()
                        for path in source.rglob("*.py")}
        mirror_files = {str(path.relative_to(mirror)): path.read_bytes()
                        for path in mirror.rglob("*.py")}
        self.assertEqual(source_files, mirror_files)

    def test_1_9x_core_mirror_matches_canonical_core(self):
        source = ROOT / "core/stalker_translate_core"
        mirror = ROOT / "products/plugin_1_9x/stalker_translate_core"
        source_files = {str(path.relative_to(source)): path.read_bytes()
                        for path in source.rglob("*.py")}
        mirror_files = {str(path.relative_to(mirror)): path.read_bytes()
                        for path in mirror.rglob("*.py")}
        self.assertEqual(source_files, mirror_files)

    def test_mcp_hermes_core_mirror_matches_canonical_core(self):
        source = ROOT / "core/stalker_translate_core"
        mirror = ROOT / "products/mcp_hermes/stalker_translate_core"
        source_files = {str(path.relative_to(source)): path.read_bytes()
                        for path in source.rglob("*.py")}
        mirror_files = {str(path.relative_to(mirror)): path.read_bytes()
                        for path in mirror.rglob("*.py")}
        self.assertEqual(source_files, mirror_files)

    def test_mcp_online_core_mirror_matches_canonical_core(self):
        source = ROOT / "core/stalker_translate_core"
        mirror = ROOT / "products/mcp_online/stalker_translate_core"
        source_files = {str(path.relative_to(source)): path.read_bytes()
                        for path in source.rglob("*.py")}
        mirror_files = {str(path.relative_to(mirror)): path.read_bytes()
                        for path in mirror.rglob("*.py")}
        self.assertEqual(source_files, mirror_files)

    def test_no_product_local_termbase_copies(self):
        forbidden = []
        for base in (ROOT / "products", ROOT / "skills"):
            for path in base.rglob("stalker_termbase.json"):
                forbidden.append(str(path.relative_to(ROOT)))
        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
