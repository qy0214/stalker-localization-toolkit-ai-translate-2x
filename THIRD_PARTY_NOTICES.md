# 第三方组件与来源声明

本仓库包含或参考了以下第三方成果，各组件的版权归其原作者所有。

## 1. wzyddg/stalker_auto-trans-tool_remake — X-Ray 解析器与翻译流程

- 仓库：<https://github.com/wzyddg/stalker_auto-trans-tool_remake>
- 作者：wzyddg
- 许可证：**上游仓库未声明开源许可证**（2026-10-29 通过 GitHub API 检查为
  `license: null`）。相关代码的版权归原作者；本仓库仅保留来源与作者信息，
  不对上游代码主张任何额外授权。
- 本仓库的引用方式：

  | 本仓库 `plugins/ai_translate/assets/xray_pkg.zip` 内的文件 | 相对上游 |
  |---|---|
  | `xRayXmlParser/__init__.py` | 原样收录 |
  | `xRayXmlParser/entityDefinition.py` | 原样收录 |
  | `xRayXmlParser/xmlFileGenerator.py` | 原样收录 |
  | `xRayXmlParser/xmlxReader.py` | 已修改：改用标准库 XML 解析、加入编码候选链与健壮性修复 |
  | `xRayXmlParser/xRayTextAnalyzer.py` | 已修改：正则转义修正、俄文字母表修正、文本字段兼容处理；cfgxml 标签正则的灾难性回溯已修复（`ui_faction_patches.xml` 单文件 58 秒 → 2 毫秒） |

- `plugins/ai_translate/plugin.py` 的翻译流水线（文本 XML / cfgxml / ltx / script 的
  "提取—翻译—回填"思路）参考了上游 `xRayTrans.py`，但为独立实现：本地
  llama.cpp 推理、完成账本、原子写盘、术语分层、下载校验与断点续传等均由
  本项目实现。

> 如果你是上述代码的权利人，希望调整署名方式或移除相关代码，请在本仓库
> 开 issue 说明，我们会配合处理。

## 2. llama.cpp

- 仓库：<https://github.com/ggml-org/llama.cpp>
- 许可证：MIT
- 使用方式：不随本仓库分发，插件在用户机器上按官方 SHA-256 校验后下载
  （当前固定构建 `b11612`）。

## 3. Hy-MT 模型

- 模型：Hy-MT2-7B / Hy-MT2-1.8B（腾讯混元）
- 许可证：Apache-2.0
- 使用方式：不随本仓库分发，插件在用户机器上按 HuggingFace LFS oid 校验后下载。

## 4. 人工汉化语料与术语库来源

随包术语库（`plugins/ai_translate/assets/stalker_termbase.json` 与
`glossary_builtin.json`）的条目收割自以下第三方文本。这些来源**均未声明
开源许可证**，版权均归各自权利人/汉化组；本仓库只保留来源署名，不主张
任何额外授权，也不对它们的译文质量负责。

| 来源 | 仓库内位置 | 用途与状态 |
|---|---|---|
| NS OGSR 系列汉化补丁（2019 / 2026-02-19 / 2026-06-19） | 本机 MOD 目录（未收录原文） | **权威源**，条目并入 canon/names 等标准分类 |
| GAMMA 汉化组（GSCTP）Anomaly 简体中文 | `corpus/gamma_chs/` | 曾为 `GAMMA 参考` 软层 7998 条；**2.0.22 整体退役** |
| OGSR Gunslinger Addon 人工汉化（原文 `configs.db`，中文在 eng 槽） | `corpus/gunslinger_eng/`、`corpus/gunslinger_rus/` | 曾为 `Gunslinger 参考` 软层 5948 条；**2.0.22 整体退役** |
| 《S.T.A.L.K.E.R.：普里皮亚季的呼唤》官方简体中文（主机版） | `corpus/cop_chs/`、`corpus/cop_eng/`、`corpus/cop_rus/` | 1432 条并入标准分类；仅作对齐核验参考 |
| Anomaly 英文原文 | `corpus/anomaly_eng/` | 源文对齐用，非译文来源 |
| ANTHOLOGY 2.1 汉化整合包 | 本机 MOD 目录（未收录原文） | `ANTHOLOGY 参考` 148 条（软提示层） |
| bilibili 《潜行者》中文 wiki 专有名词/神器表 | 经 `tools/ingest_wiki_terms.py` 收割 | 24 条并入标准分类 |

退役的两层共 13946 条留档在
[`corpus/soft_reference_retired.json`](corpus/soft_reference_retired.json)，
其 `_meta.reason` 记录了退役依据（逐条语义抽检明确错率约 31%）与出处；
该文件不参与运行时，仅作追溯与将来重新收割的原料。

`corpus/` 下收录的是上述来源的完整文本副本，仅用于本地对齐收割与核验，
**不随插件发布包分发**（发布范围见 `release_manifest.json` 的 `scope`）。

> 如果你是上述任一来源的权利人，希望调整署名方式或要求移除相关数据，
> 请在本仓库开 issue 说明，我们会配合处理。

## 5. 本仓库自身许可

本仓库自身代码按 **GPL-3.0** 分发（见 [`LICENSE`](LICENSE)）。
该许可不改变上述第三方组件的版权与授权状态；引用上游未声明许可证的代码时，
请同时遵守上游仓库的要求。
