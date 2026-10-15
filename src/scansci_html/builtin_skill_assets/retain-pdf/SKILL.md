---
name: retain-pdf
description: Use when a user wants to translate a scientific PDF or scan into a local PDF. Guide them to ScanSci's native literature translation entry and report layout limits accurately.
---

# 文献翻译

本 Skill 是 ScanSci 预装的任务规范，借鉴 RetainPDF 的版面翻译流程。ScanSci 已有 OCR 与文档处理配置；本 Skill 使用当前工作区已选的提供者、语言和凭据状态，不引导用户另配一套 RetainPDF OCR。翻译与版面引擎、模型权重不随 Skill 安装。Skill 不能增加工具权限，也不能把缺失的运行组件描述为已就绪。

## 执行顺序

1. 确认当前任务已登记要处理的 PDF、目标语言和用户希望保留的内容（双栏、公式、表格、图注、参考文献）。避免自行读取或上传无关文件。
2. 首页“文献翻译”可由用户直接选择本地 PDF、目标语言和翻译库位置；当前 ScanSci Agent 租约尚未开放翻译任务提交工具。Agent 在当前租约内检查是否有已授权的提交、状态查询和产物读取能力；缺少时引导用户使用首页入口，不能假装已经生成译文。
3. 读取 ScanSci 当前 `document_processing.ocr` 与 `document_processing.mineru` 配置，确认已选提供者、启用状态、语言、实际执行位置和运行状态。文本 PDF 可直接提取文字；扫描页在 OCR 关闭或已选提供者不可用时要报告原因。`mineru`、AI Studio Paddle 和 DeepSeek 云端服务会发送文档或图片，必须沿用用户已配置的授权；不能自行切换到其他远程服务。凭据只交给 Host 管理，不放进聊天、日志或 Skill 文本。
4. 通过已授权工具引用 ScanSci 已登记的 PDF、创建异步翻译任务、查询终态，再读取 ScanSci 产物目录中的译文 PDF。任务失败时报告实际处理阶段和可公开错误；不可把原 PDF 或 OCR 原文冒充译文。不要向工具传任意本地路径。
5. 核对任务实际使用的 OCR backend 与页级文字块、坐标和阅读顺序；纯文本 OCR 结果不足以保证保留版式。打开最终 PDF，至少核对页数和可读性，以及标题、双栏顺序、公式、数字、单位、表格、图注和引用。无法核对的部分标为待人工检查；不要宣称逐页完美保真。
6. 确认 Host 已将通过质检的译文 PDF 保存到本地“翻译库”文件夹；知识库会自动列出该文件夹中的 PDF。此关联只提供文件管理，尚未建立证据索引，也不会自动加入问答范围。若任务登记失败但 PDF 已写入文件夹，报告实际路径；不要自行复制、移动或删除用户文件。
7. 交付译文 PDF 路径、翻译库记录、所用 OCR 与模型、成功/失败状态，以及值得复核的版面或术语问题。若需要 Markdown，先检查其语言和内容；RetainPDF 的 `markdown?raw=true` 可返回 OCR 原文，不能仅凭文件名判断它是译文。

## 边界

- 普通短句或段落翻译直接使用当前模型即可。
- 若复用 RetainPDF 的翻译或渲染引擎，应作为独立可选组件交付；不要把它的桌面安装包、Python/Rust 环境或模型权重并入 ScanSci `core`。ScanSci 的 OCR 与模型配置仍由 Host 管理。
- 本 Skill 不授权任意 shell、任意本地文件访问、未审查的 MCP 工具或远程文件上传。

设计参考：[wxyhgk/retain-pdf](https://github.com/wxyhgk/retain-pdf)，MIT。ScanSci 此处提供原创工作流说明，尚未集成 RetainPDF 引擎。
