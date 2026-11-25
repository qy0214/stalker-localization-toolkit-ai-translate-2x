# -*- coding: utf-8 -*-
"""发版助手：改版本号 → 重建 zip/清单 → 打印平台注册表条目。

用法：
    python tools/release_plugin.py 2.0.1

做三件事：
  1) 把 plugin.py 的 PLUGIN_INFO.version 与 tools/gen_release_manifest.py 的
     EXPECTED_PLUGIN_VERSION 一起改成新版本（平台「检查插件更新」比对的就是
     这个版本号，写死不变会导致永远显示"无需更新"）；
  2) 重建发布 zip、安装就绪 zip 与发布清单；
  3) 打印可直接粘进平台注册表 plugins.json 的条目（含真实 sha256）。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "ai_translate" / "plugin.py"
TOOLS = ROOT / "tools" / "gen_release_manifest.py"

PLUGIN_ID = "ai_translate"
REPO = "https://github.com/qy0214/stalker-localization-toolkit-ai-translate-2x"


def current_version() -> str:
    text = PLUGIN.read_text(encoding="utf-8")
    m = re.search(r'"version"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else ""


def bump(version: str) -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    text2 = re.sub(r'("version"\s*:\s*")[^"]+(")',
                   lambda m: m.group(1) + version + m.group(2), text, count=1)
    if text2 == text:
        raise SystemExit("plugin.py 里没找到 version 字段")
    PLUGIN.write_text(text2, encoding="utf-8")

    ttext = TOOLS.read_text(encoding="utf-8")
    ttext2 = re.sub(r'(EXPECTED_PLUGIN_VERSION\s*=\s*")[^"]+(")',
                    lambda m: m.group(1) + version + m.group(2), ttext, count=1)
    TOOLS.write_text(ttext2, encoding="utf-8")


def sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    version = sys.argv[1].strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("版本号格式应为 x.y.z：%s" % version)

    old = current_version()
    bump(version)
    for sync_script in ("tools/sync_core.py", "tools/sync_assets.py"):
        subprocess.run([sys.executable, str(ROOT / sync_script), "--write"],
                       check=True, cwd=str(ROOT))
    subprocess.run([sys.executable, str(TOOLS), "--build"], check=True,
                   cwd=str(ROOT))

    install_zip = ROOT / "dist" / ("ai_translate_plugin_v%s_install.zip" % version)
    repo_zip = ROOT / "dist" / ("ai_translate_plugin_v%s.zip" % version)
    # 字段名严格按平台 tools/plugin_manager/main.cpp：
    #   plugins[] 里每条读 id / version / package / sha256 / entry / target
    #   package 不以 http 开头时，平台会拼到
    #   https://raw.githubusercontent.com/ALittleHuman/STALKER-Localization-Toolkit-2.x-Network-Plugin/main/
    #   entry 是**文件名**（在解压目录里递归按文件名找），找到后其所在目录
    #   整体替换 plugins/<target>/。
    entry = {
        "id": PLUGIN_ID,
        "version": version,
        "package": "https://github.com/qy0214/stalker-localization-toolkit-ai-translate-2x"
                   "/releases/download/v%s/%s" % (version, install_zip.name),
        "sha256": sha256(install_zip),
        "entry": "plugin.py",
        "target": PLUGIN_ID,
        "name": "AI 离线翻译（Hy-MT2 / 自选模型）",
        "repo": REPO,
        "note": "本地离线 AI 翻译 X-Ray 文本：Hy-MT2 两档内置模型 + 自选 GGUF，"
                "CPU/CUDA/Vulkan 自动识别，多路并发，术语库随包。",
    }
    print()
    print("版本 %s → %s" % (old, version))
    print("仓库发布包   : %s  sha256=%s" % (repo_zip.name, sha256(repo_zip)))
    print("平台安装包   : %s  sha256=%s" % (install_zip.name, entry["sha256"]))
    print()
    print("粘进平台注册表 plugins.json 的条目（放进 plugins 数组里）：")
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    print()
    print("提醒：注册表地址是平台硬编码的 "
          "https://raw.githubusercontent.com/ALittleHuman/"
          "STALKER-Localization-Toolkit-2.x-Network-Plugin/main/plugins.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
