# RetainPDF 评估与 ScanSci 接入边界

2026-09-29 在 Windows 上试用 RetainPDF v4.2.5。官方项目采用 MIT 许可，公开提供桌面安装包、本地后端和 [HTTP API](https://github.com/wxyhgk/retain-pdf/blob/main/docs/api/README.md)。试用安装包的 SHA256 与该版本发布的 `SHA256SUMS.txt` 一致；安装包没有 Authenticode 签名。

## 一页隔离样本

用一页合成的英文双栏科研短文生成扫描版 PDF，经 MinerU OCR 和本机 `qwen3.8-4b-distill:latest` 翻译。RetainPDF 的 OCR、翻译、渲染三个阶段均完成，得到一页中文 PDF。标题、双栏、行内公式、`12`/`10`、`1.25 ± 0.15 mg/L` 和 `18% (95% CI: 4%–30%)` 得以保留。小表格的行被挤成不自然的换行，`treatment` 在表中译为“治疗”，需要人工核对。这个小样本证明链路可运行，不代表复杂论文或批量文档的质量已经验收。

一体化 `POST /api/v1/translate/bundle` 在此次试用中断开连接；按[官方部署文档](https://github.com/wxyhgk/retain-pdf/blob/main/ops/deployment/docker/delivery/README.md)使用“上传 PDF → 创建任务 → 查询任务 → 下载 PDF”的接口后成功。`GET /api/v1/jobs/{id}/markdown?raw=true` 返回的是英文 OCR 文本，此次不能作为译文 Markdown 使用。

## ScanSci 现有 OCR 基线

用户在“设置 · 文档处理”已有 OCR 提供者、语言、启用状态和凭据配置；默认选择 Tesseract，另可选择 Windows OCR、PaddleOCR（AI Studio）、DeepSeek-OCR 或自定义 API。MinerU 是同页单独配置的 PDF 文档解析服务，默认关闭。配置保存在 `app_settings.py`，密钥由 Host 的凭据接口保存。版面翻译沿用这些设置，不增加第二套 RetainPDF OCR 选择和密钥入口。

当前执行路径有明确的适配工作：

| 路径 | 已有行为 | 版面翻译所缺的输出 |
| --- | --- | --- |
| `vision_routing.ocr_image_blocks` | 图片会话调用已选 Windows OCR 或 DeepSeek-OCR，并可回退到 Tesseract；返回识别文本、实际 backend 和状态 | 返回值没有逐页文字块与坐标；一次最多处理 4 张图片；Tesseract 当前只读取 `.txt` |
| `app_settings.document_processing.ocr` | 保存 Tesseract、Windows、Paddle、DeepSeek、自定义提供者设置 | Paddle 与自定义提供者尚无 `ocr_image_blocks` 执行分支；配置成功不能等同于本流程可识别 |
| `ingestion._extract_source` | PDF 可优先走已启用且有密钥的 MinerU；否则尝试本地 PDF 文本提取 | 扫描 PDF 尚未逐页调用上面的 OCR 路由；MinerU 当前只向导入层返回 Markdown，不能直接提供重排坐标 |

这些差异意味着：**OCR 配置和图片识别能力已经内置；PDF 版面翻译需要补页级调用、提供者适配和几何数据。** 新流程不得把 RetainPDF 自带的 OCR 设置当作主真值，也不得因某个提供者缺少坐标就宣称保留原版式。

## 产品决定

ScanSci 将“保留排版的 PDF 翻译”设计为已有 OCR 能力上的文档流程。`retain-pdf` 是预装、可发现的声明式 Skill，负责识别任务、提示权限与核对译文；Host 使用现有 OCR 设置执行识别，再调用翻译和版面组件。RetainPDF 的版面与翻译设计，以及经评估可独立运行的引擎组件，作为后两阶段的参考和候选实现。[项目源码与 MIT 许可](https://github.com/wxyhgk/retain-pdf)允许二次开发，具体复用范围仍须逐模块检查依赖、资源和版本。

### 用户入口与命名

- 用户可见名称为 **文献翻译**。首页已有快捷入口，打开专用对话框；`RetainPDF` 保留在技术来源与许可说明中，`retain-pdf` 保留为 Skill ID。
- 当前入口接受本地 PDF，可选择目标语言和翻译库保存位置，并显示当前主力模型与 OCR 设置。OCR 提供者和密钥仍在“设置 · 文档处理”修改。任务由 Host 后台执行，状态保存在 ResearchRun，完成后可打开译文 PDF。
- 知识库已有 PDF 的直接选择、PDF 阅读器“翻译此 PDF”、原译并排对照和术语/表格/公式核对界面是后续增强；当前页块排版结果需人工查看，不能宣称复杂版式质量已验收。

### 译文保存与翻译库

[RetainPDF 的产品界面](https://github.com/wxyhgk/retain-pdf#%E5%9B%BE%E4%B9%A6%E9%A6%86)将集中管理论文、翻译状态和收藏的区域称为“图书馆”。ScanSci 已有“知识库”总入口，新增的用户可见来源组定名为 **翻译库**，与个人知识库、Zotero、Obsidian、Notion 平级，放在个人知识库之后。任务入口仍叫“文献翻译”；翻译库用于查找和管理已完成的译文。尚无译文时只显示“暂无译文”，不提供失效的翻译或导入按钮。

- 用户选择一个已有保存位置；Host 在其中创建 `翻译库` 子文件夹（若无），并将它关联为唯一的 `library_kind=translation` 知识库对象。不指定位置时使用当前工作区目录。译文 PDF 写临时文件、校验后替换；原件位置保持不变。
- 知识库每次读取时列出该文件夹中的 PDF，包括用户从应用外放入的文件；目录内的文件无需经过全文索引就能显示和打开。生成译文旁的 `.translation.json` 保存任务 ID、目标语言、模型、OCR 和核对提示。
- 文件夹关联只提供管理和阅读，界面显示“仅文件关联”。译文目前不自动建立证据索引，也不自动参与问答；原始论文的 clean HTML 仍是科研证据母本。未来若加入译文检索，需要把派生内容和原文证据分开标记并验证引用回跳。
- 保存后如任务登记失败，已生成 PDF 仍留在翻译库文件夹，下一次读取知识库会自动看到它。删除翻译库对象不删除本地文件。

### 值得吸收的部分

| RetainPDF 经验 | ScanSci 接入位置 | 验收重点 |
| --- | --- | --- |
| OCR 结果归一化为文字块、坐标和阅读顺序 | Host 在现有 OCR/文档解析执行路径上增加页级结构化输出 | 双栏、跨页段落、图表附近文字可回指原页；沿用用户已选 OCR 和远程授权 |
| 先恢复语义单元，再翻译；保护公式、代码、数字和单位 | Host 翻译流程调用 ScanSci 模型目录；Pi 只选择授权能力和任务参数 | 一页样本中 4B 模型译出正文；术语、公式、数字和表格需独立质检 |
| 根据原页几何信息重新排版 | 独立版本的可选文档处理组件，产出派生 PDF | 页数、栏序、公式位置、表格和图注逐项检查；失败保留原件 |
| 上传、异步任务、阶段状态和下载接口 | Host 的 ResearchRun/Artifact 生命周期和受控组件适配器 | 中断可恢复；错误阶段可见；只交付已验证的产物 |
| 原文与译文逐页对照阅读 | 现有 PDF 阅读器的后续增强 | 原文和译文页码联动，便于人工校对 |

ScanSci 使用现有知识库承载翻译库，沿用现有阅读器、OCR 与模型配置，并继续以 [`docs/project-governance.zh.md`](project-governance.zh.md) 规定的 clean HTML 为科研证据母本；译文 PDF 是用户主动生成的派生产物。

### 后续完整处理顺序（规划）

1. **登记输入**：当前首页入口通过本机同源接口上传 PDF，Host 校验格式、大小与页数后复制到任务暂存目录；后续知识库/阅读器入口只接受已登记文档的资源标识。Pi 工具参数不接受任意文件路径或上传地址。
2. **冻结已有设置**：任务创建时读取 `document_processing.ocr`、`document_processing.mineru` 的有效配置与当前模型路由，记录提供者、语言和是否远程；凭据仍在 Host 内解析。文本 PDF 提取原生文字与几何信息；扫描页和混合页按页进入已选 OCR。用户关闭 OCR 时，扫描页停在“需要 OCR”状态。提供者失败可按现有策略回退本地 OCR，但必须记录实际 backend 与版面降级，且不得自动改用未授权远程服务。
3. **补足结构化结果**：新增 Host 页级 OCR 入口，复用已配置提供者和凭据。统一返回 `page_index`、页面尺寸、`bbox`、文字、阅读顺序、块类型、置信度、实际 backend 和降级原因。Tesseract 读取 TSV/hOCR 坐标，Windows OCR 提取行/词矩形；Paddle、自定义 API 和 MinerU 增加各自的结构化适配。DeepSeek 当前只返回 Markdown，只有可靠几何映射完成后才允许“保留版式”交付。结构化识别结果按原件与实际提供者记录为可复用派生产物，导入与翻译共用，避免重复 OCR；现有 `ocr_image_blocks` 的聊天文本结果保持兼容。
4. **翻译与排版**：Host 将归一化页结构交给段落合并、公式/代码占位、术语翻译和版面渲染。优先复用 ScanSci 已配置模型。RetainPDF 已公开 [`generic_flat_ocr` 页块契约与 `document.v1` 中间格式](https://github.com/wxyhgk/retain-pdf/blob/main/docs/api/03-OCR/04-local-command%E6%8F%92%E4%BB%B6.md)，可用来验证结构化结果能否驱动其翻译和渲染；正式组件只接受 Host 固定的适配命令与受控输入。其协议与 ScanSci 本地 Transformers 仍需联调，Ollama 不设为依赖。
5. **授权与持久化**：新增 `pdf-translation` 任务模式及可逆工具租约。Pi 请求提交、查询或取消；Host 逐次验证 effect，在 ResearchRun 记录识别、翻译、排版、质检阶段，在 Artifact 保存原件引用、译文、实际 OCR/模型来源和可公开错误。通过质检后按上面的本地保存与翻译库契约入库；Pi 和 Skill 不直接写用户文件。`scansci_harness.py` 继续传递 TaskContract、租约与事件，符合[薄边界契约](project-governance.zh.md#最小-scansci-harnesspi-编排层与-host-权威层)。
6. **交付质检**：自动检查 PDF 可打开、页数、提取文本、数字与单位；双栏顺序、公式、表格、图注和术语提供原译对照人工核对入口。试用中表格换行和 `treatment` 误译说明这一步是交付门槛。OCR 只有纯文本或页面几何不可用时，明确标记结果为“文本翻译”，不称“保留版式”。

当前可运行切口覆盖原生文字 PDF 与已配置的 Tesseract 扫描页。其他 OCR 提供者尚无版面坐标适配，扫描页会给出明确错误；PDF 阅读器上下文入口、复杂双栏/公式/表格质量验证、跨重启任务恢复以及译文证据索引仍待完成。当前 v0.4.2 P0 未包含文献翻译验收，不能把这一切口计入公开发布完成。
