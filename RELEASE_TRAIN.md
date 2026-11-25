# STALKER AI Translate Suite 发布列车

- Release train：`2026.11.25`
- Git tag：`suite-v2026.11.25`
- 共享 core：`1.0.0`

## 产品与资产

| 产品 | 版本 | 资产 | 字节 | SHA-256 |
|---|---|---|---:|---|
| plugin_2x | 2.0.28 | `dist/ai_translate_plugin_v2.0.28.zip` | 1022471 | `501a964087ba8d1c75f12e3c47a6b6c8920e2f8cf0233180804286fb5eb92ef4` |
| plugin_2x | 2.0.28 | `dist/ai_translate_plugin_v2.0.28_install.zip` | 1022071 | `fa0df94c57717e53435f202b0e8f48e41bb9662874035ba92b3b393e07d382bf` |
| plugin_1_9x | 1.9.25 | `dist/ai_translate_plugin_v1.9.25.zip` | 1035929 | `d3ef2212fe0ca7b55fd7467ed0d4b0e83c059917b3a4a47f337ebb3c94205e02` |
| plugin_1_9x | 1.9.25 | `dist/ai_translate_plugin_v1.9.25.manifest.json` | 3701 | `f85862de6656a5d2077b57937f7ce3967a2020d3edce5d776a877c324d8aa3e4` |
| mcp_hermes | 1.1.10 | `dist/stalker-translate-MCP-1.1.10.zip` | 12131077 | `3b8e6816e6b965b5237fbbbd113430ad29274f37836189f31fe7b5c111809050` |
| mcp_hermes | 1.1.10 | `dist/stalker-translate-MCP-1.1.10.manifest.json` | 4806 | `3c022c10aa428ddb3170548ab5a262173e6757bc830dc2d39eba0489803e5644` |
| mcp_online | 0.1.0 | `dist/stalker-translate-online-MCP-0.1.0.zip` | 986940 | `0391c23e8052bb6dea4fc5109cb5b6730ed9bee15f552f453b1deea359e2474b` |
| mcp_online | 0.1.0 | `dist/stalker-translate-online-MCP-0.1.0.manifest.json` | 4202 | `05931ba916858312ae28bbe4f6dbd026d15996ab5c5103a428190fc95181f887` |
| skill | 0.1.0 | `dist/stalker-translate-skill-0.1.0.zip` | 991469 | `e10d887b5a745552da92d25e7518c3eb6ea27d1c3dbd131951469c9ad333c762` |
| skill | 0.1.0 | `dist/stalker-translate-skill-0.1.0.manifest.json` | 2823 | `44d71f63c77a814d9e8f93ac2a66e89b2679e4a974be4cefcc6e63bd56524466` |

## 兼容矩阵

| 组件 | 版本/要求 |
|---|---|
| core | 1.0.0 |
| plugin_2x | 2.0.28 |
| plugin_1_9x | 1.9.25 |
| mcp_hermes | 1.1.10 |
| mcp_online | 0.1.0 |
| skill | 0.1.0 |
| Python core | >=3.11 |
| Python plugin_2x | host-managed |
| Python plugin_1_9x | host-managed |

## 旧仓库切换

本仓库启用后，旧仓库只保留历史与跳转说明，不再同步源码：

- plugin_1_9x：https://github.com/qy0214/stalker-localization-toolkit-ai-translate（pending_archive）
- mcp_hermes：https://github.com/qy0214/stalker-translate-mcp（pending_archive）

归档前确认：主仓库 tag、五个分享包、SHA-256 与注册表条目均已发布。
