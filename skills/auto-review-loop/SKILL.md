---
name: auto-review-loop
description: 对研究方案或草稿执行多轮批判式评审并产出修复闭环。
---

# Auto Review Loop Skill

当用户输入 `/auto-review-loop` 或要求“审稿式改进”时使用本 skill。

## 推荐调用

调用工具 `auto_review_loop` 并提供：

- `topic`
- `draft`
- `max_rounds`（默认 3）
- `executor_model` / `reviewer_model`（可选）

## 输出要点

- 审稿视角评分与致命问题
- Must Fix / Nice to Have 清单
- 多轮修复计划（每轮目标、实验、判据）
