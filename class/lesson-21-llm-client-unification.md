# 第 21 课：统一 LLM Client

## 1. 本课定位

这是进入 Agent/runtime 区域的第一课。你会看到，`ResearchOS` 并没有让每个业务模块各自直接调用模型厂商 SDK，而是专门做了一层 `LLMClient`。理解这层，是读懂后续 Agent、论文处理、写作服务的基础。

## 2. 学完你应该能回答的问题

- 为什么 `LLMClient` 必须存在。
- 多模型提供商抽象解决了哪些现实问题。
- 为什么配置缓存、模型解析、流式事件、embedding 配置会出现在同一个模块族中。
- 统一 LLM 层如何让上层业务保持稳定。

## 3. 学习前准备

- 阅读 `packages/integrations/llm_client.py` 前半部分。
- 浏览 `packages/integrations/llm_provider_registry.py`、`llm_provider_stream.py`、`llm_provider_resolver.py`。
- 回看 `packages/config.py` 中与模型相关的字段。

## 4. 详细讲解

### 4.1 统一抽象不是“多此一举”，而是隔离厂商差异

如果没有统一 `LLMClient`，各业务模块会直接面对：

- 不同厂商的鉴权方式
- 不同的 base URL 规则
- 不同的流式响应格式
- 不同的工具调用能力
- 不同的 embedding 接口差异

这样一来，论文处理、写作、Agent 会全都绑死在某个厂商 SDK 细节上。统一抽象层的价值，就是把这些差异挡在下层。

### 4.2 `LLMConfig` 说明模型调用不是单一开关

从 `LLMConfig` 可以看出，系统并不把模型调用看成单个“provider + api_key”：

- 有不同任务阶段模型
- 有视觉模型
- 有 embedding 配置
- 有 fallback 模型

这意味着模型策略本身就是系统的一部分，而不是随手写在业务函数里的参数。

### 4.3 为什么流式事件被正式建模

`StreamEvent` 非常值得你注意。它说明系统不只需要最终结果，还需要：

- 文本增量
- reasoning 增量
- tool call
- tool result
- usage 信息
- error 信息

这正是为什么后续 Agent 页面能做复杂流式渲染。你要认识到：流式事件模型是前后端协作协议的一部分。

### 4.4 配置缓存与动态配置的张力

`_load_active_config()` 里有配置缓存，但 TTL 默认又被关闭，以保证切换配置能立即生效。这说明项目在权衡两件事：

- 读取配置不要太重
- 配置切换又要及时生效

这类权衡是工程里非常真实的问题。不是一味缓存，也不是完全不缓存，而是根据产品行为调整。

### 4.5 统一 LLM 层带来的最大收益

真正的收益不是“代码更优雅”，而是：

- 论文业务和 Agent 业务共享同一套调用基础设施
- 配置、预算、超时、provider 能集中治理
- 前后续扩展新模型提供商时成本更低
- 上层模块能围绕“任务意图”而不是“厂商接口”编程

这正是平台型工程思路。

## 5. 参考代码对照

### 5.1 对照 `reference/claw-code-main`

`reference/claw-code-main` 也需要把助手能力和界面能力组织在一起。对照它可以帮助你理解：一旦系统进入助手和执行阶段，模型调用就必须变成正式基础设施，而不能散在业务函数里。

## 6. 代码精读顺序

1. `packages/integrations/llm_client.py`
2. `packages/integrations/llm_provider_registry.py`
3. `packages/integrations/llm_provider_resolver.py`
4. `packages/integrations/llm_provider_stream.py`
5. `packages/config.py`

## 7. 动手任务

1. 画出统一 LLM 调用层的结构图。
2. 列出直接调用厂商 SDK 会给上层模块带来的 5 个问题。
3. 解释 `StreamEvent` 为什么对前端 Agent 页面很重要。

## 8. 验收标准

- 你能说明统一抽象层的必要性。
- 你能解释流式事件与多阶段模型配置的意义。
- 你能理解“任务意图编程”比“厂商接口编程”更适合这个项目。

## 9. 常见误区

- 误区一：把 `LLMClient` 当成简单包装层。
- 误区二：只从代码复用角度理解统一抽象，而忽略运行策略与协作协议。
- 误区三：低估多模型与流式事件的复杂度。
