# ScanSci 科研 Agent 架构（v0.4.0）

本文是 ScanSci 科研 Agent 的当前权威契约。目标不是把 ScanSci 变成无边界的通用编码 Agent，而是在可追溯、可复核、可恢复的科研业务边界内充分使用 Pi Agent SDK。

## P0 与历史状态

唯一当前 P0 是 `config/release-scope.json` 中的 v0.4.0。原 v0.3.1 P0 已完整冻结到 `release_history`，状态为 `superseded` / `frozen_unverified`：仓库没有可复核的 v0.3.1 tag 或通过的 release report，因此不得把冻结历史写成 released。

“100% Pi 能力”只表示本仓库声明的 13 项能力在权限边界内实现，并由 Task8 的 10 个报告轴验收；它不等于无限资源、无限上下文、无限并发、任意模型质量或整个 Pi/第三方生态的所有 extension。

13 项声明能力是：

1. protocol/feature 协商与 fail-closed 租约；
2. 所有需要工具、Skill、知识库、联网、附件或工作流能力的模型轮次 Pi-first 自主规划；普通 `direct_chat` 走 Host 精简快速通道；
3. 动态工具搜索、激活、撤销与逐次授权；
4. 只读并行、effect 顺序屏障、hooks 与取消隔离；
5. 模型感知长上下文、压缩和恢复；
6. 渐进式 Skill 发现与有界资源加载；
7. 最多三个只读科研子代理及结构化交接；
8. 队列、abort-compaction、close/load、clone/fork 等会话控制；
9. 延迟 MCP 目录、连接、真实 schema 与 effect 审计；
10. 受限多模态 Pi 消息与显式不支持降级；
11. 审批、路径、秘密、未知 effect 与 late-result 安全隔离；
12. 重试、只读缓存、幂等与断连/超时恢复；
13. run/effect/subagent/compaction 的可观测性和哈希绑定发布证据。

10 个报告轴是 `routing`、`dynamic_tools`、`parallelism`、`long_context`、`skills`、`subagents`、`mcp`、`multimodal`、`safety`、`observability`。能力实现与发布通过是两回事：缺少 real provider 凭据时可以完成确定性实现，但 provider-dependent 轴和正式发布仍为 `not_run`/blocked。

## 权责边界

```mermaid
flowchart TD
  USER["用户任务"] --> HOST["Host 权威层\nTaskContract / lease / evidence / persistence"]
  HOST <-->|"protocol v7 JSONL"| PI["Pi 编排层\nmodel loop / planning / context / sessions"]
  PI --> CAT["授权能力目录\nTools / Skills / deferred MCP / subagents"]
  CAT --> HOST
  HOST --> STORE["Workspace / Evidence / Research Runs / Artifacts"]
  HOST --> UI["流式 UI 与审计事件"]
```

### Host 权威层

Python Host 负责：

- 编译 `scansci.task-contract.v2` 和 capability lease，逐次校验 request/run/generation、风险、预算、调用次数和审批 token；
- 执行所有 ScanSci 工具、Workspace/Evidence/ResearchRun/Artifact 读写和稳定 `scansci://` URI 校验；
- 执行证据充分性、引用验证、科学改写/时间交付等后处理；这些护栏保留，但不能冒充 Pi 的模型能力；
- 把模型、工具、effect、子代理、MCP、压缩和降级事件写入有界、脱敏、可恢复的记录；
- 在危险或未知状态 fail-closed。空租约不暴露可执行工具，只有显式 `approve` 能产生 request-scoped approval token。

### Pi 编排层

Node sidecar 中的 Pi AgentSession 负责：

- 在 Host 授权目录内读取完整的模型感知上下文，自主规划路线；
- 使用 `search_tools` / `search_skills` / `load_skill` 逐步发现能力；
- 批量并行 thread-safe 只读工具，按顺序执行 effectful 工具；
- 使用 hooks、steer/follow-up、compaction、queue、abort、clone/fork 和 session resume；
- 委派安全科研子代理、使用延迟 MCP 和经验证的图像内容；
- 把工具请求发给 Host，而不是直接触碰业务数据库或任意主机能力。

Pi 不决定租约、审批、证据真伪、引用是否通过、Artifact 是否提交，也不能修改 Host 的完成判定。

### 不对任何模型开放的能力

- 不开放任意 shell、任意文件系统读写、Pi 内置 `bash/read/write/edit/find/grep/ls` 或任意第三方 extension；
- 不允许 Skill、MCP annotations、工具描述、模型文本或子代理结果改变 capability lease；
- 不把 Session JSONL 当作 Workspace/ResearchRunStore 的业务真相；
- 不把未经验证的模型自由文本保存为“已验证研究简报”。

## protocol v7 与 schema 迁移

当前 wire 只写 `protocol v7`，启动必须协商严格 required features；Node 和 Python 都校验消息大小、类型、schema、当前 request/run/generation、工具调用 ID 和结果归属。过期、未知、跨请求或 late result 不得进入当前历史或持久化结果。

迁移规则：

- TaskContract 只写 v2；旧 v1/整数版本只在一个兼容窗口内读取，再编译为当前租约，不能沿用旧权限语义；
- `research_runs schema v4` 采用 additive migration，保存已存在 run、stage、tool、artifact 和 session 引用；
- capability lease、model-runtime descriptor、subagent-result 与 mcp-effect 各自使用 v1 schema；未知字段不产生权限；
- 旧 eager/direct MCP 配置保留为显式兼容执行路径并继续逐次授权；它可能在构建 direct 工具目录时连接，不能作为“deferred 启动零连接”的证据。v0.4 新增/选择的 P0 路径使用 deferred 目录→连接→激活→调用；旧兼容路径不计 deferred capability 成功；
- 会话、checkpoint、artifact、evidence URI 和旧报告保留，不通过删除用户数据完成迁移；
- session 的“暂停”语义是 `abort + resume`，不是进程级 suspend。压缩可 abort 后恢复，不能宣称冻结了任意远端副作用。

## 工具、Skill、MCP 与子代理

### 动态工具与 effect

`allowed_tools` 是硬权限包络，`initial_tools` 只是初始活动子集；`required_tool_groups` 只约束完成判定，不会自动把整组工具定义加入模型输入。模型通过 `search_tools` 按需激活其余已授权工具，搜索、激活、调用和结果提交每一步都重新检查当前契约。只有明确标记 thread-safe/read-only 的调用可并行；任一 effectful sibling 形成顺序屏障。超时或取消后的 late result 被丢弃，文件、wire 与 history 结果必须原子提交。

Host 执行的非只读 Pi 内置工具在派发前用 SQLite 原子占位：键由 Host 用户轮次 `effect_scope`、工具名和标准化参数摘要组成。首次产生占位的 Pi request 独占该 scope 的后续写入；自动恢复或输出修复即使生成新的 protocol request ID、改写参数，也不能在原轮次执行新的副作用。已完成和结果不明的相同调用都拒绝自动重做。新用户轮次获得新 scope。账本不覆盖 Pi 直连 MCP effect；后者沿用各自的授权与幂等策略。

持久 `ask` 工作流的 tool 阶段以 `research-run:{run_id}:tool:{effect_epoch}` 作为 scope，阶段因数据库锁等瞬时错误重排时继续使用原身份；显式重启会持久生成新的 epoch。对外的科研续问、同步聊天与问答请求先以提交键持久占位；科研续问和同步聊天同键重发可回放已完成结果，同步问答只保存完成状态。科研任务与续问提交表同在 workspace SQLite，删除在一个事务内拒绝活跃执行并清除终态，失败整体回滚。同一 Pi request 内若以不同参数重发同一意图，账本仍无法可靠识别；同一轮次有意两次同参操作会被拒绝。完整区分这两种意图需要未来的显式 effect intent 身份。

当前日期只辅助检索近期材料，不能成为论文测量或采集年份。Pi 在同一轮已检索到本地证据后，写入 Word、PDF 或 PPT 前要求产物包含当前证据库中的完整 evidence_id；缺失或不存在的编号会返回可修正错误。对显式测量、采集等日期陈述，Host 只接受引用的证据句子本身出现的年份；该检查只覆盖显式年份，不声称完成全部事实核验。

### Skills

Skill 只改变指令，不改变租约、风险、证据来源或完成判定。显式 `$skill` 可以预载；推断候选只是提示，模型通过搜索/加载读取有界资源。Skill 路径必须在包根内，拒绝 traversal/symlink escape，记录 hash/provenance 并限制单次与累计字节。

### MCP

deferred MCP 服务器启动连接数为 0。模型先搜索有界目录，再按需连接、注册真实远端 schema、激活和调用。未知 effect 拒绝；annotations 只可提高风险，不能降低 Host policy。server raw ID、alias、remote name、transport、effect、duration、decision、digest 和有界结果引用进入审计。重试仅限 idempotent 调用，缓存仅限当前 run 的只读结果并服从 freshness。旧 direct/eager 配置只作兼容，可能立即连接且不计本轮 deferred 轴证据。

### 科研子代理

每个父任务最多 3 个并发 child。child lease 必须是 `role ∩ parent.allowed ∩ host-ready ∩ subagent_allowed ∩ read-only`，无 MCP、无外部/本地写入、无递归委派。每个 child 有独立预算、trace、取消与结构化 handoff，只能返回经 Host 校验的 `scansci://` evidence/artifact/run URI。一项失败不能取消 sibling；父 Agent 可收集部分有效结果或取消剩余任务。

## 上下文与多模态

上下文预算来自受信 model-runtime descriptor，而不是固定字符/轮数裁剪。Host contract、最终用户任务、显式 Skill、最近对话、attachments、recap 和被引用 tool result 按优先级进入 envelope；当前 tool-call/result 原子保留。长会话用可恢复 sidecar compaction，不销毁持久化业务记录。

图像只以通过 MIME、magic、base64、尺寸、像素、单项/累计字节和数量限制的内存内容过 wire；不接收路径或 URL，不把 raw base64 写入日志/manifest。支持图像的模型用 Pi 原生 image+tool 路径；unsupported 模型必须记录显式 degradation，并使用声明的 OCR/text 或 alternate-model 路径。该降级交付不计 Pi multimodal 成功。

## fallback、退化与完成判定

流式直接对话的 loopback HTTP 入口要求 `idempotency_key` 和 1–160 字符的 `conversation_id`，历史与提交入口使用相同的会话 ID 校验。浏览器先等待 Host 历史快照保存成功；Host 在进入 Pi 或直连模型前用独立的持久化提交表原子占位，终态可按同键回放；同键异内容、仍在执行或结果不明的提交不会再次执行。浏览器对流式 POST 只发送一次，暂停续跑和失败重试先查询原请求状态；数据库仍显示运行且所属进程存活时继续等待，所属进程确认退出后才转为结果不明。结果不明或查无记录时须用户核查并确认新分支；查无记录的原键先原子封存，防止迟到旧 POST 与新分支同时执行。该表保存请求摘要及有界终态，删除对话时先在提交事务中封存会话身份并擦除终态，再删除 JSON 历史；JSON 删除失败时保留封存状态供重试。所有历史写入口共用提交表写锁，阻断迟到旧 POST 和历史回写；启动时清理过期缓存。该提交表约束整轮请求重入。Pi 的 Host 内置 effect 另按用户轮次 scope、首个产生副作用的 Pi request、工具名和参数摘要在执行前持久占位；完成或结果不明时均不自动重做。同一 session 的新用户轮次有新的 scope，可明确重新提交。Pi 直连 MCP 继续使用其独立 effect 策略。

需要能力编排的模型介导文本默认 Pi-first。没有知识库、Skill、附件、联网或工具意图的 `direct_chat` 是计划内 Host 快速通道：使用精简系统提示，按用户要求组织回答；支持省略输出上限的模型接口由服务端决定单次长度，截断时执行有界续写。明确要求完整 HTML 页面或游戏时，即使服务端以 `stop` 结束，只要已生成的 `<html>` 尚未闭合，也按未完成处理并有界续写。完成判定只使用当前用户请求，不继承历史轮次的结束标记或 HTML 要求。对话请求固定携带本轮选中的 provider/model 标识，Host 在已配置且启用的模型目录中校验并执行，避免异步设置变化造成界面标签与实际模型分离；未携带该标识的旧客户端继续使用默认模型。该通道显式关闭自适应隐藏思考，不获得 capability lease，也不计入 Pi 能力证据。其他允许的 Host direct 路径包括不需要模型的确定性产品事实、模型调用前的 effect/安全拒绝，以及明确的安全降级交付；它们必须标记来源。

Pi 模型轮次若以 `stopReason=length` 结束，保留已流出的文本，并在同一 session 中携带断点末尾续写；最多自动续写 3 次。完整 HTML 页面提前以 `stop` 结束且未闭合时走同一有界续写流程，续写状态写入对话过程。每次续写仍经过原有模型请求、上下文压缩、租约和取消边界。截断的工具调用交给 Pi SDK 生成错误结果并继续工具循环，不执行不完整参数；文本续写只处理无工具调用的轮次。连续达到上限时明确失败，不能把残段标为完成。输出续写与任务完成判定分别处理，科研任务仍以 Host 的产物和证据检查为准。

普通对话和知识库问答将 Pi 或直连模型实际返回的思考文本或摘要增量作为 `model_reasoning` 事件转发，并在生成期间展开显示；用户手动收起后保持收起。知识库写作退回已核验答案时发送重置事件，清除未采用的模型思考。没有返回该字段的模型只展示真实工具进度。已采用回答的思考内容随本地对话历史保存，不能用推测内容填充。

任何 direct fallback 都不计 Pi 成功，`fallback_count` 必须为 0 才能通过 `scansci.pi-capabilities` report。模型/transport 不支持时记录 `degraded`，缺少真实 provider、凭据或外部环境时记录 `not_run`；不得把 deterministic mock、OCR 文本或 Host 改写包装成 provider-real 通过。

科研完成仍由 Host 检查：通过引用验证的终止工具、结构化 evidence gap 或获批准的专用交付工具。Pi 的自然语言结束不是业务完成证明。

## 发布证据

能力矩阵源是 `bench/pi_capability_tasks.json`（schema v2），能力报告遵循 `config/release-report.schema.json`：

- `schema_version=2`、`report_kind=scansci.pi-capabilities`、`mode`、`status`；
- `protocol_version=7`、`sdk_version`、`source_sha256`；
- `bundle.path/sha256/bytes`、`matrix.path/sha256/bytes/schema_version`；
- `fallback_count=0`、`run_manifests`、精确 10 个 `axes`、`provider` 与 mode-specific `evidence`。

`passed` 必须绑定当前源码、matrix、bundle、JUnit/runtime proofs 和至少一个 run manifest。deterministic 报告证明本地协议与工具循环；`dynamic_tools` 和 `multimodal` 的真实 provider 序列化仍要求 real 模式。未配置 provider 的 real 报告必须是 `not_run` 并阻塞公开发布。

## 不变的科研原则

1. 证据优先：没有来源锚点的结论标记为推测或待核验。
2. 权威状态在 Host：模型、MCP、Skill 与子代理都不能扩大权限或越过人审。
3. 可恢复：长任务从 Research Run、session 和 artifact 引用重建。
4. 可观察：每次调用、effect、委派、压缩、fallback 与失败都有有界记录。
5. 可替换：Pi 是运行时，模型、MCP、UI 与本地组件仍可独立更新。
6. 包边界不变：core 不携带 PyTorch/Transformers/模型权重；Node 与 local-transformers 是独立组件；完整 Windows ZIP 始终是更新回退入口。
