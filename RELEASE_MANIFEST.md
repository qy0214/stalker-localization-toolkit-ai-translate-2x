# AI 离线翻译插件（2.x）· 发布清单

- 生成时间：`2026-11-16T19:19:45+08:00`
- 范围：**AI 离线翻译插件（ai_translate）· STALKER Localization Toolkit 2.x**
- Git：`main` @ `7fd5fff`（有未提交改动）
- 已跟踪文件：19 个，1.61 MB

## 1. 版本与交付物

| 项 | 值 |
|---|---|
| 插件版本 | `2.0.0` |
| 预期版本 | `2.0.0` |
| 宿主插件 API | `1` |
| 适用平台 | `STALKER Localization Toolkit 2.x` |

| 角色 | 路径 | 大小 | SHA-256 | 说明 |
|---|---|---:|---|---|
| 插件入口 | `plugins\ai_translate\plugin.py` | 343.80 KB | `e70bf6ac08e9405724b0656936403656eea7f68f9c8bb3b9544e50cf5c9dbabf` | 2.x 目录插件（api.ui 门面） |
| 插件分享目录 | `plugins\ai_translate` | 1018.16 KB | — | 5 个文件（目录） |
| 插件发布 zip | `dist\ai_translate_plugin_v2.0.0.zip` | 345.57 KB | `1d53ab978ca465c42b16c498673b2063152af4b359309358cf9a8da68d867333` | 当前发布包 |
| 术语库（随包，只读） | `plugins\ai_translate\assets\glossary_builtin.json` | 311.86 KB | `da7c2e2795b04c4a2fb1533875a72927d0ed321fd7f73ded1e7f68c86b71d61f` | 运行时匹配的内置层 |
| 术语基表（分类） | `plugins\ai_translate\assets\stalker_termbase.json` | 353.93 KB | `9e689d3b5959b291927503a2d62563f4aa7c634aa02034889587144f0a10e941` | 导入用 |
| X-Ray 解析器 | `plugins\ai_translate\assets\xray_pkg.zip` | 7.38 KB | `2f3e1dcd4b2ce701e981d5c0f3e6c16cf17782692a5a892aa9cb1e159e63f8a6` | 解析 XML/LTX/脚本 |
| 第三方来源声明 | `plugins\ai_translate\assets\THIRD_PARTY_NOTICES.txt` | 1.18 KB | `d971f8c84ae81732effb86d0b3bb907166e81a2c804cf85207a0427f9b929d4e` | 随包分发（上游来源与许可状态） |

## 2. 下载链路官方 SHA-256

| 产物 | 文件 | 字节数 | SHA-256 |
|---|---|---:|---|
| llama.cpp `cpu` | `llama-b11612-bin-win-cpu-x64.zip` | — | `b8456ccb1be98365a5a2227efa5c495508a6a3ebce881c6189a1dbeaaa170b69` |
| llama.cpp `cuda-12.4` | `llama-b11612-bin-win-cuda-12.4-x64.zip` | — | `4c6d8afd23475f6eded74c4a3019a6746aab83183a3158f3fc4de1cb7c4f5f12` |
| llama.cpp `vulkan` | `llama-b11612-bin-win-vulkan-x64.zip` | — | `e62f0674970a70b007fa3c425d191ceacabbf02406d6c9ac8b6f27cd23abe9e9` |
| llama.cpp `cudart-12.4` | `cudart-llama-bin-win-cuda-12.4-x64.zip` | — | `8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6` |
| llama.cpp `cuda-13.4` | `llama-b11612-bin-win-cuda-13.4-x64.zip` | — | `e1c59ee10ead069dee12bf5a1f5a3ed294b144ea3f9c5006aa89675c5306de38` |
| llama.cpp `cudart-13.4` | `cudart-llama-bin-win-cuda-13.4-x64.zip` | — | `738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668` |
| 模型 | `Hy-MT2-7B-Q4_K_M.gguf` | 4624648896 | `9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b` |
| 模型 | `Hy-MT2-1.8B-Q8_0.gguf` | 1908528192 | `5c3fe0b1408a5ceb0143184ef247b11b579c525f4b02b060e6c851bb76fef1a4` |

来源：llama.cpp `b11612` GitHub Release asset digest；HuggingFace LFS oid（`tencent/Hy-MT2-7B-GGUF`、`tencent/Hy-MT2-1.8B-GGUF`）。下载后强制比对，不一致即删除并换源。

## 3. 一致性核对

| 检查 | 结果 | 说明 |
|---|---|---|
| 插件语法 | 一致 | compile() 通过 |
| 插件版本 == 预期 | 一致 | PLUGIN_INFO 2.0.0 / 预期 2.0.0 |
| PLUGIN_API == 1（2.x 宿主契约） | 一致 | 1 |
| PLUGIN_INFO.id 为 ASCII | 一致 | ai_translate |
| PLUGIN_INFO.repo 指向 2.x 专用仓库 | 一致 | https://github.com/qy0214/stalker-localization-toolkit-ai-translate-2x |
| 目录插件形状：plugins/ai_translate/{plugin.py,assets/} | 一致 | plugin.py + assets/ |
| 无 2.x 禁用导入（ssl/http/urllib.request/urllib.error/tkinter） | 一致 | 干净 |
| zip 文件名含插件版本 | 一致 | ai_translate_plugin_v2.0.0.zip |
| zip 条目 == 5 个预期文件 | 一致 | 多出：无；缺少：无 |
| zip 不含空目录/运行库/模型/日志/__pycache__ | 一致 | 干净 |
| 随包术语库：内置词表 + 分类基表都在 zip 内 | 一致 | 缺少：无 |
| 随包术语库非空壳（两文件合计 > 600 KB） | 一致 | glossary_builtin.json=319342；stalker_termbase.json=362428 |
| zip 内文件 == 工作副本 | 一致 | 全部一致 |
| 官方哈希：llama.cpp 6 项 | 一致 | 登记 6 项；不一致：无 |
| 官方哈希：模型 2 项 | 一致 | 登记 2 项；不一致：无 |
| 官方哈希：8 个合法 SHA-256 | 一致 | 8 个 |
| 官方哈希：llama.cpp 标签 b11612 | 一致 | 源码常量 _LLAMA_TAG='b11612' |
| 分享目录不含 bin/models/logs/downloads/config.json | 一致 | 干净 |
| 插件目录无被跟踪的 __pycache__ | 一致 | 干净 |
| 无绝对路径/用户名泄漏 | 一致 | 未发现 |
| 术语基表：_meta.total_entries == 实际分类合计 | 一致 | meta 9059 / 实际 9059 |
| 术语基表：_meta.categories == 实际分类条数 | 一致 | meta 11 类 / 实际 11 类，差异：无 |
| 术语库：随包条目 >= 8000 | 一致 | 9059 条 |
| 术语库：随包 >= 分类基表 | 一致 | 9059 / 9059 |
| dist 只保留当前版本 zip | 一致 | 仅当前版本 |

## 4. 术语数据规模

| 文件 | 条目 |
|---|---:|
| `glossary_builtin.json`（随包，运行时匹配） | 9059 |
| `stalker_termbase.json`（分类导入） | 9059 |

分类明细：阵营与组织 107、人物与军衔 3069、地点与区域 359、变异生物 67、物品·武器·装备·神器 920、任务 907、百科与术语 3092、界面与提示 345、新闻·对话·其他 27、ANTHOLOGY 参考 148、技术缩写 18

## 5. 安装位置与数据落点

- 插件：解压到 `<工具集>\plugins\` ⇒ `plugins\ai_translate\plugin.py` + `assets\`（目录插件）。
- 运行数据（模型/运行库/日志/配置）默认落 `%LOCALAPPDATA%\STALKER Localization Toolkit\ai_translate`。
- 绿色版：在 `plugin.py` 同目录放一个空的 `portable.txt`，数据改为写插件目录（只读安装位置会自动回落）。
- 运行库/模型不随包分发，首次使用由插件按官方 SHA-256 下载。

## 6. 校验方式

```powershell
# 校验当前插件 zip
Get-FileHash 'dist\ai_translate_plugin_v2.0.0.zip' -Algorithm SHA256

# 重新打包 + 重生成清单
python tools/gen_release_manifest.py --build

# CI 门禁（只校验不写文件）
python tools/gen_release_manifest.py --check
```

## 7. 备注

- 本清单只读文件，不修改插件；范围仅限 `plugins/ai_translate/` 与发布 zip。
- 发布包只含代码 + 解析器 + 术语数据；运行库、模型、配置、日志均不随包。
- 2.x 发行包不带 `_ssl.pyd`：插件网络层用 ctypes + 系统 WinHTTP，发布前由一致性检查守住「无 ssl/http/urllib.request 导入链」。
- 官方哈希取自发布方元数据（2026-10-23）；若上游重新上传同标签产物，需重新核对。
