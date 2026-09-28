# 多 Agent 启动与交接规范

ScanSci 会由多个 agent 并行维护。每个 agent 都可能只负责一个页面、一个模型路由、一个运行组件或一段发布脚本，因此必须先读取项目级契约，再开始局部设计。

## 决策来源优先级

出现冲突时按下面顺序判断：

1. 用户在当前任务中的明确要求；
2. 本仓库根目录 [`AGENTS.md`](../AGENTS.md)；
3. `config/release-scope.json` 中的当前 P0、验收项和非目标；
4. `docs/project-governance.zh.md`、[`docs/desktop-packaging.zh.md`](desktop-packaging.zh.md) 和 [`docs/release-workflow.zh.md`](release-workflow.zh.md)；
5. 现有实现和测试；
6. 旧聊天记录、个人偏好或 agent 自己的“更方便”方案。

旧实现如果违反上面的新契约，不能因为“代码已经这样”就继续扩散；应先记录迁移范围，再用测试保护新行为。

## 每个任务的启动清单

开始写代码前，agent 必须完成以下检查：

- 确认任务属于哪一层：core、独立运行组件、模型权重、桌面 UI、更新器还是发布门禁；
- 搜索相关现有模块、API、清单字段和测试，避免另造一套并行协议；
- 查看当前 `git status`，保留其他 agent 或用户已有的未提交修改；
- 判断任务是否会改变包体积、默认运行时、模型下载位置、更新清单或发布流程；
- 如果会改变，先更新设计文档/计划，再实现代码；如果只是修复实现，则保持现有契约不变；
- 为新行为写失败测试，至少覆盖旧清单兼容、失败回退和用户数据不受影响的路径。

如果任务触及 Pi，启动时还要读取 `bench/pi_capability_tasks.json`、`config/release-report.schema.json` 和 `config/release-gate.json`，确认当前是 `protocol v7`、TaskContract v2、10 轴能力矩阵以及 report schema v2；不能从旧会话、旧文档或已生成 bundle 推断当前协议。

## 当前产品策略速查

| 主题 | 当前决定 | agent 不应自行改成 |
| --- | --- | --- |
| 默认桌面包 | `core` 轻量发布 | 把 PyTorch、Transformers、模型权重打进 core |
| 本地 AI | 独立 `local-transformers`，默认 Transformers/Hugging Face 路线 | 用 Ollama 替代默认路线，或要求同时安装四套运行时 |
| Ollama | 可选外部连接 | 作为 ScanSci 的必装内置依赖 |
| ASR/视觉/检索/重排 | 由独立 Transformers 组件按需提供 | 通过临时下载或隐式切换到未知后端 |
| 主程序更新 | 完整 ZIP + 可选 blockmap 差分 | 只发布无法独立安装的差分补丁 |
| 差分失败 | 自动下载完整 ZIP | 继续拼接不完整文件或覆盖旧安装 |
| 用户数据 | `%LOCALAPPDATA%\ScanSciPi\` 等持久目录 | 跟随程序包删除或放进临时更新目录 |
| 直接对话状态 | Host 保存稳定消息 ID、修订、事件、分支、当前项和队列；Pi session 承载执行上下文 | 前端固定截断历史、重启丢队列，或编辑/重试时原地覆盖旧消息 |
| 发布完成 | 通过对应 release gate，并有可复核报告 | 仅凭单元测试、源码启动或截图宣称完成 |

## 更新器设计契约

发布脚本 [`scripts/package_desktop_release.ps1`](../scripts/package_desktop_release.ps1) 生成三个关联产物：

- `ScanSci-<version>-windows-x64.zip`：完整可安装包；
- `ScanSci-<version>-windows-x64.zip.blockmap`：64 KiB SHA256 区块描述；
- `stable.json`：版本、完整包 URL/SHA256、blockmap URL/SHA256/大小和可选组件清单。

应用更新器 [`src/scansci_html/app_update.py`](../src/scansci_html/app_update.py) 的正确流程是：读取清单 → 校验 blockmap → 查找当前版本缓存 → Range 差分重建 → 完整包校验 → 暂存替换 → 重启。任何中间条件不满足都回退完整 ZIP。

因此，agent 不能设计“只下载 blockmap 就能安装”“差分失败后继续安装半包”或“为了节省空间删除当前基线后再差分”的逻辑。CDN 必须正确返回 `206 Partial Content`；否则完整包回退是预期行为。

## 结束任务时的交接模板

```text
任务：
影响层：core / local-transformers / 模型 / UI / updater / release
遵循的契约：
主要修改：
验证命令与结果：
未完成或风险：
是否改变了 P0、清单字段、默认运行时或用户数据位置：是/否
下一 agent 必须先看：
```

Pi 任务在上述模板后追加：

```text
Pi protocol / SDK：
10 轴 matrix（routing/dynamic_tools/parallelism/long_context/skills/subagents/mcp/multimodal/safety/observability）：
fallback_count / degradation / not_run：
验证命令：
capability report / release-report / run manifests：
source_sha256 / matrix.sha256 / bundle.sha256：
未完成轴与 provider-real、签名、打包或人工验收 blocker：
```

`fallback_count > 0`、缺失 `bundle.sha256`、哈希不绑定当前源码或 provider 轴为 `not_run` 时，必须如实写出；direct fallback 和显式 degradation 都不等价于 Pi 轴通过。

如果发现新的长期决策，必须把它写入根目录 `AGENTS.md` 或相应 `docs/`，并在交接中链接到具体文件；不能只说“之前已经讨论过”。
