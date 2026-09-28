# 托管模型长任务恢复（2026-09-09）

遵循 Pi 唯一模型编排、Host 授权与持久化、protocol v7 / SDK 0.84.3 契约。

## 恢复责任

- Pi 会话负责临时模型错误的恢复：最多两次，基础等待 15 秒、指数退避；保留已完成工具结果，仅重新请求失败的模型响应。
- Pi provider 内部重试关闭；托管适配器在 Pi 调用链中只发一次请求。多个会话和科研子代理共享同一端点的请求锁及冷却时间。
- 服务端 `Retry-After` 支持秒数和 HTTP 日期，完整冷却时间不向下截短。等待可取消；超出本轮时限的冷却仍保留，后续请求继续遵守。
- 单次模型 HTTP 等待上限 240 秒；托管适配器上游读取 180 秒；一般 Pi 交互整轮上限 900 秒，包含工具执行与恢复等待。外部服务仍可提前终止请求。
- 任务追问收到 Pi 已耗尽恢复的限流、超时或连接错误时，保留原错误及原任务，不再追加相同模型的裸请求。
- 恢复预算有限。长期限流、配额不足或网络中断仍需要恢复服务、等待或用户选择其他模型；客户端不能保证上游可用。

## 任务核查

已完成的 Host `scansci.academic.search` 可满足对应检索工具组；失败、取消和仅有产物缺少执行记录的情况不能算工具完成。
读取有核查记录的任务时提供最新 `advisor_report` 展示投影，保留原 `advisor.reviewed` 审计事件。页面优先展示最新投影，追问错误只在消息内显示一次。

## 验证与边界

- 定向传输/追问测试；原生 SDK 集成验证“检索工具执行后 429 → 等待 → 恢复”，断言工具只执行一次、模型请求保留工具结果。
- `python scripts/verify_glm_long_task.py`：真实 GLM 两轮检索与总结，使用独立诊断工作区，不修改用户历史。
- 本轮真实验证目录：`.scansci-diagnostics/glm-long-task-yhqjg6dp/`。第一轮首请求收到上游 429，两次退避后均遇到 ConnectionResetError 10054；未完成检索，第二轮未运行。后续仅回复 OK 的最小诊断也遇到 ConnectionError。真实长任务尚未通过。
- 已有用户检索任务 `run_ca20d2f2dc974a5db6ab` 实际完成 Host 检索，24 条候选，重新核查为 passed；未改写用户数据。
- 本次为源码修复与定向验证，构建 Pi bundle；不执行全量测试、桌面打包或发布。跨模块改动做一次综合只读审查；不重复执行同一代码状态下已通过的验证。
- 未改变 P0、发布清单、默认运行时、权限或数据位置。

## Pi 交接

补充：任务追问上下文现在包含实际候选论文的题名、年份、DOI、链接、期刊与有界摘要，明确标注为检索元数据及不可信来源内容；不会将“24 条候选”的数量摘要当作论文内容，也不注入原始服务商数据块。

本轮实际局部结果：传输/追问 28 passed；原生 SDK 工具后 429 恢复 1 passed；advisor 23 passed；历史核查投影 2 passed；追问候选上下文 3 passed。恢复界面浏览器 light/dark 通过。共享排队锁等待支持取消和过期检查，避免取消后继续发送请求。真实服务仍受前述 429/连接中断阻塞。

protocol=7；Pi SDK=0.84.3。原生 SDK 恢复定向验证通过，不能等同于完整能力 matrix。
10 轴 routing/dynamic_tools/parallelism/long_context/skills/subagents/mcp/multimodal/safety/observability 本轮均未全量验收（not_run=10）。
真实验证 fallback_count=0、无模拟降级替代真实结果；真实第二轮 not_run=1，第一轮 failed。
capability report：上述目录 `report.json`（定向报告，非发布 matrix）；run manifest 在同目录 `.scansci-diagnostics/runs/`。
本轮未生成 source/matrix/bundle 哈希：没有发布/传输完整性或确定性验证需求。无 release-report；真实服务可用性及桌面包验收仍未完成。
