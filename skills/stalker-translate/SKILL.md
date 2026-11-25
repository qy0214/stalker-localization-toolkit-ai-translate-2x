---
name: stalker-translate
version: 0.1.1
description: "翻译 STALKER / X-Ray MOD 或单段游戏文本。优先调用通用在线 MCP；平台不支持 MCP 时使用跨平台 CLI 包装脚本。支持术语库、缓存、预算、占位符/换行校验与断点续译。"
license: GPL-3.0
---

# STALKER 在线翻译

## 何时使用

- 用户给出 STALKER / X-Ray MOD 目录，要求汉化或翻译。
- 用户贴来一段游戏文本，要求译成中文或其他目标语言。
- 普通文本不要使用本技能。

## 运行方式

按平台能力选择，不要同时启动两套：

1. **MCP 可用时**：调用 `stalker-translate-online` 工具。
2. **MCP 不可用时**：调用本技能脚本：

```text
python scripts/stalker_translate.py --config <配置文件> <命令> ...
```

脚本会自动查找：

- `STALKER_TRANSLATE_ONLINE_CLI` 指定的 CLI；
- 已安装的 `stalker-translate-online` 命令；
- 技能包内置 `runtime/cli.py`；
- 源码树中的 `products/mcp_online/cli.py`。

API Key 只通过环境变量读取，禁止写入命令参数、技能文件或配置文件。

## 标准流程

### 整目录翻译

1. `check_env`：确认 Provider、模型与资产可用。
2. `scan_project`：只读统计文件、条目、字符、预估 token/费用。
3. 向用户确认输出目录与预算；不要自行扩大 `allowed_roots`。
4. `translate_project`：异步启动，获得 `job_id`。
5. `job_status`：轮询进度；用户要求停止时调用 `job_cancel`。
6. 只有 `state=succeeded` 才能报告“翻译完成”；`needs_review` 必须报告待复核。
7. `validate_output`：核对 ID、占位符、换行和中文产出。

CLI 等价流程：

```text
python scripts/stalker_translate.py check
python scripts/stalker_translate.py scan <MOD目录>
python scripts/stalker_translate.py translate <MOD目录> --output-dir <输出目录>
python scripts/stalker_translate.py validate <MOD目录> <输出目录>
```

### 单段文本

```text
python scripts/stalker_translate.py translate-text "<待译文本>" --source-lang auto --target-lang chs
```

返回 `retained_source=true` 或 `quality_issues` 非空时，不要当作成功译文使用。

### 术语查询

```text
python scripts/stalker_translate.py glossary "Pripyat"
```

## 硬性规则

- 不要用终端或文件工具逐文件扫描 MOD；`scan_project`/`translate_project` 会自行识别。
- 不要要求用户上传整个 MOD；使用用户授权的本地路径。
- 不要猜测路径，不要绕过 `allowed_roots`。
- 不要启动、下载或切换在线模型；Provider 由用户配置。
- 不要把密钥、正文或完整译文写入日志与回复；日志只保留元数据。
- 输出目录不得等于输入目录或成为其祖先。
- 只有 `succeeded` 表示通过；`needs_review`、`failed`、`cancelled` 都必须明确报告。

## 完成汇报

只返回紧凑摘要：

```text
状态：succeeded / needs_review / failed / cancelled
输出目录：<path>
文件/条目：<counts>
token/费用：<usage>
待复核问题：<issue codes>
```

不要把整份译文回灌到对话上下文。
