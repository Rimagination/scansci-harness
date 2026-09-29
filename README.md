<p align="center">
  <img src="assets/scansci-harness-mark.svg" alt="ScanSci Harness 标识" width="88" height="88">
</p>

<h1 align="center">ScanSci Harness</h1>

<p align="center">
  <img alt="Windows desktop" src="https://img.shields.io/badge/Windows-desktop-087f70?style=flat-square">
  <img alt="Powered by Pi" src="https://img.shields.io/badge/Agent-Pi-078ba9?style=flat-square">
  <img alt="Evidence first" src="https://img.shields.io/badge/Research-evidence--first-0b1b3a?style=flat-square">
</p>

<p align="center"><strong>证据优先的科研 AI 工作台。</strong> 找论文、核验证据、写综述、做幻灯片；每一步都能回到原文，证据不足时明确说明。</p>

<p align="center">
  <a href="https://github.com/Rimagination/scansci-harness#user-content-核心能力">核心能力</a> ·
  <a href="https://github.com/Rimagination/scansci-harness#user-content-界面预览">界面预览</a> ·
  <a href="https://github.com/Rimagination/scansci-harness#user-content-快速开始">快速开始</a> ·
  <a href="https://github.com/Rimagination/scansci-harness/releases">下载</a>
</p>

## 核心能力

### 文献发现与获取

- **多源搜索**：30+ 预配置模型供应商（OpenAI、DeepSeek、智谱、硅基流动……），国内直连和国际模型开箱即用。
- **Agent Reach 互联网路由**：内置 12 个只读互联网渠道——网页、RSS、GitHub、B 站、V2EX、YouTube 等，无需额外安装 CLI。登录态页面自动升级到内置浏览器桥接。
- **全文获取**：支持 PDF、DOCX、PPTX、EPUB、HTML 等 22 种格式导入；Zotero 和 Notion 集成可将外部知识库同步到本地。

### 证据检索与问答

- **句子级证据搜索**：在本地论文库中以句子粒度检索，每条答案绑定原文、引用和定位信息。
- **语义 + 关键词双路召回**：sqlite-vec 向量嵌入与词法检索并行，自动融合排序。支持本机运行 BGE-M3、GTE 等嵌入模型。
- **GPU/CUDA 智能检测**：模型市场标注 GPU 需求（CPU / 推荐 / 必需），设置页显示 CUDA 状态和安装引导。
- **文献综述生成**：多章节证据综述，逐句验证出处，自动补充遗漏来源，确保每一条引用都有原文支撑。

### PDF 与文档解析

- **MinerU 云端解析**：可选启用 MinerU API，对复杂排版的学术 PDF 进行高精度结构提取（公式、表格、多栏）。
- **多级回退**：MinerU（云）→ Docling（增强）→ MarkItDown（默认）→ pypdf（兜底），自动选择最佳解析器。
- **多 OCR 引擎**：Tesseract（本地默认）、Windows 系统 OCR、Paddle、DeepSeek OCR 可切换，中文 + 英文混排支持。

### 交付物生成

- **学术幻灯片**：6 套模板（通用学术、SCQA、答辩左右导航、国基金答辩、极简文献），支持从 PDF/DOCX 自动生成 PPTX。
- **办公文档**：内置 DOCX、XLSX、PPTX 创建工具，支持公式、样式、结构校验。
- **LaTeX / PDF**：自动检测 TeX Live 或 Tectonic，编译 LaTeX 并校验输出。

### 桌面应用

- **Windows 原生窗口**：无边框设计、最大化任务栏可见、自适应窄屏（≥800px）。
- **本地语音转写**：Qwen3-ASR 在本机运行，音频不上传。
- **本地视觉模型**：默认通过 ScanSci 的 Transformers 本地运行组件运行 MiniCPM-V 4.6，图片理解完全离线；Ollama 作为可选外部连接。
- **主题与字体**：浅色 / 深色 / 跟随系统、字体缩放、强调色自定义。

### 安全与可控

- Agent 默认只能使用白名单科研工具，Shell 和文件修改工具默认关闭。
- API 密钥保存在系统凭据管理器（Windows Credential Manager），不写入���置文件或日志。
- 任务支持暂停、续接、自动纠错和 checkpoint，中途失败不丢失进度。

## 界面预览

<img src="assets/scansci-pi-home.png" alt="ScanSci Harness 首页与科研工作入口" width="100%">

## 快速开始

### Windows 安装包

从 [GitHub Releases](https://github.com/Rimagination/scansci-harness/releases) 下载测试版安装包。安装前请核对发布页的 SHA-256；当前测试版可能显示"未知发布者"或 SmartScreen 提示。

桌面版更新支持 blockmap 差分下载：有可用的上一版缓存且下载服务支持 HTTP `Range` 时，只下载变化区块；否则自动回退到经过 SHA-256 校验的完整 ZIP。主程序更新不会重新下载独立的 `local-transformers` 运行组件。

### 从源码运行

```powershell
# 安装核心依赖 + 桌面窗口
python -m pip install -e ".[desktop]"

# 浏览器预览模式
python scripts/scansci_preview_entry.py `
  --workspace workspace.sqlite `
  --evidence-db html-papers/evidence.sqlite `
  --host 127.0.0.1 --port 8781

# 桌面窗口模式
python scripts/scansci_desktop_entry.py
```

可选依赖组：

| 安装项 | 用途 |
|---|---|
| `[desktop]` | 桌面窗口（pywebview） |
| `[local-gpu]` | 本机 GPU 嵌入与重排序 |
| `[rerank]` | 交叉编码器重排序 |
| `[enhanced-document]` | Docling 增强文档解析 |

```powershell
# 完整安装
python -m pip install -e ".[desktop,local-gpu,rerank,enhanced-document]"
```

## 构建 Windows 安装包

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_desktop.ps1 `
  -Mode onedir -PackageProfile full -Name ScanSci
```

正式发布由 `scripts/release_gate.ps1` 驱动，包含三层门禁（针对性 → 源码 → 发布 EXE），详见[发布工作流](docs/release-workflow.zh.md)。

## 开发

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q
```

运行时诊断：

```powershell
scansci doctor capabilities --root . --json
```

## 技术架构

- **Pi Sidecar**：模型会话、工具选择、流式输出、取消、恢复与上下文压缩。
- **ScanSci Core**：项目空间、证据库、任务状态、引用验证与交付物生成。
- **统一传输层**：在 Chat Completions、Responses 和 Anthropic 协议间复用重试、SSE 和错误归一化。
- **可选 Harness**：PydanticAI、OpenAI Agents SDK、LangGraph 按需安装，不影响默认启动路径。
- **托管运行时**：Node.js 和 Tectonic 作为组件化管理，无需用户手动安装。

更多文档见 `docs/` 目录：先读根目录 [`AGENTS.md`](AGENTS.md) 和[多 Agent 启动规范](docs/agent-startup.zh.md)，再按需阅读架构设计、证据 RAG 方案、研究 Agent 架构、PaperQA2 对比和错误账本。
