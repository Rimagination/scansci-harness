# ScanSci 命名体系设计

当前决策日期：2026-06-20。

## 核心判断

`ScanSci` 应该作为长期品牌和总命名空间，`scansci-html` 只保留为历史兼容入口和 HTML capture 层名称。

原因很简单：项目已经不只是“下载 HTML”。它正在变成一套面向论文写作的 evidence-first 工具链，包括论文获取、结构化归档、证据检索、精确引用、问答、验证、benchmark，未来还会扩展到综述写作。如果继续把整个项目叫 `scansci-html`，名字会把用户心智锁死在下载器上。

但不建议立刻做破坏性改名。已有包名、Python import、keyring service name、缓存目录、文档链接和脚本可能都依赖 `scansci-html`。更稳的路线是：对外品牌升级，工程兼容保留。

## 当前工程项目名

当前控制面和项目标识为 `scansci-harness`。`ScanSci` 继续承担面向用户的产品品牌，Pi 继续表示模型执行驱动。Python 发行包 `scansci`、`scansci_html` import、CLI 命令、keyring service name、会话目录和既有发行地址继续保留，保证已有安装、脚本与用户数据可继续使用。

## 命名原则

命名时优先区分三件事：

1. `ScanSci` 是品牌，不等于某一个文件格式、某一种检索算法或某一个 CLI 命令。
2. `html` 是 capture 层的介质选择，只在“获取和保存原文”这个边界内出现。
3. `evidence`、`ask`、`review`、`bench` 是能力命名，应该描述用户要完成的研究任务，而不是描述底层实现。

因此，不建议把后续能力继续叫 `scansci-rag`、`scansci-qa`、`scansci-reviewer` 这类技术或单点功能名。`RAG` 可以作为实现方法写在架构文档里，但对用户来说，真正稳定的心智是“我用 ScanSci 找证据、问问题、写综述、做校验”。

一个实用判断是：

```text
用户是否在接触总产品？
  是 -> ScanSci

这个能力是否只负责获取、清洗、保存论文 HTML/XML？
  是 -> ScanSci Capture / scansci-html

这个能力是否负责把原文拆成可检索、可引用、可校对的证据？
  是 -> ScanSci Evidence

这个能力是否负责基于证据回答具体问题？
  是 -> ScanSci Ask

这个能力是否负责把多篇论文组织成综述、证据矩阵或研究脉络？
  是 -> ScanSci Review

这个能力是否负责质量评测、gold set、回归门禁？
  是 -> ScanSci Bench
```

## 五层命名

| 层级 | 推荐命名 | 当前状态 | 原则 |
|---|---|---|---|
| 品牌 | `ScanSci` | 作为总品牌使用 | 面向用户和长期愿景 |
| 发行包 | `scansci-html` | 暂时保留 | 避免破坏安装和兼容 |
| Python import | `scansci_html` | 暂时保留 | 先稳定 API，再考虑 facade |
| CLI 主入口 | `scansci` | 已新增 | 新文档优先使用 |
| CLI 兼容入口 | `scansci-html` | 已保留 | 不破坏旧脚本 |

## 能力层命名

长期用户心智应该是：

```text
ScanSci
  capture   获取、清洗、归档可合法访问的论文 HTML/XML
  library   本地文献库、元数据、去重、集合管理
  evidence  句子级证据库、检索、rerank、quote extraction
  ask       基于证据表回答问题
  verify    claim-level support verification
  review    综述写作、证据矩阵、研究脉络整理
  discover  外部论文发现、citation traversal
  bench     gold set、质量门禁、回归测试
  admin     credentials、doctor、diagnostics
```

当前 CLI 仍是扁平命令，例如 `scansci fetch`、`scansci index-v2`、`scansci search-v2`、`scansci ask`、`scansci bench`。这是 MVP 阶段的兼容设计。等命令数量继续变多时，再迁移到分组式命令：

```powershell
scansci capture fetch ...
scansci evidence index ...
scansci evidence search ...
scansci ask ...
scansci review matrix ...
scansci bench run ...
```

迁移时应保留旧命令 alias，例如 `scansci index-v2` 继续转发到 `scansci evidence index`。

当前已落地的综述导出入口是扁平命令 `scansci review-matrix`；未来分组后可迁移到 `scansci review matrix`。

## 综述扩展命名

未来如果扩展到写综述，建议不要新开一个叫 `scansci-reviewer` 或 `scansci-litreview` 的平行项目。综述写作应该是 `ScanSci Review` 能力层，依赖上游的 `Capture`、`Library`、`Evidence` 和 `Ask`。

推荐的未来命令形态：

```powershell
scansci review matrix ...
scansci review outline ...
scansci review draft ...
scansci review verify ...
```

其中：

- `matrix` 产出证据矩阵，适合人眼校对和写作前整理。
- `outline` 产出综述结构，不直接生成大段正文。
- `draft` 只从已经确认的证据矩阵生成草稿。
- `verify` 对综述段落做 claim-level evidence check。

这会让项目的长期叙事保持一致：`ScanSci` 不是“下载器 + 另一个 RAG 工具 + 另一个综述工具”的拼盘，而是一条从原文获取到证据校验再到写作的研究工作流。

## 子项目边界

`scansci-html` 不再代表整个项目，只代表 capture 层里的 HTML-first 策略：

- 输入：DOI、DOI URL、article URL、本地 HTML library。
- 输出：clean HTML、raw snapshot、official XML/JATS sidecar、asset files。
- 约束：不下载 PDF 作为主路径，不生成 Markdown 作为证据源，不保存 cookie、token、机构登录态或密码。

证据检索、问答和综述写作不应该叫 `html`，而应该挂在 `ScanSci` 下面：

- `ScanSci Evidence`：证据库和检索。
- `ScanSci Ask`：证据约束问答。
- `ScanSci Review`：综述写作工作台。
- `ScanSci Bench`：质量评估和回归测试。

## 数据产物命名

推荐把功能命名和文件命名分开。文件名描述 artifact，命令名描述能力。

| Artifact | 推荐命名 | 说明 |
|---|---|---|
| 原始保存正文 | `paper.html` | clean HTML，离线阅读主文件 |
| 证据锚点正文 | `paper.evidence.html` | 带 `data-evidence-id` 的校对界面 |
| 机器证据库 | `evidence.sqlite` | 检索、quote、verification 的可信数据层 |
| 证据导出 | `evidence-spans.jsonl` | 可审计、可迁移的句子级证据 |
| 问答报告 | `reports/question-001.html` | answer pane + evidence pane + source pane |
| 结构化报告 | `reports/question-001.json` | claim、quote、verification 的机器输出 |
| 质量基准 | `bench/gold_questions.jsonl` | gold evidence IDs 和验收门禁 |
| 标注起点 | `bench/gold_questions.template.jsonl` | 待人工改写的问题模板，不是真值 |
| 标注校对页 | `bench/gold_questions.template.html` | HTML worksheet，候选证据链接回 source anchors |
| Benchmark 诊断 | `bench/benchmark-details.html` | 逐题检索、引用、充分性和错误定位 |

## 未来包结构

短期不建议把 `src/scansci_html` 直接改名为 `src/scansci`。更稳的两步迁移是：

1. 保留 `scansci_html` 作为真实实现包，继续保障旧 import 和测试。
2. 未来新增轻量 facade 包 `scansci`，只重新导出稳定 API，并承载新的 plugin/module namespace。

未来可能的结构：

```text
src/
  scansci_html/        # legacy-compatible implementation
  scansci/             # future public facade
    capture/
    evidence/
    ask/
    review/
    bench/
```

在 API 没稳定前，不要为了名字漂亮提前搬目录。命名迁移应该服务用户理解，而不是制造工程风险。

## 对外文案

推荐一句话：

> ScanSci is an evidence-first literature intelligence toolkit built on source-faithful scholarly HTML.

中文可以写成：

> ScanSci 是一套以原文证据为中心的文献智能工具链，从可合法访问的论文 HTML/XML 出发，完成证据检索、精确引用、问答验证和综述写作。

不推荐把长期项目描述成：

- `HTML downloader`：太窄，只适合 `scansci-html` 子层。
- `RAG app`：太泛，无法突出证据校验和论文写作。
- `citation generator`：容易让人以为只是补脚注。
- `PDF parser`：和当前 HTML-first 路线相反。

## 当前落地规则

1. README 标题和用户文档逐步使用 `ScanSci`。
2. 新命令示例优先使用 `scansci`，老文档里的 `python -m scansci_html.cli` 可作为开发入口保留。
3. `scansci-html` CLI 保留，作为旧脚本兼容入口。
4. `pyproject.toml` 发行包名暂不改，避免破坏安装和依赖。
5. `credentials` 的 keyring service name 暂不改，避免用户已保存的 API key 失效。
6. 模块内部仍使用 `scansci_html`，等 public API 稳定后再加 `scansci` facade。
