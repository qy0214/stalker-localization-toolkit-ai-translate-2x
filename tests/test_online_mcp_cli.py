# -*- coding: utf-8 -*-
"""通用在线 MCP/CLI 独立包与真实 stdio 全流程测试。"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from mock_openai import MockOpenAI  # noqa: E402

BUILD = ROOT / "tools/gen_release_manifest_mcp_online.py"


class McpClient:
    def __init__(self, server: Path, cfg: Path, cwd: Path):
        env = dict(os.environ)
        env["STALKER_TRANSLATE_CONFIG"] = str(cfg)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, str(server)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(cwd),
            env=env)
        self.q = queue.Queue()
        self.id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.q.put(line)

    def request(self, method, params=None, timeout=60):
        self.id += 1
        msg = {"jsonrpc": "2.0", "id": self.id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode())
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = json.loads(self.q.get(timeout=deadline - time.time()).decode())
            if resp.get("id") == self.id:
                return resp
        raise TimeoutError(method)

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
            self.proc.wait(timeout=5)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass


def payload(resp):
    if "error" in resp:
        raise AssertionError(resp["error"])
    result = resp["result"]
    if result.get("isError"):
        raise AssertionError(result["content"][0]["text"])
    return json.loads(result["content"][0]["text"])


class OnlineIntegrationTests(unittest.TestCase):
    def test_package_cli_and_mcp_end_to_end(self):
        with tempfile.TemporaryDirectory(prefix="ait_online_full_") as td:
            base = Path(td)
            dist = base / "dist"
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            subprocess.run(
                [sys.executable, str(BUILD), "--build", "--out-dir", str(dist)],
                cwd=str(ROOT), env=env, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            versions = json.loads((ROOT / "versions.json").read_text(encoding="utf-8"))
            package = dist / ("stalker-translate-online-MCP-%s.zip" % versions["mcp_online"])
            self.assertTrue(package.is_file())
            extract = base / "package"
            with zipfile.ZipFile(package) as archive:
                names = set(archive.namelist())
                self.assertIn("stalker_translate_online/server.py", names)
                self.assertIn("stalker_translate_core/mcp.py", names)
                self.assertIn("assets/stalker_termbase.json", names)
                archive.extractall(extract)

            mod = base / "mod"
            (mod / "text").mkdir(parents=True)
            (mod / "text/st_hello.xml").write_text(
                '<?xml version="1.0" encoding="utf-8"?>\n<string_table>\n'
                '<string id="hello"><rus>Привет, сталкер.</rus></string>\n'
                '<string id="gun"><eng>Heckler & Koch</eng></string>\n'
                '</string_table>\n', encoding="utf-8")
            mock = MockOpenAI("mock-model").start()
            try:
                cfg = base / "config.json"
                cfg.write_text(json.dumps({
                    "base_url": mock.base_url, "model": "mock-model",
                    "allowed_roots": [str(base)], "rpm": 0, "tpm": 0,
                    "cache_dir": str(base / "cache"),
                    "audit_dir": str(base / "audit"),
                    "open_progress_page": False,
                }), encoding="utf-8")

                cli = subprocess.run(
                    [sys.executable, str(extract / "cli.py"), "--config", str(cfg),
                     "translate-text", "Привет, сталкер."],
                    cwd=str(extract), env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8",
                    timeout=60)
                self.assertEqual(cli.returncode, 0, cli.stderr)
                self.assertRegex(cli.stdout, r"[\u4e00-\u9fff]")

                client = McpClient(extract / "server.py", cfg, extract)
                try:
                    init = client.request("initialize", {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {}, "clientInfo": {"name": "test"}})
                    self.assertEqual(init["result"]["serverInfo"]["name"],
                                     "stalker-translate-online")
                    tools = client.request("tools/list")["result"]["tools"]
                    self.assertEqual([t["name"] for t in tools], [
                        "check_env", "scan_project", "translate_text",
                        "translate_project", "job_status", "job_cancel",
                        "validate_output", "glossary_lookup",
                        "plan_translation", "extract_batch", "merge_answers"])
                    self.assertTrue(payload(client.request(
                        "tools/call", {"name": "check_env", "arguments": {}}))["ready"])
                    scan = payload(client.request(
                        "tools/call", {"name": "scan_project",
                                       "arguments": {"input_dir": str(mod)}}))
                    self.assertEqual(scan["entries"], 2)
                    started = payload(client.request(
                        "tools/call", {"name": "translate_project",
                                       "arguments": {"input_dir": str(mod),
                                                     "concurrency": 2}}))
                    for _ in range(200):
                        status = payload(client.request(
                            "tools/call", {"name": "job_status",
                                           "arguments": {"job_id": started["job_id"]}}))
                        if status["state"] in ("succeeded", "needs_review",
                                               "failed", "cancelled"):
                            break
                        time.sleep(0.05)
                    self.assertEqual(status["state"], "succeeded", status)
                    validation = payload(client.request(
                        "tools/call", {"name": "validate_output",
                                       "arguments": {"input_dir": str(mod),
                                                     "output_dir": status["output_dir"]}}))
                    self.assertTrue(validation["ok"], validation)
                    glossary = payload(client.request(
                        "tools/call", {"name": "glossary_lookup",
                                       "arguments": {"query": "Pripyat"}}))
                    self.assertEqual(glossary["exact"], "普里皮亚季")
                finally:
                    client.close()
            finally:
                mock.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
