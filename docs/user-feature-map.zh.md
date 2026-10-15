# ScanSci 用户功能地图（首批）

本地图从用户操作出发，列出入口、应出现的状态和现有验证。适用于当前 v0.4.2 工作树的直接对话、Agent 编排、PDF 资料接入与更新功能；产品边界以 [`config/release-scope.json`](../config/release-scope.json) 和 [项目治理](project-governance.zh.md) 为准。下列测试是**可执行的现有证据入口**，本地图本身不表示它们已在当前代码状态运行通过。

维护时先检查受改动功能的所有用户入口，再更新对应状态和测试引用。静态 UI 测试只证明页面结构或脚本契约，接口测试只证明调用边界；需要真实点击、重启或安装包的行为须单列人工/端到端证据。没有覆盖的入口保持显式记录。

## 直接对话与回复队列

**用户入口**

1. 首页“向 ScanSci 提问”输入框发送普通问题；进入会话后在底部输入框继续追问。
2. 从历史列表重新打开会话；在进行中的回复里再次发送消息，查看逐条后续队列。
3. 在队列里立即执行、编辑、删除或拖动排序；可暂停当前回复，随后检查原轮次结果或确认重新生成。失败回复可重试，最近一条用户消息可编辑并形成分支。

**可见状态与复核**

| 状态 | 应观察到的结果 | 现有证据入口 |
| --- | --- | --- |
| 空知识库、首次提问、流式回复 | 普通问候可走 direct_chat 快速通道；会话有稳定标题，回复进入历史。 | `tests/test_webapp.py::test_notebook_webapp_allows_direct_chat_without_a_library`、`::test_plain_greeting_uses_direct_chat_fast_lane`、`::test_direct_chat_ui_generates_title_once_from_first_turn`、`::test_notebook_server_streams_direct_chat_events`（接口/运行时）。 |
| 运行中继续发送 | 第二条消息进入本会话的逐条队列，默认完成后继续；“立即”按当前能力请求调整，不等于取消。 | `tests/test_direct_chat_queue_ui.py::test_second_message_is_queued_by_default_and_owns_its_delivery_mode`、`::test_each_queued_message_exposes_follow_up_and_immediate_adjust_actions`、`::test_multiple_follow_ups_remain_fifo_and_click_dispatches_per_row_mode`（静态 UI）；`tests/test_webapp.py::test_chat_steer_and_follow_up_routes_forward_validated_images`（接口）。 |
| 暂停、失败、恢复、编辑 | 暂停续跑和失败重试先查询 Host：原轮次还在运行时等待，完成时恢复结果，结果不明时确认后建立新分支；队列和当前轮次可恢复，旧失败尝试保留在分支记录。 | `tests/electron/direct_chat_resume_submission.test.cjs`（状态裁决）；`tests/test_direct_chat_recovery_ui.py::test_direct_chat_job_snapshots_include_recoverable_queue_state`、`::test_retry_uses_a_new_session_and_preserves_the_failed_attempt_as_a_branch`（静态 UI）；`tests/test_direct_chat_history.py::test_direct_history_persists_current_turn_queue_and_recovery_status`、`::test_editing_last_message_creates_one_durable_branch`（存储）。 |

**人工复核路径：**分别从首页和已打开会话发送；在回复尚未结束时连续发送两条，检查队列顺序并编辑其中一条；重开会话确认状态；模拟一次失败后重试。当前自动测试主要覆盖静态 UI、接口和存储，尚缺一条真实浏览器内完成“发送→排队→刷新→恢复→继续”的端到端证据。

## Agent 编排模式

**用户入口**

1. 首页输入框和会话输入框旁均有“Agent 编排模式”机器人按钮，按下后再发送开放任务。
2. 同一输入区可选择模型、联网策略、知识库和资料附件；这些选择影响本轮租约和工具库存。

**可见状态与复核**

| 状态 | 应观察到的结果 | 现有证据入口 |
| --- | --- | --- |
| 开关与执行 | 两个输入区都能显示按钮选中状态；开放任务由 Pi 工具循环处理，界面可见阶段/工具摘要与最终回答。 | `tests/test_webapp.py::test_agent_mode_uses_robot_icon_in_both_composers`（静态 UI）；`::test_agent_mode_routes_open_request_through_pi_without_expanding_writes`、`::test_managed_chat_uses_pi_agent_for_required_tools_and_reports_runtime`（模拟 Pi 的运行时检查）。 |
| 联网关闭、能力受限 | 关闭联网后 Pi 租约不包含联网工具和 MCP；Agent 开关本身不扩大写权限。 | `tests/test_webapp.py::test_agent_web_off_restricts_the_actual_pi_lease`、`::test_agent_mode_routes_open_request_through_pi_without_expanding_writes`（租约检查）。 |
| Pi 失败 | 用户收到明确错误；该轮保留可追溯状态，不静默改走 direct provider。 | `tests/test_webapp.py::test_agent_mode_failure_never_falls_back_to_uninstrumented_chat`、`::test_sync_agent_mode_failure_never_calls_direct_provider`（失败路径）。 |

**人工复核路径：**分别在首页和已有会话开启按钮，提交一个需要只读工具的任务，检查按钮、执行摘要和最终结果；再关闭联网执行任务，核对报告中的工具决策。现有测试未证明真实 provider 在这两个 UI 入口都完成了任务；真实能力通过状态仍以 [10 轴矩阵](agent-harness-p0-p2.zh.md)及当前发布报告为准。

## PDF 资料接入

**用户入口**

1. 首页或会话输入框的“添加资料 → 添加文件”可把 PDF 加入当前提问，并在消息中保留附件引用。
2. 左侧“知识库”进入个人知识库，可选择“链接文件”“链接文件夹”，也可将文件拖入知识库。首页/会话的“知识库”按钮用于选择本轮检索范围。
3. 浏览器预览缺少本机原始文件夹路径时，文件夹原位置绑定需在桌面端完成；界面会提示这一限制。

**可见状态与复核**

| 状态 | 应观察到的结果 | 现有证据入口 |
| --- | --- | --- |
| 本轮 PDF 附件 | 发送前看到文件条目；附件先完成暂存，刷新/重开后消息仍有可用引用。 | `tests/test_webapp.py::test_chat_attachments_reuse_file_type_icons_in_draft_and_history`、`::test_direct_chat_attachment_staging_returns_restart_safe_references`、`::test_direct_chat_history_api_preserves_attachment_reference_and_archive_actions`（UI 结构/接口）；`tests/test_direct_chat_recovery_ui.py::test_direct_chat_stages_attachments_before_starting_or_queueing_a_turn`（静态 UI）。 |
| 知识库链接与索引 | 文件或文件夹接入后显示扫描/索引进度；失败可见原因与重试入口；完成后可选择该库用于对话。 | `tests/test_webapp.py::test_selected_personal_library_appends_file_links`、`::test_library_import_job_exposes_progress_and_result`、`::test_folder_binding_is_persisted_before_background_indexing_finishes`（接口/任务）；`tests/test_knowledge_base_v2.py::test_adding_one_file_updates_one_document_and_preserves_old_evidence_and_vectors`（索引）。 |
| 不支持的来源 | 接入请求被拒绝，用户保留可诊断的错误。 | `tests/test_webapp.py::test_library_import_job_rejects_unsupported_sources`（接口）。 |

**人工复核路径：**用同一 PDF 分别走“本轮添加文件”和“知识库链接文件”，观察消息附件、知识库进度和检索范围的差异；再用无效来源检查错误提示。自动测试主要覆盖暂存、接口与索引；知识库拖放、桌面文件选择和 PDF 可视预览尚无对应的完整用户旅程证据。

## 应用与运行组件更新

**用户入口**

1. 标题栏更新提示可展开版本卡片并启动可安装的应用更新；“设置 → 软件更新”可检查版本、查看说明和切换自动检查。
2. “设置 → 运行时”可安装、更新、重新检测本地 Transformers 运行组件；自动通道不可用时可选择官方组件文件。模型权重在“本地模型”单独管理。Node/Tectonic 等其他组件仅在需要处理时出现在运行时页。

**可见状态与复核**

| 状态 | 应观察到的结果 | 现有证据入口 |
| --- | --- | --- |
| 应用检查与提示 | 可区分检查中、已是最新版、发现新版本、下载校验中和检查错误；标题栏与软件更新页指向同一版本信息。 | `tests/test_webapp.py::test_app_update_notice_lives_in_titlebar_and_starts_install_from_notice_button`、`::test_settings_sidebar_labels_the_about_page_as_software_update`、`::test_automatic_update_preference_uses_portable_settings_instead_of_loopback_storage`（静态 UI）；`tests/test_app_update.py::test_update_service_reads_verified_windows_release_manifest`（服务）。 |
| 应用安装/回退 | 有校验的当前包缓存时可走 blockmap 差分；校验失败或启动失败时维持可恢复安装。 | `tests/test_app_update.py::test_install_prefers_differential_update_from_cached_current_package`、`::test_update_download_rejects_wrong_size_and_removes_partial_file`、`::test_windows_updater_rolls_back_when_new_process_exits_before_startup_confirmation`（服务/Windows，后者需 Windows 环境）。 |
| 本地运行组件 | 显示缺失、准备进度、需要更新、失败/重试和已就绪；手动选择官方文件可恢复下载失败；组件更新复用已有模型。 | `tests/test_webapp.py::test_local_runtime_install_api_starts_background_job_and_exposes_progress`、`::test_local_runtime_channels_and_manual_install_apis_expose_the_fallback`、`::test_local_runtime_update_is_separate_from_model_downloads`、`::test_managed_runtime_component_apis_keep_node_and_tectonic_independent`（接口/静态 UI）。 |

**人工复核路径：**在已打包 Windows 桌面版分别打开标题栏卡片和软件更新页；用有更新、无更新及通道错误三种清单检查状态。运行时页另用“缺失→安装→就绪”和“旧版→更新”检查独立组件与模型文件复用。Electron 预览版当前会提示暂不支持自动更新；发布验收依 [桌面打包契约](desktop-packaging.zh.md)和 [发布流程](release-workflow.zh.md)进行，静态页面和服务测试不能替代已打包应用的人工验收。

## 维护记录与下一轮缺口

- 首批只登记四个高频功能。科研工作流、Skill、MCP、翻译、PPT、外部模型和扩展市场尚未映射；这些功能没有因此被判定为失败或通过。
- 每次功能改动检查本节所列入口、状态和测试路径。若用户从另一入口能触发同一功能，把该入口补入本图，并注明目前是静态 UI、接口、存储、模拟模型还是真实端到端证据。
- [会话恢复与工具裁决审计](omnigent-session-policy-audit.zh.md)已记录浏览器单次提交、Host 持久化请求去重和内置工具裁决。下一轮优先补真实浏览器内直接对话队列恢复与断线故障注入旅程，以及 PDF 知识库拖放/索引完成旅程。测试范围按风险定向选择；未经用户在当前任务明确允许，不运行全量测试。
