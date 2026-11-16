# 接入平台的插件管理（注册表 / 检查更新 / 一键安装）

本文说明 STALKER 汉化工具集 2.x 的插件管理机制，以及 `ai_translate` 怎么接进去。

> **当前状态：客户只有平台时，在平台的「插件管理」里还搜不到本插件。**
> 平台的插件管理器只认一份**硬编码的线上注册表**，我们的条目还没写进去。
> 我们这边该准备的都准备好了（发布包、发版脚本、注册表条目），
> 只差把条目追加进注册表仓库 —— 那一步需要 `ALittleHuman/…-Network-Plugin`
> 仓库的写权限。

本文的字段与流程**来自平台公开源码**（`ALittleHuman/STALKER-Localization-Toolkit-2.x`
的 `tools/plugin_manager/main.cpp` 与 `src/core/plugin_update.cpp`），不是猜的。

## 1. 平台机制

- 插件管理器：`xrkit_plugin_manager.exe`（与 `xrkit_app.exe` 同目录）。
- 注册表地址（**硬编码**，没有环境变量可覆盖）：

  ```text
  https://raw.githubusercontent.com/ALittleHuman/STALKER-Localization-Toolkit-2.x-Network-Plugin/main/plugins.json
  ```

- 注册表格式（`main.cpp` 实际读取的字段）：

  ```json
  {
    "plugins": [
      {
        "id": "ai_translate",
        "version": "2.0.1",
        "package": "https://…/ai_translate_plugin_v2.0.1_install.zip",
        "sha256": "211266ae…",
        "entry": "plugin.py",
        "target": "ai_translate"
      }
    ]
  }
  ```

  - `id`：插件 id，与目录名一致；
  - `version`：显示用的版本号；
  - `package`：安装包地址；**不以 `http` 开头时**，平台会拼到
    `…/STALKER-Localization-Toolkit-2.x-Network-Plugin/main/` 后面（即把包直接
    放进注册表仓库里，用相对路径）；
  - `sha256`：可选（为空就跳过校验）；不匹配会直接拒绝安装；
  - `entry`：**文件名**（不是路径），平台在解压后的目录树里递归按文件名找；
    找到后，**它所在的整个目录**被整体替换到 `plugins/<target>/`；
  - `target`：目标目录名，缺省取 `id`。

  `name` / `repo` / `note` 这三个字段平台不读，但我们照样写上，便于人看。

- 「检查插件更新」＝ 拉下注册表并把每条 `id version package` 列出来
  （**只列注册表内容，不和本地已装版本做比对**）——所以"平台看见新版本"
  完全取决于注册表里的 `version`。
- 「安装所选更新」＝ 下载 zip → 校验 sha256 → 解压 → 找 `entry` →
  `xrkit_cli --plugin-update <plugins_dir> <target_dir> <staged_dir> <entry> <txn> apply`
  → `activate` → 提示"插件已替换，请重启本体生效"。
  更新走事务：先在 `plugins.disabled` 里自动禁用 → 整目录备份替换 →
  下次启动确认成功则提交、失败则回滚；原本被禁用的插件更新后仍禁用。
- 下载走系统 WinHTTP，`WINHTTP_OPTION_REDIRECT_POLICY_ALWAYS`，
  所以 GitHub Release 资产（302 → objects.githubusercontent.com）可以直接用。

## 2. 我们这边要满足的条件

| 条件 | 现状 | 说明 |
|---|---|---|
| 插件 id = `ai_translate` | ✅ | 目录名即 id，入口固定 `plugin.py` |
| `PLUGIN_INFO` 有 id/name/version/repo/description | ✅ | 平台扫描本地插件时的元数据来源 |
| **版本号随发布递增** | ✅ | `tools/release_plugin.py` 一条命令同步两处版本号 |
| 安装包结构 | ✅ | `dist/…_install.zip` 顶层是 `ai_translate/plugin.py`，`entry="plugin.py"` 正好命中 |
| 安装包 sha256 | ✅ | 发版脚本自动算好，直接填注册表 |
| 注册表条目 | ❌ | 还没写进 Network-Plugin 仓库的 `plugins.json` |

## 3. 发布流程（一条命令 + 一次粘贴）

```powershell
# 1) 改版本号 + 重建 zip/清单 + 打印注册表条目（含真实 sha256）
python tools/release_plugin.py 2.0.1
```

脚本做三件事：

1. 同时改 `plugin.py` 的 `PLUGIN_INFO.version` 与
   `tools/gen_release_manifest.py` 的 `EXPECTED_PLUGIN_VERSION`；
2. 重建两个包：
   - `dist/ai_translate_plugin_v2.0.1.zip`（仓库结构原样，用于分享/核对）
   - `dist/ai_translate_plugin_v2.0.1_install.zip`（**顶层 `ai_translate/`，
     给平台安装器用**）
3. 打印可直接粘进 `plugins.json` 的条目。

接着：

```powershell
# 2) 传成 GitHub Release 资产（tag = v<版本号>）
gh release create v2.0.1 `
  dist/ai_translate_plugin_v2.0.1_install.zip `
  dist/ai_translate_plugin_v2.0.1.zip `
  --title "ai_translate 2.0.1" --notes "见 README"

# 3) 把第 1 步打印的条目追加进注册表的 plugins 数组
#    https://github.com/ALittleHuman/STALKER-Localization-Toolkit-2.x-Network-Plugin
#    → main/plugins.json
```

之后客户侧：平台 → 插件管理 → 「检查插件更新」→ 列表里出现
`ai_translate 2.0.1 <package 地址>` → 选中后点「安装所选更新」→
提示"插件已替换，请重启本体生效" → 重启工具集即用。

## 4. 客户侧的完整路径

1. 只装平台 → 「工具 → 插件管理」→ 「检查插件更新」；
2. 列表里出现 `ai_translate`（**前提是注册表里已有我们的条目**）；
3. 「安装所选更新」→ 平台下载 zip、校验 sha256、整目录替换 `plugins/ai_translate/`；
4. 重启工具集 → 工具页出现「AI 翻译」；
5. 首次使用按显卡自动选档，**模型与 llama.cpp 运行库由插件自己下载**
   （不在安装包里，避免安装包上百 MB）。

## 5. 已知限制 / 待办

- **注册表仓库当前无写权限**：本机 SSH 密钥与 `gh` 账号对
  `ALittleHuman/STALKER-Localization-Toolkit-2.x-Network-Plugin` 都是
  404 / `Permission denied (publickey)`，第 3 步必须由有权限的人完成。
- 该仓库在 GitHub 上目前搜不到（可能私有或尚未创建）；平台是匿名拉取的，
  所以它**必须是公开仓库**，否则所有客户端都拿不到注册表。
- `package` 也可以用相对路径（把安装包放进注册表仓库里），这样不依赖
  GitHub Release 的跳转，更稳。
- 平台有 `plugins.disabled` 机制：被禁用的插件更新后仍禁用
  （我们插件没有前置依赖，不受影响）。

## 6. 发了条目之后，用户到底能看到什么、能不能装

| 场景 | 结果 |
|---|---|
| 任意用户打开「插件管理」主表格 | **看不到**（表格只扫本地 `plugins/` 目录） |
| 任意用户点「检查插件更新」弹窗 | **能看到** `ai_translate  2.0.1  <package 地址>`（需开「工具 → 联网功能」） |
| 已装过旧版的用户，选中 `ai_translate` 点「安装所选更新」 | **能升级**：下载 → 校验 sha256 → 整目录替换 → 重启生效 |
| 全新用户想从界面装上 | **装不了**：按钮要求先在本地表格选中一行，表格里没有它就没有行可选 |

结论：这份注册表条目解决的是**「同步更新后平台可见 + 已装用户可一键升级」**；
「从未装过的用户直接在界面里装上」需要平台侧改造。

### 6.1 平台侧最小改动（建议一并提给平台作者）

在 `tools/plugin_manager/main.cpp` 的 `ManagerWindow::refresh()` 里，扫完本地之后
再拉一次注册表，把**本地没有的条目也追加成表格行**（状态列写「可安装」）：

```cpp
// 现状：items_ 只来自本地扫描（run_scan）
// 建议：合并注册表条目，让全新用户也能选中安装
//   - 拉 https://raw.githubusercontent.com/ALittleHuman/
//     STALKER-Localization-Toolkit-2.x-Network-Plugin/main/plugins.json
//   - 对每个 id 不在本地 items_ 里的条目，追加一行：
//       col0=id, col1=id, col2=name, col3=version, col4="可安装"
//   - 安装处理逻辑不用改：它本来就按「选中行的 id」去注册表取
//     package / sha256 / entry / target，整目录替换 plugins/<target>/
```

改动后，客户侧就是完整闭环：平台 → 插件管理 → 表格里出现 `ai_translate` →
「安装所选更新」→ 重启生效，我们这边不需要再改任何代码。
