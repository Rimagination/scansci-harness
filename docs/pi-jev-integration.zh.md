# pi-jev 思想接入边界

ScanSci 吸收 `pi-jev` 的三点产品思想：能力按需发现、Skill 渐进发现、结构化判断结果。实现保持 ScanSci 的三层权责，Pi 只提出候选和路由请求，Host 继续拥有租约、effect、证据和产物的最终权威。

## 当前映射

| pi-jev 概念 | ScanSci 入口 | 当前职责 |
| --- | --- | --- |
| `jev_find_tools` | `search_tools` | 在当前租约投影内检索工具，按本地启发式置信度阈值增量激活 |
| `jev_find_skill` | `search_skills` + `load_skill` | 返回安全清单中的 Skill 候选，加载前再次检查包快照和资源边界 |
| typed judgment | `TaskProfile`、`TaskContract`、`agent_advisor` | Host 生成任务分类、租约和完成判定，并保存结构化理由 |
| evidence judgment | `search_local_evidence` 的 Host 后处理 | 用户启用 JEV 后，对有界候选证据做支持度判断并重排，不改变证据权威 |
| automatic routing | 暂不默认开启 | 现阶段由 Pi 模型显式调用发现工具；自动模式保留为后续可选策略 |
| tool-call guard | Host 每次调用的租约和 effect 校验 | 发现结果只能影响 Pi 的工具面，不能扩大授权 |

## 文献证据召回闭环

当 Pi 通过按需发现激活 `search_local_evidence` 后，Host 在当前已授权的证据库中执行句子级召回。每个命中保留：

- `evidence_id` 与 `html_anchor`：命中句的稳定身份和原文定位锚点；
- `matched_text`：实际参与排序的命中句；
- `context_before` / `context_after`：同一原文块内紧邻的上一句和下一句；
- 对应的前后句 `evidence_id`：便于继续审计证据边界。

命中句仍是引用边界，前后句只作为阅读上下文。最终核验引文沿用同一个 `html_anchor`，点击引文后由 Source Reader 滚动并高亮命中句；上下文同时出现在证据预览中。用户要求检索尚未入库的研究时，Pi 先使用 `discover_papers`，再由用户允许的 `download_and_index` 建立全文索引，之后进入同一召回闭环。

当用户在模型服务中启用 JEV 且已配置 API Key 时，`search_local_evidence` 会把本地候选池扩大到请求数量的两倍（上限 20），再将最多 12 条经过截断的候选片段发送给 JEV。JEV 并行回答每条片段是否直接支持问题，并返回整体覆盖度信号；Host 按该信号重排候选，保留原始 `evidence_id`、`html_anchor` 和本地召回结果。覆盖度目前只作为信号交给 Pi，继续检索或交付仍由 Pi 与 Host 的完成门槛共同决定。

这条证据检索能力已整理为公开的 [`scansci-evidence` 插件](../plugins/scansci-evidence/README.md)，并以同名内置 Skill 随 ScanSci Harness 发布。其他 Harness 可以复用 Skill 与 `search_local_evidence` 语义契约；实际证据库、引用验证、权限和 JEV 凭据仍由目标 Host 接管。

## 发现结果协议

工具和 Skill 发现结果带有 `scansci.discovery-decision.v1` 决策记录，核心字段为：

- `kind`：`tool_activation` 或 `skill_selection`；
- `source`：当前为 `local_heuristic`；
- `semantic_provider`：当前为 `not_configured`；
- `fallback_used`：当前为 `true`，明确表示尚未调用外部语义判断服务；
- `threshold`、`candidate_count`、`selected`：记录候选、阈值和实际选择。

`search_tools` 默认使用 `0.65` 的本地置信度阈值。显式传入工具名时仍要求 Host 重新授权；空租约、禁用工具和过期租约不会因为发现结果被激活。Skill 的 `confidence` 只用于排序和提示，`load_skill` 仍是单独的安全边界。

本地分数属于路由启发式指标，不作为 calibrated probability 写入能力报告。Pi capability report 继续区分真实 provider、degradation、not-run 和 fallback。

## 有意保留的边界

- 不把 `pi-jev` 第三方扩展直接打进 core；默认轮次不把用户内容发送到 TypeSafe 或其他外部判断服务，只有用户开启 JEV 后才发送有界证据片段。
- ScanSci 已提供 Host-owned 的 TypeSafe JEV 连接适配：官方地址为 `https://api.typesafe.ai/v1/systemone`，模型为 `jev-latest`，密钥通过 `TYPESAFE_API_KEY` 或本机凭据管理器读取；模型服务页的“检测”只发送固定的最小连接检查，不上传用户任务内容。
- 不让语义判断服务签发 capability lease、审批写操作、改变 MCP effect 或提交证据。
- 不改变 `protocol v7`、`TaskContract v2`、10 轴能力矩阵和完整包发布契约。
- 自动模型选择、Jev compaction、外部 gate 和自动子代理编排暂不接入；它们分别涉及模型路由、上下文保留、发布门禁和并发资源治理，需要独立的 Host 契约与隐私/成本选择。

## 后续运行时接入条件

证据候选重排已经接入 Host-owned 的可选 decision provider 路径，具备数据范围、用户开关、超时、脱敏、`not_run/degraded` 记录和本地回退。若后续让 JEV 参与工具或 Skill 自动发现，仍需为 discovery 单独增加 decision provider manifest；该 provider 只能返回候选评分，最终激活仍走当前租约和调用校验。当前 discovery 默认保持本地启发式。
