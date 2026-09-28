# 文献下载定向验收（2026-09-23）

> 本文前半部分保留首次验收记录。后续修复结果见文末“问题修复复查”；此前列出的标题检索、暂停、标题/状态和跨进程导入问题已完成定向修复，未作公开发布结论。

## 结论与范围

用户未指定文献，本轮选两篇公开 arXiv 论文，从网页的“文献下载”入口实际执行下载，再通过知识库界面导入。两篇 PDF 均成功落盘并建立基础全文索引。按完整标题检索后下载仍失败，本轮不作可公开发布的结论。

本轮为一个跨模块、中风险修复批次：下载候选准入、导入与索引触发、继续添加文件入口。执行定向测试和一次独立审查，不扩展为全项目审计。使用 Playwright 技能按页面快照操作真实 UI，并检查浅色和深色下修改的工具栏控件。

依据仓库 core/本地 AI 分离、Host 验证交付以及基础全文检索可独立运行的契约。已在 `project-governance.zh.md` 固化自动导入不触发重型索引的规则；没有改变 Pi 编排、协议、SDK、权限或发布包格式。

## 真实运行记录

预览：`http://127.0.0.1:8784/?preview=conversation`，版本 0.4.2，使用临时验收工作区 `scansci-pi-preview-v31`，未更改其他预览实例或用户原始文献。

| 入口 | 结果 | 运行记录 |
| --- | --- | --- |
| 输入 `1706.03762 2005.11401` 下载 | 2/2 成功，约 8 秒 | `run_50da232d5be347cb893e` |
| 新知识库“文献下载验收-轻量”导入两个 PDF | 2 个文档，基础全文索引完成，无新语义索引任务 | `kb_0829bab673b5` |
| 修复前按完整 RAG 标题检索，数量 1 | 返回无关临床论文并进入下载；已请求暂停，重启后保留为暂停任务 | `run_007646fa688648a8a345` |
| 修复后同一标题新任务 | 约 13 秒返回无可下载标识，未进入下载阶段；标题检索未通过 | `run_e4f03cda6f9a48879f3f` |

文件均位于验收工作区的 `downloads` 文件夹：

- `Vaswani2017_Attention.pdf`：2,215,244 字节，15 页；打开 PDF 核对首页标题为 *Attention Is All You Need*。
- `Lewis2020_Retrieval.pdf`：885,323 字节，19 页；打开 PDF 核对首页标题为 *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*。

首次导入触发 Qwen 检索模型加载时，预览进程工作集约 4.7 GB、私有提交内存约 14.4 GB，系统出现内存分配失败。修复后重新导入两个 PDF，工作集约 130 MB，私有提交约 625 MB，无新增语义索引任务。这是本机定向观察，不代表所有文档规模的性能基准。

## 本轮修改

- `research_agent.py`、`webapp.py`、`web/app.js`：自动刷新/导入仅查询模型状态、建立基础索引并复用已有向量；重型准备和索引由用户明确操作触发。准备失败保留错误，避免循环重试。
- `library_manager.py`：复用现有导入路径，以进程内可重入锁串行化文件/文件夹导入，防止并发覆盖索引及清理另一导入任务的 generation；原始文件不删除。
- `web/app.js`、`web/styles.css`：已有绑定后保留“添加文件/添加文件夹”；修改的按钮使用语义颜色，检查 light/dark。
- `research_tools.py`：复用现有主题相关性准入，先过滤再截取数量；无合格可下载候选时尝试公共学术检索，增加现有 arXiv 来源。
- 定向回归覆盖：首库和旧库自动索引、显式准备、并发追加、无关检索结果拒绝、公共检索回退、过滤与数量限制顺序。

## 已执行验证

设置 `PYTHONPATH=src` 后运行：

```powershell
python -m pytest -q tests/test_webapp.py tests/test_research_runs.py -k 'import_does_not_load_installed_retrieval_models or evidence_index_route_prepares_models_only_on_explicit_request or automatic_evidence_index_defers_for_new_and_reusable_libraries or local_ai_status_route_starts_and_reads_background_preparation or library_binding_never_starts_a_model_download_implicitly' --disable-warnings
# 8 passed，19.87 秒：防止自动加载/隐式下载模型，保持显式操作可用。

python -m pytest -q tests/test_library_manager.py -k 'concurrent_file_imports_preserve_both_additions or import_library_folder_merge_existing_keeps_previous_linked_sources or import_library_files_keeps_originals_in_place_and_discards_superseded_generations' --disable-warnings
# 3 passed，10.02 秒：防止连续导入覆盖资料或删除原始文件。

python -m pytest -q tests/test_research_tools.py -k 'search_papers_for_download or search_download_rejects or download_relevance_filter' --disable-warnings
# 9 passed，0.59 秒：防止下载不相关论文，验证过滤后再限制数量。

node --check src/scansci_html/web/app.js
```

另用 Node VM 执行实际 `refreshLocalAiStatus` 函数：error/fallback 不触发新索引请求，ready 触发一次。相关性回归使用确定性候选，不将其当作真实网络标题检索成功的证据。

浏览器检查两种主题下添加按钮可见；截图位于 `output/playwright/literature-download-light.png` 与 `literature-download-dark.png`。检查范围为本轮修改的工具栏，未宣称所有历史 UI 的主题适配完成。

20 个定向测试合计约 30.5 秒。一次独立只读审查发现并发导入和准备失败重试两项，已修复并做局部验证。未开展第二轮审查。

未重复运行相关逻辑未变化的成功测试。未运行全量测试、完整构建、全量 lint 或发布门禁：本轮为局部修复，未形成发布候选。未做哈希检查：没有生成发布包或进行传输完整性验收。未进行 Pi matrix/provider 验收，也未作 Pi 能力完成声明。

## 未完成项与下一轮建议

1. **标题检索仍是发布阻塞项。** 完整且已知可下载的 RAG 标题未获得可下载标识；需要继续定位候选来源和标识解析。当前网络复查只证明该任务没有误下其他论文，不能证明完整检索下载链路已恢复。
2. **暂停响应偏慢。** 下载器正在阻塞网络调用时，暂停请求不能及时中断。旧验收任务已通过仅重启自有预览恢复为暂停；不要继续那个包含无关候选的旧任务。
3. **导入后标题/状态文案。** 知识库显示 `Lewis2020`、`Vaswani2017` 等文件名衍生标题；已有基础索引时仍显示“等待同步”，需要区分基础可用与可选语义索引状态。应用内 PDF 阅读器未完整验收。
4. **并发边界。** 当前导入锁保护单进程；多个应用实例同时写同一个知识库仍需跨进程协调。没有为本轮引入新锁依赖。

建议下一轮先处理第 1、2 项，按真实用户路径维护小规模固定样例：指定标识下载、标题检索下载、追加导入、打开原文、失败恢复。通过这些路径后再形成发布候选并执行仓库要求的正式门禁。当前未修改 `release-scope.json`，也未发布软件。

## 问题修复复查（2026-09-23）

### 修改与依据

本批继续遵守 Host 校验交付、自动导入只建立基础全文索引、原文与引用身份保留的契约；没有改变 Pi 协议、SDK、权限或桌面发布格式。

- 标题检索：下载数量不再限制候选召回预算；先过滤相关性、作者、起止年份和可用标识，再截取交付数量。识别 DOI/arXiv URL，优先使用 arXiv 标识。CLI 空结果、损坏输出或超时可走公共学术来源，保留用户约束。
- 暂停：搜索、单篇与批量下载共享取消信号，等待外部响应时可退出 Host 等待；已取消的旧任务不能因恢复而提交晚到文件。Windows CLI 使用独立 Job 约束自己的子进程，启动挂起后加入 Job，清理不依赖父进程是否仍存活。
- 导入：采用每个知识库的操作系统文件锁，拒绝多个应用实例同时写入；异常后释放锁。低置信度 PDF 文件名标题可由首页显著标题补全，保留可信 PDF/用户元数据优先级。
- 重入库：按原文路径更新现有生成文本，在临时索引中复用文献身份；保留 `doc_id`、`source_id` 及正文未变化的证据 ID。解析失败保留旧记录。
- 状态：基础索引与可选语义索引分别显示。真实后端的 `optional` 状态也显示“基础检索可用 · 可优化语义索引”，用户点击才开始优化；空库和真正的导入失败继续明确提示。

### 真实路径与定向验证

公共检索回退先复现“完整标题只返回无标识的仓储副本”，调整候选预算后得到正确的 `2005.11401v4`。随后从网页提交完整 RAG 标题，运行 `run_1f607a2e827c4c689c4e` 于 09:36:30–09:36:51 完成：正确选中 `2005.11401`，复用本地已下载 PDF 并入库。21 秒包含检索和缓存复用，不作为全新网络下载的速度基准。

本轮实际运行的局部检查：

| 检查 | 结果 | 防止的风险 |
| --- | --- | --- |
| `tests/test_research_tools.py -k 'download or search_papers'` | 35 passed，1.90 秒 | 候选、标识、暂停、晚到文件与原有下载行为 |
| 同文件作者解析相关两项（修改后） | 2 passed，0.44 秒 | 作者检索可取消后保持原行为 |
| `tests/test_research_runs.py` 下载/暂停相关选择 | 6 passed，12.27 秒 | 三种下载入口正确暂停，单篇失败仍报告失败 |
| `tests/test_local_model_market.py` 下载/公共存档相关选择 | 8 passed，3.61 秒 | 公共存档竞速、CLI 与已有标识处理兼容 |
| `tests/test_library_manager.py` 标题、锁与并发相关选择 | 9 passed，21.42 秒 | 标题优先级、跨进程冲突、导入覆盖 |
| 同文件 `-k reimport` | 5 passed，17.05 秒 | 三类重入库保留身份，解析失败和历史冲突保护 |
| `tests/test_research_tools.py -k 'download_cli or search_papers_for_download or download_search_preserves'` | 12 passed，2.87 秒 | 父进程先退出的子进程清理、超时、正常结束、作者与年份约束 |
| `node --test tests/test_knowledge_index_status.mjs` | 最终 9 passed，0.19 秒 | 包含真实 `optional` 状态的基础/语义索引文案与操作 |

交叉模块修改进行了一次独立审查，发现的子进程清理和回退约束问题已修复并局部验证。真实 UI 复查发现重入库身份及 `optional` 文案问题，补充了对应回归。未新增第二轮独立审查。

最新源码预览为 `http://127.0.0.1:8785/?preview=conversation`。保留旧预览及旧运行记录；不继续那个包含无关候选的已暂停任务。Playwright 按真实页面操作复查，paper-fetch 的短超时、标识优先原则用于限定下载路径，未扩展到大批量联网基准。

在修复版网页新建“文献下载修复验收”（`kb_9aa7ac9688ed`），通过“链接文件”导入两篇真实 PDF。页面显示 2 个文档、两篇完整论文标题和“基础检索可用 · 可优化语义索引”。分别检查了 light/dark 的标题、状态与添加入口；截图为 `output/playwright/literature-download-fixed-light.png` 和 `literature-download-fixed-dark.png`。没有重复联网下载。`node --check` 与改动文件的 diff 空白检查通过；检查不涵盖所有既有 UI 的主题颜色。

### 验证边界与交接

- 已成功且逻辑未再修改的测试不重跑；只对修改后的子进程、过滤、重入库及状态文案补测。未进行全量测试、完整构建或发布门禁，本批没有生成发布候选；未做产物哈希检查，没有传输/打包完整性待验。
- 已在阻塞读取中的 HTTP 请求可能在本次短超时到达后才结束；Host 可先暂停，取消信号阻止结果提交。公网上游仍可能限流或不可用。
- 历史上已经重复的文献没有自动合并；重入库遇到同一原文对应多个身份时明确提示，保留原索引和引用。验收旧库中保留了本轮复现时产生的重复记录，新导入使用修复后的身份逻辑。
- 未新增依赖，不自动加载检索模型；原始 PDF 未改写。没有完成应用内 PDF 阅读器、所有外部来源、所有模型与桌面安装包验收。
- 本轮无需调整 P0 范围或 release scope。下一次形成发布候选时，仍需执行仓库规定的正式发布门禁；没有进行 Pi matrix/provider 验收或作 Pi 能力完成声明。
