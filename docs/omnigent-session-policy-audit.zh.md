# ScanSci 会话恢复与工具裁决审计

> 2026-10-01。接续 [OmniGent 源码调研](omnigent-reference-study.zh.md)的第三批学习。依据固定的 [OmniGent 提交](https://github.com/omnigent-ai/omnigent/commit/74f94c3d55ae38623fc01c21ad2ea1823dbe8828)和当前 ScanSci 工作树追踪链路，并完成会话重试、工具裁决和回退边界的定向修补。本页不代表浏览器断线或真实 provider 已完成端到端验收。现行 v0.4.2、TaskContract v2、Pi protocol v7 与 Host 授权权责保持有效。

## 已有机制

| 路径 | 当前行为 | 可执行证据入口 |
| --- | --- | --- |
| 对话快照 | [`persistDirectChatJob`](../src/scansci_html/web/app.js) 按单个 job 串行提交快照；Host [`DirectChatHistoryStore.upsert`](../src/scansci_html/direct_chat_history.py)保存稳定消息 ID、队列、当前轮次、revision、单调事件序号和分支。 | `tests/test_direct_chat_history.py::test_direct_history_persists_current_turn_queue_and_recovery_status`；`tests/test_direct_chat_recovery_ui.py::test_direct_chat_job_snapshots_include_recoverable_queue_state`。 |
| 重开会话 | [`restoreDirectChatJob`](../src/scansci_html/web/app.js)将可恢复轮次设为 `paused`，由用户明确续跑；`openDirectConversation` 用 navigation generation 丢弃过期的历史请求。 | `tests/test_webapp.py::test_navigation_ignores_stale_conversation_fetches`；`tests/test_direct_chat_recovery_ui.py::test_retry_uses_a_new_session_and_preserves_the_failed_attempt_as_a_branch`。 |
| Pi 晚到事件 | [`_ProtocolDispatcher`](../src/scansci_html/pi_agent.py)按 request/command 路由，注销后保留 tombstone；command 的 generation 不匹配就丢弃。Host 授权器对跨 request 的工具调用拒绝执行。 | `tests/test_pi_agent.py::test_protocol_dispatcher_rejects_cross_generation_command_ack`；`tests/test_task_contract.py::test_tool_authorization_denies_missing_empty_spoofed_and_over_budget_calls`。 |
| Host 内部回退 | [`_pi_events_with_compatibility_fallback`](../src/scansci_html/research_agent.py)在可见文本、非幂等内置工具授权通过、非幂等 MCP effect 启动或 effect 已发生后停止回退，避免重做工作。 | `tests/test_research_runs.py::test_non_idempotent_tool_authorization_blocks_compatibility_replay`、`::test_non_idempotent_mcp_start_blocks_compatibility_replay`。 |
| 工具授权 | [`authorize_tool_call`](../src/scansci_html/tool_authorization.py)校验 request、租约、Host descriptor、风险上限、预算和 request 级批准 token；空答复与非字面 `approve` 不发 token。 | `tests/test_task_contract.py::test_approval_token_is_created_only_for_explicit_request_scoped_approve`；`tests/test_pi_agent.py::test_plan_approval_defaults_to_deny_without_explicit_approve`。 |

OmniGent 的 [session stream](https://github.com/omnigent-ai/omnigent/blob/74f94c3d55ae38623fc01c21ad2ea1823dbe8828/omnigent/runtime/session_stream.py)把实时队列限制为临时传输；重连依持久化快照接续，溢出时要求重新取快照。ScanSci 已有 Host 快照和显式续跑；当前静态证据尚不足以证明浏览器断线、Host 仍执行工具时的完整收敛。

## 已定位的边界与定向修补

### 1. 浏览器整轮自动重试

原有 [`streamChatWithRecovery`](../src/scansci_html/web/app.js)在普通对话失败时最多请求两次，主要依据错误类型、`failure.retryable`、限流与生图事件判断；它没有记录本轮是否已经收到文本、附件导入完成或工具结果。新请求会再次 POST `/api/chat/stream`。服务端 [`chat_stream`](../src/scansci_html/research_agent.py)每次创建新的 run ID，通用请求没有像明确生图路径那样以 `image_request_id` 去重。若首次请求已做过非幂等工具工作，随后 SSE 报错且错误满足前端重试条件，第二次请求可能重复执行。

浏览器现对流式 POST 只提交一次，且先等待当前轮次历史快照保存成功；服务端 [`DirectChatSubmissionStore`](../src/scansci_html/direct_chat_submissions.py)在模型和工具开始前原子占位，使用持久化请求键拦截并发与跨重启重放。完成的同键请求回放终态；执行中、异常中断、终态缓存过期或同键异内容的请求返回不可自动重试的明确错误。运行中的请求记录所属进程，只有进程确认退出才转为结果不明。提交表将有界终态绑定到 1–160 字符的 `conversation_id`；删除时先持久封存对话身份并擦除缓存，再删除 JSON 历史，后一步失败可重试清理且旧 POST 仍被拒绝。历史内容、标题、归档和恢复共用跨进程写锁与删除检查；无会话身份的旧缓存迁移时清除。定向验收场景如下：

1. 首次请求在任何 SSE 事件前遇到连接错误：只发一次 POST；重新提交相同请求键也不能再次进入 effect。
2. 已收到 `ready`、`RUN_STARTED`、文本 delta、附件或工具事件后遇到同类错误：只发一次 POST，保留已有内容与错误。
3. 暂停/取消导致 `AbortError`：不自动重试；续跑及失败回复的手动重试先查询原键状态。仍执行时等待，完成时恢复终态；结果不明或查无记录时须用户检查已产生的效果并确认新分支。查无记录时先原子封存旧键；若旧请求已抢先占位，新分支停止。空 `run_id` 不得控制其他正在执行的 Pi 请求。
4. `tests/electron/chat_stream_retry.test.cjs` 验证浏览器无隐式重发；`tests/electron/direct_chat_resume_submission.test.cjs` 验证暂停与手动重试的状态裁决、先保存后提交及原键封存；`tests/test_direct_chat_submissions.py` 注入首事件丢失、并发重复、重启后重放、迟到旧 POST、进程存活裁决、删除墓碑及空控制 ID；`tests/test_webapp.py` 验证 HTTP 缺键拒绝、状态读取、封存入口、完成结果回放和删除清理。真实浏览器和 provider 断线实验仍待完成。

Pi 的内置 effect 另由 [`ToolEffectLedger`](../src/scansci_html/tool_effect_ledger.py)在 Host 授权后、实际执行前持久占位。账本键从 Host 用户轮次 `effect_scope`、工具名与规范化参数摘要计算，侧车传来的 `_scansci_idempotency_key` 只作传输元数据且执行前移除；同轮自动恢复、输出修复、并发或跨客户端重复调用不会再次执行，完成或异常后都保留占位。首次产生副作用的 Pi request 成为该轮账本的唯一写入者；自动恢复生成的新 request 即使改写工具参数，也不能在旧 scope 内提交新副作用。新用户轮次生成新 scope。`tests/test_pi_effect_ledger.py` 覆盖跨 Pi request、参数改写、旧账本迁移、并发、结果不明、取消、SQLite 故障和授权桥接。Pi 直连 MCP 尚未接入该 Host 账本，其独立 effect 授权、审计及重试策略仍然有效。

### 2. Host 内置工具裁决的结构化链路

原有 Host 拒绝工具时，[`pi_agent.py`](../src/scansci_html/pi_agent.py)把脱敏错误写入 `context.history` 并发出 `tool.failed`；成功调用通过授权器后缺少同结构的 `ALLOW` 裁决记录，运行记录也难以将单次裁决与执行结果关联。

内置工具桥接现在由 Host 授权器产生稳定拒绝码，并在执行前发出 `tool.authorization` 的 `allow` 或 `deny` 事件。事件携带 request、契约和租约版本、Host descriptor 的风险与幂等性、规则码及批准状态；正常调用保存由 request、generation、call ID 计算的 `call_ref`，畸形重复/空 ID 的拒绝另用 Host 调用序号生成独立引用。审计字段不保存原始 call ID、参数、凭据或工具结果。Host 还在单个 request 内对首次收到的 call ID 占位，缺失或重复 ID 在派发前拒绝，防止同一 ID 执行两次并混淆关联。[`RunManifest`](../src/scansci_html/run_manifest.py)按字段白名单记录裁决和最终状态；直接对话的 `agent_runtime.tool_authorizations` 保留最近 64 条，`process_trace` 展示最近 8 条轨迹，并通过同一 `call_ref` 关联正常工具完成或失败。现有 `ASK` interaction 生命周期及默认拒绝规则未改变。

该投影目前覆盖 Pi 的 Host 内置工具桥接；MCP 使用已有独立 effect 审计链路，兼容回退现会识别其非幂等启动事件。其他科研工作流尚未统一展示逐次裁决。正常授权表示允许提交执行，最终效果仍以 `tool.completed`、`tool.failed` 和 Host 持久化记录为准。拒绝在派发前发生，批准超时或取消仍由既有 interaction 路径处理。

## 当前验收状态与下一步

- 前期会话/权限审计的定向用例均通过。本批 `node --test tests/electron/chat_stream_retry.test.cjs` 的 3 个动态用例及 `tests/electron/direct_chat_resume_submission.test.cjs` 的 11 个状态裁决用例通过，`node --check src/scansci_html/web/app.js` 通过。提交表的 11 个故障注入与状态用例通过；历史和流式 HTTP 入口先跑 9 个用例，严格删除清理改动后按影响范围复测删除路径，并新增损坏与不可读文件的故障用例。浏览器脚本未再修改，故未重复执行 Node 测试。发布门禁的 `targeted-python` 已纳入提交表测试。
- 权限边界已做一次独立只读专项审查；审查发现的 `RUN_STARTED` 后重试、重复调用 ID、非幂等 MCP 回退和畸形调用审计引用问题已按定向用例修补。审查未发现新增字段泄露工具参数或凭据，也未发现侧车可直接伪造 Host 裁决事件。
- 本轮补上请求级 Host 去重，首事件前传输失败不会触发隐式第二次执行。真实浏览器断线、真实 provider 和打包环境尚未验证；结果不明的旧请求需由用户检查历史后显式新建分支，跨不同请求键的外部 MCP effect 仍遵循各自 effect 策略。当前代码状态没有新的 10 轴通过报告。
- Pi 内置非只读工具增加用户轮次级持久占位；Host 创建的 `effect_scope` 穿过同一轮自动恢复、结构化输出修复和上下文修复，重复的工具名与参数被拒绝。跨 Pi request、并发、取消和 SQLite 故障注入的 9 个定向用例通过。该占位只覆盖 Python Host 执行的内置工具，真实 provider/打包运行与 Pi 直连 MCP 的跨请求效果仍待独立验证。
- 持久 `ask` 工具阶段使用 `run_id` 加持久 `effect_epoch` 固定 scope；自动重试沿用，用户显式重启生成新 epoch。科研续问、同步聊天和同步问答的 HTTP 入口要求提交键，并在工具前持久占位。科研续问浏览器保存未确认的原始请求体，同问题重试沿用该键；结果不明时须用户明确确认才能使用新键。同步聊天要求 `conversation_id`，同步问答只缓存完成状态。任务与续问提交表共用 workspace SQLite；删除在一个事务内排除活跃执行、删除任务并清除终态，失败整体回滚。结果回放、同键冲突、并发执行、结果不明和删除提交故障注入均有定向用例；真实浏览器及 provider 断线仍待实测。
- 当前内置账本以首个产生副作用的 Pi request 限定同轮后续写入，并按完整参数摘要识别同 request 重复。同一 Pi request 内以不同参数重发同一意图仍无法被可靠区分；同一轮有意重复完全相同的参数会被阻止。要同时满足这两种语义，需要由 Host 持久签发每个 effect intent 的身份或由工具接收幂等键；本批保持保守占位。
- 本批不变更 Pi/Host 权责、protocol v7 或 `config/release-scope.json` 的 P0；未经用户当前任务明确允许，不运行全量测试。
