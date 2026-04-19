---
name: researchos-topic-survey
description: 生成某个专题、关键词或研究方向的结构化综述。用于用户要求做专题综述、研究地图、方向概览、related work 草稿或某领域快速入门时。
---

# ResearchOS Topic Survey

当用户给出关键词、主题或论文 ID 时，优先调用 ResearchOS 内置综述能力。

推荐流程：

1. 明确用户给的是 `topic` 关键词还是具体 `paper_id`。
2. 调用 `generate_wiki` 生成专题综述或单篇结构化概览。
3. 如果会话已挂载论文，可把挂载论文作为综述切入点。

输出应尽量包含：

- 主题定义
- 代表工作
- 方法分支
- 最新趋势
- 后续可研究的问题

如果综述是围绕某篇已挂载论文展开，要明确说明关联方式。
