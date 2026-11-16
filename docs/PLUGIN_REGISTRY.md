# 接入平台的插件管理（注册表 / 检查更新 / 一键安装）

本文说明 STALKER 汉化工具集 2.x（BETA.246）的插件管理机制，以及 `ai_translate`
需要怎么配合。结论先说：

> **当前客户只有平台时，在平台的「插件管理」里搜不到本插件，也装不了。**
> 平台的插件管理器只认一份**硬编码的线上注册表**，我们的条目还没写进去。

## 1. 平台的机制（从平台二进制里核实过）

- 插件管理器：`xrkit_plugin_manager.exe`（与 `xrkit_app.exe` 同目录）。
- 注册表地址（**硬编码**，没有环境变量可覆盖）：

  ```text
  https://raw.githubusercontent.com/ALittleHuman/STALKER-Localization-Toolkit-2.x-Network-Plugin/main/plugins.json
  ```

- 注册表是一个 JSON，根下有 `plugins` 数组；每条至少要有
  **版本号**和**安装包地址**（平台提示语：`Release 缺 version/安装包地址`），
  安装包地址要带 **sha256**（提示语：`sha256 不匹配`）。
  平台二进制里出现的字段名：`id` / `name` / `version` / `repo` / `note` /
  `url`（安装包地址）/ `sha256` / `entry` / `target` / `depends`（插件前置）。
- 下载与安装：下载 zip → 校验 sha256 → 解压到 `xrkit_update/plugin-<id>/` →
  落到 `plugins/<id>/`；日志里会打印 `PLUGIN-MANAGER ok action=…`。
- 「检查插件更新」＝ 比对**本地已装插件的 version** 与注册表里的 version。
  本地版本来自插件自己的 `PLUGIN_INFO.version`，平台用
  `xrkit_cli --py-plugins-scan` 扫描出来，输出形如：

  ```json
  [{"file": "ai_translate", "id": "ai_translate",
    "name": "AI 离线翻译（Hy-MT2 / 自选模型）", "version": "2.0.0",
    "repo": "https://github.com/qy0214/…", "provides": [], "depends": {}}]
  ```

- 平台还支持「插件前置」：注册表条目缺前置时，插件会被自动禁用。

## 2. 我们这边要满足的条件

| 条件 | 现状 | 说明 |
|---|---|---|
| 插件 id = `ai_translate` | ✅ | 目录名即 id，入口固定 `plugin.py` |
| `PLUGIN_INFO` 有 id/name/version/repo/description | ✅ | 平台扫描得到的元数据来源 |
| **版本号随发布递增** | ⚠️ | 一直写死 `2.0.0`，不改的话平台永远显示「无需更新」 |
| 安装包结构 | ✅ | `dist/…_install.zip` 顶层就是 `ai_translate/`（`plugin.py` + `assets/`） |
| 安装包 sha256 | ✅ | 发版脚本自动算好，直接填注册表 |
| 注册表条目 | ❌ | 还没写进 network-plugin 仓库的 `plugins.json` |

## 3. 发布流程（一条命令 + 一次粘贴）

```powershell
# 1) 改版本号 + 重建 zip/清单 + 打印注册表条目
python tools/release_plugin.py 2.0.1
```

脚本会做三件事：

1. 同时改 `plugin.py` 的 `PLUGIN_INFO.version` 和
   `tools/gen_release_manifest.py` 的 `EXPECTED_PLUGIN_VERSION`；
2. 重建两个包：
   - `dist/ai_translate_plugin_v2.0.1.zip`（仓库结构原样）
   - `dist/ai_translate_plugin_v2.0.1_install.zip`（**顶层 `ai_translate/`，
     给平台安装器用**）；
3. 打印一段可直接粘进 `plugins.json` 的条目（含真实 sha256 与 Release 下载地址）。

接着：

```powershell
# 2) 把 install.zip 传成 GitHub Release 资产（tag 用 v<版本号>）
gh release create v2.0.1 dist/ai_translate_plugin_v2.0.1_install.zip --title "ai_translate 2.0.1"

# 3) 把脚本打印的条目追加进注册表的 plugins 数组
#    https://github.com/ALittleHuman/STALKER-Localization-Toolkit-2.x-Network-Plugin
#    → main/plugins.json
```

最后在平台里：插件管理 → 「检查插件更新」→ 选中 ai_translate →
「现在下载并安装」→ 重启工具集即可用。

## 4. 客户侧的完整路径

1. 只装平台 → 打开「插件管理」→ 「检查插件更新」；
2. 列表里出现 `ai_translate 2.0.1`（**前提是注册表里已有我们的条目**）；
3. 点「现在下载并安装」→ 平台下载 zip、校验 sha256、解压到
   `plugins/ai_translate/`；
4. 重启工具集 → 工具页出现「AI 翻译」；
5. 首次使用按显卡自动选档，**模型与 llama.cpp 运行库由插件自己下载**
   （不在安装包里，避免安装包上百 MB）。

## 5. 已知限制 / 待办

- **注册表仓库当前无写权限**：本机 SSH 密钥对
  `ALittleHuman/STALKER-Localization-Toolkit-2.x-Network-Plugin` 返回
  `Permission denied (publickey)`，因此第 3 步的「追加条目」必须由有权限的人完成。
- 注册表字段名来自平台二进制的字符串，**建议以注册表里现有一条真实条目为准**
  再定稿（尤其 `url` / `entry` / `target` 三个字段的写法）。
- 平台自带 `plugins.disabled` 机制：插件被禁用时不会加载（我们插件没有前置依赖，
  不受影响）。
