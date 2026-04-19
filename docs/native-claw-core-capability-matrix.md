# Native / Claw 核心能力对照表

更新时间：2026-04-10

## 1. 目的

本文用于明确 `ResearchOS native host runtime` 与 `claw` daemon runtime 的核心能力差异，并作为后续“旧 agent 向 claw 靠拢”的执行基线。

这里的比较不是简单判断谁“更好”，而是识别：

- 哪些能力应该保留 `native` 现有优势
- 哪些能力需要把 `native` 向 `claw` 收敛
- 哪些差异短期只做适配，不做大重构

当前策略仍然是：

- 默认生产链路以 `native` 为主
- `claw` 保留为显式后端与行为参考
- 不做一次性“整体替换内核”，而是逐项收平 `context / runtime / tool loop`

## 2. 核心能力矩阵

| 能力域 | native host runtime | claw daemon runtime | 当前判断 | 当前策略 |
|---|---|---|---|---|
| 后端接入方式 | 内嵌在 ResearchOS 服务内，直接绑定 session/message store | 独立 CLI/daemon，通过桥接事件接入 | `native` 兼容性更强，`claw` 内核独立性更强 | 保持双栈，默认走 `native` |
| Session 持久化 | 原生持久化 `session / message / part / permission / diff / summary` | daemon 自带运行态，但不天然绑定 ResearchOS 会话存储 | `native` 明显更强 | 继续以 `native` 为主链路 |
| Prompt lifecycle / queue / callback | 已有完整 `SessionPromptProcessor`、busy queue、callback handoff、resume | daemon 以单次 prompt 执行为主，ResearchOS 侧仅消费桥接事件 | `native` 更适合项目内嵌场景 | 保留 `native` 所有权 |
| Permission / pause / resume | 已支持 pending action、确认、拒绝、resume continuation | `claw` 在当前接入里没有同等级的 ResearchOS 会话恢复语义 | `native` 更强 | 不迁移，继续保留 `native` 机制 |
| Transcript 组织方式 | 真实消息流 + provider prompt + system sections | CLI prompt 会先把完整对话转成显式 transcript 文本 | 两者各有优势；`claw` 在“跨轮语义恢复”上更稳 | 让 `native` 吸收 `claw` 的 transcript 恢复策略 |
| Mounted paper 上下文 | 已支持 mounted paper prompt、research lookup strategy | CLI prompt 直接注入 mounted paper 摘要和 figure-grounded 指引 | `claw` 在 figure-grounded turn 判定上更直接 | 已开始把 `native` 收敛到同等策略 |
| 图表/架构图导向策略 | 之前部分逻辑受 academic lookup gating 影响 | 只要 mounted paper + 图表型请求就直接触发 | `claw` 更稳 | 本轮已把 `native` 的 gating 拆开 |
| Tool exposure / permission policy | 已有 registry、read-only、plan-mode、permission rule set | daemon 依赖 MCP 暴露工具，桥接层较薄 | `native` 更细粒度，`claw` 更统一 | 保留 `native` policy，吸收 `claw` 的结果反馈风格 |
| Tool result 回流上下文 | 正常链路可回流为 `tool` 消息；历史孤儿结果恢复较弱 | `claw` 会把 orphan tool result 转成文本上下文继续喂模型 | `claw` 更鲁棒 | 本轮已把 `native` 补到同类恢复能力 |
| Tool-only turn 最终回复 | `native` 通常会继续 loop 到 assistant 文本 | `claw` 原先可能只跑工具后 `done.message` 为空 | `native` 更稳 | 已给 `claw` 增加兜底最终回复 |
| 多步 loop / step budget | 已有 step budget、step finish、step limit summary、auto compaction | daemon 自带内部多步 loop，但当前桥接出的控制面较薄 | `native` 控制面更适合集成，`claw` 内核策略更成熟；本轮已共享预算文案与 step-limit summary policy | 继续把剩余 stop / continue heuristic 向 `claw` 靠拢 |
| Auto compaction threshold / 开关 | 现在通过共享 policy 读取统一配置，并驱动 native overflow / preflight 判断 | bridge 启动 daemon 时注入同一份 env 阈值 | 这一层已经开始共享，不再各自硬编码 | 继续保留共享 policy，后续再决定是否进一步统一 compaction 结果格式 |
| Provider/tool 事件流 | 事件持久化、bus、前端可增量消费 | bridge events 清晰，但最终只是一层桥 | 两者都可用 | 保持现状 |
| 全链路测试覆盖 | 已有大量 `test_agent*.py` 回归 | `claw` 本体在上游；ResearchOS 侧主要桥接回归 | `native` 更容易做产品级回归 | 继续以 `native` 为验收主线 |

## 3. 结论

结论不是“把 native 改成另一个 claw”，而是：

- `native` 继续负责项目内嵌所需的会话所有权
  - session 持久化
  - permission/pause/resume
  - bus/lifecycle
  - 前端兼容
- `claw` 继续作为内核行为参考
  - prompt transcript 的恢复性
  - mounted paper 图表导向策略
  - tool result 变成后续上下文的鲁棒性
  - daemon 事件语义

因此后续真正要做的是：

1. 保留 `native` 的宿主能力
2. 吸收 `claw` 在 `context / runtime / tool loop` 上更稳的行为
3. 避免大规模重写或一次性内核迁移

## 4. 本轮已完成的对齐项

### 4.1 默认执行链恢复为 native

- 默认 backend 已从强制 `claw` 切回 `native`
- session 新增 `agent_backend_id` 持久化
- `researchos_native` 旧别名统一归一化到 `native`
- 显式 `claw` 会话仍然可保留并复用

### 4.2 mounted paper figure-grounded 策略向 claw 靠拢

之前 `native` 的图表/架构图策略挂在 academic lookup prompt 下面，会被“不是典型论文检索措辞”的请求绕过。

本轮已改为：

- 只要满足 `mounted_paper_ids + figure-grounded request`
- 就单独注入 mounted paper turn guidance
- 不再依赖 academic lookup 前置判定

这让类似下面的请求也会正确触发图表策略：

- “这个 encoder 和 decoder 是怎么交互的？”
- “请结合原图解释这个架构图”

即使这类请求没有显式出现“论文 / 文献 / arXiv”等关键词。

### 4.3 orphan tool result 恢复向 claw 靠拢

`claw` 在把历史消息重新喂给模型时，如果发现某个 tool result 缺少对应 tool use，会把它转成文本上下文继续保留语义。

本轮 `native` 也补上了这条能力：

- 如果 `tool` 消息没有 `tool_call_id`
- 或者 `tool_call_id` 在当前 transcript 中找不到对应 assistant tool call
- 就把该结果恢复为一条文本上下文，而不是直接把孤立 `tool` role 生硬传给 provider

这样能减少：

- 历史会话恢复不稳
- 外部 transcript 导入后 tool context 丢失
- 旧链路/异常中断后下一轮模型看不懂历史工具结果

### 4.4 claw tool-only turn 最终回复兜底

本轮前已经修过：

- `claw` 如果只产出 `tool_result`，但 `done.message` 为空
- 会在桥接层合成一条最终文本回复

这直接修复了：

- 导入论文后分析论文
- 前端只看到工具调用
- 最后没有 assistant 回复

这一类用户可见问题。

### 4.5 shared turn context 已开始收口

本轮继续把 `native` 与 `claw` 原先分离的 turn-level context 开始收口：

- 已抽出共享的 `turn context sections`
- `native` system prompt 和 `claw` CLI prompt 现在都会复用同一组上下文段
- 当前共享的段包括：
  - environment
  - skills
  - tool binding
  - reasoning profile
  - repo lookup strategy
  - mounted paper summary
  - mounted paper turn guidance
  - academic lookup strategy

这意味着后续继续对齐时，不需要再同时改两套 mounted paper / tool binding / reasoning prompt 逻辑。

### 4.6 claw 源头完成态也已补强

除了 Python bridge 层的 fallback，本轮还在 vendored `claw-code` Rust 源码中补了源头修复：

- 当一个 turn 没有 assistant text
- 但已经有 `tool_result`
- `final_assistant_text(...)` 现在会回退生成纯文本工具结果摘要

这样修完后：

- daemon 自身的 `done.message` 不再轻易为空
- bridge 层 fallback 仍然保留，形成双保险

这两层同时存在，可以最大限度降低“工具执行完就结束但没有回复”的复发概率。

### 4.7 shared transcript recovery 已进一步落地

本轮继续把 `native` 和 `claw` 的会话转录语义往同一层收：

- 新增共享 helper：`packages/ai/agent_transcript.py`
- `native` 的 orphan tool result 恢复不再只是 `agent_service.py` 内部私有逻辑
- `claw` 的 ResearchOS CLI prompt 组装也开始复用同一套工具结果恢复/摘要规则

现在 CLI prompt 不再只是“把历史消息原样拼成字符串”，而是会额外保留：

- assistant 发起过哪些 tool call
- tool call 的核心参数
- 有配对的 tool result 摘要
- 无配对的 orphan tool result 恢复文本

这意味着 `claw` 作为外接 daemon 时，ResearchOS 侧传过去的历史 transcript 语义更接近 `native` 真实会话，也更接近 `claw-code` 自身 `convert_messages(...)` 的恢复思路。

### 4.8 claw done.tool_results 兜底链路已补齐

之前 Python bridge 层只会在流式阶段收集到 `tool_result` event 时，才合成 fallback 最终回复。

本轮进一步补齐：

- 如果流式阶段没有单独收到 `tool_result`
- 但 `done` payload 自带 `tool_results`
- ResearchOS 侧仍然会根据 `done.tool_results` 合成最终 assistant 文本

这让“工具执行完就结束但没有回复”的修复，从“依赖中间事件完整送达”变成了“只要最终 done 带回工具结果，也能兜底出回复”。

### 4.9 shared runtime policy 已落成独立模块

本轮继续把 `native` 和 `claw` 共同依赖的 loop policy 从 `agent_service.py` 私有逻辑中抽出来，落到：

- `packages/ai/agent_runtime_policy.py`

当前已经共享到这一层的核心策略包括：

- `max tool steps` 预算计算
- reasoning profile 中的 tool budget 文案
- 最后一步前注入的 `max-steps` 提醒判定
- step-limit summary 的统一提示词
- auto compaction 的统一阈值计算
- `agent_compaction_auto` 开关对 native / claw 两侧的共同生效

对应落地效果是：

- `native` loop 继续保留自己的宿主控制面，但预算与 summary policy 不再完全私有
- `session_compaction` 不再写死单独阈值，而是复用共享 compaction threshold
- `claw_runtime_manager` 启动 daemon 时，会注入统一的 `CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS`
- 如果关闭 `agent_compaction_auto`，native 的 preflight / post-step auto compaction 与 claw daemon 的 auto compaction 都会一起收敛到“基本关闭”的行为

### 4.10 native 的 tool-only stop heuristic 继续向 claw 靠拢

之前 `native` 在下面这类场景里，虽然不会错误地再次执行本地工具，但仍可能停在“只有工具调用 / 工具结果，没有最终 assistant 文本”的状态：

- provider-executed builtin tool 已经返回结果
- 本轮没有新的本地可执行 tool call
- 模型也没有补出最终自然语言回答

这类情况在 `claw` 侧已经有 `final_assistant_text(...)` fallback。

本轮 `native` 也补上了同类 stop heuristic：

- 如果本轮没有可继续执行的 tool call
- 但已经拿到了 provider-executed tool results
- 且 assistant 没有正文

那么 `native` 会主动基于工具结果合成一条最终摘要文本，而不是直接 silent stop。

这样做的收益是：

- `native` 的“最终回复收口”行为更接近 `claw`
- provider builtin tools 不再容易留下“只显示工具、不显示回答”的尾部状态
- stop / continue 的判定从“只看还有没有 tool_calls”进一步收敛到“还要看这一轮是否已经形成可向用户交付的最终文本”

### 4.11 tool-preamble stop heuristic 也开始共享

上一轮补的是“完全没有 assistant 文本”时的兜底。

但还有一类更隐蔽的问题：

- provider-executed tool 前，模型先吐一句很弱的前导话术
  - 例如“我先帮你查一下”
  - “Let me search for that first”
- 后面工具已经完成
- 但模型没有继续产出真正的总结

这会导致 native / claw 虽然“ technically 有回复”，但用户看到的只是工具前导语，仍然像是停在半路。

本轮继续补上这一层：

- 新增共享 heuristic：识别 `tool progress placeholder text`
- native 在同轮内会尽量缓存这类前导语
  - 如果后面真的进入 tool call / tool result，就不把这句前导语当最终正文保留下来
  - 如果最后没有进工具，它仍会正常回放，不影响普通回答
- 如果本轮最终只剩这类弱前导语，而工具结果已经拿到
  - native 会补上一段基于 tool result 的最终摘要
  - claw bridge 侧也会用同类规则在尾部补 summary

这让 stop / continue heuristic 又往 claw 的“最终可交付文本”逻辑靠近了一层：

- 不再把“我要去查一下”这种过程话术误判成最终答复
- 工具链完成后，更稳定地收口到真正对用户有用的回答
- native / claw 在 provider builtin tool 场景下的尾部行为更接近

### 4.12 transcript/prompt shaping 又收了一层

上一轮里，`native` 和 `claw` 虽然已经共享了 turn context sections 与 tool transcript recovery，但还有一层用户态 prompt shaping 没对齐：

- `native` 会显式读取最后一条 user message 里的：
  - `system`
  - output constraint
- `claw` CLI prompt 之前只用了：
  - transcript
  - turn context sections

这会导致一种残余差异：

- 同一条用户消息如果带了额外系统指令
- 或者带了“最多 60 字 / 不超过 N 行”之类的硬输出约束
- `native` 会显式遵守
- `claw` prompt builder 之前不一定能同等显式地看到这一层

本轮已补成：

- 抽出 shared latest-user prompt shaping helper
- `native` 的 `_normalize_messages(...)` 与 `claw` 的 `_build_cli_chat_prompt(...)` 现在都复用同一份：
  - latest user request
  - latest user tool binding
  - latest user system
  - latest user output constraint

对应效果是：

- `claw` CLI prompt 不再只拿 transcript 本身
- 还会把最后一条用户消息携带的附加系统指令和输出硬约束显式抬升出来
- 这一层与 `native` 的 provider-style prompt shaping 已经更接近

### 4.13 native 最后一轮工具预算现在会硬停

之前 `native` 已经有：

- max-steps reminder prompt
- step-limit summary

但还缺一个真正的“硬停止”：

- 到了预算保留的最后一轮
- model 仍然继续请求本地工具
- `native` 之前主要还是靠 prompt 提醒模型自己停
- 如果模型不听，ResearchOS 仍可能真的把这一步工具执行掉

本轮已补成：

- 共享 policy 新增 `should_hard_stop_after_tool_request(...)`
- native loop 在“最后一轮仍请求本地工具”时，不再继续执行该工具
- 而是：
  - 立刻为这些未执行的 tool call 合成 `skipped` tool result
  - 追加 step-limit notice
  - 直接进入最终总结

这样做的好处是：

- stop / continue heuristic 不再只是提示词层面的“软约束”
- 即使模型忽略最后一轮提醒，也不会真的再多执行一轮本地工具
- 持久化 transcript 不会留下悬空 pending tool call，而是明确记录“因预算上限跳过”

另外，fallback step-limit summary 现在也会跳过“我继续查一下”这类弱过程话术，不再把这类文本误当成最后的关键信息。

### 4.14 repeated identical tool-call hard-stop 也补上了

上一轮 stop heuristic 主要解决的是：

- 工具预算打满后怎么停
- provider builtin tool 只有工具结果时怎么收口
- tool-preamble 这种弱前导语不要误判成最终答复

但还有一类 native / claw 之间很典型的残余差异：

- 第一轮工具已经执行成功
- 第二轮模型又发出了完全相同的工具请求
- 如果宿主层不介入，native 可能会真的再执行一次
- `claw-code` 的实际行为通常更倾向于在这种场景里尽快总结，而不是无限重复同一步

本轮已补成：

- shared runtime policy 新增：
  - `tool_call_signature(...)`
  - `should_hard_stop_after_repeated_tool_calls(...)`
  - `build_repeated_tool_call_notice()`
- native loop 会对相邻两轮的 tool request 做稳定签名比较
- 如果检测到“连续重复且参数完全一致”的工具请求：
  - 第二轮不再真实执行工具
  - transcript 中会落一条 `skipped` tool result，原因是 `duplicate_tool_call`
  - 然后立即基于前面已经拿到的真实 tool result 生成最终总结

这样收口后，native 在下面这类问题上会更接近 claw：

- 避免重复 bash / 检索 / 读取类工具被无意义地再次执行
- 避免会话停在“又调用了一次同样工具”的空转状态
- 把 stop / continue heuristic 进一步从“看预算”扩展到“看是否已经陷入重复执行”

### 4.15 shared CLI prompt text builder / tool-result followup text 也统一了一层

之前虽然 transcript recovery 已经共享，但 native / claw 仍有两层实现差异：

- `claw` 的 `_build_cli_chat_prompt(...)` 还有一部分 prompt text 拼装是 bridge 私有逻辑
- tool 执行完后的 followup / fallback final text，在 native 与 claw 两边也各有一份近似实现

本轮继续抽出到共享层：

- `packages/ai/agent_transcript.py`
  - `build_cli_chat_prompt_text(...)`
  - `collect_tool_result_items_from_messages(...)`
  - `ToolResultFollowupText`
  - `resolve_tool_result_followup_text(...)`

现在对齐后的效果是：

- `claw` CLI prompt 文本组装不再自己维护一份独立模板
- latest-user prompt shaping、turn context sections、transcript recovery 之外，连 CLI prompt 最终落成文本的方式也开始共享
- native 在 tool-only / placeholder / repeated-tool-call 场景下合成最终答复时，会和 claw bridge 复用同一套 tool-result followup 语义

也就是说，这一轮不只是把 heuristic 往 claw 靠，而是把“工具结果之后怎么组织最终回复文本”这一层也拉到了共享模块。

### 4.16 前端 tool transcript 去重也补齐了一层

之前研究助手里还残留一类非常影响体感的现象：

- 同一个 tool step 会先以本地 transient 卡片出现一次
- session 持久化 part 回来后又再渲染一次
- 用户会看到“工具一下出来两个，结束后又合并成一个”

这不是模型真的执行了两次，而是前端对 `local stream item` 和 `persisted session part` 的合流不够稳。

本轮已补成：

- `step_group` 也纳入 transient suppression
- 如果持久化消息里已经包含同一 `tool call id`
  - 前端会抑制本地临时工具卡片
- 如果 `session.message.part.updated` 早于 `session.message.updated`
  - 前端会先补一个 assistant message shell
  - 避免持久化 tool part 因为“消息壳还没到”而丢失

这让 native 主链在工具执行可视化上更接近 claw 期望的单一 transcript 语义：

- 用户只看到一份稳定的工具轨迹
- 不再因为事件先后顺序导致视觉上的“重复执行”
- 会话刷新后工具步骤也更容易完整恢复

## 5. 当前仍存在的差距

下面这些差距仍然存在，但不建议现在做“大重构”。

### 5.1 Prompt shaping 仍是双实现

- `native` 走 provider-style system sections
- `claw` 走 transcript-to-text prompt builder

两套实现都在表达同一类策略。本轮之后，已经共享了：

- turn context sections
- tool result recovery / fallback 摘要语义
- CLI transcript 的 assistant tool call / tool result 转录层
- latest user request / tool binding / system / output constraint shaping

剩余差距主要在：

- `claw` 仍然是纯 transcript prompt builder
- `native` 仍然是 provider-native message array
- 两边还没有共享到同一份“完整 provider transcript composer”

### 5.2 Tool result 的用户态摘要格式仍不统一

- `claw` Rust 侧有自己的 `format_tool_result(...)`
- `native` Python 侧主要依赖 `ToolResult(summary, data)` 与前端 part 渲染
- Python bridge 层虽然已经开始共享 fallback summary helper，但还没有和 Rust 侧完全统一成同一份格式规则

现在两边“语义接近”，但还不是同一套格式化规则。

### 5.3 Step stopping / continuation heuristic 还未共享

- `native` 已有 `step budget / step limit summary / auto compaction`
- `claw` 有自己更成熟的 daemon loop heuristic

本轮之后，下面这些已经共享：

- step budget 配置来源
- reasoning profile 中的 budget 文案
- step-limit summary prompt
- 最后一轮本地 tool request 的 hard-stop 判定
- auto compaction threshold / env 开关
- provider builtin tool 的 tool-only fallback
- tool progress placeholder 的识别与尾部 summary heuristic

但真正的：

- 什么时候继续下一步
- 什么时候只做总结停止
- 多工具 turn 后的 stop / continue heuristic
- daemon 内部更细粒度的 `stop / continue / retry / self-correct` 状态机

仍然还没有完全共用同一套 runtime，只是行为已经比前一轮更接近。

### 5.4 结构上仍然偏平

对照 `PaperMind` 更整洁的结构，ResearchOS 现在的 agent 核心仍然主要堆在：

- `packages/ai/agent_service.py`
- `packages/ai/session_processor.py`
- `packages/ai/session_runtime.py`

这对短期迭代是安全的，但长期不够清晰。

### 5.5 现在还不建议直接删除 `claw`

这轮桌面端 host smoke 已经证明：

- `native` 默认路径是可工作的
- ACP confirm / reject / abort 也已经打通
- 前端在 `native` 默认后端下的完整桌面端交互没有再出现“工具后直接结束”这一类明显断链

但这不等于 `native` 和 `claw` 已经“同一套 runtime”。

当前更准确的判断是：

- 行为差距已经从“明显不稳定”收敛到“多数场景下可替代”
- native 在 prompt shaping、tool-result followup、重复工具硬停这几层已经明显向 claw 靠拢
- 但内部仍然不是完全同构
- 如果现在直接删 `claw`，一旦后面要回查某些 transcript / tool-format / continuation 边缘行为，会失去一个现成对照实现

所以当前建议是：

- 继续以 `native` 作为默认主路径
- 保留 `claw` 作为对照实现和回归参照
- 等 prompt composer / tool-result formatter / continuation heuristic 再收一轮后，再删除 `claw`

## 6. 下一轮建议

下一轮建议按下面顺序继续，不要跳着做：

1. 继续推进共享 loop policy 配置
   - step budget
   - stop / continue heuristic
   - tool-only turn fallback
   - step limit summary policy

2. 继续推进 Rust / Python tool-result formatter 收口
   - 对齐 orphan tool result
   - 对齐 tool-only turn fallback text
   - 决定是否把 Rust `format_tool_result(...)` 与 Python helper 再进一步统一

3. 最后再考虑结构整理
   - 参考 `PaperMind` 的清晰边界，但不直接大搬目录
   - 可以先在 `packages/` 下增加薄的 `agent_core/`
   - 优先拆：
     - `context.py`
     - `runtime_policy.py`
     - `tool_loop.py`
     - `transcript_recovery.py`

## 7. 当前文档对应的代码落点

- backend 归一化与双栈选择：`packages/ai/agent_backends.py`
- shared loop / compaction policy：`packages/ai/agent_runtime_policy.py`
- native / claw 路由分发：`packages/ai/agent_service.py`
- session backend 持久化：`packages/ai/session_runtime.py`
- session backend 存储模型：`packages/storage/models.py`
- session backend migration / repository：`packages/storage/db.py`、`packages/storage/repositories.py`
- 路由层 backend 透传：`apps/api/routers/session_runtime.py`、`apps/api/routers/agent.py`

## 8. 验证基线

当前 agent 全链路回归基线：

```text
python -m pytest <all test_agent*.py> -q
204 passed, 14 skipped

corepack pnpm --dir frontend build
passed

desktop host smoke
- assistant new conversation button creates a routed conversation shell: passed
- projects workspace supports creating a project through the ui and shows the desktop workbench layout: passed
- assistant custom ACP confirm flow survives refresh and session switching: passed
- assistant custom ACP reject and full access auto-allow flows work: passed
- assistant ui reflects an external abort for a paused custom ACP prompt: passed
- full `frontend/tests/smoke.spec.ts`: 19 passed
```

这份基线应作为后续继续向 `claw` 靠拢时的最低回归要求。
