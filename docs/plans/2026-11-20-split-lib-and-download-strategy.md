# 规划：拆分 ai_translate_lib/ 包 + download_one 策略化（v2.1.0）

> 依据：BETA.250 宿主源码 `src/engine/plugin/py_host.cpp`（commit 80becbc）逐段核实，
> 非猜测。本文所有"宿主约束"条目均给出源码证据。

## 0. 宿主约束（实证结论，规划的地基）

从 py_host.cpp 核实到的加载机制：

1. **目录插件的入口模块名 = 目录名**（`raw_key_of`：目录插件取父目录名，单文件取文件名去 `.py`）。
   ⇒ 我们的入口永远是 `sys.modules["ai_translate"]`（由 `spec_from_file_location` 导入
   `plugins/ai_translate/plugin.py`）。**兄弟包不得叫 `ai_translate`**（会与入口模块名冲突）。
2. **插件根目录被宿主追加进 sys.path**（append，不遮蔽标准库）⇒ `import ai_translate_lib`
   在插件代码里直接可用，**不需要**自己 `sys.path.insert`，也不应该。
3. **模块名占用检查**：加载前对入口名查 `find_spec`，撞标准库/三方库直接**拒绝加载**。
   `ai_translate_lib` 无冲突；但它是全局命名空间里的名字，须保证没有其他插件用同名。
4. **卸载清理按 `__file__` 前缀匹配插件根**（`collect_plugin_modules`）：凡是
   `__file__` 落在 `plugins/ai_translate/` 下的模块（含 `ai_translate_lib.*`）都会被记录
   进 `Manifest.modules`，禁用/卸载/同进程重载时统一 purge（`lifecycle::purge_modules`）。
   ⇒ **拆包文件必须全部位于插件根目录之内**，放外面就脱离宿主生命周期管理。
5. **同进程可两批加载 + 同名重载**（GUI 自检：默认目录 → 夹具目录；重载先跑上一批的
   shutdown/on_unload 再清模块）⇒ lib 全部模块必须**可安全重载**：禁止模块级副作用
   （启动线程、建目录、开文件），全局状态（`_UI/_HOST/_API` 等）只允许在 `register()`
   内注入，重复 import/二次 register 语义不变。
6. **两遍加载**：① import 全部入口收 `PLUGIN_INFO`（不 register）；② 解析 depends；
   ③ 只对启用的插件调 register。⇒ `plugin.py` 顶层必须保持"轻"：`PLUGIN_INFO` 可读、
   重资源（翻译管线、UI 装配）全部函数内懒加载——与现行 P2 契约（禁顶层 urllib 等
   重导入）同口径。
7. **跨插件 import 被有意阻断**（"插件之间不许靠 import 互相串"）⇒ lib 只服务本插件，
   不得设计成可被其他插件复用的公共库。
8. **更新事务整目录替换**（`.xrkit_update` staged_dir + 自动禁用/恢复）⇒ 拆出的每个文件
   都必须进安装 zip，否则更新后缺文件。注册表 `sha256` 锁的是整个 install zip，天然覆盖。
9. 入口契约不变：`register(api)` 导出、`register()` 返回 bool、失败按 owner 回滚。

## 1. 阶段一：download_one 策略化（单文件内做，随 2.0.7 独立发布）

### 现状问题
`download_one` 圈复杂度 51：源轮询 × 重试 × 断点续传 × 宿主门面回退 × 校验 × 取消
六重关注点纠缠在一个双层循环里。

### 设计

```python
class DownloadContext:
    """一次尝试的全部输入：url/target/log/progress/cancel/verify/total_hint/have/attempt"""
    # 不变量：策略不得自行决定"换源/重试"——那是编排器的事

class DownloadResult:            # 值对象
    digest: str | None           # verify 通过后的 sha256；无 verify 为 True
    bytes_done: int

class DownloadStrategy:          # 抽象：can_handle + download 两段式
    name = "?"
    def can_handle(self, ctx) -> str | None:
        """返回 None=不接手；否则返回一句原因（用于日志）。"""
    def download(self, ctx) -> DownloadResult:
        """成功返回结果；失败抛异常。异常分类沿袭现有 taxonomy：
        DownloadCancelled / InvalidArtifact / _HttpError(4xx) / IOError"""

class HostDownloadStrategy:      # B250+ http_download
    # can_handle：宿主有 http_download 门面 且 have==0（无断点）
    #             且 total_hint<=_HOST_DL_MAX 且 attempt==1（每源只试首轮）
    # download：api.host.http_download(url, target, timeout=按 0.5MB/s 换算,
    #             max_bytes=total+4MB)；ok 后 cancel 检查（取消则删目标重抛）；
    #             verify 在 target 上做；返回结果
class StreamDownloadStrategy:    # 自建流式
    # can_handle：恒 True（兜底）
    # download：现 _http_request 流式循环原样搬入（Range 续传/206 语义/
    #             0.4s 进度节流/4xx 不重试）

class DownloadPlanner:
    def plan(self, ctx) -> list[DownloadStrategy]:
        """给出本次 attempt 的有序策略。2.0.7 口径＝[Host?, Stream]；
        未来宿主加进度回调时只改这里。"""

def download_one(sources, target, log, progress=None, cancel_event=None,
                 retries_per_source=3, verify=None, total_hint=None):
    """编排器：源轮询 × 重试循环不变；每次 attempt 内按 planner 给出的
    策略序列逐个执行，错误分类处理（InvalidArtifact→删件换源、4xx→换源、
    其余→退避重试）全部留在编排器。"""
```

### 验收
- 现有 test_ui_fallback T7-1..T7-9 改写为策略级单测（Host/Stream 各自独立测）
  + planner 组合测（选择矩阵：有无宿主/有无 .part/体积超限/attempt 序号）。
- `download_one` 编排器圈复杂度 ≤15（v2.0.6 是 51）。
- gamedata 模拟回归 + 六套全绿后随 2.0.7 发布（纯内部重构，注册表 note 一句话）。

## 2. 阶段二：拆 ai_translate_lib/ 包（2.1.0，独立大版本）

### 文件切分清单（= 现有分节注释的边界，只搬移不改逻辑）

```
plugins/ai_translate/
  plugin.py                 # 入口壳：<300 行。PLUGIN_INFO + register + 步骤装配调用
  ai_translate_lib/
    __init__.py             # 版本/公共常量 re-export；零副作用、零顶层重导入
    platform_win.py         # ~L62-460：portable 标记/目录落点/编码链/Win 进程生命周期
                            #   （Job Object/孤儿清理）——注意：目录创建移到惰性函数
    http_gate.py            # ~L479-912：WinHTTP ctypes/urllib 包装/宿主门面/小请求分发
    downloader.py           # 阶段一产物：策略对象 + download_one 编排
    models_meta.py          # 档位表/下载任务/GGUF 元数据/sha256 校验/探针测速
    server.py               # LlamaServer/自检/GPU 验证（依赖 http_gate）
    glossary.py             # 术语分层/会话词表/高频专名/TM-lite/技术精确表
    prompts.py              # 提示词分区/掩码还原/清洗/风格处理
    pipeline.py             # TranslationJob 整类搬移（本阶段不内拆）
    analyzer.py             # classify_xray_file/analyze_input_dir/分句（勘察与执行共用判定）
    ui/
      __init__.py tab.py widgets.py steps_hw.py … steps_glossary.py
```

### 拆分顺序（每步一个可发布 commit，六套全绿才进下一步）

1. `http_gate.py`（最独立；T6/T7 测试覆盖最厚）
2. `downloader.py`（依赖 http_gate）
3. `platform_win.py` + `models_meta.py` + `server.py`
4. `glossary.py` + `prompts.py` + `analyzer.py`
5. `pipeline.py`（TranslationJob 整类，只搬不拆）
6. `ui/`（最大块，最后）

每步的机械动作：剪切函数区 → 新文件加头注释与导入 → 旧位置留
`from .x import y` 形态的兼容别名**只存在于过渡 commit**，当步测试绿了就删别名
（插件无外部 import 方，不需要长期 shim）。

### 导入纪律（写成 contract 测试）

- lib 内禁止：模块级副作用（建目录/开文件/起线程）、顶层重导入（urllib 等保持
  函数内惰性——沿用 P2 口径）、`import plugin` 反向依赖。
- 依赖方向单向：`ui → pipeline → server → downloader → http_gate → platform_win`。
- `plugin.py`：`from ai_translate_lib import …` 一律在函数内；顶层只有 PLUGIN_INFO
  与常量（宿主第一遍 import 只读它，冷启动目标 ≤50ms）。
- 可重载契约：新增测试——按宿主口径模拟（sys.modules purge 掉 `ai_translate*`
  与根目录下模块 → 重新 import → 二次 register），断言全局状态重建正确、无残留
  线程/句柄。

### 命名决策
- 包名 `ai_translate_lib`：不与入口模块名 `ai_translate` 冲突（宿主占用检查安全）；
  卸载清理按 `__file__` 根前缀匹配而非名字前缀，任何包名都会被 purge——但取
  `ai_translate_lib` 仍优于随意名（全局命名空间唯一、日志可读）。

## 3. 阶段三：打包 / 门禁 / 注册表适配

- `tools/release_plugin.py`：收集规则从"plugin.py + assets/"扩为
  "plugin.py + ai_translate_lib/** + assets/**"；repo zip 与 install zip 同口径。
- `tools/gen_release_manifest.py`：imported_modules 扫描扩展到 lib/**；
  zip↔仓库逐字节一致性自动覆盖新文件（现有检查机制不变）。
- `tests/test_2x_contract.py`：P2 扫描范围扩到 `ai_translate_lib/**`；
  新增"包可重载"与"禁止模块级副作用"契约项（AST 检查 lib 顶层语句类型）。
- 注册表/README：2.1.0 note 说明结构演进；`entry`/`target`/`sha256` 流程不变
  （宿主与平台用户无感知）。

## 4. 阶段四：真机验收（250 实例）

1. 六套测试全绿（contract/ui_fallback 项数增加）。
2. 真机 smoke（`.workbuddy/platform-250/run250`）：
   装 install zip → 探针确认 `import ai_translate_lib` 成功、
   `Manifest.modules` 记录了 lib 模块、禁用后 purge 干净（sys.modules 无残留）。
3. 冷启动计时：第一批 import（读 PLUGIN_INFO）≤50ms。
4. gamedata 模拟回归：勘察数字与 62,357 段全量结果与 2.0.6 一致。
5. 更新事务演练：2.0.x → 2.1.0 staged 替换后 lib 完整、禁用状态恢复正常。

## 5. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 宿主对子包 import 的运行时口径与源码理解有偏差 | 阶段二动主代码**前**，先用探针插件在 250 真机验证：子包 import / Manifest 记录 / 禁用 purge 三件事 |
| 重载后全局残留 | 可重载契约测试 + register 内注入唯一化 |
| 拆包期间发布被打断 | 每步 commit 独立可发布；策略化先随 2.0.7 落地 |
| 回滚 | 开拆前打 tag `v2.0.6-singlefile`；真机验证失败则放弃拆包、保留策略化成果 |

## 6. 里程碑

- M1（2.0.7）：download_one 策略化，单文件发布。
- M2：真机探针验证子包生命周期（决定性闸口，通过才开拆）。
- M3（2.1.0）：六步拆包，每步全绿。
- M4：打包/门禁/文档收口 + 真机验收 + 发布。
