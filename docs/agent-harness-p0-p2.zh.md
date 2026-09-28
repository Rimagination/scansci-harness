# ScanSci Harness 与 Pi 能力门禁（v0.4.2）

`scansci-harness` 是 ScanSci 自有控制面，负责规范化 turn、校验 TaskContract、选择并管理执行驱动、绑定会话与宿主状态。Pi 是当前唯一生产执行驱动，负责成熟的模型/工具循环。Python Host 继续拥有权限、证据、持久化与发布权威。PydanticAI、OpenAI Agents SDK 和 LangGraph 仍只是可选适配，不在启动时强制导入，也不计入本轮 Pi 成功。

## 自有边界与兼容标识

- `scansci_html/scansci_harness.py` 定义 `scansci.harness.v1`、规范化 turn、驱动生命周期和公开能力描述；描述中不得出现 API key、提示全文或消息正文。
- 文献获取采用外部 MCP provider contract。Harness 保存 `provider_kind`、有界 `provider_manifest` 和 Host-owned tool effects；provider 的源码、网络策略和版本继续由独立插件维护。
- `literature` provider 的稳定语义 id 是 `discover_papers`、`verify_doi`、`download_papers`。插件工具名通过 manifest 映射到语义 id，完成账本按语义 id 计数，因此插件内部策略更新不要求同步改 Harness。
- `download_papers` 的 MCP effect 是独立的 `reversible` 类别；用户显式打开 `allow_reversible` 后才会暴露下载产物工具，普通 `write`/`destructive` 仍走原有高风险审批。它只表示外部 provider 已交付下载产物，不宣称 ScanSci 已完成 ResearchRun、全文清洗或证据入库；内置 `download_and_index` 继续表示宿主自有的完整流程。
- 进入驱动前必须存在用户消息，并通过 `scansci.task-contract.v2` 校验；未知驱动、未知 schema 或失去 authority 的契约直接拒绝。
- 生产驱动固定为 Pi protocol v7。Host 通过 `ScanSciHarness` 调用 Pi，不再由顶层对话路径直接构造 Pi 客户端。
- 外部运行结果继续保留 `harness: pi-agent-sdk`，用于兼容已有客户端和诊断；新增 `control_plane.id: scansci-harness` 表达产品控制面。两者分别描述控制面和执行驱动。
- Pi 事件在本里程碑保持原协议透传，避免破坏流式、工具、session、compaction 和取消语义；ScanSci 会话事件由直接对话状态契约 v2 独立持久化。

## protocol 与恢复

- wire 只写 `protocol v7` 并严格协商 required features；旧消息或契约只读迁移，不能降级权限。
- TaskContract 写 v2/读旧版一个兼容窗口；`research_runs schema v4` additive migration 保留 run/session/stage/tool/artifact。
- capability lease、model-runtime descriptor、subagent-result、mcp-effect 分别是 v1；未知字段无授权含义。
- 队列 inspection/clear、abort-compaction、close/load、完整 clone 和 entry fork 都有 request/generation ack。运行时只承诺 `abort + resume`，不把 abort 写成 suspend。
- 每个 Pi run manifest 保存脱敏 prefix shape、工具/Skill/MCP/模型 hash、token/cache/context breakdown，以及有界 event type/name/status；不保存系统提示全文、raw image、工具大结果或密钥。`fallback_count`/degradation 汇总属于 capability report，科研资源引用与 effect 详情分别属于 ResearchRun/Artifact 和 effect audit，不能混写成每个 manifest 的字段。
- Host 会为每个 turn 标注 `capability_profile`：`minimal`（普通对话）、`focused`（按需工具）、`workflow`（可恢复工作流）或 `approval`（需要确认）。它用于上下文装配、界面摘要和审计，不能扩大 `allowed_tools` 或 capability lease。

## 10 轴验收矩阵

`bench/pi_capability_tasks.json` schema v2 是唯一矩阵：

| P0 acceptance ID | report axis | threshold | 关键门槛 |
|---|---:|---:|---|
| `pi-routing` | `routing` | 40 | 40 个双语模型轮次 Pi 可达，silent fallback=0 |
| `pi-dynamic-tools` | `dynamic_tools` | 10 | 10 次真实会话搜索/激活/调用，撤销 100% 拒绝 |
| `pi-parallelism` | `parallelism` | 3 | 三轮 3×2s 只读并行 ≤3.5s，concurrency≥3 |
| `pi-long-context` | `long_context` | 20 | ≥100K token/20 turns，20/20 sentinel |
| `pi-skills` | `skills` | 20 | 20 个选择/加载用例，租约扩张=0 |
| `pi-subagents` | `subagents` | 3 | 最多 3 child，严格只读子租约与失败隔离 |
| `pi-mcp` | `mcp` | 10 | deferred stdio+HTTP 启动连接=0，10/10 调用；direct 仅兼容且不计轴证据 |
| `pi-multimodal` | `multimodal` | 10 | 10 个 real provider image+tool，unsupported 显式降级 |
| `pi-safety` | `safety` | 128 | 128 对抗用例，write/traversal/secret leak=0 |
| `pi-observability` | `observability` | 10 | run/effect/subagent/compaction 有 ID、时序、决策与引用 |

`report schema v2` 要求精确 10 轴、`protocol_version=7`、SDK/source/bundle/matrix hash、`fallback_count=0` 和 run manifests。deterministic 证明协议及本地工具循环；provider-dependent dynamic serialization 与 multimodal 必须在 real 模式证明。缺凭据只能是 `not_run`，不能 fake pass。

## 运行命令与证据路径

Task8 在 `config/release-gate.json` 中固定以下顺序：

```powershell
npm.cmd run build:pi-runtime
python scripts/verify_pi_capabilities.py --validate-matrix-only --output <diagnostics>/pi-capability-matrix.json
python scripts/verify_pi_capabilities.py --mode deterministic --workspace <diagnostics>/pi-capability-workspace.sqlite --test-evidence <diagnostics>/pi-targeted-junit.xml --output <diagnostics>/pi-capabilities-deterministic.json
python scripts/verify_pi_capabilities.py --mode real --workspace <diagnostics>/pi-capability-real-workspace.sqlite --test-evidence <diagnostics>/pi-targeted-junit.xml --output <diagnostics>/pi-capabilities-real.json
```

targeted pytest JUnit 写入 `pi-targeted-junit.xml`。上述 capability report 是内层能力证据；发布编排器的外层状态仍写 `.scansci-diagnostics/release-gates/<version>+<build-id>/release-report.json`。两者不能互相冒充，外层只有解析并验证当前内层报告、哈希和 status 才能晋级。

## 安全边界

- Dynamic tools：`allowed_tools` 是权限，`initial_tools` 是活动子集；搜索、激活、调用逐步重验。
- Skills：只改指令，包根内有界读取，hash/provenance 可恢复，不增加工具或证据权威。普通对话只注入显式/推断 Skill 与按需发现提示；完整安全目录留在运行时供 `search_skills` 查询，研究/工作流模式只注入有界候选子集，避免每轮把所有 Skill 描述送入模型。`typesafe-ai` 随 Harness 内置，提供 TypeSafe System One/Jev 的结构化判断设计约束；JEV 的服务配置、密钥、请求传输和 effect 授权仍由 Host 控制。
- Subagents：父任务最多 3 个，只读、无 MCP/write/递归委派，独立预算/trace/cancel。
- MCP：P0 deferred 路径延迟连接，未知 effect 拒绝，annotations 只能抬风险，retry 只限 idempotent；旧 direct 路径显式兼容但不计 deferred 证据。
- External literature provider：Host 只接受受限语义 manifest；manifest 不扩大 server lease，也不能把未知工具变成可调用工具。`scansci-pdf` 以独立 MCP 进程接入，Harness 不导入其 Python 包，避免插件更新形成第二份实现。
- Multimodal：只传验证后的内存内容，不传路径/URL，不记录 raw base64；unsupported 必须显式 degradation。
- Session：late result 不得写当前历史；clone/fork 不修改源 session；abort-compaction 后恢复。
- Direct fallback：可以做安全拒绝或降级交付，但永远不计 Pi 轴通过。

## 科研终止与可观测性

Host 的引用验证、evidence gap、Artifact 提交和科学后处理继续是业务完成门槛。ScanSci Harness 负责 turn 与驱动边界，Pi 负责规划和调用，不自行宣布科研事实已验证。每个 run/effect/subagent/compaction 记录 ID、duration、decision 和有界 result reference；秘密、完整提示、raw binary 和无界工具结果不能进入 report 或 manifest。界面默认展示当前阶段、完成度和结果，工具调用与详细轨迹保持在可展开区域；直接对话的阶段更新会去重并限制为最近记录，避免过程信息挤压回答正文。
