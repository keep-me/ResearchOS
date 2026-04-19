---
name: researchos-paper-figure-analysis
description: 提取和分析论文中的图表、表格与关键视觉区域。用于用户要求解释图表含义、比较趋势、提取表格结论、分析框选区域或需要图表级证据时。
---

# ResearchOS Paper Figure Analysis

先定位目标论文 `paper_id`。优先使用当前挂载论文，不要要求用户重新提供 PDF。

推荐流程：

1. 调用 `get_paper_detail` 确认论文存在且 PDF 可用。
2. 如果本地已有图表分析，优先复用已有内容。
3. 若缺少图表分析或用户要求重新提取，调用 `analyze_figures`。

输出时聚焦：

- 图表在表达什么
- 关键趋势、对比和数字
- 对方法或实验结论的支撑关系

如果只能基于已有图表文本或题注推断，要明确说明证据范围。
