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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from stalker_translate_core import OfflineTranslationJob, OutputTruncated  # noqa: E402
PLUGIN = ROOT / "products/plugin_1_9x/ai_translate.py"
BUILD = ROOT / "tools/gen_release_manifest_1_9x.py"


def load_plugin(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeServer:
    def __init__(self, fn=None):
        self.fn = fn or (lambda text: "你好。")
        self.calls = []

    def is_running(self):
        return True

    def translate(self, text, src, tgt, **kwargs):
        self.calls.append((text, src))
        return self.fn(text)

    def translate_batch(self, lines, src, tgt, **kwargs):
        self.calls.extend((line, src) for line in lines)
        return "\n".join("[%d] %s" % (index, self.fn(line))
                         for index, line in enumerate(lines, 1))


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

    def _make_job(self, module, server=None, **kwargs):
        root = Path(self._tmp.name) / "job"
        root.mkdir(exist_ok=True)
        params = dict(
            parser=module._load_vendor(), server=server or FakeServer(),
            in_dir=str(root), mode="auto", src_lang="auto", tgt_lang="chs",
            out_dir=str(root / "out"), glossary={}, force_glossary=False,
            hard_glossary_keys=set(), reference_mode=False,
            copy_reference=False, protect_proper=False,
            translate_literals=False, auto_session_terms=False,
            tm_lite=False, log=lambda *args, **kw: None,
            detail=lambda *args, **kw: None,
        )
        params.update(kwargs)
        return module.TranslationJob(**params)

    def test_legacy_job_uses_shared_core_and_exception(self):
        module = load_plugin(PLUGIN, "ait19_shared_job")
        self.assertTrue(issubclass(module.TranslationJob, OfflineTranslationJob))
        self.assertIs(module.OutputTruncated, OutputTruncated)

    def test_legacy_job_counts_placeholder_and_context_failures(self):
        module = load_plugin(PLUGIN, "ait19_failures")
        placeholder = self._make_job(
            module, FakeServer(lambda text: "%c[默认]汉译"))
        self.assertEqual(
            placeholder._translate_one("%c[default]Hello", "eng"),
            "%c[default]Hello",
        )
        self.assertGreaterEqual(placeholder.failed_strings, 1)
        bleed = self._make_job(
            module, FakeServer(lambda text: "前一句：你好\n打开门。"))
        self.assertEqual(
            bleed._translate_one("Open the door.", "eng", context="前一句：你好"),
            "Open the door.",
        )
        self.assertGreaterEqual(bleed.failed_strings, 1)

    def test_legacy_job_marks_discard_and_ledger_failures(self):
        module = load_plugin(PLUGIN, "ait19_io_failures")
        job = self._make_job(module)
        output = Path(job.out_dir) / "unchanged.xml"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("old", encoding="utf-8")
        with mock.patch("os.remove", side_effect=OSError("controlled")):
            job._discard_unchanged_output("unchanged.xml", str(output))
        self.assertGreaterEqual(job.failed_strings, 1)
        job._ledger_path = str(Path(job.out_dir) / ".ai_translate_done.json")
        job._ledger = {"a": {}}
        job._write_atomic = lambda *args: (_ for _ in ()).throw(OSError("controlled"))
        job._save_ledger()
        self.assertTrue(job._ledger_save_failed)

    def test_legacy_job_revokes_dangling_generated_references(self):
        module = load_plugin(PLUGIN, "ait19_dangling")
        job = self._make_job(module)
        done = Path(job.out_dir)
        done.mkdir(parents=True, exist_ok=True)
        (done / "body.ltx").write_text("descr = sgtat_gen_text_1_1", encoding="utf-8")
        job._ledger = {"body.ltx": {"out_size": 1}}
        revoked = job._revoke_dangling_generated_refs(str(done))
        self.assertEqual(revoked, ["body.ltx"])
        self.assertNotIn("body.ltx", job._ledger)

    def test_legacy_job_detects_actual_language_for_explicit_missing_source(self):
        module = load_plugin(PLUGIN, "ait19_language")
        server = FakeServer(lambda text: "你好。")
        job = self._make_job(module, server, mode="text", src_lang="fra")
        source = Path(job.in_dir) / "configs" / "text" / "a.xml"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            '<string_table><string id="a"><eng>Open the rusty door.</eng></string></string_table>',
            encoding="utf-8",
        )
        self.assertTrue(job.run())
        self.assertTrue(any(lang == "eng" for _text, lang in server.calls))

    def test_source_adapter_uses_canonical_assets_and_core_hash(self):
        module = load_plugin(PLUGIN, "ait19_source")
        self.assertEqual(module.PLUGIN_INFO["version"], "1.9.27")
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
        package = out / "ai_translate_plugin_v1.9.27.zip"
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
        self.assertEqual(module.PLUGIN_INFO["version"], "1.9.27")
        self.assertEqual(Path(module._ASSETS),
                         extracted.parent / "ai_translate_assets")
        self.assertEqual(module.termbase_lookup("КПК"), "PDA")

        manifest = json.loads(
            (out / "ai_translate_plugin_v1.9.27.manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["version"], "1.9.27")
        self.assertEqual(manifest["package_sha256"],
                         hashlib.sha256(package.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main(verbosity=2)
