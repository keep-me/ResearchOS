# ResearchOS 命名边界

日期：2026-04-15

## 结论

新代码、用户可见文案和公开 API 统一使用 `ResearchOS`。历史名称只允许留在兼容层、迁移说明、第三方协议适配器或旧数据字段中。

## 命名映射

| 名称 | 状态 | 使用边界 |
| --- | --- | --- |
| ResearchOS | 当前品牌 | 用户界面、API 文案、文档、默认模块命名 |
| ResearchClaw | 历史/兼容名 | 仅保留在旧注释、旧路由兼容说明和迁移记录 |
| OpenCode | 上游/兼容协议 | 仅用于 OpenCode 后端、session protocol 或配置字段兼容 |
| Claw / ResearchClaw | Agent 兼容层 | 仅用于 legacy CLI backend、测试夹具和兼容变量 |
| ARIS | 工作流能力名 | 可作为内部 workflow/product capability 名，不作为全局品牌 |
| Amadeus | 历史提示词兼容 | 仅用于 `amadeus_compat` 和旧提示词迁移 |

## 新增代码规则

- 用户可见标题、按钮、toast、API response message 默认写 `ResearchOS`。
- 新模块名不再使用 `claw`、`amadeus`、`opencode`，除非模块职责就是适配该 legacy/backend 协议。
- 兼容层必须在文件头或模块名中说明 legacy 边界。
- 新测试可以引用旧名，但测试名应说明是在验证兼容行为。

## 渐进收敛清单

- 将用户可见 `ResearchClaw` 文案改为 `ResearchOS`。
- 将非协议性的 `claw` 变量重命名为 `agent` 或 `runtime`。
- 保留 OpenCode/Claw 作为后端类型枚举值，避免破坏既有 session 数据。
- 在 API 文档中标注 legacy aliases，禁止新增旧别名。
