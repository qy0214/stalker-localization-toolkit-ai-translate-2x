# STALKER AI Translate Suite

单一仓库维护共享翻译核心、术语库和五个独立产品：2.x 离线插件、1.9.x 兼容插件、
Hermes MCP、通用在线 MCP/CLI、通用翻译技能。核心与资产只保留一份源，发布时生成五个独立分享包。

仓库：<https://github.com/qy0214/stalker-localization-toolkit-ai-translate-2x>

## 产品矩阵

| 产品 | 当前版本 | 状态 |
|---|---|---|
| 2.x 离线桌面插件 | 2.0.29 | active |
| 1.9.x 兼容离线插件 | 1.9.26 | active |
| Hermes MCP | 1.1.11 | active（共享 core，完整离线作业） |
| 通用在线 MCP / CLI | 0.2.2 | active（OpenAI 兼容、stdio、共享全链） |
| 通用翻译技能 | 0.2.2 | active（对话 AI 批量协议，与 2.x 共享全链） |

## 统一发布列车

当前列车：`2026.11.25`，tag：`suite-v2026.11.25`。总清单与资产哈希见
[`release_train.json`](release_train.json) 和 [`RELEASE_TRAIN.md`](RELEASE_TRAIN.md)。

```powershell
python tools/release.py --build   # 构建五个产品并生成总清单
python tools/release.py --check   # 校验版本、兼容矩阵与资产哈希
python tools/release.py --tag --dry-run   # 查看本地 tag 命令
python tools/release.py --tag     # 仅工作区干净时创建本地 tag
```

脚本不会自动推送或创建 GitHub Release。旧 1.9.x 与 MCP 仓库（`stalker-localization-toolkit-ai-translate`、
`stalker-translate-mcp`）**已归档**并在各自 README 顶部引导至本单仓库；首个发布列车
[`suite-v2026.11.25`](https://github.com/qy0214/stalker-localization-toolkit-ai-translate-2x/releases/tag/suite-v2026.11.25)
已包含五个分享包、SHA-256 与发布说明。

## 2.x 插件迭代

完整明细（修复清单、证据与量化结果）见 [`CHANGELOG.md`](CHANGELOG.md)。

| 版本 | 摘要 |
|---|---|
| **2.0.29**（2026.11.25 列车） | 五合一单仓库归库并首次 GitHub Release（suite-v2026.11.25）：两轮深度审计全修复（未定义名/调用点/只读化/畸形 XML 告警/锁退避/CI 加固/XML 非法字符，见 MONOREPO_DEEP_AUDIT_20261125.md）；wzyddg 解析器来源注释补全；`strip_output_wrappers` 输出外壳剥离（代码栅栏/"译文："前缀/引号包裹）接入单条+批量出口——小模型提示词不可依赖时由代码硬闸门兜底；术语库健康门 10 项回归纳入 CI；测试 34/34 全绿、ruff F821=0、bandit High=0。 |
| **2.0.28** | 小模型交付可靠性修复：纯符号/零变化文本不再生成文件或记账，旧账本按 pipeline + SHA-256 强制迁移；取消/异常/写盘失败均撤销完成记录，生成 ID 定义表按文件事务落盘并补齐中断恢复；终检升级为结构化硬门禁（悬空 ID、占位符损坏、漏译、硬术语次数、语义方向冲突），失败不再显示「全部完成」；补齐严格 token 序列、提示词串入、多语言字段保真和原子替换并发保护。新增 60 项真实解析器/假模型边界回归，21 个测试文件全绿，发布一致性 25/25 |
| 2.0.27 | 术语库交叉一致性审计（新方法，弃用旧脚本）：同源跨类冲突 15 组全部统一（Loners→独行者、Wolf→狼、Cap→帽子、Mama's Beads→妈妈的念珠、Generator→发电机 等）、中文半角标点 1087 条规范化、俄英同源（Мамины бусы 系列）对齐；交叉审计 S1/S4a 归零，两仓四副本同步，测试 20/20 全绿 |
| 2.0.26 | 术语库深度审查与优化：16 个 MOD / 1623 文件 / 64.2 MB 全量词频扫描 + 人工复核，新增「高频硬词」分类 51 条并挂入 canon 层（主产地名格变补位：Зона 主语单数、Припяти/Болотах/Кордоне/Агропроме/Янтаре/Радаре/Юпитере/Лощина 四格变；英文地名、CNPP/ЧАЭС 机组、Lab X-8/10/16/18、固定物品与 UI）；修正 9 处错译（Russian→俄语、НИИ→研究所、Director→主管、Darkscape→幽暗地带、Исполнитель желаний→许愿石、Крота→鼹鼠、Шурупа→螺丝、Стрелка 三格变→史特列洛克，删除 S.T.A.L.K.E.R.→潜行者）；词库 24456→24455（13 分类），本机用户层同步写入 51 条；测试 20/20 全绿 |
| 2.0.25 | aera MOD 审计（188 文件 / 36138 应译条目）+ 人工对照包按 id 全量比对驱动的修复（不做 A/B 开关）：ident 假跳过修复（`_name/_descr/_text/_title` 等 id 后缀豁免，2206 条找回）；强制术语长文阈值（≥600 字符走分段链，1.8B 长文保不住占位符）；上下文串入切片级兜底；字面换行收敛 11 条；CHS 人工对照收割 1932 条入「人工对照」硬层（P1/P2/P4）；口语化提示词锚例（P6）；审计/清单工具口径修正。词库 22524→24456，测试 20/20 全绿，全 MOD 重放零漂移 |
| 2.0.24 | IWP 已翻译产物全量深度审计（186 文件 / 18978 应译条目）驱动的 6 项修复：批量对话路径补齐占位符掩码/还原/数字/粘连/错位校验（P0，实测 `%c[default]→%c[默认]` 等 16 条运行期破坏全部来自该无掩码旁路）；「（原文数字/型号 …）」注记退役（年代误报 38 条清零 + 定向重试 + 裸数字兜底 + 型号数字双注记修复）；短名回显新增定名指令兜底；粘连残词（克里门enko 类）新增确定性闸门；未解析引用报告收紧；汉字间空格收敛。全 MOD 重放零漂移，测试 19/19 全绿 |
| 2.0.23 | 界面选项精简：11 个勾选逐项核对后，5 个「关闭只有坏处」的收编为内部固定开启（引用层/完整补丁/TM-lite/会话词表/高频专名），1 个实验键移出 UI，保留 5 个各挂一行开关后果说明；强制术语/保护专名出厂默认改为开；引用层无文本表自动回退整目录模式（原先警告后终止）；词库 22524 条，测试 18/18 全绿 |
| 2.0.22 | 三轮随机文件测试（678 文件）+ 全 MOD 全文件测试（1600 文件 / 24 万条目）：GAMMA/Gunslinger 软层 13946 条整体退役（逐条语义抽检明确错率约 31%）、硬层子串替换加固（半中半英写出 112→0）、title 长句豁免找回 23 条正文、藏宝点目录豁免找回 679 条、ogg/music 保护收窄、词库内容级修正 13 条、`termbase_lookup` 带引号键 50 条不可达修复；词库 22524 条，测试 16/16 全绿 |
| 2.0.21 | 全库确定性错误清理 14 条 + 垃圾过滤三处口径修正 + 闸门豁免色码内数字 + 专名保护硬层子串替换；词库 36472 |
| 2.0.20 | 多源语料收割（Gunslinger / CoP 官中 / NS2016 / wiki / GAMMA），词库 21065→36323；`termbase_lookup` O(N)→O(1)（3.656→0.125 ms） |
| 2.0.19 | 人工汉化语料收割 1.2 万+ 术语对，NS7 官方译名为权威 |
| 2.0.18 | 出口级换行对齐 + 型号语境窗口放宽 + 品牌词库 |
| 2.0.17 | 新译文复核 3 处修复（调试正则误伤 / 标题误伤 / 占位符闸门兜底） |
| 2.0.16 | st_characters 97 条人物绰号核实收录 + 11 处误译纠正 |
| 2.0.15 及更早 | 见 [`CHANGELOG.md`](CHANGELOG.md) |

### 1.9.x 兼容插件迭代

| 版本 | 摘要 |
|---|---|
| **1.9.26**（2026.11.25 列车） | 纳入五合一单仓库（products/plugin_1_9x），同步 core 1.0.1 全部修复；新增 `strip_output_wrappers` vendored 副本接入单条+批量出口（小模型输出外壳硬闸门）；旧独立仓库已归档并引导至本库。 |
| **1.9.25** | 小模型交付可靠性：零变化不落盘、旧账本强制迁移、取消/异常/写盘失败不记完成、生成 ID 事务、终检硬门禁与 54 项交付回归。 |
| 1.9.24 | 同步 2.0.27 术语交叉审计：15 组同源冲突统一、1087 条中文标点规范化。 |
| 1.9.23 | 同步 2.0.26 术语深度审查：51 条高频硬词、9 处错译修正。 |
| 1.9.22 | 同步 2.0.25 aera 审计：ident 假跳过修复、长文分段、上下文兜底、1932 条人工对照术语。 |
| 1.9.21 | 同步 2.0.24 IWP 深审：批量占位符防护、错位检测、保真注记退役、粘连残词闸门。 |
| 1.9.20 | 同步 2.0.23：界面选项精简、引用层无文本表自动回退。 |
| 1.9.19 | 同步 2.0.22：软层退役、子串替换加固、title/藏宝点/ogg 守卫修正、数据根兼容式闸门。 |
| 1.9.18 及更早 | 筛选器同步、全库错误清理、多源语料收割与词库扩充，详见 [`CHANGELOG.md`](CHANGELOG.md)。 |

### Hermes MCP 迭代

| 版本 | 摘要 |
|---|---|
| **1.1.11**（2026.11.25 列车） | 纳入五合一单仓库，同步 core 1.0.1 全部修复；模型响应出口接入 `strip_output_wrappers` 外壳剥离；新增可配置 `allowed_roots` 边界；旧独立仓库已归档并引导至本库。 |
| **1.1.10** | 修复中断悬空 ID、零变化输出、占位符损坏、漏译和完成状态误报；增加语义方向、硬术语终检、问题文件重译与旧账本迁移。 |
| 1.1.9 | 修复进度页回归，统一 MCP 主线，清理旧 CLI 支线。 |
| 1.1.8 | 首次调用时同时选择 1.8B/7B 模型与并发路数。 |
| 1.1.7 | 工具行进度、浏览器实时进度页与 MCP 日志通知。 |
| 1.1.6 | 修复管理员权限启动内核导致 Hermes 桌面端连接失败。 |
| 1.1.5 | 单技能安装、禁用危险工具集、加入常驻规则。 |
| 1.1.2 | 安装后自动重启 Hermes，修复 YAML UTF-8 读写。 |
| 1.1.1 | 实时进度板、暂停/继续/停止、断点续译。 |
| 1.0.0 | 独立仓库交付、Hermes 注册与首个 stdio MCP 冒烟测试。 |

### 通用在线 MCP / CLI 迭代

| 版本 | 摘要 |
|---|---|
| **0.2.2** | 批次协议两处关键修正：**同源多键折叠**（同一句在不同场景提示键只须作答一次，答任意键全键生效）与 **stale 清理**（本轮未再问起的旧 pending 键自动出清单，杜绝“循环至零”空转）。0.2.0 接入共享 core 全链（与 2.x 同源判定链/词库直取/运行期掩码/闸门回写/断点账本）、`pending_entries`/`apply_batch` 协议、core 提示词统一与 MCP stdout 隔离。 |
| **0.1.1**（2026.11.25 列车） | 纳入五合一单仓库，同步 core 1.0.1 全部修复与统一发布列车（tag suite-v2026.11.25）。 |
| **0.1.0** | 首个通用版本：OpenAI 兼容 Provider、stdio MCP、CLI、术语/缓存/预算/限流、质量闸门、X-Ray XML 项目任务、断点账本、取消/进度、输出校验与文件安全边界。 |

### 通用翻译技能迭代

| 版本 | 摘要 |
|---|---|
| **0.2.2** | 发布三连修：0.2.0 包装层白名单漏转发 pending/apply-batch（用户第一步即报错，0.2.1 补齐）；0.2.2 修同源折叠与 stale 清理保证清单收敛。工作流定稿**对话 AI 批量协议**（收集 pending → 平台 AI 作答 → apply-batch → 填充遍，循环至 pending=0）：筛选/直取/掩码/回写全部程序侧完成，模型只答筛后残余、一遍过；SKILL.md 硬护栏改为「禁止本地小模型代答、技能不做 API 编排」（自动 API 需求指向通用在线 MCP 独立包）。 |
| **0.1.2**（未发布） | 护栏版 SKILL.md 重打包，因并入 0.2.0 回收产物。 |
| **0.1.1**（2026.11.25 列车） | 纳入五合一单仓库，同步 core 1.0.1 与统一发布列车（tag suite-v2026.11.25）。 |
| **0.1.0** | 首个通用技能包：平台中立工作流、MCP 优先/CLI 回退、跨平台包装脚本、内置运行时与独立分享包。 |

## 2.x 插件功能

- 内置两档本地模型（Hy-MT2-7B Q4_K_M、Hy-MT2-1.8B Q8_0），也支持自选任意 GGUF。
- 四种后端：CPU / CUDA 12.4 / CUDA 13.4 / Vulkan，自动检测硬件并推荐档位。
- 多路翻译按显存（GPU）或内存（CPU）动态生成 1..16 路，每档带显存估算。
- 八道译后闸门（占位符 / 换行 / 粗口 / 完整性 / 残留源文 / 长度比 / 回显等），收尾输出「提示词体检」遥测。
- 随包术语库 24455 条（13 分类）：内置只读层 + 用户覆盖层，含 canon/names 硬层、技术精确表与软提示层，2.0.25 起含「人工对照」层，2.0.26 起含「高频硬词」层（51 条高频专名/地名格变升入 canon）；另有会话词表与 TM-lite 精确复用。
- 场景批处理：连续短对话按上下文批量翻译，失败回退逐条；中文去翻译腔后处理。
- 完成账本 + 原子写盘：输入或输出 SHA-256 变化即重译；取消、异常、写盘失败与终检硬失败均不记为完成，生成 ID 定义表与正文按文件保持一致，失败时保留源文供复核。
- 模型与 llama.cpp 运行库不随包分发：自动测速选源、按官方 SHA-256 校验、断点续传、失败换源。
- 跟随宿主：小文本请求走 `api.host.http_*`、中小产物走 `api.host.http_download`、GB 级模型走自建流式管道；UI 回调走 `api.ui.post`，老宿主自动回退，不引入第三方 Python 依赖。

## 2.x 界面说明

插件只在工具集的**一个工具页内**再分页：`① 硬件 / ② 环境 / ③ 模型 / ④ 引擎 / ⑤ 翻译 / ⑥ 术语（可选）`，不额外注册宿主工具页。

![AI 翻译插件界面（翻译进行中）](docs/images/ai_translate_progress.png)

- ⑥ 只放附加提示词与强制术语，不影响前五步；`文件类型` 默认「auto 自动识别全部」。
- ⑤ 翻译页勾选只留真正需取舍的 5 项，每项下挂一行开/关后果说明：
  强制术语（开=词库命中强制回填译名，关=只作软提示）、保护专名/歌名/型号
  （开=整条跳过模型只做子串替换）、只译被引用条目（开=省请求，关=全量更保险）、
  翻译脚本/XML 字面文本（关=脚本里写死的文字不翻）、先预览（试运行不写盘）。
  引用层识别 / 完整补丁 / TM-lite / 会话词表 / 高频专名定译已收编为内部固定
  行为，不再提供开关（关闭只有坏处）。
- 底部进度条常驻：下载按字节推进，翻译按本轮处理文件数推进；旁边两行给出
  `阶段：翻译中 12/87 · configs\items\x.ltx` 与 `已翻译 12 / 87 个文件（14%），条目 345 条`。
  分母是自动识别出的整个 MOD 待翻译文件数（只算确实含可译文本的文件）。
- `查看日志（弹窗）`：日志不占页面高度；完整日志在
  `%LOCALAPPDATA%\STALKER Localization Toolkit\ai_translate\logs\translate_run.log`。

## 2.x 插件安装

把 `dist/ai_translate_plugin_v2.0.29.zip` 解压到工具集的 `plugins/` 目录：

```text
<工具集目录>\plugins\ai_translate\plugin.py
                          \stalker_translate_core\...
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

## 1.9.x 兼容插件

### 功能

- 适用于 **STALKER Localization Toolkit 1.1.0 Hub**，界面为旧版六步骤页。
- 使用同一共享 core 与 24455 条术语库，支持文本 XML、cfgxml、ltx、脚本、引用层、会话词表、TM-lite、占位符/数字/换行/上下文/语义闸门。
- 支持 1.8B/7B 本地模型、CPU/CUDA/Vulkan、动态多路、断点续译和完成账本。
- 1.9.19 起采用兼容式数据根：默认 `%LOCALAPPDATA%`；老用户插件目录已有模型/配置时保持原样，不强制重新下载。

### 安装

1. 解压 `dist/ai_translate_plugin_v1.9.26.zip`。
2. 将包内 `plugins/` 整个覆盖到工具集 1.1.0 目录（与 `STALKER Localization Toolkit 1.1.0.exe` 同级）。
3. 确认结构：

```text
<工具集目录>\plugins\
    ai_translate.py
    stalker_translate_core\
    ai_translate_assets\
        xray_pkg.zip
        stalker_termbase.json
        glossary_builtin.json
        THIRD_PARTY_NOTICES.txt
```

4. 重启工具集，在顶部打开「AI 翻译」。
5. 首次运行选择硬件、后端和模型；模型与 llama.cpp 运行库不随包分发，会按官方 SHA-256 自动下载校验。

## Hermes MCP

### 功能

- 面向 Hermes Agent 的 stdio MCP 插件，工具为 `check_env`、`translate_mod`、`translate_text`。
- 自动读取 Hermes 本地模型端点与 API Key；支持 1.8B/7B 模型和并发路数选择。
- 支持整 MOD 翻译、实时进度页、暂停/继续/停止、日志通知、断点续译、问题文件重译和终检。
- 共享 core 提供完整离线翻译作业、术语资产与 X-Ray 解析器；不包含过渡内核。

### 安装

1. 解压 `dist/stalker-translate-MCP-1.1.11.zip`。
2. Windows 双击 `install-plugin.cmd`；安装器会复制插件、启用工具、安装技能并尝试重启 Hermes。
3. 完全退出并重新打开 Hermes，确认工具列表出现 `check_env`、`translate_mod`、`translate_text`。
4. 不需要单独安装 Python 或模型，包内带便携 Python；模型端点由 Hermes 管理。

手动安装：把解压目录复制到 `<HermesHome>\plugins\stalker-translate`，在 Hermes `config.yaml` 的 `plugins.enabled` 中加入 `stalker-translate`，然后完全退出并重启 Hermes。
安装器会关闭当前 profile 的 `terminal/file/browser/code_execution/computer_use` 工具集；需要恢复时删除 `config.yaml` 中 `agent.disabled_toolsets` 对应项及 `SOUL.md` 的标记段。

## 通用在线 MCP / CLI

### 功能

- 适用于任何支持 **MCP stdio** 的客户端，也提供独立 CLI。
- 第一版支持 OpenAI 兼容 Chat Completions；不绑定具体云厂商。
- MCP 工具：`check_env`、`scan_project`、`translate_text`、`translate_project`、`job_status`、`job_cancel`、`validate_output`、`glossary_lookup`。
- CLI 命令：`check`、`scan`、`translate-text`、`translate`、`status`、`cancel`、`validate`、`glossary`、`serve`。
- 支持术语直取、SQLite 缓存、RPM/TPM 限流、token/费用预算、占位符/换行/中文/回显闸门、元数据审计、X-Ray XML 断点任务与输出校验。
- 密钥只从环境变量读取；文件访问受 `allowed_roots`、realpath、防覆写与大小/数量限制。

### 安装与配置

1. 解压 `dist/stalker-translate-online-MCP-0.2.2.zip`，要求 Python 3.11+。
2. 复制 `config.example.json` 为 `config.json`，填写：

```json
{
  "base_url": "http://127.0.0.1:8080/v1",
  "model": "your-model-name",
  "api_key_env": "STALKER_TRANSLATE_API_KEY",
  "allowed_roots": ["/absolute/path/to/mods"],
  "max_concurrency": 4,
  "rpm": 60,
  "tpm": 120000
}
```

将 `base_url`、`model` 和 `allowed_roots` 换成你的在线服务与本地 MOD 路径。

3. 设置密钥环境变量（不要写进配置文件）：

```powershell
$env:STALKER_TRANSLATE_API_KEY = "你的密钥"
python cli.py --config config.json check
python cli.py --config config.json scan "D:\mods\你的MOD"
python cli.py --config config.json translate "D:\mods\你的MOD"
```

4. MCP 客户端配置使用：

```json
{
  "mcpServers": {
    "stalker-translate-online": {
      "type": "stdio",
      "command": "python",
      "args": ["/absolute/path/to/server.py"],
      "env": {
        "STALKER_TRANSLATE_CONFIG": "/absolute/path/to/config.json",
        "STALKER_TRANSLATE_API_KEY": "你的密钥"
      }
    }
  }
}
```

## 通用翻译技能

### 功能

- 平台中立的技能说明与跨平台 Python 包装脚本。
- 平台支持 MCP 时直接调用在线 MCP 工具；不支持 MCP 时自动回退包内 CLI。
- 内置在线 MCP/CLI、共享 core 与术语资产，无需另行安装运行时。
- 提供检查、扫描、整目录翻译、单段翻译、校验、术语查询的标准工作流。

### 安装

1. 解压 `dist/stalker-translate-skill-0.2.2.zip`。
2. 将整个目录放入所用平台的技能目录（不同平台路径不同）。
3. 编辑包内 `runtime/config.example.json` 并保存为 `runtime/config.json`，配置 Provider、模型、`allowed_roots` 和密钥环境变量。
4. 直接使用技能，或运行：

```text
python scripts/stalker_translate.py --config runtime/config.json check
python scripts/stalker_translate.py --config runtime/config.json translate-text "Привет, сталкер."
python scripts/stalker_translate.py --config runtime/config.json translate "<MOD目录>"
```

脚本会依次查找 `STALKER_TRANSLATE_ONLINE_CLI`、已安装 CLI、技能包内置 runtime 和源码树 CLI。

## 接入平台插件管理（注册表 / 检查更新 / 一键安装）

平台插件管理器只认一份线上注册表
（`ALittleHuman/STALKER-Localization-Toolkit-2.x-Network-Plugin/main/plugins.json`）。
联网能力自 BETA.249 起已内置于平台，不需要额外的「联网插件」。
**本插件条目尚未写入该注册表**，因此仅有平台的用户在插件管理里搜不到；打通三步：

```powershell
# 1) 改版本号 + 重建 zip/清单 + 打印可直接粘贴的注册表条目
python tools/release_plugin.py 2.0.29
# 2) 把安装包传成 GitHub Release 资产（tag = v<版本号>）
gh release create v2.0.29 dist/ai_translate_plugin_v2.0.29_install.zip --title "ai_translate 2.0.29"
# 3) 把第 1 步打印的条目追加进注册表 plugins 数组（需要该仓库写权限）
```

之后用户流程：平台 → 插件管理 → 检查插件更新 → 选中 ai_translate →
「现在下载并安装」→ 重启工具集。字段含义与已知限制见
[`docs/PLUGIN_REGISTRY.md`](docs/PLUGIN_REGISTRY.md)。

## 仓库结构

| 路径 | 说明 |
|---|---|
| `core/stalker_translate_core/` | 共享翻译核心：领域对象、端口、术语、资产、流水线指纹 |
| `assets/` | 唯一资产源：术语库、内置 glossary、X-Ray 解析器、来源声明 |
| `plugins/ai_translate/plugin.py` | 2.x 宿主适配器；`stalker_translate_core/` 为构建时 vendored 镜像 |
| `plugins/ai_translate/assets/` | 2.x 发布兼容镜像，由根 `assets/` 自动同步 |
| `products/` | 1.9.x、Hermes MCP、通用在线 MCP 的产品入口与 manifest |
| `skills/stalker-translate/` | 通用技能入口与 manifest |
| `versions.json` / `compatibility.json` | 产品版本与兼容矩阵 |
| `dist/stalker-translate-skill-0.2.2.zip` | 通用在线翻译技能包（含跨平台脚本与内置运行时） |
| `dist/stalker-translate-skill-0.2.2.manifest.json` | 技能说明/包装脚本/运行时/资产哈希清单 |
| `dist/stalker-translate-online-MCP-0.2.2.zip` | 通用在线 MCP/CLI 独立包（OpenAI 兼容、stdio、共享全链） |
| `dist/stalker-translate-online-MCP-0.2.2.manifest.json` | 在线 MCP/CLI server/core/资产哈希清单 |
| `dist/stalker-translate-MCP-1.1.11.zip` | Hermes MCP 独立分享包（含便携 Python、core、资产、技能） |
| `dist/stalker-translate-MCP-1.1.11.manifest.json` | Hermes MCP server/core/资产哈希清单（无过渡管线） |
| `dist/ai_translate_plugin_v1.9.26.zip` | 1.9.x 兼容插件独立分享包 |
| `dist/ai_translate_plugin_v1.9.25.manifest.json` | 1.9.x 入口/core/资产/包哈希清单 |
| `dist/ai_translate_plugin_v2.0.29.zip` | 可直接分发的 2.x 插件包 |
| `dist/ai_translate_plugin_v2.0.29_install.zip` | 平台安装包（顶层 `ai_translate/`） |
| `RELEASE_MANIFEST.md` / `release_manifest.json` | 发布清单（版本、哈希、一致性核对） |
| `CHANGELOG.md` | 各版本完整修复明细与证据 |
| `corpus/` | 收割用双语语料与退役软层留档（不随发布包分发） |
| `audit-reports/` | 审计报告与测试原始输出（可复跑核对） |
| `tests/` | 契约 / 功能回归 / 各版本专项 / 分享包一致性测试，`fake_host.py` 为严格宿主假件 |
| `tools/build.py` | 统一构建入口；五个 active 产品分别生成独立分享包 |
| `tools/release.py` | 统一发布列车：构建、总清单、哈希校验、本地 tag |
| `release_train.json` / `RELEASE_TRAIN.md` | 五产品版本、资产 SHA-256、兼容矩阵与旧仓库切换说明 |
| `tools/check.py` | 结构、版本、入口、core/资产镜像哈希门禁 |
| `tools/test_all.py` | 共享 core + 当前 active 产品回归 |
| `tools/sync_core.py` / `tools/sync_assets.py` | 把根 core/assets 同步到产品 vendored 镜像 |
| `tools/gen_release_manifest.py` | 重建 2.x zip 与清单，可做漂移门禁 |
| `tools/release_plugin.py` | 发版助手：改版本号 → 重建包 → 打印注册表条目 |
| `tools/test_random20_per_mod.py` | 每 MOD 随机 N 文件（或 `all` 全量）判定链重放测试，只读 |
| `tools/audit_termbase_full.py` | 词库全量深度审计（结构/质量/译文/软层/硬层/性能六层） |
| `tools/audit_iwp_full.py` | 已翻译产物全量深度审计（判定链复用插件本体，切片×条目双粒度） |
| `tools/harvest_untranslated_terms.py` | 从未译/残留审计收割待补译专名候选（只出报告不写词库） |
| `docs/PLUGIN_REGISTRY.md` | 接入平台插件管理说明 |
| `THIRD_PARTY_NOTICES.md` | 第三方来源、许可状态与致谢 |
| `.github/workflows/ci.yml` | CI：全量测试 + 发布清单门禁 |

## 版本与校验

- 2.x 插件版本：`ai_translate 2.0.29`　宿主插件 API：`1`
- 2.x 入口 SHA-256：`1582cb62d9f8f3c6307cd005ba2034c27f9675187065b54795c2a0f9293db990`
- 2.x 发布 zip SHA-256：`7d6473f8554bdcd04350cb8574680b4acde71a53c2c551f4f756d00697c85f27`
- 2.x 平台安装包 SHA-256：`c67a58cc73a24f0073e601f9bc1a1b79a4e4ac8e48c1f5e802a9d4513e51dc92`
- 1.9.x 插件版本：`ai_translate 1.9.26`
- 1.9.x 入口 SHA-256：`2dd7226137259fca71761d801a3dd9f39ac9394b304a234c870fd19348b436be`
- 1.9.x 发布 zip SHA-256：`bf6759672717439c55e457d29e27b7c19633dc048597e69357a0b972338bbfaf`
- Hermes MCP 版本：`1.1.11`
- Hermes MCP server SHA-256：`46dd6ab6a7b3653dd139f7e2fac41644bd97749effde2a954c69a0cf242dbceb`
- Hermes MCP 发布 zip SHA-256：`38fd8ab16d25913338753cf188e466b1635b35f866d6412e7c66d57a0a76559e`
- 通用在线 MCP 版本：`0.2.2`
- 在线 MCP server SHA-256：`7231f74c7ce8db8ba2716f2b47d4de4e2507f3c5f0f29e7227c367c8d2ee3756`
- 在线 CLI SHA-256：`b5d47a4b8f872a3faee2c65f7636ed4e6e92736bb61806e24f586e7768687f66`
- 在线 MCP/CLI 发布 zip SHA-256：`a9b2a983fc6498d9bf8de68653971c3928128c80f13576f2be600445b8c20a22`
- 在线翻译技能版本：`0.2.2`
- 技能说明 SHA-256：`387396cc4456f1b1fa6e46eaeaee6aac7c8c4afe0aa6256a290ffdd49a985359`
- 技能包装脚本 SHA-256：`269a0aae0e35b231a401bcceb9e2d2f3ec8bc5931e47c1561ec50faef7dae90f`
- 技能发布 zip SHA-256：`e0576f805465971ff94da93973d7d058724c2d8ea0ec94ccd67052c1ebaaba48`
- 术语规模：两张 JSON 各 **24455** 条、13 个分类、条目数一致（分类明细见
  [`RELEASE_MANIFEST.md`](RELEASE_MANIFEST.md)）；canon 4327 / names 5182 /
  reference 14928。2.0.26 新增「高频硬词」51 条（地名格变补位 + 设施 + 固定
  物品/UI，挂 canon 层）；2.0.25 新增「人工对照」1932 条（人工翻译包按条目 id
  对照收割，挂 names 层）。
  2.0.22 起 `Gunslinger 参考`/`GAMMA 参考`（13946 条）退役，留档
  `corpus/soft_reference_retired.json`，不参与直取与提示。
- 下载链路官方哈希：llama.cpp `b11612` 的 6 个 Windows 包 + 2 个 HuggingFace 模型。

```powershell
Get-FileHash 'dist\ai_translate_plugin_v2.0.29.zip' -Algorithm SHA256
Get-FileHash 'dist\ai_translate_plugin_v1.9.26.zip' -Algorithm SHA256
Get-FileHash 'dist\stalker-translate-MCP-1.1.11.zip' -Algorithm SHA256
Get-FileHash 'dist\stalker-translate-online-MCP-0.2.2.zip' -Algorithm SHA256
Get-FileHash 'dist\stalker-translate-skill-0.2.2.zip' -Algorithm SHA256
```

完整核对结果见 [`RELEASE_MANIFEST.md`](RELEASE_MANIFEST.md)。

## 开发与测试

```powershell
python tests/test_core_contracts.py       # 共享 core 契约
python tests/test_monorepo_layout.py      # 结构、版本、镜像哈希
python tools/check.py                     # Monorepo 总门禁
python tools/test_all.py                  # core + 当前 active 产品回归
python tests/test_1_9x_core_adapter.py    # 1.9.x 适配器与独立包
python tests/test_mcp_hermes_core_adapter.py # Hermes MCP 共享 core + stdio 全流程
python tests/test_online_service.py        # 在线 Provider/缓存/预算/质量
python tests/test_online_jobs.py           # 在线项目任务/断点/校验
python tests/test_online_mcp_cli.py        # 在线 MCP stdio + CLI + 独立包
python tests/test_skill.py                 # 通用技能包 + 跨平台包装脚本
python tests/test_release_train.py         # 五产品发布列车清单与哈希
python tools/build.py --product plugin_2x # 同步 core/assets 并重建 2.x 包
python tools/build.py --product plugin_1_9x   # 重建 1.9.x 独立包
python tools/build.py --product mcp_hermes    # 重建 Hermes MCP 独立包
python tools/build.py --product mcp_online    # 重建通用在线 MCP/CLI 独立包
python tools/build.py --product skill         # 重建通用翻译技能包
python tools/build.py --product all --check   # 校验全部 active 产品
python tools/release.py --check               # 校验五产品发布列车
python tools/test_random20_per_mod.py "" 0 "" all   # 全 MOD 全文件判定链重放
```

当前全量：30 个测试文件全绿（共享 core 12、Monorepo 结构 9、1.9.x 适配器 2、
Hermes MCP 适配器 1、在线服务 6、在线项目任务 2、在线 MCP/CLI 全流程 1、
技能包 1、发布列车 3、契约 67、功能回归 358、动态多路 42、提示词规则 46、
页签与宿主门面 34、分享包 30、2.0.28 交付闸门 60、2.0.24 专项 27、
2.0.25 专项 24，及各版本专项）；五个 active 产品构建门禁全部通过，2.x 发布一致性 25/25。
1.9.x 另用原仓库 10 个功能测试文件在新包临时仓库中全量回归通过。
2.0.28 新测试使用真实 X-Ray 解析器与临时文件，只在模型/宿主边界 programmable fake；
覆盖零变化输出、旧账本迁移、取消与写盘失败、生成 ID 事务、终检假绿、token 序列、
提示词串入、多语言字段、术语精确直取、同尺寸输出篡改、损坏定义表恢复和并发互斥。
测试统一 `sys.dont_write_bytecode = True`，不会把 `.pyc` 写进随包目录。

## 许可

本仓库自身代码按 **GPL-3.0** 分发，见 [`LICENSE`](LICENSE)。
第三方来源与许可状态见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)：
X-Ray 解析器参考 wzyddg/stalker_auto-trans-tool_remake（上游未声明许可证，版权归原作者）；
llama.cpp（MIT）与 Hy-MT 模型（Apache-2.0）均不随仓库分发；
术语库条目收割自 NS 汉化补丁（权威源）、GAMMA 汉化组、Gunslinger 人工汉化、
CoP 官方中文、ANTHOLOGY 汉化整合包与 bilibili wiki（均未声明开源许可证，只保留署名）。
该 GPL-3.0 许可不改变上述第三方组件的版权与授权状态。
