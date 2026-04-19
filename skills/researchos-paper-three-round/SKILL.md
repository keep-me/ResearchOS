---
name: researchos-paper-three-round
description: 对单篇论文执行或复用 ResearchOS 的三轮分析流程。用于用户要求系统化分析一篇论文，或需要粗到细、多轮结构化结论、汇报材料和综合判断时。
---

# ResearchOS Paper Three Round

目标是优先复用本地已有三轮分析，不重复计算。

推荐流程：

1. 调用 `get_paper_detail` 与 `get_paper_analysis` 检查已有三轮分析。
2. 如果已有结果完整，直接整理为回答。
3. 如果结果缺失、过旧或用户要求重新跑，调用 `analyze_paper_rounds`。

输出建议按以下结构组织：

- 研究问题与背景
- 方法与创新点
- 实验结果与证据
- 局限、风险与启发

如果触发了新的三轮分析，要在回复里明确说明这是执行了本地分析流程。
