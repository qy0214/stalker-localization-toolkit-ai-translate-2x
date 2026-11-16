# 平台侧补丁：让全新用户也能一键安装线上插件

补丁本体：`plugin_registry_merge.diff`（可直接 `git apply`）。
上游分支与 PR：`qy0214/STALKER-Localization-Toolkit-2.x` 的 `feat/plugin-registry-install`
→ <https://github.com/ALittleHuman/STALKER-Localization-Toolkit-2.x/pull/1>
背景与注册表字段含义见 [`../PLUGIN_REGISTRY.md`](../PLUGIN_REGISTRY.md)。

## 改动了什么

| 文件 | 改动 |
|---|---|
| `include/xrkit/core/plugin_registry.h` | **新增**。注册表条目结构（`id` / `version` / `package` / `sha256` / `entry` / `target`）+ 两个纯函数：`registry_missing()`（注册表 ∖ 本地已装）、`registry_pick()`（按选中 id 取条目）。不依赖 Qt —— core 层不许依赖 Qt。 |
| `tools/plugin_manager/main.cpp` | 新增 `sync_registry_rows()`：拉线上注册表，把**本地还没装的条目**补进表格，状态列标「可安装（线上注册表）」。接入三条路径：**启动时自动同步**、点「刷新」、点「检查插件更新」（并弹窗列出注册表条目）。注册表地址提为 `kDefaultRegistryUrl`，支持环境变量 `XRKIT_PLUGIN_REGISTRY_URL` 覆盖（默认仍是原地址）。新增 `--registry-rows <url>` 无头自检口。**安装逻辑一行未改**。 |
| `tests/plugin_registry_test.cpp` | **新增**。纯 C++ 回归测试（不依赖 Qt，可单独编译）。 |
| `docs/2.0.0-插件注册表与全新用户一键安装.md` | **新增**。上游分支里的说明文档。 |

## 作用是什么

**改动前**：插件管理器的表格只扫本地 `plugins/` 目录，「安装所选更新」也只认表格里已经存在的行 ⇒ 线上注册表里有的、本地没装的插件，**界面里既看不到也装不上**（这条链路只能给已装插件做更新）。

**改动后**：打开插件管理器即自动同步（或点「刷新」/「检查插件更新」），线上注册表里尚未安装的插件以「可安装（线上注册表）」出现在表格中；选中 → 「安装所选更新」→ 下载 → sha256 校验 → 解压 → 按 `entry` 定位目录 → 整目录替换 `plugins/<target>/`。未安装过也能装（`update_prepare` 对不存在的目标目录建空备份再替换，同样走事务、失败可回滚）。

**结果**：客户只装平台，也能在插件管理界面里找到并一键安装线上插件；插件作者把发布包登记进注册表就能触达用户，无需预装、无需手工解压。

## 应用与构建

```text
git apply docs\patches\plugin_registry_merge.diff
scripts\build.ps1 -Preset msvc-shell      # 产物 build\msvc-shell\src\shell\xrkit_plugin_manager.exe
```

补丁已在平台源码的干净 checkout 上 `git apply --check` 通过；本机（VS2022 BuildTools 14.44 / MSVC 19.44 / Qt 6.8.3 / Win SDK 10.0.26100）编译 `xrkit_plugin_manager.exe` **BUILD_OK 无警告**，纯逻辑单测 `PLUGIN-REGISTRY-TEST ok`，界面实测全新用户表格自动出现「ai_translate / 2.0.1 / 可安装（线上注册表）」，点「安装所选更新」后落地文件与发布包逐字节一致。
