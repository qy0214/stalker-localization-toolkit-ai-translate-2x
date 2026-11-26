# STALKER AI Translate Suite 发布列车

- Release train：`2026.11.27`
- Git tag：`suite-v2026.11.27`
- 共享 core：`1.0.2`

## 产品与资产

| 产品 | 版本 | 资产 | 字节 | SHA-256 |
|---|---|---|---:|---|
| plugin_2x | 2.0.30 | `dist/ai_translate_plugin_v2.0.30.zip` | 957189 | `9ac081a753101f84b0089b97eeb270d7f24df2fe6d962598ed4d0e638f1fe5fc` |
| plugin_2x | 2.0.30 | `dist/ai_translate_plugin_v2.0.30_install.zip` | 956725 | `1564f79e14f13381a02755c1fe4a60d9fb1fb012e1984bee1ef3325137d48989` |
| plugin_1_9x | 1.9.27 | `dist/ai_translate_plugin_v1.9.27.zip` | 967988 | `799a9057b36dd919493ced11e599f79dc2c73b5227978ce8468932562e2a3a48` |
| plugin_1_9x | 1.9.27 | `dist/ai_translate_plugin_v1.9.27.manifest.json` | 4254 | `246aba85a7d68ebe7c790eba7b8a5d75495c15ca06991d317e452da31bc887c0` |
| mcp_hermes | 1.1.12 | `dist/stalker-translate-MCP-1.1.12.zip` | 12066404 | `c0a7df2cc5c358974a7315135b08dcbd6db01ec3843a8d29e249f5b0f3aac546` |
| mcp_hermes | 1.1.12 | `dist/stalker-translate-MCP-1.1.12.manifest.json` | 5343 | `bdf4d6d6d83debbb3b273335472e0f25917d758d80c94fcf22912b5a063ef413` |
| mcp_online | 0.3.1 | `dist/stalker-translate-online-MCP-0.3.1.zip` | 933702 | `fa510cc206fbd6db9cff31c246b6f7ac168b268c35f726fc69c166737f57b65a` |
| mcp_online | 0.3.1 | `dist/stalker-translate-online-MCP-0.3.1.manifest.json` | 4855 | `322e90479e0dbc3be0fec91167bb669740186e283d6d0fad5d70763ad949c2e3` |
| skill | 0.3.1 | `dist/stalker-translate-skill-0.3.1.zip` | 946907 | `278f0c3464aec6263d5ae5ab0b89caf3a25f959856ebeefd262e0cc232c06743` |
| skill | 0.3.1 | `dist/stalker-translate-skill-0.3.1.manifest.json` | 3294 | `e0f6895e0975f88998a3ff4ded540d66f3296797b21e323b21ea890fc0c24827` |

## Pending 产品（不进入当前列车）

- voice_plugin 0.1.0：骨架版 0.1.0；不纳入当前发布列车，TTS 管线完成并验证后再转 active。

## 兼容矩阵

| 组件 | 版本/要求 |
|---|---|
| core | 1.0.2 |
| plugin_2x | 2.0.30 |
| plugin_1_9x | 1.9.27 |
| mcp_hermes | 1.1.12 |
| mcp_online | 0.3.1 |
| skill | 0.3.1 |
| core | 1.0.2 |
| voice_plugin | 0.1.0 |
| Python core | >=3.11 |
| Python plugin_2x | host-managed |
| Python plugin_1_9x | host-managed |

## 旧仓库切换

本仓库启用后，旧仓库只保留历史与跳转说明，不再同步源码：

- plugin_1_9x：https://github.com/qy0214/stalker-localization-toolkit-ai-translate（archived）
- mcp_hermes：https://github.com/qy0214/stalker-translate-mcp（archived）

归档前确认：主仓库 tag、五个分享包、SHA-256 与注册表条目均已发布。
