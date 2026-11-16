# ai_translate 2.0.0

面向 **STALKER Localization Toolkit 2.x**（C++/Qt）的本地离线 AI 翻译插件。
基于 Hy-MT2 / llama.cpp 在本机完成 X-Ray 文本 XML、cfgxml、ltx 与脚本的翻译，
不依赖在线翻译 API。

仓库：<https://github.com/qy0214/stalker-localization-toolkit-ai-translate-2x>

## 功能

- 内置两档本地模型：Hy-MT2-7B Q4_K_M、Hy-MT2-1.8B Q8_0；也支持自选任意 GGUF 模型。
- 四种运行后端：CPU / CUDA 12.4 / CUDA 13.4 / Vulkan，自动检测硬件并给出推荐档位。
- 模型与 llama.cpp 运行库不随包分发；插件自动测速选源、按官方 SHA-256 校验，支持断点续传、取消与失败换源。
- 随包术语库 9059 条，分内置只读层与用户覆盖层；支持会话词表、技术精确表与参考软提示层。
- 场景批处理：连续短对话按上下文批量翻译，失败回退逐条；中文去翻译腔后处理。
- 完成账本 + 原子写盘：输入变化或输出未覆盖全部条目时自动重译，中断不留半截成品。
- 网络层用 ctypes 调系统 WinHTTP，不引入任何第三方 Python 依赖。
- 界面完全走宿主 `api.ui` 门面；工作线程只操作数据，控件操作统一投递回 UI 线程。

## 安装

把 `dist/ai_translate_plugin_v2.0.0.zip` 解压到工具集的 `plugins/` 目录，形成：

```text
<工具集目录>\
    plugins\
        ai_translate\
            plugin.py
            assets\
                xray_pkg.zip
                stalker_termbase.json
                glossary_builtin.json
                THIRD_PARTY_NOTICES.txt
```

重启工具集后，在工具页中打开「AI 翻译」。本插件是**目录插件**，入口文件名固定为
`plugin.py`，目录名 `ai_translate` 即插件 id。

插件包**不包含**模型、运行库、配置与日志。首次运行按硬件选择档位并下载；下载后
按官方 SHA-256 校验，文件不一致就删除并自动换下一个源。

运行数据默认落在：

```text
%LOCALAPPDATA%\STALKER Localization Toolkit\ai_translate
    bin\        llama.cpp 运行库
    models\     模型
    downloads\  下载缓存
    logs\       运行日志
```

绿色版：在 `plugin.py` 同目录放一个空的 `portable.txt`，数据改为写插件目录；
只读安装位置会自动回落到 `%LOCALAPPDATA%`。

## 仓库结构

| 路径 | 说明 |
|---|---|
| `plugins/ai_translate/plugin.py` | 插件入口，单文件实现 |
| `plugins/ai_translate/assets/` | X-Ray 解析器 + 术语数据（随包只读） |
| `dist/ai_translate_plugin_v2.0.0.zip` | 可直接分发的插件包 |
| `RELEASE_MANIFEST.md` / `release_manifest.json` | 发布清单（版本、哈希、一致性核对） |
| `tests/test_2x_contract.py` | 严格宿主契约测试 |
| `tests/fake_host.py` | 严格宿主假件 |
| `tests/test_ai_translate_fixes.py` | 功能回归测试 |
| `tests/test_share_package.py` | 分享包卫生与发布包一致性测试 |
| `tools/gen_release_manifest.py` | 重建 zip 与清单，可做漂移门禁 |
| `THIRD_PARTY_NOTICES.md` | 第三方来源、许可状态与致谢 |
| `.github/workflows/ci.yml` | CI：跑全量测试 + 发布清单门禁 |

## 版本与校验

- 插件版本：`ai_translate 2.0.0`
- 宿主插件 API：`1`
- 插件入口 SHA-256：
  `5aee0a05c0b3a3f1d8afe681887fa508c2bdae2a98310227e4364a6741dd8204`
- 当前 zip SHA-256：
  `523e31f5fc27f5de0e566eea3e01e007a609969b242e25a5178e2bfec939c24f`
- 术语规模：`glossary_builtin.json` 9059 条 / `stalker_termbase.json` 9059 条；
  11 个分类，两个文件条目数一致。
- 下载链路官方哈希：llama.cpp `b11612` 的 6 个 Windows 包 + 2 个 HuggingFace 模型。

```powershell
Get-FileHash 'dist\ai_translate_plugin_v2.0.0.zip' -Algorithm SHA256
```

完整核对结果见 [`RELEASE_MANIFEST.md`](RELEASE_MANIFEST.md)。

## 开发与测试

```powershell
# 宿主契约测试
python tests/test_2x_contract.py

# 功能回归测试
python tests/test_ai_translate_fixes.py

# 分享包 / 发布包一致性
python tests/test_share_package.py

# 重建 zip 与清单（--check 只校验不写盘）
python tools/gen_release_manifest.py --build
python tools/gen_release_manifest.py --check
```

当前全量测试：契约 55 项、功能回归 353 项、分享包 28 项，全部通过。

## 许可

本仓库自身代码按 **GPL-3.0** 分发，见 [`LICENSE`](LICENSE)。

第三方来源与许可见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)：

- X-Ray 解析器与翻译流程参考
  [wzyddg/stalker_auto-trans-tool_remake](https://github.com/wzyddg/stalker_auto-trans-tool_remake)
  （上游未声明许可证，版权归原作者；本仓库保留来源说明，不主张额外授权）；
- llama.cpp：MIT；Hy-MT 模型：Apache-2.0；二者均不随仓库分发。

该 GPL-3.0 许可不改变上述第三方组件的版权与授权状态。
