---
name: idea-discovery
description: 围绕研究主题进行文献扫描、候选方向生成与批判式筛选。
---

# Idea Discovery Skill

当用户输入 `/idea-discovery` 或要求“先找方向/产出想法”时使用本 skill。

## 推荐调用

调用工具 `idea_discovery` 并提供：

- `topic`
- `top_k`（默认 12）
- `executor_model` / `reviewer_model`（可选）

## 输出要点

- 至少 8 个候选想法（含实验可行性）
- 评审模型批判意见（保留/合并/淘汰）
- 最终优先级路线图（建议先做 2-3 条）
