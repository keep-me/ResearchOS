---
name: research-pipeline
description: 一键串联 idea-discovery、auto-review-loop、paper-writing 的科研自动化流程。
---

# Research Pipeline Skill

当用户输入 `/research-pipeline` 或明确要“一键跑完整科研流程”时使用本 skill。

## 目标

把一个研究主题快速推进为可执行输出：

1. 方向与想法（Idea Discovery）
2. 批判式评审闭环（Auto Review Loop）
3. 写作交付包（Paper Writing）

## 推荐调用

优先调用工具 `research_pipeline`，并传入：

- `topic`
- `initial_context`（可选）
- `max_rounds`（默认 3）
- `executor_model` / `reviewer_model`（可选，跨模型协作时使用）

## 输出要求

最终结果应包含：

- 三阶段结论
- 每阶段关键风险
- 下一步可执行动作清单
