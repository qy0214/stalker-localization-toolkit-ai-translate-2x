# 平台侧补丁：让「全新用户」在插件管理里就能看到并一键安装线上插件

给平台作者（`ALittleHuman/STALKER-Localization-Toolkit-2.x`）的补丁。
背景与字段含义见 [`../PLUGIN_REGISTRY.md`](../PLUGIN_REGISTRY.md)。

## 问题

插件管理器（`tools/plugin_manager/main.cpp`）里：

- 主表格 `items_` **只来自本地扫描**（`xrkit_cli --py-plugins-scan <plugins_dir>`）；
- 「安装所选更新」按钮取的是**表格当前行的 id**，再去线上注册表里找同名条目，
  下载 `package`、校验 `sha256`、解压、按 `entry` 定位目录，
  整目录替换 `plugins/<target>/`。

于是这条链路只能给**已经装在本地**的插件做更新；
线上注册表里有的、但本地没装的插件，在表格里没有行 ⇒ 选不中 ⇒ 装不上。

## 改动内容

| 文件 | 改动 |
|---|---|
| `include/xrkit/core/plugin_registry.h` | **新增**：不依赖 Qt 的纯逻辑（`RegistryEntry` / `LocalPluginRef` / `registry_missing` / `registry_pick`）。core 层不许依赖 Qt，所以这段必须用 std 写。 |
| `tools/plugin_manager/main.cpp` | 新增 `sync_registry_rows(verbose)`：拉线上注册表，把**本地还没装的条目**补成表格行（状态列「可安装（线上注册表）」）。接入三条路径：**启动时自动同步 / 点「刷新」同步 / 点「检查插件更新」同步（并弹窗列出）**。其余安装逻辑一行未改。 |
| `tests/plugin_registry_test.cpp` | **新增**：纯 C++ 回归测试（不依赖 Qt，可单独编译）。 |
| `tools/plugin_manager/main.cpp`（自检入口） | 新增 `--registry-rows <url>`：无头跑一遍**真实的表格补行路径**并把表格内容打到 stdout（CI / 人工核对用）。 |

行为变化：

1. 用户打开插件管理器（或点「刷新」）→ 表格里除了本地插件，多出线上注册表里尚未安装的条目；
2. 选中其中一行 → 「安装所选更新」按原逻辑下载 / 校验 / 解压 / 整目录替换；
3. 本地没有该插件时，`update_prepare` 会建空备份再替换（`src/core/update_transaction.cpp`
   已有分支），所以**全新安装**同样走事务，失败可回滚。

## 应用与构建

```powershell
git apply docs\patches\plugin_registry_merge.diff
# 或者把三个文件直接拷进源码树：
#   include\xrkit\core\plugin_registry.h
#   tools\plugin_manager\main.cpp（已含改动）
#   tests\plugin_registry_test.cpp

scripts\build.ps1 -Preset msvc-shell
# 需要 Visual Studio 2022 Build Tools + CMake + Ninja + Qt 6.8.3
# 产物：build\msvc-shell\src\shell\xrkit_plugin_manager.exe
```

## 跑新增的回归测试（纯 C++，不需要 Qt）

```bat
cl /nologo /std:c++20 /EHsc /W4 /permissive- /utf-8 /I include ^
   tests\plugin_registry_test.cpp /Fe:build\plugin_registry_test.exe
build\plugin_registry_test.exe
```

预期输出：`PLUGIN-REGISTRY-TEST ok`（退出码 0）。

## 无头自检（走真实表格路径）

```bat
xrkit_plugin_manager.exe --registry-rows <plugins.json 的地址> <插件目录>
```

输出形如：

```text
REGISTRY-ROW ai_translate|ai_translate|AI 离线翻译（Hy-MT2 / 自选模型）|2.0.1|可安装（线上注册表）
PLUGIN-MANAGER ok action=registry-rows added=1 rows=1
```

## 验证状态（本机实测）

环境：VS2022 Build Tools 14.44（MSVC 19.44）+ Windows SDK 10.0.26100 + Qt 6.8.3
（msvc2022_64，用 aqtinstall 装的）。

| 验证项 | 结果 |
|---|---|
| 编译整个插件管理器（`xrkit_plugin_manager.exe`） | ✅ `BUILD_OK`，无警告 |
| 纯逻辑单测 `tests/plugin_registry_test.cpp` | ✅ `PLUGIN-REGISTRY-TEST ok` |
| 无头自检：空插件目录（全新用户） | ✅ `added=1 rows=1`，行内容 `ai_translate / 2.0.1 / 可安装（线上注册表）` |
| 无头自检：本地已装 ai_translate | ✅ `added=0 rows=1`（不重复出现"可安装"行，只显示本地那条） |
| 安装事务（`xrkit_cli --plugin-update apply/activate`） | ✅ 平台自带 CLI 实测 `PLUGIN-UPDATE ok` |
| 补丁在平台源码干净 checkout 上 `git apply --check` | ✅ 通过 |

注：无头自检用的是**本地 HTTP 服务**提供的 `plugins.json`（把 `kRegistryUrl` 临时指到
`http://127.0.0.1:8765/plugins.json` 验证的）；合入后 `kRegistryUrl` 已恢复成
平台原本硬编码的那个地址。
