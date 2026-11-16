# 平台侧补丁：让「全新用户」也能从插件管理界面一键安装线上插件

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
| `include/xrkit/core/plugin_registry.h` | **新增**：不依赖 Qt 的纯逻辑（注册表条目结构、`registry_missing`、`registry_pick`）。core 层不许依赖 Qt，所以这段必须用 std 写。 |
| `tools/plugin_manager/main.cpp` | 「检查插件更新」解析注册表时，把**本地还没装的条目**补成表格行（状态列写「可安装（线上注册表）」），其余安装逻辑一行不改。 |
| `tests/plugin_registry_test.cpp` | **新增**：纯 C++ 回归测试（不依赖 Qt，可单独编译）。 |

行为变化：

1. 用户点「检查插件更新」→ 表格里除了本地插件，多出线上注册表里尚未安装的条目；
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

## 验证状态

- 纯逻辑部分：**已用本机 MSVC 14.44 (VS2022 Build Tools) 编译并跑通**
  （`PLUGIN-REGISTRY-TEST ok`）。
- Qt 外壳部分（`tools/plugin_manager/main.cpp`）：**未在本机编译**——
  本机没有 Qt 6.8.3 SDK（只有 HH2 目录里的运行时 DLL），改动只用了本文件
  已在用的 API（`QJsonDocument` / `QTableWidget::insertRow` / `QTableWidgetItem`），
  但**编译与整体验证需在平台作者那边做**。
- 补丁已在平台源码的干净 checkout 上 `git apply --check` 通过。
