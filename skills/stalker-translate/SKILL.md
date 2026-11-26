---
name: stalker-translate
version: 0.3.1
description: "翻译 STALKER / X-Ray MOD 或游戏文本。技能=宇宙正字法+操作手册：术语核心表(GLOSSARY-CORE)统一专名，plan/extract/merge/validate 四个薄命令负责本地扫描、直取回填、闸门与写回 XML；对话 AI 只专注逐句写译文。"
license: GPL-3.0
---

# STALKER MOD 翻译技能（知识 + 三个薄命令）

## 心智模型（先读这段）

翻译一件 MOD = 三类文本：**能定的**（词库整句直取、术语、噪声）已经在本地处理完，
**要译的**（人类语言的句子）才轮到你。所有文件读写、编码、占位符、格式都由
本地脚本负责——**你永远不要手工编辑 XML/脚本文件，也不要把整张词库读进对话**。

```
plan(报价) → 模式判定 → [extract → 你写译文 → merge] 循环 → validate
```

## 标准流程

技能脚本定位顺序：环境变量 `STALKER_TRANSLATE_SKILL_DIR` → 已安装 CLI →
技能内置 `runtime/cli.py`。命令形态（`--config` 用用户配置，缓存/批次文件都在
cache 目录，全程幂等可断）：

```text
python scripts/stalker_translate.py --config <配置> plan    <MOD目录>
python scripts/stalker_translate.py --config <配置> extract <MOD目录> [--chunk 200 --part 1]
python scripts/stalker_translate.py --config <配置> merge   <MOD目录> <答案.tsv>
python scripts/stalker_translate.py --config <配置> validate <MOD目录> <输出目录>
```

### 第 0 步：读 `GLOSSARY-CORE.md`（本目录，约 650 条 → 一次读完）

这是 STALKER 宇宙的正字法：地名、阵营、怪物、实验室、高频硬词的唯一写法。
**你的译文里凡出现表中词条及其变体，一律改用定译。** 库里没有的专名，merge
的术语回填闸门会按整库兜底——写错也不会漏网，但尽量遵守以省返工。

### 第 1 步：plan（必做，不许跳过）

输出 `mode` 三选一：

- `dialog`：unique ≤ 3000 句 → 继续下面的循环；
- `engine_recommended`：3000 < unique ≤ 20000 → **停下报告用户**，建议改用
  「通用在线 MCP/CLI」独立包配 API 或本地引擎跑主体（`translate_project`），
  得到同意前最多先做第一批示范；
- `engine_required`：unique > 20000 → **禁止**按对话逐句硬翻（积分会到万级）。
  把报价交还给用户决策，本技能只负责之后的收尾精修。

### 第 2 步：extract 领批次

只回一个 `batch_file` 路径。用文件工具读它（每行：`sid<TAB>源文<TAB>术语提示`；
`sid#i` 表示成组对话第 i 行，`%s` `$$Rn$$` `$player` `<br>` 等令牌是
**占位符，必须逐字出现在译文**，`\n` 是换行占位，保持原样）。

### 第 3 步：写答案 TSV

新建文件，每行 `sid<TAB>译文`（组句 `sid#i<TAB>第i行译文`）。纪律：

- 一次一批（默认 200 行）；术语提示列优先采用；
- 译文简洁口语化，阵营/地名/怪物名以 GLOSSARY-CORE 为准；
- **不要**在对话里复述源文或译文清单；写完直接进 merge。

### 第 4 步：merge 交回

自动完成：闸门校验（占位符/换行/数字/粘连）→ 术语回填 → 写回镜像目录。
返回 `unique_remaining`；>0 就回第 2 步领下一批（已处理的自动消失，重复运行
零成本），直到 0 再 `validate`，`ok=true` 才能报告完成。

### 单段文本

直接翻译：术语命中先用 `glossary <词>` 查询（零成本）；无需 plan/extract。

## 硬性规则

- 答案只能来自当前平台的对话 AI。**禁止**把句子转交本地小模型端点
  （llama.cpp/ollama 1~8B）代答——那是绕过闸门的低质量流水线。
- 本技能**不做 API 编排**：用户要自动化批量，转介「通用在线 MCP/CLI」
  独立包（translate_project 连 OpenAI 兼容端点或本地引擎），不在技能内配置密钥。
- 永不修改输入目录原文件；产出一律在 `<输出目录>`（默认 MOD 旁
  `translated_online_mcp/`，plan 会告知）。
- sid 由脚本生成，**不要自造或改写 sid**；答案文件里出现未知 sid 会被忽略并计数。
- 每一轮 merge 后只报告数字（批次/剩余/拒收），不粘贴译文清单。
- 超出用户声明预算、或 plan 给出非 dialog 模式仍被要求继续时，先停下确认。
- 只有 `validate ok=true` 才能报告完成。

## 完成汇报（紧凑）

```text
状态：succeeded / needs_review / engine_required / …
输出目录：<path>
句数：直取+译出 / 剩余 / 拒收待复核
积分账：本会话实际对话轮次（不申报平台单价，由用户对照）
```

## 命令与工具的边界备忘

- `extract` 批次文件命名 `<cache>/kit/batch_NNN.tsv`；
- 同句在多个场景（提示/上下文差异）已由脚本折叠：一句只出现一次；
- merge 的填充遍会重新跑一遍判定链（直取/缓存/账本生效），这是幂等的。
