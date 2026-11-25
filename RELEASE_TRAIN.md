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
| mcp_online | 0.3.0 | `dist/stalker-translate-online-MCP-0.3.0.zip` | 1001516 | `478200f102d303b7b148ebfd5f19d5231f9e7fb6024c4c8d1c395392d4e65929` |
| mcp_online | 0.3.0 | `dist/stalker-translate-online-MCP-0.3.0.manifest.json` | 4334 | `ea629af3da14b49d41162279b616883c5b98b21697e3280db0dbcb7c00b79fdb` |
| skill | 0.3.0 | `dist/stalker-translate-skill-0.3.0.zip` | 1014654 | `1f9e4cbbe88332029e2d4455dd25b0e72473f3475e716352ef9d737ad3bee736` |
| skill | 0.3.0 | `dist/stalker-translate-skill-0.3.0.manifest.json` | 3092 | `06a48c21d814fab47d12e1ff51d34d5ffd4075dd124c3e5566aec08f9f0ac78f` |

## 兼容矩阵

| 组件 | 版本/要求 |
|---|---|
| core | 1.0.1 |
| plugin_2x | 2.0.29 |
| plugin_1_9x | 1.9.26 |
| mcp_hermes | 1.1.11 |
| mcp_online | 0.3.0 |
| skill | 0.3.0 |
| core | 1.0.1 |
| Python core | >=3.11 |
| Python plugin_2x | host-managed |
| Python plugin_1_9x | host-managed |

## 旧仓库切换

本仓库启用后，旧仓库只保留历史与跳转说明，不再同步源码：

- plugin_1_9x：https://github.com/qy0214/stalker-localization-toolkit-ai-translate（pending_archive）
- mcp_hermes：https://github.com/qy0214/stalker-translate-mcp（pending_archive）

归档前确认：主仓库 tag、五个分享包、SHA-256 与注册表条目均已发布。
