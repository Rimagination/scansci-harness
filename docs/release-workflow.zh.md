# ScanSci 收敛式开发与发布门禁

> 发布渠道分级：`beta` 生成带风险说明、未做 Authenticode 签名的受邀内测包；`release` 生成必须通过 Authenticode 与 Ed25519 下载清单签名的正式候选包，并执行人工桌面验收。证书与私钥只存在于发布机，不写入仓库。

这套流程解决两个问题：开发期间不要每改一点就完整打包；交付时也不能再用源码测试、浏览器预览或“进程启动了”冒充 EXE 验收。

## 1. 开工前只允许一个 P0

当前任务写在 `config/release-scope.json`：

- `version`：当前唯一候选版本，必须与 `release-gate.json`、Python/Node 包和桌面构建默认值一致；
- `p0_objective`：本轮唯一目标；
- `acceptance`：可观察、可复核的验收项；
- `non_goals`：本轮明确不做的内容。
- `release_history`：只读冻结的旧 P0；v0.3.1 当前为 `superseded/frozen_unverified`，不等于 released。

目标或验收项变化时必须修改该文件。门禁会记录它的 SHA256；修改后不得沿用旧构建的测试证据。

## 2. 三层门禁，不重复做最贵的工作

日常修改先跑针对性门禁：

```powershell
.\scripts\release_gate.ps1 -Profile targeted
```

它只跑与桌面、对话、模型、Skill、资料库、幻灯片和门禁直接相关的测试，以及前端语法检查。失败时停止，不运行完整测试、真实模型或打包。

准备候选版本时跑源码门禁：

```powershell
.\scripts\release_gate.ps1 -Profile source
```

执行顺序固定为：针对性测试 → 完整测试 → ScanSci 框架内双模型与 Skill → 真实本地知识库。上一层失败，下一层不会运行。

如果刚刚已经通过 `targeted`，可以晋级原报告，避免重复针对性测试：

```powershell
.\scripts\release_gate.ps1 `
  -Profile source `
  -PromoteReport "<targeted 的 release-report.json>"
```

正式交付时运行：

```powershell
.\scripts\release_gate.ps1 -Profile release
```

如果 `source` 已通过，正式构建应晋级该报告，不重复完整测试、双模型和知识库：

```powershell
.\scripts\release_gate.ps1 `
  -Profile release `
  -PromoteReport "<source 的 release-report.json>"
```

它在源码门禁之后只构建一次，并继续检查包完整性、Windows 安装包、隔离目录中的安装→运行诊断→卸载、运行时诊断、正式 EXE `/api/health` 和桌面进程存活。每次输出到唯一目录：

```text
releases\<version>+<build_id>\
  ScanSci\ScanSci.exe
  update\ScanSci-<version>-windows-x64.zip
  update\ScanSci-<version>-windows-x64.zip.blockmap
  update\stable.json
  installer\ScanSci-<version>-windows-x64-setup.exe
  installer\installer-manifest.json
  diagnostics\
  visual-evidence\
  release-report.json
```

正式门禁强制 clean build；直接调用 `build_desktop.ps1` 的开发构建默认保留以 `build_id` 隔离的 PyInstaller 缓存。不同构建不共享 `dist`、workpath、specpath 或 metadata 目录。

`targeted-python` 还必须覆盖直接对话状态契约：20 轮以上历史不被浏览器截断，v1 历史可无损迁移，运行中当前项与队列可在重启后恢复，编辑与重试生成可追溯分支，快速队列修改按提交顺序落盘。

`frontend-browser-submit` 使用真实浏览器和本地确定性模型流验证点击/回车提交、灰色紧凑队列、拖动重排、最后一问编辑重做、重载恢复、稳定标题、独立回到底部按钮、语音转文字和跨对话并行。`dual-model-agent` 还会对正式托管模型逐个执行中文问答、英文问答和带随机校验码的真实多轮记忆验收；任一模型失败都不能通过正式门禁。

### Pi capability report v2 是内层强制证据

`release-report.json` 是发布编排器的外层报告；Pi 的内层能力证据是 `report_kind=scansci.pi-capabilities`、`schema_version=2` 的 JSON。外层报告只能在解析、重算并验证内层内容后晋级，文件“存在”不算通过。

targeted/source 的 Pi 步骤固定生成：

```text
<diagnostics>/pi-targeted-junit.xml
<diagnostics>/pi-capability-matrix.json
<diagnostics>/pi-capability-workspace.sqlite
<diagnostics>/pi-capabilities-deterministic.json
```

real gate 另生成：

```text
<diagnostics>/pi-capability-real-workspace.sqlite
<diagnostics>/pi-capabilities-real.json
```

内层报告必须包含 `protocol_version=7`、SDK 版本、`source_sha256`、bundle/matrix 的 path+SHA256+bytes、`fallback_count = 0`、run manifests、精确 10 轴、provider 状态与 mode-specific evidence。deterministic 报告证明当前 bundle 的真实 sidecar 工具循环；provider-real 还必须证明 10 次动态 schema 序列化和 10 个 image+tool 任务。无 provider 凭据时 real 状态只能为 `not_run` 并阻塞公开发布，不能用 deterministic 报告顶替。

对应命令由 `config/release-gate.json` 固定：`npm.cmd run build:pi-runtime`、`scripts/verify_pi_capabilities.py --validate-matrix-only`、`--mode deterministic` 和 real gate 的 `--mode real`。任何 direct fallback 或隐式 OCR/Host 交付都不算 Pi 成功；`fallback_count > 0` 直接拒绝。

## 3. EXE 人工验收不能被自动测试替代

Electron 迁移候选须额外验收：`ScanSci.exe` 能启动随包 Python 后端并在关闭窗口后终止；应用与外部网页处于不同分区；外部网页不能调用桌面 IPC；左右视图在窄屏可切换；文件选择、PPTX 导出、模型/组件入口和麦克风权限保持可用。现有发布脚本仍以 pywebview EXE 为基准；在 Electron 安装/卸载、签名、完整 ZIP + blockmap、自动更新和视觉证据全部纳入门禁前，不得把候选目录包报告为公开发布通过。

自动门禁通过后，如果缺少桌面证据，命令以退出码 `2` 停在 `awaiting_visual_evidence`，而不是错误宣称发布完成。它会生成：

```text
visual-evidence\visual-evidence.json
```

必须启动报告中记录的同一个 `ExecutablePath`，逐项验证并保留三张真实桌面截图：

- `home.png`：首页布局；
- `chat.png`：Enter、点击发送、回答及参考文献栏规则；
- `maximized.png`：最大化后任务栏仍可见。

把对应检查改为 `true` 后，用原报告续跑：

```powershell
.\scripts\release_gate.ps1 `
  -Profile release `
  -ResumeReport "<repo>\releases\<version>+<build_id>\release-report.json"
```

晋级仅在 `contract_sha256`、`scope_sha256` 和发布相关源码指纹 `source_sha256` 均未变化时成立；任一代码、安装器或契约发生变化，门禁拒绝复用。续跑会复用已通过步骤和同一个 EXE，不再重新跑模型、知识库或 20 分钟打包。只有 `release-report.json` 最终为 `passed`，才可以说“已交付候选”。

### 公开发布还需独立满足的条件

门禁的 `passed` 证明候选包可构建、安装、运行、卸载并完成桌面验收；它不替代外部发布权限。公开分发前还必须完成：

- 正式安装器与其中的 `ScanSci.exe` 都必须通过 Authenticode 验证；未签名包只能进入 `beta` 渠道；
- `stable.json` 以及正式构建引用的本地运行组件清单必须通过应用内置 Ed25519 公钥验证；
- 完成同一候选的安全扫描与高风险问题处置；
- 由网关维护者确认生产凭据、限流、监控与故障响应可用；
- 发布不可变下载地址、SHA256、版本说明和回滚方案。
- 将安装器、`installer-manifest.json`、完整 Windows ZIP、`.zip.blockmap`、本地运行时清单及其全部分片上传到不可变版本标签，再最后上传 `stable.json`；不得留下任何指向不存在资产的清单；
- 上传完成后由 `.github/workflows/verify-published-release.yml` 自动检查公开清单、差分/全量下载、本地运行时分片，并在干净 Windows 环境中完成安装、签名复核、运行诊断和卸载。

没有这些外部证据时，只能称为“已验证的发布候选”；公开稳定版必须等发布后验证通过。

## 4. 计划预览与报告

不执行任何命令，只检查契约并查看将运行的步骤：

```powershell
.\scripts\release_gate.ps1 -Profile release -PlanOnly
```

计划报告只写入 `.scansci-diagnostics\release-gate-plans`，不会在 `releases` 下制造一个没有 EXE 的空候选目录。

`release-report.json` 记录：

- `scope_sha256`、`contract_sha256` 与 `source_sha256`；
- 每一步的命令、开始/结束时间、耗时、退出码与日志；
- 正式 EXE 的绝对路径、`build_id`、大小、文件数和 SHA256；
- 安装包路径、SHA256、大小、签名状态，以及安装、运行、卸载验证结果；
- 双模型、Skill、知识库、运行时和桌面证据路径；
- `planned`、`running`、`failed`、`awaiting_visual_evidence` 或 `passed` 状态。

报告和日志不得包含 API Key。密钥只保留在既有服务端 Secret / 系统凭据链路中。

## 5. 什么时候才运行完整测试

- 产品代码尚在快速迭代：只跑 `targeted`。
- 针对性门禁稳定且准备候选版本：跑 `source`。
- `source` 已通过且确实要交付 EXE：跑一次 `release`。
- 只补桌面截图或人工勾选：用 `-ResumeReport`，不重新打包。

任何“顺便优化”先写入下一轮候选，不并入当前 P0。OpenCode 仅在某个明确问题缺少设计依据时查相应模块，不做全量迁移。

## 6. 受邀内测包（无需购买证书）

`beta` 仍用于受邀内测且不要求购买代码签名证书；`release` 用于正式候选并强制签名：

```powershell
.\scripts\release_gate.ps1 `
  -Profile beta `
  -PromoteReport ".scansci-diagnostics\release-gates\<version>+<source-build-id>\release-report.json" `
  -BuildId "beta-20260729"
```

`beta` 会复用同一源代码指纹已通过的 source 测试证据，并自动完成：干净构建、包完整性、未签名 Inno 安装包、安装/启动/卸载、运行时诊断、健康检查和桌面进程存活检查。

交付目录为：

```text
internal-beta-releases\<version>+<build-id>\
  installer\ScanSci-<version>-windows-x64-setup.exe
  beta-delivery\SHA256SUMS.txt
  beta-delivery\BETA-README.zh-CN.md
  beta-delivery\BETA-FEEDBACK-TEMPLATE.md
  beta-delivery\beta-distribution.json
  release-report.json
```

`BETA-README.zh-CN.md` 会明确说明“未签名、仅限受邀测试”、Windows 可能出现的未知发布者提示、SHA-256 校验方式、卸载方式及反馈时禁止提交的敏感数据。`beta-distribution.json` 绑定被实际安装验收过的安装包 SHA-256；只有它与 `release-report.json` 同时为 `passed`，才可发给受邀测试者。

## 7. 正式版签名发布机准备

正式发布必须在独立发布机上配置以下条件；`config/release-gate.json` 将 `signature_required` 设为 `true`，缺少任一项都会在构建安装包前失败。`beta` 配置仍可生成带明确风险说明的未签名受邀测试包：

- 安装在当前用户 `CurrentUser\\My` 证书库中的、未过期且可访问私钥的 Authenticode 代码签名证书；
- Windows SDK 或 Visual Studio 提供的 `SignTool.exe`；
- 由发布运维方提供的 HTTPS RFC 3161 时间戳服务地址。

在发布机的受控环境中设置（不要写入仓库、日志或安装包）：

```powershell
$env:SCANSCI_SIGNING_CERT_THUMBPRINT = "<40 位证书指纹>"
$env:SCANSCI_TIMESTAMP_URL = "https://<timestamp-service>"
```

门禁会先签署并验证 `ScanSci.exe`，随后才计算包哈希、生成更新 ZIP 与 blockmap；安装器阶段只验证这份已签主程序并签署安装器，不再改写主程序。隔离安装验收还会再次验证安装目录内的 `ScanSci.exe`。安装器清单记录两个文件的签名状态和 SHA256，但不记录证书指纹、私钥或时间戳地址。

### 下载清单 Ed25519 签名

Windows Authenticode 代码签名证书通常需要付费。下载清单使用独立的 Ed25519 密钥，不需要购买证书。它保护 `stable.json`、完整 ZIP、blockmap 和运行组件 SHA256 之间的绑定关系。

首次建钥应在离线或受控发布机执行一次。下面的命令创建只含 Base64 原始私钥的文件，并输出可公开的 Base64 公钥：

```powershell
python -c "import base64; from pathlib import Path; from cryptography.hazmat.primitives import serialization; from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; k=Ed25519PrivateKey.generate(); Path('scansci-release-v1.key').write_text(base64.b64encode(k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode(), encoding='utf-8'); print(base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode())"
```

操作要求：

- 把输出的公钥写入 `config/release-gate.json` 的 `package.release_public_key`；公钥会进入 `build-info.json`。
- 将 `scansci-release-v1.key` 移到发布机受限目录并备份，禁止提交到 Git、聊天、日志或安装包。
- 在正式发布会话设置 `$env:SCANSCI_RELEASE_MANIFEST_PRIVATE_KEY_FILE = "<私钥绝对路径>"`。
- Node、Tectonic 与 `local-transformers` 的清单在全部不可变下载 URL 确定后，用同一密钥签名：

```powershell
python .\scripts\sign_release_manifest.py <component-manifest.json> `
  --private-key $env:SCANSCI_RELEASE_MANIFEST_PRIVATE_KEY_FILE `
  --key-id scansci-release-v1
```

正式门禁会在开始耗时构建前核对私钥、公钥是否匹配，验证三个运行组件远程清单的签名，并让 `package_desktop_release.ps1` 对最终 `stable.json` 原子签名。发布后工作流再使用仓库中的公钥复核线上清单。
