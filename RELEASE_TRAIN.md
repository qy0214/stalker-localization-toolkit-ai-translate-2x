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
| mcp_online | 0.2.2 | `dist/stalker-translate-online-MCP-0.2.2.zip` | 997887 | `a9b2a983fc6498d9bf8de68653971c3928128c80f13576f2be600445b8c20a22` |
| mcp_online | 0.2.2 | `dist/stalker-translate-online-MCP-0.2.2.manifest.json` | 4289 | `5d989de247c575c1a9055b6835eb1946dc3045c2f786c35b9a682eb42f4abe3d` |
| skill | 0.2.2 | `dist/stalker-translate-skill-0.2.2.zip` | 1003509 | `e0576f805465971ff94da93973d7d058724c2d8ea0ec94ccd67052c1ebaaba48` |
| skill | 0.2.2 | `dist/stalker-translate-skill-0.2.2.manifest.json` | 2927 | `9c381b6dcad863312a98f607550947f98ee3b79974a24e740c491a209f4bb449` |

## 兼容矩阵

| 组件 | 版本/要求 |
|---|---|
| core | 1.0.1 |
| plugin_2x | 2.0.29 |
| plugin_1_9x | 1.9.26 |
| mcp_hermes | 1.1.11 |
| mcp_online | 0.2.2 |
| skill | 0.2.2 |
| core | 1.0.1 |
| Python core | >=3.11 |
| Python plugin_2x | host-managed |
| Python plugin_1_9x | host-managed |

## 旧仓库切换

本仓库启用后，旧仓库只保留历史与跳转说明，不再同步源码：

- plugin_1_9x：https://github.com/qy0214/stalker-localization-toolkit-ai-translate（pending_archive）
- mcp_hermes：https://github.com/qy0214/stalker-translate-mcp（pending_archive）

归档前确认：主仓库 tag、五个分享包、SHA-256 与注册表条目均已发布。
