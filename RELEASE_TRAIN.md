# STALKER AI Translate Suite 发布列车

- Release train：`2026.11.25`
- Git tag：`suite-v2026.11.25`
- 共享 core：`1.0.1`

## 产品与资产

| 产品 | 版本 | 资产 | 字节 | SHA-256 |
|---|---|---|---:|---|
| plugin_2x | 2.0.29 | `dist/ai_translate_plugin_v2.0.29.zip` | 1022483 | `7d6473f8554bdcd04350cb8574680b4acde71a53c2c551f4f756d00697c85f27` |
| plugin_2x | 2.0.29 | `dist/ai_translate_plugin_v2.0.29_install.zip` | 1022083 | `c67a58cc73a24f0073e601f9bc1a1b79a4e4ac8e48c1f5e802a9d4513e51dc92` |
| plugin_1_9x | 1.9.26 | `dist/ai_translate_plugin_v1.9.26.zip` | 1035941 | `bf6759672717439c55e457d29e27b7c19633dc048597e69357a0b972338bbfaf` |
| plugin_1_9x | 1.9.26 | `dist/ai_translate_plugin_v1.9.26.manifest.json` | 3701 | `31a3eca6d8f16589c438425b4bed59b815a5efecbf34e722e1a0eae737d6e740` |
| mcp_hermes | 1.1.11 | `dist/stalker-translate-MCP-1.1.11.zip` | 12131102 | `38fd8ab16d25913338753cf188e466b1635b35f866d6412e7c66d57a0a76559e` |
| mcp_hermes | 1.1.11 | `dist/stalker-translate-MCP-1.1.11.manifest.json` | 4806 | `41e7819a59dc75c509bf40fb5a3075a68c81b98d953205a93330ae9df816a051` |
| mcp_online | 0.2.0 | `dist/stalker-translate-online-MCP-0.2.0.zip` | 996537 | `34c780527c39e0bce48e380b977feb428349e336f35a31e470a23645a0ef3cb8` |
| mcp_online | 0.2.0 | `dist/stalker-translate-online-MCP-0.2.0.manifest.json` | 4289 | `8c2544644ec7488d1a547aaa8c0d54c6e8c56788e48d81429a20512180fac98e` |
| skill | 0.2.0 | `dist/stalker-translate-skill-0.2.0.zip` | 1001979 | `d1097a5349870a691de4300512b2210b0c31053222286ca49c10ab2908f5a8bf` |
| skill | 0.2.0 | `dist/stalker-translate-skill-0.2.0.manifest.json` | 2927 | `28149fa9c9d21f318bdafbbff28ca3506160ea57915a942a0e7f5cbf16539d31` |

## 兼容矩阵

| 组件 | 版本/要求 |
|---|---|
| core | 1.0.1 |
| plugin_2x | 2.0.29 |
| plugin_1_9x | 1.9.26 |
| mcp_hermes | 1.1.11 |
| mcp_online | 0.2.0 |
| skill | 0.2.0 |
| core | 1.0.1 |
| Python core | >=3.11 |
| Python plugin_2x | host-managed |
| Python plugin_1_9x | host-managed |

## 旧仓库切换

本仓库启用后，旧仓库只保留历史与跳转说明，不再同步源码：

- plugin_1_9x：https://github.com/qy0214/stalker-localization-toolkit-ai-translate（pending_archive）
- mcp_hermes：https://github.com/qy0214/stalker-translate-mcp（pending_archive）

归档前确认：主仓库 tag、五个分享包、SHA-256 与注册表条目均已发布。
