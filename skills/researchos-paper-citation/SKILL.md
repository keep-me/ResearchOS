---
name: researchos-paper-citation
description: 分析论文的引用关系、上下游工作与相关文献脉络。用于用户要求查看某篇论文引用链、前作后续、相关工作比较、citation tree 或文献定位时。
---

# ResearchOS Paper Citation

先定位目标论文 `paper_id`。

推荐流程：

1. 调用 `get_paper_detail` 确认目标论文。
2. 调用 `get_citation_tree` 获取上下游引用关系。
3. 如需补充上下文，可结合 `search_papers` 查找相关论文。

回答时优先说明：

- 关键前置工作
- 后续延伸工作
- 与目标论文最接近的研究脉络
- 可继续追踪的代表论文

不要只罗列标题；要解释它们之间的关系。
