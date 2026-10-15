# ScanSci 桌面构建与按需模型安装

## 发行策略

ScanSci 主程序和本地模型推理栈具有不同的更新频率。界面、对话、知识库和文档功能经常更新；`torch` 与 `transformers` 只在本地推理兼容性变化时更新。把两者绑定会让每次主程序修改都重复复制和分发数百 MB 的依赖。

现在发行物分为：

- `core`：轻量桌面包。包含界面、对话、知识库、文档、PPT、Skill、MCP 和已构建的 `pi_runtime/main.mjs`；**不包含** PyTorch、Transformers、Node executable 或任何模型权重。
- `node`：Pi sidecar 的独立可执行运行组件，由 `node_component_manifest_url` 单独安装、校验和升级。bundle 属于 core 资源，Node executable 不属于 core；两者缺一时 Pi 只能明确报不可用，不能偷偷改用系统 shell 或打入另一套 Node。
- `local-transformers`：ScanSci 默认推荐的本地 AI 组件，包含 PyTorch、Transformers、sentence-transformers 与回环 sidecar；用于 Qwen3-ASR、原生 Hugging Face 视觉模型、语义检索和重排。
- 用户模型：由用户在“设置 · 本地模型”中按需下载，默认进入 Transformers 路线；Ollama / LM Studio 等外部运行时保留为可选连接，模型文件保留在用户选择的位置，不属于应用包。
- `full`：仅用于内部支持或离线交付的技术包，必须显式指定，不是公开发行默认值。

轻量包的硬约束是：**先有可用运行时，再允许下载依赖该运行时的模型；下载完成后校验并启用。** 资料接入只建立文档与基础关键词索引，绝不偷偷下载大模型。这样用户不会出现“已经下载模型但程序无法运行”的状态。

## Electron 迁移候选

Electron 主进程负责桌面窗口、受限原生桥接和独立网页视图；Python `electron_backend.py` 继续提供现有科研 API。源码预览运行 `npm run desktop:electron`，Electron 二进制为开发依赖。候选 Windows 目录包运行 `.\scripts\build_electron.ps1 -ReleasePublicKey <官方 Ed25519 公钥>`：先构建不带 pywebview/Node executable 的 `ScanSciBackend.exe`，再把它和 Electron 壳打入 `dist-electron\win-unpacked`。未配置正式公钥时只运行源码预览，不制作可分发包。用户工作区仍在 `%LOCALAPPDATA%\ScanSciPi`，Electron 浏览器资料放在其 `electron` 子目录，不进入公开包或更新 ZIP。

这一路线会把 Chromium 随桌面包一起分发，包体和主程序更新量需要重新测量；Electron 内置 Node 目前只用于桌面主进程，Pi sidecar 仍按现有独立 Node 组件契约运行。完整 ZIP、64 KiB blockmap、签名和全包回退继续沿用现有协议。现阶段 Electron 候选仅供源码/内部预览；自动安装更新、签名、安装/卸载和正式包视觉证据通过发布门禁后，才能替代 pywebview 公开包。

## 日常开发

日常修改直接运行源码和针对性测试，不打包：

```powershell
python -m pytest tests/test_build_profiles.py tests/test_local_runtime_component.py -q
python -m scansci_html.desktop
```

确实需要桌面包时，默认生成轻量 `core` 包：

```powershell
.\scripts\build_desktop.ps1 -OutputDir dist-preview -BuildId preview
```

构建内部离线支持包时才显式选择 `full`：

```powershell
python -m pip install -e ".[desktop,local-gpu,rerank]"
.\scripts\build_desktop.ps1 -PackageProfile full -OutputDir dist-support -BuildId support
```

`BuildId` 只进入交付元数据。PyInstaller 的 `workpath` 使用由 Python 版本、PyInstaller 版本、依赖文件和构建配置计算的稳定 `CacheKey`，因此新的构建编号不会自动造成冷构建。并发构建同一缓存会被锁拒绝，避免缓存损坏。

只有正式门禁或确认缓存异常时才加 `-Clean`。

## core 的差分更新

桌面更新采用与 Cherry Studio / electron-builder 同类的 **blockmap 差分下载**：发布时仍然提供完整 ZIP，旁边额外发布一个 `.zip.blockmap`，而不是维护一套容易失配的“文件级补丁”。blockmap 默认以 64 KiB 为区块，用 SHA256 描述每个区块。

`stable.json` 的 Windows 条目应包含完整包和 blockmap：

```json
{
  "schema_version": 1,
  "windows": {
    "url": "https://download.example/ScanSci-0.4.0-windows-x64.zip",
    "sha256": "<完整 ZIP 的 SHA256>",
    "size": 123456789,
    "blockmap": {
      "url": "https://download.example/ScanSci-0.4.0-windows-x64.zip.blockmap",
      "sha256": "<blockmap 的 SHA256>",
      "size": 12345,
      "block_size": 65536
    }
  },
  "signature": {"algorithm": "ed25519", "key_id": "scansci-release-v1", "value": "<Base64 签名>"}
}
```

更新器会保留最近一次已经下载并通过校验的完整包作为本地基线。检测到新版本后，它会：

1. 下载并校验新的 blockmap；
2. 将新旧 SHA256 区块逐一比较；
3. 从本地基线复制未变化区块，只通过 HTTP `Range` 请求下载变化区块；
4. 对重建出的完整 ZIP 再做文件大小、SHA256 和 ZIP 安全校验，然后交给重启替换脚本。

以下情况会自动回退到完整 ZIP，不会生成半包或覆盖当前安装：没有可用的本地基线、CDN 不支持 `Range`、blockmap/区块响应校验失败，或重建后的完整包 SHA256 不匹配。因此差分更新是节省流量的优化，不改变完整包始终可用的发布契约。

发布脚本会自动生成 blockmap：

```powershell
.\scripts\package_desktop_release.ps1 `
  -BuildDir dist-core\ScanSci `
  -Version 0.4.0 `
  -PackageUrl https://download.example/ScanSci-0.4.0-windows-x64.zip `
  -OutputDir release
```

生产 CDN 必须正确转发 `Range` 请求并返回 `206 Partial Content`；如果 CDN 只返回 `200 OK`，客户端会安全地走全量下载。`local-transformers` 组件仍按自身版本和 SHA256 清单更新，不会因为 core 的差分更新被重复下载。

正式 `release` 门禁会把完整 ZIP、`.zip.blockmap` 与 `stable.json` 作为同一组不可拆分的更新资产构建和校验。公开上传时先上传完整 ZIP 和 blockmap，最后上传 `stable.json`；上传完成后运行：

```powershell
python .\scripts\verify_update_channel.py `
  --manifest-url https://github.com/Rimagination/scansci-harness/releases/latest/download/stable.json `
  --expected-version 0.4.0
```

审计会核对 schema、Ed25519 签名、版本、HTTPS 地址、逐跳重定向目标、完整包大小、SHA256、blockmap 身份和 HTTP Range。Range 不可用不会破坏更新通道，结果会明确标记使用完整包回退。

## 构建本地 AI 组件

本地组件只在其版本或推理依赖变化时构建：

```powershell
.\scripts\build_local_runtime.ps1 -Version 1.0.0 -Archive
```

输出目录包含：

- `ScanSciLocalRuntime\ScanSciLocalRuntime.exe`
- `ScanSciLocalRuntime-1.0.0.zip`
- `local-transformers.json`，记录版本、下载地址和 SHA256

上传 ZIP 后，可通过 `-PackageUrl` 写入正式 HTTPS 地址。主发行清单也可以直接包含：

```json
{
  "version": "0.4.0",
  "windows": {"url": "https://example/ScanSci-core.zip", "sha256": "..."},
  "components": {
    "local-transformers": {
      "version": "1.0.0",
      "windows": {"url": "https://example/ScanSciLocalRuntime-1.0.0.zip", "sha256": "..."}
    }
  }
}
```

首次使用本地模型时，主程序会引导用户安装 `local-transformers` 组件；用户也可以在“设置 · 本地模型”中手动选择“安装运行组件”。主程序读取同一发行清单，校验组件并安装到：

```text
%LOCALAPPDATA%\ScanSci\runtimes\local-transformers\versions\<version>\
```

首次运行引导和“设置 · 常规 · 目录与文件”允许用户分别选择“模型缓存目录”“本地运行组件目录”和“向量索引目录”。留空时沿用上面的 `%LOCALAPPDATA%` 默认位置；选择后，新下载会写入用户指定目录，已有运行中的进程不会被强制中断，下一次启动会完整采用新目录。模型缓存目录使用 Hugging Face hub 目录语义，运行组件目录仍包含 `versions/`、`downloads/` 和 `active.json`。

向量索引目录只影响每个知识库的 `*.libraries/<notebook>.sqlite` 证据/向量数据库。更换目录时，应用先通过 SQLite 备份和完整性校验，再更新 workspace 中的路径；原目录保留为可恢复副本，不会直接删除用户数据，也不会重新调用嵌入模型。资料增量索引会按证据文本摘要复用未变向量，只为新增或变更的证据重新嵌入。

`active.json` 指向当前版本。更新 core 不触碰该目录；只有组件版本变化才下载新的运行时。组件下载和启用均经过 SHA256 校验，失败时会保留已经可用的旧版本。

主程序必须比较 `active.json` 中的组件版本与当前 core 要求的组件契约版本。旧版本仍保留在版本目录中，但在完成兼容更新前不能被误报为“已就绪”；设置页应明确显示“更新本地运行组件”。组件更新只原子切换运行时目录，不删除 `%LOCALAPPDATA%\ScanSci\models\`、Hugging Face 缓存或用户配置的其他模型根目录，因此已经下载并校验过的模型权重不会重复下载。

### 本地运行时的进程隔离与就绪判定

`local-transformers` 必须采用“稳定守护进程 + 按模型启动的子进程”结构。守护进程只负责健康检查、请求转发、状态记录和子进程生命周期；PyTorch、Transformers、模型权重与生成状态只能存在于模型子进程中。模型加载失败、原生库崩溃、显存或页面文件不足时，守护进程应保持可响应并返回可诊断的结构化错误，不能让整个本地服务随模型一起退出。

模型存在于磁盘不等于可用。只有隔离环境真实完成“加载指定模型并产生非空回复”的兼容性探测后，才能标记 `ready=true` 与 `runtime_compatible=true`。探测必须离线复用已经安装的组件、用户模型目录和 Hugging Face 缓存；不得为了重新检测而下载模型。待验证、加载失败或生成失败的模型仍可显示“文件已存在”，但必须明确显示为未就绪，不能进入正常模型选择列表或被自动路由使用。

流式生成过程中如果模型子进程异常退出，守护进程应保留已经发送的 token，随后发送明确的错误事件和结束事件，并把运行时降级状态写入健康检查。下一次请求可以重启新的模型子进程，不需要重启主程序或重新安装组件。

### 本机主力模型推荐

“设置 → 本地模型”在模型市场之前只展示一个本机助手推荐。Host 离线读取系统内存、NVIDIA 显存和已明确配置的 Ollama 模型目录剩余空间；未知显卡按保守档处理。当前首批目录为 `qwen3.5:9b`、`qwen3.5:4b` 和 `qwen3.5:2b` 的 Ollama 四位量化版本：显存至少 12 GB 且内存至少 16 GB 时推荐 9B，显存至少 5 GB 或内存至少 24 GB 时推荐 4B，其余内存至少 8 GB 时推荐 2B。4B 在 8 GB 显存设备上以 32K 上下文完成 Pi 检索、Word 和 PPT 连续任务，Ollama 显示 100% GPU、约 4.2 GB 占用；9B 与 2B 仍从 16K 开始。模型标称的 256K 窗口不作为本机可用窗口承诺。空间不足时停止推荐下载。

上述 Ollama 目录只针对已经运行本机 Ollama 的用户。新用户的默认路径是 ScanSci 独立的 `local-transformers` 组件；设置页不得为了获得“主力模型”引导其另装 Ollama。当前内置对话接口只返回文本，没有完整的 Pi 工具调用响应，因此不能把它宣传为已可承担主力模型。内置主力模型进入自动推荐前，必须同时验证量化权重在目标 Windows 内存/页面文件条件下加载、16K Pi 任务、工具调用、证据引用和故障隔离；已有 Ollama 模型继续作为可选连接复用。

Host 与 Pi 的 `model-runtime` 元数据接受明确声明的 16K 及以上窗口，并对 16K–32K 采用较小的输入、输出和压缩预算；无法识别的窗口仍保守回退到 32K。Ollama 原生适配中的 `num_ctx` 必须使用同一声明值；4B 预设明确声明 32K。

普通对话沿用 Ollama 的 OpenAI 兼容接口，其上下文取运行时当前默认值；该接口目前不接受请求级 `num_ctx`，所以界面只把预设窗口标为 Pi 任务上下文，不能承诺普通对话也已获得同样窗口。

推荐只对已有 Ollama 用户提供显式安装入口。未连接 Ollama 时显示内置主力能力的真实状态，不请求安装第三方运行时，也不改变 `local-transformers` 服务 ASR、视觉、检索和重排的默认路线。推荐安装只连接本机回环 Ollama，支持用户设置的端口；下载器与状态检测必须指向同一实例，地址在当前进程中变更时要求重启应用后再安装。模型下载完成仅表示文件存在。用户点击“验证并设为主力”后，Host 必须经实际使用的 OpenAI 兼容对话接口完成加载、简短证据引用和一次工具调用，再以 Ollama 原生接口按 `num_ctx=16384` 完成生成探测；任一步失败均保留原来的 `active_model` 和角色映射。Ollama Qwen3.5 的普通请求通过 `reasoning_effort: none` 关闭隐式长思考，显式思考请求才开启；Pi 的 Ollama 原生适配遵守同一选择。通过后一次性把对话、任务、写作、演示映射到该模型，原有模型仍保留。

社区微调、蒸馏和量化版本可以进入实验候选。它们的来源、基础模型、量化格式、许可与版本必须可追溯；作者自报指标只作为候选发现依据。要进入自动主力推荐，还需在 ScanSci 的中文科研问答、证据引用、工具调用和速度用例中与同尺寸官方基线比较，记录失败样例。已安装模型页同时展示 Hugging Face 快照和 Ollama 清单，所以用户已有的 `qwen3.8-4b-distill` 可见且保持可手选；清单中的“已安装”不代表已通过主力模型探测。

## 正式发行

正式门禁默认构建 `core`。它不携带大型推理依赖，也不预装模型权重：

- `build-info.json` 的 `package_profile` 为 `core`；
- `pi_runtime/main.mjs` 必须存在且通过 bundle hash/受限工具循环诊断；Node executable 继续由独立组件清单提供，不能为通过诊断把它塞回 core；
- 资料导入仅建立资料卡、章节和基础关键词索引；
- 用户默认通过可信 HTTPS 清单安装 `local-transformers` 组件，也可连接已经安装的 Ollama、LM Studio 等外部运行时；
- 只有运行时健康后，界面才允许下载 Qwen3 Embedding 0.6B 与 Qwen3 Reranker 0.6B；下载完成后自动校验并构建语义索引；
- 发行包必须低于 `core` 的体积门禁，原有桌面对话、知识库、模型和窗口验收继续通过。

Pi v0.4.0 不改变更新契约：正式资产仍是完整 Windows ZIP + 可选 64 KiB `.zip.blockmap` + `stable.json`，完整 ZIP 永远是差分失败的安全回退。`local-transformers`、Node、Tectonic 和模型权重保持独立版本/清单，不因 core 或 Pi bundle 小改动重复分发。

官方按需运行组件必须使用国内可访问、带 SHA256 的发行清单构建：

```powershell
.\scripts\build_desktop.ps1 -PackageProfile core -RuntimeManifestUrl <国内可访问的清单地址> -OutputDir dist-core -BuildId compact
```
