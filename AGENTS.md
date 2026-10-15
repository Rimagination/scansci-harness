# ScanSci Agent 工作规则

这是 ScanSci 仓库级的 agent 入口。开始任何代码、打包、运行时、模型或桌面 UI 任务前，必须先阅读本文件和 [`docs/agent-startup.zh.md`](docs/agent-startup.zh.md)。本文件描述的是当前已经确认的架构契约，不是可随意替换的实现建议。

## 开始任务前必须读取

- [`docs/agent-startup.zh.md`](docs/agent-startup.zh.md)：多 agent 启动、交接和变更流程；
- [`docs/project-governance.zh.md`](docs/project-governance.zh.md)：业务分层和模块边界；
- [`docs/desktop-packaging.zh.md`](docs/desktop-packaging.zh.md)：core、运行组件和差分更新契约；
- [`docs/release-workflow.zh.md`](docs/release-workflow.zh.md)：源码、桌面包和公开发布门禁；
- [`config/release-scope.json`](config/release-scope.json)：当前 P0、验收项和明确非目标。

如果任务会改变其中任何契约，必须同时更新对应文档、测试和发布门禁；不能只修改实现后把新策略留在当前对话里。

## 全量测试授权

- 未经用户在当前任务中明确允许，不运行全量测试；最终交付也不自动触发全量测试。本条优先于其他文档中的最终验证流程。
- 默认只运行受影响范围的定向测试。若发布门禁包含全量测试，先说明所需验证并取得用户对全量测试的明确允许。

## Pi 与 Host 权责（v0.4.0）

- Pi 是所有需要能力编排的模型轮次的唯一编排面：模型在当前租约内自主规划、搜索并激活工具、并行只读调用、加载 Skill、委派科研子代理、使用延迟 MCP、多模态输入和会话控制。没有知识库、Skill、附件、联网或工具意图的 `direct_chat` 由 Host 走精简直连快速通道；该通道不持有 capability lease，也不计入 Pi capability 成功。
- Host 是不可转让的权威面：编译 `TaskContract v2`、签发/撤销 capability lease、逐次授权工具与 effect、持久化 Research Run/Artifact、验证证据与引用、执行后处理并发布 UI 事件。Pi、Skill、MCP 和子代理都不能扩大权限。
- Python/Node 只写 `protocol v7`，并对 required features、当前 request/run、generation、schema 和租约 fail-closed 校验。兼容读取旧契约不得改变当前写入格式或放宽授权。
- `direct fallback`、OCR/text degradation、Host 确定性事实和 effect 前拒绝必须显式记录；它们可以形成安全交付，但不计作 Pi capability 成功。provider 不支持或未配置时记录 `degraded`/`not_run`，不得伪造通过。
- “100% Pi 能力”仅表示 [`config/release-scope.json`](config/release-scope.json) 声明的能力边界及 Task8 的 10 个报告轴全部有合格证据；不表示无限上下文/并发、任意 shell/文件系统、任意 extension/MCP 权限或任意模型质量保证。

## 当前不可悄悄改变的架构决定

### 桌面包与本地 AI 运行时

- `core` 是公开默认桌面包：包含应用界面、对话、知识库、文档、PPT、Skill 和 MCP，不包含 PyTorch、Transformers 或模型权重。
- `local-transformers` 是默认推荐的本地 AI 组件，独立版本、独立下载和独立校验；它服务 Qwen3-ASR、原生 Hugging Face 视觉模型、语义检索和重排。
- Ollama、LM Studio 等是可选外部运行时连接，不得为了“更简单”而替换默认 Transformers 路线，也不得强制用户同时安装多个本地运行时。
- 模型权重属于用户按需安装的数据，不得偷偷打进 `core` 或因主程序小改动而重复分发。
- `full` 只用于内部支持或离线交付，不能成为正式公开发布的默认 profile。

### 应用更新

- 主程序发布的是完整 Windows ZIP 加 `.zip.blockmap`，不是只有补丁包；完整 ZIP 永远是可用的回退和手动下载入口。
- blockmap 默认使用 64 KiB SHA256 区块。更新器只有在存在已校验的当前版本缓存且 CDN 支持 HTTP `Range`/`206` 时才走差分。
- 差分重建必须写临时文件，并在替换前通过完整包大小、SHA256、ZIP 路径安全和 `ScanSci.exe` 存在性校验。
- 缺少缓存、清单或 blockmap 错误、Range 不可用、区块响应错误或最终校验失败时，必须自动回退完整 ZIP，不能留下半包或覆盖当前安装。
- `stable.json` 的 `windows.url`/`sha256` 是旧清单兼容的必需字段；`windows.blockmap` 是可选增强字段。不能把旧版本清单升级成差分必需品。
- `local-transformers`、Node、Tectonic 等组件继续按各自清单和版本更新，不因 core 差分更新而重复下载。

## 不允许的快捷方案

- 不要把所有依赖重新塞回 `core`，以解决单个模型或运行时问题。
- 不要把 Ollama 当成默认唯一后端，除非用户明确改变产品决策并同步更新架构文档和验收项。
- 不要删除完整包回退，只保留“差分一定成功”的路径。
- 不要直接改发布产物、安装目录或用户数据来绕过测试。
- 不要把 API key、token、cookie、签名私钥或内部接入点写入代码、日志、文档或提交。
- 不要仅凭“源码能启动”宣称桌面包或公开发布完成；发布必须遵循 `docs/release-workflow.zh.md` 和 `release-report.json`。

## Agent 交接要求

每个 agent 结束任务时，必须说明：改了什么、依据哪个契约、运行了哪些验证、有哪些未完成项，以及是否需要更新下一轮 P0。架构决定不能只存在于聊天记录；应写入 `docs/`、`config/`、测试或实现注释中的稳定位置。

涉及 Pi 的交接还必须列出：protocol/SDK 版本、10 轴 matrix 状态、`fallback_count` 与 degradation/not-run 数量、source/matrix/bundle 哈希、run manifest 与 capability report 路径、未完成轴及真实 provider/打包外部 blocker。没有这些证据不得用“Pi 已完成”替代实际状态。

## 深色主题设计契约

- 新建或修改桌面 UI 时，页面、卡片、浮层、控件、正文、次级文字和分隔线必须消费 `styles.css` 中的语义颜色变量，不能绕过变量直接绑定某一个主题的颜色。推荐优先使用 `--page-background`、`--surface`、`--surface-elevated`、`--ink`、`--muted`、`--rule`、`--rule-strong`、`--control-background` 和 `--control-background-hover`。
- UI 改动必须同时验证 light/dark；浅色主题、品牌色以及成功、错误、警告等状态语义不能因深色适配被抹平。
- 禁止在组件中新增没有 dark 对应关系的硬编码浅色背景或深色正文。确需品牌色、插图色或状态色时，应明确限定用途，不能把它当成通用 surface 或正文颜色。

## 桌面交互图标

- 新增或修改交互图标时，优先使用 `web/app.js` 的 `uiIcon` / `data-ui-icon` 管线和 Lucide 线性 SVG；Electron 隔离页面可内嵌同风格 SVG。
- 按钮图标不使用 Unicode 字符或 emoji 代替；品牌标识、第三方服务商 Logo 和有实际文字语义的符号除外。
- 动态更新按钮状态时仅更新文字标签，保留现有 SVG 图标及无障碍名称。
