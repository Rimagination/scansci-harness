# 并行智算云生图接入记录

日期：2026-09-09。交付范围：源码接入与 Pi runtime bundle，未发布桌面安装包。

## 使用

1. 在设置 → 模型服务中选择「并行智算云（Paratera）」，启用服务并保存 API Key。
2. 在输入框模型菜单选择带“生图”标签的 `WanX2.1-T2I-Plus`，输入图片描述并发送；这是按服务商计费的模型。也可在设置 → 默认能力 → 图像生成中指定它，供科研工具按需使用。
3. 图片出现在回复中，可下载原图，历史记录保留本地链接。显式生图不依赖聊天模型。已有默认模型不会自动更换；CogView 仍保留，但当前账户实测存在上游鉴权问题，见下方验收记录。

依据官方[生图说明](https://ai.paratera.com/document/llm/modalAbility/image)和[API 地址说明](https://ai.paratera.com/document/llm/api/assistance/Chatbox)，使用 `https://llmapi.paratera.com/v1/images/generations`，Bearer 鉴权，模型 ID 为 `GLM-CogView3-Flash`。当前模型目录的「限时免费」来自服务商页面，实际计费以服务商为准。

## 实现边界

遵守 project-governance 的 Pi/Host 分工：Pi 使用通用 `generate_image` 工具；Host 授权、读取凭据、解析独立图像角色、调用服务并原子保存图片。服务商名称与模型 ID 留在配置和服务传输层。API Key 不进入 Pi 工具参数、结果或设置 JSON。

同步兼容服务支持七种尺寸，默认 1024×1024；WanX 首批接入仅开放已验证的 1024×1024。支持 Base64 和 HTTPS 图片结果。生成文件通过受限的同源图片路由提供预览。输入框统一模型菜单展示纯生图模型并标记“生图”；纯生图模型不作为聊天模型调用。

工具返回图片时立即向界面发布产物事件，通过现有历史接口保存。后续文字回复失败时仍显示图片，并停止自动重发整轮请求，避免再次生图。任务内追问使用已有 Markdown 消息保存图片链接。

图片卡片沿用语义颜色变量；frontend-design 检查影响了预览高度、标题字号和深浅主题的呈现。

## 已运行的验证

- 12 项初始定向 pytest 通过：服务配置、授权契约、请求参数与落盘、路径限制、Pi 分派、历史与图片路由。
- 补齐英文/概念图触发和同步/流式消息检查后，相关 9 项通过；产物即时事件调整后，对应 2 项再次通过。合计覆盖 16 个不同测试用例。
- `python scripts/verify_image_generation_ui.py` 最终通过：真实本地服务与浏览器，模拟模型输出；检查 light/dark、图片加载、下载链接、生成后断线不重复生图，以及刷新历史后图片保留。脚本使用临时工作区，并模拟标题生成，不调用真实模型服务。
- `npm run build:pi-runtime` 通过；后续仅改 Python、界面和验证脚本，未重复构建未变的 Pi bundle。
- `node --check src/scansci_html/web/app.js` 通过。最终限定范围 diff 检查无空白错误。
- 对跨模块调用链启动一次独立只读审查，发现并修复生成后文本失败导致图片引用丢失的问题；恢复检查进一步发现并修复了重复生图重试。无二次独立审查循环。

本次沿用局部验证策略，未运行全库测试、完整桌面打包或发布门禁；源码/UI 改动未做哈希检查。无变化的测试与构建未重复执行。浏览器验证曾因节点更新与断言表达式失败，已修正验证脚本；上述通过结果均来自最终相关代码状态。

## Pi 交接与未完成项

- protocol v7 / TaskContract v2；Pi AI、coding-agent SDK 0.84.3；MCP SDK 1.29.0。
- 10 轴 capability matrix 本轮全部 `not_run`，没有新建或覆盖正式 matrix。此功能检查不计入真实 Pi 能力验收。
- 正式 `fallback_count`、degradation 数量未测量；真实 Paratera 验证 `not_run` 1 项；不将模拟输出记录为真实服务通过。
- source/matrix/bundle 哈希：本轮未生成，遵守源码改动不做哈希的轻量策略。
- 本轮未生成正式 run manifest 或 capability report；浏览器截图位于 `output/playwright/generated-image-light.png` 与 `output/playwright/generated-image-dark.png`，其中图片为界面验证用的单色夹具。
- 外部未完成项：配置真实 Paratera Key 后验证端到端生图；需要桌面交付时再执行正式打包与发布验收。下一轮 P0 无需调整；不声明完整 Pi 验收完成。

## 输入框选择生图模型（2026-09-09 后续批次）

统一模型菜单现在包含“生图”标签。选择独立保存，不覆盖原聊天模型或默认能力；未配置凭据的服务展示“待配置”。选择后仅使用本条文字，隐藏无关的联网、思考和文本上下文设置；切回聊天模型恢复原入口。frontend-design 与 impeccable 的局部检查影响了这些控件的显示和现有语义样式复用。

用户明确选择生图模型时，Host 通过现有图像服务直接执行，标记 `host-explicit-image`，不调用聊天模型规划或生成标题，不计作 Pi 自主编排成功。每条排队消息保留所选模型和稳定请求 ID；历史保留受限的模型标识。任务追问的图片保存到原任务，不覆盖既有研究产物。

生成前独占写入请求记录。相同 ID 的完成请求复用本地结果；参数变化或提交后结果不明时阻止重复请求。暂停/取消会停止等待，已发往服务商的请求可能继续执行，结果仍保存；恢复时可复用结果。当前没有服务商侧对账能力；用户主动新建请求会形成一次新的生成。

本批实际验证（各阶段结果不累计）：

- 前端相关 `test_local_models_ui.py`、`test_direct_chat_recovery_ui.py`、`test_direct_chat_queue_ui.py`：16 passed。
- 后端 `test_image_generation.py` 初始 24 passed；扩充后的 explicit_image 子集 21 passed；最后鉴权与可用性子集 38 passed、14 deselected。覆盖精确路由、同步/流式、任务追问、幂等与取消，以及托管/本地鉴权复用。
- 历史模型标识受限持久化定向测试：1 passed。
- `python scripts/verify_image_model_picker_ui.py`：真实浏览器 light/dark、选择与切回、队列快照、请求参数通过；模拟失败后 requests=1。截图为 `output/playwright/image-model-picker-light.png`、`output/playwright/image-model-picker-dark.png`。
- 跨模块批次完成一次交叉审查；发现共享鉴权不一致后已修复并局部验证，无新增依赖或接口端点。

当前工作区 Paratera 未配置 API Key，真实生图未运行，也未产生付费测试请求。请在设置 → 模型服务 → 并行智算云保存凭据。源码后端需要重启后生效。本批未运行全量测试、桌面打包或哈希检查；已通过且代码未变化的命令未重复执行。正式交付门禁纳入图像生成与历史测试，发布时运行。上述 Pi 版本、matrix 未验收及外部阻塞仍适用。

## 连接检测与生图拒绝诊断（后续修复）

用户随后已保存 Key；只读核对确认检测与生图使用相同密钥。历史生图失败被旧逻辑归并为“鉴权失败”，原始 HTTP 401/403 及拒绝详情未保留，无法从旧记录确定具体原因。模型列表检测成功不能证明生图权限。

- 检测继续仅 GET `/models`，使用首个非空密钥，与生图选择一致；返回 `checked_scope=model_catalog`、`generation_verified=false` 和明确文案。生图服务提示“模型列表接口可访问；生图权限尚未验证，未发起生图”。无有效 `data` 列表或含业务错误时不能报成功。
- 生图分别处理 HTTP 401、403、402、404、429、参数错误及服务异常。已知机器错误码进一步区分密钥、模型授权、配额、余额、内容策略和限流；403 不直接要求用户换 Key。
- 错误响应最多解析 16 KiB，只放行已知错误码。上游任意请求编号、原始服务消息、HTML、用户提示词、Authorization 和响应正文不进入 UI、历史或日志。无法识别原因时保留 HTTP 状态并明确下一步检查方向，不推断历史请求的真实拒绝原因。
- 复用既有 toast、消息和请求链路；无 UI 布局、主题或 API 端点变化，无新增依赖，无权限/租约扩张。已有发布门禁覆盖两个受影响测试文件，无需新增门禁流程。

新增诊断/检测定向测试 21 passed、81 deselected：覆盖状态分类、业务错误、异常正文、秘密不反射、单次提交，以及检测不执行生成。真实生图没有重试，未产生测试计费；本轮不声明 Paratera 生图已通过。源码变更需重启后生效，用户历史和凭据未修改。

原有生图回归 52 passed、15 deselected。跨模块诊断信息出界涉及隐私，进行一次独立只读综合审查，发现上游请求编号可能反射提示词；已取消该字段透传并补充回归。按局部策略未执行全库测试、桌面构建或哈希检查；未重复执行代码未变的检测测试和原有成功路径测试。P0、默认运行时及数据位置不变。

审查修正后只重跑受影响的 `test_image_generation.py -k image_service_error`：19 passed、52 deselected；此前已通过且未变化的检测用例 6 项继续有效。不同阶段结果不累计为测试次数。限定文件最终 diff 检查无空白错误。

## 用户授权的真实生图验收

执行 `scripts/verify_paratera_image_real.py`，读取既有系统凭据，通过 `ResearchAgentRuntime.chat_stream` 的真实显式生图链路请求原描述“生成一张哈利波特的证件照”。没有替换模型输出；记录包装器只统计真实 HTTP 请求并脱敏记录错误字段。

结果：**failed，未生成图片**。发送 1 次生图请求，3.55 秒，HTTP 401；服务商响应包含 `AuthenticationError: OpenAIException`、嵌套错误码 `1000`、“身份验证失败”，以及 `GLM-CogView3-Flash` 模型组没有可用回退的说明。证据：`.scansci-diagnostics/paratera-image-real-7b707042be394637bcd7833b9a8de48a/report.json`。

随后进行一次官方最小请求对照（相同密钥/地址/模型，仅 `model` 和 `prompt`，描述为白底红苹果、不传 size），仍 HTTP 401、相同嵌套错误，无图片。另做只读鉴权对照：相同密钥 GET `/models` 返回 200 且包含目标模型；不带密钥返回 401。

结论：现有密钥通过模型目录鉴权；ScanSci 调用和官方最小请求均在生图阶段失败，故障指向服务商的上游模型鉴权/授权。尚不能区分服务商通道凭据问题与账户模型授权问题，需要并行智算云支持核查。两次生图都失败，不能声明真实生图通过，也无法验收图片解码/落盘/预览。没有更换用户模型、修改凭据、自动循环重试或改用另一个计费服务。

官方接口依据：`https://ai.paratera.com/document/llm/modalAbility/image` 当次读取 HTTP 200，确认 CogView 使用 Bearer 与 `/v1/images/generations`，最小参数为 model/prompt。新增真实验收脚本只供用户明确授权后运行，不纳入默认测试，可能产生服务商费用。本轮仅新增诊断脚本与记录；未重复单元测试、独立审查、打包或哈希检查。外部鉴权阻塞解除后再运行真实验收。

### 另一上游 API 对照：WanX 成功

用户随后明确要求测试其他生图 API。相同 Paratera 密钥与服务地址，模型列表确认含 `WanX2.1-T2I-Plus`；按照官方文档向 `/v1/p003/text2image` 提交一张 1024×1024 红苹果图，再通过 `/v1/p003/tasks/{task_id}` 查询，未改用户模型设置。

`scripts/verify_paratera_wanx_real.py` 实测 **passed**：生成 POST 1 次，查询 4 次，任务 `SUCCEEDED`，HTTP 200，含下载与解码总耗时 18.19 秒。输出 PNG 1024×1024，已打开目视确认红苹果与白色桌面符合描述。报告和原图：`.scansci-diagnostics/paratera-wanx-real-d6a2d7bd65b747bba818e5127c09e2c2/report.json`、同目录 `wanx-test.png`。官方当次标价 0.29 元/张，实际扣费未查询。

这证明同一密钥可调用 WanX 生图，CogView 的上游鉴权/模型授权问题仍需单独排查。此处为真实 API 验证，尚未将 WanX 异步协议接入 ScanSci 模型菜单或任务恢复链路；不能据此宣称桌面已支持 WanX。仅新增显式收费验证脚本与记录，限定 diff 检查通过；没有产品实现变更，未跑全量测试、构建、独立审查或哈希检查。web-access 指引用于核对官方协议并直接请求 API，没有浏览器登录页操作。

### WanX 软件接入与真实界面验收

用户确认接入后，Paratera 目录追加 `WanX2.1-T2I-Plus`，分组“通义万相”、能力 `image`。现有配置加载时补齐新增模型，保留模型自定义名称、启用状态、地址、密钥和默认角色；不会自动把 CogView 切换成付费模型。模型服务引导明确按服务商计费，输入框复用既有“生图”菜单。

transport 单次提交 `/p003/text2image`，通过 `/p003/tasks/{task_id}` 查询；查询预算默认 180 秒、最多 300 秒和 75 次 GET，单次网络请求另有连接/读取超时。任务 ID 限定安全路径段，任务 JSON 限长 64 KiB，图片复用原有 HTTPS 下载、25 MiB 上限及原子落盘。失败、超时不重复 POST。取消前台等待后后台继续保存；同请求 ID 的完成结果直接复用。当前仍未持久化服务商 task ID，进程中断或查询失败后无法仅 GET 恢复，同 ID 会阻止重提；主动新建请求可能再次计费。

实际验证：

- `tests/test_app_settings.py -k paratera`：2 passed、30 deselected；验证新旧目录与用户配置保留。
- `tests/test_image_generation.py -k 'wanx or generate_image_posts_openai_compatible_payload_and_saves_artifact or generated_image_asset_rejects_path_escape'`：38 passed、69 deselected；覆盖单次提交、查询、异常状态、取消、幂等、安全下载和旧同步接口。
- `node --check src/scansci_html/web/app.js` 通过。配置测试加入既有发布门禁，真实收费脚本不加入自动测试。
- `scripts/verify_wanx_integration_ui.py` 通过真实浏览器菜单选择并发送红苹果描述：1 次 POST、5 次 GET，真实图片在页面解码为 1024×1024。后续下载检查因验证脚本使用相对 URL 中断，首次报告保留为 `verification_failed`，未改写成整次通过：`.scansci-diagnostics/wanx-ui-9d48d1283888429abb902d0e7df8d24e/report.json`。
- 修正验证脚本 URL 后，仅运行 `scripts/verify_wanx_integration_ui.py --existing` 检查刚才的历史图片，结果 passed：原图 HTTP 200、1024×1024、light/dark 截图、刷新历史后图片保留；0 次生成、0 次查询。报告及结果截图位于 `.scansci-diagnostics/wanx-ui-d42c0262c11449cba067f65d38526f53/`。原图 `artifacts/images/image-983c9b99f0bb4863b80d5533f708d7c8.png`，已目视确认。

本批总共只发起一次真实生图，未再请求 CogView。frontend-design/impeccable 影响了现有组件与“生图”标记的复用；Playwright 用于真实软件链路和两种主题验收。跨模块进行一次只读综合审查，无阻塞；限定 diff 检查通过。源码交付未执行全库测试、桌面打包或哈希检查；同状态已通过的测试与 Pi bundle 构建未重复运行。Pi protocol/SDK、10 轴及正式验收证据不变，本批不声明 Pi 能力验收或桌面包发布完成。下一轮 P0 无需调整。

### 2026-09-10：WanX 失败原因与任务追踪修复

用户报告“生成一张蜘蛛侠泡在无边泳池里的照片”失败。旧实现读取 `output.task_status` 后直接抛出通用 FAILED，未分类 `output.code`，也未保存 task ID，旧记录只有 `submitted`，无法复查原任务。

本批保持 provider transport / Host 请求记录边界：

- 首次收到提交结果和后续状态变化时，原子保存 `provider_task`；字段限已知状态、规范 UUID 任务编号、白名单机器错误码和本地生成的中文说明。未知 code、服务商原始 message、提示词、凭据和下载 URL 均不写入诊断。任务编号用于排查，不授权自动重提交或切换模型。
- 分类内容审核、疑似知识产权侵权、参数、鉴权、欠费、限流和内部错误；同时识别 `SUCCEEDED` 内失败的单个结果。未知错误明确提示原因未识别，避免猜测。
- 同请求 ID 保留失败说明并阻止重提。初始记录必须持久化成功才发送 POST；中间记录写入失败时保留初始防重记录，继续查询已付费任务，并在后续状态/完成时尝试保存。进程中断后的自动 GET 恢复仍未实现；已知 task ID 可用于显式只读诊断。

用户授权后，通过真实 `ResearchAgentRuntime.chat_stream` 用原描述提交 **1 次**：HTTP 200 接收，随后 FAILED，task ID `dcce0b30-4633-4cf5-b35d-cfb6d8374ccd`。初次结果报告 `.scansci-diagnostics/paratera-image-real-c3cf283e77424c008e739ce6f6fad2e1/report.json`。仅 GET 同一任务后确认 `output.code=IPInfringementSuspect`，上游固定错误说明为输入疑似涉及 IP 侵权；这是服务商策略判定，不作为法律结论。没有生成图片，没有改写描述，也没有提交第二次生成。

加入该已确认错误码后执行：

```powershell
python scripts/verify_paratera_image_real.py --model WanX2.1-T2I-Plus --task-id dcce0b30-4633-4cf5-b35d-cfb6d8374ccd
```

只读报告为 `queried`（生成请求 0、GET 1），任务仍 FAILED，软件已正确输出中文原因和任务编号：`.scansci-diagnostics/paratera-image-real-a7e9397ff2954a06a5a46b502f010dc0/report.json`。查询模式不代表生图成功。脚本去掉了旧的原始错误 prose 记录，仅输出受限诊断字段。

依据：[万相 2.1 API](https://help.aliyun.com/zh/model-studio/text-to-image-v2-api-reference)、[异步任务管理](https://help.aliyun.com/zh/model-studio/manage-asynchronous-tasks)。明确提及受保护角色的输入可能被拒绝；不对模型服务的全部提示词成功率作保证。

实际局部验证：初始 `test_image_generation.py -k 'wanx or explicit_image'` 70 passed；加入真实错误码后分类子集 4 passed；审查修复后 `-k 'checkpoint or wanx_failure_reason or task_saved or receipt'` 8 passed。阶段结果不累计。一次独立只读专项审查发现中间记录 I/O 错误可能中断已付费任务，已直接修复并局部验证，无复审循环。未修改布局、主题或 Pi 协议，未重复前端截图、全库测试、构建或哈希检查。正式 Pi matrix/source/bundle 证据未生成，P0 不变。

### 生图界面精简

按用户确认的界面建议调整，范围仅前端与浏览器验证脚本：

- 图片卡片依据受限尺寸比例收窄，等比例展示，不裁切；点击复用应用内图片预览，支持 Esc 关闭。
- 图片下方保留“本次图片”可展开描述，以及下载、复用描述、详情工具栏；服务商、模型、尺寸和回复用量移入详情。仍保留带正文回复的复制操作。
- 复用描述只回填输入框，不提交请求；已有不同草稿时提示用户先保留或清空，避免覆盖。
- 输入框默认约两行并随文字增长，移除厚阴影，保留焦点边框和键盘可见焦点；生图模式隐藏知识库入口，空闲按钮标“生成”，切回聊天恢复原入口。
- 默认侧栏宽度从 352 改为 288 CSS px，复用拖动功能，保留手动宽度；压缩导航间距和会话标题栏，移除重复“历史对话”小标题。
- 会话标题可用铅笔按钮就地编辑（Enter 保存、Esc/失焦取消），复用已有 title API；用户命名不会被后续自动命名覆盖。明确的图片生成完成记录显示“已生成”，未改任务状态语义。

frontend-design/impeccable 影响了比例、留白、语义色和现有组件复用。实现批次自检，无额外独立审查：本批没有后端公共接口、权限或数据迁移变更。

验证：相关 `test_local_models_ui.py`、`test_direct_chat_queue_ui.py`、`test_direct_chat_recovery_ui.py`、`test_dark_theme_css_contract.py` 共 38 passed；JS 语法检查通过。`scripts/verify_image_generation_ui.py` 检查 light/dark、820px 窄屏、图片尺寸、详情、放大/Esc、复用不提交和不覆盖草稿、输入框伸缩、模式切换、标题保存及重载历史，全部通过。测试关闭页面时本地 HTTP 服务记录 WinError 10053 客户端断连，不影响上述断言或历史恢复；未扩展到无关服务器日志改造。

`scripts/verify_wanx_integration_ui.py --existing` 检查旧真实图片，passed：1024×1024、下载可用、两种主题和刷新历史保留，生成 POST 0 / 任务 GET 0。报告及真实图片新布局截图位于 `.scansci-diagnostics/wanx-ui-33b97a6c0e7a4d94a222abdedfd4a258/`；测试夹具截图位于 `output/playwright/generated-image-{light,dark,narrow}.png`。最后模式切换修正后只重跑受影响浏览器检查，未重复无变化的后端、全库测试、桌面构建或哈希检查。P0、Pi 协议、默认模型、运行时及用户数据位置不变。
