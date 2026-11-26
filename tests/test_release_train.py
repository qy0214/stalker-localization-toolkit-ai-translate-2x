# -*- coding: utf-8 -*-
"""五产品统一发布列车清单与门禁测试。"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from suite_common import ACTIVE_PRODUCTS, sha256  # noqa: E402


class ReleaseTrainTests(unittest.TestCase):
    def test_manifest_products_and_artifacts(self):
        data = json.loads((ROOT / "release_train.json").read_text(encoding="utf-8"))
        compatibility = json.loads((ROOT / "compatibility.json").read_text(encoding="utf-8"))
        self.assertEqual(data["release_train"], compatibility["release_train"])
        self.assertEqual(data["tag"], "suite-v" + compatibility["release_train"])
        self.assertEqual(set(data["products"]), set(ACTIVE_PRODUCTS))
        total = 0
        for product, info in data["products"].items():
            self.assertEqual(info["status"], "active")
            self.assertEqual(info["core"], data["core"])
            for artifact in info["artifacts"]:
                path = ROOT / artifact["path"]
                self.assertTrue(path.is_file(), artifact["path"])
                self.assertEqual(path.stat().st_size, artifact["size"])
                self.assertEqual(sha256(path), artifact["sha256"])
                total += 1
        self.assertEqual(total, 10)

    def test_package_manifests_match_release_products(self):
        versions = json.loads((ROOT / "versions.json").read_text(encoding="utf-8"))
        train = json.loads((ROOT / "release_train.json").read_text(encoding="utf-8"))
        for product, info in train["products"].items():
            artifacts = {item["role"]: item for item in info["artifacts"]}
            if "manifest" not in artifacts:
                continue
            details = json.loads((ROOT / artifacts["manifest"]["path"]).read_text(
                encoding="utf-8"))
            package = artifacts["package"]
            self.assertEqual(details["product"], product)
            self.assertEqual(details["version"], versions[product])
            self.assertEqual(details["core"], versions["core"])
            self.assertEqual(details["package"], Path(package["path"]).name)
            self.assertEqual(details["package_size"], package["size"])
            self.assertEqual(details["package_sha256"], package["sha256"])

    def test_release_check_command(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/release.py"), "--check"],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("products=5", result.stdout)

    def test_markdown_mentions_legacy_switch(self):
        text = (ROOT / "RELEASE_TRAIN.md").read_text(encoding="utf-8")
        compatibility = json.loads((ROOT / "compatibility.json").read_text(encoding="utf-8"))
        self.assertIn("suite-v" + compatibility["release_train"], text)
        self.assertIn("archived", text)
        self.assertIn("stalker-localization-toolkit-ai-translate", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
