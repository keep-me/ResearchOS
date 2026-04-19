# 第 28 课：其他业务页面与 API 服务层

## 1. 本课定位

学完 Agent 页面后，这一课的目标是建立“其余页面也有规律”的信心。虽然 Papers、Projects、Graph、Wiki、Settings 看起来差异很大，但它们大多遵循类似结构：页面层 + 服务层 + 共享组件 + 共享上下文。

## 2. 学完你应该能回答的问题

- 为什么 `frontend/src/services/api.ts` 是前端学习核心文件之一。
- 不同业务页面之间有哪些共同模式。
- 为什么共享组件和共享服务层能显著降低前端复杂度。

## 3. 学习前准备

- 阅读 `frontend/src/services/api.ts`。
- 阅读 `frontend/src/pages/Papers.tsx`。
- 阅读 `frontend/src/pages/Projects.tsx`。
- 浏览 `frontend/src/pages/GraphExplorer.tsx`、`Wiki.tsx`、`SettingsPage.tsx`。

## 4. 详细讲解

### 4.1 `api.ts` 是前端能力目录

从 `api.ts` 的类型导入和请求封装你会看到：

- 认证
- 论文
- 图谱
- Project
- Agent workspace
- MCP
- OpenCode/runtime

这说明它本质上是前端调用后端能力的统一目录。它的学习价值在于：

- 看一眼就知道前端能调哪些能力
- 能快速定位某个页面的数据来源
- 能理解错误处理和认证注入的统一方式

### 4.2 页面层的共同模式

不同页面虽然业务不同，但常有共性：

- 通过 `api.ts` 获取数据
- 自己维护加载态和错误态
- 组合通用组件
- 某些页面有页面级筛选、分页、局部弹窗

所以你学会一个页面的阅读方式后，可以迁移到别的页面。

### 4.3 Papers 和 Projects 为什么值得并列看

它们分别代表两种前端复杂度来源：

- `Papers` 更偏资产浏览、筛选、详情跳转。
- `Projects` 更偏工作流和执行状态。

把它们并列看，你会更容易理解“数据型页面”和“编排型页面”的区别。

### 4.4 服务层统一的价值

统一服务层至少解决了几件事：

- 基础地址解析
- 认证 header 附加
- 统一错误处理
- 请求方法封装
- 类型接口集中管理

如果没有这一层，页面会出现大量重复且不一致的 `fetch` 代码。

### 4.5 为什么其余页面也能反映产品演进方向

当你浏览更多页面时，其实能看到产品演进轨迹：

- 早期论文和知识页偏资产管理
- 后来的项目和任务页偏执行平台
- 设置页和工作区页偏操作系统式控制面板

前端页面本身就是产品路线图。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 强调统一工作台壳层下的多资产页面，这和 `ResearchOS` 逐渐形成的工作台前端很接近。对照它能帮助你更快把 Papers、Projects、Settings 这些页面放回统一产品心智里。

## 6. 代码精读顺序

1. `frontend/src/services/api.ts`
2. `frontend/src/pages/Papers.tsx`
3. `frontend/src/pages/Projects.tsx`
4. 任选一个 `GraphExplorer.tsx` 或 `Wiki.tsx`
5. 观察 `frontend/src/components/ui/` 与通用组件目录

## 7. 动手任务

1. 从 `api.ts` 中列出前端主要能力域。
2. 任选两个页面，对比它们的共同结构和差异来源。
3. 解释为什么服务层统一对大型前端尤其重要。

## 8. 验收标准

- 你能把 `api.ts` 当作前端能力地图来使用。
- 你能总结页面层的共同模式。
- 你能区分数据型页面与编排型页面。

## 9. 常见误区

- 误区一：把每个页面都当作独立孤岛来读。
- 误区二：忽视服务层统一所带来的结构优势。
- 误区三：只从 UI 外观而不是交互职责来理解页面。
