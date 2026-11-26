# 2026-11-26 GitHub 生态强化后续迭代计划

## 状态与范围

- 状态：**仅归档，未排入当前修复列车，不修改现有代码。**
- 调研截至：2026-11-26；覆盖 60+ 候选，深读约 40 个仓库/规范。
- 当前优先级：先完成 Hermes 1.1.11 真实客户端回归与发布验证，再启动本计划。
- 原则：不复制 AGPL/GPL/无许可证/Source-Available 项目代码；只借鉴设计并独立实现。

## P0：互操作、安全与分发基础

1. MCP 工具补充 ToolAnnotations；支持结构化输出；协商新协议版本并保留旧客户端兼容。
2. 修正在线 MCP `notifications/cancelled` 的 requestId→job_id 映射，增加请求幂等。
3. `merge_answers` 的答案文件纳入 allowed_roots、realpath、大小和敏感路径校验。
4. 修正 PyPI 包发现范围，包含共享 core、资产、许可证与第三方声明；增加 server.json。
5. 修正技能 frontmatter 中 `stats/extract/merge` 与实际 `plan/extract/merge/validate` 不一致。

验收：标准客户端取消/进度/结构化输出通过；任意文件读取被拒绝；干净 venv 可安装并完成 stdio 冒烟。

## P1：增量正确性与人工工作流

1. 指纹扩展为 source + target_lang + model + prompt + glossary + core/pipeline + QA policy。
2. 术语或提示词变化只重译受影响条目；人工修改标记 user_edited，默认禁止自动覆盖。
3. 增加 provenance、source-bound apply、单写入口、实际变化备份和备份轮转。
4. 对高风险条目增加独立审校；改写后重新经过全部确定性闸门。
5. 技能增加 preview/review/polish；批次回执区分 applied/rejected/pending/skipped/stale/user-protected。

验收：改一个术语不触发整文件重译；人工修正 100% 保留；审校不得引入新的硬错误。

## P1/P2：质量评测与工程接口

1. 建立 300–500 条真实 MOD 金标集，统计精确率、召回率、误报、token、成本和人工修正存活率。
2. QA 输出 SARIF 2.1.0 与 JUnit，接入 CI/Code Scanning。
3. 在现有元数据审计上增加 OpenTelemetry；禁止记录密钥、完整源文和完整译文。
4. 增加 TMX/CSV 导入导出，保留来源、上下文、术语和 provenance。

验收：金标回归可作为发布门禁；报告映射真实文件与 source_id；外部遥测默认关闭。

## P2：通用格式适配层

- 统一 detect→parse→extract→render→validate 适配器接口。
- 顺序：PO/XLIFF → SRT/ASS → JSON/VDF → Ren'Py → Unreal locres。
- 每种格式提供真实 fixture、roundtrip、模糊测试、ID 覆盖和占位符保真。
- 不把 Unity/Unreal 全资源注入或 OpenXRay 引擎带入默认共享 core。

## P3：按需增强

- 本地 embedding 语义召回、VLM 截图消歧、字体覆盖与溢出预检。
- vLLM/LMCache 仅作为可选推理后端，先 benchmark。
- MCP Tasks、ACP 编辑器适配、远程 MCP + OAuth 仅在客户端/需求成熟后实施。

## 明确不采纳

- 重型 GUI、多媒体翻译和全格式一次性支持。
- 默认多 Agent 并行；开启时也必须只读分析、主控唯一写回。
- 外部文本遥测或以 BLEU 代替真实 LQA。
- 直接复用 AGPL/GPL/无许可证/Source-Available 代码。

## 主要参考

- MCP：https://github.com/modelcontextprotocol/modelcontextprotocol
- MCP Registry：https://github.com/modelcontextprotocol/registry
- Agent Skills：https://github.com/agentskills/agentskills
- translate-book：https://github.com/deusyu/translate-book
- 玄骥技能：https://github.com/xuanji86/ainiee-translate-skill
- YN Translation Workshop：https://github.com/TohmaN233/YN-translation-workshop
- Lara MCP：https://github.com/translated/lara-mcp
- Languine：https://github.com/languine-ai/languine
- J-GameLQA：https://github.com/liguozheng-ac/J-gamelqa
- OpenTelemetry GenAI：https://github.com/open-telemetry/semantic-conventions-genai
