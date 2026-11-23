# ai_translate 2.0.22

面向 **STALKER Localization Toolkit 2.x**（C++/Qt）的本地离线 AI 翻译插件：
用 Hy-MT2 / llama.cpp 在本机翻译 X-Ray 文本 XML、cfgxml、ltx 与脚本，不依赖在线翻译 API。

仓库：<https://github.com/qy0214/stalker-localization-toolkit-ai-translate-2x>

## 版本迭代

完整明细（修复清单、证据与量化结果）见 [`CHANGELOG.md`](CHANGELOG.md)。

| 版本 | 摘要 |
|---|---|
| **2.0.22** | 三轮随机文件测试（678 文件）+ 全 MOD 全文件测试（1600 文件 / 24 万条目）：GAMMA/Gunslinger 软层 13946 条整体退役（逐条语义抽检明确错率约 31%）、硬层子串替换加固（半中半英写出 112→0）、title 长句豁免找回 23 条正文、藏宝点目录豁免找回 679 条、ogg/music 保护收窄、词库内容级修正 13 条、`termbase_lookup` 带引号键 50 条不可达修复；词库 22524 条，测试 16/16 全绿 |
| 2.0.21 | 全库确定性错误清理 14 条 + 垃圾过滤三处口径修正 + 闸门豁免色码内数字 + 专名保护硬层子串替换；词库 36472 |
| 2.0.20 | 多源语料收割（Gunslinger / CoP 官中 / NS2016 / wiki / GAMMA），词库 21065→36323；`termbase_lookup` O(N)→O(1)（3.656→0.125 ms） |
| 2.0.19 | 人工汉化语料收割 1.2 万+ 术语对，NS7 官方译名为权威 |
| 2.0.18 | 出口级换行对齐 + 型号语境窗口放宽 + 品牌词库 |
| 2.0.17 | 新译文复核 3 处修复（调试正则误伤 / 标题误伤 / 占位符闸门兜底） |
| 2.0.16 | st_characters 97 条人物绰号核实收录 + 11 处误译纠正 |
| 2.0.15 及更早 | 见 [`CHANGELOG.md`](CHANGELOG.md) |

## 功能

- 内置两档本地模型（Hy-MT2-7B Q4_K_M、Hy-MT2-1.8B Q8_0），也支持自选任意 GGUF。
- 四种后端：CPU / CUDA 12.4 / CUDA 13.4 / Vulkan，自动检测硬件并推荐档位。
- 多路翻译按显存（GPU）或内存（CPU）动态生成 1..16 路，每档带显存估算。
- 八道译后闸门（占位符 / 换行 / 粗口 / 完整性 / 残留源文 / 长度比 / 回显等），收尾输出「提示词体检」遥测。
- 随包术语库 22524 条：内置只读层 + 用户覆盖层，含 canon/names 硬层、技术精确表与软提示层；另有会话词表与 TM-lite 精确复用。
- 场景批处理：连续短对话按上下文批量翻译，失败回退逐条；中文去翻译腔后处理。
- 完成账本 + 原子写盘：输入变化或输出未覆盖全部条目时自动重译，中断不留半截成品。
- 模型与 llama.cpp 运行库不随包分发：自动测速选源、按官方 SHA-256 校验、断点续传、失败换源。
- 跟随宿主：小文本请求走 `api.host.http_*`、中小产物走 `api.host.http_download`、GB 级模型走自建流式管道；UI 回调走 `api.ui.post`，老宿主自动回退，不引入第三方 Python 依赖。

## 界面说明

插件只在工具集的**一个工具页内**再分页：`① 硬件 / ② 环境 / ③ 模型 / ④ 引擎 / ⑤ 翻译 / ⑥ 术语（可选）`，不额外注册宿主工具页。

![AI 翻译插件界面（翻译进行中）](docs/images/ai_translate_progress.png)

- ⑥ 只放附加提示词与强制术语，不影响前五步；`文件类型` 默认「auto 自动识别全部」。
- 勾选项：强制术语、识别 ID 引用层、输出流补丁、只译引用项、保护专名/型号、先预览、翻译脚本/XML 字面文本、自动生成会话词表、TM-lite 精确复用。
- 底部进度条常驻：下载按字节推进，翻译按本轮处理文件数推进；旁边两行给出
  `阶段：翻译中 12/87 · configs\items\x.ltx` 与 `已翻译 12 / 87 个文件（14%），条目 345 条`。
  分母是自动识别出的整个 MOD 待翻译文件数（只算确实含可译文本的文件）。
- `查看日志（弹窗）`：日志不占页面高度；完整日志在
  `%LOCALAPPDATA%\STALKER Localization Toolkit\ai_translate\logs\translate_run.log`。

## 安装

把 `dist/ai_translate_plugin_v2.0.22.zip` 解压到工具集的 `plugins/` 目录：

```text
<工具集目录>\plugins\ai_translate\plugin.py
                          \assets\{xray_pkg.zip, stalker_termbase.json,
                                   glossary_builtin.json, THIRD_PARTY_NOTICES.txt}
```

重启工具集后在工具页打开「AI 翻译」。本插件是**目录插件**，入口固定为 `plugin.py`，
目录名 `ai_translate` 即插件 id。

插件包不含模型、运行库、配置与日志；首次运行按硬件选档下载并按官方 SHA-256 校验，
不一致就删除并自动换源。运行数据默认落在：

```text
%LOCALAPPDATA%\STALKER Localization Toolkit\ai_translate\{bin, models, downloads, logs}
```

绿色版：在 `plugin.py` 同目录放一个空的 `portable.txt`，数据改写插件目录；
只读安装位置自动回落 `%LOCALAPPDATA%`。

## 接入平台插件管理（注册表 / 检查更新 / 一键安装）

平台插件管理器只认一份线上注册表
（`ALittleHuman/STALKER-Localization-Toolkit-2.x-Network-Plugin/main/plugins.json`）。
联网能力自 BETA.249 起已内置于平台，不需要额外的「联网插件」。
**本插件条目尚未写入该注册表**，因此仅有平台的用户在插件管理里搜不到；打通三步：

```powershell
# 1) 改版本号 + 重建 zip/清单 + 打印可直接粘贴的注册表条目
python tools/release_plugin.py 2.0.22
# 2) 把安装包传成 GitHub Release 资产（tag = v<版本号>）
gh release create v2.0.22 dist/ai_translate_plugin_v2.0.22_install.zip --title "ai_translate 2.0.22"
# 3) 把第 1 步打印的条目追加进注册表 plugins 数组（需要该仓库写权限）
```

之后用户流程：平台 → 插件管理 → 检查插件更新 → 选中 ai_translate →
「现在下载并安装」→ 重启工具集。字段含义与已知限制见
[`docs/PLUGIN_REGISTRY.md`](docs/PLUGIN_REGISTRY.md)。

## 仓库结构

| 路径 | 说明 |
|---|---|
| `plugins/ai_translate/plugin.py` | 插件入口，单文件实现 |
| `plugins/ai_translate/assets/` | X-Ray 解析器 + 术语数据（随包只读） |
| `dist/ai_translate_plugin_v2.0.22.zip` | 可直接分发的插件包 |
| `dist/ai_translate_plugin_v2.0.22_install.zip` | 平台安装包（顶层 `ai_translate/`） |
| `RELEASE_MANIFEST.md` / `release_manifest.json` | 发布清单（版本、哈希、一致性核对） |
| `CHANGELOG.md` | 各版本完整修复明细与证据 |
| `corpus/` | 收割用双语语料与退役软层留档（不随发布包分发） |
| `audit-reports/` | 审计报告与测试原始输出（可复跑核对） |
| `tests/` | 契约 / 功能回归 / 各版本专项 / 分享包一致性测试，`fake_host.py` 为严格宿主假件 |
| `tools/gen_release_manifest.py` | 重建 zip 与清单，可做漂移门禁 |
| `tools/release_plugin.py` | 发版助手：改版本号 → 重建包 → 打印注册表条目 |
| `tools/test_random20_per_mod.py` | 每 MOD 随机 N 文件（或 `all` 全量）判定链重放测试，只读 |
| `tools/audit_termbase_full.py` | 词库全量深度审计（结构/质量/译文/软层/硬层/性能六层） |
| `docs/PLUGIN_REGISTRY.md` | 接入平台插件管理说明 |
| `THIRD_PARTY_NOTICES.md` | 第三方来源、许可状态与致谢 |
| `.github/workflows/ci.yml` | CI：全量测试 + 发布清单门禁 |

## 版本与校验

- 插件版本：`ai_translate 2.0.22`　宿主插件 API：`1`
- 插件入口 SHA-256：`23ee71961bc2c76a5eac799cfcb26b0c9fbde179e00f81696a4cd00e95159bfa`
- 发布 zip SHA-256：`7592ea633418a9d2fb94f795b6c20a41062cf8fca2904cbf92b4583b4d91d022`
- 平台安装包 SHA-256：`e322127295fc27936a939ddd4dfb9f8b77e75976ae9fd71a7dea644994de63f8`
- 术语规模：两张 JSON 各 **22524** 条、11 个分类、条目数一致；
  canon 4276 / names 3250 / technical 18 / reference 14981。
  2.0.22 起 `Gunslinger 参考`/`GAMMA 参考`（13946 条）退役，留档
  `corpus/soft_reference_retired.json`，不参与直取与提示。
- 下载链路官方哈希：llama.cpp `b11612` 的 6 个 Windows 包 + 2 个 HuggingFace 模型。

```powershell
Get-FileHash 'dist\ai_translate_plugin_v2.0.22.zip' -Algorithm SHA256
```

完整核对结果见 [`RELEASE_MANIFEST.md`](RELEASE_MANIFEST.md)。

## 开发与测试

```powershell
python tests/test_2x_contract.py          # 严格宿主契约
python tests/test_ai_translate_fixes.py   # 功能回归
python tests/test_share_package.py        # 分享包卫生 + 发布包一致性
python tools/gen_release_manifest.py --build   # 重建 zip 与清单
python tools/gen_release_manifest.py --check   # 只校验不写盘（CI 门禁）
python tools/test_random20_per_mod.py "" 0 "" all   # 全 MOD 全文件判定链重放
```

当前全量：16 个测试文件全绿（契约 67、功能回归 356、动态多路 42、提示词规则 46、
页签与宿主门面 21、分享包 30、2.0.22 专项 79，及各版本专项）；发布一致性 25/25。
测试统一 `sys.dont_write_bytecode = True`，不会把 `.pyc` 写进随包目录。

## 许可

本仓库自身代码按 **GPL-3.0** 分发，见 [`LICENSE`](LICENSE)。
第三方来源与许可状态见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)：
X-Ray 解析器参考 wzyddg/stalker_auto-trans-tool_remake（上游未声明许可证，版权归原作者）；
llama.cpp（MIT）与 Hy-MT 模型（Apache-2.0）均不随仓库分发；
术语库条目收割自 NS 汉化补丁（权威源）、GAMMA 汉化组、Gunslinger 人工汉化、
CoP 官方中文、ANTHOLOGY 汉化整合包与 bilibili wiki（均未声明开源许可证，只保留署名）。
该 GPL-3.0 许可不改变上述第三方组件的版权与授权状态。
