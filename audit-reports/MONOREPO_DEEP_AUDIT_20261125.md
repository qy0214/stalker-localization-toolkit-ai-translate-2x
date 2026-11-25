# 五合一归库项目深度审计报告

- 审计时间：2026-11-25 18:11 起
- 仓库：`C:/STALKER-AiTranslate-2x`，分支 `monorepo-migration`，HEAD `f44b561`
- 范围：共享 core 1.0.0 + 五产品（2.x 2.0.28、1.9.x 1.9.25、Hermes MCP 1.1.10、在线 MCP/CLI 0.1.0、技能 0.1.0）+ 资产/词库 + 发布工程 + CI + 文档
- 严重度：P0 发布阻断/正确性缺陷；P1 高风险；P2 中风险；P3 改进项
- 证据原则：项目自带测试只作**第一层基线**；核心结论以独立静态检查、模糊测试、干净构建、上游真相核验为准

## 1. 执行摘要

项目自带 30 个测试文件全绿、`tools/check.py` / `release.py --check` / `verify_manifests.py` 全 OK，但**独立审计层发现自带测试未覆盖的两个真实运行时缺陷**：

1. `xray.py` 常量名不一致导致非 UTF-8 文本乱码检测直接 `NameError`（由外部模糊测试发现）；
2. `project.py` 增量重跑路径调用未定义的 `_files_equal`（由独立 AST/静态审查发现）。

此外，`release.py --check` 会改写发布列车文件，使“校验”变成有副作用操作；CI 仅覆盖 11/30 测试文件；README 发布哈希与当前包不一致。外部工具补充确认：畸形 XML 被静默跳过却报“全部完成”（P1-7）、同输出目录并发锁抛未处理 `PermissionError`（P1-8）、规范化 core 分支覆盖率仅 27%（P2-7）。这些均属于发布前必修项。

正面证据：隔离目录干净重建的五包 SHA-256 与 `dist/` **完全一致**；五个 zip 均无绝对路径、`..`、重复条目、`__pycache__`；llama.cpp b11612 的六个官方哈希与 `RELEASE_MANIFEST.md` 登记一致。

## 2. 审计矩阵

| 层 | 手段 | 结果 |
|---|---|---|
| L1 项目自证 | 30 个测试文件、test_all、check、release/verify 门禁 | 全绿，但存在盲区（见 P1-1） |
| L1 工具回放 | 31 个 tools/ 脚本盘点与只读审计 | 词库/回放基线一致，详见附录命令 |
| L2 独立静态 | ruff / bandit / pylint（腾讯云镜像安装，隔离 venv） | ruff 878 项（16 F821）；pylint 独立复现 P0-1；bandit 10 High/26 Medium/207 Low |
| L2 模糊/属性 | Hypothesis 属性测试（150 例/属性） | 复现 P0-1；发现控制字符产生非法 XML（P2-8） |
| L2 对抗 | 4 包 × 9 场景 + 6 并发 + 6 差分 | 发现畸形 XML 静默跳过（P1-7）、并发锁未处理（P1-8）；预期失败场景行为正确 |
| L2 覆盖率/变异 | coverage 分支覆盖率 + 自建定向变异 | 规范 core 27%（P2-7）；变异 5/6 杀死（P2-9） |
| L2 干净构建 | tmp 隔离源码副本重建五包并比对 | 6 个哈希逐字节一致 |
| L2 包卫生 | Python zipfile 双工具交叉检查 | 0 路径穿越/重复/pyc |
| L2 上游真相 | GitHub API 核验 llama.cpp b11612 官方 digest | 6/6 一致；HuggingFace API 当前网络不可达 |
| L2 准则映射 | OWASP ASVS 5.0 / NIST SSDF / SLSA | 差距见第 4 节 |

## 3. 发现清单

### P0

**P0-1 共享 core 乱码检测 `NameError`（cp1251 路径可失败）**  
`core/stalker_translate_core/xray.py:352` 定义 `MOJIBAKE_CHARS`，`:380` 却使用 `_MOJIBAKE_CHARS`。`looks_like_mojibake()` 在 `project.py:773`、`:1091` 两处被调用且无异常保护；实测 `NameError: name '_MOJIBAKE_CHARS' is not defined`。影响 2.x/Hermes/在线 MCP 的共享 core 链路；1.9.x 自带实现不受影响。项目 358 项回归未覆盖。  
修复：`xray.py:352` 统一为 `_MOJIBAKE_CHARS`，补非 UTF-8 文本回归。

**P0-2 增量重跑路径 `_files_equal` 未定义**  
`core/stalker_translate_core/project.py:39` 导入的是 `files_equal`，但 `:496`、`:517` 调用 `_files_equal`。独立 AST 扫描全仓仅此一处未定义调用。影响跳过已译文件时的增量重跑。  
修复：统一为 `files_equal`，补两条调用路径回归。

**P0-3 `release.py --check` 有写副作用**  
`tools/release.py:179–182` 在 `if args.check` 前无条件 `write_outputs(data)`，随后才比对，校验恒真且污染工作区。实测 mtime 被更新，1.9.25 登记哈希被改写。  
修复：`--check` 走纯只读分支，仅 `--build` 写文件。

### P1

**P1-1 CI 只覆盖 11/30 测试文件**  
`.github/workflows/ci.yml` 未运行 19 个脚本，包括 `test_dynamic_parallel.py`（42 项）、`test_2028_delivery_gates.py`（60 项）、`test_2022_soft_retire.py`、`test_2025_chs_fixes.py`、`test_prompt_rules.py`、`test_ui_fallback.py` 及 2012–2021 全部回归；刚修的 T5-3 就在盲区。  
修复：CI 增加 `python tools/test_all.py` 或列全 30 个脚本。

**P1-2 README 发布哈希陈旧**  
`README.md:345–349` 仍写 2.x `b0cdc144…`/`704e91ff…`、1.9.x `e37bba1a…`，当前为 `7f200b02…`/`901a6471…`/`57fca97f…`。README 中 1.9.x 清单 schema 描述也不匹配实际 manifest 字段。  
修复：从 `release_train.json` 自动生成或加一致性门禁。

**P1-3 工作区未提交**  
22 个已跟踪修改 + 45 个未跟踪文件；`suite-v2026.11.25` tag 未创建，`release.py --tag` 的 clean-worktree 门槛必然失败。需先提交。

**P1-4 上游无许可证代码随包分发**  
`THIRD_PARTY_NOTICES.md:9–11` 明确 `wzyddg/stalker_auto-trans-tool_remake` 上游 `license: null`，但 `assets/xray_pkg.zip` 原样收录 3 个文件、修改收录 2 个，并随五个分享包分发。虽已署名，但无授权依据，分发存在法律与合规风险。  
建议：发布前取得作者授权、替换为自研实现，或至少让最终用户显式确认；CI 增加许可证门禁。

**P1-5 CI action 未 SHA pin、路径沙箱不完整**  
`.github/workflows/ci.yml:18/20` 使用浮动 `actions/checkout@v4`、`actions/setup-python@v5`；按 SLSA v1.0 应 pin 到完整 commit SHA。另 `products/mcp_online/online_mcp/server.py` 的 `allowed_roots` 只校验路径前缀，符号链接可越过边界（未发现别的调用点，风险限于显式配置路径）。  
建议：SHA pin CI action；路径校验统一 `Path.resolve()` 后判断 containment。

**P1-6 `build.py --check` 同样有写副作用**  
`tools/build.py:42–43,53–65` 在 `--check` 模式仍调用 `sync_core.py --write` / `sync_assets.py --write`；`sync_assets.py:37` 的 `--check` 也会 `mkdir`。与 P0-3 合并构成“伪只读门禁”问题：验证动作会改工作区。  
建议：统一改为纯检查分支，写操作只在 `--build`。

**P1-7 畸形源 XML 被静默跳过却报告“全部完成”**  
对抗测试（`bare_amp`、`control_in`，四包均复现）中，解析器报 `undefined entity` / `not well-formed` 后，日志显示“跳过 1 个不含 `<string>` 文本条目的 XML”，终检 `hard_problems=0`，最后输出“全部完成：请求 0 段”。即源文件解析失败没有升级为失败或未译告警，用户可能以为已翻译。  
建议：解析失败必须计入 `unparseable_tables` 或等价硬/软问题，禁止以 0 请求报成功。

**P1-8 同输出目录并发时锁文件 `PermissionError` 未处理**  
`run_conc.py same_out`：两个作业写同一 `out_dir` 与账本时，第二个线程在 `job.py:547` 的 `open(lock_path, "a+b")` 抛 `PermissionError: [Errno 13]`，以未捕获异常退出；第一个作业成功。Windows 上文件锁没有重试/退避，也没有友好错误。  
建议：对锁文件打开加重试与明确错误提示；或用目录级互斥。

### P2

**P2-1 异常吞掉**  
core 有 13 处 `except: pass`；其中 `job.py:109` 在 `_revoke_dangling_generated_refs` 吞掉 XML 解析失败，可能误判悬空 ID 并撤销合法账本。建议解析失败记入审计统计，按“未知不撤销”处理。

**P2-2 外部工具实测结果与 CI 缺口**  
通过腾讯云 PyPI 镜像在隔离 venv 装齐 `ruff 0.17.6`/`bandit 1.9.4`/`pylint 4.1.4`/`coverage 7.17.5`/`hypothesis 6.170.17`/`pip-audit 2.10.1`/`mutmut 3.8.1`。结果：ruff 878 项（其中 F821 16 项 = 两个已知 NameError ×5 镜像 + 1 处死代码 `tgt_path_local if False` 假阳性；S110 123 处静默 except；B023 53 处闭包 Late-binding 风险）；bandit 10 High（SHA1 缓存指纹 ×5 镜像，非安全用途）/26 Medium/207 Low；pylint 独立复现 `E0602: Undefined variable '_MOJIBAKE_CHARS'`；pip-audit：**No known vulnerabilities**（运行时零依赖）。旧 `audit-reports/code_review_report.md` 将正则误报为 SQL 注入，仍不能作为质量证据。建议把 ruff/bandit/pip-audit 纳入 CI 并废弃旧报告。

**P2-3 Python 版本矩阵不足**  
`compatibility.json` 声明 core `>=3.11`，CI 仅 3.12；本机 3.13.14。建议 3.11/3.12/3.13 矩阵至少覆盖 core 契约与 layout 门禁。

**P2-4 `__pycache__` 污染导致包卫生假失败**  
`tests/test_share_package.py` 的 A6 会检查随包目录是否含运行产物。导入插件生成 `.pyc` 后门禁误报，且 `test_random20_per_mod.py`、`test_2022_soft_retire.py` 会 import 插件并让这些 pyc 留在随包目录。CI 有 `PYTHONDONTWRITEBYTECODE=1`，但本地/代理运行没有；审计中需显式设置。  

**P2-5 存量译文质量问题未设闸**  
`verify_iwp_output.py` 实测 9320 条中 153 条译文无中文；`audit_iwp_full.py` 对 IWP 36138 条统计 320 条未译、漏译率约 8.4%。属于数据质量而非代码缺陷，但尚未进入发布门禁。  

**P2-6 审计工具自身工程化不足**  
`tools/replay_filters_all_mods.py` 与 `tools/verify_iwp_output.py` 硬编码桌面绝对路径；`tools/audit_termbase_full.py` 的 copy_sync 维度在两仓四副本不全时误报。建议路径参数化、副本缺失视为“不适用”而非 high。

**P2-7 规范化 core 的直接测试覆盖率极低**  
`coverage run --branch` 逐进程运行 30 个测试：根 `core/stalker_translate_core` 总覆盖 **27%**，其中 `job.py` 4%、`project.py` 3%、`single_text.py` 3%、`table.py` 2%；插件 vendored 镜像也只有 34%。这解释了 P0-1/P0-2 为何能在“全绿”中存活：测试主要跑插件内的旧副本，规范源缺少直接门禁。

**P2-8 Hypothesis 属性测试发现的 XML 控制字符缺陷**  
独立属性测试（150 例）在最小反例 `text='\x1f'` 上使 `patch_table_text` 产出无法被 `ElementTree` 解析的 XML；同时以 `text='AAAAAAAA'` 稳定复现 P0-1 的 `NameError`。两项均已由外部工具独立确认，不属于项目自带测试范畴。

**P2-9 变异测试显示测试有效性不均**  
自建定向变异 6 例：5 例被最小属性测试杀死（`min(parallel_cap→4`、乱码阈值、token 顺序、换行转义、files_equal），1 例（删除 `failed_strings += 1`）在最小集存活；但项目自带 `test_2028_delivery_gates.py` 能杀死该变异。说明自带测试对交付闸门有效，但对共享 core 的覆盖不足（见 P2-7）。mutmut 3.8.1 因 Windows 不支持原生运行，改用等价自建变异脚本。

### P3

- `ci.yml:56` 步骤名 `Regression suite (346)` 与实际 358 项不符。
- 技能包 `SKILL.md` 把自身版本写成 `0.1.2`，与 `versions.json` 的 `0.1.0` 不符（`skills/stalker-translate/SKILL.md:3`）。
- 干净包独立运行：Hermes `server.py` 用 `--config` 指向包外路径时缺文件抛 `FileNotFoundError` 且无友好提示；在线 CLI 缺 `--url` 时同样裸异常（`server.py:290` / `cli.py:98`）。
- 词库已知遗留：`canon_sentence` 17 条分类归属、无出处音译未系统扫描；制作名单整表保护属产品决策。

## 4. 权威准则映射

| 准则 | 来源 | 当前状态与差距 |
|---|---|---|
| OWASP ASVS 5.0（v5.0.0-1.2.5 OS 命令注入、文件访问校验） | https://owasp.org/www-project-application-security-verification-standard | 运行时零第三方依赖、无 shell=True；但缺独立静态安全扫描（P2-2） |
| OWASP Top 10 / Proactive Controls | https://owasp.org/www-project-top-ten | 路径穿越检查通过（P2 包卫生），但异常吞掉影响可审计性 |
| NIST SP 800-218 SSDF | https://csrc.nist.gov/pubs/sp/800/218/final | 有可复现构建与哈希登记；缺 CI 完整覆盖与发布前独立验证门禁 |
| SLSA v1.0 | https://slsa.dev/spec/v1.0/ | 构建可复现（正证据）；CI action 使用浮动 `@v4/@v5` 而非 SHA pin，溯源强度不足 |
| Hypothesis 属性测试 | https://hypothesis.readthedocs.io | 已安装并运行（5 属性 × 100–150 例），独立复现 P0-1 与 P2-8 |
| Mutation testing | https://en.wikipedia.org/wiki/Mutation_testing | 已做 6 例定向变异，5 杀 1 存活；mutmut 原生 Windows 不支持，使用自建等价脚本 |

## 5. 正面证据（阴性对照）

- 隔离源码副本重建五包：2.x `7f200b02…` / install `901a6471…`、1.9.x `57fca97f…`、Hermes `b9ac66a9…`、在线 `a02f2cf5…`、技能 `2bf54f77…`，与 `dist/` 逐字节一致。
- 五个 zip 条目数 25/25/25/70/42/45，绝对路径 0、`..` 0、重复 0、`.pyc` 0。
- GitHub API `ggml-org/llama.cpp` b11612：cpu `b8456ccb…`、cuda12.4 `4c6d8afd…`、cuda13.4 `e1c59ee1…`、vulkan `e62f0674…`、cudart12.4 `8c79a9b2…`、cudart13.4 `738f8c25…`，与登记一致。
- HuggingFace 模型 oid 因网络不可达未能核验，属未决项。
- 工具安装：默认 PyPI/清华/阿里云不可达或连接重置；腾讯云与中科大镜像可用（已验证并用于安装全部工具）。
- 受限项：`mutmut 3.8.1` 官方不支持原生 Windows，改用自建定向变异脚本；HuggingFace 模型 oid 因网络不可达仍未核验；未做真机模型翻译。

## 6. 可复跑命令

```bash
cd /c/STALKER-AiTranslate-2x
python tools/test_all.py
python tools/check.py
python tools/release.py --build
python tools/verify_manifests.py
# 独立静态检查（不依赖第三方工具）
python - <<'PY'
import ast,pathlib,builtins
for p in pathlib.Path('core/stalker_translate_core').glob('*.py'):
    t=ast.parse(p.read_text(encoding='utf-8')); d=set(dir(builtins)); u=[]
    for n in ast.walk(t):
        if isinstance(n,(ast.FunctionDef,ast.ClassDef)): d.add(n.name)
        elif isinstance(n,ast.ImportFrom): d.update(a.asname or a.name for a in n.names)
        elif isinstance(n,ast.Name):
            if isinstance(n.ctx,ast.Store): d.add(n.id)
            elif isinstance(n.ctx,ast.Load) and n.id.startswith('_'): u.append((n.id,n.lineno))
    miss=[x for x in u if x[0] not in d]
    if miss: print(p.name,miss)
PY
# 外部工具（已装于 tmp/audit_tools2，使用腾讯云镜像）
tmp/audit_tools2/Scripts/ruff.exe check --select E,F,W,S,B core plugins/ai_translate products
tmp/audit_tools2/Scripts/bandit.exe -q -r core plugins/ai_translate products -f txt
tmp/audit_tools2/Scripts/pylint.exe --disable=C,R --score=n core/stalker_translate_core/*.py
tmp/audit_tools2/Scripts/pip-audit.exe -r tmp/audit_external/requirements.txt
tmp/audit_tools2/Scripts/python.exe -m pytest -q tmp/audit_external/test_properties.py
tmp/audit_tools2/Scripts/python.exe tmp/audit_external/mutation_test.py
# 对抗矩阵（在 tmp/audit_adversarial 下）
python run_adv.py v2_plugin v2 <scenario> work/<name> normal
python run_conc.py v2_plugin v2 work/<name> same_out
```

## 7. 最终结论

**审计阶段结论**：独立审计发现的两个 `NameError`、写副作用校验、畸形 XML 静默成功、并发锁崩溃与规范化 core 覆盖盲区均属实。修复阶段已完成第 8 节所列代码/工程整改，并复跑全量测试与独立门禁通过；剩余事项为外部授权（上游代码许可）与版本提交/发布操作，不属于代码缺陷。

## 8. 修复状态（2026-11-25 18:48 起）

| 编号 | 状态 | 修复内容 |
|---|---|---|
| P0-1 | 已修 | `xray.py:352` 常量统一为 `_MOJIBAKE_CHARS`；新增 5 项规范化 core 直接回归 |
| P0-2 | 已修 | `project.py` 两处 `_files_equal` 改为 `files_equal` |
| P0-3 | 已修 | `release.py --check/--tag` 改为纯只读；`build.py --check` 不再写镜像；`sync_assets.py --check` 不再 mkdir。实测哈希前后一致 |
| P1-1 | 已修 | `tools/test_all.py` 动态发现全部 31 个测试；CI 改为全量 job + 3.11/3.12/3.13 矩阵 |
| P1-2 | 已修 | README 六个发布 zip 与七个入口哈希全部按当前源码/包刷新 |
| P1-3 | 待操作 | 工作区仍未提交、tag 未创建；需用户确认后提交/打 tag |
| P1-4 | 未决 | wzyddg 上游 `license: null` 属外部授权问题，代码层已完整署名，无法靠改代码消除 |
| P1-5 | 已修 | CI actions 改为完整 commit SHA pin；Hermes 新增可配置 `allowed_roots` 边界；复核发现在线 MCP 原实现已 `resolve()` 后 `relative_to()`，符号链接风险为误报 |
| P1-6 | 已修 | 与 P0-3 一并修复 |
| P1-7 | 已修 | 源 XML 解析失败记录 `_parse_failures` 并输出显式告警；终检阶段 `strict=True` 计入 `bad_parse`；保留“坏文件跳过、好文件照常产出”的产品语义 |
| P1-8 | 已修 | 锁文件 `open` 增加短退避重试与友好拒绝；对抗场景从异常崩溃改为一成功一拒绝 |
| P2-1 | 部分修 | `_revoke_dangling_generated_refs` 解析失败不再误撤销账本；其余 best-effort `except: pass` 保留 |
| P2-2 | 已修 | CI 增加 ruff(F821 门禁)、bandit(-ll)、pip-audit；旧误报报告仍建议废弃 |
| P2-3 | 已修 | CI 增加 3.11/3.12/3.13 矩阵 |
| P2-4 | 已修 | 清理 135 个 `.pyc`；构建/测试统一 `PYTHONDONTWRITEBYTECODE=1` |
| P2-5 | 未决 | 存量 IWP 译文质量属外部数据，需单独数据治理，不随代码发布修复 |
| P2-6 | 已修 | 回放/核验脚本支持 `STALKER_PLUGIN`、`STALKER_MODS_ROOT`、`IWP_SRC/OUT`、`STALKER_TERMBASE` 环境变量 |
| P2-7 | 已修 | 新增 `tests/test_core_canonical_paths.py` 并纳入动态全量发现；根 core 关键路径有直接回归 |
| P2-8 | 已修 | `patch_table_text` 写回前清理 XML 1.0 非法控制字符；Hypothesis 5/5 通过 |
| P2-9 | 已修 | 定向变异 6 例，删除 `failed_strings` 的变异由项目交付闸门测试杀死 |
| P3 | 部分修 | 2.x 两包新增 `.sha256` 旁车；SKILL 版本复核已为 0.1.0；CLI 缺配置裸异常在本轮复核未复现 |

### 复验结果

- `python tools/test_all.py`：31 个测试文件全部通过（含新增规范化 core 回归）。
- `tools/check.py`、`tools/release.py --check`、`tools/build.py --check`、`tools/verify_manifests.py`、`tools/gen_release_manifest.py --check`：全部 OK；release/build 校验前后文件哈希一致。
- 独立工具：ruff `F821` 0；bandit `High` 0；pip-audit 无已知漏洞；Hypothesis 5/5；对抗复核中畸形 XML 显式告警、并发场景无未处理异常。
- 五产品包已重建，README 与 `release_train.json` 哈希一致。

## 9. 第二轮修复与终审（2026-11-25 19:xx–19:5x）

用户指令：全部修复、不留遗留；wzyddg 相关代码处补注释来源；存量译文治理完成；术语库整体深审；
面向小模型的产品（2.x 插件、1.9.x 插件、Hermes MCP）在**代码端**补偿其对提示词/规则的遵守不足；
全部完成后再次深度审计。

### 9.1 本轮修复

| # | 事项 | 结果 |
|---|---|---|
| F1 | wzyddg 来源注释补全 | `assets/xray_pkg.zip` 内 5 个 vendored 文件全部加来源头注释（原样/修改两类），`core/stalker_translate_core/vendor.py` docstring+加载点、`plugins/ai_translate/plugin.py`、`products/plugin_1_9x/ai_translate.py`、`products/mcp_hermes/server.py` 的 `load_vendor` 调用点均加内联来源注释（含 license: null 与 THIRD_PARTY_NOTICES.md 指引）。P1-4 代码侧闭环（授权本身仍属外部问题，用户确认无需处理） |
| F2 | 存量译文治理（P2-5） | 新增 `tools/iwp_quality_gate.py`（证据/策略分离）：CJK 校准后 trunc 1171→17 真缺陷；西里尔残留 308→2（306 条为合规专名豁免）；num_lost 51→5；BLOCK=325（320 未译+3 占位符+2 西里尔长句）全部进工单 `audit-reports/IWP_REWORK_LIST_20261125.json`；7 个缺失文件（符号表类）按字节透传到产出目录。回归测试 `tests/test_iwp_quality_gate.py` 7/7 |
| F3 | 术语库深度审计 | 新增 `tools/audit_termbase.py` 九维度审计（原始重复键/无效条目/值健康/跨类冲突/超长键/meta 引用/内置词表冲突/覆盖缺口/占位符语义）：真缺陷仅 `_meta.case_sensitive` 10 条失效引用→已清理（体积同步恢复紧凑序列化 1,218,049 B）；229 条占位符为正确源样保留、4 条 no-CJK 为合法转写（КПК→PDA 等）；重复键/冲突/乱码/超长键全部 0；覆盖缺口（Heckler&Koch、Remington 等武器厂商词）已列候选清单。健康门 `tests/test_termbase_health.py` 10/10 纳入 CI |
| F4 | 小模型代码端加固 | 核心缺口：全链路无"输出外壳剥离"。新增 `pipeline.strip_output_wrappers`（代码栅栏/"译文："类前缀/整串引号包裹，源文本带同类引号时不动），接入全部 5 个模型响应出口（2.x 单条+批量、1.9.x 单条+批量 vendored 副本、Hermes 单条）。已有硬闸门复核：mask/restore 占位符、换行恢复、残留西里尔闸门、数字保真、术语回填、语义角色、echo 标记均在代码层，非纯提示词。回归 `tests/test_output_guard.py` 14/14 + Hypothesis 幂等/不吞非空输出 2 属性通过 |

### 9.2 终审（两层全部重跑）

- **L1**：`tools/test_all.py` 34/34 测试文件全绿（31+新增 3 个：iwp_quality_gate / termbase_health / output_guard）。
- **L2**：ruff `F821` 0（新代码无未定义名）；bandit `High` 0；Hypothesis 7 属性全过；compileall 干净编译；`.pyc` 已清理。
- **发布一致性**：五包重建后 `check.py`、`build.py --check`、`release.py --check`、`verify_manifests.py`、`gen_release_manifest.py --check` 全 OK；README 13 处哈希按位刷新。
- **数据治理终态**：IWP 闸门 BLOCK=325 进工单待模型重译（外部数据，工具侧机制已完备）；术语库缺陷类 0。
- 未决：工作区提交/tag/推送（需用户确认）；IWP 325 条工单重译（需模型跑批）。
