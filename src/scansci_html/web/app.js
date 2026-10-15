const sidebarCollapsedPreference = window.localStorage.getItem("scansci.sidebar.collapsed");
const compactShellDefault = sidebarCollapsedPreference === null;

const state = {
  workspace: null,
  notebook: null,
  settings: null,
  systemOcrStatus: { loading: false, requestedKey: "", provider: "tesseract", checkedAt: "", available: false, message: "尚未检测 OCR" },
  presets: { providers: [], local_models: [] },
  modelHealth: { checked_at: "", providers: {}, models: {}, loading: false },
  localModelMarket: { installed: [], catalog: [], recommendation: null, source: "", query: "", loading: false, loaded: false },
  localModelInstall: { jobs: [], active: null },
  localRuntime: { installed: false, install_available: false, manual_install_available: true, mode: "missing", channels: null },
  runtimeComponents: {
    node: { id: "node", name: "Agent 运行组件", installed: false, install_available: false, mode: "missing" },
    tectonic: { id: "tectonic", name: "LaTeX 排版组件", installed: false, install_available: false, mode: "missing" },
  },
  ollama: { reachable: false, model_ready: false, model_id: "minicpm-v4.6", error: "" },
  localRuntimeManualOpen: false,
  strataPreflight: null,
  strataProbeRunning: false,
  strataProbeError: "",
  strataDraftUrl: "",
  strataCandidateConnection: null,
  downloadStatusError: "",
  onboardingOpen: false,
  onboardingStep: "welcome",
  onboardingMode: "",
  onboardingGuideFromWelcome: false,
  resourceGuideStep: 0,
  onboardingPersisting: false,
  pendingLocalModelResource: "",
  capabilities: null,
  activeView: "home",
  activeMode: "tools",
  activeSettings: "general",
  generalSettingsTab: "appearance",
  settingsReturnView: "home",
  activeExtensions: "skills",
  extensionDetail: null,
  skillInstallReview: null,
  skillInstallBusy: false,
  composerImages: { home: [], chat: [] },
  composerAudio: { home: [], chat: [] },
  composerRecordings: { home: null, chat: null },
  composerTranscribing: { home: false, chat: false },
  composerSources: { home: [], chat: [] },
  composerSkills: { home: [], chat: [] },
  composerImageModel: window.localStorage.getItem("scansci.composer.image-model") || "",
  selectedProviderId: "",
  providerQuery: "",
  modelQuery: "",
  draggedProviderId: "",
  editingModelIndex: -1,
  sourceQuery: "",
  historyQuery: "",
  historyCollapsed: window.localStorage.getItem("scansci.history.collapsed") === "true",
  historyView: window.localStorage.getItem("scansci.history.view") === "archived" ? "archived" : "active",
  historyMenuRunId: "",
  historySearchOpen: false,
  directMessages: [],
  directConversations: [],
  archivedRuns: [],
  archivedConversations: [],
  archiveSettingsQuery: "",
  archiveSettingsLoaded: false,
  archiveSettingsLoading: false,
  archiveSettingsError: "",
  directConversationId: window.localStorage.getItem("scansci.active.direct") || "",
  runs: [],
  activeTaskId: "",
  sessionId: window.localStorage.getItem("scansci.active.session") || null,
  sessionTokens: 0,
  contextUsagePercent: 0,
  sessionStats: null,
  contextStatsOpen: false,
  streaming: false,
  processingTimer: 0,
  conversationAutoFollow: true,
  activeStreamRunId: "",
  toolProgress: null,
  reviewDocument: null,
  // Research documents are prepared alongside the task conversation, but
  // opening a historical task must never hide the request that created it.
  // The reader becomes full-page only after an explicit user action.
  reviewDocumentOpen: false,
  reviewSaveDialog: {
    open: false,
    folderPath: "",
    newFolderName: "",
    browserDirectoryHandle: null,
    browserFolderLabel: "",
    browserFolderMode: "",
    busy: false,
  },
  contextPanel: "sources",
  contextPanelPreset: "research",
  contextPanelCollapsed: window.localStorage.getItem("scansci.context-panel.collapsed") !== "false",
  evidenceReturnPanel: "sources",
  activeEvidence: null,
  evidencePanelExpanded: false,
  contextPanelWidth: Math.max(280, Number(window.localStorage.getItem("scansci.context-panel.width")) || 335),
  expandedEvidencePanelWidth: Math.max(420, Number(window.localStorage.getItem("scansci.evidence-panel.width")) || 560),
  citationPreviewTimer: 0,
  extensions: { skills: null, libraryPath: "", marketplace: [], marketplaceOffline: false, marketplaceLoaded: false },
  extensionUpdates: { checked_at: "", loading: false, skills: [], mcp: [], plugins: [], app: null, error: "" },
  marketplaceQuery: "",
  mcpMarketplace: { items: [], disciplines: [], source: null, synced_at: "", cached_count: 0, updates: [], loaded: false, loading: false },
  mcpMarketplaceQuery: "",
  mcpMarketplaceDiscipline: "all",
  mcpMarketplaceSort: "hot",
  mcpMarketplaceTab: "public",
  mcpManualOpen: false,
  mcpCreateMode: "",
  libraryImportKind: "folder",
  libraryImportGuided: false,
  libraryImportJob: null,
  libraryImportAppliedJobId: "",
  libraryImportNotebookId: "",
  zoteroConnectionIssue: null,
  knowledgeSubscope: null,
  knowledgeScopeIds: [],
  // The picker keeps a local draft while it is open.  Selecting a library
  // must not redraw the whole workspace or change the active chat context.
  knowledgeScopeDraftIds: null,
  knowledgePreviewSourceId: "",
  knowledgeQuery: "",
  knowledgeSearchOpen: false,
  knowledgeVisibleLimit: 200,
  knowledgeIndexStatuses: {},
  localAiStatuses: {},
  localAiStatusPollTimers: {},
  knowledgeScopeRefreshing: false,
  knowledgePreviewCollapsed: window.localStorage.getItem("scansci.knowledge.preview.collapsed") === "true",
  knowledgeSettingsPreview: {
    embedding: "auto",
    reranking: "auto",
    advancedOpen: false,
    chunking: "semantic",
    topK: "8",
  },
  knowledgeTreeExpanded: window.localStorage.getItem("scansci.knowledge.tree.expanded") !== "false",
  downloadStrategy: ["oa_first", "gray_oa", "legal_only"].includes(window.localStorage.getItem("scansci.download.strategy"))
    ? window.localStorage.getItem("scansci.download.strategy")
    : "oa_first",
  downloadStrategyOpen: false,
  pendingBatchIdentifiers: [],
  pendingBatchFilename: "",
  useTor: false,
  torRotateEvery: 3,
  torTransport: "snowflake",
  torTransportOpen: false,
  slideTemplates: [],
  slideTemplatesAvailable: false,
  slideTemplatesPlugin: {},
  selectedSlideTemplateId: window.localStorage.getItem("scansci.slides.template") || "",
  previewSlideTemplateId: "",
  previewSlidePage: "",
  inlineSlidePreviewTemplateId: "",
  inlineSlidePreviewPage: "",
  slideTemplateQuery: "",
  lastRunRenderKey: "",
  runExecutionDetailsOpen: false,
  runExecutionDetailsRunId: "",
  sidebarCollapsed: sidebarCollapsedPreference === "true" || compactShellDefault,
  sidebarWidth: Math.max(260, Math.min(520, Number(window.localStorage.getItem("scansci.sidebar.width")) || 268)),
  thinkingLevel: ["auto", "low", "medium", "high", "xhigh", "max"].includes(window.localStorage.getItem("scansci.thinking.level"))
    ? window.localStorage.getItem("scansci.thinking.level")
    : "auto",
  webSearchMode: ["auto", "on", "off"].includes(window.localStorage.getItem("scansci.web-search.mode"))
    ? window.localStorage.getItem("scansci.web-search.mode")
    : "auto",
  agentMode: window.localStorage.getItem("scansci.agent-mode") === "true",
  researchWorkflow: "",
  // “证据综述” is a delivery choice inside evidence Q&A, not a new
  // restrictive top-level mode. Normal free-form use remains untouched.
  evidenceOutputMode: "answer",
  academicSearchPlanDraft: null,
  profileAvatar: window.localStorage.getItem("scansci.profile.avatar") === "female" ? "female" : "male",
  navigationHistory: [{ view: "home", mode: "tools", settings: "general", task: "" }],
  navigationIndex: 0,
  update: {
    state: "checking",
    available: false,
    current_version: "",
    latest_version: "",
    release_title: "ScanSci",
    release_notes: [],
    message: "正在检查更新",
    checked_at: "",
  },
  updateCardOpen: false,
  updateNative: false,
  autoCheckUpdates: true,
};

const byId = (id) => document.getElementById(id);
const sourceList = byId("sourceList");
let directConversationRenderFrame = 0;
const pendingDirectConversationRenders = new Set();
let navigationRequestGeneration = 0;
// A direct chat run belongs to its conversation, not to the whole window.
// Keeping these jobs separate lets users move to another conversation and
// start work there while the first one continues in the background.
const directChatJobs = new Map();
let activeDirectChatController = null;
let composerSubmissionInFlight = false;
let confirmDialogResolve = null;
let confirmDialogPreviousFocus = null;
let onboardingReturnFocus = null;
let localModelInstallPollTimer = 0;
let localRuntimeInstallPollTimer = 0;
let translationRunId = window.localStorage.getItem("scansci.translation.run-id") || "";
const translationWatchers = new Set();
const runtimeComponentInstallPollTimers = new Map();
let notionWizardResolve = null;
let notionWizardScopeRestore = null;
let directQueueDrag = { conversationId: "", queueId: "", targetId: "", position: "" };
let directQueueEdit = null;
let directMessageEdit = null;

const applicationCopy = Object.freeze({
  "zh-CN": {
    brand: "ScanSci | 搜索科学",
    newResearch: "新建研究",
    knowledgeBase: "知识库",
    extensions: "插件和技能",
    mcpServers: "MCP 服务器",
    history: "历史对话",
    localWorkspace: "本地工作区",
    backToWorkspace: "返回工作区",
    general: "常规",
    defaultCapabilities: "默认能力",
    resources: "本地模型",
    modelServices: "模型服务",
    localModels: "模型市场",
    documentProcessing: "文档处理",
    softwareUpdate: "软件更新",
    settingsLocal: "配置仅保存在此电脑",
    settingsTitle: "设置",
    settingsDescription: "此设备上的工作区与应用设置。",
    localSaved: "本地保存",
    appearanceTitle: "界面与外观",
    appearanceDescription: "语言、明暗与强调色会立即预览，并仅保存在这台电脑上。",
    interfaceLanguage: "界面语言",
    interfaceLanguageHint: "主要导航与设置会立即切换；研究材料与第三方内容保持原文。",
    appearanceTheme: "外观主题",
    appearanceThemeHint: "可跟随系统，也可固定为浅色或深色。",
    accentColor: "强调色",
    accentColorHint: "用于选中状态、进度和主要操作。",
    fontScale: "界面字号",
    fontScaleHint: "调整设置页和工作区的基础文字大小。",
    fontSmall: "小",
    fontMedium: "标准",
    fontLarge: "大",
    fontSmallDetail: "更紧凑",
    fontMediumDetail: "推荐",
    fontLargeDetail: "更易阅读",
    system: "跟随系统",
    light: "浅色",
    dark: "深色",
    systemDetail: "自动",
    lightDetail: "浅色",
    darkDetail: "深色",
    jade: "松绿",
    ocean: "深海",
    plum: "墨紫",
    amber: "琥珀",
    saveAppearance: "保存界面偏好",
    appearanceSaved: "界面偏好已保存",
    currentWorkspace: "当前工作区",
    noWorkspace: "未打开资料库",
    sources: "资料来源",
    citations: "已保存引文",
    layers: "标注图层",
    currentModel: "当前模型",
    modelKeyNote: "模型密钥不会写入工作区文件。",
    runtimeStatus: "运行状态",
    readyTools: "个工具可用。模型与本地模型的配置可分别在对应页面查看。",
    generalTitle: "通用",
    generalDescription: "管理界面偏好、对话输入和目录行为。",
    generalAutoApply: "修改会自动生效",
    generalTabsLabel: "通用设置分区",
    generalTabAppearance: "界面与外观",
    generalTabConversation: "对话与输入",
    generalTabDirectories: "目录与文件",
    conversationDescription: "控制消息阅读、发送方式和完成提醒。",
    sendShortcut: "发送快捷键",
    sendShortcutHint: "选择用 Enter 还是 Shift+Enter 发送消息；另一个键用于换行。",
    sendEnter: "Enter 发送 · Shift+Enter 换行",
    sendShiftEnter: "Shift+Enter 发送 · Enter 换行",
    completionNotifications: "回复完成通知",
    completionNotificationsHint: "AI 助手回复完成时，显示系统通知。",
    agentCompletionNotifications: "主代理完成通知",
    agentCompletionNotificationsHint: "主对话或分支对话完成时，显示系统通知。",
    subagentCompletionNotifications: "子代理完成通知",
    subagentCompletionNotificationsHint: "子代理会话完成时，显示系统通知。",
    directoriesTitle: "目录与文件",
    directoriesDescription: "选择工作区、对话文件和知识库向量索引的默认保存位置；留空则继续使用应用默认目录。",
    defaultWorkspace: "默认工作目录",
    defaultWorkspaceHint: "新建资料库和研究项目时优先使用此目录。",
    conversationWorkspace: "对话工作目录",
    conversationWorkspaceHint: "未绑定资料库的对话文件保存在此目录。",
    chooseDirectory: "选择目录",
    resetDefault: "恢复默认",
    defaultWorkspacePlaceholder: "使用应用默认工作区",
    conversationWorkspacePlaceholder: "使用应用默认对话目录",
    modelCacheDirectory: "模型缓存目录",
    modelCacheDirectoryHint: "Hugging Face 模型文件保存在此目录，可避免占用 C 盘空间。",
    localRuntimeDirectory: "本地运行组件目录",
    localRuntimeDirectoryHint: "Transformers 运行组件与版本放在此目录，新版本会复用已下载内容。",
    vectorIndexDirectory: "向量索引目录",
    vectorIndexDirectoryHint: "知识库的向量索引和检索缓存保存在此目录；更换后会迁移现有索引。",
    storageDirectoryPlaceholder: "使用应用默认位置",
    storageDirectoryRestartHint: "改变后将按新目录下载；向量索引会先校验再迁移，已启动的运行不会被中断。",
  },
  en: {
    brand: "ScanSci | Research Science",
    newResearch: "New research",
    knowledgeBase: "Knowledge",
    extensions: "Plugins & skills",
    mcpServers: "MCP servers",
    history: "History",
    localWorkspace: "Local workspace",
    backToWorkspace: "Back to workspace",
    general: "General",
    defaultCapabilities: "Default capabilities",
    resources: "Resources",
    modelServices: "Model services",
    localModels: "Model market",
    documentProcessing: "Documents",
    softwareUpdate: "Software update",
    settingsLocal: "Preferences stay on this computer",
    settingsTitle: "Settings",
    settingsDescription: "Workspace and application preferences for this device.",
    localSaved: "Saved locally",
    appearanceTitle: "Language & appearance",
    appearanceDescription: "Preview language, light and dark modes, and accent colour instantly. They stay on this computer.",
    interfaceLanguage: "Interface language",
    interfaceLanguageHint: "Primary navigation and settings switch immediately; research material and third-party content stay in their original language.",
    appearanceTheme: "Appearance",
    appearanceThemeHint: "Follow your system or keep ScanSci light or dark.",
    accentColor: "Accent colour",
    accentColorHint: "Used for selected states, progress, and primary actions.",
    fontScale: "Interface size",
    fontScaleHint: "Adjust the base text size across settings and the workspace.",
    fontSmall: "Small",
    fontMedium: "Standard",
    fontLarge: "Large",
    fontSmallDetail: "More compact",
    fontMediumDetail: "Recommended",
    fontLargeDetail: "Easier to read",
    system: "System",
    light: "Light",
    dark: "Dark",
    systemDetail: "Auto",
    lightDetail: "Light",
    darkDetail: "Dark",
    jade: "Jade",
    ocean: "Ocean",
    plum: "Plum",
    amber: "Amber",
    saveAppearance: "Save preferences",
    appearanceSaved: "Appearance preferences saved",
    currentWorkspace: "Current workspace",
    noWorkspace: "No knowledge library open",
    sources: "Sources",
    citations: "Saved citations",
    layers: "Annotation layers",
    currentModel: "Current model",
    modelKeyNote: "Model credentials are never written to workspace files.",
    runtimeStatus: "Runtime status",
    readyTools: "tools available. Configure cloud and local models on their respective pages.",
    generalTitle: "General",
    generalDescription: "Manage interface preferences, conversation input, and directory behavior.",
    generalAutoApply: "Changes apply automatically",
    generalTabsLabel: "General settings sections",
    generalTabAppearance: "Language & appearance",
    generalTabConversation: "Conversation & input",
    generalTabDirectories: "Directories & files",
    conversationDescription: "Control message reading, sending, and completion notifications.",
    sendShortcut: "Send shortcut",
    sendShortcutHint: "Choose whether Enter or Shift+Enter sends a message; the other key inserts a new line.",
    sendEnter: "Enter sends · Shift+Enter new line",
    sendShiftEnter: "Shift+Enter sends · Enter new line",
    completionNotifications: "Reply completion notifications",
    completionNotificationsHint: "Show a system notification when the AI assistant finishes replying.",
    agentCompletionNotifications: "Agent completion notifications",
    agentCompletionNotificationsHint: "Show a system notification when the main or branch conversation finishes.",
    subagentCompletionNotifications: "Subagent completion notifications",
    subagentCompletionNotificationsHint: "Show a system notification when a subagent session finishes.",
    directoriesTitle: "Directories & files",
    directoriesDescription: "Choose where workspaces, conversation files, and knowledge-base vector indexes are saved; leave blank to use application defaults.",
    defaultWorkspace: "Default workspace directory",
    defaultWorkspaceHint: "Prefer this directory when creating libraries and research projects.",
    conversationWorkspace: "Conversation workspace",
    conversationWorkspaceHint: "Conversation files without a linked library are saved here.",
    chooseDirectory: "Choose directory",
    resetDefault: "Restore default",
    defaultWorkspacePlaceholder: "Use application default workspace",
    conversationWorkspacePlaceholder: "Use application default conversation folder",
    modelCacheDirectory: "Model cache directory",
    modelCacheDirectoryHint: "Hugging Face model files are stored here so they do not fill the system drive.",
    localRuntimeDirectory: "Local runtime directory",
    localRuntimeDirectoryHint: "Versioned Transformers runtime components are kept here and reused across updates.",
    vectorIndexDirectory: "Vector index directory",
    vectorIndexDirectoryHint: "Knowledge-base vector indexes and retrieval caches are stored here; existing indexes are migrated when changed.",
    storageDirectoryPlaceholder: "Use application default location",
    storageDirectoryRestartHint: "New downloads use this location; vector indexes are validated before migration, and an already running runtime is not interrupted.",
  },
});

function appearancePreferences() {
  const source = state.settings?.appearance || {};
  return {
    locale: source.locale === "en" ? "en" : "zh-CN",
    theme: ["system", "light", "dark"].includes(source.theme) ? source.theme : "system",
    accent: ["jade", "ocean", "plum", "amber"].includes(source.accent) ? source.accent : "jade",
    font_scale: ["small", "medium", "large"].includes(source.font_scale) ? source.font_scale : "medium",
  };
}

function copy(key) {
  const locale = appearancePreferences().locale;
  return applicationCopy[locale]?.[key] || applicationCopy["zh-CN"][key] || key;
}

function resolvedTheme(preference = appearancePreferences().theme) {
  if (preference !== "system") return preference;
  return window.matchMedia?.("(prefers-color-scheme: dark)")?.matches ? "dark" : "light";
}

function applyAppearancePreferences() {
  const preferences = appearancePreferences();
  const root = document.documentElement;
  const theme = resolvedTheme(preferences.theme);
  root.lang = preferences.locale;
  root.dataset.theme = theme;
  root.dataset.themePreference = preferences.theme;
  root.dataset.accent = preferences.accent;
  root.dataset.fontScale = preferences.font_scale;
  root.style.colorScheme = theme;
  document.title = copy("brand");
  document.querySelector('meta[name="application-name"]')?.setAttribute("content", copy("brand"));
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    const value = copy(element.dataset.i18n || "");
    if (value) element.textContent = value;
  });
  refreshEvidenceReaderTheme();
  syncEmbeddedBrowserTheme();
}

function syncEmbeddedBrowserTheme() {
  const api = window.pywebview?.api;
  if (typeof api?.set_browser_theme !== "function") return;
  const style = window.getComputedStyle(document.documentElement);
  const token = (name) => style.getPropertyValue(name).trim();
  api.set_browser_theme({
    canvas: token("--canvas"),
    surface: token("--surface"),
    ink: token("--ink"),
    muted: token("--muted"),
    rule: token("--rule"),
    control: token("--control-background"),
    accent: token("--accent"),
  }).catch(() => {});
}

function updateEmbeddedBrowserButton(open) {
  document.documentElement.classList.toggle("embedded-browser-open", Boolean(open));
  const button = document.querySelector('[data-action="toggle-embedded-browser"]');
  if (!button) return;
  button.setAttribute("aria-pressed", String(Boolean(open)));
  button.setAttribute("aria-label", open ? "收起内置浏览器" : "打开内置浏览器");
  button.title = open ? "收起内置浏览器" : "打开内置浏览器";
}

async function openEmbeddedBrowser(address = "") {
  const api = window.pywebview?.api;
  if (typeof api?.open_browser !== "function") {
    if (address) window.open(address, "_blank", "noopener");
    return;
  }
  const result = await api.open_browser(address);
  if (!result?.ok) throw new Error(result?.message || "内置浏览器暂时不可用");
  updateEmbeddedBrowserButton(result.open);
}

function collectAppearanceForm() {
  const form = byId("generalPreferencesForm");
  if (!form || !state.settings) return appearancePreferences();
  state.settings.appearance = {
    locale: form.elements["appearance-locale"]?.value === "en" ? "en" : "zh-CN",
    theme: ["system", "light", "dark"].includes(form.elements["appearance-theme"]?.value) ? form.elements["appearance-theme"].value : "system",
    accent: ["jade", "ocean", "plum", "amber"].includes(form.elements["appearance-accent"]?.value) ? form.elements["appearance-accent"].value : "jade",
    font_scale: ["small", "medium", "large"].includes(form.elements["appearance-font-scale"]?.value) ? form.elements["appearance-font-scale"].value : "medium",
  };
  applyAppearancePreferences();
  return state.settings.appearance;
}

function collectGeneralSettingsForm() {
  if (!state.settings) return generalPreferences();
  const current = generalPreferences();
  const conversationForm = byId("generalConversationForm");
  const directoriesForm = byId("generalDirectoriesForm");
  const readDirectory = (name, fallback) => {
    const value = String(directoriesForm?.elements[name]?.value ?? fallback).trim();
    const applicationDirectory = String(state.workspace?.workspace_directory || "").trim()
      || workspaceDirectoryFromFilePath(state.workspace?.workspace_path);
    if (!value || value.startsWith("使用应用默认") || value.startsWith("Use application default") || value === applicationDirectory) return "";
    return value;
  };
  state.settings.general = {
    conversation: {
      send_shortcut: conversationForm?.elements["conversation-send-shortcut"]?.value === "shift-enter" ? "shift-enter" : current.conversation.send_shortcut,
      completion_notifications: Boolean(conversationForm?.querySelector('[data-general-toggle="completion_notifications"]')?.checked ?? current.conversation.completion_notifications),
      agent_completion_notifications: Boolean(conversationForm?.querySelector('[data-general-toggle="agent_completion_notifications"]')?.checked ?? current.conversation.agent_completion_notifications),
      subagent_completion_notifications: Boolean(conversationForm?.querySelector('[data-general-toggle="subagent_completion_notifications"]')?.checked ?? current.conversation.subagent_completion_notifications),
    },
    directories: {
      default_workspace: readDirectory("directory-default-workspace", current.directories.default_workspace),
      conversation_workspace: readDirectory("directory-conversation-workspace", current.directories.conversation_workspace),
      model_cache: readDirectory("directory-model-cache", current.directories.model_cache),
      local_runtime: readDirectory("directory-local-runtime", current.directories.local_runtime),
      vector_index: readDirectory("directory-vector-index", current.directories.vector_index),
    },
    updates: {
      auto_check: current.updates.auto_check,
    },
  };
  return state.settings.general;
}

function composerUsesShiftEnterToSend() {
  return generalPreferences().conversation.send_shortcut === "shift-enter";
}

window.matchMedia?.("(prefers-color-scheme: dark)")?.addEventListener?.("change", () => {
  if (appearancePreferences().theme === "system") applyAppearancePreferences();
});

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const failure = new Error(payload?.error?.message || `Request failed (${response.status})`);
    failure.payload = payload;
    throw failure;
  }
  return payload;
}

async function streamChat(payload, onEvent, { signal } = {}) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const failure = new Error(error?.error?.message || `Request failed (${response.status})`);
    failure.code = error?.error?.code || "chat_failed";
    failure.failure = error?.error?.failure || error?.failure || null;
    throw failure;
  }
  if (!response.body) throw new Error("This browser does not support streaming responses.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const dispatch = (frame) => {
    let eventType = "message";
    const dataLines = [];
    frame.split(/\r?\n/).forEach((line) => {
      if (line.startsWith("event:")) eventType = line.slice(6).trim() || "message";
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    });
    if (!dataLines.length) return;
    const event = JSON.parse(dataLines.join("\n"));
    if (eventType === "error" || eventType === "RUN_ERROR") {
      const failure = new Error(event.message || "The streaming response could not be completed.");
      failure.code = event.code || "chat_failed";
      failure.failure = event.failure || null;
      failure.userImages = Array.isArray(event.user_images) ? event.user_images : [];
      throw failure;
    }
    onEvent(eventType, event);
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let match = buffer.match(/\r?\n\r?\n/);
      while (match && match.index !== undefined) {
        const frame = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        dispatch(frame);
        match = buffer.match(/\r?\n\r?\n/);
      }
      if (done) break;
    }
    if (buffer.trim()) dispatch(buffer);
  } finally {
    reader.releaseLock();
  }
}

function streamChatWithRecovery(payload, onEvent, { signal } = {}) {
  // A transport failure cannot prove that the server has not executed an effect.
  return streamChat(payload, onEvent, { signal });
}

function scheduleDirectConversationRender(conversationId = state.directConversationId, { forceFollow = false } = {}) {
  const id = String(conversationId || "");
  if (id) pendingDirectConversationRenders.add(id);
  if (directConversationRenderFrame) return;
  directConversationRenderFrame = window.requestAnimationFrame(() => {
    directConversationRenderFrame = 0;
    const activeId = String(state.directConversationId || "");
    if (!id || pendingDirectConversationRenders.has(activeId)) {
      const job = directChatJobs.get(activeId);
      if (job) state.directMessages = job.messages;
      if (!state.activeTaskId && state.activeView === "conversation") renderDirectConversation({ forceFollow });
    }
    pendingDirectConversationRenders.clear();
    renderDirectLiveControls();
    renderTasks();
  });
}

const conversationFollowThreshold = 72;

function conversationScrollSnapshot() {
  const answerArea = byId("answerArea");
  if (!answerArea) return { top: 0, shouldFollow: true };
  const distanceFromBottom = answerArea.scrollHeight - answerArea.scrollTop - answerArea.clientHeight;
  const shouldFollow = distanceFromBottom < 72;
  const followsThreshold = state.conversationAutoFollow && distanceFromBottom < conversationFollowThreshold;
  return {
    top: answerArea.scrollTop,
    shouldFollow: shouldFollow && followsThreshold,
  };
}

function updateConversationScrollAffordance() {
  const answerArea = byId("answerArea");
  const button = byId("conversationJumpLatest");
  if (!answerArea || !button) return;
  const distanceFromBottom = Math.max(0, answerArea.scrollHeight - answerArea.scrollTop - answerArea.clientHeight);
  button.hidden = state.activeView !== "conversation"
    || state.conversationAutoFollow
    || distanceFromBottom < conversationFollowThreshold;
}

function restoreConversationScroll(snapshot, { forceFollow = false } = {}) {
  const answerArea = byId("answerArea");
  if (!answerArea) return;
  if (forceFollow || snapshot?.shouldFollow) {
    answerArea.scrollTop = answerArea.scrollHeight;
    state.conversationAutoFollow = true;
  } else {
    const maximum = Math.max(0, answerArea.scrollHeight - answerArea.clientHeight);
    answerArea.scrollTop = Math.min(Math.max(0, Number(snapshot?.top || 0)), maximum);
  }
  updateConversationScrollAffordance();
}

function followLatestConversationMessage({ smooth = false } = {}) {
  const answerArea = byId("answerArea");
  if (!answerArea) return;
  state.conversationAutoFollow = true;
  answerArea.scrollTo({ top: answerArea.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  updateConversationScrollAffordance();
}

const conversationTurnNavState = { entries: [], signature: "", hovered: -1, renderFrame: 0, activeFrame: 0, waveFrame: 0, waveY: 0 };

function conversationTurnPreviewText(value, limit = 220) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function collectConversationTurns() {
  const turns = [];
  byId("answerArea")?.querySelectorAll(".conversation-message").forEach((message) => {
    if (message.classList.contains("is-user")) {
      const bubble = message.querySelector(".user-turn-bubble");
      const question = bubble?.querySelector("p")?.textContent || bubble?.querySelector("textarea")?.value || bubble?.textContent;
      turns.push({ element: message, question: conversationTurnPreviewText(question, 220), answer: "" });
    } else if (message.classList.contains("is-assistant") && turns.length) {
      const turn = turns[turns.length - 1];
      const answer = message.querySelector(".answer-sentence, .run-result")?.textContent
        || message.querySelector(".message-body")?.textContent;
      if (answer && !turn.answer) turn.answer = conversationTurnPreviewText(answer, 280);
    }
  });
  if (turns.length && !turns[0].answer) {
    turns[0].answer = conversationTurnPreviewText(byId("answerArea")?.querySelector(".run-result")?.textContent, 280);
  }
  return turns;
}

function scheduleConversationTurnNav() {
  if (conversationTurnNavState.renderFrame) return;
  conversationTurnNavState.renderFrame = window.requestAnimationFrame(() => {
    conversationTurnNavState.renderFrame = 0;
    renderConversationTurnNav();
  });
}

function renderConversationTurnNav() {
  const nav = byId("conversationTurnNav");
  const list = byId("conversationTurnList");
  const area = byId("answerArea");
  if (!nav || !list || !area) return;
  const entries = collectConversationTurns();
  conversationTurnNavState.entries = entries;
  if (state.activeView !== "conversation" || entries.length < 2 || area.clientWidth < 900 || area.clientHeight < 200 || area.scrollHeight <= area.clientHeight + 10) {
    nav.hidden = true;
    conversationTurnNavState.signature = "";
    hideConversationTurnPreview();
    return;
  }
  const signature = `${state.activeTaskId || state.directConversationId}|${entries.map((entry) => entry.question).join("\u001f")}`;
  if (signature !== conversationTurnNavState.signature) {
    conversationTurnNavState.signature = signature;
    list.innerHTML = entries.map((entry, index) => {
      return `<button type="button" class="conversation-turn-marker" data-action="jump-conversation-turn" data-turn-index="${index}" aria-label="跳转到第 ${index + 1} 轮：${escapeHtml(conversationTurnPreviewText(entry.question, 52) || "附件提问")}"><span class="conversation-turn-stroke" aria-hidden="true"></span></button>`;
    }).join("");
    hideConversationTurnPreview();
  }
  const mainRect = nav.parentElement.getBoundingClientRect();
  const areaRect = area.getBoundingClientRect();
  nav.style.top = `${areaRect.top - mainRect.top + 8}px`;
  nav.style.height = `${Math.max(0, area.clientHeight - 16)}px`;
  nav.hidden = false;
  if (conversationTurnNavState.hovered >= 0) showConversationTurnPreview(conversationTurnNavState.hovered);
  updateConversationTurnNavActive();
}

function updateConversationTurnNavActive() {
  const nav = byId("conversationTurnNav");
  const area = byId("answerArea");
  if (!nav || nav.hidden || !area) return;
  if (conversationTurnNavState.entries.some((entry) => !entry.element.isConnected)) {
    scheduleConversationTurnNav();
    return;
  }
  const boundary = area.getBoundingClientRect().top + area.clientHeight * 0.42;
  let active = 0;
  conversationTurnNavState.entries.forEach((entry, index) => {
    if (entry.element.getBoundingClientRect().top <= boundary) active = index;
  });
  nav.querySelectorAll(".conversation-turn-marker").forEach((button, index) => {
    button.classList.toggle("is-current", index === active);
    if (index === active) button.setAttribute("aria-current", "location");
    else button.removeAttribute("aria-current");
  });
  const list = byId("conversationTurnList");
  if (list) {
    const rowTop = active * 20;
    if (rowTop < list.scrollTop + 10) list.scrollTop = Math.max(0, rowTop - 20);
    else if (rowTop + 20 > list.scrollTop + list.clientHeight - 10) list.scrollTop = rowTop + 30 - list.clientHeight;
  }
}

function scheduleConversationTurnNavActive() {
  if (conversationTurnNavState.activeFrame) return;
  conversationTurnNavState.activeFrame = window.requestAnimationFrame(() => {
    conversationTurnNavState.activeFrame = 0;
    updateConversationTurnNavActive();
  });
}

function showConversationTurnPreview(index) {
  const entry = conversationTurnNavState.entries[index];
  const nav = byId("conversationTurnNav");
  const button = byId("conversationTurnList")?.querySelector(`[data-turn-index="${index}"]`);
  const preview = byId("conversationTurnPreview");
  if (!entry || !nav || nav.hidden || !button || !preview) return;
  byId("conversationTurnList")?.querySelector('[aria-describedby="conversationTurnPreview"]')?.removeAttribute("aria-describedby");
  conversationTurnNavState.hovered = index;
  byId("conversationTurnPreviewQuestion").textContent = entry.question || "附件提问";
  byId("conversationTurnPreviewAnswer").textContent = entry.answer || "正在等待回答…";
  preview.hidden = false;
  const desired = button.getBoundingClientRect().top - nav.getBoundingClientRect().top + 10 - preview.offsetHeight / 2;
  preview.style.top = `${Math.max(4, Math.min(desired, nav.clientHeight - preview.offsetHeight - 4))}px`;
  button.setAttribute("aria-describedby", "conversationTurnPreview");
}

function hideConversationTurnPreview() {
  conversationTurnNavState.hovered = -1;
  if (conversationTurnNavState.waveFrame) window.cancelAnimationFrame(conversationTurnNavState.waveFrame);
  conversationTurnNavState.waveFrame = 0;
  const preview = byId("conversationTurnPreview");
  if (preview) preview.hidden = true;
  byId("conversationTurnNav")?.classList.remove("is-hovering");
  byId("conversationTurnList")?.querySelectorAll(".conversation-turn-marker").forEach((button) => {
    button.style.removeProperty("--wave-width");
    button.removeAttribute("aria-describedby");
  });
}

function jumpConversationTurn(index) {
  const area = byId("answerArea");
  const target = conversationTurnNavState.entries[Number(index)]?.element;
  if (!area || !target?.isConnected) return;
  state.conversationAutoFollow = false;
  hideConversationTurnPreview();
  const top = target.getBoundingClientRect().top - area.getBoundingClientRect().top + area.scrollTop - 18;
  area.scrollTo({ top: Math.max(0, top), behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? "auto" : "smooth" });
  updateConversationScrollAffordance();
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
}

const localResourceKinds = {
  pdf: new Set(["pdf"]),
  spreadsheet: new Set(["xls", "xlsx", "xlsm", "xlsb", "csv", "tsv", "ods"]),
  document: new Set(["doc", "docx", "odt", "rtf"]),
  presentation: new Set(["ppt", "pptx", "odp", "key"]),
  image: new Set(["png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "tif", "tiff", "heic"]),
  markdown: new Set(["md", "markdown"]),
  text: new Set(["txt", "log"]),
  archive: new Set(["zip", "7z", "rar", "tar", "gz", "bz2", "xz"]),
  audio: new Set(["mp3", "wav", "m4a", "flac", "aac", "ogg"]),
  video: new Set(["mp4", "mov", "mkv", "avi", "webm", "wmv"]),
  data: new Set(["json", "jsonl", "xml", "yaml", "yml", "parquet", "feather", "sqlite", "sqlite3", "db"]),
  code: new Set(["py", "r", "rmd", "ipynb", "js", "mjs", "ts", "tsx", "jsx", "html", "css", "sql", "sh", "ps1"]),
};

function localResourceExtension(path = "") {
  const leaf = localPathLeaf(String(path).replace(/[\\/]+$/, ""));
  const match = leaf.match(/\.([^.\\/]+)$/);
  return match ? match[1].toLowerCase() : "";
}

function localResourceKind(path = "", { folder = false } = {}) {
  const value = String(path || "").trim();
  const extension = localResourceExtension(value);
  if (folder || /[\\/]$/.test(value)) return "folder";
  if (!extension) return "file";
  return Object.entries(localResourceKinds).find(([, extensions]) => extensions.has(extension))?.[0] || "file";
}

function localResourceIcon(kind = "file") {
  const icons = {
    folder: "folder-open",
    pdf: "file-text",
    spreadsheet: "file-spreadsheet",
    document: "file-text",
    presentation: "presentation",
    image: "image",
    markdown: "file-text",
    text: "file-text",
    archive: "archive",
    audio: "file-audio",
    video: "file-video",
    data: "database",
    code: "file-code",
    file: "file",
  };
  return icons[kind] || "file";
}

function fileTypeIconMarkup(path) {
  const kind = localResourceKind(path);
  return `<span class="local-artifact-icon is-${kind}" aria-hidden="true">${uiIcon(localResourceIcon(kind))}</span>`;
}

const siteUrlTrailingPunctuation = /[),.;:!?，。！？；：、》）】』」]+$/u;

function normalizeExternalUrl(value = "") {
  const candidate = String(value || "").trim().replace(siteUrlTrailingPunctuation, "");
  if (!candidate) return "";
  try {
    const parsed = new URL(candidate);
    if (!/^https?:$/.test(parsed.protocol)) return "";
    return parsed.href;
  } catch (_error) {
    return "";
  }
}

function siteLinkMarkup(rawUrl, label = rawUrl) {
  const url = normalizeExternalUrl(rawUrl);
  if (!url) return escapeHtml(label);
  const iconUrl = `/api/site-icon?url=${encodeURIComponent(url)}`;
  return `<a class="site-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" data-site-link="true" data-site-url="${escapeHtml(url)}" title="${escapeHtml(url)}"><span class="site-link-icon" aria-hidden="true"><img data-site-icon="true" src="${escapeHtml(iconUrl)}" alt="" loading="lazy" decoding="async"><span class="site-link-fallback">${uiIcon("globe")}</span></span><span class="site-link-label">${escapeHtml(label)}</span></a>`;
}

function linkifyExternalUrls(source, rememberHtml) {
  return String(source).replace(/(^|[^A-Za-z0-9_])((?:https?:\/\/)[^\s<>"'`]+)/gu, (_match, prefix, rawUrl) => {
    const cleanUrl = rawUrl.replace(siteUrlTrailingPunctuation, "");
    if (!normalizeExternalUrl(cleanUrl)) return `${prefix}${rawUrl}`;
    const trailing = rawUrl.slice(cleanUrl.length);
    return `${prefix}${rememberHtml(siteLinkMarkup(cleanUrl, cleanUrl))}${trailing}`;
  });
}

function renderAssistantInline(value = "") {
  const localResources = [];
  const inlineHtml = [];
  const rememberLocalResource = (path, label = "") => {
    const cleanPath = String(path || "").trim().replace(/^[<"'“‘]+|[>"'”’]+$/g, "");
    if (!/^(?:[a-zA-Z]:[\\/]|\\\\)/.test(cleanPath)) return path;
    const token = `SCANSCI_LOCAL_RESOURCE_${localResources.length}_TOKEN`;
    localResources.push({ token, path: cleanPath, label: String(label || "").trim() });
    return token;
  };
  const rememberHtml = (html) => {
    const token = `SCANSCI_INLINE_HTML_${inlineHtml.length}_TOKEN`;
    inlineHtml.push({ token, html });
    return token;
  };
  let source = String(value);
  source = source.replace(/\[([^\]]+)\]\(((?:[a-zA-Z]:[\\/]|\\\\)[^)]+)\)/g, (_match, label, path) => rememberLocalResource(path, label));
  source = source.replace(/`((?:[a-zA-Z]:[\\/]|\\\\)[^`\r\n]+)`/g, (_match, path) => rememberLocalResource(path));
  source = source.replace(/(["“‘])((?:[a-zA-Z]:[\\/]|\\\\)[^"”’\r\n]+)(["”’])/g, (_match, _open, path) => rememberLocalResource(path));
  source = source.replace(/(^|[^a-zA-Z0-9])((?:[a-zA-Z]:[\\/]|\\\\)[^\s<>"'`，。；、！？）》】}]+)/g, (_match, prefix, path) => {
    const cleanPath = path.replace(/[),.;:]+$/g, "");
    return `${prefix}${rememberLocalResource(cleanPath)}${path.slice(cleanPath.length)}`;
  });
  source = source.replace(/`([^`\r\n]+)`/g, (_match, code) => rememberHtml(`<code>${escapeHtml(code)}</code>`));
  source = source.replace(/\[([^\]\r\n]+)\]\(((?:https?:\/\/)[^\s)]+)\)/g, (_match, label, url) => {
    const normalized = normalizeExternalUrl(url);
    return normalized ? rememberHtml(siteLinkMarkup(normalized, label)) : _match;
  });
  source = linkifyExternalUrls(source, rememberHtml);
  let markup = escapeHtml(source)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  inlineHtml.forEach(({ token, html }) => {
    markup = markup.replace(token, html);
  });
  localResources.forEach(({ token, path, label }) => {
    const folder = localResourceKind(path) === "folder";
    markup = markup.replace(token, localFileLinkMarkup(path, label || localPathLeaf(path), { folder, inline: true }));
  });
  return markup;
}

function renderAssistantContent(value = "") {
  const lines = String(value).replace(/\r\n/g, "\n").split("\n");
  const output = [];
  let listType = "";
  let paragraph = [];
  let quote = [];
  let code = null;
  const flushParagraph = () => {
    if (!paragraph.length) return;
    output.push(`<p>${renderAssistantInline(paragraph.join("\n")).replace(/\n/g, "<br>")}</p>`);
    paragraph = [];
  };
  const flushQuote = () => {
    if (!quote.length) return;
    output.push(`<blockquote>${renderAssistantContent(quote.join("\n"))}</blockquote>`);
    quote = [];
  };
  const closeList = () => {
    if (!listType) return;
    output.push(`</${listType}>`);
    listType = "";
  };
  const isTableRow = (line) => /^\s*\|?.+\|.+\|?\s*$/.test(line);
  const isTableDivider = (line) => /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
  const splitTableRow = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  const renderTable = (rows) => {
    if (rows.length < 2 || !isTableDivider(rows[1])) return false;
    const header = splitTableRow(rows[0]);
    const divider = splitTableRow(rows[1]);
    if (!header.length || divider.length !== header.length) return false;
    const body = rows.slice(2).map(splitTableRow).filter((row) => row.length === header.length);
    const cells = (row, tag) => row.map((cell) => `<${tag}>${renderAssistantInline(cell)}</${tag}>`).join("");
    output.push(`<div class="assistant-table-wrap"><table class="assistant-table"><thead><tr>${cells(header, "th")}</tr></thead><tbody>${body.map((row) => `<tr>${cells(row, "td")}</tr>`).join("")}</tbody></table></div>`);
    return true;
  };
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (code) {
      if (line.trim().startsWith("```")) {
        output.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
        code = null;
      } else code.push(line);
      continue;
    }
    if (/^\s*```/.test(line)) {
      flushParagraph(); flushQuote(); closeList(); code = [];
      continue;
    }
    if (isTableRow(line) && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
      flushParagraph(); flushQuote(); closeList();
      const rows = [line];
      index += 1;
      rows.push(lines[index]);
      while (index + 1 < lines.length && isTableRow(lines[index + 1]) && !isTableDivider(lines[index + 1])) {
        index += 1;
        rows.push(lines[index]);
      }
      if (!renderTable(rows)) paragraph.push(...rows);
      continue;
    }
    const quoteLine = line.match(/^\s*>\s?(.*)$/);
    if (quoteLine) {
      flushParagraph(); closeList(); quote.push(quoteLine[1]);
      continue;
    }
    if (quote.length) flushQuote();
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (/^\s*(?:---+|\*\s*\*\s*\*|___+)\s*$/.test(line)) {
      flushParagraph(); closeList(); output.push("<hr>");
    } else if (heading) {
      flushParagraph(); closeList();
      const level = Math.min(4, heading[1].length + 1);
      output.push(`<h${level}>${renderAssistantInline(heading[2])}</h${level}>`);
    } else if (unordered || ordered) {
      flushParagraph();
      const nextType = unordered ? "ul" : "ol";
      if (listType !== nextType) { closeList(); listType = nextType; output.push(`<${listType}>`); }
      output.push(`<li>${renderAssistantInline((unordered || ordered)[1])}</li>`);
    } else if (!line.trim()) {
      flushParagraph(); closeList();
    } else {
      closeList(); paragraph.push(line);
    }
  }
  flushParagraph(); flushQuote(); closeList();
  if (code) output.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  return output.join("");
}

function extractSkillMentions(text = "") {
  return [...new Set([...String(text).matchAll(/(?:^|\s)\$([a-zA-Z0-9._-]+)/g)].map((match) => match[1].toLowerCase()))];
}

function enabledSkillCatalog() {
  return (state.extensions.skills || []).filter((item) => item.available !== false && item.enabled !== false && !item.uninstalled);
}

function skillRecord(skillId) {
  const normalized = String(skillId || "").trim().toLowerCase();
  return enabledSkillCatalog().find((item) => String(item.id || "").toLowerCase() === normalized) || null;
}

function localSkillHref(path = "") {
  const normalized = String(path || "").trim().replace(/\\/g, "/");
  if (!normalized) return "";
  const encoded = normalized.split("/").map((part, index) => {
    if (index === 0 && /^[a-z]:$/i.test(part)) return part;
    return encodeURIComponent(part);
  }).join("/");
  return /^[a-z]:\//i.test(normalized) ? `file:///${encoded}` : `file://${encoded.startsWith("/") ? "" : "/"}${encoded}`;
}

function skillTokenMarkup(skill, { key = "", removable = false } = {}) {
  if (!skill?.id) return "";
  const id = String(skill.id);
  const name = String(skill.name || id);
  const path = String(skill.skill_file || "").trim();
  const label = path
    ? `<a class="composer-skill-token-link" href="${escapeHtml(localSkillHref(path))}" data-action="open-local-path" data-local-path="${escapeHtml(path)}" data-skill-id="${escapeHtml(id)}" aria-label="${escapeHtml(name)}，打开本地 SKILL.md" title="${escapeHtml(path)}">${escapeHtml(name)}</a>`
    : `<span class="composer-skill-token-link" data-skill-id="${escapeHtml(id)}">${escapeHtml(name)}</span>`;
  const remove = removable
    ? `<button type="button" class="composer-skill-token-remove" data-action="remove-composer-skill" data-composer-key="${escapeHtml(key)}" data-skill-id="${escapeHtml(id)}" aria-label="移除 Skill ${escapeHtml(name)}" title="移除">×</button>`
    : "";
  return `<span class="composer-skill-token" data-skill-token="${escapeHtml(id)}">${label}${remove}</span>`;
}

function composerSkillRecords(key) {
  return Array.isArray(state.composerSkills[key]) ? state.composerSkills[key] : [];
}

function composerSkillIds(key, text = "") {
  return [...new Set([
    ...composerSkillRecords(key).map((item) => String(item.id || "").toLowerCase()),
    ...extractSkillMentions(text),
  ].filter(Boolean))].slice(0, 4);
}

function skillRecordsForIds(ids = []) {
  return [...new Set(ids.map((item) => String(typeof item === "string" ? item : item?.id || "").toLowerCase()).filter(Boolean))]
    .map((id) => skillRecord(id) || { id, name: id, skill_file: "" });
}

function messageSkillTokensMarkup(skills = []) {
  const records = Array.isArray(skills) ? skills.map((item) => typeof item === "string" ? (skillRecord(item) || { id: item, name: item }) : item) : [];
  if (!records.length) return "";
  return `<div class="message-skill-tokens" aria-label="本轮使用的 Skill">${records.map((item) => skillTokenMarkup(item)).join("")}</div>`;
}

function renderComposerSkills(key) {
  const target = byId(`${key}SkillTokens`);
  if (!target) return;
  const skills = composerSkillRecords(key);
  target.hidden = !skills.length;
  target.innerHTML = skills.map((item) => skillTokenMarkup(item, { key, removable: true })).join("");
  target.closest("form")?.classList.toggle("has-skill-token", Boolean(skills.length));
}

function addComposerSkill(key, skillId) {
  const item = skillRecord(skillId);
  if (!item) return false;
  const existing = composerSkillRecords(key);
  if (existing.some((skill) => String(skill.id).toLowerCase() === String(item.id).toLowerCase())) return true;
  if (existing.length >= 4) {
    toast("一次最多选择 4 个 Skill", true);
    return false;
  }
  state.composerSkills[key] = [...existing, item];
  renderComposerSkills(key);
  return true;
}

function removeComposerSkill(key, skillId) {
  state.composerSkills[key] = composerSkillRecords(key).filter((item) => String(item.id).toLowerCase() !== String(skillId || "").toLowerCase());
  renderComposerSkills(key);
}

function clearComposerSkills(key) {
  state.composerSkills[key] = [];
  renderComposerSkills(key);
}

function currentSkillMention(input) {
  if (!input) return null;
  const cursor = Number.isInteger(input.selectionStart) ? input.selectionStart : input.value.length;
  const before = input.value.slice(0, cursor);
  const match = before.match(/(^|\s)\$([a-zA-Z0-9._-]*)$/);
  if (!match) return null;
  return { query: match[2].toLowerCase(), start: cursor - match[2].length - 1, end: cursor };
}

function closeSkillSuggestions() {
  document.querySelectorAll(".skill-suggestions").forEach((item) => item.remove());
}

function closeContextUsagePopovers() {
  state.contextStatsOpen = false;
  document.querySelectorAll("[data-context-usage].is-open").forEach((control) => {
    control.classList.remove("is-open");
    control.querySelector(".context-usage-trigger")?.setAttribute("aria-expanded", "false");
  });
}

function toggleContextUsagePopover(element) {
  const control = element.closest("[data-context-usage]");
  if (!control) return;
  const willOpen = !control.classList.contains("is-open");
  closeContextUsagePopovers();
  if (!willOpen) return;
  state.contextStatsOpen = true;
  control.classList.add("is-open");
  element.setAttribute("aria-expanded", "true");
  renderContextUsage();
}

function renderSkillSuggestions(input) {
  closeSkillSuggestions();
  const mention = currentSkillMention(input);
  if (!mention) return;
  const selected = new Set(composerSkillRecords(composerKey(input.id)).map((item) => String(item.id).toLowerCase()));
  const candidates = enabledSkillCatalog().filter((item) => {
    const searchable = `${item.id || ""} ${item.name || ""} ${item.description || ""}`.toLowerCase();
    return !selected.has(String(item.id || "").toLowerCase()) && (!mention.query || searchable.includes(mention.query));
  }).slice(0, 8);
  if (!candidates.length) return;
  const popover = document.createElement("div");
  popover.className = "skill-suggestions";
  popover.setAttribute("role", "listbox");
  popover.innerHTML = `<div class="skill-suggestions-head"><span>Skills</span><small><kbd>↑↓</kbd> 移动 · <kbd>Enter</kbd> 选择</small></div>${candidates.map((item, index) => `<button type="button" class="skill-suggestion ${index === 0 ? "is-active" : ""}" data-action="select-skill-suggestion" data-skill-id="${escapeHtml(item.id)}" data-input-id="${escapeHtml(input.id)}" role="option" aria-selected="${index === 0 ? "true" : "false"}"><span><strong>${escapeHtml(item.name || item.id)}</strong><small><code>$${escapeHtml(item.id)}</code><span>${escapeHtml(item.description || "已安装 Skill")}</span></small></span>${item.builtin ? '<em>内置</em>' : ""}</button>`).join("")}`;
  input.closest("form")?.appendChild(popover);
}

function selectSkillSuggestion(input, skillId) {
  const mention = currentSkillMention(input);
  if (!mention || !skillId) return;
  const key = composerKey(input.id);
  if (!addComposerSkill(key, skillId)) return;
  const left = input.value.slice(0, mention.start).replace(/[ \t]+$/, "");
  const right = input.value.slice(mention.end).replace(/^[ \t]+/, "");
  const spacer = left && right && !/\s$/.test(left) ? " " : "";
  input.value = `${left}${spacer}${right}`;
  const cursor = left.length + spacer.length;
  input.setSelectionRange(cursor, cursor);
  closeSkillSuggestions();
  input.focus();
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function moveSkillSuggestion(direction) {
  const options = [...document.querySelectorAll(".skill-suggestion")];
  if (!options.length) return false;
  const current = Math.max(0, options.findIndex((item) => item.classList.contains("is-active")));
  const next = (current + direction + options.length) % options.length;
  options.forEach((item, index) => {
    item.classList.toggle("is-active", index === next);
    item.setAttribute("aria-selected", String(index === next));
  });
  return true;
}

// A local adapter for the official Lucide SVG paths used by ScanSci. Keeping
// the small used subset in the application makes the desktop build
// deterministic: no icon font, CDN, or operating-system fallback is needed.
const uiIconPaths = {
  "circle-plus": '<circle cx="12" cy="12" r="8.5"></circle><path d="M12 8v8M8 12h8"></path>',
  library: '<path d="M4.5 4.5h15v15h-15z"></path><path d="M8.5 4.5v15M12 9h4M12 12h4"></path>',
  wand: '<path d="m6 18 10.5-10.5M13.5 4.5l.7 1.8L16 7l-1.8.7-.7 1.8-.7-1.8L11 7l1.8-.7.7-1.8ZM5 6l.5 1.3L7 8l-1.5.7L5 10l-.5-1.3L3 8l1.5-.7L5 6ZM18 15l.5 1.3L20 17l-1.5.7L18 19l-.5-1.3L16 17l1.5-.7L18 15Z"></path>',
  puzzle: '<path d="M9 4.5H6.5a2 2 0 0 0-2 2V9h2a2 2 0 1 1 0 4h-2v2.5a2 2 0 0 0 2 2H9v-2a2 2 0 1 1 4 0v2h2.5a2 2 0 0 0 2-2V13h-2a2 2 0 1 1 0-4h2V6.5a2 2 0 0 0-2-2H13v2a2 2 0 1 1-4 0v-2Z"></path>',
  folder: '<path d="M3.5 6.5h6l2 2h9v9a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2Z"></path>',
  // Lucide "settings" — kept verbatim so the application settings affordance
  // is the familiar upstream icon rather than a hand-drawn approximation.
  settings: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.38a2 2 0 0 0-.73-2.73l-.15-.09a2 2 0 0 1-1-1.74v-.51a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z"></path><circle cx="12" cy="12" r="3"></circle>',
  "panel-left": '<rect x="3.5" y="4" width="17" height="16" rx="2.5"></rect><path d="M10 4v16"></path>',
  "panel-right": '<rect x="3.5" y="4" width="17" height="16" rx="2.5"></rect><path d="M14 4v16"></path>',
  "arrow-left": '<path d="m11 6-6 6 6 6M5.5 12h13"></path>',
  "arrow-right": '<path d="m13 6 6 6-6 6M18.5 12h-13"></path>',
  "chevron-down": '<path d="m7 9 5 5 5-5"></path>',
  "chevron-left": '<path d="m15 18-6-6 6-6"></path>',
  "chevron-right": '<path d="m9 7 5 5-5 5"></path>',
  plus: '<path d="M12 5v14M5 12h14"></path>',
  minus: '<path d="M5 12h14"></path>',
  check: '<path d="m5.5 12 4.1 4 8.9-8.5"></path>',
  play: '<path d="m8 5 11 7-11 7V5Z"></path>',
  x: '<path d="m7 7 10 10M17 7 7 17"></path>',
  refresh: '<path d="M19 8.5A7.5 7.5 0 1 0 19.5 14"></path><path d="M19.5 4.5v4.8h-4.8"></path>',
  "triangle-alert": '<path d="m21.7 18.2-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-2.8Z"></path><path d="M12 9v4M12 17h.01"></path>',
  "circle-alert": '<circle cx="12" cy="12" r="10"></circle><path d="M12 8v4M12 16h.01"></path>',
  "loader-circle": '<path d="M12 3a9 9 0 1 0 9 9"></path>',
  "map-pin": '<path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z"></path><circle cx="12" cy="10" r="2.5"></circle>',
  "git-branch": '<circle cx="6" cy="5" r="2"></circle><circle cx="18" cy="6" r="2"></circle><circle cx="6" cy="19" r="2"></circle><path d="M6 7v10M8 11h4a6 6 0 0 0 6-3"></path>',
  send: '<path d="M12 20V4"></path><path d="m6 10 6-6 6 6"></path>',
  file: '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"></path><path d="M14 2v5a1 1 0 0 0 1 1h5"></path>',
  "file-text": '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"></path><path d="M14 2v5a1 1 0 0 0 1 1h5"></path><path d="M10 9H8"></path><path d="M16 13H8"></path><path d="M16 17H8"></path>',
  "file-spreadsheet": '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"></path><path d="M14 2v5a1 1 0 0 0 1 1h5"></path><path d="M8 13h2"></path><path d="M14 13h2"></path><path d="M8 17h2"></path><path d="M14 17h2"></path>',
  "file-audio": '<path d="M4 6.835V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.706.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2h-.343"></path><path d="M14 2v5a1 1 0 0 0 1 1h5"></path><path d="M2 19a2 2 0 0 1 4 0v1a2 2 0 0 1-4 0v-4a6 6 0 0 1 12 0v4a2 2 0 0 1-4 0v-1a2 2 0 0 1 4 0"></path>',
  "file-video": '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"></path><path d="M14 2v5a1 1 0 0 0 1 1h5"></path><path d="M15.033 13.44a.647.647 0 0 1 0 1.12l-4.065 2.352a.645.645 0 0 1-.968-.56v-4.704a.645.645 0 0 1 .967-.56z"></path>',
  "file-code": '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"></path><path d="M14 2v5a1 1 0 0 0 1 1h5"></path><path d="M10 12.5 8 15l2 2.5"></path><path d="m14 12.5 2 2.5-2 2.5"></path>',
  "file-plus": '<path d="M6 3.5h7l4.5 4.5v12H6z"></path><path d="M13 3.5V8h4.5M11.5 12v5M9 14.5h5"></path>',
  "folder-open": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"></path>',
  image: '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"></rect><circle cx="9" cy="9" r="2"></circle><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"></path>',
  book: '<path d="M4.5 5.5A2.5 2.5 0 0 1 7 3h11v16H7a2.5 2.5 0 0 0-2.5 2.5v-16Z"></path><path d="M4.5 18.5A2.5 2.5 0 0 1 7 16h11"></path>',
  "book-open": '<path d="M12 7v14"></path><path d="M3 18V5a2 2 0 0 1 2-2h4a3 3 0 0 1 3 3v15a3 3 0 0 0-3-3H3Z"></path><path d="M21 18V5a2 2 0 0 0-2-2h-4a3 3 0 0 0-3 3v15a3 3 0 0 1 3-3h6Z"></path>',
  "message-circle": '<path d="M19.5 11.5a7.5 7.5 0 0 1-8 7.5 8.4 8.4 0 0 1-3.5-.8L4.5 19l.9-3a7.5 7.5 0 1 1 14.1-4.5Z"></path>',
  bot: '<path d="M12 8V4H8"></path><rect width="16" height="12" x="4" y="8" rx="2"></rect><path d="M2 14h2"></path><path d="M20 14h2"></path><path d="M15 13v2"></path><path d="M9 13v2"></path>',
  link: '<path d="M10 13.8 8.2 15.6a3.4 3.4 0 1 1-4.8-4.8l3-3a3.4 3.4 0 0 1 4.8 0"></path><path d="m14 10.2 1.8-1.8a3.4 3.4 0 1 1 4.8 4.8l-3 3a3.4 3.4 0 0 1-4.8 0"></path><path d="m8.5 15.5 7-7"></path>',
  pen: '<path d="m5 19 1.4-4.5L15.8 5a2.1 2.1 0 0 1 3 3l-9.4 9.5L5 19Z"></path><path d="m13.8 7 3.2 3.2"></path>',
  presentation: '<path d="M2 3h20"></path><path d="M21 3v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V3"></path><path d="m7 21 5-5 5 5"></path>',
  layout: '<rect x="3.5" y="4" width="17" height="16" rx="2"></rect><path d="M3.5 9h17M9 9v11"></path>',
  search: '<circle cx="10.7" cy="10.7" r="5.8"></circle><path d="m15 15 4.5 4.5"></path>',
  filter: '<path d="M4 5h16l-6.2 7v5.2L10.2 19v-7L4 5Z"></path>',
  info: '<circle cx="12" cy="12" r="8.5"></circle><path d="M12 10.5V16M12 7.7h.01"></path>',
  sliders: '<path d="M4 6h16M4 12h16M4 18h16"></path><circle cx="9" cy="6" r="1.6"></circle><circle cx="15" cy="12" r="1.6"></circle><circle cx="11" cy="18" r="1.6"></circle>',
  server: '<rect x="4" y="4.5" width="16" height="6" rx="1.5"></rect><rect x="4" y="13.5" width="16" height="6" rx="1.5"></rect><path d="M7.5 7.5h.01M7.5 16.5h.01M11 7.5h5M11 16.5h5"></path>',
  terminal: '<polyline points="4 17 10 11 4 5"></polyline><line x1="12" x2="20" y1="19" y2="19"></line>',
  globe: '<circle cx="12" cy="12" r="8.5"></circle><path d="M3.8 12h16.4M12 3.5c2.2 2.2 3.3 5 3.3 8.5S14.2 18.3 12 20.5C9.8 18.3 8.7 15.5 8.7 12S9.8 5.7 12 3.5Z"></path>',
  sparkles: '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"></path><path d="M20 3v4M22 5h-4M4 17v2M5 18H3"></path>',
  sun: '<circle cx="12" cy="12" r="4"></circle><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4"></path>',
  moon: '<path d="M20.5 14.8A8.5 8.5 0 1 1 9.2 3.5a6.7 6.7 0 0 0 11.3 11.3Z"></path>',
  "shield-check": '<path d="M20 13c0 5-3.5 7.5-8 9-4.5-1.5-8-4-8-9V5l8-3 8 3v5"></path><path d="m9 12 2 2 4-4"></path>',
  "lock-keyhole": '<rect width="18" height="11" x="3" y="10" rx="2"></rect><path d="M7 10V7a5 5 0 0 1 10 0v3"></path><circle cx="12" cy="15.5" r="1"></circle><path d="M12 16.5v1.6"></path>',
  brain: '<path d="M9.2 5.3A3 3 0 0 0 4.5 7.8a3 3 0 0 0 .3 5.7 3.2 3.2 0 0 0 4.7 3.2 3.1 3.1 0 0 0 5 0 3.2 3.2 0 0 0 4.7-3.2 3 3 0 0 0 .3-5.7 3 3 0 0 0-4.7-2.5 3.1 3.1 0 0 0-5.6 0Z"></path><path d="M12 5.5v12.7M8 9.5a2.2 2.2 0 0 0 2.2 2.2M16 9.5a2.2 2.2 0 0 1-2.2 2.2M8 14a2.2 2.2 0 0 1 2.2 2.2M16 14a2.2 2.2 0 0 0-2.2 2.2"></path>',
  eye: '<path d="M3.5 12s3-5.2 8.5-5.2 8.5 5.2 8.5 5.2-3 5.2-8.5 5.2S3.5 12 3.5 12Z"></path><circle cx="12" cy="12" r="2.3"></circle>',
  "eye-off": '<path d="m4 4 16 16"></path><path d="M10.6 6.9A9.7 9.7 0 0 1 12 6.8c5.5 0 8.5 5.2 8.5 5.2a14.4 14.4 0 0 1-2.1 2.7M14.3 14.4a3.2 3.2 0 0 1-4.7-4.3M7.1 8.2A14.7 14.7 0 0 0 3.5 12s3 5.2 8.5 5.2c1.3 0 2.5-.3 3.5-.7"></path>',
  "arrow-up-right": '<path d="M7 17 17 7M9 7h8v8"></path>',
  "arrow-up-down": '<path d="m8 6 4-4 4 4M16 18l-4 4-4-4M12 2v20"></path>',
  wrench: '<path d="M14.5 6a4 4 0 0 0-5 5l-5 5a2 2 0 1 0 2.8 2.8l5-5a4 4 0 0 0 5-5L14 12l-2-2 2.5-4Z"></path>',
  code: '<path d="m8.5 7-4 5 4 5M15.5 7l4 5-4 5"></path>',
  audio: '<path d="M4 13h2M8 9v6M12 6v12M16 9v6M20 13h-2"></path>',
  mic: '<path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z"></path><path d="M19 11.5a7 7 0 0 1-14 0M12 18.5V22M8 22h8"></path>',
  video: '<path d="m16 13 5.223 3.482A.5.5 0 0 0 22 16.066V7.87a.5.5 0 0 0-.752-.432L16 10.5"></path><rect x="2" y="6" width="14" height="12" rx="2"></rect>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M3 5V19A9 3 0 0 0 21 19V5"></path><path d="M3 12A9 3 0 0 0 21 12"></path>',
  "grip-vertical": '<path d="M9 7h.01M15 7h.01M9 12h.01M15 12h.01M9 17h.01M15 17h.01"></path>',
  square: '<rect x="6" y="6" width="12" height="12" rx="1.5"></rect>',
  copy: '<rect x="8" y="8" width="10" height="10" rx="1.5"></rect><path d="M6 15H5.5A1.5 1.5 0 0 1 4 13.5v-8A1.5 1.5 0 0 1 5.5 4h8A1.5 1.5 0 0 1 15 5.5V6"></path>',
  download: '<path d="M12 4v10M8 11l4 4 4-4M5 19.5h14"></path>',
  cpu: '<rect x="4" y="4" width="16" height="16" rx="2"></rect><rect x="9" y="9" width="6" height="6" rx="1"></rect><path d="M9 2v2M15 2v2M9 20v2M15 20v2M20 9h2M20 14h2M2 9h2M2 14h2"></path>',
  "wifi-off": '<path d="M12.3 9.2a10.5 10.5 0 0 1 6.8 2.9M5 12.6a10.8 10.8 0 0 1 2.1-1.4M8.7 16.4a5 5 0 0 1 6.6 0M12 20h.01M2 2l20 20"></path>',
  expand: '<path d="M8 4H4v4M16 4h4v4M20 16v4h-4M4 16v4h4"></path>',
  archive: '<rect width="20" height="5" x="2" y="3" rx="1"></rect><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"></path><path d="M10 12h4"></path>',
  "archive-restore": '<path d="M4 7h16v13H4zM3 4h18v3H3zM8 13a4 4 0 1 1 1.2 2.9M8 13v4h4"></path>',
  "more-horizontal": '<path d="M5 12h.01M12 12h.01M19 12h.01"></path>',
  trash: '<path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5"></path>',
};
uiIconPaths["globe-2"] = uiIconPaths.globe;
uiIconPaths.gpu = uiIconPaths.cpu;
uiIconPaths["refresh-cw"] = uiIconPaths.refresh;

function uiIcon(name, className = "") {
  const path = uiIconPaths[name] || uiIconPaths.info;
  const classes = ["ui-icon", className].filter(Boolean).join(" ");
  return `<svg class="${classes}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${path}</svg>`;
}

function iconElement(name, className = "") {
  const template = document.createElement("template");
  template.innerHTML = uiIcon(name, className);
  return template.content.firstElementChild;
}

const legacyIconNames = new Map([
  ["⊕", "circle-plus"], ["▤", "library"], ["⌁", "wand"], ["✦", "puzzle"], ["⌑", "folder"],
  ["⚙", "settings"], ["←", "arrow-left"], ["→", "arrow-right"], ["⌄", "chevron-down"], ["›", "chevron-right"],
  ["＋", "plus"], ["+", "plus"], ["↑", "send"], ["✓", "check"], ["×", "x"], ["↻", "refresh"],
  ["⌕", "search"], ["ⓘ", "info"], ["⌘", "sliders"], ["◫", "layout"], ["▧", "file-plus"],
  ["⇄", "arrow-up-down"], ["↗", "arrow-up-right"], ["↓", "download"], ["⧉", "copy"], ["−", "minus"],
  ["⠿", "grip-vertical"], ["◌", "circle-plus"], ["◇", "brain"], ["◉", "eye"], ["‹›", "code"], ["∿", "audio"],
]);

function hydrateIcons(root = document) {
  root.querySelectorAll?.("[data-ui-icon]").forEach((placeholder) => {
    placeholder.replaceWith(iconElement(placeholder.dataset.uiIcon, placeholder.dataset.iconClass || ""));
  });
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach((node) => {
    const parent = node.parentElement;
    if (!parent || parent.closest("script, style, textarea, option, code, pre, .provider-logo, [data-icon-preserve]")) return;
    const value = node.nodeValue || "";
    const trimmed = value.trim();
    const exactName = legacyIconNames.get(trimmed);
    if (exactName) {
      node.replaceWith(iconElement(exactName));
      return;
    }
    const match = value.match(/^(\s*)([↻↗])(\s+)/);
    if (!match) return;
    const name = legacyIconNames.get(match[2]);
    if (!name) return;
    const fragment = document.createDocumentFragment();
    if (match[1]) fragment.append(document.createTextNode(match[1]));
    fragment.append(iconElement(name, "ui-icon-inline"));
    fragment.append(document.createTextNode(value.slice(match[0].length)));
    node.replaceWith(fragment);
  });
}

let settingsSelectSequence = 0;

function closeSettingsSelects(except = null) {
  let closed = false;
  document.querySelectorAll("[data-settings-select].is-open").forEach((wrapper) => {
    if (wrapper === except) return;
    wrapper.classList.remove("is-open");
    wrapper.querySelector(".settings-select-trigger")?.setAttribute("aria-expanded", "false");
    const menu = wrapper.querySelector(".settings-select-menu");
    if (menu) menu.hidden = true;
    closed = true;
  });
  return closed;
}

function renderSettingsSelectLabel(target, option) {
  if (!target) return;
  target.replaceChildren();
  const accentColor = String(option?.dataset?.accentColor || "").trim();
  if (accentColor) {
    const label = document.createElement("span");
    label.className = "settings-select-accent-label";
    const swatch = document.createElement("span");
    swatch.className = "settings-select-accent-swatch";
    swatch.style.backgroundColor = accentColor;
    swatch.setAttribute("aria-hidden", "true");
    const value = document.createElement("strong");
    value.className = "settings-select-accent-value";
    value.textContent = accentColor;
    label.append(swatch, value);
    target.append(label);
    return;
  }
  const modelName = String(option?.dataset?.modelName || "").trim();
  const modelMeta = String(option?.dataset?.modelMeta || "").trim();
  if (!modelName) {
    target.textContent = option?.textContent?.trim() || option?.value || "请选择";
    return;
  }
  const label = document.createElement("span");
  label.className = "settings-select-model-label";
  const name = document.createElement("strong");
  name.className = "settings-select-model-name";
  name.textContent = modelName;
  label.append(name);
  if (modelMeta) {
    const meta = document.createElement("small");
    meta.className = "settings-select-model-meta";
    meta.textContent = modelMeta;
    label.append(meta);
  }
  target.append(label);
}

function renderMultiSettingsSelectLabel(target, select) {
  if (!target) return;
  const selected = [...(select?.options || [])]
    .filter((option) => option.selected)
    .map((option) => option.textContent?.trim() || option.value)
    .filter(Boolean);
  target.replaceChildren();
  const label = document.createElement("span");
  label.className = "settings-select-multi-summary";
  label.textContent = selected.join("、") || "请选择";
  target.append(label);
}

function hydrateSettingsSelects(root = document) {
  const selects = root.querySelectorAll?.(".settings-content select") || [];
  selects.forEach((select) => {
    if (select.closest("[data-settings-select]")) return;
    const isMultiple = select.multiple;
    const wrapper = document.createElement("div");
    wrapper.className = "settings-select";
    if (isMultiple) wrapper.classList.add("is-multiple");
    wrapper.dataset.settingsSelect = "true";
    select.parentNode.insertBefore(wrapper, select);
    wrapper.append(select);

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "settings-select-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    const accessibleLabel = select.getAttribute("aria-label")
      || select.closest("label")?.querySelector(".settings-row > span > strong, .default-capability-copy > strong, .setting-field > span, .document-select-row > span, .cherry-field > span")?.textContent?.trim()
      || select.name
      || "选择选项";
    trigger.setAttribute("aria-label", accessibleLabel);

    const menu = document.createElement("div");
    menu.className = "settings-select-menu";
    menu.hidden = true;
    menu.id = `settings-select-menu-${++settingsSelectSequence}`;
    menu.setAttribute("role", "listbox");
    if (isMultiple) menu.setAttribute("aria-multiselectable", "true");
    trigger.setAttribute("aria-controls", menu.id);

    const updateSelection = () => {
      if (isMultiple) renderMultiSettingsSelectLabel(trigger, select);
      else renderSettingsSelectLabel(trigger, select.options[select.selectedIndex]);
      menu.querySelectorAll("[role=option]").forEach((option) => {
        const isSelected = isMultiple
          ? [...select.options].some((candidate) => candidate.value === option.dataset.value && candidate.selected)
          : option.dataset.value === select.value;
        option.classList.toggle("is-selected", isSelected);
        option.setAttribute("aria-selected", isSelected ? "true" : "false");
        const check = option.querySelector(".settings-select-multi-check");
        if (check) {
          check.classList.toggle("is-selected", isSelected);
          check.replaceChildren();
          if (isSelected) check.append(iconElement("check"));
        }
      });
    };
    const setOpen = (open) => {
      if (open) closeSettingsSelects(wrapper);
      wrapper.classList.toggle("is-open", open);
      trigger.setAttribute("aria-expanded", open ? "true" : "false");
      menu.hidden = !open;
    };

    [...select.options].forEach((option) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = `settings-select-option${isMultiple ? " is-multiple" : ""}`;
      item.dataset.value = option.value;
      renderSettingsSelectLabel(item, option);
      if (isMultiple) {
        const check = document.createElement("span");
        check.className = "settings-select-multi-check";
        check.setAttribute("aria-hidden", "true");
        item.append(check);
      }
      item.disabled = option.disabled;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", "false");
      item.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (item.disabled) return;
        if (isMultiple) option.selected = !option.selected;
        else select.value = option.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        updateSelection();
        if (isMultiple) item.focus();
        else {
          setOpen(false);
          trigger.focus();
        }
      });
      menu.append(item);
    });

    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setOpen(!wrapper.classList.contains("is-open"));
    });
    trigger.addEventListener("keydown", (event) => {
      if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        setOpen(true);
        menu.querySelector(".settings-select-option:not(:disabled)")?.focus();
      } else if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
      }
    });
    select.addEventListener("change", updateSelection);
    select.classList.add("settings-native-select");
    select.tabIndex = -1;
    select.hidden = true;
    select.setAttribute("aria-hidden", "true");
    wrapper.append(trigger, menu);
    updateSelection();
  });
}

function observeIcons() {
  hydrateIcons();
  const observer = new MutationObserver((records) => {
    records.forEach((record) => record.addedNodes.forEach((node) => {
      if (node.nodeType === Node.ELEMENT_NODE) hydrateIcons(node);
      else if (node.nodeType === Node.TEXT_NODE && node.parentElement) hydrateIcons(node.parentElement);
    }));
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

function compact(value = "", max = 110) {
  const text = String(value).replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

function readerUrl(docId, anchor = "") {
  return `/api/sources/${encodeURIComponent(docId)}/reader${anchor ? `#${encodeURIComponent(anchor)}` : ""}`;
}

function evidenceReaderFrameUrl(value = "") {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const [route, anchor = ""] = raw.split("#", 2);
  const separator = route.includes("?") ? "&" : "?";
  const preferences = appearancePreferences();
  const query = new URLSearchParams({ theme: resolvedTheme(preferences.theme), accent: preferences.accent });
  return `${route}${separator}${query.toString()}${anchor ? `#${anchor}` : ""}`;
}

function refreshEvidenceReaderTheme() {
  const frame = byId("evidenceReaderFrame");
  const reader = state.activeEvidence?.reader_url;
  const originalActive = byId("evidenceReaderPanel")?.querySelector('[data-action="show-evidence-original"].is-active');
  if (frame && reader && state.contextPanel === "evidence" && !originalActive) frame.src = evidenceReaderFrameUrl(reader);
}

function citationMarkerMarkup(citationId) {
  const marker = escapeHtml(citationId);
  return `<button class="citation-marker" type="button" data-citation-id="${marker}" aria-label="查看引用 ${marker}" aria-haspopup="dialog">[${marker}]</button>`;
}

function citationTextMarkup(value, citations = []) {
  const records = Array.isArray(citations) ? citations : [];
  const knownCitationIds = new Set(
    records
      .map((citation) => String(citation?.citation_id || "").trim())
      .filter(Boolean),
  );
  const source = String(value || "");
  if (!source || !knownCitationIds.size) return renderAssistantContent(source);
  const tokenized = source.replace(/\[(\d+)\]/g, (marker, citationId) => (
    knownCitationIds.has(citationId) ? `@@SCANSCI_CITATION_${citationId}@@` : marker
  ));
  return renderAssistantContent(tokenized).replace(/@@SCANSCI_CITATION_(\d+)@@/g, (_token, citationId) => (
    citationMarkerMarkup(citationId)
  ));
}

function citationRecordsForRun(run = {}) {
  const records = [
    ...(run.output_artifact?.payload?.reader_answer?.citations || []),
    ...(run.output_artifact?.evidence_links || []),
    ...(Array.isArray(run.messages)
      ? run.messages.flatMap((message) => message.reader_answer?.citations || message.metadata?.reader_answer?.citations || [])
      : []),
  ];
  const byId = new Map();
  records.forEach((citation) => {
    const id = String(citation?.citation_id || "").trim();
    if (id) byId.set(id, citation);
  });
  return [...byId.values()];
}

function safeReaderUrl(record = {}) {
  const supplied = String(record.reader_url || "");
  // Task-scoped Deep Research evidence lives under /api/runs.  Both routes
  // are local, application-controlled readers; reject every other URL here.
  if (supplied.startsWith("/api/sources/") || supplied.startsWith("/api/runs/")) return supplied;
  const docId = String(record.doc_id || "").trim();
  if (docId.startsWith("external:")) return "";
  return docId ? readerUrl(docId, String(record.html_anchor || "")) : "";
}

function safeEvidenceSourceUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["https:", "http:"].includes(url.protocol) ? url.href : "";
  } catch (_) {
    return "";
  }
}

function citationPublicSourceUrl(record = {}) {
  const supplied = safeEvidenceSourceUrl(record.source_href || record.original_url || record.source_url || record.url || "");
  if (supplied) return supplied;
  const doi = String(record.doi || "")
    .trim()
    .replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")
    .replace(/^doi:\s*/i, "");
  return doi && !/\s/.test(doi) ? safeEvidenceSourceUrl(`https://doi.org/${encodeURI(doi)}`) : "";
}

function sourceTitle(record = {}) {
  return String(record.paper || record.title || record.doc_id || "未命名来源");
}

function evidenceMeta(record = {}) {
  return String(record.section || "").trim();
}

function evidenceDetailsMarkup(record = {}, compact = false) {
  const rows = [
    ...(compact ? [["文献", sourceTitle(record)]] : []),
    ["DOI", record.doi],
    ["证据 ID", record.evidence_id],
  ]
    .map(([label, value]) => [String(label), String(value || "").trim()])
  .filter(([, value]) => value);
  if (!rows.length) return "";
  const summary = compact ? '<summary aria-label="来源详情" title="来源详情">?</summary>' : `<summary>来源详情${uiIcon("chevron-right", "evidence-source-details-chevron")}</summary>`;
  return `<details class="evidence-source-details${compact ? " is-compact" : ""}">${summary}<dl>${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl></details>`;
}

function clearCitationPreviewTimer() {
  window.clearTimeout(state.citationPreviewTimer);
  state.citationPreviewTimer = 0;
}

function hideCitationPreview() {
  clearCitationPreviewTimer();
  const preview = byId("citationPreview");
  if (!preview) return;
  preview.classList.remove("is-visible");
  preview.setAttribute("aria-hidden", "true");
}

function deferCitationPreviewHide() {
  clearCitationPreviewTimer();
  state.citationPreviewTimer = window.setTimeout(hideCitationPreview, 130);
}

function citationContextMarkup(citation = {}, maxChars = 680) {
  const rows = [
    { label: "上一句", value: citation.context_before, className: "is-before" },
    { label: "命中片段", value: citation.matched_text || citation.exact_quote, className: "is-match" },
    { label: "下一句", value: citation.context_after, className: "is-after" },
  ]
    .map((row) => ({ ...row, value: compact(row.value || "", maxChars) }))
    .filter((row) => row.value);
  if (!rows.length) return "";
  return `<div class="citation-context" aria-label="原文上下文">${rows.map((row) => `<div class="citation-context-row ${row.className}"><span>${row.label}</span><p>${escapeHtml(row.value)}</p></div>`).join("")}</div>`;
}

function showCitationPreview(citation, marker) {
  const preview = byId("citationPreview");
  if (!preview || !citation || !marker) return;
  if (state.contextPanel === "evidence") {
    hideCitationPreview();
    return;
  }
  clearCitationPreviewTimer();
  const quote = compact(citation.exact_quote || "当前引用未保存原文摘录。", 220);
  const meta = evidenceMeta(citation);
  const context = citationContextMarkup(citation, 220);
  const hint = safeReaderUrl(citation)
    ? "点击脚标可在本应用中查看全文与高亮原文。"
    : citationPublicSourceUrl(citation)
      ? "点击脚标可展开证据，并跳转公开原文。"
      : "点击脚标可展开本次任务保存的证据摘录。";
  preview.innerHTML = `<div class="citation-preview-kicker">证据 ${escapeHtml(citation.citation_id || "")}</div><h3>${escapeHtml(sourceTitle(citation))}</h3>${meta ? `<p class="citation-preview-meta">${escapeHtml(meta)}</p>` : ""}${context || `<blockquote>${escapeHtml(quote)}</blockquote>`}<p class="citation-preview-hint">${escapeHtml(hint)}</p>`;
  preview.classList.add("is-visible");
  preview.setAttribute("aria-hidden", "false");
  const rect = marker.getBoundingClientRect();
  const width = Math.min(320, Math.max(240, window.innerWidth - 32));
  preview.style.width = `${width}px`;
  const previewHeight = preview.offsetHeight;
  const left = Math.min(Math.max(16, rect.left), Math.max(16, window.innerWidth - width - 16));
  const below = rect.bottom + 9;
  const top = below + previewHeight <= window.innerHeight - 16 ? below : Math.max(16, rect.top - previewHeight - 9);
  preview.style.left = `${left}px`;
  preview.style.top = `${top}px`;
}

function openSourceReader(source) {
  if (!source?.doc_id) return;
  if (state.activeView !== "conversation") setView("conversation");
  showEvidenceReader({
    paper: source.title,
    doc_id: source.doc_id,
    doi: source.doi || "",
    reader_url: readerUrl(source.doc_id),
    original_url: `/api/sources/${encodeURIComponent(source.doc_id)}/original`,
    exact_quote: "已打开此来源的全文。选择回答中的脚标可直接定位至对应证据。",
  }, { returnPanel: "sources", sourceOnly: true });
}

function locateEvidenceReaderQuote(readerDocument, citation) {
  const quote = String(citation.exact_quote || citation.matched_text || "").replace(/\s+/g, "");
  const fullText = readerDocument?.querySelector("[data-scansci-source-text]");
  const textNode = fullText?.firstChild;
  if (quote.length < 12 || textNode?.nodeType !== 3) return false;
  const source = textNode.textContent || "";
  const offsets = [];
  const normalized = [];
  for (let index = 0; index < source.length; index += 1) {
    if (/\s/.test(source[index])) continue;
    offsets.push(index);
    normalized.push(source[index]);
  }
  const searchable = normalized.join("");
  const start = searchable.indexOf(quote);
  if (start < 0 || searchable.indexOf(quote, start + 1) >= 0) return false;
  const highlight = readerDocument.createElement("mark");
  highlight.id = String(citation.html_anchor || "");
  highlight.dataset.scansciFallbackMatch = "true";
  const range = readerDocument.createRange();
  range.setStart(textNode, offsets[start]);
  range.setEnd(textNode, offsets[start + quote.length - 1] + 1);
  range.surroundContents(highlight);
  fullText.closest("details")?.setAttribute("open", "");
  readerDocument.querySelector(".empty")?.setAttribute("hidden", "");
  return true;
}

function centerEvidenceReaderAnchor() {
  const anchor = String(state.activeEvidence?.html_anchor || "");
  const frame = byId("evidenceReaderFrame");
  if (!anchor || state.contextPanel !== "evidence" || !frame?.contentDocument) return;
  frame.contentDocument.getElementById(anchor)?.scrollIntoView({ block: "center", inline: "nearest" });
}

function showEvidenceReader(citation, options = {}) {
  hideCitationPreview();
  const target = byId("evidenceReaderPanel");
  const reader = safeReaderUrl(citation);
  if (!target || !reader) {
    toast("该引用缺少可读取的本地来源。", true);
    return;
  }
  const current = state.contextPanel === "evidence" ? state.evidenceReturnPanel : state.contextPanel;
  state.evidenceReturnPanel = options.returnPanel || (["review", "none"].includes(current) ? current : "sources");
  state.activeEvidence = { ...citation, reader_url: reader, sourceOnly: Boolean(options.sourceOnly) };
  const quote = String(citation.exact_quote || "已打开来源全文；被回答使用的原文会以高亮显示。") || "已打开来源全文。";
  const meta = evidenceMeta(citation);
  const anchorNote = citation.html_anchor ? "正在定位该回答使用的原文片段" : "正在显示来源全文";
  const context = citationContextMarkup(citation, 460);
  const details = evidenceDetailsMarkup(citation, true);
  const original = String(citation.original_url || "");
  const tabs = original ? `<div class="evidence-reader-tabs"><button type="button" class="is-active" data-action="show-evidence-blocks">证据定位</button><button type="button" data-action="show-evidence-original" data-original-url="${escapeHtml(original)}">原始文件</button></div>` : "";
  target.innerHTML = `<header class="evidence-reader-head"><button type="button" class="evidence-reader-back" data-action="close-evidence-reader" aria-label="返回来源列表">←</button><div><span>${options.sourceOnly ? "来源全文" : "原文全文 · 命中定位"}</span><p>${escapeHtml(!options.sourceOnly && meta ? `命中章节：${meta}` : anchorNote)}</p></div>${details}</header>${tabs}<div class="evidence-reader-summary" hidden role="status"><span class="evidence-reader-fallback-label">原文暂时无法显示；以下是保存的证据摘录</span>${options.sourceOnly ? "" : context || `<blockquote>${escapeHtml(compact(quote, 680))}</blockquote>`}</div><div class="evidence-reader-frame-wrap"><iframe class="evidence-reader-frame" id="evidenceReaderFrame" title="${escapeHtml(sourceTitle(citation))} 的来源全文" src="${escapeHtml(evidenceReaderFrameUrl(reader))}" sandbox="allow-same-origin"></iframe></div>`;
  setContextPanel("evidence");
  const frame = target.querySelector(".evidence-reader-frame");
  const fallback = target.querySelector(".evidence-reader-summary");
  const frameWrap = target.querySelector(".evidence-reader-frame-wrap");
  const heading = target.querySelector(".evidence-reader-head span");
  const subtitle = target.querySelector(".evidence-reader-head p");
  const showReaderState = (available, located, mode = "anchor") => {
    fallback.hidden = available && located;
    frameWrap.hidden = !available;
    if (!options.sourceOnly) {
      if (mode === "text") {
        heading.textContent = "转换文本 · 引文定位";
        subtitle.textContent = located
          ? "原证据锚点不可用，已按引文文字定位"
          : "证据块索引不可用，可浏览转换文本";
      } else if (mode === "original") {
        heading.textContent = "原始文件";
        subtitle.textContent = available ? "正在显示原始文件" : "原始文件无法显示";
      } else {
        heading.textContent = "原文全文 · 证据定位";
        subtitle.textContent = !located && available ? "当前来源无法定位这条引用" : meta ? `命中章节：${meta}` : anchorNote;
      }
    }
    if (!fallback.hidden) {
      let message = available
        ? "当前来源无法定位这条引用；以下是保存的证据摘录"
        : options.sourceOnly ? "来源全文暂时无法显示" : "原文暂时无法显示；以下是保存的证据摘录";
      if (mode === "original" && !available) {
        message = options.sourceOnly ? "原始文件无法显示" : "原始文件无法显示；以下是保存的证据摘录";
      }
      fallback.querySelector(".evidence-reader-fallback-label").textContent = message;
    }
  };
  frame.addEventListener("load", () => {
    if (target.querySelector('.evidence-reader-tabs button.is-active')?.dataset.action === "show-evidence-original") {
      const failed = frame.contentDocument?.contentType === "application/json";
      showReaderState(!failed, !failed, "original");
      return;
    }
    const readerDocument = frame.contentDocument;
    const available = Boolean(readerDocument?.querySelector('style[data-scansci-reader-theme="true"]'));
    const anchorLocated = Boolean(citation.html_anchor && readerDocument?.getElementById(citation.html_anchor));
    const textLocated = available && !anchorLocated && Boolean(citation.html_anchor)
      && locateEvidenceReaderQuote(readerDocument, citation);
    const located = !citation.html_anchor || anchorLocated || textLocated;
    const textMode = Boolean(readerDocument?.querySelector("[data-scansci-source-text]"))
      && !readerDocument?.querySelector("[data-evidence-id]");
    if (textMode) {
      readerDocument.querySelector("[data-scansci-source-text]")?.closest("details")?.setAttribute("open", "");
      readerDocument.querySelector(".empty")?.setAttribute("hidden", "");
    }
    const firstTab = target.querySelector('[data-action="show-evidence-blocks"]');
    if (firstTab) firstTab.textContent = textLocated || textMode ? "转换文本" : "证据定位";
    showReaderState(available, located, textLocated || (textMode && !anchorLocated) ? "text" : "anchor");
    if (available && located && citation.html_anchor) window.requestAnimationFrame(centerEvidenceReaderAnchor);
  });
  frame.addEventListener("error", () => showReaderState(false, false));
}

function closeEvidenceReader() {
  const returnPanel = state.evidenceReturnPanel === "review" && state.reviewDocument && state.reviewDocumentOpen
    ? "review"
    : state.evidenceReturnPanel === "none" ? "none" : "sources";
  state.activeEvidence = null;
  setContextPanel(returnPanel);
}

function bindCitationInteractions(result, scope = byId("answerArea")) {
  const reader = result.reader_answer || {};
  const citationMap = new Map((reader.citations || []).map((citation) => [String(citation.citation_id), citation]));
  if (!scope) return;
  scope.querySelectorAll("[data-citation-id]").forEach((marker) => {
    const citation = citationMap.get(String(marker.dataset.citationId));
    if (!citation) return;
    marker.addEventListener("pointerenter", () => showCitationPreview(citation, marker));
    marker.addEventListener("pointerleave", deferCitationPreviewHide);
    marker.addEventListener("focus", () => showCitationPreview(citation, marker));
    marker.addEventListener("blur", deferCitationPreviewHide);
    marker.addEventListener("keydown", (event) => {
      if (event.key === "Escape") hideCitationPreview();
    });
    marker.addEventListener("click", (event) => {
      event.preventDefault();
      hideCitationPreview();
      showEvidenceReader(citation);
    });
  });
  const preview = byId("citationPreview");
  if (preview) {
    preview.onpointerenter = clearCitationPreviewTimer;
    preview.onpointerleave = deferCitationPreviewHide;
  }
}

function toast(message, isError = false) {
  const target = byId("toast");
  if (!target) return;
  target.textContent = message;
  target.classList.toggle("is-error", isError);
  target.setAttribute("role", isError ? "alert" : "status");
  target.setAttribute("aria-live", isError ? "assertive" : "polite");
  target.classList.add("is-visible");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(
    () => target.classList.remove("is-visible"),
    isError ? 10000 : 2800,
  );
}

function closeNotionWizard(result = null) {
  if (!notionWizardResolve) return;
  const resolve = notionWizardResolve;
  notionWizardResolve = null;
  byId("confirmDialogHost")?.replaceChildren();
  document.body.classList.remove("has-confirm-dialog");
  const scopeRestore = notionWizardScopeRestore;
  notionWizardScopeRestore = null;
  if (scopeRestore) {
    state.knowledgeScopeDraftIds = scopeRestore.draftIds;
    renderKnowledgeScopeDialog();
    const scopeDialog = byId("knowledgeScopeDialog");
    if (scopeDialog && !scopeDialog.open) scopeDialog.showModal();
  }
  resolve(result);
}

function openNotionWizard() {
  if (notionWizardResolve) closeNotionWizard(null);
  const host = byId("confirmDialogHost");
  if (!host) return Promise.resolve(null);
  const scopeDialog = byId("knowledgeScopeDialog");
  const resumeScope = Boolean(scopeDialog?.open);
  const scopeDraftIds = Array.isArray(state.knowledgeScopeDraftIds)
    ? [...state.knowledgeScopeDraftIds]
    : null;
  if (resumeScope) scopeDialog.close();
  notionWizardScopeRestore = resumeScope ? { draftIds: scopeDraftIds } : null;
  const wizard = {
    step: 1,
    token: "",
    rootPageId: "",
    title: "Notion 知识库",
    verified: false,
    tokenConfigured: Boolean(state.settings?.notion_api_token_configured),
  };
  host.innerHTML = `<div class="confirm-dialog-backdrop" data-notion-wizard-cancel><section class="confirm-dialog-card notion-wizard-card" role="dialog" aria-modal="true" aria-labelledby="notionWizardTitle"><div class="confirm-dialog-icon notion-brand-icon" aria-hidden="true"><img src="/notion-logo.png" alt="" /></div><div class="confirm-dialog-copy"><p class="confirm-dialog-eyebrow">Notion 知识库接入</p><div class="notion-wizard-progress"><span>01</span><i></i><span>02</span><i></i><span>03</span></div><div id="notionWizardBody"></div></div><footer class="confirm-dialog-actions"><button type="button" class="secondary-button" data-notion-wizard-cancel>取消</button><button type="button" class="secondary-button" id="notionWizardBack">上一步</button><button type="button" class="primary-button" id="notionWizardNext">下一步</button></footer></section></div>`;
  document.body.classList.add("has-confirm-dialog");
  const body = byId("notionWizardBody");
  const setStatus = (message, error = false) => { const status = byId("notionWizardStatus"); if (status) { status.textContent = message; status.classList.toggle("is-error", error); } };
  const saveInputs = () => {
    const tokenInput = byId("notionWizardToken");
    if (tokenInput) wizard.token = tokenInput.value.trim();
    wizard.rootPageId = byId("notionWizardRoot")?.value.trim() || wizard.rootPageId;
    wizard.title = byId("notionWizardTitleInput")?.value.trim() || wizard.title;
  };
  const render = () => {
    saveInputs();
    const temporaryPreview = state.settings?.workspace_profile === "temporary" && !window.pywebview?.api;
    const credentialNote = temporaryPreview
      ? `<p class="notion-wizard-tip">当前是临时浏览器预览，资料与密钥和 ScanSci 桌面软件隔离。${wizard.tokenConfigured ? "此预览工作区已保存密钥，可留空直接验证；它不会同步到桌面软件。" : "桌面软件会复用其工作区已保存的密钥；只有单独测试此预览时才需要在这里输入。"}</p>`
      : wizard.tokenConfigured
        ? `<p class="notion-wizard-tip">已找到当前工作区保存在本机的密钥，可直接验证复用；留空即可，无需重新粘贴。</p>`
        : `<p class="notion-wizard-tip">验证成功后，密钥会保存在本机凭据管理器。同一工作区的桌面软件与浏览器会自动复用；若已在另一工作区保存，请切换到该工作区。</p>`;
    body.innerHTML = wizard.step === 1
      ? `<h2 id="notionWizardTitle">第 1 步：验证 Notion 密钥</h2>${credentialNote}<a class="notion-wizard-open" href="https://app.notion.com/developers/tokens" target="_blank" rel="noreferrer">打开 Notion 密钥创建页 ↗</a><label class="notion-wizard-label">Integration Token<input id="notionWizardToken" type="password" autocomplete="off" value="${escapeHtml(wizard.token)}" placeholder="${wizard.tokenConfigured ? "留空复用本机密钥；或粘贴新的 Token" : "粘贴 ntn_…"}" /></label><p class="notion-wizard-status" id="notionWizardStatus">${wizard.verified ? "密钥验证成功，点击下一步。" : wizard.tokenConfigured ? "点击“验证已保存密钥”继续。" : "首次连接时需要输入 Notion Token。"}</p>`
      : wizard.step === 2
        ? `<h2 id="notionWizardTitle">第 2 步：确认同步全部资料</h2><p class="notion-wizard-lead">PAT 会按照你的 Notion 用户权限读取全部可访问内容。你不需要打开任何单篇笔记，也不需要寻找“连接”菜单。</p><div class="notion-wizard-scope-card"><strong>同步范围：你的全部 Notion 内容</strong><span>包括你能看到的页面、子页面、数据库、数据库条目和文件。ScanSci 会自动搜索它们。</span></div><label class="notion-wizard-label">知识库名称<input id="notionWizardTitleInput" type="text" value="${escapeHtml(wizard.title)}" /></label><p class="notion-wizard-tip">如果以后 Notion 中新增内容，再次同步即可更新。ScanSci 不会修改你的 Notion 原文。</p>`
        : `<h2 id="notionWizardTitle">第 3 步：开始同步</h2><p class="notion-wizard-lead">PAT 已验证。点击下面的按钮后，ScanSci 会把你当前 Notion 用户能够访问的全部内容建立为一个固定知识库。</p><div class="notion-wizard-summary"><span>Token</span><strong>已验证</strong><span>同步范围</span><strong>全部可访问内容</strong><span>知识库</span><strong>${escapeHtml(wizard.title || "Notion 知识库")}</strong></div><p class="notion-wizard-tip">点击“连接并同步”后，Notion 会出现在知识库左侧列表中。</p>`;
    byId("notionWizardBack").disabled = wizard.step === 1;
    byId("notionWizardNext").textContent = wizard.step === 1 && !wizard.verified
      ? wizard.tokenConfigured && !wizard.token ? "验证已保存密钥" : "验证 Token"
      : wizard.step === 3 ? "连接并同步" : "下一步";
    host.querySelectorAll(".notion-wizard-progress span").forEach((item, index) => item.classList.toggle("is-active", index + 1 === wizard.step));
  };
  host.querySelectorAll("[data-notion-wizard-cancel]").forEach((item) => item.addEventListener("click", (event) => {
    if (event.target === item) closeNotionWizard(null);
  }));
  byId("notionWizardBack").addEventListener("click", () => { saveInputs(); if (wizard.step > 1) { wizard.step -= 1; render(); } });
  byId("notionWizardNext").addEventListener("click", async () => {
    saveInputs();
    if (wizard.step === 1) {
      if (!wizard.token && !wizard.tokenConfigured) return setStatus("请先粘贴 Integration Token。", true);
      if (!wizard.verified) {
        setStatus(wizard.token ? "正在验证 Token…" : "正在验证本机已保存的密钥…");
        try {
          const result = await request("/api/notion/test", { method: "POST", body: JSON.stringify({ token: wizard.token }) });
          if (wizard.token) {
            state.settings = mergeProviderCatalogIntoSettings(
              await request("/api/notion/token", { method: "POST", body: JSON.stringify({ token: wizard.token }) }),
              state.presets,
            );
            wizard.tokenConfigured = true;
          }
          wizard.verified = true;
          render();
          setStatus(wizard.token ? `验证成功，密钥已保存在本机；${result.name || "Notion Integration"}。` : `已复用本机密钥验证成功：${result.name || "Notion Integration"}。`);
        } catch (error) {
          setStatus(`${error.message}。请检查 Token，或确认当前工作区已保存密钥。`, true);
        }
        return;
      }
      wizard.step = 2; render(); return;
    }
    if (wizard.step === 2) { wizard.step = 3; render(); return; }
    closeNotionWizard({ token: wizard.token, rootPageId: wizard.rootPageId, title: wizard.title });
  });
  render();
  return new Promise((resolve) => { notionWizardResolve = resolve; });
}

function settleConfirmation(confirmed) {
  if (!confirmDialogResolve) return;
  const resolve = confirmDialogResolve;
  const previousFocus = confirmDialogPreviousFocus;
  confirmDialogResolve = null;
  confirmDialogPreviousFocus = null;
  byId("confirmDialogHost")?.replaceChildren();
  document.body.classList.remove("has-confirm-dialog");
  resolve(Boolean(confirmed));
  window.requestAnimationFrame(() => previousFocus?.focus?.({ preventScroll: true }));
}

function requestConfirmation({
  eyebrow = "请确认操作",
  title = "继续此操作？",
  subject = "",
  message = "",
  confirmLabel = "确认",
  cancelLabel = "取消",
  danger = false,
} = {}) {
  if (confirmDialogResolve) settleConfirmation(false);
  const host = byId("confirmDialogHost");
  if (!host) return Promise.resolve(false);
  confirmDialogPreviousFocus = document.activeElement;
  host.innerHTML = `
    <div class="confirm-dialog-backdrop" data-action="cancel-confirm-dialog">
      <section class="confirm-dialog-card" data-action="confirm-dialog-content" role="dialog" aria-modal="true" aria-labelledby="confirmDialogTitle" aria-describedby="confirmDialogMessage">
        <div class="confirm-dialog-icon${danger ? " is-danger" : ""}" aria-hidden="true">${uiIcon(danger ? "trash" : "info")}</div>
        <div class="confirm-dialog-copy">
          <p class="confirm-dialog-eyebrow">${escapeHtml(eyebrow)}</p>
          <h2 id="confirmDialogTitle">${escapeHtml(title)}</h2>
          ${subject ? `<p class="confirm-dialog-subject">${escapeHtml(subject)}</p>` : ""}
          ${message ? `<p class="confirm-dialog-message" id="confirmDialogMessage">${escapeHtml(message)}</p>` : '<p class="visually-hidden" id="confirmDialogMessage">请确认是否继续。</p>'}
        </div>
        <footer class="confirm-dialog-actions">
          <button type="button" class="confirm-dialog-button is-cancel" data-action="cancel-confirm-dialog">${escapeHtml(cancelLabel)}</button>
          <button type="button" class="confirm-dialog-button${danger ? " is-danger" : " is-primary"}" data-action="accept-confirm-dialog">${escapeHtml(confirmLabel)}</button>
        </footer>
      </section>
    </div>`;
  document.body.classList.add("has-confirm-dialog");
  return new Promise((resolve) => {
    confirmDialogResolve = resolve;
    window.requestAnimationFrame(() => host.querySelector('[data-action="cancel-confirm-dialog"]')?.focus());
  });
}

function safeReviewFileName(title = "") {
  const value = String(title || "")
    .trim()
    .replace(/[\\/:*?"<>|\x00-\x1f]+/g, "-")
    .replace(/\s+/g, " ")
    .replace(/[ .]+$/g, "")
    .slice(0, 120)
    .replace(/[ .]+$/g, "");
  return value || "ScanSci-笔记";
}

function reviewSaveFolderName(value = "") {
  const name = String(value || "").trim();
  if (!name) return "";
  if (name === "." || name === ".." || /[\\/:*?"<>|]/.test(name)) {
    throw new Error("新建文件夹名称只能是单个文件夹名");
  }
  return name.slice(0, 120).replace(/[ .]+$/g, "");
}

function reviewSaveLocationLabel(dialog = state.reviewSaveDialog) {
  const base = String(dialog?.folderPath || dialog?.browserFolderLabel || "").trim();
  const child = String(dialog?.newFolderName || "").trim();
  if (!base) return "尚未选择文件夹";
  return child ? `${base.replace(/[\\/]$/, "")} / ${child}` : base;
}

function reviewPickedFolderPath(value) {
  if (Array.isArray(value)) return reviewPickedFolderPath(value[0]);
  if (value && typeof value === "object") {
    return reviewPickedFolderPath(value.path || value.folder_path || value.folder || value.directory);
  }
  return String(value || "").trim();
}

function closeReviewSaveDialog() {
  if (!state.reviewSaveDialog?.open) return;
  state.reviewSaveDialog = {
    open: false,
    folderPath: "",
    newFolderName: "",
    browserDirectoryHandle: null,
    browserFolderLabel: "",
    browserFolderMode: "",
    busy: false,
  };
  const host = byId("confirmDialogHost");
  if (host?.dataset.dialogKind === "review-save") {
    host.replaceChildren();
    delete host.dataset.dialogKind;
    document.body.classList.remove("has-confirm-dialog");
  }
}

function renderReviewSaveDialog() {
  const dialog = state.reviewSaveDialog;
  const host = byId("confirmDialogHost");
  if (!host) return;
  if (!dialog?.open) {
    if (host.dataset.dialogKind === "review-save") closeReviewSaveDialog();
    return;
  }
  const hasFolder = Boolean(dialog.folderPath || dialog.browserDirectoryHandle || (dialog.browserFolderMode === "input" && dialog.browserFolderLabel));
  const location = reviewSaveLocationLabel(dialog);
  host.dataset.dialogKind = "review-save";
  host.innerHTML = `
    <div class="confirm-dialog-backdrop" data-action="close-review-save-dialog">
      <section class="confirm-dialog-card review-save-dialog" data-action="review-save-dialog-content" role="dialog" aria-modal="true" aria-labelledby="reviewSaveDialogTitle">
        <div class="confirm-dialog-icon review-save-dialog-icon" aria-hidden="true">${uiIcon("folder-open")}</div>
        <div class="confirm-dialog-copy">
          <p class="confirm-dialog-eyebrow">保存研究笔记</p>
          <h2 id="reviewSaveDialogTitle">选择保存位置</h2>
          <p class="confirm-dialog-subject">${escapeHtml(state.reviewDocument?.title || "研究稿件")}</p>
          <p class="confirm-dialog-message">会保存一份 Markdown 文件，同时登记到当前知识库，方便后续检索和查看。</p>
          <div class="review-save-location">
            <div class="review-save-location-copy"><span>目标文件夹</span><strong title="${escapeHtml(location)}">${escapeHtml(location)}</strong></div>
            <button type="button" class="review-save-folder-button" data-action="choose-review-save-folder" ${dialog.busy ? "disabled" : ""}>${uiIcon("folder-open")}选择文件夹</button>
            <input id="reviewSaveFolderInput" class="review-save-folder-input" type="file" webkitdirectory directory multiple />
          </div>
          ${dialog.error ? `<p class="review-save-error">${escapeHtml(dialog.error)}</p>` : ""}
          <label class="review-save-new-folder"><span>新建文件夹（可选）</span><input id="reviewSaveNewFolderInput" type="text" maxlength="120" value="${escapeHtml(dialog.newFolderName || "")}" placeholder="例如：2026-08-08 光伏综述" ${dialog.busy ? "disabled" : ""} /></label>
          <p class="review-save-hint">填写后，会在选定位置下新建这个文件夹，并把笔记保存进去。</p>
        </div>
        <footer class="confirm-dialog-actions">
          <button type="button" class="confirm-dialog-button is-cancel" data-action="close-review-save-dialog" ${dialog.busy ? "disabled" : ""}>取消</button>
          <button type="button" class="confirm-dialog-button is-primary" data-action="confirm-review-save-note" ${dialog.busy || !hasFolder ? "disabled" : ""}>${dialog.busy ? "保存中…" : "保存笔记"}</button>
        </footer>
      </section>
    </div>`;
  document.body.classList.add("has-confirm-dialog");
  window.requestAnimationFrame(() => {
    const input = byId("reviewSaveNewFolderInput");
    if (input && dialog.newFolderName) input.setSelectionRange(input.value.length, input.value.length);
    if (!dialog.newFolderName) host.querySelector('[data-action="choose-review-save-folder"]')?.focus();
  });
}

function openReviewSaveDialog() {
  if (!state.reviewDocument?.markdown) {
    toast("当前没有可保存的研究稿件。", true);
    return;
  }
  if (!state.notebook?.notebook_id) {
    toast("请先选择一个知识库。", true);
    return;
  }
  const remembered = window.localStorage.getItem("scansci.review.save-folder") || "";
  state.reviewSaveDialog = {
    open: true,
    folderPath: remembered,
    newFolderName: "",
    browserDirectoryHandle: null,
    browserFolderLabel: "",
    browserFolderMode: "",
    busy: false,
  };
  renderReviewSaveDialog();
}

async function chooseReviewSaveFolder() {
  const dialog = state.reviewSaveDialog;
  if (!dialog?.open || dialog.busy) return;
  dialog.error = "";
  const nativePicker = window.pywebview?.api?.choose_library_folder;
  if (typeof nativePicker === "function") {
    const path = reviewPickedFolderPath(await nativePicker());
    if (!path) return;
    dialog.folderPath = path;
    dialog.browserDirectoryHandle = null;
    dialog.browserFolderLabel = "";
    dialog.browserFolderMode = "";
    renderReviewSaveDialog();
    return;
  }
  if (typeof window.showDirectoryPicker !== "function") {
    const fallbackInput = byId("reviewSaveFolderInput");
    if (fallbackInput) {
      fallbackInput.click();
      return;
    }
  }
  if (typeof window.showDirectoryPicker === "function") {
    try {
      const handle = await window.showDirectoryPicker({ mode: "readwrite" });
      dialog.folderPath = "";
      dialog.browserDirectoryHandle = handle;
      dialog.browserFolderLabel = handle.name || "已选择文件夹";
      dialog.browserFolderMode = "handle";
      state.reviewSaveDialog.folderPath = "";
      state.reviewSaveDialog.browserDirectoryHandle = handle;
      state.reviewSaveDialog.browserFolderLabel = handle.name || "已选择文件夹";
      renderReviewSaveDialog();
    } catch (error) {
      if (error?.name !== "AbortError") throw error;
    }
    return;
  }
  throw new Error("当前预览不能访问本机文件夹，请在 ScanSci 桌面应用中完成保存。 ");
}

async function writeReviewInBrowserFolder(dialog, newFolderName) {
  let target = dialog.browserDirectoryHandle;
  let folderLabel = dialog.browserFolderLabel || target?.name || "已选择文件夹";
  if (newFolderName) {
    target = await target.getDirectoryHandle(newFolderName, { create: true });
    folderLabel = `${folderLabel} / ${newFolderName}`;
  }
  const fileHandle = await target.getFileHandle(`${safeReviewFileName(state.reviewDocument.title)}.md`, { create: true });
  const writable = await fileHandle.createWritable();
  await writable.write(`${state.reviewDocument.markdown.trim()}\n`);
  await writable.close();
  return folderLabel;
}

async function commitReviewAsNote() {
  const dialog = state.reviewSaveDialog;
  if (!dialog?.open || dialog.busy) return;
  const newFolderName = reviewSaveFolderName(byId("reviewSaveNewFolderInput")?.value || dialog.newFolderName);
  dialog.newFolderName = newFolderName;
  const browserFolder = dialog.browserDirectoryHandle;
  if (!dialog.folderPath && !browserFolder) {
    if (dialog.browserFolderMode === "input" && dialog.browserFolderLabel) {
      downloadReviewDocument();
      closeReviewSaveDialog();
      toast("当前预览无法直接写入本机文件夹，已下载 Markdown；桌面应用可保存到所选位置。", false);
      return;
    }
    toast("请先选择一个保存文件夹。", true);
    return;
  }
  dialog.busy = true;
  renderReviewSaveDialog();
  const notebookId = state.notebook.notebook_id;
  try {
    const payload = {
      title: state.reviewDocument.title || "研究稿件",
      body: state.reviewDocument.markdown,
      note_type: "literature_review",
    };
    let browserFolderLabel = "";
    if (browserFolder) {
      browserFolderLabel = await writeReviewInBrowserFolder(dialog, newFolderName);
      payload.metadata = { storage: "browser-filesystem", folder_label: browserFolderLabel };
    } else {
      payload.folder_path = dialog.folderPath;
      if (newFolderName) payload.new_folder_name = newFolderName;
    }
    const result = await request(`/api/notebooks/${encodeURIComponent(notebookId)}/notes`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (result.notebook) state.notebook = result.notebook;
    state.workspace = await request("/api/workspace");
    state.notebook = (state.workspace.notebooks || []).find((item) => item.notebook_id === notebookId) || state.notebook;
    if (result.destination?.folder_path) window.localStorage.setItem("scansci.review.save-folder", result.destination.folder_path);
    closeReviewSaveDialog();
    const destination = result.destination?.file_path || browserFolderLabel;
    toast(destination ? `笔记已保存到：${destination}` : "笔记已保存到当前知识库");
  } catch (error) {
    dialog.busy = false;
    renderReviewSaveDialog();
    throw error;
  }
}

function trapReviewSaveFocus(event) {
  const dialog = byId("confirmDialogHost")?.querySelector(".review-save-dialog");
  if (!dialog || event.key !== "Tab") return false;
  const controls = [...dialog.querySelectorAll("button:not(:disabled), input:not(:disabled)")];
  if (!controls.length) return false;
  const first = controls[0];
  const last = controls[controls.length - 1];
  if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
    event.preventDefault();
    first.focus();
  }
  return true;
}

function trapConfirmationFocus(event) {
  const dialog = byId("confirmDialogHost")?.querySelector(".confirm-dialog-card");
  if (!dialog || event.key !== "Tab") return false;
  const controls = [...dialog.querySelectorAll("button:not(:disabled)")];
  if (!controls.length) return false;
  const first = controls[0];
  const last = controls[controls.length - 1];
  if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
    event.preventDefault();
    first.focus();
  }
  return true;
}

function updateNoteMarkup(sections = []) {
  return sections.map((section) => {
    const items = (section.items || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    return items ? `<section class="app-update-note-section"><h3>${escapeHtml(section.title || "更新内容")}</h3><ul>${items}</ul></section>` : "";
  }).join("");
}

function formatUpdateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return `检查于 ${date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
}

function renderAppUpdate() {
  const update = state.update || {};
  state.updateNative = state.updateNative || Boolean(window.pywebview?.api?.install_update);
  const root = byId("appUpdate");
  const card = byId("appUpdateCard");
  const trigger = root?.querySelector("[data-action]");
  const status = ["checking", "installing", "restarting"].includes(update.state) ? update.state : (update.available ? "available" : update.state || "current");
  const shouldShowNotice = Boolean(update.available || ["installing", "restarting"].includes(status));
  root.hidden = !shouldShowNotice;
  document.querySelector(".workbench")?.classList.toggle("has-app-update", shouldShowNotice);
  if (!shouldShowNotice) {
    state.updateCardOpen = false;
    card.hidden = true;
    root.classList.remove("is-card-open", "is-card-closed");
    trigger?.setAttribute("aria-expanded", "false");
  } else {
    // Keep the card mounted so CSS can reveal it on hover or keyboard focus.
    // Do not remove is-card-closed here: an explicit close must survive an
    // unrelated update/status render while the pointer is still over the card.
    card.hidden = false;
    if (state.updateCardOpen) root.classList.add("is-card-open");
  }
  root.dataset.state = status;
  const labels = {
    checking: "检查更新",
    installing: "正在更新",
    restarting: "正在重启",
    available: "更新",
    current: `v${update.current_version || "—"}`,
    idle: `v${update.current_version || "—"}`,
    error: "版本信息",
  };
  byId("appUpdateLabel").textContent = labels[status] || "版本信息";
  byId("appUpdateKicker").textContent = update.channel || "稳定版";
  byId("appUpdateTitle").textContent = update.available
    ? (update.release_title || `ScanSci ${update.latest_version}`)
    : (status === "current" ? "ScanSci 已是最新版" : `ScanSci ${update.current_version || ""}`.trim());
  byId("appUpdateSummary").textContent = update.message || (update.available ? "发现新的 ScanSci 桌面版本。" : "当前已是最新版本。 ");
  byId("appUpdateCurrentVersion").textContent = `v${update.current_version || "—"}`;
  byId("appUpdateLatestVersion").textContent = `v${update.latest_version || update.current_version || "—"}`;
  byId("appUpdateNotes").innerHTML = updateNoteMarkup(update.release_notes || []);
  byId("appUpdateCheckedAt").textContent = formatUpdateTime(update.checked_at);
  byId("appUpdateProgress").hidden = !["installing", "restarting"].includes(status);

  const busy = ["checking", "installing", "restarting"].includes(status);
  trigger.disabled = busy;
  if (update.available && update.can_install) {
    trigger.dataset.action = "install-app-update";
    trigger.setAttribute("aria-label", `更新到 v${update.latest_version || "新版本"}`);
    trigger.setAttribute("title", "下载并安装更新");
  } else {
    trigger.dataset.action = "toggle-app-update";
    trigger.setAttribute("aria-label", "查看更新说明");
    trigger.setAttribute("title", "查看更新说明");
  }

  const primary = byId("appUpdatePrimary");
  primary.disabled = busy;
  if (update.available && update.can_install) {
    primary.dataset.action = "install-app-update";
    primary.textContent = state.updateNative ? "立即更新" : "在桌面端更新";
  } else if (update.available) {
    primary.dataset.action = "check-app-update";
    primary.textContent = "安装包准备中";
    primary.disabled = true;
  } else {
    primary.dataset.action = "check-app-update";
    primary.textContent = status === "checking" ? "检查中" : "再次检查";
  }
}

function toggleAppUpdateCard(force) {
  if (byId("appUpdate")?.hidden) return;
  const root = byId("appUpdate");
  const card = byId("appUpdateCard");
  const trigger = document.querySelector("[data-action='toggle-app-update']");
  state.updateCardOpen = typeof force === "boolean" ? force : !state.updateCardOpen;
  card.hidden = false;
  root.classList.toggle("is-card-open", state.updateCardOpen);
  root.classList.toggle("is-card-closed", !state.updateCardOpen);
  trigger?.setAttribute("aria-expanded", String(state.updateCardOpen));
}

function renderUpdateSurfaces() {
  renderAppUpdate();
  if (state.activeView === "settings" && state.activeSettings === "about" && state.settings) renderSettings();
  if (state.activeView === "extensions") renderExtensions();
}

async function refreshAppUpdate({ quiet = false } = {}) {
  if (window.pywebview?.api?.toggle_browser && !window.pywebview?.api?.install_update) {
    state.update = { state: "idle", available: false, message: "Electron 预览版暂不支持自动更新。" };
    renderUpdateSurfaces();
    return;
  }
  if (!quiet) {
    state.update = { ...state.update, state: "checking", message: "正在检查 ScanSci 更新。" };
    renderUpdateSurfaces();
  }
  try {
    state.update = await request("/api/app/update/check", { method: "POST", body: "{}" });
    renderUpdateSurfaces();
  } catch (error) {
    state.update = { ...state.update, state: "error", message: "暂时无法检查更新", error: error.message };
    renderUpdateSurfaces();
    if (!quiet) toast(error.message, true);
  }
}

async function installAppUpdate() {
  if (!window.pywebview?.api?.install_update) {
    toast("请在 ScanSci 桌面应用中完成更新。", true);
    return;
  }
  state.update = { ...state.update, state: "installing", message: "正在下载并校验更新包，请不要关闭 ScanSci。" };
  renderUpdateSurfaces();
  try {
    state.update = await window.pywebview.api.install_update();
    renderUpdateSurfaces();
  } catch (error) {
    state.update = { ...state.update, state: "error", message: "更新没有安装", error: error.message };
    renderUpdateSurfaces();
    toast(error.message || "更新失败", true);
  }
}

async function restoreSessionStats(fallbackStats = null) {
  if (fallbackStats) updateSessionStats(fallbackStats);
  const sessionId = String(state.sessionId || "").trim();
  if (!sessionId) return;
  try {
    const payload = await request(`/api/chat/stats?session_id=${encodeURIComponent(sessionId)}`);
    if (payload?.stats && typeof payload.stats === "object") updateSessionStats(payload.stats);
  } catch (_error) {
    // A stale session registry should not prevent the rest of the workspace
    // from loading; the next direct-chat response will refresh these stats.
  }
}

function bootstrapWorkspaceFallback() {
  return {
    workspace_path: "",
    workspace_directory: "",
    notebooks: [],
    counts: { notebooks: 0, sources: 0, notes: 0, layers: 0 },
  };
}

// The browser preview is intentionally served without the Python API. Keep a
// credential-free copy of the provider directory here so a failed /api/settings
// request does not make the Model Services page look as if its providers were
// deleted. The backend remains the source of truth once it is reachable.
const ZHIPU_STANDARD_BASE_URL = "https://open.bigmodel.cn/api/paas/v4";
const ZHIPU_CODING_PLAN_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4";
const JEV_DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone";

const BOOTSTRAP_PROVIDER_DEFINITIONS = [
  { id: "scansci-managed", name: "ScanSci", category: "ScanSci", baseUrl: "https://scansci-glm-gateway.932196440.workers.dev/v1", modelId: "glm-4.7-flash", modelName: "GLM-4.7 Flash", authMode: "managed", modelListing: false },
  { id: "jev", name: "JEV", logo: "typesafe", category: "Pi 决策层", baseUrl: JEV_DEFAULT_ENDPOINT, apiSurface: "jev_systemone", summary: "JEV 作为 Pi 的结构化决策层，按需参与工具与 Skill 流程。", modelId: "jev-decision-layer", modelName: "JEV Decision Layer", modelListing: false, capabilities: ["reasoning", "tool"] },
  { id: "openai", name: "OpenAI", category: "国际模型", baseUrl: "https://api.openai.com/v1", modelId: "gpt-5.2", modelName: "GPT-5.2" },
  { id: "anthropic", name: "Anthropic", category: "国际模型", kind: "anthropic-compatible", baseUrl: "https://api.anthropic.com/v1", modelId: "claude-sonnet-4-6", modelName: "Claude Sonnet 4.6" },
  { id: "gemini", name: "Google Gemini", category: "国际模型", baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai", modelId: "gemini-3.1-pro-preview", modelName: "Gemini 3.1 Pro" },
  { id: "vertex-ai", name: "Google Vertex AI", category: "国际模型", modelId: "gemini-3.1-pro-preview", modelName: "Gemini 3.1 Pro" },
  { id: "openrouter", name: "OpenRouter", category: "模型聚合", baseUrl: "https://openrouter.ai/api/v1", modelId: "openai/gpt-5.2", modelName: "OpenAI GPT-5.2" },
  { id: "l0veyou", name: "l0veyou", category: "模型聚合", baseUrl: "https://l0veyou.com/v1", modelId: "openai/gpt-6-luna", modelName: "GPT-6 Luna", capabilities: ["reasoning"] },
  { id: "opencode-go", name: "OpenCode Go", category: "订阅模型", baseUrl: "https://opencode.ai/zen/go/v1", apiSurface: "auto", modelId: "glm-5.3-flash", modelName: "GLM-5.3 Flash" },
  { id: "nvidia", name: "NVIDIA NIM", category: "国际模型", baseUrl: "https://integrate.api.nvidia.com/v1", modelId: "meta/llama-3.3-70b-instruct", modelName: "Llama 3.3 70B Instruct" },
  { id: "deepseek", name: "DeepSeek", category: "国内直连", baseUrl: "https://api.deepseek.com", modelId: "deepseek-v4-flash", modelName: "DeepSeek V4 Flash" },
  { id: "dashscope", name: "阿里云百炼", category: "国内直连", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", modelId: "qwen-plus", modelName: "Qwen Plus" },
  { id: "zai", name: "Z.ai", category: "国内直连", baseUrl: "https://api.z.ai/api/paas/v4", modelId: "glm-4.7", modelName: "GLM-4.7" },
  { id: "zhipu", name: "智谱开放平台", category: "国内直连", baseUrl: ZHIPU_STANDARD_BASE_URL, standardBaseUrl: ZHIPU_STANDARD_BASE_URL, codingPlanBaseUrl: ZHIPU_CODING_PLAN_BASE_URL, modelId: "glm-4.7-flash", modelName: "GLM-4.7 Flash" },
  { id: "paratera", name: "并行智算云（Paratera）", category: "国内直连", baseUrl: "https://llmapi.paratera.com/v1", modelId: "WanX2.1-T2I-Plus", modelName: "WanX2.1-T2I-Plus", modelListing: false, capabilities: ["image"] },
  { id: "moonshot", name: "Kimi", category: "国内直连", baseUrl: "https://api.moonshot.ai/v1", modelId: "kimi-k2.5", modelName: "Kimi K2.5" },
  { id: "minimax", name: "MiniMax", category: "国内直连", baseUrl: "https://api.minimaxi.com/v1", modelId: "MiniMax-M2.7", modelName: "MiniMax M2.7" },
  { id: "xiaomi-mimo", name: "Xiaomi MiMo", category: "国内直连", modelId: "mimo-v2-flash", modelName: "MiMo V2 Flash" },
  { id: "siliconflow", name: "硅基流动", category: "云端推理", baseUrl: "https://api.siliconflow.cn/v1", modelId: "deepseek-ai/DeepSeek-V3", modelName: "DeepSeek V3" },
  { id: "modelscope", name: "ModelScope", category: "云端推理", baseUrl: "https://api-inference.modelscope.cn/v1", modelId: "Qwen/Qwen2.5-72B-Instruct", modelName: "Qwen 2.5 72B" },
  { id: "ppio", name: "PPIO Cloud", category: "云端推理", modelId: "deepseek-ai/DeepSeek-V3", modelName: "DeepSeek V3" },
  { id: "volcengine", name: "火山引擎", category: "云端推理", baseUrl: "https://ark.cn-beijing.volces.com/api/v3", modelId: "doubao-seed-1-6-thinking", modelName: "Doubao Seed Thinking" },
  { id: "huawei-cloud", name: "华为云", category: "云端推理", modelId: "DeepSeek-R1", modelName: "DeepSeek R1" },
  { id: "infinigence", name: "无问芯穹", category: "云端推理", modelId: "Qwen2.5-72B-Instruct", modelName: "Qwen 2.5 72B" },
  { id: "qiniu-ai", name: "七牛云 AI 推理", category: "云端推理", modelId: "DeepSeek-V3", modelName: "DeepSeek V3" },
  { id: "modal", name: "Modal", category: "云端推理", modelId: "zai-org/GLM-5.1-FP8", modelName: "GLM-5.1-FP8" },
  { id: "new-api", name: "NewAPI", category: "模型聚合", modelId: "gpt-5.2", modelName: "GPT-5.2" },
  { id: "one-api", name: "OneAPI", category: "模型聚合", modelId: "gpt-5.2", modelName: "GPT-5.2" },
  { id: "aihubmix", name: "AiHubMix", category: "模型聚合", modelId: "gpt-5.2", modelName: "GPT-5.2" },
  { id: "ocoolai", name: "ocoolAI", category: "模型聚合", modelId: "gpt-5.2", modelName: "GPT-5.2" },
  { id: "alaya", name: "Alaya NeW", category: "模型聚合", modelId: "deepseek-chat", modelName: "DeepSeek Chat" },
  { id: "dmxapi", name: "DMXAPI", category: "模型聚合", modelId: "gemini-3.1-pro-preview", modelName: "Gemini 3.1 Pro" },
  { id: "aionly", name: "唯一AI (AiOnly)", category: "模型聚合", modelId: "deepseek-chat", modelName: "DeepSeek Chat" },
  { id: "burncloud", name: "BurnCloud", category: "模型聚合", modelId: "gpt-5.2", modelName: "GPT-5.2" },
  { id: "cherryai", name: "CherryAI", category: "Cherry 生态", modelId: "cherry-model", modelName: "Cherry 模型", authMode: "account_or_key" },
  { id: "cherryin", name: "CherryIN", category: "Cherry 生态", modelId: "gpt-5.2", modelName: "GPT-5.2" },
  { id: "github-copilot", name: "GitHub Copilot", category: "Cherry 生态", modelId: "gpt-5.2", modelName: "GPT-5.2", authMode: "account_or_token" },
  { id: "wuwen", name: "无问", category: "Cherry 生态", modelId: "deepseek-r1", modelName: "DeepSeek R1" },
];

function bootstrapProviderCatalogFallback() {
  return BOOTSTRAP_PROVIDER_DEFINITIONS.map((definition) => {
    const managed = definition.id === "scansci-managed";
    const modelId = definition.modelId || "chat-model";
    return {
      id: definition.id,
      name: definition.name,
      logo: definition.logo || definition.id,
      kind: definition.kind || "openai-compatible",
      base_url: definition.baseUrl || "",
      ...(definition.standardBaseUrl ? {
        standard_base_url: definition.standardBaseUrl,
        coding_plan_base_url: definition.codingPlanBaseUrl || "",
        coding_plan_enabled: Boolean(definition.codingPlanEnabled),
      } : {}),
      api_surface: definition.apiSurface || "chat_completions",
      responses_enabled: false,
      enabled: managed,
      category: definition.category || "自定义提供商",
      summary: managed ? "ScanSci 托管模型，无需配置 API 密钥。" : "在此配置 API 地址与密钥。",
      auth_mode: definition.authMode || (managed ? "managed" : "key"),
      model_listing: definition.modelListing !== false,
      api_key_configured: managed,
      models: [{
        id: modelId,
        name: definition.modelName || "通用对话模型",
        group: definition.name,
        context_window: managed ? "200K" : "",
        capabilities: definition.capabilities || ["reasoning", "tool", "coding"],
      }],
    };
  });
}

function bootstrapSettingsFallback() {
  // The desktop must still expose the built-in model and credential-free
  // provider catalog when an unrelated local database request fails during
  // first launch. The backend remains the source of truth and will replace
  // this snapshot as soon as /api/settings responds.
  return {
    schema_version: 2,
    active_model: { provider_id: "scansci-managed", model_id: "glm-4.7-flash" },
    providers: bootstrapProviderCatalogFallback(),
    local_models: [{
      id: "builtin-evidence",
      name: "离线基础检索",
      runtime: "builtin",
      model_id: "local-hash-v1 / local-lexical-v1",
      enabled: true,
      capabilities: ["embedding", "reranking"],
    }],
    model_roles: {
      reasoning: "provider:scansci-managed:glm-4.7-flash",
      writing: "provider:scansci-managed:glm-4.7-flash",
      retrieval: "local:builtin-evidence",
      embedding: "auto",
      reranking: "auto",
      vision: "",
      audio: "",
      image: "auto",
      slides: "provider:scansci-managed:glm-4.7-flash",
    },
    document_processing: {
      ocr: { provider: "tesseract", base_url: "", languages: ["zh", "en"], enabled: true, api_key_configured: false },
      mineru: { provider: "mineru", base_url: "https://mineru.net", enabled: false, api_key_configured: false },
    },
    onboarding: { welcome_dismissed: false, resource_setup_completed: false, data_setup_completed: false },
    appearance: { locale: "zh-CN", theme: "system", accent: "jade", font_scale: "medium" },
    general: {
      conversation: {
        send_shortcut: "enter",
        completion_notifications: true,
        agent_completion_notifications: true,
        subagent_completion_notifications: false,
      },
      directories: { default_workspace: "", conversation_workspace: "" },
      updates: { auto_check: true },
    },
    skills: [],
    mcp_servers: [],
    plugins: [],
  };
}

function bootstrapPresetsFallback() {
  return { providers: bootstrapProviderCatalogFallback(), local_models: [] };
}

function mergeProviderCatalogIntoSettings(settings, presets) {
  const base = settings && typeof settings === "object" ? settings : bootstrapSettingsFallback();
  const currentProviders = Array.isArray(base.providers) ? base.providers : [];
  const presetProviders = Array.isArray(presets?.providers) ? presets.providers : [];
  const knownIds = new Set(currentProviders.map((provider) => String(provider?.id || "")).filter(Boolean));
  const missingProviders = presetProviders
    .filter((preset) => preset && preset.id && !knownIds.has(String(preset.id)))
    .map((preset) => ({
      ...preset,
      enabled: String(preset.id) === "scansci-managed",
      api_key_configured: String(preset.id) === "scansci-managed",
      models: Array.isArray(preset.models) ? preset.models.map((model) => ({ ...model })) : [],
    }));
  if (!missingProviders.length) return base;
  const mergedProviders = [...currentProviders];
  for (const missingProvider of missingProviders) {
    if (missingProvider.id === "jev") {
      const managedIndex = mergedProviders.findIndex((item) => item.id === "scansci-managed");
      mergedProviders.splice(managedIndex >= 0 ? managedIndex + 1 : mergedProviders.length, 0, missingProvider);
    } else {
      mergedProviders.push(missingProvider);
    }
  }
  return { ...base, providers: mergedProviders };
}

async function initialize() {
  const bootstrapWarnings = [];
  const safeBootstrapRequest = async (path, fallback, label) => {
    try {
      return await request(path);
    } catch (error) {
      bootstrapWarnings.push(`${label}: ${error.message}`);
      return typeof fallback === "function" ? fallback() : fallback;
    }
  };

  try {
    // Only the three records needed to render a usable shell are allowed to
    // gate the first paint. A failed history, market, or diagnostics endpoint
    // must never hide settings and the built-in conversation model.
    const [workspace, settings, presets] = await Promise.all([
      safeBootstrapRequest("/api/workspace", bootstrapWorkspaceFallback, "工作区"),
      safeBootstrapRequest("/api/settings", bootstrapSettingsFallback, "设置"),
      safeBootstrapRequest("/api/settings/presets", bootstrapPresetsFallback, "设置预设"),
    ]);
    state.workspace = workspace || bootstrapWorkspaceFallback();
    const rememberedNotebookId = window.localStorage.getItem("scansci.knowledge.scope") || "";
    const rememberedKnowledgeIds = (() => {
      try {
        const values = JSON.parse(window.localStorage.getItem("scansci.knowledge.scopes") || "[]");
        return Array.isArray(values) ? values.map(String) : [];
      } catch (_error) {
        return [];
      }
    })();
    const searchableNotebookIds = new Set((state.workspace.notebooks || [])
      .filter(notebookHasSearchableContent)
      .map((item) => String(item.notebook_id)));
    state.knowledgeScopeIds = rememberedKnowledgeIds.filter((id) => searchableNotebookIds.has(id));
    if (!state.knowledgeScopeIds.length && rememberedNotebookId && searchableNotebookIds.has(rememberedNotebookId)) {
      state.knowledgeScopeIds = [rememberedNotebookId];
    }
    state.notebook = (state.workspace.notebooks || []).find((item) => item.notebook_id === rememberedNotebookId)
      || (state.workspace.notebooks || [])[0]
      || null;
    state.presets = presets || bootstrapPresetsFallback();
    // Keep the full provider catalog visible even when the settings request
    // temporarily falls back to the minimal built-in ScanSci snapshot. The
    // catalog contains no credentials; user-specific values still come from
    // the successful /api/settings response and remain authoritative.
    state.settings = mergeProviderCatalogIntoSettings(settings, state.presets);
    state.autoCheckUpdates = state.settings?.general?.updates?.auto_check !== false;
    state.selectedProviderId = state.settings.active_model?.provider_id || state.settings.providers?.[0]?.id || "";
    applyAppearancePreferences();
    const preview = new URLSearchParams(window.location.search).get("preview");
    const previewSettings = {
      settings: "general",
      "general-settings": "general",
      defaults: "defaults",
      "knowledge-settings": "knowledge-preview",
      models: "models",
      "local-models": "local-models",
      runtime: "runtime",
      about: "about",
      "software-update": "about",
      archive: "archive",
      storage: "storage",
    }[preview];
    state.onboardingOpen = !Boolean(state.settings?.onboarding?.welcome_dismissed) && !previewSettings;
    if (state.onboardingOpen) {
      state.onboardingMode = "welcome";
      state.resourceGuideStep = 0;
    }
    renderWorkspace();
    renderResourceOnboarding();
    if (state.onboardingOpen && state.onboardingMode === "welcome") {
      window.setTimeout(() => byId("homeQuestionInput")?.focus({ preventScroll: true }), 0);
    }
    if (state.autoCheckUpdates) void refreshAppUpdate({ quiet: true });

    const [capabilities, runsPayload, directHistoryPayload, slideTemplatesPayload, localInstalled, localInstall, localRuntime, runtimeComponents, ollamaStatus, modelHealthPayload, skillsPayload] = await Promise.all([
      request("/api/capabilities").catch(() => ({})),
      request("/api/runs?view=all&limit=200").catch(() => ({ runs: [] })),
      request(`/api/chat/history?view=${state.historyView === "archived" ? "archived" : "active"}&limit=200`).catch(() => ({ conversations: [] })),
      request("/api/slides/templates").catch(() => ({ available: false, templates: [] })),
      request("/api/local-models/installed").catch(() => ({ models: [] })),
      request("/api/local-models/install-status").catch(() => ({ jobs: [], active: null })),
      request("/api/local-runtime").catch(() => ({ installed: false, install_available: false, mode: "missing" })),
      request("/api/runtime-components").catch(() => ({ components: {} })),
      request("/api/ollama/status").catch(() => ({ reachable: false, model_ready: false, model_id: "minicpm-v4.6", error: "" })),
      request("/api/model-health").catch(() => ({ checked_at: "", providers: {}, models: {} })),
      request("/api/skills").catch(() => ({ skills: [], library_path: "" })),
    ]);
    state.capabilities = capabilities;
    state.runs = runsPayload.runs || [];
    state.directConversations = directHistoryPayload.conversations || [];
    state.slideTemplates = slideTemplatesPayload.templates || [];
    state.slideTemplatesPlugin = slideTemplatesPayload.plugin || {};
    state.localModelMarket = { installed: localInstalled.models || [], catalog: [], source: "", query: "", loading: false, loaded: false };
    state.localModelInstall = localInstall || { jobs: [], active: null };
    state.localRuntime = { ...(localRuntime || { installed: false, install_available: false, mode: "missing" }), channels: null };
    state.runtimeComponents = { ...state.runtimeComponents, ...(runtimeComponents?.components || {}) };
    state.ollama = ollamaStatus || state.ollama;
    state.modelHealth = modelHealthPayload || state.modelHealth;
    applyModelSelectionCatalog(state.modelHealth);
    state.extensions.skills = skillsPayload.skills || [];
    state.extensions.libraryPath = skillsPayload.library_path || "";
    state.slideTemplatesAvailable = Boolean(slideTemplatesPayload.available && state.slideTemplates.length);
    const legacySlideTemplateIds = {
      nsfc_purple_semantic: "nsfc_defense",
      nsfc_semantic: "nsfc_defense",
      nsfc_purple: "nsfc_defense",
    };
    if (legacySlideTemplateIds[state.selectedSlideTemplateId]) {
      state.selectedSlideTemplateId = legacySlideTemplateIds[state.selectedSlideTemplateId];
      window.localStorage.setItem("scansci.slides.template", state.selectedSlideTemplateId);
    }
    if (!state.slideTemplates.some((item) => item.id === state.selectedSlideTemplateId)) {
      state.selectedSlideTemplateId = state.slideTemplates[0]?.id || "";
      if (state.selectedSlideTemplateId) window.localStorage.setItem("scansci.slides.template", state.selectedSlideTemplateId);
    }
    state.previewSlideTemplateId = state.selectedSlideTemplateId;
    renderWorkspace();
    renderResourceOnboarding();
    void restoreSessionStats();
    // Startup observes index readiness only. Index creation and model
    // preparation remain attached to imports and explicit retry actions.
    void refreshKnowledgeIndexStatus();
    if (state.localModelInstall?.active) scheduleLocalModelInstallPoll();
    if (["queued", "installing"].includes(state.localRuntime?.install_job?.state)) scheduleLocalRuntimeInstallPoll();
    for (const [componentId, component] of Object.entries(state.runtimeComponents || {})) {
      if (["queued", "installing"].includes(component?.install_job?.state)) scheduleRuntimeComponentInstallPoll(componentId);
    }

    // Restore the last opened task after a reload.  The history list is loaded
    // asynchronously, so relying on the in-memory activeTaskId would leave
    // the conversation looking open while the composer silently fell back to
    // a fresh direct chat.  Persist only the task id; the authoritative run
    // (including messages and artifacts) is fetched from the API below.
    const rememberedTaskId = window.localStorage.getItem("scansci.active.task") || "";
    const rememberedTask = state.runs.find((item) => item.run_id === rememberedTaskId);
    if (rememberedTask && rememberedTask.workflow_type !== "evidence_index") {
      await openTask(rememberedTaskId, { record: false });
    } else {
      if (rememberedTaskId) {
        window.localStorage.removeItem("scansci.active.task");
        window.localStorage.removeItem("scansci.active.session");
      }
      const rememberedDirectId = window.localStorage.getItem("scansci.active.direct") || "";
      if (rememberedDirectId && state.directConversations.some((item) => item.conversation_id === rememberedDirectId)) {
        await openDirectConversation(rememberedDirectId, { record: false });
      }
    }
    if (previewSettings) {
      state.activeSettings = previewSettings;
      setView("settings", { record: false });
    }
    if (bootstrapWarnings.length) {
      toast("部分本地状态暂未读取，默认模型仍可用；稍后可重试。", true);
    }
  } catch (error) {
    const homeSubline = byId("homeSubline");
    if (homeSubline) homeSubline.textContent = `无法加载本地工作区：${error.message}`;
    toast(error.message, true);
  }
}

async function ensureActiveKnowledgeIndex(requestedNotebookId = "", { force = false } = {}) {
  const notebookId = String(requestedNotebookId || state.notebook?.notebook_id || "").trim();
  if (!notebookId) return;
  if (knowledgeSourceKind((state.workspace?.notebooks || []).find((item) => item.notebook_id === notebookId)) === "translation") return;
  try {
    // Retrying the vector index must not reload already prepared model
    // weights. Model retries have their own explicit action below.
    if (force) await refreshLocalAiStatus(notebookId, { prepare: true });
    const result = await request(`/api/notebooks/${encodeURIComponent(notebookId)}/evidence-index`, {
      method: "POST",
      body: JSON.stringify(force ? { force: true } : { auto: true }),
    });
    if (result?.status) {
      state.knowledgeIndexStatuses[notebookId] = result.status;
      syncKnowledgeIndexBadge(notebookId);
    }
    if (result?.local_ai) {
      state.localAiStatuses[notebookId] = result.local_ai;
      syncKnowledgeIndexBadge(notebookId);
    }
    if (result?.model_install) {
      mergeLocalModelInstall(result.model_install);
      if (["queued", "downloading"].includes(result.model_install.state)) {
        scheduleLocalModelInstallPoll();
      }
    }
    if (result?.run) {
      upsertRun(result.run);
      watchRun(result.run.run_id, (run) => {
        const previous = state.knowledgeIndexStatuses[notebookId] || {};
        state.knowledgeIndexStatuses[notebookId] = {
          ...previous,
          state: ["failed", "cancelled"].includes(run.status) ? "failed" : "indexing",
          progress: Math.max(Number(previous.progress || 0), Number(run.progress || 0)),
          error: run.status === "failed" ? runFailureSummary(run) : "",
          run,
        };
        syncKnowledgeIndexBadge(notebookId);
        if (["completed", "failed", "cancelled", "paused"].includes(run.status)) {
          void refreshKnowledgeIndexStatus(notebookId);
        }
      });
    }
  } catch (_error) {
    // Index migration is a background optimization. Retrieval remains usable
    // through lexical search while the next explicit import can retry it.
  }
}

function mergeLocalModelInstall(job) {
  if (!job?.job_id) return;
  const jobs = [...(state.localModelInstall?.jobs || [])].filter((item) => item.job_id !== job.job_id);
  jobs.unshift(job);
  state.localModelInstall = {
    jobs,
    active: ["queued", "downloading", "pausing", "cancelling"].includes(job.state) ? job : jobs.find((item) => ["queued", "downloading", "pausing", "cancelling"].includes(item.state)) || null,
  };
}

function formatDownloadBytes(value) {
  const bytes = Math.max(0, Number(value || 0));
  if (!bytes) return "0 B";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(bytes >= 100 * 1024 ** 2 ? 0 : 1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${Math.round(bytes)} B`;
}

function formatDownloadDuration(value) {
  const seconds = Math.max(0, Math.round(Number(value || 0)));
  if (!seconds) return "";
  if (seconds < 60) return `约 ${seconds} 秒`;
  if (seconds < 3600) return `约 ${Math.ceil(seconds / 60)} 分钟`;
  return `约 ${(seconds / 3600).toFixed(seconds >= 7200 ? 0 : 1)} 小时`;
}

function downloadJobTitle(job, kind = "model") {
  if (kind === "runtime") return "本地运行组件";
  if (kind === "component:node") return "Agent 运行组件";
  if (kind === "component:tectonic") return "LaTeX 排版组件";
  if (job?.job_id === "retrieval-core") return "研究检索组件";
  const models = Array.isArray(job?.models) ? job.models : [];
  const names = {
    [ONBOARDING_EMBEDDING_MODEL]: "嵌入模型",
    [ONBOARDING_RERANKER_MODEL]: "重排模型",
    [ONBOARDING_CHAT_MODEL]: "小型本地对话模型",
    [ONBOARDING_AUDIO_MODEL]: "语音模型",
    [ONBOARDING_VISION_MODEL]: "视觉模型",
  };
  return models.length === 1 ? names[models[0]] || models[0] : models.length ? `${models.length} 个本地模型` : "本地模型";
}

function downloadJobStatus(job) {
  const stateName = String(job?.state || "idle");
  if (job?.stalled) return { label: "进度停滞", tone: "warning", detail: `已 ${formatDownloadDuration(job.last_update_seconds).replace("约 ", "")} 没有收到新数据，可能是网络受阻。` };
  if (stateName === "cancelled") return { label: "已取消", tone: "warning", detail: job?.message || "下载已取消；可以重试并续传已有内容。" };
  if (stateName === "failed") return { label: "下载失败", tone: "error", detail: job?.error || job?.message || "下载没有完成。" };
  if (stateName === "paused") return { label: "已暂停", tone: "warning", detail: job?.message || "恢复时会继续使用已下载的临时文件。" };
  if (stateName === "pausing") return { label: "正在暂停", tone: "warning", detail: job?.message || "正在保存可恢复的下载位置。" };
  if (stateName === "cancelling") return { label: "正在取消", tone: "warning", detail: job?.message || "正在停止下载并保留可重试内容。" };
  if (stateName === "interrupted") return { label: "下载已中断", tone: "warning", detail: job?.message || "重新开始后会续传已有内容。" };
  if (stateName === "ready") return { label: "已完成", tone: "ready", detail: job?.message || "下载和校验已完成。" };
  if (stateName === "queued") return { label: "等待下载", tone: "active", detail: job?.message || "正在连接下载源。" };
  return { label: "正在下载", tone: "active", detail: job?.message || "正在接收文件。" };
}

function downloadJobTelemetry(job) {
  const completed = Number(job?.completed_bytes || 0);
  const total = Number(job?.total_bytes || 0);
  const parts = [];
  if (completed || total) parts.push(total ? `${formatDownloadBytes(completed)} / ${formatDownloadBytes(total)}` : formatDownloadBytes(completed));
  const speed = Number(job?.speed_bytes_per_second || 0);
  if (speed > 0) parts.push(`${formatDownloadBytes(speed)}/s`);
  const eta = formatDownloadDuration(job?.eta_seconds);
  if (eta) parts.push(`剩余 ${eta}`);
  if (job?.current_file) parts.push(compact(pathLeaf(job.current_file), 42));
  return parts.join(" · ");
}

function downloadJobProgressSummary(job) {
  const completed = Array.isArray(job?.completed_models) ? job.completed_models.length : 0;
  const total = Math.max(0, Number(job?.total_models || (Array.isArray(job?.models) ? job.models.length : 0)));
  const stateName = String(job?.state || "");
  if (total > 1) {
    const modelProgress = Math.max(0, Math.min(100, Math.round(Number(job?.current_model_progress || 0) * 100)));
    if (["queued", "downloading", "installing"].includes(stateName) && job?.current_model) {
      return `已完成 ${completed}/${total} 个模型 · 当前模型 ${modelProgress}%`;
    }
    return `已完成 ${completed}/${total} 个模型`;
  }
  return `${Math.max(0, Math.min(100, Math.round(Number(job?.progress || 0) * 100)))}%`;
}

function downloadTaskEntries({ includeReady = false } = {}) {
  const entries = [];
  const runtimeJob = state.localRuntime?.install_job;
  const runtimeReplacementFailed = Boolean(state.localRuntime?.installed)
    && ["failed", "cancelled", "interrupted"].includes(String(runtimeJob?.state || ""));
  if (runtimeJob?.state && runtimeJob.state !== "idle" && !runtimeReplacementFailed && (includeReady || runtimeJob.state !== "ready")) {
    entries.push({ kind: "runtime", job: runtimeJob });
  }
  for (const [componentId, component] of Object.entries(state.runtimeComponents || {})) {
    const job = component?.install_job;
    const replacementFailed = Boolean(component?.installed)
      && ["failed", "cancelled", "interrupted"].includes(String(job?.state || ""));
    if (!job?.state || job.state === "idle" || replacementFailed || (!includeReady && job.state === "ready")) continue;
    entries.push({ kind: `component:${componentId}`, job });
  }
  for (const job of state.localModelInstall?.jobs || []) {
    if (!includeReady && job.state === "ready") continue;
    entries.push({ kind: "model", job });
  }
  return entries.sort((left, right) => Number(right.job.updated_at || 0) - Number(left.job.updated_at || 0));
}

function downloadTaskControls(entry) {
  if (!(entry.kind === "model" || entry.kind === "runtime" || String(entry.kind).startsWith("component:")) || !entry.job?.job_id) return "";
  const job = entry.job;
  const jobId = escapeHtml(job.job_id);
  const kind = escapeHtml(entry.kind);
  const stateName = String(job.state || "");
  if (["queued", "downloading", "installing"].includes(stateName)) {
    return `<div class="download-task-controls"><button type="button" class="download-task-action" data-action="control-download-task" data-download-kind="${kind}" data-download-action="pause" data-job-id="${jobId}">暂停</button><button type="button" class="download-task-action is-quiet" data-action="control-download-task" data-download-kind="${kind}" data-download-action="cancel" data-job-id="${jobId}">取消</button></div>`;
  }
  if (["pausing", "cancelling"].includes(stateName)) return "";
  if (["paused", "interrupted"].includes(stateName)) {
    return `<div class="download-task-controls"><button type="button" class="download-task-action" data-action="control-download-task" data-download-kind="${kind}" data-download-action="resume" data-job-id="${jobId}">恢复</button><button type="button" class="download-task-action is-quiet" data-action="control-download-task" data-download-kind="${kind}" data-download-action="cancel" data-job-id="${jobId}">取消</button></div>`;
  }
  if (["failed", "cancelled"].includes(stateName)) {
    return `<div class="download-task-controls"><button type="button" class="download-task-action" data-action="control-download-task" data-download-kind="${kind}" data-download-action="retry" data-job-id="${jobId}">重试</button></div>`;
  }
  return "";
}

function downloadTaskRow(entry) {
  const job = entry.job || {};
  const status = downloadJobStatus(job);
  const progress = Math.max(0, Math.min(100, Math.round(Number(job.progress || 0) * 100)));
  const telemetry = downloadJobTelemetry(job);
  const progressSummary = downloadJobProgressSummary(job);
  const runtimeKind = entry.kind === "runtime" || String(entry.kind).startsWith("component:");
  return `<article class="download-task-row is-${escapeHtml(status.tone)}"><span class="download-task-icon">${uiIcon(runtimeKind ? "cpu" : "download")}</span><div class="download-task-copy"><header><strong>${escapeHtml(downloadJobTitle(job, entry.kind))}</strong><b>${escapeHtml(status.label)}${progressSummary ? ` · ${escapeHtml(progressSummary)}` : ""}</b></header><p>${escapeHtml(status.detail)}</p>${telemetry ? `<small>${escapeHtml(telemetry)}</small>` : ""}<div class="download-task-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><span class="${progressWidthClass(progress)}"></span></div>${downloadTaskControls(entry)}</div></article>`;
}

function renderDownloadActivity() {
  let host = byId("downloadActivity");
  if (!host) {
    host = document.createElement("aside");
    host.id = "downloadActivity";
    host.className = "download-activity";
    host.setAttribute("aria-live", "polite");
    document.body.appendChild(host);
  }
  const entries = downloadTaskEntries().filter((entry) => ["queued", "downloading", "installing", "pausing", "cancelling", "paused", "failed", "cancelled", "interrupted"].includes(entry.job.state));
  if (!entries.length && !state.downloadStatusError) {
    host.hidden = true;
    host.innerHTML = "";
    return;
  }
  host.hidden = false;
  const primary = entries[0];
  const count = entries.length;
  const primaryRuntime = primary?.kind === "runtime" || String(primary?.kind || "").startsWith("component:");
  host.innerHTML = `<button type="button" class="download-activity-card ${state.downloadStatusError ? "has-connection-error" : ""}" data-action="open-download-center"><span class="download-activity-symbol">${uiIcon(state.downloadStatusError ? "wifi-off" : primaryRuntime ? "cpu" : "download")}</span><span class="download-activity-copy"><strong>${state.downloadStatusError ? "暂时无法读取下载进度" : escapeHtml(downloadJobTitle(primary.job, primary.kind))}</strong><small>${state.downloadStatusError ? "ScanSci 正在重试连接，下载任务不会因此被删除。" : escapeHtml([downloadJobStatus(primary.job).label, downloadJobProgressSummary(primary.job), downloadJobTelemetry(primary.job)].filter(Boolean).join(" · "))}</small></span>${count > 1 ? `<b>${count}</b>` : ""}<span class="download-activity-open">${uiIcon("chevron-right")}</span><span class="download-activity-progress"><i class="${progressWidthClass(Math.round(Number(primary?.job?.progress || 0) * 100))}"></i></span></button>`;
  hydrateIcons(host);
}

function scheduleLocalModelInstallPoll(delay = 900) {
  if (localModelInstallPollTimer) return;
  localModelInstallPollTimer = window.setTimeout(async () => {
    localModelInstallPollTimer = 0;
    try {
      const previousRetrieval = (state.localModelInstall?.jobs || []).find((item) => item.job_id === "retrieval-core");
      const status = await request("/api/local-models/install-status");
      state.downloadStatusError = "";
      state.localModelInstall = status || { jobs: [], active: null };
      const retrieval = (status.jobs || []).find((item) => item.job_id === "retrieval-core");
      if (state.activeView === "settings" && ["local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
      if (state.onboardingOpen) renderResourceOnboarding();
      renderDownloadActivity();
      if (retrieval && retrieval.state !== previousRetrieval?.state) {
        for (const notebook of state.workspace?.notebooks || []) {
          void refreshKnowledgeIndexStatus(notebook.notebook_id);
        }
      }
      if (status.active) {
        scheduleLocalModelInstallPoll(900);
      } else if ((status.jobs || []).some((item) => item.state === "ready")) {
        await refreshLocalModelMarket();
        // A completed download is not necessarily a usable Transformers
        // model: Qwen3.5 still needs the isolated runtime load/generation
        // probe. Keep refreshing until that probe changes the snapshot from
        // pending to ready (or failed), so the derived local provider appears
        // without requiring a manual settings refresh.
        const pendingRuntimeProbe = (state.localModelMarket?.installed || []).some((item) =>
          Boolean(item?.model_files_present) && item?.runtime_probe_state === "pending"
        );
        if (pendingRuntimeProbe) scheduleLocalModelInstallPoll(1500);
      }
    } catch (error) {
      state.downloadStatusError = error?.message || "无法读取模型下载进度";
      renderDownloadActivity();
      scheduleLocalModelInstallPoll(2500);
    }
  }, delay);
}

async function controlDownloadTask(jobId, action, kind = "model") {
  const runtime = kind === "runtime";
  const componentId = String(kind).startsWith("component:") ? String(kind).split(":", 2)[1] : "";
  const endpoint = componentId
    ? "/api/runtime-components/install-control"
    : runtime
      ? "/api/local-runtime/install-control"
      : "/api/local-models/install-control";
  const job = await request(endpoint, {
    method: "POST",
    body: JSON.stringify(componentId ? { component: componentId, action } : runtime ? { action } : { job_id: jobId, action }),
  });
  if (componentId) {
    state.runtimeComponents[componentId] = { ...(state.runtimeComponents?.[componentId] || {}), install_job: job };
  } else if (runtime) {
    state.localRuntime = { ...(state.localRuntime || {}), install_job: job };
  } else {
    mergeLocalModelInstall(job);
  }
  renderDownloadActivity();
  if (state.activeView === "settings" && ["local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
  if (componentId) scheduleRuntimeComponentInstallPoll(componentId, 250);
  else if (runtime) scheduleLocalRuntimeInstallPoll(250);
  else scheduleLocalModelInstallPoll(250);
  toast({ pause: "下载已暂停", resume: "下载已恢复", retry: "已重新开始下载", cancel: "下载已取消" }[action] || "下载任务已更新");
}

function scheduleLocalRuntimeInstallPoll(delay = 700) {
  if (localRuntimeInstallPollTimer) return;
  localRuntimeInstallPollTimer = window.setTimeout(async () => {
    localRuntimeInstallPollTimer = 0;
    try {
      const job = await request("/api/local-runtime/install-status");
      state.downloadStatusError = "";
      state.localRuntime = { ...(state.localRuntime || {}), install_job: job };
      if (state.activeView === "settings" && ["local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
      if (state.onboardingOpen) renderResourceOnboarding();
      renderDownloadActivity();
      if (["queued", "installing", "pausing", "cancelling"].includes(job.state)) {
        scheduleLocalRuntimeInstallPoll(700);
      } else if (job.state === "ready") {
        state.localRuntime = { ...(state.localRuntime || {}), ...(await request("/api/local-runtime")) };
        await refreshLocalModelMarket();
        const pendingResource = state.pendingLocalModelResource;
        state.pendingLocalModelResource = "";
        if (pendingResource) {
          toast("本地运行能力已就绪，正在继续下载模型。");
          await startOnboardingResource(pendingResource);
        } else {
          toast("ScanSci 本地运行能力已就绪；现在可以按需下载模型。");
        }
      } else if (job.state === "failed") {
        toast(job.error || "本地运行能力安装未完成", true);
      }
    } catch (error) {
      state.downloadStatusError = error?.message || "无法读取运行组件安装进度";
      renderDownloadActivity();
      scheduleLocalRuntimeInstallPoll(2200);
    }
  }, delay);
}

function runtimeComponentSnapshot(componentId) {
  const identifier = String(componentId || "").trim().toLowerCase();
  const fallback = identifier === "tectonic"
    ? { id: "tectonic", name: "LaTeX 排版组件", installed: false, install_available: false, manual_install_available: true, mode: "missing" }
    : { id: "node", name: "Agent 运行组件", installed: false, install_available: false, manual_install_available: true, mode: "missing" };
  const component = { ...fallback, ...(state.runtimeComponents?.[identifier] || {}) };
  const job = component.install_job || {};
  const jobState = String(job.state || "idle");
  const active = ["queued", "downloading", "installing", "pausing", "cancelling"].includes(jobState);
  const failed = ["failed", "cancelled", "interrupted"].includes(jobState);
  const paused = jobState === "paused";
  // An interrupted replacement must never hide a previously usable binary.
  // The stable executable remains authoritative until a new version passes
  // validation and atomically replaces active.json.
  const stableInstalled = Boolean(component.installed);
  return {
    ...component,
    id: identifier,
    job,
    active,
    failed,
    paused,
    progress: stableInstalled ? 100 : Math.max(0, Math.min(100, Math.round(Number(job.progress || 0) * 100))),
    state: stableInstalled ? "ready" : active ? jobState : paused ? "paused" : failed ? jobState : "missing",
  };
}

function runtimeComponentDefinition(componentId) {
  return componentId === "tectonic"
    ? {
      icon: "file-text",
      eyebrow: "可选 · 学术排版",
      title: "LaTeX 排版组件",
      description: "用于生成 LaTeX 与高质量 PDF；普通对话、检索和文档预览不依赖它。",
    }
    : {
      icon: "cpu",
      eyebrow: "推荐 · Agent 核心",
      title: "Agent 运行组件",
      description: "为 Pi Agent 提供工具编排、联网检索和持续任务运行环境。已有系统 Node 时会直接复用。",
    };
}

function runtimeComponentModeLabel(component) {
  if (component.mode === "system") return "已复用系统安装";
  if (component.mode === "component") return "已安装，可跨版本复用";
  if (component.mode === "embedded") return "随当前应用提供";
  if (component.mode === "source") return "开发环境可用";
  if (component.mode === "external") return "已复用外部组件";
  return "尚未安装";
}

function runtimeComponentCardMarkup(componentId, { compact = false } = {}) {
  const component = runtimeComponentSnapshot(componentId);
  const definition = runtimeComponentDefinition(component.id);
  const active = ["queued", "downloading", "installing", "pausing", "cancelling"].includes(component.state);
  const statusLabel = component.state === "ready"
    ? runtimeComponentModeLabel(component)
    : active
      ? `${component.job?.message || "正在安装"} ${component.progress}%`
      : component.state === "paused"
        ? "安装已暂停"
        : component.failed
          ? component.state === "interrupted" ? "安装已中断" : "安装未完成"
          : component.id === "tectonic" ? "未安装（可选）" : "尚未就绪";
  const statusHint = component.state === "ready"
    ? (component.executable ? `正在使用 ${component.executable}` : "当前环境已经可以直接使用。")
    : active
      ? (downloadJobTelemetry(component.job) || "正在下载并校验独立组件")
      : component.state === "paused"
        ? "继续安装会复用已经下载的内容。"
        : component.failed
          ? (component.job?.error || component.job?.message || "可以重试，或选择本地组件文件。")
          : component.install_available
            ? "仅在你确认后下载；不会随启动自动安装。"
            : component.install_unavailable_reason || "可直接复用系统安装，也可选择已下载的官方组件 ZIP。";
  const progress = active
    ? `<div class="runtime-component-progress" role="progressbar" aria-label="${escapeHtml(definition.title)}安装进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${component.progress}"><span class="${progressWidthClass(component.progress)}"></span></div>`
    : "";
  let action = "";
  if (component.state === "ready") {
    action = `<span class="runtime-component-ready">${uiIcon("check")} 可用</span>`;
  } else if (active) {
    action = `<span class="runtime-component-running">${uiIcon("loader-circle")}</span>`;
  } else if (component.state === "paused") {
    action = `<button type="button" class="runtime-component-primary" data-action="control-download-task" data-download-kind="component:${escapeHtml(component.id)}" data-download-action="resume" data-job-id="${escapeHtml(component.job?.job_id || `runtime:${component.id}`)}">${uiIcon("download")}继续安装</button>`;
  } else {
    const installButton = component.install_available
      ? `<button type="button" class="runtime-component-primary" data-action="install-runtime-component" data-component-id="${escapeHtml(component.id)}">${uiIcon(component.failed ? "refresh" : "download")}${component.failed ? "重试安装" : "安装组件"}</button>`
      : "";
    const manualButton = component.manual_install_available
      ? `<button type="button" class="runtime-component-secondary" data-action="choose-runtime-component-files" data-component-id="${escapeHtml(component.id)}">选择本地文件</button>`
      : "";
    action = `<div class="runtime-component-actions">${installButton}${manualButton}</div>`;
  }
  return `<article class="runtime-component-card is-${escapeHtml(component.state)} ${compact ? "is-compact" : ""}"><span class="runtime-component-icon">${uiIcon(definition.icon)}</span><div class="runtime-component-copy"><span>${escapeHtml(definition.eyebrow)}</span><strong>${escapeHtml(definition.title)}</strong><p>${escapeHtml(definition.description)}</p><small>${escapeHtml(statusLabel)} · ${escapeHtml(statusHint)}</small>${progress}</div><div class="runtime-component-action">${action}</div></article>`;
}

function runtimeComponentsSettingsMarkup() {
  const actionableComponents = ["node", "tectonic"]
    .map((componentId) => runtimeComponentSnapshot(componentId))
    .filter((component) => component.state !== "ready" && (
      component.active
      || component.failed
      || component.paused
      || component.install_available
      || component.manual_install_available
    ));
  if (!actionableComponents.length) return `<p class="runtime-components-empty">没有需要处理的应用组件</p>`;
  const cards = actionableComponents.map((component) => runtimeComponentCardMarkup(component.id)).join("");
  return `<section class="runtime-components-panel"><header><div><span>APP COMPONENTS</span><h2>应用运行组件</h2><p>只在组件缺失或安装未完成时显示操作；已就绪的内部组件不会占用设置页。</p></div><button type="button" class="quiet-text-button" data-action="refresh-runtime-components">重新检测</button></header><div class="runtime-component-list">${cards}</div></section>`;
}

function scheduleLocalAiStatusPoll(notebookId) {
  const id = String(notebookId || "").trim();
  if (!id) return;
  if (state.localAiStatusPollTimers[id]) window.clearTimeout(state.localAiStatusPollTimers[id]);
  state.localAiStatusPollTimers[id] = window.setTimeout(() => {
    delete state.localAiStatusPollTimers[id];
    void refreshLocalAiStatus(id);
  }, 1500);
}

async function refreshLocalAiStatus(notebookId = state.notebook?.notebook_id || "", { prepare = false, force = false } = {}) {
  const id = String(notebookId || "").trim();
  if (!id) return null;
  if (knowledgeSourceKind((state.workspace?.notebooks || []).find((item) => item.notebook_id === id)) === "translation") return null;
  const previous = state.localAiStatuses[id] || {};
  try {
    const result = await request(`/api/notebooks/${encodeURIComponent(id)}/local-ai-status`, {
      method: prepare ? "POST" : "GET",
      ...(prepare ? { body: JSON.stringify({ quality_profile: "precision", force }) } : {}),
    });
    state.localAiStatuses[id] = result;
    syncKnowledgeIndexBadge(id);
    if (result?.state === "preparing") {
      scheduleLocalAiStatusPoll(id);
      if (previous.state !== "preparing") toast(result.message || "正在加载本地 AI 模型");
    }
    else if (state.localAiStatusPollTimers[id]) {
      window.clearTimeout(state.localAiStatusPollTimers[id]);
      delete state.localAiStatusPollTimers[id];
    }
    if (previous.state === "preparing" && result?.state === "ready") {
      void ensureActiveKnowledgeIndex(id, { force: true });
    }
    return result;
  } catch (_error) {
    return null;
  }
}

async function refreshRuntimeComponents({ render = true } = {}) {
  const payload = await request("/api/runtime-components");
  state.runtimeComponents = { ...state.runtimeComponents, ...(payload?.components || {}) };
  if (render) {
    if (state.activeView === "settings" && ["local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
    if (state.onboardingOpen) renderResourceOnboarding();
    renderDownloadActivity();
  }
  return state.runtimeComponents;
}

function scheduleRuntimeComponentInstallPoll(componentId, delay = 700) {
  const identifier = String(componentId || "").trim().toLowerCase();
  if (!identifier || runtimeComponentInstallPollTimers.has(identifier)) return;
  const timer = window.setTimeout(async () => {
    runtimeComponentInstallPollTimers.delete(identifier);
    try {
      const job = await request(`/api/runtime-components/install-status?component=${encodeURIComponent(identifier)}`);
      state.downloadStatusError = "";
      state.runtimeComponents[identifier] = { ...(state.runtimeComponents?.[identifier] || {}), install_job: job };
      if (state.activeView === "settings" && ["local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
      if (state.onboardingOpen) renderResourceOnboarding();
      renderDownloadActivity();
      if (["queued", "downloading", "installing", "pausing", "cancelling"].includes(String(job.state || ""))) {
        scheduleRuntimeComponentInstallPoll(identifier, 700);
        return;
      }
      const before = runtimeComponentSnapshot(identifier);
      await refreshRuntimeComponents();
      const after = runtimeComponentSnapshot(identifier);
      if (after.state === "ready" && before.state !== "ready") {
        toast(`${runtimeComponentDefinition(identifier).title}已就绪`);
      } else if (!after.installed && ["failed", "cancelled", "interrupted"].includes(String(job.state || ""))) {
        toast(job.error || job.message || `${runtimeComponentDefinition(identifier).title}安装未完成`, true);
      }
    } catch (error) {
      state.downloadStatusError = error?.message || "无法读取运行组件安装进度";
      renderDownloadActivity();
      scheduleRuntimeComponentInstallPoll(identifier, 2200);
    }
  }, delay);
  runtimeComponentInstallPollTimers.set(identifier, timer);
}

async function startRuntimeComponentInstall(componentId) {
  const identifier = String(componentId || "").trim().toLowerCase();
  const current = runtimeComponentSnapshot(identifier);
  if (current.installed) {
    toast(`${runtimeComponentDefinition(identifier).title}已经可用，无需重复下载`);
    return current;
  }
  if (!current.install_available) {
    await chooseRuntimeComponentFiles(identifier);
    return current;
  }
  const job = await request("/api/runtime-components/install", {
    method: "POST",
    body: JSON.stringify({ component: identifier }),
  });
  state.runtimeComponents[identifier] = { ...(state.runtimeComponents?.[identifier] || {}), install_job: job };
  if (state.activeView === "settings" && ["local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
  if (state.onboardingOpen) renderResourceOnboarding();
  renderDownloadActivity();
  scheduleRuntimeComponentInstallPoll(identifier);
  toast(`${runtimeComponentDefinition(identifier).title}已开始安装`);
  return job;
}

async function chooseRuntimeComponentFiles(componentId) {
  const identifier = String(componentId || "").trim().toLowerCase();
  const componentPicker = window.pywebview?.api?.choose_runtime_component_files;
  const legacyPicker = window.pywebview?.api?.choose_local_runtime_files;
  const picker = componentPicker || legacyPicker;
  if (typeof picker !== "function") {
    toast("浏览器预览不能读取本地路径，请在 ScanSci 桌面应用中选择组件文件。", true);
    return null;
  }
  const selected = componentPicker
    ? await componentPicker.call(window.pywebview.api, identifier)
    : await legacyPicker.call(window.pywebview.api);
  const paths = Array.from(selected || []).map(String).filter(Boolean);
  if (!paths.length) return null;
  const job = await request("/api/runtime-components/install-local", {
    method: "POST",
    body: JSON.stringify({ component: identifier, paths }),
  });
  state.runtimeComponents[identifier] = { ...(state.runtimeComponents?.[identifier] || {}), install_job: job };
  if (state.activeView === "settings" && ["local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
  if (state.onboardingOpen) renderResourceOnboarding();
  renderDownloadActivity();
  scheduleRuntimeComponentInstallPoll(identifier);
  toast(`正在校验${runtimeComponentDefinition(identifier).title}`);
  return job;
}

async function refreshKnowledgeIndexStatus(notebookId = state.notebook?.notebook_id || "") {
  const id = String(notebookId || "").trim();
  if (!id) return null;
  if (knowledgeSourceKind((state.workspace?.notebooks || []).find((item) => item.notebook_id === id)) === "translation") return null;
  try {
    const status = await request(`/api/notebooks/${encodeURIComponent(id)}/evidence-index`);
    state.knowledgeIndexStatuses[id] = status;
    syncKnowledgeIndexBadge(id);
    void refreshLocalAiStatus(id);
    return status;
  } catch (_error) {
    return null;
  }
}

function renderWorkspace() {
  const title = state.notebook?.title || state.notebook?.notebook_id || "未打开资料库";
  const sidebarTitle = byId("sidebarNotebookTitle");
  if (sidebarTitle) sidebarTitle.textContent = title;
  const sourceCount = state.notebook?.counts?.sources || 0;
  const homeSubline = byId("homeSubline");
  if (homeSubline) homeSubline.textContent = sourceCount ? "直接描述任务，也可引用本地资料" : "直接描述你想完成的事";
  renderKnowledgeScopeSurfaces();
  renderModelSelectors();
  syncSlideTemplateDocks();
  renderSources();
  renderTasks();
  renderDownloadActivity();
}

function knowledgeKind(notebook = state.notebook) {
  const kind = String(notebook?.metadata?.library_kind || "folder");
  if (kind === "translation") return { key: kind, label: "翻译库", icon: "book" };
  if (kind === "zotero") return { key: kind, label: "Zotero", icon: "library" };
  if (kind === "obsidian") return { key: kind, label: "Obsidian", icon: "book" };
  if (kind === "notion") return { key: kind, label: "Notion", icon: "book" };
  if (kind === "empty") return { key: kind, label: "自建知识库", icon: "file-plus" };
  if (kind === "files") return { key: kind, label: "本地文件", icon: "file-plus" };
  return { key: "folder", label: "本地文件夹", icon: "folder-open" };
}

function knowledgeScopeTitle(notebook = state.notebook) {
  const base = notebook?.title || pathLeaf(notebook?.root_path) || "未命名知识库";
  if (notebook?.notebook_id === state.notebook?.notebook_id && state.knowledgeSubscope?.name) {
    return `${base} / ${state.knowledgeSubscope.name}`;
  }
  return base;
}

function notebookHasSearchableContent(notebook) {
  return Number(notebook?.counts?.sources || 0) > 0;
}

function sanitizeKnowledgeScopeIds(ids = state.knowledgeScopeIds || []) {
  const searchableIds = new Set((state.workspace?.notebooks || [])
    .filter(notebookHasSearchableContent)
    .map((notebook) => String(notebook.notebook_id)));
  return [...new Set((ids || []).map(String))].filter((id) => searchableIds.has(id));
}

function unavailableKnowledgeAction(notebook) {
  switch (knowledgeSourceKind(notebook)) {
    case "translation": return "knowledge-scope-unavailable";
    case "zotero": return "choose-zotero-library";
    case "obsidian": return "choose-obsidian-vault";
    case "notion": return "connect-notion";
    default: return "choose-library-files";
  }
}

function unavailableKnowledgeLabel(notebook) {
  switch (knowledgeSourceKind(notebook)) {
    case "translation": return "暂无译文";
    case "zotero": return "连接 Zotero";
    case "obsidian": return "选择 Vault";
    case "notion": return "连接 Notion";
    default: return "添加资料";
  }
}

function knowledgeLocalBindings(notebook) {
  const metadata = notebook?.metadata || {};
  let bindings = Array.isArray(metadata.local_bindings)
    ? metadata.local_bindings
    : metadata.local_binding && typeof metadata.local_binding === "object"
      ? [metadata.local_binding]
      : [];
  if (!bindings.length && String(metadata.imported_from_folder || "").trim()) {
    bindings = [{
      source_path: String(metadata.imported_from_folder).trim(),
      kind: "folder",
      state: "bound",
      index_state: "ready",
    }];
  }
  return bindings.filter((binding) => binding && typeof binding === "object" && String(binding.source_path || "").trim());
}

function knowledgeLocalBindingKind(binding) {
  const explicit = String(binding?.kind || "").trim().toLowerCase();
  if (explicit === "file") return "file";
  if (explicit === "folder" || explicit === "obsidian") return "folder";
  const sourcePath = String(binding?.source_path || "").trim();
  const leaf = sourcePath.split(/[\\/]/).pop() || "";
  return leaf.includes(".") ? "file" : "folder";
}

function knowledgeLocalBindingKinds(notebook) {
  const kinds = new Set(knowledgeLocalBindings(notebook).map(knowledgeLocalBindingKind));
  return { file: kinds.has("file"), folder: kinds.has("folder") };
}

function knowledgeLocalBindingSummary(notebook) {
  const bindings = knowledgeLocalBindings(notebook);
  if (!bindings.length) return { label: "未链接资料", title: "尚未链接文件或文件夹" };
  const folders = bindings.filter((binding) => knowledgeLocalBindingKind(binding) === "folder").length;
  const files = bindings.filter((binding) => knowledgeLocalBindingKind(binding) === "file").length;
  const counts = [];
  if (folders) counts.push(`${folders} 个文件夹`);
  if (files) counts.push(`${files} 个文件`);
  const names = bindings.map((binding) => pathLeaf(binding.source_path)).filter(Boolean);
  const title = `已链接：${names.join("、")}`;
  const visibleNames = names.slice(0, 2).join("、");
  const more = names.length > 2 ? ` 等 ${names.length} 项` : "";
  return {
    label: `已链接 ${counts.join(" · ") || `${bindings.length} 个来源`}${visibleNames ? `：${visibleNames}${more}` : ""}`,
    title,
  };
}

function knowledgeConnectionStatus(notebook) {
  const metadata = notebook?.metadata || {};
  const kind = knowledgeSourceKind(notebook);
  if (kind === "translation") {
    const files = Number(notebook?.translation_files?.length || 0);
    return { state: files ? "connected" : "empty", label: files ? `已关联 · ${files} 份译文 PDF` : "暂无译文" };
  }
  const count = Number(notebook?.counts?.sources || 0);
  const bindings = knowledgeLocalBindings(notebook);
  const zotero = metadata.zotero || {};
  const notion = metadata.notion || {};
  const connected = kind === "zotero"
    ? Boolean(zotero.connected)
    : kind === "notion"
      ? Boolean(notion.last_sync || Number(notion.page_count || 0) > 0)
      : bindings.length > 0;
  const indexState = String(
    state.knowledgeIndexStatuses?.[String(notebook?.notebook_id || "")]?.state
      || zotero.evidence_index?.status
      || "",
  ).toLowerCase();
  const indexing = ["queued", "running", "indexing", "processing"].includes(indexState);
  if (count > 0) {
    return {
      state: connected ? "connected" : "ready",
      label: `${connected ? "已连接" : "已有资料"} · ${count} 个可检索文档`,
    };
  }
  if (kind === "translation") return { state: "empty", label: "暂无译文" };
  if (connected && indexing) return { state: "indexing", label: "已连接 · 正在建立索引" };
  if (connected && kind === "zotero") {
    return {
      state: "connected",
      label: Number(zotero.pdf_count || 0) > 0
        ? "已连接 Zotero · 暂无可检索全文"
        : "已连接 Zotero · 未找到全文",
    };
  }
  if (connected && kind === "notion") return { state: "connected", label: "已连接 Notion · 暂无可检索内容" };
  if (connected) return { state: "connected", label: "已链接 · 暂无可检索内容" };
  if (bindings.length) return { state: "linked", label: `已链接 ${bindings.length} 项 · 暂无可检索内容` };
  if (String(metadata.library_kind || "") === "empty") return { state: "empty", label: "已建库 · 尚未添加资料" };
  return { state: "disconnected", label: "尚未连接资料" };
}

function knowledgeConnectorSummary(kind) {
  const notebooks = (state.workspace?.notebooks || []).filter((notebook) => knowledgeSourceKind(notebook) === kind);
  if (!notebooks.length) {
    return kind === "notion" && state.settings?.notion_api_token_configured
      ? "密钥已保存 · 尚未同步"
      : "尚未连接";
  }
  const searchableCount = notebooks.reduce((total, notebook) => total + Number(notebook.counts?.sources || 0), 0);
  return searchableCount
    ? `${notebooks.length} 个库 · ${searchableCount} 个可检索`
    : `${notebooks.length} 个库 · 暂无可检索内容`;
}

function selectedKnowledgeNotebooks() {
  const selected = new Set(sanitizeKnowledgeScopeIds());
  return (state.workspace?.notebooks || []).filter((notebook) => selected.has(String(notebook.notebook_id)));
}

function knowledgeScopeDialogSelection() {
  const draft = Array.isArray(state.knowledgeScopeDraftIds)
    ? state.knowledgeScopeDraftIds
    : state.knowledgeScopeIds;
  return sanitizeKnowledgeScopeIds(draft || []);
}

function knowledgeScopeSelectionsEqual(left = [], right = []) {
  const a = new Set((left || []).map(String));
  const b = new Set((right || []).map(String));
  return a.size === b.size && [...a].every((id) => b.has(id));
}

function persistKnowledgeScopes() {
  window.localStorage.setItem("scansci.knowledge.scopes", JSON.stringify(state.knowledgeScopeIds || []));
}

function setKnowledgeScope(notebook, { close = true, toggle = false } = {}) {
  if (!notebook) {
    state.knowledgeScopeIds = [];
    state.knowledgeSubscope = null;
    persistKnowledgeScopes();
  } else {
    if (!notebookHasSearchableContent(notebook)) {
      state.knowledgeScopeIds = sanitizeKnowledgeScopeIds();
      persistKnowledgeScopes();
      renderWorkspace();
      if (state.activeView === "mode" && state.activeMode === "library") renderMode();
      if (byId("knowledgeScopeDialog")?.open) renderKnowledgeScopeDialog();
      return;
    }
    const notebookId = String(notebook.notebook_id);
    const selected = new Set(state.knowledgeScopeIds || []);
    if (toggle && selected.has(notebookId)) selected.delete(notebookId);
    else selected.add(notebookId);
    state.knowledgeScopeIds = [...selected];
    state.notebook = notebook;
    state.knowledgeSubscope = null;
    window.localStorage.setItem("scansci.knowledge.scope", notebookId);
    persistKnowledgeScopes();
  }
  renderWorkspace();
  if (state.activeView === "mode" && state.activeMode === "library") renderMode();
  if (byId("knowledgeScopeDialog")?.open) renderKnowledgeScopeDialog();
  if (close) closeKnowledgeScopeDialog();
}

function removeKnowledgeScope(notebookId) {
  state.knowledgeScopeIds = (state.knowledgeScopeIds || []).filter((id) => id !== notebookId);
  if (Array.isArray(state.knowledgeScopeDraftIds)) {
    state.knowledgeScopeDraftIds = state.knowledgeScopeDraftIds.filter((id) => String(id) !== String(notebookId));
  }
  persistKnowledgeScopes();
  renderKnowledgeScopeSurfaces();
  if (byId("knowledgeScopeDialog")?.open) renderKnowledgeScopeDialog();
}

function setZoteroCollectionScope(notebook, key, name) {
  if (!notebookHasSearchableContent(notebook)) return;
  state.notebook = notebook;
  state.knowledgeSubscope = { type: "zotero-collection", key, name };
  if (!state.knowledgeScopeIds.includes(notebook.notebook_id)) state.knowledgeScopeIds.push(notebook.notebook_id);
  window.localStorage.setItem("scansci.knowledge.scope", notebook.notebook_id);
  persistKnowledgeScopes();
  renderWorkspace();
  renderKnowledgeScopeDialog();
  closeKnowledgeScopeDialog();
}

function zoteroMetadataItems(notebook) {
  return Array.isArray(notebook?.metadata?.zotero?.items)
    ? notebook.metadata.zotero.items.filter((item) => item && typeof item === "object")
    : [];
}

function zoteroMetadataItemForKnowledgeItem(item, notebook) {
  const records = zoteroMetadataItems(notebook);
  if (!records.length) return null;
  const doi = String(item?.doi || "").trim().toLowerCase().replace(/^https?:\/\/(?:dx\.)?doi\.org\//, "");
  if (doi) {
    const byDoi = records.find((record) => String(record.doi || "").trim().toLowerCase().replace(/^https?:\/\/(?:dx\.)?doi\.org\//, "") === doi);
    if (byDoi) return byDoi;
  }
  const title = String(item?.title || "").trim().toLowerCase().replace(/[^\w]+/g, "");
  if (title.length >= 8) {
    const byTitle = records.find((record) => String(record.title || "").trim().toLowerCase().replace(/[^\w]+/g, "") === title);
    if (byTitle) return byTitle;
  }
  const fileName = String(item?.path || item?.source_url || "").replace(/\\/g, "/").split("/").at(-1)?.toLowerCase();
  if (fileName) {
    const byAttachment = records.find((record) => (record.attachments || []).some((attachment) => String(attachment?.path || "").replace(/\\/g, "/").split("/").at(-1)?.toLowerCase() === fileName));
    if (byAttachment) return byAttachment;
  }
  return null;
}

function zoteroTagValues(item) {
  const raw = Array.isArray(item?.tags) ? item.tags : [];
  return raw.map((tag) => typeof tag === "object" ? tag.tag : tag).map((tag) => String(tag || "").trim()).filter(Boolean);
}

function activeKnowledgeScopePayload() {
  const selected = selectedKnowledgeNotebooks();
  const notebook = selected.find((item) => String(item.notebook_id) === String(state.notebook?.notebook_id)) || selected[0];
  if (!notebook) return null;
  const subscope = state.knowledgeSubscope?.type === "zotero-tag" ? null : state.knowledgeSubscope;
  return {
    notebook_id: notebook.notebook_id,
    library_kind: String(notebook.metadata?.library_kind || "folder"),
    ...(subscope || {}),
  };
}

function activeKnowledgeScopePayloads() {
  return selectedKnowledgeNotebooks().map((notebook) => ({
    notebook_id: notebook.notebook_id,
    library_kind: String(notebook.metadata?.library_kind || "folder"),
    ...(String(notebook.notebook_id) === String(state.notebook?.notebook_id)
      && state.knowledgeSubscope?.type !== "zotero-tag"
      && state.knowledgeSubscope
      ? state.knowledgeSubscope
      : {}),
  }));
}

function renderKnowledgeScopeSurfaces() {
  const notebooks = selectedKnowledgeNotebooks();
  // Academic discovery and deep research are standalone web workflows.  A
  // selected knowledge library may remain available for evidence Q&A, but it
  // must never look like a prerequisite or be silently attached to an
  // external search request.
  const usesExternalResearch = ["academic", "deep-research"].includes(state.researchWorkflow);
  ["home", "chat"].forEach((key) => {
    const target = byId(`${key}KnowledgeScope`);
    if (!target) return;
    target.hidden = true;
    target.innerHTML = "";
  });
  document.querySelectorAll("[data-knowledge-menu-status]").forEach((target) => {
    target.textContent = notebooks.length ? `${notebooks.length} 个` : "未选择";
    target.classList.toggle("is-active", Boolean(notebooks.length));
  });
  document.querySelectorAll(".composer-knowledge-button").forEach((button) => {
    button.hidden = usesExternalResearch;
    button.classList.toggle("is-active", Boolean(notebooks.length));
    const scopeLabel = notebooks.length === 1
      ? `@${compact(knowledgeScopeTitle(notebooks[0]), 22)}`
      : notebooks.length > 1 ? `@${notebooks.length} 个知识库` : "知识库";
    const detail = notebooks.length
      ? `本轮将检索：${notebooks.map((notebook) => knowledgeScopeTitle(notebook)).join("、")}`
      : "选择本轮要检索的知识库";
    const label = button.querySelector(".composer-knowledge-button-label");
    if (label) label.textContent = scopeLabel;
    button.setAttribute("aria-label", detail);
    button.setAttribute("title", detail);
  });
  document.querySelectorAll(".composer-external-research-status").forEach((status) => {
    status.hidden = !usesExternalResearch;
  });
}

function openKnowledgeScopeDialog() {
  closeAttachmentMenus();
  state.knowledgeScopeDraftIds = [...sanitizeKnowledgeScopeIds()];
  renderKnowledgeScopeDialog();
  const dialog = byId("knowledgeScopeDialog");
  if (dialog && !dialog.open) dialog.showModal();
}

function closeKnowledgeScopeDialog() {
  // Closing with the X or by opening another setup flow is intentionally a
  // cancel action.  Only the explicit footer action commits the draft.
  state.knowledgeScopeDraftIds = null;
  const dialog = byId("knowledgeScopeDialog");
  if (dialog?.open) dialog.close();
}

function syncKnowledgeScopeDialogFooter() {
  const status = byId("knowledgeScopeDraftStatus");
  if (!status) return;
  const text = status.querySelector("[data-knowledge-scope-status-text]");
  if (!text) return;
  const selected = knowledgeScopeDialogSelection();
  const committed = sanitizeKnowledgeScopeIds();
  text.textContent = knowledgeScopeSelectionsEqual(selected, committed)
    ? selected.length ? `本轮使用 ${selected.length} 个知识库` : "下方显示连接状态；勾选知识库可用于本轮检索"
    : `${selected.length ? `已选择 ${selected.length} 个知识库` : "未选择知识库"} · 点击完成后生效`;
  status.classList.toggle("is-dirty", !knowledgeScopeSelectionsEqual(selected, committed));
}

function syncKnowledgeScopeDialogSelection() {
  const target = byId("knowledgeScopeContent");
  if (!target) return;
  const selected = new Set(knowledgeScopeDialogSelection());
  target.querySelectorAll("[data-knowledge-scope-row]").forEach((row) => {
    const active = selected.has(String(row.dataset.notebookId || ""));
    row.classList.toggle("is-active", active);
    const button = row.querySelector(".knowledge-scope-row-main");
    if (button) button.setAttribute("aria-pressed", String(active));
    const mark = row.querySelector(".knowledge-scope-selected");
    if (mark) mark.hidden = !active;
  });
  syncKnowledgeScopeDialogFooter();
}

function toggleKnowledgeScopeDraft(notebookId) {
  const notebook = (state.workspace?.notebooks || []).find((item) => String(item.notebook_id) === String(notebookId));
  if (!notebook || !notebookHasSearchableContent(notebook)) return;
  const selected = new Set(knowledgeScopeDialogSelection());
  const id = String(notebook.notebook_id);
  if (selected.has(id)) selected.delete(id);
  else selected.add(id);
  state.knowledgeScopeDraftIds = [...selected];
  // This is deliberately DOM-local: no workspace render, index request, or
  // history refresh is needed to acknowledge a checkbox-like interaction.
  syncKnowledgeScopeDialogSelection();
}

function applyKnowledgeScopeSelection() {
  const nextIds = knowledgeScopeDialogSelection();
  const previousActiveId = String(state.notebook?.notebook_id || "");
  state.knowledgeScopeIds = nextIds;
  const nextActive = nextIds.length
    ? (state.workspace?.notebooks || []).find((item) => String(item.notebook_id) === previousActiveId && nextIds.includes(String(item.notebook_id)))
      || (state.workspace?.notebooks || []).find((item) => nextIds.includes(String(item.notebook_id)))
    : null;
  if (nextActive) {
    if (String(nextActive.notebook_id) !== previousActiveId) state.knowledgeSubscope = null;
    state.notebook = nextActive;
    window.localStorage.setItem("scansci.knowledge.scope", String(nextActive.notebook_id));
  } else if (!nextIds.length) {
    state.knowledgeSubscope = null;
    window.localStorage.setItem("scansci.knowledge.scope", "");
  }
  persistKnowledgeScopes();
  state.knowledgeScopeDraftIds = null;
  // One committed update is enough.  The expensive render is kept out of the
  // repeated select/deselect path above.
  renderWorkspace();
  if (state.activeView === "mode" && state.activeMode === "library") renderMode();
  const dialog = byId("knowledgeScopeDialog");
  if (dialog?.open) dialog.close();
  toast(nextIds.length ? `已应用 ${nextIds.length} 个知识库` : "已移除本轮知识库范围");
}

function syncKnowledgeScopeRefreshButton() {
  const button = byId("knowledgeScopeRefresh");
  if (!button) return;
  const refreshing = Boolean(state.knowledgeScopeRefreshing);
  button.disabled = refreshing;
  button.classList.toggle("is-loading", refreshing);
  button.setAttribute("aria-label", refreshing ? "正在刷新资料数量" : "刷新可检索资料数量");
  button.setAttribute("title", refreshing ? "正在刷新资料数量" : "刷新可检索资料数量");
}

async function refreshKnowledgeScopeCounts() {
  if (state.knowledgeScopeRefreshing) return;
  state.knowledgeScopeRefreshing = true;
  syncKnowledgeScopeRefreshButton();
  try {
    const activeNotebookId = String(state.notebook?.notebook_id || "");
    const workspace = await request("/api/workspace");
    const notebooks = workspace?.notebooks || [];
    state.workspace = workspace;
    state.knowledgeScopeIds = sanitizeKnowledgeScopeIds();
    if (Array.isArray(state.knowledgeScopeDraftIds)) {
      state.knowledgeScopeDraftIds = sanitizeKnowledgeScopeIds(state.knowledgeScopeDraftIds);
    }
    state.notebook = notebooks.find((notebook) => String(notebook.notebook_id) === activeNotebookId)
      || notebooks[0]
      || null;
    persistKnowledgeScopes();
    renderWorkspace();
    if (state.activeView === "mode" && state.activeMode === "library") renderMode();
    if (byId("knowledgeScopeDialog")?.open) renderKnowledgeScopeDialog();
    toast("已更新可检索资料数量");
  } catch (error) {
    toast(`无法刷新资料数量：${error.message}`, true);
  } finally {
    state.knowledgeScopeRefreshing = false;
    syncKnowledgeScopeRefreshButton();
  }
}

function renderKnowledgeScopeDialog() {
  const target = byId("knowledgeScopeContent");
  if (!target) return;
  syncKnowledgeScopeRefreshButton();
  const notebooks = state.workspace?.notebooks || [];
  const selected = new Set(knowledgeScopeDialogSelection());
  const rows = notebooks.map((notebook) => {
    const kind = knowledgeKind(notebook);
    const ready = notebookHasSearchableContent(notebook);
    const active = ready && selected.has(String(notebook.notebook_id));
    const status = knowledgeConnectionStatus(notebook);
    const personal = knowledgeSourceKind(notebook) === "personal";
    const action = ready ? "toggle-notebook-scope" : "knowledge-scope-unavailable";
    const selectionMark = active
      ? `<span class="knowledge-scope-selected" aria-label="已加入本轮">${uiIcon("check")}</span>`
      : `<span class="knowledge-scope-selected" aria-label="已加入本轮" hidden>${uiIcon("check")}</span>`;
    const linkedKinds = knowledgeLocalBindingKinds(notebook);
    const resourceButtons = [
      linkedKinds.file ? "" : `<button type="button" data-action="choose-library-files" data-notebook-id="${escapeHtml(notebook.notebook_id)}" aria-label="向 ${escapeHtml(knowledgeScopeTitle(notebook))} 链接文件" title="链接文件">${uiIcon("file-plus")}</button>`,
      linkedKinds.folder ? "" : `<button type="button" data-action="choose-library-folder" data-notebook-id="${escapeHtml(notebook.notebook_id)}" aria-label="向 ${escapeHtml(knowledgeScopeTitle(notebook))} 链接文件夹" title="链接文件夹">${uiIcon("folder-open")}</button>`,
    ].filter(Boolean);
    const resourceActions = personal
      ? resourceButtons.length
        ? `<div class="knowledge-scope-resource-actions">${resourceButtons.join("")}</div>`
        : `<span class="knowledge-scope-resource-status" title="文件与文件夹已连接">已全部连接</span>`
      : "";
    return `<div class="knowledge-scope-row ${active ? "is-active" : ""} ${ready ? "" : "is-unavailable"}" data-knowledge-scope-row data-notebook-id="${escapeHtml(notebook.notebook_id)}"><button type="button" class="knowledge-scope-row-main" data-action="${ready ? "toggle-knowledge-scope-draft" : action}" data-notebook-id="${escapeHtml(notebook.notebook_id)}" aria-label="${escapeHtml(ready ? `选择 ${knowledgeScopeTitle(notebook)}用于本轮检索` : `${knowledgeScopeTitle(notebook)}：${status.label}`)}" aria-pressed="${ready ? String(active) : "false"}" ${ready ? "" : "disabled"}><img src="${knowledgeLogoUrl(kind.key)}" alt="" /><span>${escapeHtml(knowledgeScopeTitle(notebook))}</span><small data-connection-state="${escapeHtml(status.state)}" title="${escapeHtml(status.label)}">${escapeHtml(status.label)}</small>${selectionMark}</button>${resourceActions}</div>`;
  }).join("");
  target.innerHTML = `<section class="knowledge-scope-connect"><button type="button" data-action="create-empty-library"><img src="/knowledge-personal.svg" alt="" /><span><strong>个人知识库</strong><small>新建后链接本地资料</small></span>${uiIcon("plus")}</button><button type="button" data-action="choose-zotero-library" title="${escapeHtml(knowledgeConnectorSummary("zotero"))}"><img src="/zotero-logo.svg" alt="" /><span><strong>Zotero</strong><small>${escapeHtml(knowledgeConnectorSummary("zotero"))}</small></span>${uiIcon("plus")}</button><button type="button" data-action="choose-obsidian-vault" title="${escapeHtml(knowledgeConnectorSummary("obsidian"))}"><img src="/obsidian-logo.svg" alt="" /><span><strong>Obsidian</strong><small>${escapeHtml(knowledgeConnectorSummary("obsidian"))}</small></span>${uiIcon("plus")}</button><button type="button" data-action="connect-notion" title="${escapeHtml(knowledgeConnectorSummary("notion"))}"><img src="/notion-logo.png" alt="" /><span><strong>Notion</strong><small>${escapeHtml(knowledgeConnectorSummary("notion"))}</small></span>${uiIcon("plus")}</button></section><section class="knowledge-scope-list"><header><h3>选择知识库</h3><span>可多选</span></header><p class="knowledge-scope-hint">资料连接状态和本轮检索范围会分别显示。</p>${rows || `<div class="knowledge-scope-empty">先连接资料来源</div>`}</section>`;
  hydrateIcons(target);
  syncKnowledgeScopeDialogFooter();
}

function knowledgeLogoUrl(kind) {
  if (kind === "translation") return "/pdf-document.svg";
  if (kind === "zotero") return "/zotero-logo.svg";
  if (kind === "obsidian") return "/obsidian-logo.svg";
  if (kind === "notion") return "/notion-logo.png";
  return "/knowledge-personal.svg";
}

function activeModel() {
  const active = state.settings?.active_model || {};
  const provider = (state.settings?.providers || []).find((item) => item.id === active.provider_id) || state.settings?.providers?.[0];
  const model = provider?.models?.find((item) => item.id === active.model_id) || provider?.models?.[0];
  return { provider, model };
}

function uniqueModelProviderLabel(primary, secondary, separator = " · ") {
  const first = String(primary || "").trim();
  const second = String(secondary || "").trim();
  if (!first) return second;
  if (!second || first.localeCompare(second, undefined, { sensitivity: "base" }) === 0) return first;
  return `${first}${separator}${second}`;
}

function activeModelLabel() {
  const imageSelection = selectedComposerImageModel();
  if (imageSelection) {
    const provider = state.settings?.providers?.find(p => p.id === imageSelection.provider_id);
    const model = provider?.models?.find(m => m.id === imageSelection.model_id);
    return `${model?.name || imageSelection.model_id} · 生图`;
  }
  const { provider, model } = activeModel();
  if (!provider || !model || !isConversationModel(model)) return "选择模型";
  return model.name;
}

function isConversationModel(model) {
  const capabilities = new Set(model?.capabilities || []);
  if (["audio", "image"].some((item) => capabilities.has(item)) && !["reasoning", "writing", "vision", "tool", "coding"].some((item) => capabilities.has(item))) {
    return false;
  }
  return !capabilities.has("embedding") && !capabilities.has("reranking");
}

function selectedComposerImageModel() {
  const value = String(state.composerImageModel || "");
  const separator = value.indexOf("::");
  return separator > 0 && value.slice(separator + 2)
    ? { provider_id: value.slice(0, separator), model_id: value.slice(separator + 2) }
    : null;
}

function composerModelAvailability(model, provider) {
  if (!model?.capabilities?.includes("image")) return conversationModelAvailability(model, provider);
  const enabled = model.enabled !== false;
  const available = model.available !== false && String(model.readiness || "production") === "production";
  const selectable = enabled && available && isProviderUsable(provider);
  const detail = selectable
    ? "文生图 · 仅使用本条文字描述"
    : !enabled
      ? "该模型已停用，可在模型服务中重新启用。"
      : !available
        ? model.unavailable_reason || "该图像模型当前不可用，请在模型服务中检查状态。"
        : provider?.auth_mode === "key" && !provider.api_key_configured
          ? `${provider.name} 未配置 API 密钥，请在模型服务中填写。`
          : state.modelHealth?.providers?.[provider?.id]?.detail || "图像服务尚未就绪，请在模型服务中检查状态。";
  return {
    visible: Boolean(provider?.enabled), selectable,
    label: selectable ? "" : !enabled ? "已停用" : !available ? "不可用" : "待配置",
    detail,
  };
}

function modelHealthKey(providerId, modelId) {
  return `${String(providerId || "")}::${String(modelId || "")}`;
}

function modelHealthEntry(providerId, modelId) {
  return state.modelHealth?.models?.[modelHealthKey(providerId, modelId)] || null;
}

function applyModelSelectionCatalog(snapshot) {
  const catalog = snapshot?.selection_catalog;
  if (!catalog || !state.settings) return;
  const discovered = Array.isArray(catalog.providers) ? catalog.providers : [];
  const discoveredById = new Map(discovered.map((provider) => [String(provider?.id || ""), provider]));
  const merged = [];
  const seen = new Set();
  for (const provider of (state.settings.providers || [])) {
    const id = String(provider?.id || "");
    const derived = id === "local-huggingface" || id.startsWith("local-runtime-");
    if (derived && discoveredById.has(id)) {
      merged.push(discoveredById.get(id));
      seen.add(id);
    } else if (!derived) {
      merged.push(provider);
      seen.add(id);
    }
  }
  for (const provider of discovered) {
    const id = String(provider?.id || "");
    if (id && !seen.has(id)) merged.push(provider);
  }
  state.settings.providers = merged;
  if (Array.isArray(catalog.local_models)) state.settings.local_models = catalog.local_models;
}

function localModelSnapshotReady(modelId) {
  const wanted = String(modelId || "");
  return (state.localModelMarket?.installed || []).some((item) => (
    String(item?.id || "") === wanted
    && Boolean(item?.ready)
    && item?.runtime_compatible !== false
  ));
}

function isLocalModelProvider(provider) {
  return provider?.kind === "local" || provider?.auth_mode === "local" || String(provider?.id || "").startsWith("local-runtime-");
}

function localModelStatusLabel(status, detail = "") {
  const message = String(detail || "");
  if (/\b1455\b|page\s*file|paging\s*file|\u9875\u9762\u6587\u4ef6|\u865a\u62df\u5185\u5b58/i.test(message)) return "\u865a\u62df\u5185\u5b58\u4e0d\u8db3";
  if (/VRAM|\u663e\u5b58\u4e0d\u8db3|(?:cuda|gpu).{0,64}(?:out of memory|outofmemory|memory allocation)/i.test(message)) return "\u663e\u5b58\u4e0d\u8db3";
  if (/out of memory|memory allocation|\u5185\u5b58\u4e0d\u8db3/i.test(message)) return "\u5185\u5b58\u4e0d\u8db3";
  if (/\u672a\u8fd0\u884c|\u672a\u54cd\u5e94|refused|\u65e0\u6cd5\u8fde\u63a5|timed out|timeout/i.test(message)) return "\u8fd0\u884c\u65f6\u672a\u542f\u52a8";
  return ({
    incompatible: "\u5f53\u524d\u73af\u5883\u4e0d\u517c\u5bb9",
    not_installed: "\u672a\u5b89\u88c5",
    unavailable: "\u6682\u4e0d\u53ef\u7528",
    disabled: "\u5df2\u505c\u7528",
    needs_key: "\u5f85\u914d\u7f6e",
  })[String(status || "")] || "\u6682\u4e0d\u53ef\u7528";
}

function conversationModelAvailability(model, provider = null) {
  if (!isConversationModel(model) || String(model?.readiness || "production") !== "production") {
    return { visible: false, selectable: false, status: "unsupported", label: "", detail: "" };
  }
  const local = isLocalModelProvider(provider);
  const explicitReason = String(model?.unavailable_reason || "").trim();
  if (!provider) {
    const selectable = model?.available !== false;
    return { visible: true, selectable, status: selectable ? "ready" : "unavailable", label: selectable ? "" : localModelStatusLabel("unavailable", explicitReason), detail: explicitReason };
  }
  if (!provider.enabled) {
    return { visible: local, selectable: false, status: "disabled", label: "\u5df2\u505c\u7528", detail: "\u8be5\u672c\u5730\u670d\u52a1\u5df2\u505c\u7528\uff0c\u53ef\u5728\u6a21\u578b\u8bbe\u7f6e\u4e2d\u91cd\u65b0\u542f\u7528\u3002" };
  }
  if (model?.available === false) {
    return { visible: local, selectable: false, status: "incompatible", label: localModelStatusLabel("incompatible", explicitReason), detail: explicitReason || "\u5f53\u524d\u672c\u5730\u8fd0\u884c\u7ec4\u4ef6\u65e0\u6cd5\u52a0\u8f7d\u8fd9\u4e2a\u6a21\u578b\u3002" };
  }
  if (!local) {
    const selectable = isProviderUsable(provider);
    return { visible: selectable, selectable, status: selectable ? "ready" : "needs_key", label: selectable ? "" : "\u5f85\u914d\u7f6e", detail: selectable ? "" : "\u8bf7\u5148\u5b8c\u6210\u670d\u52a1\u5546\u914d\u7f6e\u3002" };
  }
  const health = modelHealthEntry(provider.id, model.id);
  if (health) {
    const status = String(health.status || "unknown");
    const selectable = ["ready", "connected", "configured"].includes(status);
    const detail = String(health.detail || explicitReason || "").trim();
    return { visible: true, selectable, status, label: selectable ? "" : localModelStatusLabel(status, detail), detail };
  }
  // Keep the initial render migration-safe if an older embedded page loads
  // before the health endpoint responds. Once a snapshot exists, local models
  // without a ready cache entry stay visible with an honest pending state.
  const selectable = provider.id === "local-evidence" || (!state.modelHealth?.checked_at && localModelSnapshotReady(model.id));
  return {
    visible: true,
    selectable,
    status: selectable ? "ready" : "unavailable",
    label: selectable ? "" : "\u72b6\u6001\u5f85\u786e\u8ba4",
    detail: selectable ? "" : "\u5c1a\u672a\u53d6\u5f97\u672c\u5730\u8fd0\u884c\u72b6\u6001\uff0c\u53ef\u5237\u65b0\u68c0\u6d4b\u3002",
  };
}

function isSelectableConversationModel(model, provider = null) {
  return conversationModelAvailability(model, provider).selectable;
}

function parseTokenCapacity(value) {
  if (typeof value === "number" && Number.isFinite(value)) return Math.max(0, Math.round(value));
  const text = String(value || "").trim().toLowerCase().replace(/,/g, "");
  const match = text.match(/^(\d+(?:\.\d+)?)\s*(k|m|b|涓|万)?$/i);
  if (!match) return 0;
  const base = Number(match[1]);
  if (!Number.isFinite(base)) return 0;
  const multiplier = ({ k: 1e3, m: 1e6, b: 1e9, "涓": 1e4, "万": 1e4 })[match[2] || ""] || 1;
  return Math.round(base * multiplier);
}

function modelContextWindow() {
  const model = activeModel().model || {};
  return parseTokenCapacity(model.context_window || model.contextWindow || model.context_length);
}

function modelContextWindowFor(providerId, modelId) {
  const providers = [...(state.settings?.providers || []), ...(state.presets?.providers || [])];
  const provider = providers.find((item) => String(item.id) === String(providerId || ""));
  const model = provider?.models?.find((item) => String(item.id) === String(modelId || ""));
  return parseTokenCapacity(model?.context_window || model?.contextWindow || model?.context_length) || modelContextWindow();
}

function normalizeSessionStats(raw) {
  if (!raw || typeof raw !== "object") return null;
  const sourceTokens = raw.tokens && typeof raw.tokens === "object" ? raw.tokens : raw;
  const contextSource = raw.contextUsage || raw.context_usage || {};
  const tokens = {
    input: Number(sourceTokens.input ?? sourceTokens.input_tokens ?? sourceTokens.prompt_tokens ?? 0) || 0,
    output: Number(sourceTokens.output ?? sourceTokens.output_tokens ?? sourceTokens.completion_tokens ?? 0) || 0,
    cacheRead: Number(sourceTokens.cacheRead ?? sourceTokens.cache_read ?? sourceTokens.cache_read_tokens ?? 0) || 0,
    cacheWrite: Number(sourceTokens.cacheWrite ?? sourceTokens.cache_write ?? sourceTokens.cache_write_tokens ?? 0) || 0,
    total: Number(sourceTokens.total ?? sourceTokens.total_tokens ?? 0) || 0,
  };
  const categoryTotal = tokens.input + tokens.output + tokens.cacheRead + tokens.cacheWrite;
  if (!tokens.total) tokens.total = categoryTotal;
  const contextWindow = parseTokenCapacity(contextSource.contextWindow ?? contextSource.context_window) || modelContextWindow();
  const contextTokens = Number(contextSource.tokens ?? contextSource.token_count ?? 0) || 0;
  const percent = Math.max(0, Math.min(100, Number(contextSource.percent) || (contextWindow ? contextTokens / contextWindow * 100 : 0)));
  const rawBreakdown = raw.contextBreakdown && typeof raw.contextBreakdown === "object" ? raw.contextBreakdown : null;
  const contextBreakdown = rawBreakdown ? {
    message: Number(rawBreakdown.message || 0) || 0,
    systemTools: Number(rawBreakdown.systemTools || 0) || 0,
    mcpTools: Number(rawBreakdown.mcpTools || 0) || 0,
    skills: Number(rawBreakdown.skills || 0) || 0,
    systemPrompt: Number(rawBreakdown.systemPrompt || 0) || 0,
    other: Number(rawBreakdown.other || 0) || 0,
    total: Number(rawBreakdown.total || contextTokens) || contextTokens,
  } : null;
  return {
    ...raw,
    tokens,
    contextUsage: { ...contextSource, tokens: contextTokens, contextWindow, percent },
    contextBreakdown,
    toolInventory: raw.toolInventory && typeof raw.toolInventory === "object" ? raw.toolInventory : null,
    skillInventory: raw.skillInventory && typeof raw.skillInventory === "object" ? raw.skillInventory : null,
  };
}

function updateSessionStats(raw) {
  const normalized = normalizeSessionStats(raw);
  if (!normalized) return;
  state.sessionStats = normalized;
  state.contextUsagePercent = Math.round(normalized.contextUsage.percent || 0);
  state.sessionTokens = Number(normalized.tokens.total || 0);
  renderContextUsage();
}

function formatTokenShort(value) {
  const count = Number(value || 0);
  if (!Number.isFinite(count) || count <= 0) return "0";
  if (count >= 1e8) return `${(count / 1e8).toFixed(count >= 1e9 ? 0 : 1).replace(/\.0$/, "")}亿`;
  if (count >= 1e4) return `${(count / 1e4).toFixed(count >= 1e5 ? 0 : 1).replace(/\.0$/, "")}万`;
  if (count >= 1e3) return `${(count / 1e3).toFixed(1).replace(/\.0$/, "")}K`;
  return formatTokenCount(count);
}

function contextUsagePopoverMarkup(stats) {
  const usage = stats?.contextUsage || {};
  const tokens = stats?.tokens || {};
  const contextTokens = Number(usage.tokens || 0);
  const contextWindow = Number(usage.contextWindow || modelContextWindow() || 0);
  const percent = Math.max(0, Math.min(100, Number(usage.percent || 0)));
  const breakdown = stats?.contextBreakdown && typeof stats.contextBreakdown === "object"
    ? stats.contextBreakdown
    : null;
  const legacyRows = [
    ["消息输入", Number(tokens.input || 0), "is-input"],
    ["模型输出", Number(tokens.output || 0), "is-output"],
    ["缓存读取", Number(tokens.cacheRead || 0), "is-cache-read"],
    ["缓存写入", Number(tokens.cacheWrite || 0), "is-cache-write"],
  ];
  const rows = breakdown
    ? [
      ["消息", Number(breakdown.message || 0), "is-input"],
      ["系统工具", Number(breakdown.systemTools || 0), "is-output"],
      ["MCP 工具", Number(breakdown.mcpTools || 0), "is-cache-read"],
      ["技能", Number(breakdown.skills || 0), "is-cache-write"],
      ["系统提示词", Number(breakdown.systemPrompt || 0), "is-system-prompt"],
      ["其他", Number(breakdown.other || 0), "is-other"],
    ]
    : legacyRows;
  const breakdownTotal = Number(breakdown?.total || 0);
  const total = Math.max(0, Number(contextTokens || breakdownTotal || tokens.total || rows.reduce((sum, row) => sum + row[1], 0) || 0));
  if (!breakdown && total > rows.reduce((sum, row) => sum + row[1], 0)) {
    rows.push(["其他", total - rows.reduce((sum, row) => sum + row[1], 0), "is-other"]);
  }
  const rowsMarkup = rows.map(([label, value, colorClass]) => {
    const share = total > 0 ? (value / total) * 100 : 0;
    return `<div class="context-usage-row ${colorClass}"><i></i><span>${label}</span><strong>${share >= 0.1 ? `${share.toFixed(1)}%` : "0%"}</strong></div>`;
  }).join("");
  // Match Pi's own cache report: the prompt consists of uncached input,
  // cache reads, and cache writes.
  const cacheBase = Number(tokens.input || 0) + Number(tokens.cacheRead || 0) + Number(tokens.cacheWrite || 0);
  const cacheHit = cacheBase > 0 ? Math.round(Number(tokens.cacheRead || 0) / cacheBase * 100) : 0;
  const hasRuntimeStats = Boolean(stats && (contextTokens || total || stats.totalMessages || stats.userMessages));
  const progressClass = `context-progress-pct-${Math.round(percent / 5) * 5}`;
  return `<div class="context-usage-head"><strong>上下文容量${stats?.estimated ? " · 历史估算" : ""}</strong><span>${formatTokenShort(contextTokens)} / ${formatTokenShort(contextWindow)}（${Math.round(percent)}%）</span></div><div class="context-usage-progress ${progressClass}"><i></i></div>${hasRuntimeStats ? `<div class="context-usage-rows">${rowsMarkup}</div><div class="context-usage-foot"><span>平均缓存命中率</span><strong>${cacheHit}%</strong></div>` : `<p class="context-usage-empty">开始一次对话后，这里会显示当前会话的实时 Token 和上下文占用。</p>`}`;
}

function renderContextUsage() {
  const stats = state.sessionStats;
  const usage = stats?.contextUsage || {};
  const contextTokens = Number(usage.tokens || 0);
  const contextWindow = Number(usage.contextWindow || modelContextWindow() || 0);
  const percent = Math.max(0, Math.min(100, Number(usage.percent || 0)));
  document.querySelectorAll("[data-context-usage]").forEach((control) => {
    const ring = control.querySelector("[data-context-ring]");
    if (ring) {
      const progressCircle = ring.querySelector(".context-usage-ring-progress");
      if (progressCircle) ring.style.setProperty("--context-ring-offset", `${100 - percent}`);
      ring.setAttribute("aria-valuenow", `${Math.round(percent)}`);
      ring.classList.toggle("is-warn", percent >= 70 && percent < 90);
      ring.classList.toggle("is-danger", percent >= 90);
      const percentLabel = ring.querySelector("[data-context-percent]");
      if (percentLabel) percentLabel.textContent = stats ? `${Math.round(percent)}` : "—";
    }
    const trigger = control.querySelector(".context-usage-trigger");
    if (trigger) {
      const label = contextWindow ? `上下文容量 ${formatTokenShort(contextTokens)} / ${formatTokenShort(contextWindow)}（${Math.round(percent)}%）` : "查看上下文容量";
      trigger.setAttribute("aria-label", label);
      trigger.title = label;
    }
    const popover = control.querySelector(".context-usage-popover");
    if (popover) popover.innerHTML = contextUsagePopoverMarkup(stats);
  });
}

function renderModelSelectors() {
  const current = selectedComposerImageModel() || state.settings?.active_model || {};
  const label = activeModelLabel();
  ["homeModelLabel", "chatModelLabel"].forEach((id) => {
    const target = byId(id);
    if (target) target.textContent = label;
  });
  const menu = composerModelMenuMarkup(current);
  ["homeModelMenu", "chatModelMenu"].forEach((id) => {
    const target = byId(id);
    if (target) target.innerHTML = menu;
  });
  renderThinkingSelectors();
  setWebSearchMode(state.webSearchMode, { announce: false });
  setAgentMode(state.agentMode, { announce: false });
  renderContextUsage();
  if (selectedComposerImageModel()) {
    ["homeQuestionInput", "chatQuestionInput"].forEach(id => {
      const input = byId(id);
      if (input) input.placeholder = "描述你想生成的图片（仅使用本条文字）";
    });
  }
  document.querySelectorAll("[data-context-usage]").forEach(control => {
    control.hidden = Boolean(selectedComposerImageModel());
  });
  if (selectedComposerImageModel()) closeContextUsagePopovers();
  renderComposerSendButtons();
}

const thinkingLevels = [
  { value: "auto", label: "\u81ea\u52a8", detail: "\u7531\u6a21\u578b\u548c Agent \u6309\u95ee\u9898\u590d\u6742\u5ea6\u5206\u914d" },
  { value: "low", label: "\u4f4e", detail: "\u66f4\u5feb\u54cd\u5e94\uff0c\u8f83\u5c11\u68c0\u7d22\u4e0e\u5de5\u5177\u9884\u7b97" },
  { value: "medium", label: "\u4e2d", detail: "\u5e73\u8861\u63a8\u7406\u3001\u68c0\u7d22\u8303\u56f4\u4e0e\u54cd\u5e94\u901f\u5ea6" },
  { value: "high", label: "\u9ad8", detail: "\u66f4\u5927\u7684 Agent \u8bc1\u636e\u9884\u7b97\u4e0e\u539f\u751f\u63a8\u7406\u5f3a\u5ea6" },
  { value: "xhigh", label: "\u6781\u9ad8", detail: "\u66f4\u6df1\u63a8\u7406\u4e0e\u66f4\u5927\u7684 Agent \u8bc1\u636e\u9884\u7b97" },
  { value: "max", label: "\u6700\u9ad8", detail: "\u8bf7\u6c42 Pi \u4e0e\u6a21\u578b\u652f\u6301\u7684\u6700\u9ad8\u601d\u8003\u5f3a\u5ea6\uff1b\u4e0d\u652f\u6301\u65f6\u5b89\u5168\u964d\u7ea7" },
];

function currentThinkingLevel() {
  return thinkingLevels.some((item) => item.value === state.thinkingLevel) ? state.thinkingLevel : "auto";
}

function currentThinkingLabel() {
  return thinkingLevels.find((item) => item.value === currentThinkingLevel())?.label || "\u81ea\u52a8";
}

function activeModelSupportsReasoning() {
  return !selectedComposerImageModel() && Boolean(activeModel().model?.capabilities?.includes("reasoning"));
}

function renderThinkingSelectors() {
  const supported = activeModelSupportsReasoning();
  const menu = thinkingMenuMarkup({ disabled: !supported });
  document.querySelectorAll("[data-composer-thinking]").forEach((picker) => {
    const trigger = picker.querySelector("[data-action='toggle-composer-thinking']");
    picker.querySelector("[data-thinking-label]")?.replaceChildren(document.createTextNode(currentThinkingLabel()));
    const options = picker.querySelector(".composer-thinking-popover");
    if (options) options.innerHTML = menu;
    picker.classList.toggle("is-unavailable", !supported);
    if (!trigger) return;
    trigger.disabled = !supported;
    trigger.setAttribute("aria-label", supported ? `${label}\uff0c\u8c03\u6574\u63a8\u7406\u7b49\u7ea7` : "\u5f53\u524d\u6a21\u578b\u4e0d\u652f\u6301\u601d\u8003\u9884\u7b97");
    if (!supported) {
      picker.classList.remove("is-open");
      trigger.setAttribute("aria-expanded", "false");
    }
  });
}

function thinkingMenuMarkup({ disabled = false } = {}) {
  const current = currentThinkingLevel();
  const options = thinkingLevels.map((item) => {
    const selected = item.value === current;
    return `<button type="button" class="composer-thinking-option ${selected ? "is-selected" : ""}" data-action="select-composer-thinking" data-thinking-value="${item.value}" role="option" aria-selected="${selected ? "true" : "false"}" title="${escapeHtml(item.detail)}" ${disabled ? "disabled" : ""}><span>${item.label}</span><span class="composer-thinking-check" aria-hidden="true">${uiIcon("check")}</span></button>`;
  }).join("");
  return options;
}

function setComposerThinkingLevel(value) {
  if (!thinkingLevels.some((item) => item.value === value)) return;
  state.thinkingLevel = value;
  window.localStorage.setItem("scansci.thinking.level", value);
  renderThinkingSelectors();
  closeComposerThinkingPickers();
  toast(`\u601d\u8003\u7b49\u7ea7\u5df2\u8bbe\u4e3a\u300c${currentThinkingLabel()}\u300d`);
}

function composerModelMenuMarkup(current = {}) {
  const groupedProviders = new Map();
  for (const provider of (state.settings?.providers || [])) {
    const local = isLocalModelProvider(provider);
    if (!local && !isProviderUsable(provider) && !(provider.enabled && provider.models?.some(m => m.capabilities?.includes("image")))) continue;
    const models = (provider.models || [])
      .filter((model) => model.show_in_composer !== false)
      .map((model) => ({ model, availability: composerModelAvailability(model, provider) }))
      .filter(({ model, availability }) => availability.visible && (local || availability.selectable || model.capabilities?.includes("image")));
    if (!models.length) continue;
    const runtime = String(provider.runtime || provider.logo || "local").toLowerCase();
    const runtimeLabel = ({ ollama: "Ollama", "lm-studio": "LM Studio", "llama.cpp": "llama.cpp", local: "\u672c\u5730\u8fd0\u884c\u65f6" })[runtime] || runtime;
    const groupKey = String(provider.id || "").startsWith("local-runtime-")
      ? `runtime:${runtime}:${String(provider.base_url || "").toLowerCase()}`
      : String(provider.id || "");
    if (!groupedProviders.has(groupKey)) {
      groupedProviders.set(groupKey, { name: String(provider.id || "").startsWith("local-runtime-") ? `\u672c\u5730 \u00b7 ${runtimeLabel}` : provider.name, entries: [] });
    }
    groupedProviders.get(groupKey).entries.push(...models.map(({ model, availability }) => ({ provider, model, availability })));
  }
  const groups = [...groupedProviders.values()].map((group) => {
    const rows = group.entries.map(({ provider, model, availability }) => {
      const selected = provider.id === current.provider_id && model.id === current.model_id;
      const tags = [
        model.capabilities?.includes("image") ? '<span class="composer-model-tag">生图</span>' : "",
        model.capabilities?.includes("vision") ? '<span class="composer-model-tag">\u89c6\u89c9</span>' : "",
        model.capabilities?.includes("audio") ? '<span class="composer-model-tag">\u8bed\u97f3</span>' : "",
      ].join("");
      const name = escapeHtml(model.name || model.id);
      if (!availability.selectable) {
        const reason = escapeHtml(availability.detail || availability.label || "模型暂不可用，请到模型服务检查状态。");
        return `<details class="composer-model-reason"><summary class="composer-model-option is-unavailable" aria-label="查看 ${name} 不可用的原因"><span class="composer-model-option-main"><strong>${name}</strong></span><span class="composer-model-option-meta">${tags}<span class="composer-model-warning" title="${reason}" aria-hidden="true">${uiIcon("circle-alert")}</span></span></summary><div class="composer-model-reason-detail">${reason}</div></details>`;
      }
      const detail = model.capabilities?.includes("image") ? availability.detail : "";
      return `<button type="button" class="composer-model-option ${selected ? "is-selected" : ""}" data-action="select-composer-model" data-model-value="${escapeHtml(`${provider.id}::${model.id}`)}" role="option" aria-selected="${selected ? "true" : "false"}" title="${escapeHtml(detail)}"><span class="composer-model-option-main"><strong>${name}</strong>${detail ? `<small>${escapeHtml(detail)}</small>` : ""}</span><span class="composer-model-option-meta">${tags}</span><span class="composer-model-check" aria-hidden="true">${uiIcon("check")}</span></button>`;
    }).join("");
    const singleName = group.entries.length === 1 ? group.entries[0].model.name || group.entries[0].model.id : "";
    const groupLabel = singleName && String(group.name || "").localeCompare(String(singleName), undefined, { sensitivity: "base" }) === 0
      ? ""
      : `<span>${escapeHtml(group.name)}</span>`;
    return `<section class="composer-model-group">${groupLabel}${rows}</section>`;
  }).join("");
  const empty = groups || '<div class="composer-model-empty">对话候选中暂无模型，可在模型服务中重新显示</div>';
  const webMode = Object.hasOwn(webSearchLabels, state.webSearchMode) ? state.webSearchMode : "auto";
  const webOptions = [
    ["auto", "自动"],
    ["on", "开启"],
    ["off", "关闭"],
  ].map(([value, label]) => `<button type="button" class="web-search-option ${webMode === value ? "is-selected" : ""}" data-action="select-web-search" data-web-search-value="${value}" role="option" aria-selected="${webMode === value ? "true" : "false"}">${label}</button>`).join("");
  return `<div class="composer-settings-panel">
    ${selectedComposerImageModel() ? "" : `<section class="composer-settings-section web-search-picker" data-web-search-picker>
      <header><span data-ui-icon="globe"></span><span>联网搜索</span></header>
      <div class="composer-segmented-control" role="listbox" aria-label="联网搜索策略">${webOptions}</div>
    </section>
    <section class="composer-settings-section composer-thinking" data-composer-thinking>
      <header><span data-ui-icon="brain"></span><span>思考模式</span></header>
      <div class="composer-thinking-popover composer-segmented-control" role="listbox" aria-label="调整思考等级"></div>
    </section>`}
    <section class="composer-settings-models">
      <header><span>模型</span></header>
      ${empty}
      <div class="composer-model-manage"><button type="button" data-action="open-settings" data-settings-panel="models">${uiIcon("plus")}管理模型</button></div>
    </section>
  </div>`;
}

async function setActiveComposerModel(value) {
  const separator = String(value || "").indexOf("::");
  if (separator < 1) return;
  const providerId = String(value || "").slice(0, separator);
  const modelId = String(value || "").slice(separator + 2);
  if (!providerId || !modelId) return;
  const provider = state.settings?.providers?.find(p => p.id === providerId);
  const model = provider?.models?.find(m => m.id === modelId);
  if (!model || model.show_in_composer === false || !composerModelAvailability(model, provider).selectable) throw Error("所选模型尚不可用，请先完成模型服务配置。");
  state.composerImageModel = model.capabilities?.includes("image") ? String(value) : "";
  window.localStorage.setItem("scansci.composer.image-model", state.composerImageModel);
  if (state.composerImageModel) {
    renderModelSelectors();
    renderComposerSendButtons();
    closeComposerModelPickers();
    toast("已切换文生图，仅使用本条文字描述。");
    return;
  }
  if (state.settings.active_model?.provider_id === providerId && state.settings.active_model?.model_id === modelId) {
    renderModelSelectors();
    closeComposerModelPickers();
    return;
  }
  state.settings.active_model = { provider_id: providerId, model_id: modelId };
  renderModelSelectors();
  closeComposerModelPickers();
  await persistSettings("当前模型已切换");
}

function isProviderUsable(provider) {
  if (!provider?.enabled) return false;
  if (provider.id === "jev" || provider.api_surface === "jev_systemone") return false;
  // An Ollama model is only selectable after the exact model has been
  // confirmed by /api/tags.  The resource page still shows the install/setup
  // action while this is false.
  if (String(provider?.id || "").startsWith("local-runtime-") && provider?.logo === "ollama") {
    const health = state.modelHealth?.providers?.[provider.id];
    return health ? ["ready", "connected"].includes(String(health.status || "")) : Boolean(state.ollama?.reachable && state.ollama?.model_ready);
  }
  const isLocal = provider.kind === "local" || provider.auth_mode === "local" || String(provider.id || "").startsWith("local-runtime-");
  if (isLocal) {
    if (provider.id === "local-evidence") return true;
    const health = state.modelHealth?.providers?.[provider.id];
    return health ? ["ready", "connected", "partial"].includes(String(health.status || "")) : Boolean(provider.auth_mode === "local" && provider.base_url);
  }
  return provider.auth_mode === "managed" || Boolean(provider.api_key_configured);
}

const COMPOSER_IMAGE_LIMIT = 4;
const COMPOSER_IMAGE_MAX_BYTES = 4 * 1024 * 1024;
const COMPOSER_IMAGE_TOTAL_BYTES = 10 * 1024 * 1024;
const COMPOSER_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
const COMPOSER_SOURCE_LIMIT = 8;
const COMPOSER_SOURCE_MAX_BYTES = 50 * 1024 * 1024;
const COMPOSER_SOURCE_TOTAL_BYTES = 120 * 1024 * 1024;
const COMPOSER_SOURCE_SUFFIXES = new Set([
  ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".json", ".xml", ".html", ".htm",
  ".md", ".markdown", ".txt", ".rtf", ".epub", ".zip", ".png", ".jpg", ".jpeg", ".webp",
  ".gif", ".wav", ".mp3", ".m4a", ".flac",
]);

function composerKey(inputId) {
  return inputId === "homeQuestionInput" ? "home" : "chat";
}

function currentModelSupportsVision() {
  return Boolean(activeModel().model?.capabilities?.includes("vision"));
}

function localAudioModelReady() {
  return (state.localModelMarket?.installed || state.localInstalled || []).some((item) => {
    const id = String(item?.id || "").toLowerCase();
    const legacyQwen = id === "qwen/qwen3-asr-0.6b";
    return Boolean(item?.ready)
      && item?.runtime_compatible !== false
      && !legacyQwen
      && (item?.kind === "audio" || id.includes("asr") || id.includes("whisper"));
  });
}

function composerImagePreviewMarkup(images = []) {
  if (!images.length) return "";
  return `<div class="user-image-preview-list">${images.map((image) => {
    const src = image.preview_url || image.data_url || "";
    if (!src) return "";
    const alt = image.name || "用户图片";
    return `<button type="button" class="user-image-preview-trigger" data-action="open-image-preview" data-image-src="${escapeHtml(src)}" data-image-alt="${escapeHtml(alt)}" aria-label="查看 ${escapeHtml(alt)}"><img src="${escapeHtml(src)}" alt="${escapeHtml(alt)}" /></button>`;
  }).join("")}</div>`;
}

function composerSourcePreviewMarkup(sources = []) {
  if (!sources.length) return "";
  return `<div class="user-source-preview">${sources.map((source) => {
    const name = source.name || "附件";
    const label = `${fileTypeIconMarkup(name)}<span class="source-file-name">${escapeHtml(name)}</span>`;
    return source.file_url
      ? `<button type="button" class="user-source-file" data-action="open-ingestion-source" data-source-url="${escapeHtml(source.file_url)}" data-source-name="${escapeHtml(name)}" title="${escapeHtml(name)}">${label}</button>`
      : `<span class="user-source-file" title="${escapeHtml(name)}">${label}</span>`;
  }).join("")}</div>`;
}

function renderComposerImages(key) {
  const target = byId(`${key}ImageAttachments`);
  if (!target) return;
  const images = state.composerImages[key] || [];
  target.hidden = !images.length;
  target.innerHTML = images.map((image) => `<figure class="composer-image-card"><img src="${escapeHtml(image.data_url)}" alt="${escapeHtml(image.name)}" /><figcaption>${escapeHtml(image.name)}</figcaption><button type="button" data-action="remove-composer-image" data-composer-key="${key}" data-image-id="${escapeHtml(image.id)}" aria-label="移除 ${escapeHtml(image.name)}">${uiIcon("x")}</button></figure>`).join("");
}

function fileToDataUrl(file, errorMessage = "无法读取附件") {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error(errorMessage));
    reader.readAsDataURL(file);
  });
}

async function addComposerImages(key, files) {
  const incoming = [...files].filter(Boolean);
  if (!incoming.length) return;
  const existing = state.composerImages[key] || [];
  if (existing.length + incoming.length > COMPOSER_IMAGE_LIMIT) {
    toast(`一次最多可添加 ${COMPOSER_IMAGE_LIMIT} 张图片`, true);
    return;
  }
  const accepted = [];
  let totalBytes = existing.reduce((sum, image) => sum + Number(image.size || 0), 0);
  for (const file of incoming) {
    if (!COMPOSER_IMAGE_TYPES.has(file.type)) {
      toast("仅支持 PNG、JPG、WebP 或 GIF 图片", true);
      continue;
    }
    if (file.size > COMPOSER_IMAGE_MAX_BYTES) {
      toast("单张图片不能超过 4 MB", true);
      continue;
    }
    totalBytes += file.size;
    if (totalBytes > COMPOSER_IMAGE_TOTAL_BYTES) {
      toast("本次图片总大小不能超过 10 MB", true);
      break;
    }
    accepted.push({
      id: `image-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      name: file.name || `粘贴图片 ${existing.length + accepted.length + 1}`,
      mime_type: file.type,
      size: file.size,
      data_url: await fileToDataUrl(file),
    });
  }
  if (!accepted.length) return;
  state.composerImages[key] = [...existing, ...accepted];
  renderComposerImages(key);
}

function removeComposerImage(key, imageId) {
  state.composerImages[key] = (state.composerImages[key] || []).filter((image) => image.id !== imageId);
  renderComposerImages(key);
}

function clearComposerImages(key) {
  state.composerImages[key] = [];
  renderComposerImages(key);
}

function imagePayloadForComposer(key) {
  return (state.composerImages[key] || []).map((image) => ({
    name: image.name,
    mime_type: image.mime_type,
    data_url: image.data_url,
  }));
}

function sourceSuffix(name = "") {
  const match = String(name).toLowerCase().match(/\.[a-z0-9]+$/);
  return match ? match[0] : "";
}

function sourceTypeLabel(name = "") {
  return ({
    ".pdf": "PDF", ".docx": "Word", ".pptx": "PowerPoint", ".xlsx": "Excel", ".xls": "Excel",
    ".csv": "CSV", ".json": "JSON", ".xml": "XML", ".md": "Markdown", ".markdown": "Markdown",
    ".txt": "文本", ".rtf": "RTF", ".html": "HTML", ".htm": "HTML", ".epub": "EPUB", ".zip": "ZIP",
    ".png": "图片", ".jpg": "图片", ".jpeg": "图片", ".webp": "图片", ".gif": "图片",
    ".wav": "音频", ".mp3": "音频", ".m4a": "音频", ".flac": "音频",
  })[sourceSuffix(name)] || "文档";
}

function formatFileSize(size) {
  const bytes = Number(size || 0);
  if (!bytes) return "本地文件";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(bytes >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function renderComposerSources(key) {
  const target = byId(`${key}SourceAttachments`);
  if (!target) return;
  const sources = state.composerSources[key] || [];
  target.hidden = !sources.length;
  const cards = sources.map((source) => `<article class="composer-source-card">${fileTypeIconMarkup(source.name)}<span><strong title="${escapeHtml(source.name)}">${escapeHtml(source.name)}</strong><small>${escapeHtml(sourceTypeLabel(source.name))} · ${escapeHtml(formatFileSize(source.size))}</small></span><button type="button" data-action="remove-composer-source" data-composer-key="${escapeHtml(key)}" data-source-id="${escapeHtml(source.id)}" aria-label="移除 ${escapeHtml(source.name)}">${uiIcon("x")}</button></article>`).join("");
  const firstName = sources[0]?.name || "当前文件";
  const suggestions = sources.length === 1
    ? `<nav class="composer-file-suggestions" aria-label="基于文件继续"><button type="button" data-action="use-file-suggestion" data-composer-key="${escapeHtml(key)}" data-file-name="${escapeHtml(firstName)}" data-suggestion="总结">总结</button><button type="button" data-action="use-file-suggestion" data-composer-key="${escapeHtml(key)}" data-file-name="${escapeHtml(firstName)}" data-suggestion="提取要点">提取要点</button><button type="button" data-action="use-file-suggestion" data-composer-key="${escapeHtml(key)}" data-file-name="${escapeHtml(firstName)}" data-suggestion="生成综述">生成综述</button></nav>`
    : "";
  target.innerHTML = `<div class="composer-source-cards">${cards}</div>${suggestions}`;
}

function useFileSuggestion(key, fileName, suggestion) {
  const input = byId(key === "home" ? "homeQuestionInput" : "chatQuestionInput");
  if (!input) return;
  const prompts = {
    "总结": `请总结《${fileName}》的核心内容，并列出关键结论。`,
    "提取要点": `请从《${fileName}》中提取研究问题、方法、数据、主要发现与局限。`,
    "生成综述": `请以《${fileName}》为起点生成一份结构化文献综述，并区分原文证据与延伸判断。`,
  };
  input.value = prompts[suggestion] || `请阅读《${fileName}》。`;
  input.focus();
}

async function addComposerSources(key, files) {
  const incoming = [...files].filter(Boolean);
  if (!incoming.length) return;
  const existing = state.composerSources[key] || [];
  if (existing.length + incoming.length > COMPOSER_SOURCE_LIMIT) {
    toast(`一次最多可添加 ${COMPOSER_SOURCE_LIMIT} 个文件`, true);
    return;
  }
  const accepted = [];
  let totalBytes = existing.reduce((sum, item) => sum + Number(item.size || 0), 0);
  for (const file of incoming) {
    const suffix = sourceSuffix(file.name);
    if (!COMPOSER_SOURCE_SUFFIXES.has(suffix)) {
      toast("暂不支持这个文件格式", true);
      continue;
    }
    if (!file.size || file.size > COMPOSER_SOURCE_MAX_BYTES || totalBytes + file.size > COMPOSER_SOURCE_TOTAL_BYTES) {
      toast("单个附件不能超过 50 MB，本次附件总大小不能超过 120 MB", true);
      break;
    }
    totalBytes += file.size;
    accepted.push({
      id: `source-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      name: file.name || "未命名材料",
      size: file.size,
      data_url: await fileToDataUrl(file),
    });
  }
  if (!accepted.length) return;
  state.composerSources[key] = [...existing, ...accepted];
  renderComposerSources(key);
}

function addComposerSourcePaths(key, paths) {
  const incoming = Array.from(paths || []).map(String).filter(Boolean);
  if (!incoming.length) return;
  const existing = state.composerSources[key] || [];
  const remaining = COMPOSER_SOURCE_LIMIT - existing.length;
  if (remaining <= 0) {
    toast(`一次最多可添加 ${COMPOSER_SOURCE_LIMIT} 个文件`, true);
    return;
  }
  const accepted = incoming.slice(0, remaining).filter((path) => COMPOSER_SOURCE_SUFFIXES.has(sourceSuffix(path))).map((path, index) => ({
    id: `source-${Date.now()}-${index}`,
    name: path.split(/[\\/]/).pop() || "未命名材料",
    path,
    size: 0,
  }));
  if (!accepted.length) {
    toast("暂不支持所选文件格式", true);
    return;
  }
  state.composerSources[key] = [...existing, ...accepted];
  renderComposerSources(key);
}

function removeComposerSource(key, sourceId) {
  state.composerSources[key] = (state.composerSources[key] || []).filter((source) => source.id !== sourceId);
  renderComposerSources(key);
}

function clearComposerSources(key) {
  state.composerSources[key] = [];
  renderComposerSources(key);
}

function sourcePayloadForComposer(key) {
  return (state.composerSources[key] || []).map((source) => ({
    name: source.name,
    ...(source.path ? { path: source.path } : { data_url: source.data_url }),
  }));
}

function isBuiltInProvider(provider) {
  return (state.presets?.providers || []).some((preset) => preset.id === provider?.id);
}

function zhipuEndpointValue(provider, codingPlanEnabled = Boolean(provider?.coding_plan_enabled)) {
  const configured = codingPlanEnabled ? provider?.coding_plan_base_url : provider?.standard_base_url;
  return String(configured || "").trim() || (codingPlanEnabled ? ZHIPU_CODING_PLAN_BASE_URL : ZHIPU_STANDARD_BASE_URL);
}

const modelCapabilityDefinitions = [
  ["reasoning", "推理", "brain"], ["vision", "视觉", "eye"], ["embedding", "嵌入", "arrow-up-right"],
  ["reranking", "重排", "arrow-up-down"], ["tool", "工具", "wrench"], ["coding", "代码", "code"],
  ["image", "图像", "image"], ["audio", "音频", "audio"],
];

function providerLogo(provider, large = false) {
  const iconAssets = {
    "scansci-managed": "/scansci-mark.png",
    openai: "openai.png", anthropic: "anthropic.png", gemini: "gemini.png", "vertex-ai": "google.png",
    openrouter: "openrouter.png", nvidia: "nvidia.png", deepseek: "deepseek.png", dashscope: "qwen.png",
    zai: "zhipu.png", zhipu: "zhipu.png", moonshot: "moonshot.webp", minimax: "minimax.png",
    "xiaomi-mimo": "mimo.svg", modelscope: "modelscope.png", volcengine: "volcengine.png",
    "new-api": "new-api.png", aihubmix: "aihubmix.webp", ocoolai: "ocoolai.png", alaya: "alaya.webp",
    dmxapi: "dmxapi.webp", aionly: "aionly.webp", burncloud: "burncloud.png", cherryin: "cherryin.png",
    "github-copilot": "github-copilot.webp", typesafe: "typesafe.png",
  };
  const marks = {
    openai: "◎", anthropic: "A", gemini: "✦", "vertex-ai": "V", openrouter: "◌", "opencode-go": "OC", nvidia: "N",
    deepseek: "DS", dashscope: "Q", zai: "Z", zhipu: "Z", paratera: "PT", moonshot: "K", minimax: "M", "xiaomi-mimo": "mi",
    siliconflow: "SF", modelscope: "MS", ppio: "P", volcengine: "V", "huawei-cloud": "H", infinigence: "∞",
    "qiniu-ai": "7", modal: "M", "new-api": "N", "one-api": "1", aihubmix: "AH", ocoolai: "oc",
    alaya: "A", dmxapi: "D", aionly: "AI", burncloud: "B", cherryai: "C", cherryin: "CI",
    "github-copilot": "GH", typesafe: "J", wuwen: "W", "local-evidence": "S",
  };
  const rawKey = String(provider?.logo || provider?.id || "custom").toLowerCase();
  const key = rawKey.replace(/[^a-z0-9-]/g, "-");
  const mark = marks[rawKey] || String(provider?.name || "+").trim().slice(0, 2).toUpperCase();
  const iconAsset = iconAssets[rawKey];
  const iconUrl = iconAsset?.startsWith("/") ? iconAsset : `/provider-icons/${iconAsset || ""}`;
  const content = iconAsset
    ? `<img src="${escapeHtml(iconUrl)}" alt="" loading="lazy" />`
    : escapeHtml(mark);
  return `<span class="provider-logo provider-logo-${escapeHtml(key)} ${iconAsset ? "has-brand-image" : ""} ${large ? "is-large" : ""}" aria-label="${escapeHtml(provider?.name || "服务商")} 品牌标识">${content}</span>`;
}

function modelCapabilityChips(model, index, options = {}) {
  const enabled = new Set(Array.isArray(model?.capabilities) ? model.capabilities : []);
  const showAll = Boolean(options.showAll);
  const definitions = showAll ? modelCapabilityDefinitions : modelCapabilityDefinitions.filter(([id]) => enabled.has(id));
  const empty = !definitions.length && !showAll ? '<span class="model-capability-empty">未标注</span>' : "";
  if (options.readOnly) {
    return `<div class="model-capabilities" aria-label="模型能力">${definitions.map(([id, label, iconName]) => `<span class="model-capability-chip is-active" title="${escapeHtml(label)}"><i>${uiIcon(iconName)}</i><span>${label}</span></span>`).join("")}${empty}</div>`;
  }
  return `<div class="model-capabilities ${showAll ? "is-picker" : ""}" aria-label="模型能力">${definitions.map(([id, label, iconName]) => `<button type="button" class="model-capability-chip ${enabled.has(id) ? "is-active" : ""}" data-action="toggle-model-capability" data-model-index="${index}" data-capability="${id}" title="${enabled.has(id) ? `移除${label}能力` : `添加${label}能力`}"><i>${uiIcon(iconName)}</i><span>${label}</span></button>`).join("")}${empty}</div>`;
}

function modelEditorMarkup(provider) {
  const index = Number(state.editingModelIndex);
  const model = provider?.models?.[index];
  if (!model || index < 0) return "";
  return `<div class="cherry-model-editor-backdrop" role="presentation"><section class="cherry-model-editor" role="dialog" aria-modal="true" aria-labelledby="modelEditorTitle"><header><h2 id="modelEditorTitle">编辑模型</h2><button type="button" class="cherry-icon-button" data-action="close-model-editor" aria-label="关闭">${uiIcon("x")}</button></header><div class="cherry-editor-body"><label><span><b>*</b> 模型 ID</span><input data-model-id="${index}" value="${escapeHtml(model.id)}" placeholder="例如 gpt-5.2" required /></label><label><span>模型名称</span><input data-model-name="${index}" value="${escapeHtml(model.name || model.id)}" placeholder="例如 GPT-5.2" /></label><label><span>分组名称</span><input data-model-group="${index}" value="${escapeHtml(model.group || "默认模型")}" placeholder="例如 GPT" /></label><label><span>上下文窗口</span><input data-model-context="${index}" value="${escapeHtml(model.context_window || "")}" placeholder="例如 128K" /></label><section class="cherry-capability-editor"><div><strong>更多设置</strong><span>模型能力</span></div>${modelCapabilityChips(model, index, { showAll: true })}</section></div><footer><button type="button" class="cherry-text-button" data-action="close-model-editor">取消</button><button type="button" class="cherry-save-button" data-action="save-model-editor">保存</button></footer></section></div>`;
}

function providerConnectionLabel(provider) {
  if (provider?.kind === "local") return "内置";
  if (provider?.auth_mode === "managed") return "ScanSci 托管";
  return provider?.api_key_configured ? "已连接" : "未连接";
}

function navigationLocation() {
  return {
    view: state.activeView,
    mode: state.activeMode,
    settings: state.activeSettings,
    extensions: state.activeExtensions,
    task: state.activeTaskId,
    direct: state.directConversationId,
  };
}

const COMPOSER_AUDIO_LIMIT = 4;
const COMPOSER_AUDIO_MAX_BYTES = 50 * 1024 * 1024;
const COMPOSER_AUDIO_TOTAL_BYTES = 100 * 1024 * 1024;
const COMPOSER_AUDIO_RECORDING_LIMIT_MS = 5 * 60 * 1000;
const COMPOSER_AUDIO_TYPES = new Set([
  "audio/wav", "audio/wave", "audio/x-wav", "audio/mpeg", "audio/mp3", "audio/mp4", "audio/x-m4a",
  "audio/flac", "audio/ogg", "audio/aac", "audio/webm",
]);

function normalizedAudioMimeType(value = "") {
  return String(value || "").split(";", 1)[0].trim().toLowerCase();
}

function writeWavAscii(view, offset, value) {
  for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
}

function encodeAudioBufferAsWav(audioBuffer) {
  const frameCount = Number(audioBuffer.length || 0);
  const channelCount = Math.max(1, Number(audioBuffer.numberOfChannels || 1));
  const sampleRate = Math.max(8_000, Number(audioBuffer.sampleRate || 16_000));
  const output = new ArrayBuffer(44 + (frameCount * 2));
  const view = new DataView(output);
  writeWavAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + (frameCount * 2), true);
  writeWavAscii(view, 8, "WAVE");
  writeWavAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeWavAscii(view, 36, "data");
  view.setUint32(40, frameCount * 2, true);
  const channels = Array.from({ length: channelCount }, (_value, index) => audioBuffer.getChannelData(index));
  for (let frame = 0; frame < frameCount; frame += 1) {
    let sample = 0;
    for (const channel of channels) sample += Number(channel[frame] || 0) / channelCount;
    const clamped = Math.max(-1, Math.min(1, sample));
    view.setInt16(44 + (frame * 2), clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }
  return new Blob([output], { type: "audio/wav" });
}

async function browserRecordingToWavFile(recordedBlob, stem) {
  const AudioContextCtor = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (typeof AudioContextCtor !== "function") throw new Error("当前浏览器无法解码麦克风录音");
  const context = new AudioContextCtor();
  try {
    const decoded = await context.decodeAudioData(await recordedBlob.arrayBuffer());
    let prepared = decoded;
    const OfflineAudioContextCtor = globalThis.OfflineAudioContext || globalThis.webkitOfflineAudioContext;
    if (typeof OfflineAudioContextCtor === "function" && (decoded.sampleRate !== 16_000 || decoded.numberOfChannels !== 1)) {
      const renderedFrames = Math.max(1, Math.ceil(decoded.duration * 16_000));
      const offline = new OfflineAudioContextCtor(1, renderedFrames, 16_000);
      const source = offline.createBufferSource();
      source.buffer = decoded;
      source.connect(offline.destination);
      source.start(0);
      prepared = await offline.startRendering();
    }
    return new File([encodeAudioBufferAsWav(prepared)], `${stem}.wav`, { type: "audio/wav" });
  } finally {
    await context.close().catch(() => {});
  }
}

function composerAudioPreviewMarkup(audio = []) {
  if (!audio.length) return "";
  return `<div class="user-audio-preview-list">${audio.map((item) => `<span class="user-audio-preview">${fileTypeIconMarkup(item.name || "语音.wav")}<strong>${escapeHtml(item.name || "语音")}</strong></span>`).join("")}</div>`;
}

function renderComposerRecordingControl(key) {
  const recording = state.composerRecordings[key];
  const processing = Boolean(state.composerTranscribing[key]);
  document.querySelectorAll(`[data-action="toggle-composer-recording"][data-composer-key="${key}"]`).forEach((button) => {
    const active = Boolean(recording) && !processing;
    button.classList.toggle("is-recording", active);
    button.classList.toggle("is-processing", processing);
    button.disabled = processing;
    button.setAttribute("aria-pressed", String(active));
    button.setAttribute("aria-busy", String(processing));
    button.setAttribute("aria-label", processing ? "正在识别语音" : active ? "停止录音" : "语音输入");
    const idle = button.querySelector("[data-recording-idle]");
    const activeView = button.querySelector("[data-recording-active]");
    const processingView = button.querySelector("[data-recording-processing]");
    if (idle) idle.hidden = active || processing;
    if (activeView) activeView.hidden = !active;
    if (processingView) processingView.hidden = !processing;
    const duration = button.querySelector("[data-recording-duration]");
    if (duration && recording) duration.textContent = formatRecordingDuration(performance.now() - recording.startedAt);
  });
}

function renderComposerAudio(key) {
  const target = byId(`${key}AudioAttachments`);
  const audio = state.composerAudio[key] || [];
  if (target) {
    target.hidden = !audio.length;
    target.innerHTML = audio.map((item) => `<article class="composer-audio-card">${fileTypeIconMarkup(item.name)}<span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(formatFileSize(item.size))}</small></span><button type="button" data-action="remove-composer-audio" data-composer-key="${escapeHtml(key)}" data-audio-id="${escapeHtml(item.id)}" aria-label="移除 ${escapeHtml(item.name)}">${uiIcon("x")}</button></article>`).join("");
  }
  renderComposerRecordingControl(key);
}

function formatRecordingDuration(milliseconds = 0) {
  const seconds = Math.max(0, Math.floor(Number(milliseconds || 0) / 1000));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

async function transcribeComposerRecording(key, file) {
  state.composerTranscribing[key] = true;
  renderComposerRecordingControl(key);
  try {
    const input = byId(key === "home" ? "homeQuestionInput" : "chatQuestionInput");
    const dataUrl = await fileToDataUrl(file, "无法读取录音");
    const result = await request("/api/audio/transcribe", {
      method: "POST",
      body: JSON.stringify({
        audio: [{ name: file.name, mime_type: normalizedAudioMimeType(file.type) || "audio/webm", size: file.size, data_url: dataUrl }],
      }),
    });
    const text = (result.transcripts || [])
      .map((item) => String(item?.text || "").trim())
      .filter(Boolean)
      .join("\n");
    if (!text) throw new Error("录音没有识别出可用文字");
    if (input) {
      const existing = String(input.value || "").trim();
      input.value = existing ? `${existing}\n${text}` : text;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
    toast("语音已转换为文字");
  } finally {
    state.composerTranscribing[key] = false;
    renderComposerRecordingControl(key);
  }
}

async function toggleComposerRecording(key) {
  const existing = state.composerRecordings[key];
  if (existing) {
    existing.recorder.stop();
    return;
  }
  const mediaDevices = globalThis.navigator?.mediaDevices;
  const MediaRecorderCtor = globalThis.MediaRecorder;
  if (!mediaDevices?.getUserMedia || typeof MediaRecorderCtor === "undefined") {
    toast("当前环境不支持麦克风录音，请检查桌面端的麦克风权限。", true);
    return;
  }
  let stream;
  try {
    stream = await mediaDevices.getUserMedia({ audio: true });
    const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/ogg"]
      .find((type) => !MediaRecorderCtor.isTypeSupported || MediaRecorderCtor.isTypeSupported(type)) || "";
    const recorder = mimeType ? new MediaRecorderCtor(stream, { mimeType }) : new MediaRecorderCtor(stream);
    const chunks = [];
    const recording = { recorder, stream, chunks, timer: 0, uiTimer: 0, startedAt: performance.now(), discard: false };
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data?.size) chunks.push(event.data);
    });
    recorder.addEventListener("stop", async () => {
      window.clearTimeout(recording.timer);
      window.clearInterval(recording.uiTimer);
      stream.getTracks().forEach((track) => track.stop());
      if (state.composerRecordings[key] === recording) state.composerRecordings[key] = null;
      renderComposerAudio(key);
      if (recording.discard) return;
      if (!chunks.length) return;
      const type = recorder.mimeType || mimeType || "audio/webm";
      const stem = `录音-${new Date().toISOString().replace(/[:.]/g, "-")}`;
      try {
        const file = await browserRecordingToWavFile(new Blob(chunks, { type }), stem);
        await transcribeComposerRecording(key, file);
      } catch (error) {
        toast(error?.message || "语音转文字失败，请稍后重试", true);
        if (/语音模型|ASR|本地模型|转写/.test(String(error?.message || ""))) openSettings("local-models");
      }
    });
    state.composerRecordings[key] = recording;
    recorder.start();
    recording.uiTimer = window.setInterval(() => {
      if (state.composerRecordings[key] === recording) renderComposerAudio(key);
    }, 250);
    recording.timer = window.setTimeout(() => {
      if (state.composerRecordings[key] === recording && recorder.state === "recording") {
        recorder.stop();
        toast("录音已达到 5 分钟上限");
      }
    }, COMPOSER_AUDIO_RECORDING_LIMIT_MS);
    renderComposerAudio(key);
    toast("正在录音，再次点击即可停止");
  } catch (error) {
    stream?.getTracks?.().forEach((track) => track.stop());
    toast(error?.name === "NotAllowedError" ? "麦克风权限未开启，请允许 ScanSci 使用麦克风。" : `无法开始录音：${error.message}`, true);
  }
}

async function addComposerAudio(key, files) {
  const incoming = [...files].filter(Boolean);
  if (!incoming.length) return;
  const existing = state.composerAudio[key] || [];
  if (existing.length + incoming.length > COMPOSER_AUDIO_LIMIT) {
    toast(`一次最多可以添加 ${COMPOSER_AUDIO_LIMIT} 个音频文件`, true);
    return;
  }
  const accepted = [];
  let totalBytes = existing.reduce((sum, item) => sum + Number(item.size || 0), 0);
  for (const file of incoming) {
    if (!COMPOSER_AUDIO_TYPES.has(normalizedAudioMimeType(file.type))) {
      toast("仅支持 WAV、MP3、M4A、FLAC、OGG、AAC 或 WebM 音频", true);
      continue;
    }
    if (!file.size || file.size > COMPOSER_AUDIO_MAX_BYTES) {
      toast("单个音频文件不能超过 50 MB", true);
      continue;
    }
    if (totalBytes + file.size > COMPOSER_AUDIO_TOTAL_BYTES) {
      toast("本次音频附件总大小不能超过 100 MB", true);
      break;
    }
    totalBytes += file.size;
    accepted.push({
      id: `audio-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      name: file.name || `语音 ${existing.length + accepted.length + 1}`,
      mime_type: String(file.type || "audio/wav").toLowerCase(),
      size: file.size,
      data_url: await fileToDataUrl(file, "无法读取音频文件"),
    });
  }
  if (!accepted.length) return;
  state.composerAudio[key] = [...existing, ...accepted];
  renderComposerAudio(key);
}

function removeComposerAudio(key, audioId) {
  state.composerAudio[key] = (state.composerAudio[key] || []).filter((item) => item.id !== audioId);
  renderComposerAudio(key);
}

function clearComposerAudio(key) {
  const recording = state.composerRecordings[key];
  if (recording) {
    recording.discard = true;
    window.clearTimeout(recording.timer);
    window.clearInterval(recording.uiTimer);
    if (recording.recorder.state === "recording") recording.recorder.stop();
    recording.stream.getTracks().forEach((track) => track.stop());
    state.composerRecordings[key] = null;
  }
  state.composerAudio[key] = [];
  renderComposerAudio(key);
}

function audioPayloadForComposer(key) {
  return (state.composerAudio[key] || []).map((item) => ({
    name: item.name,
    mime_type: item.mime_type,
    data_url: item.data_url,
  }));
}

function navigationKey(location) {
  return [location.view, location.mode, location.settings, location.extensions, location.task, location.direct].join("::");
}

function updateChromeControls() {
  document.querySelectorAll('[data-history-direction="back"]').forEach((button) => {
    button.disabled = state.navigationIndex <= 0;
  });
  document.querySelectorAll('[data-history-direction="forward"]').forEach((button) => {
    button.disabled = state.navigationIndex >= state.navigationHistory.length - 1;
  });
}

function recordNavigation() {
  const location = navigationLocation();
  const current = state.navigationHistory[state.navigationIndex];
  if (current && navigationKey(current) === navigationKey(location)) {
    updateChromeControls();
    return;
  }
  state.navigationHistory = state.navigationHistory.slice(0, state.navigationIndex + 1);
  state.navigationHistory.push(location);
  state.navigationIndex = state.navigationHistory.length - 1;
  updateChromeControls();
}

function moveNavigation(delta) {
  const nextIndex = state.navigationIndex + delta;
  if (nextIndex < 0 || nextIndex >= state.navigationHistory.length) return;
  state.navigationIndex = nextIndex;
  const location = state.navigationHistory[nextIndex];
  state.activeMode = location.mode;
  state.activeSettings = location.settings;
  state.activeExtensions = location.extensions || "skills";
  state.activeTaskId = location.task;
  state.directConversationId = location.direct || "";
  setView(location.view, { record: false });
  if (location.view === "conversation" && location.task) {
    const run = state.runs.find((item) => item.run_id === location.task);
    if (run) openTask(run.run_id, { record: false });
  } else if (location.view === "conversation" && location.direct) {
    openDirectConversation(location.direct, { record: false });
  }
  renderTasks();
  updateChromeControls();
}

function maxSidebarWidth() {
  return Math.max(300, Math.min(520, window.innerWidth - 480));
}

function clampSidebarWidth(value) {
  return Math.round(Math.max(260, Math.min(maxSidebarWidth(), Number(value) || 288)));
}

function applySidebarWidth({ persist = false } = {}) {
  state.sidebarWidth = clampSidebarWidth(state.sidebarWidth);
  const workbench = byId("workbench");
  const resizer = byId("sidebarResizer");
  workbench.style.setProperty("--sidebar-width", `${state.sidebarWidth}px`);
  if (resizer) {
    resizer.setAttribute("aria-valuemax", String(maxSidebarWidth()));
    resizer.setAttribute("aria-valuenow", String(state.sidebarWidth));
  }
  if (persist) window.localStorage.setItem("scansci.sidebar.width", String(state.sidebarWidth));
}

function applySidebarState() {
  const workbench = byId("workbench");
  workbench.classList.toggle("is-sidebar-collapsed", state.sidebarCollapsed);
  applySidebarWidth();
  document.querySelectorAll('[data-action="toggle-sidebar"]').forEach((button) => {
    button.setAttribute("aria-expanded", String(!state.sidebarCollapsed));
    button.setAttribute("aria-label", state.sidebarCollapsed ? "展开侧栏" : "折叠侧栏");
  });
}

function toggleSidebar() {
  state.sidebarCollapsed = !state.sidebarCollapsed;
  window.localStorage.setItem("scansci.sidebar.collapsed", String(state.sidebarCollapsed));
  applySidebarState();
}

function installSidebarResizer() {
  const resizer = byId("sidebarResizer");
  if (!resizer) return;
  let startX = 0;
  let startWidth = 0;
  let resizing = false;
  const finish = (event) => {
    if (!resizing) return;
    resizing = false;
    byId("workbench").classList.remove("is-resizing-sidebar");
    if (event?.pointerId !== undefined && resizer.hasPointerCapture(event.pointerId)) resizer.releasePointerCapture(event.pointerId);
    applySidebarWidth({ persist: true });
  };
  resizer.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || state.sidebarCollapsed || state.activeView === "settings") return;
    event.preventDefault();
    startX = event.clientX;
    startWidth = state.sidebarWidth;
    resizing = true;
    resizer.focus({ preventScroll: true });
    resizer.setPointerCapture(event.pointerId);
    byId("workbench").classList.add("is-resizing-sidebar");
  });
  resizer.addEventListener("pointermove", (event) => {
    if (!resizing) return;
    state.sidebarWidth = clampSidebarWidth(startWidth + event.clientX - startX);
    applySidebarWidth();
  });
  resizer.addEventListener("pointerup", finish);
  resizer.addEventListener("pointercancel", finish);
  resizer.addEventListener("keydown", (event) => {
    const step = event.shiftKey ? 32 : 12;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      state.sidebarWidth -= step;
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      state.sidebarWidth += step;
    } else if (event.key === "Home") {
      event.preventDefault();
      state.sidebarWidth = 260;
    } else if (event.key === "End") {
      event.preventDefault();
      state.sidebarWidth = maxSidebarWidth();
    } else return;
    applySidebarWidth({ persist: true });
  });
  let wasNarrowWindow = window.innerWidth <= 900;
  window.addEventListener("resize", () => {
    const isNarrowWindow = window.innerWidth <= 900;
    if (isNarrowWindow && !wasNarrowWindow && window.localStorage.getItem("scansci.sidebar.collapsed") === null) {
      state.sidebarCollapsed = true;
      applySidebarState();
    }
    wasNarrowWindow = isNarrowWindow;
    applySidebarWidth();
  });
}

function maxContextPanelWidth() {
  const layout = byId("conversationLayout");
  const availableWidth = Number(layout?.getBoundingClientRect().width || 0);
  const fallbackAvailableWidth = Math.max(0, window.innerWidth - (state.sidebarCollapsed ? 0 : state.sidebarWidth));
  return Math.max(320, Math.floor((availableWidth || fallbackAvailableWidth) - 370));
}

function contextPanelWidthKey() {
  return state.evidencePanelExpanded ? "scansci.evidence-panel.width" : "scansci.context-panel.width";
}

function currentContextPanelWidth() {
  return state.evidencePanelExpanded ? state.expandedEvidencePanelWidth : state.contextPanelWidth;
}

function setCurrentContextPanelWidth(value) {
  const width = Math.round(Math.max(280, Math.min(maxContextPanelWidth(), Number(value) || 335)));
  if (state.evidencePanelExpanded) state.expandedEvidencePanelWidth = width;
  else state.contextPanelWidth = width;
  return width;
}

function applyContextPanelWidth({ persist = false } = {}) {
  const layout = byId("conversationLayout");
  const resizer = byId("contextPanelResizer");
  if (!layout) return;
  const width = setCurrentContextPanelWidth(currentContextPanelWidth());
  layout.style.setProperty("--context-panel-width", `${width}px`);
  if (resizer) {
    resizer.setAttribute("aria-valuemax", String(maxContextPanelWidth()));
    resizer.setAttribute("aria-valuenow", String(width));
    resizer.setAttribute("aria-label", state.contextPanel === "evidence" ? "调整全文证据栏宽度" : "调整右侧资料栏宽度");
  }
  if (persist) window.localStorage.setItem(contextPanelWidthKey(), String(width));
}

function canResizeContextPanel() {
  const layout = byId("conversationLayout");
  const panel = byId("contextPanel");
  return Boolean(
    layout
    && panel
    && window.innerWidth > 960
    && state.contextPanel !== "none"
    && !panel.classList.contains("is-hidden")
    && !layout.classList.contains("is-review-workbench"),
  );
}

function installContextPanelResizer() {
  const resizer = byId("contextPanelResizer");
  if (!resizer) return;
  let startX = 0;
  let startWidth = 0;
  let resizing = false;
  const finish = (event) => {
    if (!resizing) return;
    resizing = false;
    byId("conversationLayout")?.classList.remove("is-resizing-context-panel");
    if (event?.pointerId !== undefined && resizer.hasPointerCapture(event.pointerId)) resizer.releasePointerCapture(event.pointerId);
    applyContextPanelWidth({ persist: true });
    window.requestAnimationFrame(centerEvidenceReaderAnchor);
  };
  resizer.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !canResizeContextPanel()) return;
    event.preventDefault();
    startX = event.clientX;
    startWidth = currentContextPanelWidth();
    resizing = true;
    resizer.focus({ preventScroll: true });
    resizer.setPointerCapture(event.pointerId);
    byId("conversationLayout")?.classList.add("is-resizing-context-panel");
  });
  resizer.addEventListener("pointermove", (event) => {
    if (!resizing) return;
    setCurrentContextPanelWidth(startWidth - (event.clientX - startX));
    applyContextPanelWidth();
  });
  resizer.addEventListener("pointerup", finish);
  resizer.addEventListener("pointercancel", finish);
  resizer.addEventListener("keydown", (event) => {
    if (!canResizeContextPanel()) return;
    const step = event.shiftKey ? 32 : 12;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setCurrentContextPanelWidth(currentContextPanelWidth() + step);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      setCurrentContextPanelWidth(currentContextPanelWidth() - step);
    } else if (event.key === "Home") {
      event.preventDefault();
      setCurrentContextPanelWidth(280);
    } else if (event.key === "End") {
      event.preventDefault();
      setCurrentContextPanelWidth(maxContextPanelWidth());
    } else return;
    applyContextPanelWidth({ persist: true });
    window.requestAnimationFrame(centerEvidenceReaderAnchor);
  });
  window.addEventListener("resize", () => {
    applyContextPanelWidth();
    window.requestAnimationFrame(centerEvidenceReaderAnchor);
  });
}

async function controlDesktopWindow(method) {
  const api = window.pywebview?.api;
  if (!api || typeof api[method] !== "function") return;
  const result = await api[method]();
  if (method !== "toggle_maximize_window" || !result?.ok) return;
  const maximized = Boolean(result.maximized);
  document.documentElement.classList.toggle("desktop-window-maximized", maximized);
  document.body.classList.toggle("desktop-window-maximized", maximized);
  const button = document.querySelector('[data-action="toggle-maximize-window"]');
  if (!button) return;
  button.setAttribute("aria-label", maximized ? "还原窗口" : "最大化窗口");
  button.setAttribute("title", maximized ? "还原窗口" : "最大化窗口");
}

const profileAvatars = {
  male: { label: "男熊猫", src: "/avatar-panda-male-tight.png" },
  female: { label: "女熊猫", src: "/avatar-panda-female-tight.png" },
};

function renderProfileAvatar() {
  const avatar = profileAvatars[state.profileAvatar] || profileAvatars.male;
  document.querySelectorAll("[data-profile-avatar-image]").forEach((image) => {
    image.src = avatar.src;
  });
  document.querySelectorAll("[data-avatar-value]").forEach((option) => {
    const selected = option.dataset.avatarValue === state.profileAvatar;
    option.classList.toggle("is-selected", selected);
    option.setAttribute("aria-checked", String(selected));
  });
}

function closeProfileAvatarPicker() {
  document.querySelectorAll("[data-profile-picker]").forEach((picker) => {
    picker.classList.remove("is-open");
    picker.querySelector("[data-action='toggle-profile-avatar']")?.setAttribute("aria-expanded", "false");
  });
}

function toggleProfileAvatarPicker(trigger) {
  const picker = trigger.closest("[data-profile-picker]");
  if (!picker) return;
  const open = !picker.classList.contains("is-open");
  closeProfileAvatarPicker();
  picker.classList.toggle("is-open", open);
  trigger.setAttribute("aria-expanded", String(open));
}

function selectProfileAvatar(value) {
  if (!profileAvatars[value]) return;
  state.profileAvatar = value;
  window.localStorage.setItem("scansci.profile.avatar", value);
  renderProfileAvatar();
  closeProfileAvatarPicker();
}

function setView(name, { record = true } = {}) {
  navigationRequestGeneration += 1;
  state.activeView = name;
  byId("workbench").classList.toggle("is-settings", name === "settings");
  updateSidebarNavigation();
  document.querySelectorAll(".app-view").forEach((view) => view.classList.toggle("is-active", view.dataset.view === name));
  if (name === "conversation") scheduleConversationTurnNav();
  document.documentElement.scrollTop = 0;
  if (name === "settings") {
    renderSettings();
    const settingsContent = byId("settingsContent");
    if (settingsContent) {
      settingsContent.scrollTop = 0;
      settingsContent.scrollLeft = 0;
    }
  }
  if (name === "mode") renderMode();
  if (name === "extensions") renderExtensions();
  if (name === "mcp") renderMcpMarketplaceView();
  renderFirstRunWelcome();
  if (record) recordNavigation();
}

function updateSidebarNavigation() {
  document.querySelectorAll(".sidebar-action").forEach((button) => {
    const action = button.dataset.action;
    const mode = button.dataset.mode;
    const isActive = (action === "open-extensions" && state.activeView === "extensions")
      || (action === "open-mcp-marketplace" && state.activeView === "mcp")
      || (action === "open-mode" && state.activeView === "mode" && mode === state.activeMode);
    button.classList.toggle("is-active", isActive);
    if (isActive) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

function openMode(mode) {
  state.activeMode = mode;
  setView("mode");
  if (mode === "library" && knowledgeSourceKind(state.notebook) === "translation") void refreshTranslationLibrary();
}

async function refreshTranslationLibrary() {
  const workspace = await request("/api/workspace");
  state.workspace = workspace;
  state.notebook = (workspace.notebooks || []).find((item) => item.notebook_id === state.notebook?.notebook_id) || state.notebook;
  if (state.activeView === "mode" && state.activeMode === "library") renderMode();
}

function openSettings(panel = "general") {
  if (state.activeView !== "settings") state.settingsReturnView = state.activeView || "home";
  // Keep old deep links working while moving model roles and document tools
  // into the user-facing default-capabilities page.
  const openResourceGuide = panel === "resources";
  state.activeSettings = ["routing", "document-processing", "resources"].includes(panel) ? "defaults" : panel;
  if (panel === "models" && state.settings?.active_model?.provider_id) {
    state.selectedProviderId = state.settings.active_model.provider_id;
  }
  setView("settings");
  if (openResourceGuide) openResourceGuideOverlay();
}

function closeSettings() {
  const returnView = ["home", "mode", "extensions", "mcp"].includes(state.settingsReturnView)
    ? state.settingsReturnView
    : "home";
  setView(returnView);
}

function openExtensions(tab = "skills") {
  state.activeExtensions = ["plugins", "skills", "market"].includes(tab) ? tab : "skills";
  setView("extensions");
  refreshExtensions({ quiet: true, includeMarket: state.activeExtensions === "market" }).catch((error) => toast(error.message, true));
  refreshExtensionUpdates({ quiet: true }).catch(() => {});
}

function openMcpMarketplace() {
  setView("mcp");
  loadMcpMarketplace().catch((error) => toast(error.message, true));
}

function startTask() {
  state.activeTaskId = "";
  state.directConversationId = "";
  state.reviewDocument = null;
  state.reviewDocumentOpen = false;
  state.sessionId = null;
  state.sessionTokens = 0;
  state.contextUsagePercent = 0;
  state.sessionStats = null;
  // Starting a new conversation only detaches the view. Existing direct-chat
  // jobs keep running under their own conversation IDs.
  syncActiveDirectChatState();
  closeContextUsagePopovers();
  renderContextUsage();
  window.localStorage.removeItem("scansci.active.task");
  window.localStorage.removeItem("scansci.active.direct");
  window.localStorage.removeItem("scansci.active.session");
  state.directMessages = [];
  clearComposerSkills("home");
  clearComposerSkills("chat");
  setComposerMode("general");
  applyContextPanelPreset("none");
  setView("home");
  window.setTimeout(() => byId("homeQuestionInput").focus(), 0);
}

const contextPanelPresets = Object.freeze({
  research: {
    kind: "sources",
    eyebrow: "研究上下文",
    title: "研究资料",
    countUnit: " 个文档",
    toggleLabel: "研究资料",
    landmarkLabel: "研究资料上下文",
  },
  knowledge: {
    kind: "sources",
    eyebrow: "当前回答",
    title: "知识来源",
    countUnit: " 个可用文档",
    toggleLabel: "知识来源",
    landmarkLabel: "知识问答来源",
  },
  evidence: {
    kind: "sources",
    eyebrow: "可核验上下文",
    title: "证据来源",
    countUnit: " 个文档",
    toggleLabel: "证据来源",
    landmarkLabel: "证据与来源",
  },
  review: {
    kind: "review",
    toggleLabel: "研究稿件",
    landmarkLabel: "研究稿件",
  },
  none: {
    kind: "none",
    toggleLabel: "上下文",
    landmarkLabel: "任务上下文",
  },
});

const contextPanelWorkflowPresets = Object.freeze({
  evidence_index: "evidence",
  ask: "evidence",
  literature_review: "review",
  academic_search: "research",
  deep_research: "review",
  research_idea: "none",
  novelty_check: "evidence",
  ppt_outline: "none",
  ppt_project: "none",
  pdf_to_ppt: "none",
  paper_download: "none",
  paper_download_batch: "none",
  paper_search_download: "none",
});

function contextPanelPresetForRun(run = {}) {
  return contextPanelWorkflowPresets[String(run.workflow_type || "")] || "none";
}

function applyContextPanelPreset(name = "none") {
  const safeName = contextPanelPresets[name] ? name : "none";
  const preset = contextPanelPresets[safeName];
  state.contextPanelPreset = safeName;
  const panel = byId("contextPanel");
  if (panel) {
    panel.dataset.context = safeName;
    panel.setAttribute("aria-label", preset.landmarkLabel);
  }
  const sourceView = byId("sourcePanelView");
  if (sourceView && preset.title) sourceView.setAttribute("aria-label", preset.title);
  const eyebrow = byId("contextPanelEyebrow");
  if (eyebrow && preset.eyebrow) eyebrow.textContent = preset.eyebrow;
  const title = byId("contextPanelTitle");
  if (title && preset.title) title.textContent = preset.title;
  const countUnit = byId("sourceCountUnit");
  if (countUnit && preset.countUnit) countUnit.textContent = preset.countUnit;
  setContextPanel(preset.kind);
}

function setContextPanel(kind = "sources") {
  const safeKind = ["sources", "evidence", "review", "none"].includes(kind) ? kind : "sources";
  const preset = contextPanelPresets[state.contextPanelPreset] || contextPanelPresets.research;
  const review = safeKind === "review";
  const evidence = safeKind === "evidence";
  const unavailable = safeKind === "none";
  const userCollapsed = safeKind === "sources" && state.contextPanelCollapsed;
  const hidden = unavailable || userCollapsed;
  state.contextPanel = safeKind;
  byId("sourcePanelView")?.classList.toggle("is-active", safeKind === "sources");
  byId("evidenceReaderPanel")?.classList.toggle("is-active", evidence);
  byId("reviewDocumentPanel")?.classList.toggle("is-active", review);
  const panel = byId("contextPanel");
  panel?.classList.toggle("is-hidden", hidden);
  if (panel) panel.setAttribute("aria-label", evidence ? "引用证据" : review ? "研究稿件" : preset.landmarkLabel);
  byId("conversationLayout")?.classList.toggle("is-review-workbench", review);
  byId("conversationLayout")?.classList.toggle("is-evidence-reading", evidence);
  byId("conversationLayout")?.classList.toggle("is-direct-conversation", unavailable);
  byId("conversationLayout")?.classList.toggle("is-context-collapsed", userCollapsed);
  const toggle = byId("contextPanelToggle");
  if (toggle) {
    const expanded = !hidden;
    const label = evidence ? "引用证据" : preset.toggleLabel;
    toggle.hidden = unavailable || review;
    toggle.classList.toggle("is-active", expanded);
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-label", `${expanded ? "隐藏" : "显示"}${label}`);
    toggle.title = `${expanded ? "隐藏" : "显示"}${label}`;
  }
  syncEvidencePanelExpansion();
  if (!review) byId("conversationLayout")?.classList.remove("is-review-focus");
}

function syncEvidencePanelExpansion() {
  const panel = byId("contextPanel");
  const canExpand = state.contextPanel === "evidence" && !panel?.classList.contains("is-hidden");
  if (!canExpand) state.evidencePanelExpanded = false;
  const expanded = canExpand && state.evidencePanelExpanded;
  byId("conversationLayout")?.classList.toggle("is-evidence-expanded", expanded);
  applyContextPanelWidth();
  const control = byId("evidencePanelExpand");
  if (!control) return;
  control.hidden = !canExpand;
  control.classList.toggle("is-active", expanded);
  control.setAttribute("aria-expanded", String(expanded));
  control.setAttribute("aria-label", expanded ? "收起全文证据面板" : "展开全文证据面板");
  control.title = expanded ? "收起全文证据面板" : "展开全文证据面板";
}

function toggleEvidencePanelExpanded() {
  const panel = byId("contextPanel");
  if (state.contextPanel !== "evidence" || panel?.classList.contains("is-hidden")) return;
  state.evidencePanelExpanded = !state.evidencePanelExpanded;
  if (state.evidencePanelExpanded && !window.localStorage.getItem("scansci.evidence-panel.width")) {
    setCurrentContextPanelWidth(Math.max(state.contextPanelWidth + 160, Math.round(maxContextPanelWidth() * 0.72)));
  }
  syncEvidencePanelExpansion();
  window.requestAnimationFrame(centerEvidenceReaderAnchor);
}

function toggleContextPanel() {
  if (state.contextPanel === "review" || state.contextPanel === "none") return;
  if (state.contextPanel === "evidence" && state.evidenceReturnPanel !== "sources") {
    closeEvidenceReader();
    return;
  }
  state.contextPanelCollapsed = !byId("contextPanel")?.classList.contains("is-hidden");
  window.localStorage.setItem("scansci.context-panel.collapsed", String(state.contextPanelCollapsed));
  setContextPanel("sources");
}

function renderTasks() {
  const target = byId("taskList");
  renderHistoryControls();
  const query = state.historyQuery.trim().toLowerCase();
  const archived = state.historyView === "archived";
  const availableRuns = state.runs.filter((run) => run.workflow_type !== "evidence_index" && Boolean(run.archived) === archived);
  const availableRecords = [
    ...availableRuns,
    ...(archived ? [] : state.directConversations),
  ].sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
  const records = availableRecords.filter((record) => {
    const searchable = record.kind === "direct"
      ? [record.title, record.preview, record.status, record.updated_at, "直接对话"]
      : [record.title, record.status, record.updated_at];
    return searchable.join(" ").toLowerCase().includes(query);
  });
  if (!availableRecords.length) {
    target.innerHTML = `<p class="history-empty">${archived ? "暂无归档对话" : "暂无对话"}</p>`;
    return;
  }
  if (!records.length) {
    target.innerHTML = '<p class="history-empty">没有匹配的历史对话</p>';
    return;
  }
  target.innerHTML = records.slice(0, 80).map((record) => {
    if (record.kind === "direct") {
      const conversationId = String(record.conversation_id || "");
      const active = !state.activeTaskId && state.directConversationId === conversationId;
      const job = directChatJob(conversationId);
      const menuKey = `direct:${conversationId}`;
      const open = state.historyMenuRunId === menuKey;
      const organizeAction = archived ? "restore-direct-conversation" : "archive-direct-conversation";
      const organizeLabel = archived ? "恢复到历史对话" : "归档对话";
      const organizeIcon = archived ? "archive-restore" : "archive";
      const persistedStatus = String(record.status || "completed");
      const queueCount = job ? job.queue.length : Math.max(0, Number(record.queue_count || 0));
      const recoverable = ["starting", "running", "retrying", "queued", "paused", "interrupted"].includes(persistedStatus);
      const statusClass = job
        ? (job.status === "paused" ? "paused" : job.status === "queued" ? "queued" : "running")
        : recoverable ? (persistedStatus === "paused" ? "paused" : "queued") : persistedStatus;
      const statusLabel = archived
        ? "已归档"
        : job
          ? job.pauseRequested ? "正在暂停" : job.status === "paused" ? "已暂停" : (queueCount ? `处理中 · 后续 ${queueCount}` : "处理中")
          : recoverable
            ? persistedStatus === "paused" ? (queueCount ? `已暂停 · 后续 ${queueCount}` : "已暂停") : (queueCount ? `待恢复 · 后续 ${queueCount}` : "待恢复")
            : persistedStatus === "failed" ? "失败" : persistedStatus === "cancelled" ? "已取消" : record.preview === "图片已生成并保存。" ? "已生成" : "已完成";
      const manageDisabled = Boolean(job);
      return `<div class="task-row ${open ? "has-open-menu" : ""}" data-conversation-id="${escapeHtml(conversationId)}"><button type="button" class="task-item ${active ? "is-active" : ""}" data-action="open-direct-conversation" data-conversation-id="${escapeHtml(conversationId)}"><span>${escapeHtml(compact(record.title || "直接对话", 28))}</span><time class="task-status ${statusClass}">${escapeHtml(statusLabel)}</time></button><button type="button" class="task-more" data-action="toggle-direct-menu" data-conversation-id="${escapeHtml(conversationId)}" aria-expanded="${open}" aria-label="管理对话" title="管理对话">${uiIcon("more-horizontal")}</button>${open ? `<div class="task-menu" role="menu"><button type="button" data-action="${organizeAction}" data-conversation-id="${escapeHtml(conversationId)}" ${manageDisabled ? "disabled" : ""}>${uiIcon(organizeIcon)}<span>${organizeLabel}</span></button><button type="button" class="is-danger" data-action="delete-direct-conversation" data-conversation-id="${escapeHtml(conversationId)}" ${manageDisabled ? "disabled" : ""}>${uiIcon("trash")}<span>删除对话</span></button>${manageDisabled ? '<small>运行结束后可整理</small>' : ""}</div>` : ""}</div>`;
    }
    const run = record;
    const open = state.historyMenuRunId === run.run_id;
    const manageDisabled = Boolean(run.cancellable || run.status === "needs_confirmation");
    const organizeAction = archived ? "restore-task" : "archive-task";
    const organizeLabel = archived ? "恢复到历史对话" : "归档对话";
    const organizeIcon = archived ? "archive-restore" : "archive";
    return `<div class="task-row ${open ? "has-open-menu" : ""}" data-task-id="${escapeHtml(run.run_id)}"><button type="button" class="task-item ${run.run_id === state.activeTaskId ? "is-active" : ""}" data-action="open-task" data-task-id="${escapeHtml(run.run_id)}"><span>${escapeHtml(compact(runDisplayTitle(run), 28))}</span><time class="task-status ${escapeHtml(run.status)}">${escapeHtml(runStatusLabel(run))}</time></button><button type="button" class="task-more" data-action="toggle-task-menu" data-task-id="${escapeHtml(run.run_id)}" aria-expanded="${open}" aria-label="管理对话" title="管理对话">${uiIcon("more-horizontal")}</button>${open ? `<div class="task-menu" role="menu"><button type="button" data-action="${organizeAction}" data-task-id="${escapeHtml(run.run_id)}" ${manageDisabled ? "disabled" : ""}>${uiIcon(organizeIcon)}<span>${organizeLabel}</span></button><button type="button" class="is-danger" data-action="delete-task" data-task-id="${escapeHtml(run.run_id)}" ${manageDisabled ? "disabled" : ""}>${uiIcon("trash")}<span>删除对话</span></button>${manageDisabled ? '<small>运行结束后可整理</small>' : ""}</div>` : ""}</div>`;
  }).join("");
}

function renderHistoryControls() {
  const area = byId("historyArea");
  const collapse = byId("historyCollapse");
  const searchTrigger = byId("historySearchTrigger");
  const searchPanel = byId("historySearchPanel");
  const search = byId("historySearch");
  const archiveTrigger = byId("historyArchiveTrigger");
  const historyTitle = byId("historyTitle");
  const archived = state.historyView === "archived";
  area?.classList.toggle("is-collapsed", state.historyCollapsed);
  collapse?.setAttribute("aria-expanded", String(!state.historyCollapsed));
  if (historyTitle) historyTitle.textContent = archived ? "已归档" : "历史对话";
  archiveTrigger?.classList.toggle("is-active", archived);
  archiveTrigger?.setAttribute("aria-pressed", String(archived));
  archiveTrigger?.setAttribute("aria-label", archived ? "返回历史对话" : "查看已归档对话");
  archiveTrigger?.setAttribute("title", archived ? "返回历史对话" : "查看已归档对话");
  if (searchPanel) searchPanel.hidden = state.historyCollapsed || !state.historySearchOpen;
  searchTrigger?.setAttribute("aria-expanded", String(!state.historyCollapsed && state.historySearchOpen));
  if (search && search.value !== state.historyQuery) search.value = state.historyQuery;
}

function toggleHistoryCollapse() {
  state.historyCollapsed = !state.historyCollapsed;
  if (state.historyCollapsed) state.historySearchOpen = false;
  window.localStorage.setItem("scansci.history.collapsed", String(state.historyCollapsed));
  renderTasks();
}

function toggleHistorySearch() {
  if (state.historyCollapsed) state.historyCollapsed = false;
  state.historySearchOpen = !state.historySearchOpen;
  if (!state.historySearchOpen) state.historyQuery = "";
  window.localStorage.setItem("scansci.history.collapsed", String(state.historyCollapsed));
  renderTasks();
  if (state.historySearchOpen) window.setTimeout(() => byId("historySearch")?.focus(), 0);
}

function toggleHistoryView() {
  state.historyView = state.historyView === "archived" ? "active" : "archived";
  state.historyMenuRunId = "";
  state.historyQuery = "";
  state.historySearchOpen = false;
  window.localStorage.setItem("scansci.history.view", state.historyView);
  renderTasks();
  refreshDirectConversations().catch((error) => toast(error.message, true));
}

async function refreshDirectConversations() {
  const view = state.historyView === "archived" ? "archived" : "active";
  const payload = await request(`/api/chat/history?view=${view}&limit=200`);
  state.directConversations = Array.isArray(payload.conversations) ? payload.conversations : [];
  renderTasks();
}

function toggleTaskMenu(runId) {
  state.historyMenuRunId = state.historyMenuRunId === runId ? "" : runId;
  renderTasks();
  if (state.historyMenuRunId) {
    positionTaskMenu();
    window.requestAnimationFrame(positionTaskMenu);
    window.setTimeout(positionTaskMenu, 0);
  }
}

function positionTaskMenu() {
  const list = byId("taskList");
  const row = list?.querySelector(".task-row.has-open-menu");
  const menu = row?.querySelector(".task-menu");
  if (!list || !row || !menu) return;
  const rowRect = row.getBoundingClientRect();
  const width = Math.max(220, Math.round(rowRect.width - 10));
  menu.style.width = `${Math.min(width, window.innerWidth - 24)}px`;
  menu.style.left = `${Math.max(12, Math.round(rowRect.left + 10))}px`;
  const neededHeight = menu.offsetHeight + 8;
  const opensDownward = rowRect.top < neededHeight + 12;
  menu.classList.toggle("opens-downward", opensDownward);
  menu.style.top = `${Math.round(opensDownward ? rowRect.bottom + 6 : rowRect.top - menu.offsetHeight - 6)}px`;
}

async function archiveTask(runId) {
  state.historyMenuRunId = "";
  const run = await request(`/api/runs/${encodeURIComponent(runId)}/archive`, { method: "POST", body: "{}" });
  upsertRun(run);
  if (state.activeTaskId === runId) startTask();
  toast("对话已归档");
}

async function restoreTask(runId) {
  state.historyMenuRunId = "";
  const run = await request(`/api/runs/${encodeURIComponent(runId)}/restore`, { method: "POST", body: "{}" });
  upsertRun(run);
  state.archivedRuns = state.archivedRuns.filter((item) => item.run_id !== runId);
  if (state.activeView === "settings" && state.activeSettings === "archive") renderSettings();
  toast("对话已恢复");
}

async function deleteTask(runId) {
  const run = state.runs.find((item) => item.run_id === runId);
  const title = compact(runDisplayTitle(run || {}), 36);
  const confirmed = await requestConfirmation({
    eyebrow: "永久删除",
    title: "删除这条对话？",
    subject: title,
    message: "此操作不可撤销，但已经导出的 PPTX、Markdown 和下载的论文文件会保留。",
    confirmLabel: "删除对话",
    danger: true,
  });
  if (!confirmed) return;
  state.historyMenuRunId = "";
  await request(`/api/runs/${encodeURIComponent(runId)}/delete`, { method: "POST", body: "{}" });
  window.localStorage.removeItem(`scansci.task.followup.${runId}`);
  state.runs = state.runs.filter((item) => item.run_id !== runId);
  state.archivedRuns = state.archivedRuns.filter((item) => item.run_id !== runId);
  if (state.activeTaskId === runId) startTask();
  else if (state.activeView === "settings" && state.activeSettings === "archive") renderSettings();
  else renderTasks();
  toast("对话已删除");
}

function toggleDirectMenu(conversationId) {
  const key = `direct:${String(conversationId || "")}`;
  state.historyMenuRunId = state.historyMenuRunId === key ? "" : key;
  renderTasks();
  if (state.historyMenuRunId) {
    positionTaskMenu();
    window.requestAnimationFrame(positionTaskMenu);
    window.setTimeout(positionTaskMenu, 0);
  }
}

async function archiveDirectConversation(conversationId) {
  const id = String(conversationId || "").trim();
  if (!id) return;
  if (directChatJob(id)) {
    toast("这个对话仍在处理，请先停止或等待完成。", true);
    return;
  }
  state.historyMenuRunId = "";
  await request(`/api/chat/history/${encodeURIComponent(id)}/archive`, { method: "POST", body: "{}" });
  state.directConversations = state.directConversations.filter((item) => item.conversation_id !== id);
  if (state.directConversationId === id) startTask();
  else renderTasks();
  toast("对话已归档");
}

async function restoreDirectConversation(conversationId) {
  const id = String(conversationId || "").trim();
  if (!id) return;
  state.historyMenuRunId = "";
  await request(`/api/chat/history/${encodeURIComponent(id)}/restore`, { method: "POST", body: "{}" });
  state.directConversations = state.directConversations.filter((item) => item.conversation_id !== id);
  state.archivedConversations = state.archivedConversations.filter((item) => item.conversation_id !== id);
  if (state.activeView === "settings" && state.activeSettings === "archive") renderSettings();
  else renderTasks();
  toast("对话已恢复");
}

async function deleteDirectConversation(conversationId) {
  const id = String(conversationId || "").trim();
  if (!id) return;
  if (directChatJob(id)) {
    toast("这个对话仍在处理，请先停止或等待完成。", true);
    return;
  }
  const record = state.directConversations.find((item) => item.conversation_id === id);
  const confirmed = await requestConfirmation({
    eyebrow: "永久删除",
    title: "删除这条对话？",
    subject: compact(record?.title || "直接对话", 36),
    message: "这会删除对话文本和附件引用，已导出的文件不会受到影响。",
    confirmLabel: "删除对话",
    danger: true,
  });
  if (!confirmed) return;
  state.historyMenuRunId = "";
  await request(`/api/chat/history/${encodeURIComponent(id)}/delete`, { method: "POST", body: "{}" });
  state.directConversations = state.directConversations.filter((item) => item.conversation_id !== id);
  state.archivedConversations = state.archivedConversations.filter((item) => item.conversation_id !== id);
  if (state.directConversationId === id) startTask();
  else if (state.activeView === "settings" && state.activeSettings === "archive") renderSettings();
  else renderTasks();
  toast("对话已删除");
}

function newDirectConversationId() {
  return globalThis.crypto?.randomUUID?.()
    || `direct-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function directChatJob(conversationId = state.directConversationId) {
  return directChatJobs.get(String(conversationId || "")) || null;
}

function createDirectChatJobState({
  conversationId,
  title,
  titleSource = "provisional",
  createdAt = new Date().toISOString(),
  sessionId = "",
  messages = [],
  queue = [],
  status = "starting",
  currentTurn = null,
  pausedTurn = null,
  pausedMessage = null,
  branchId = "",
  branchFromMessageId = "",
  branchReason = "",
  titleGenerationPending = false,
  firstQuestion = "",
  firstQuestionHasImages = false,
} = {}) {
  return {
    conversationId,
    title,
    titleSource,
    titleGenerationPending,
    titleGenerationStarted: false,
    firstQuestion,
    firstQuestionHasImages,
    createdAt,
    sessionId,
    messages,
    queue,
    controller: null,
    runId: "",
    status,
    currentStartedAt: 0,
    currentTurn,
    streamingMessage: null,
    runtimeTrace: [],
    interventionTrace: [],
    modelNoticeCodes: [],
    steeringPending: false,
    pendingSteer: null,
    steerAckTimer: 0,
    agentPhase: "",
    agentLifecycle: [],
    piQueue: { steering: [], follow_up: [], pending_count: 0 },
    lastAgentControl: null,
    restartForSteer: false,
    cancelRequested: false,
    pauseRequested: false,
    resumePending: false,
    pausedMessage,
    pausedTurn,
    sessionStats: null,
    persistChain: null,
    branchId,
    branchFromMessageId,
    branchReason,
  };
}

function syncActiveDirectChatState() {
  const job = directChatJob();
  if (job && !state.activeTaskId) {
    state.directMessages = job.messages;
    state.sessionId = job.sessionId || null;
    activeDirectChatController = job.controller || null;
    state.activeStreamRunId = job.runId || "";
    state.streaming = ["starting", "running", "retrying", "queued"].includes(job.status);
  } else {
    activeDirectChatController = null;
    state.activeStreamRunId = "";
    state.streaming = false;
  }
  renderDirectLiveControls();
}

function activeResearchRun() {
  return state.activeTaskId
    ? state.runs.find((item) => item.run_id === state.activeTaskId) || null
    : null;
}

function composerSendControlState() {
  const run = activeResearchRun();
  if (run) {
    if (run.status === "paused" && !run.pause_requested) return { state: "paused", icon: "play", label: "继续任务" };
    if (run.pause_requested) return { state: "pausing", icon: "square", label: "正在暂停任务" };
    if (["queued", "planning", "running", "verifying"].includes(String(run.status || ""))) {
      return { state: "running", icon: "square", label: "暂停当前任务" };
    }
  }
  const job = !state.activeTaskId ? directChatJob() : null;
  if (job) {
    if (job.status === "paused" && !job.pauseRequested) return { state: "paused", icon: "play", label: "继续回复" };
    if (job.pauseRequested) return { state: "pausing", icon: "square", label: "正在暂停回复" };
    if (["starting", "running", "retrying", "queued"].includes(job.status)) {
      const queuedInput = String(byId("chatQuestionInput")?.value || "").trim();
      if (queuedInput) return { state: "queueing", icon: "send", label: "加入后续队列" };
      return { state: "running", icon: "square", label: "暂停当前回复" };
    }
  }
  if (composerSubmissionInFlight) return { state: "running", icon: "square", label: "暂停当前操作" };
  return { state: "idle", icon: "send", label: selectedComposerImageModel() ? "生成图片" : "发送问题" };
}

function renderComposerSendButtons() {
  const control = composerSendControlState();
  const imageMode = Boolean(selectedComposerImageModel());
  const rename = byId("renameConversationButton");
  if (rename) rename.hidden = Boolean(state.activeTaskId || !state.directConversationId || directChatJob());
  document.querySelectorAll("#homeAskForm, #chatAskForm").forEach((form) => {
    form.classList.toggle("is-image-mode", imageMode);
    const button = form.querySelector("button[type='submit']");
    if (!button) return;
    button.dataset.composerState = control.state;
    const imageSend = imageMode && control.state === "idle";
    button.classList.toggle("is-image-send", imageSend);
    button.innerHTML = imageSend ? "生成" : uiIcon(control.icon);
    button.setAttribute("aria-label", control.label);
    button.setAttribute("title", control.label);
    button.setAttribute("aria-busy", String(control.state === "pausing"));
    button.disabled = control.state === "pausing";
    const input = form.querySelector("textarea");
    if (input && input.offsetParent) {
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
    }
  });
}

function directTurnConfiguration({
  question,
  key,
  directChatMode,
  selectedKnowledge = [],
  selectedSkillIds = [],
  selectedSkills = [],
  images = [],
  audio = [],
  sourceFiles = [],
} = {}) {
  const knowledgeScopes = selectedKnowledge.map((notebook) => ({
    notebook_id: String(notebook.notebook_id),
    title: knowledgeScopeTitle(notebook),
  }));
  return {
    queueId: globalThis.crypto?.randomUUID?.() || `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    question: String(question || "").trim(),
    key: key === "home" ? "home" : "chat",
    directChatMode,
    model: modelIdentitySnapshot(),
    imageModel: selectedComposerImageModel(),
    selectedSkillIds: [...selectedSkillIds],
    selectedSkills: [...selectedSkills],
    images: [...images],
    audio: [...audio],
    sourceFiles: [...sourceFiles],
    knowledgeScopes,
    notebookId: selectedKnowledge[0]?.notebook_id || "",
    notebookIds: selectedKnowledge.map((notebook) => notebook.notebook_id),
    knowledgeScope: activeKnowledgeScopePayload() || null,
    knowledgeScopePayloads: selectedKnowledge.length ? activeKnowledgeScopePayloads() : [],
    thinkingLevel: currentThinkingLevel(),
    webSearch: state.webSearchMode,
    agentMode: state.agentMode && !selectedComposerImageModel(),
    deliveryMode: "follow-up",
    createdAt: new Date().toISOString(),
  };
}

function generalPreferences() {
  const source = state.settings?.general || {};
  const conversation = source.conversation && typeof source.conversation === "object" ? source.conversation : {};
  const directories = source.directories && typeof source.directories === "object" ? source.directories : {};
  const updates = source.updates && typeof source.updates === "object" ? source.updates : {};
  return {
    conversation: {
      send_shortcut: conversation.send_shortcut === "shift-enter" ? "shift-enter" : "enter",
      completion_notifications: conversation.completion_notifications !== false,
      agent_completion_notifications: conversation.agent_completion_notifications !== false,
      subagent_completion_notifications: conversation.subagent_completion_notifications === true,
    },
    directories: {
      default_workspace: String(directories.default_workspace || "").trim(),
      conversation_workspace: String(directories.conversation_workspace || "").trim(),
      model_cache: String(directories.model_cache || "").trim(),
      local_runtime: String(directories.local_runtime || "").trim(),
      vector_index: String(directories.vector_index || "").trim(),
    },
    updates: {
      auto_check: updates.auto_check !== false,
    },
  };
}

function workspaceDirectoryFromFilePath(value) {
  const raw = String(value || "").trim();
  if (!raw || raw.startsWith("使用应用默认")) return "";
  const normalized = raw.replace(/[\\/]+$/, "");
  const separator = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
  if (separator < 0) return normalized;
  if (separator === 2 && normalized[1] === ":") return normalized.slice(0, 3);
  return normalized.slice(0, separator) || normalized;
}

function clearSubmittedDirectComposer(turn, input = null) {
  const key = turn?.key === "home" ? "home" : "chat";
  const target = input || byId(key === "home" ? "homeQuestionInput" : "chatQuestionInput");
  if (target) {
    target.value = "";
    target.dispatchEvent(new Event("input", { bubbles: true }));
  }
  clearComposerSkills(key);
  clearComposerImages(key);
  clearComposerAudio(key);
  clearComposerSources(key);
}

function directJobSummary(job) {
  return {
    kind: "direct",
    conversation_id: job.conversationId,
    title: job.title,
    preview: [...job.messages].reverse().find((message) => String(message?.content || "").trim())?.content || job.title,
    created_at: job.createdAt,
    updated_at: new Date().toISOString(),
    message_count: job.messages.filter((message) => !message.streaming).length,
    session_id: job.sessionId || "",
    title_source: job.titleSource || "provisional",
    status: job.status || "completed",
    queue_count: Array.isArray(job.queue) ? job.queue.length : 0,
  };
}

function directConversationTitle(messages = state.directMessages) {
  const firstUser = (messages || []).find((message) => message?.role === "user" && String(message.content || "").trim());
  const firstLine = String(firstUser?.content || "直接对话").split(/\r?\n/, 1)[0];
  const firstClause = firstLine.split(/[。！？!?；;]/, 1)[0];
  return compact(firstClause || firstLine || "直接对话", 24);
}

function ensureDirectMessageIds(messages, conversationId) {
  return (Array.isArray(messages) ? messages : []).map((message, index) => {
    if (String(message?.message_id || "").trim()) return message;
    const role = String(message?.role || "message");
    return {
      ...message,
      message_id: `${conversationId || "direct"}-legacy-${index + 1}-${role}`,
    };
  });
}

function persistedDirectTurn(raw) {
  if (!raw || typeof raw !== "object" || !String(raw.question || "").trim()) return null;
  return {
    queueId: String(raw.queueId || (globalThis.crypto?.randomUUID?.() || `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`)),
    question: String(raw.question || "").trim(),
    key: raw.key === "home" ? "home" : "chat",
    directChatMode: String(raw.directChatMode || "general"),
    model: raw.model?.provider_id && raw.model?.model_id
      ? { provider_id: String(raw.model.provider_id), model_id: String(raw.model.model_id) }
      : null,
    imageModel: raw.imageModel || null,
    selectedSkillIds: Array.isArray(raw.selectedSkillIds) ? raw.selectedSkillIds : [],
    selectedSkills: Array.isArray(raw.selectedSkills) ? raw.selectedSkills : [],
    images: Array.isArray(raw.images) ? raw.images : [],
    audio: Array.isArray(raw.audio) ? raw.audio : [],
    sourceFiles: Array.isArray(raw.sourceFiles) ? raw.sourceFiles : [],
    knowledgeScopes: Array.isArray(raw.knowledgeScopes) ? raw.knowledgeScopes : [],
    notebookId: String(raw.notebookId || ""),
    notebookIds: Array.isArray(raw.notebookIds) ? raw.notebookIds : [],
    knowledgeScope: raw.knowledgeScope || null,
    knowledgeScopePayloads: Array.isArray(raw.knowledgeScopePayloads) ? raw.knowledgeScopePayloads : [],
    thinkingLevel: String(raw.thinkingLevel || currentThinkingLevel()),
    webSearch: ["auto", "on", "off"].includes(raw.webSearch) ? raw.webSearch : "auto",
    agentMode: raw.agentMode === true,
    deliveryMode: raw.deliveryMode === "steer" ? "steer" : "follow-up",
    createdAt: String(raw.createdAt || new Date().toISOString()),
  };
}

function directHistoryMessage(message) {
  const copy = { ...(message || {}) };
  delete copy.streaming;
  delete copy.control_run_id;
  if (Array.isArray(copy.images)) {
    copy.images = copy.images.map((image) => ({
      ...(image?.id ? { id: image.id } : {}),
      name: image?.name || "用户图片",
      ...(image?.mime_type ? { mime_type: image.mime_type } : {}),
      ...(image?.type ? { type: image.type } : {}),
      ...(image?.size ? { size: image.size } : {}),
      ...(image?.preview_url ? { preview_url: image.preview_url } : {}),
    }));
  }
  if (Array.isArray(copy.audio)) {
    copy.audio = copy.audio.map((audio) => ({
      ...(audio?.id ? { id: audio.id } : {}),
      name: audio?.name || "语音",
      ...(audio?.mime_type ? { mime_type: audio.mime_type } : {}),
      ...(audio?.size ? { size: audio.size } : {}),
      ...(audio?.audio_url ? { audio_url: audio.audio_url } : {}),
    }));
  }
  if (Array.isArray(copy.sources)) {
    copy.sources = copy.sources.map((source) => ({
      ...(source?.name ? { name: source.name } : {}),
      ...(source?.title ? { title: source.title } : {}),
      ...(source?.doc_id ? { doc_id: source.doc_id } : {}),
      ...(source?.doi ? { doi: source.doi } : {}),
      ...(source?.file_url ? { file_url: source.file_url } : {}),
      ...(source?.job_id ? { job_id: source.job_id } : {}),
      ...(source?.source_id ? { source_id: source.source_id } : {}),
      ...(source?.suffix ? { suffix: source.suffix } : {}),
      ...(source?.size ? { size: source.size } : {}),
    }));
  }
  return copy;
}

function upsertDirectConversation(record) {
  if (!record?.conversation_id) return;
  const messages = Array.isArray(record.messages) ? record.messages : [];
  const preview = String(record.preview || [...messages].reverse().find((message) => message?.content)?.content || "");
  const summary = {
    kind: "direct",
    conversation_id: String(record.conversation_id),
    title: String(record.title || directConversationTitle(messages)),
    preview: compact(preview, 180),
    created_at: String(record.created_at || new Date().toISOString()),
    updated_at: String(record.updated_at || new Date().toISOString()),
    message_count: Number(record.message_count || messages.length || 0),
    session_id: String(record.session_id || ""),
    title_source: String(record.title_source || ""),
    model: record.model || null,
    status: String(record.status || "completed"),
    queue_count: Math.max(0, Number(record.queue_count || 0)),
  };
  state.directConversations = [
    summary,
    ...state.directConversations.filter((item) => item.conversation_id !== summary.conversation_id),
  ].sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)));
  renderTasks();
}

async function persistDirectConversationSnapshot({
  conversationId = state.directConversationId,
  messages: sourceMessages = state.directMessages,
  sessionId = state.sessionId,
  title = "",
  titleSource = "",
  queue = [],
  status = "completed",
  currentTurn = null,
  branchId = "",
  branchFromMessageId = "",
  branchReason = "",
} = {}) {
  const messages = sourceMessages
    .filter((message) => message && (!message.streaming || message.artifacts?.length))
    .map(directHistoryMessage);
  if (!messages.length) return null;
  const id = String(conversationId || newDirectConversationId());
  if (id === state.directConversationId) window.localStorage.setItem("scansci.active.direct", id);
  const saved = await request("/api/chat/history", {
    method: "POST",
    body: JSON.stringify({
      conversation_id: id,
      title: title || directConversationTitle(messages),
      title_source: titleSource || "",
      session_id: sessionId || "",
      messages,
      queue,
      status,
      current_turn: currentTurn,
      branch_id: branchId,
      branch_from_message_id: branchFromMessageId,
      branch_reason: branchReason,
    }),
  });
  upsertDirectConversation(saved);
  return saved;
}

async function persistDirectConversation() {
  return persistDirectConversationSnapshot();
}

function persistDirectChatJob(job) {
  const snapshot = {
    conversationId: job.conversationId,
    messages: [...job.messages],
    sessionId: job.sessionId,
    title: job.title,
    titleSource: job.titleSource || "provisional",
    queue: job.queue.map((turn) => ({ ...turn })),
    status: job.status,
    currentTurn: job.currentTurn ? { ...job.currentTurn } : job.pausedTurn ? { ...job.pausedTurn } : null,
    branchId: job.branchId,
    branchFromMessageId: job.branchFromMessageId,
    branchReason: job.branchReason,
  };
  const pending = (job.persistChain || Promise.resolve())
    .catch(() => {})
    .then(() => persistDirectConversationSnapshot(snapshot));
  job.persistChain = pending;
  return pending;
}

async function stageDirectTurnAttachments(turn) {
  if (!turn) return turn;
  const hasAttachments = turn.images.length || turn.audio.length || turn.sourceFiles.length;
  if (!hasAttachments) return turn;
  const staged = await request("/api/chat/attachments/stage", {
    method: "POST",
    body: JSON.stringify({
      images: turn.images,
      audio: turn.audio,
      source_files: turn.sourceFiles,
    }),
  });
  turn.images = Array.isArray(staged.images) ? staged.images : [];
  turn.audio = Array.isArray(staged.audio) ? staged.audio : [];
  turn.sourceFiles = Array.isArray(staged.source_files) ? staged.source_files : [];
  return turn;
}

async function generateDirectConversationTitle(job, firstQuestion, hasImages = false) {
  if (!job || job.titleGenerationStarted || job.titleSource === "user") return;
  if (!String(firstQuestion || "").trim() && !hasImages) return;
  job.titleGenerationStarted = true;
  try {
    const result = await request("/api/chat/title", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: job.conversationId,
        question: String(firstQuestion || ""),
        has_images: Boolean(hasImages),
        model: job.messages.find((message) => message?.role === "assistant")?.model,
      }),
    });
    const title = compact(String(result?.title || "").trim(), 120);
    if (!title) return;
    const titleSource = String(result?.source || "model");
    await request("/api/chat/history/" + encodeURIComponent(job.conversationId) + "/title", {
      method: "POST",
      body: JSON.stringify({ title, title_source: titleSource }),
    });
    job.title = title;
    job.titleSource = titleSource;
    upsertDirectConversation(directJobSummary(job));
    if (String(state.directConversationId || "") === String(job.conversationId)) {
      const heading = byId("conversationTitle");
      if (heading) heading.textContent = compact(title, 80);
    }
  } catch (error) {
    // The provisional first-line title remains useful when the naming call is
    // unavailable. A title request must never turn a completed chat into an
    // error state.
    console.warn("Direct conversation title generation was unavailable", error);
  }
}

function queueDirectChatTurn(job, turn, { front = false, announce = true } = {}) {
  if (!job || !turn?.question) return;
  turn.deliveryMode = turn.deliveryMode === "steer" ? "steer" : "follow-up";
  if (front) job.queue.unshift(turn);
  else job.queue.push(turn);
  if (announce) toast(`已加入后续队列（${job.queue.length}）`);
  renderDirectLiveControls();
  renderTasks();
  persistDirectChatJob(job).catch((error) => console.warn("Direct chat queue could not be saved", error));
}

function beginEditingQueuedDirectTurn(queueId) {
  const job = directChatJob();
  const id = String(queueId || "");
  const turn = job?.queue.find((item) => item.queueId === id);
  if (!job || !turn) return;
  directQueueEdit = { conversationId: job.conversationId, queueId: id, value: String(turn.question || "") };
  renderDirectLiveControls();
  window.setTimeout(() => {
    const input = document.querySelector("[data-queue-edit-input]");
    input?.focus({ preventScroll: true });
    input?.select();
  }, 0);
}

function cancelEditingQueuedDirectTurn() {
  if (!directQueueEdit) return;
  directQueueEdit = null;
  renderDirectLiveControls();
}

function saveEditingQueuedDirectTurn() {
  const edit = directQueueEdit;
  if (!edit) return;
  const job = directChatJobs.get(edit.conversationId);
  const turn = job?.queue.find((item) => item.queueId === edit.queueId);
  const question = String(edit.value || "").trim();
  if (!job || !turn) {
    directQueueEdit = null;
    renderDirectLiveControls();
    return;
  }
  if (!question) {
    toast("队列消息不能为空", true);
    return;
  }
  turn.question = question;
  directQueueEdit = null;
  toast("队列消息已修改");
  renderDirectLiveControls();
  renderTasks();
  persistDirectChatJob(job).catch((error) => console.warn("Edited direct chat queue could not be saved", error));
}

async function steerDirectChat(job, turn) {
  if (!job?.runId || !turn?.question || turn.audio.length || turn.sourceFiles.length) return false;
  try {
    const result = await request("/api/chat/steer", {
      method: "POST",
      body: JSON.stringify({ run_id: job.runId, text: turn.question, images: turn.images }),
    });
    if (!result?.ok) return false;
    job.pendingSteer = turn;
    const traceItem = { title: "调整请求已发送", detail: turn.question, status: "pending" };
    job.interventionTrace.push(traceItem);
    if (job.streamingMessage) {
      job.streamingMessage.trace = [
        ...(job.runtimeTrace || []),
        ...job.interventionTrace,
      ];
    }
    scheduleDirectConversationRender(job.conversationId);
    window.clearTimeout(job.steerAckTimer);
    job.steerAckTimer = window.setTimeout(() => {
      if (job.pendingSteer === turn) fallbackSteerDirectChat(job, turn, { reason: "ack_timeout" });
    }, 2500);
    return true;
  } catch (error) {
    console.warn("Direct chat steering was unavailable", error);
    return false;
  }
}

function fallbackSteerDirectChat(job, turn, { reason = "unsupported" } = {}) {
  if (!job || !turn) return;
  window.clearTimeout(job.steerAckTimer);
  job.steerAckTimer = 0;
  job.pendingSteer = null;
  job.steeringPending = false;
  queueDirectChatTurn(job, turn, { front: true, announce: false });
  // Without Pi's acknowledgement we cannot safely claim an in-flight
  // instruction was accepted. Restart only this turn and preserve the new
  // instruction as its own user-visible turn.
  job.restartForSteer = true;
  job.status = "queued";
  const runId = job.runId;
  job.controller?.abort();
  if (runId) {
    request("/api/chat/cancel", {
      method: "POST",
      body: JSON.stringify({ run_id: runId }),
    }).catch(() => {});
  }
  toast(reason === "rejected"
    ? "当前 Pi 会话未接受调整；已停止本轮并按新方向继续。"
    : "当前回复未确认原生调整；已停止本轮并按新方向继续。", true);
  renderDirectLiveControls();
}

async function submitToRunningDirectChat(job, turn, input) {
  clearSubmittedDirectComposer(turn, input);
  queueDirectChatTurn(job, turn);
}

function beginDirectChatJob(turn) {
  const conversationId = String(state.directConversationId || newDirectConversationId());
  const existingMessages = ensureDirectMessageIds(
    state.directMessages.filter((message) => message && !message.streaming),
    conversationId,
  );
  const hasExistingUserMessage = existingMessages.some((message) => message?.role === "user");
  const existingConversation = state.directConversations.find((item) => item.conversation_id === conversationId) || null;
  const titleGenerationPending = !turn.imageModel && !hasExistingUserMessage && !existingConversation && !turn.branchId;
  const title = String(
    existingConversation?.title
      || directConversationTitle([...existingMessages, { role: "user", content: turn.question }]),
  );
  const job = createDirectChatJobState({
    conversationId,
    title,
    titleSource: String(existingConversation?.title_source || "provisional"),
    createdAt: new Date().toISOString(),
    sessionId: state.sessionId || "",
    messages: existingMessages,
    status: "starting",
    branchId: String(turn.branchId || ""),
    branchFromMessageId: String(turn.branchFromMessageId || ""),
    branchReason: String(turn.branchReason || ""),
    titleGenerationPending,
    firstQuestion: titleGenerationPending ? String(turn.question || "") : "",
    firstQuestionHasImages: titleGenerationPending && Array.isArray(turn.images) && turn.images.length > 0,
  });
  directChatJobs.set(conversationId, job);
  state.directConversationId = conversationId;
  state.activeTaskId = "";
  state.directMessages = job.messages;
  window.localStorage.setItem("scansci.active.direct", conversationId);
  upsertDirectConversation(directJobSummary(job));
  syncActiveDirectChatState();
  void runDirectChatTurn(job, turn);
  return job;
}

function restoreDirectChatJob(conversation) {
  const conversationId = String(conversation?.conversation_id || "").trim();
  if (!conversationId) return null;
  const existing = directChatJob(conversationId);
  if (existing) return existing;
  const queue = (Array.isArray(conversation.queue) ? conversation.queue : [])
    .map(persistedDirectTurn)
    .filter(Boolean);
  const currentTurn = persistedDirectTurn(conversation.current_turn);
  const recoverable = ["starting", "running", "retrying", "queued", "paused", "interrupted"].includes(
    String(conversation.status || ""),
  );
  if (!recoverable && !queue.length) return null;
  const pausedTurn = currentTurn || queue.shift() || null;
  if (!pausedTurn) return null;
  const messages = ensureDirectMessageIds(
    (Array.isArray(conversation.messages) ? conversation.messages : []).filter((message) => message && !message.streaming),
    conversationId,
  );
  const pausedMessage = currentTurn
    ? [...messages].reverse().find((message) => message?.role === "assistant" && message?.paused) || null
    : null;
  const job = createDirectChatJobState({
    conversationId,
    title: String(conversation.title || directConversationTitle(messages)),
    titleSource: String(conversation.title_source || "provisional"),
    createdAt: String(conversation.created_at || new Date().toISOString()),
    sessionId: String(conversation.session_id || ""),
    messages,
    queue,
    status: "paused",
    currentTurn: pausedTurn,
    pausedTurn,
    pausedMessage,
  });
  directChatJobs.set(conversationId, job);
  upsertDirectConversation(directJobSummary(job));
  return job;
}

async function runDirectChatTurn(job, turn) {
  if (!job || job.cancelRequested || job.pauseRequested || !turn?.question) return;
  const selectedModel = modelIdentitySnapshot(turn.model);
  job.status = "starting";
  job.currentTurn = turn;
  job.runId = "";
  job.runtimeTrace = [];
  job.interventionTrace = [];
  job.modelNoticeCodes = [];
  window.clearTimeout(job.steerAckTimer);
  job.steerAckTimer = 0;
  job.pendingSteer = null;
  job.steeringPending = false;
  job.agentPhase = "";
  job.agentLifecycle = [];
  job.piQueue = { steering: [], follow_up: [], pending_count: 0 };
  job.lastAgentControl = null;
  const priorMessages = job.messages.filter((message) => !message.streaming);
  const priorAssistantModel = [...priorMessages].reverse().find((message) => message.role === "assistant")?.model;
  if (priorAssistantModel?.provider_id && priorAssistantModel?.model_id && (
    priorAssistantModel.provider_id !== selectedModel.provider_id || priorAssistantModel.model_id !== selectedModel.model_id
  )) job.sessionId = "";
  const parentMessage = priorMessages[priorMessages.length - 1] || null;
  const userMessage = {
    message_id: turn.queueId,
    ...(parentMessage?.message_id ? { parent_message_id: parentMessage.message_id } : {}),
    role: "user",
    content: turn.question,
    mode: turn.directChatMode,
    agent_mode: turn.agentMode === true,
    image_model: turn.imageModel || null,
    knowledgeScopes: turn.knowledgeScopes,
    skills: turn.selectedSkills,
    sources: turn.sourceFiles,
    images: turn.images,
    audio: turn.audio,
    created_at: turn.createdAt || new Date().toISOString(),
  };
  const promptMessages = [...priorMessages, userMessage];
  const startedAt = performance.now();
  const streamingMessage = {
    message_id: `assistant-${turn.queueId}`,
    parent_message_id: userMessage.message_id,
    role: "assistant",
    content: "",
    streaming: true,
    processing_started_at: startedAt,
    mode: turn.directChatMode,
    trace: [],
    reasoning: "",
    knowledgeScopes: turn.knowledgeScopes,
    model: selectedModel,
    created_at: new Date().toISOString(),
  };
  job.currentStartedAt = startedAt;
  job.streamingMessage = streamingMessage;
  job.messages = [...promptMessages, streamingMessage];
  job.controller = new AbortController();
  job.status = "running";
  if (job.conversationId === state.directConversationId && !state.activeTaskId) {
    state.directMessages = job.messages;
    state.conversationAutoFollow = true;
    syncActiveDirectChatState();
    renderDirectConversation({ forceFollow: true });
  }
  upsertDirectConversation(directJobSummary(job));
  renderTasks();
  let completed = false;
  try {
    await persistDirectChatJob(job);
    if (job.pauseRequested) {
      const stopped = new Error("已暂停");
      stopped.name = "AbortError";
      throw stopped;
    }
    await streamChatWithRecovery(
      {
        messages: promptMessages,
        idempotency_key: turn.queueId,
        images: turn.images,
        audio: turn.audio,
        source_files: turn.sourceFiles,
        thinking_level: turn.thinkingLevel,
        chat_mode: turn.directChatMode,
        model: { provider_id: selectedModel.provider_id, model_id: selectedModel.model_id },
        agent_mode: turn.agentMode === true,
        ...(turn.imageModel ? { image_model: turn.imageModel, image_request_id: turn.queueId } : {}),
        web_search: turn.webSearch,
        ...(turn.notebookId ? { notebook_id: turn.notebookId } : {}),
        ...(turn.notebookIds.length ? { notebook_ids: turn.notebookIds } : {}),
        ...(turn.knowledgeScope ? { knowledge_scope: turn.knowledgeScope } : {}),
        ...(turn.knowledgeScopePayloads.length ? { knowledge_scopes: turn.knowledgeScopePayloads } : {}),
        conversation_id: job.conversationId,
        session_id: job.sessionId || "",
        pi_session_id: job.sessionId || "",
        skills: turn.selectedSkillIds,
      },
      (eventType, payload) => {
        if (eventType === "RUN_STARTED") {
          job.runId = String(payload.runId || payload.run_id || "");
          streamingMessage.control_run_id = job.runId;
          syncActiveDirectChatState();
          renderDirectLiveControls();
          return;
        }
        if (eventType === "delta" || eventType === "TEXT_MESSAGE_CONTENT") {
          streamingMessage.content += String(payload.content || payload.delta || "");
          scheduleDirectConversationRender(job.conversationId);
          return;
        }
        if (eventType === "CUSTOM" && payload.name === "model_reasoning") {
          streamingMessage.reasoning = payload.value?.reset ? "" : streamingMessage.reasoning + String(payload.value?.delta || "");
          scheduleDirectConversationRender(job.conversationId);
          return;
        }
        if (
          eventType === "retry"
          || (eventType === "status" && String(payload.subtype || "") === "retry")
        ) {
          const attempt = Number(payload.attempt || 0);
          const delaySeconds = Math.max(
            0,
            Number(payload.delay_seconds || 0) || Number(payload.delay_ms || 0) / 1000,
          );
          job.status = "retrying";
          job.agentPhase = attempt ? `正在恢复模型连接（第 ${attempt} 次）` : "正在恢复模型连接";
          job.runtimeTrace = [
            ...job.runtimeTrace.filter((item) => item.status !== "retrying"),
            {
              title: "正在恢复模型连接",
              detail: delaySeconds > 0
                ? `服务暂时拥堵，${delaySeconds.toFixed(0)} 秒后自动重试。`
                : "服务暂时拥堵，正在自动重试。",
              status: "retrying",
            },
          ].slice(-8);
          streamingMessage.trace = [...job.runtimeTrace, ...job.interventionTrace];
          scheduleDirectConversationRender(job.conversationId, { forceFollow: true });
          renderDirectLiveControls();
          return;
        }
        if (eventType === "compatibility.fallback" || eventType === "model.fallback") {
          const toModel = String(payload.to_model_name || payload.to_model || "").trim();
          job.agentPhase = toModel ? `正在切换到 ${toModel}` : "正在切换可用模型通道";
          job.runtimeTrace = [
            ...job.runtimeTrace,
            {
              title: eventType === "model.fallback" ? "正在切换模型" : "正在切换兼容通道",
              detail: toModel
                ? `当前模型暂时不可用，ScanSci 正在尝试 ${toModel}。`
                : "当前模型协议或连接暂时不可用，ScanSci 正在尝试有界回退。",
              status: "retrying",
            },
          ].slice(-8);
          streamingMessage.trace = [...job.runtimeTrace, ...job.interventionTrace];
          scheduleDirectConversationRender(job.conversationId, { forceFollow: true });
          renderDirectLiveControls();
          return;
        }
        if (eventType === "session") {
          job.sessionId = String(payload.session_id || job.sessionId || "");
          if (job.conversationId === state.directConversationId && job.sessionId) {
            state.sessionId = job.sessionId;
            window.localStorage.setItem("scansci.active.session", job.sessionId);
          }
          return;
        }
        if (eventType === "STEP_FINISHED" && payload.stepName === "ingest_attachments" && payload.result?.sources) {
          userMessage.sources = payload.result.sources;
          scheduleDirectConversationRender(job.conversationId);
          return;
        }
        if (eventType === "CUSTOM" && payload.name === "usage") {
          streamingMessage.usage = payload.value || {};
          return;
        }
        if (eventType === "CUSTOM" && payload.name === "session_stats") {
          job.sessionStats = payload.value || {};
          if (job.conversationId === state.directConversationId) updateSessionStats(job.sessionStats);
          return;
        }
        if (eventType === "CUSTOM" && payload.name === "model_notice") {
          const notice = payload.value && typeof payload.value === "object" ? payload.value : {};
          const code = String(notice.code || "model-advisory");
          if (!Array.isArray(job.modelNoticeCodes)) job.modelNoticeCodes = [];
          if (!job.modelNoticeCodes.includes(code)) {
            job.modelNoticeCodes.push(code);
            const message = String(notice.message || "当前模型有已知能力边界，请核对最终结果。");
            toast(`模型提示：${message}`);
          }
          return;
        }
        if (eventType === "CUSTOM" && payload.name === "agent_lifecycle") {
          const lifecycle = payload.value || {};
          job.agentPhase = String(lifecycle.event || "");
          job.agentLifecycle = [...job.agentLifecycle, lifecycle].slice(-64);
          renderDirectLiveControls();
          return;
        }
        if (eventType === "CUSTOM" && payload.name === "agent_queue") {
          job.piQueue = payload.value || { steering: [], follow_up: [], pending_count: 0 };
          renderDirectLiveControls();
          return;
        }
        if (eventType === "CUSTOM" && payload.name === "agent_control") {
          const control = payload.value || {};
          job.lastAgentControl = control;
          if (control.action === "steer" && job.pendingSteer) {
            const pendingTurn = job.pendingSteer;
            window.clearTimeout(job.steerAckTimer);
            job.steerAckTimer = 0;
            if (control.status === "accepted") {
              job.pendingSteer = null;
              job.steeringPending = false;
              job.interventionTrace.push({ title: "已调整当前回复", detail: pendingTurn.question, status: "accepted" });
              if (job.streamingMessage) {
                job.streamingMessage.trace = [...job.runtimeTrace, ...job.interventionTrace];
              }
              toast("Pi 已接受本轮调整");
              scheduleDirectConversationRender(job.conversationId);
            } else {
              fallbackSteerDirectChat(job, pendingTurn, { reason: "rejected" });
            }
          }
          renderDirectLiveControls();
          return;
        }
        if (eventType === "CUSTOM" && payload.name === "generated_image") {
          const artifact = payload.value;
          if (artifact?.artifact_type === "image" && generatedImageArtifactsMarkup([artifact])) {
            streamingMessage.artifacts = [...(streamingMessage.artifacts || []), artifact];
            scheduleDirectConversationRender(job.conversationId);
            persistDirectChatJob(job).catch((error) => console.warn("Generated image history could not be saved", error));
          }
          return;
        }
        if (eventType === "CUSTOM" && payload.name === "process_trace") {
          job.runtimeTrace = Array.isArray(payload.value) ? payload.value : [];
          streamingMessage.trace = [...job.runtimeTrace, ...job.interventionTrace];
          scheduleDirectConversationRender(job.conversationId);
          return;
        }
        if (eventType === "CUSTOM" && payload.name === "interaction") {
          streamingMessage.interaction = payload.value || null;
          scheduleDirectConversationRender(job.conversationId);
          return;
        }
        if (eventType === "done" || eventType === "RUN_FINISHED") {
          const result = payload.result || payload;
          if (Array.isArray(result.user_images)) userMessage.images = result.user_images;
          const message = {
            ...result.message,
            message_id: result.message?.message_id || streamingMessage.message_id,
            parent_message_id: result.message?.parent_message_id || userMessage.message_id,
            model: modelIdentitySnapshot(result.model || result.message?.model || payload.model || streamingMessage.model),
            mode: turn.directChatMode,
            knowledgeScopes: turn.knowledgeScopes,
            agent_runtime: result.agent_runtime || null,
            usage: result.message?.usage || streamingMessage.usage,
            artifacts: result.message?.artifacts || streamingMessage.artifacts,
            trace: result.message?.trace || streamingMessage.trace,
            reasoning: result.message?.reasoning || streamingMessage.reasoning,
            reasoning_open: streamingMessage.reasoning_open,
            created_at: result.message?.created_at || new Date().toISOString(),
            processing_ms: Math.max(0, Math.round(performance.now() - startedAt)),
          };
          job.sessionStats = result.stats || result.agent_runtime?.session_stats || payload.stats || job.sessionStats;
          if (job.conversationId === state.directConversationId) updateSessionStats(job.sessionStats || null);
          if (result.agent_runtime?.session?.session_id) {
            job.sessionId = String(result.agent_runtime.session.session_id);
            if (job.conversationId === state.directConversationId) {
              state.sessionId = job.sessionId;
              window.localStorage.setItem("scansci.active.session", job.sessionId);
            }
          }
          const messageIndex = job.messages.indexOf(streamingMessage);
          if (messageIndex >= 0) job.messages[messageIndex] = message;
          completed = true;
          scheduleDirectConversationRender(job.conversationId);
        }
      },
      { signal: job.controller.signal },
    );
    if (!completed) throw new Error("模型流在最终回复到达前结束。");
  } catch (error) {
    if (Array.isArray(error?.userImages) && error.userImages.length) {
      userMessage.images = error.userImages;
    }
    const restartingForSteer = error?.name === "AbortError" && job.restartForSteer;
    job.restartForSteer = false;
    streamingMessage.streaming = false;
    streamingMessage.content = String(streamingMessage.content || "");
    streamingMessage.error = restartingForSteer ? "" : job.pauseRequested && error?.name === "AbortError" ? "已暂停" : error?.name === "AbortError" ? "已停止生成" : error.message;
    streamingMessage.failure = restartingForSteer ? null : error?.failure || null;
    if (restartingForSteer && !streamingMessage.content.trim()) {
      job.messages = job.messages.filter((message) => message !== streamingMessage);
    }
    // The inline failure card is the durable, actionable error surface. A
    // second red toast repeats the same message and obscures the composer.
    scheduleDirectConversationRender(job.conversationId);
  } finally {
    const paused = Boolean(job.pauseRequested);
    job.controller = null;
    job.runId = "";
    if (paused) {
      streamingMessage.streaming = false;
      streamingMessage.paused = true;
      streamingMessage.error = "已暂停";
      job.pausedMessage = streamingMessage;
      job.pausedTurn = turn;
      job.pauseRequested = false;
      job.status = "paused";
      job.streamingMessage = null;
      job.currentTurn = turn;
    } else {
      job.streamingMessage = null;
      job.currentTurn = null;
    }
    if (!paused && job.pendingSteer) {
      const unacknowledgedTurn = job.pendingSteer;
      window.clearTimeout(job.steerAckTimer);
      job.steerAckTimer = 0;
      job.pendingSteer = null;
      job.steeringPending = false;
      job.queue.unshift(unacknowledgedTurn);
      toast("调整未在本轮结束前获得确认，已作为下一条消息继续。", true);
    }
    if (!paused && job.cancelRequested) job.queue = [];
    if (!paused) {
      job.status = job.cancelRequested
        ? "cancelled"
        : job.queue.length
        ? "queued"
        : completed
        ? "completed"
        : "failed";
    }
    try {
      await persistDirectChatJob(job);
    } catch (error) {
      console.warn("Direct conversation history could not be saved", error);
      if (job.conversationId === state.directConversationId) toast("回复已保留，但历史保存失败；请稍后重试。", true);
    }
    if (!paused && job.titleGenerationPending && (completed || !job.cancelRequested)) {
      job.titleGenerationPending = false;
      void generateDirectConversationTitle(job, job.firstQuestion, job.firstQuestionHasImages);
    }
    if (paused) {
      scheduleDirectConversationRender(job.conversationId);
      syncActiveDirectChatState();
      renderTasks();
      return;
    }
    const nextTurn = job.queue.shift() || null;
    if (nextTurn && !job.cancelRequested) {
      scheduleDirectConversationRender(job.conversationId);
      window.setTimeout(() => { void runDirectChatTurn(job, nextTurn); }, 0);
    } else {
      directChatJobs.delete(job.conversationId);
      scheduleDirectConversationRender(job.conversationId);
    }
    syncActiveDirectChatState();
    renderTasks();
  }
}

async function pauseDirectChatJob(conversationId = state.directConversationId) {
  const job = directChatJob(conversationId);
  if (!job) return;
  if (job.status === "paused" || job.pauseRequested) return;
  job.pauseRequested = true;
  const runId = job.runId;
  job.controller?.abort();
  if (runId) request("/api/chat/pause", {
    method: "POST",
    body: JSON.stringify({ run_id: runId }),
  }).catch(() => {});
  renderDirectLiveControls();
  renderTasks();
}

async function resumeDirectChatJob(conversationId = state.directConversationId) {
  const job = directChatJob(conversationId);
  if (!job || job.status !== "paused" || !job.pausedTurn || job.resumePending) return;
  job.resumePending = true;
  try {
    const turn = job.pausedTurn;
    const snapshot = await request(`/api/chat/submissions/${encodeURIComponent(turn.queueId)}?conversation_id=${encodeURIComponent(job.conversationId)}`);
    if (snapshot.state === "running") {
      toast("原请求仍在结束中，请稍后再检查。", true);
      return;
    }
    const terminal = snapshot.terminal;
    if (["completed", "failed"].includes(snapshot.state) && terminal && job.pausedMessage) {
      const result = terminal.result || terminal;
      const message = job.pausedMessage;
      const previousMessage = { ...message };
      const previousSessionId = job.sessionId;
      message.paused = false;
      message.streaming = false;
      if (snapshot.state === "completed") {
        Object.assign(message, result.message || {}, {
          message_id: message.message_id,
          parent_message_id: message.parent_message_id,
          error: "",
          failure: null,
        });
        job.sessionId = String(result.agent_runtime?.session?.session_id || job.sessionId || "");
        job.status = job.queue.length ? "queued" : "completed";
      } else {
        message.error = String(terminal.message || terminal.error || "上一轮执行失败");
        message.failure = terminal.failure || null;
        job.status = job.queue.length ? "queued" : "failed";
      }
      job.pausedTurn = null;
      job.pausedMessage = null;
      job.currentTurn = null;
      try {
        await persistDirectChatJob(job);
      } catch (error) {
        Object.assign(message, previousMessage);
        job.sessionId = previousSessionId;
        job.status = "paused";
        job.pausedTurn = turn;
        job.pausedMessage = message;
        job.currentTurn = turn;
        throw error;
      }
      const nextTurn = job.queue.shift() || null;
      if (nextTurn) void runDirectChatTurn(job, nextTurn);
      else directChatJobs.delete(job.conversationId);
      syncActiveDirectChatState();
      scheduleDirectConversationRender(job.conversationId);
      renderTasks();
      return;
    }
    if (!["uncertain", "unreplayable", "missing", "abandoned"].includes(snapshot.state)) {
      toast("无法确认暂停轮次的状态，请查看历史后再操作。", true);
      return;
    }
    const confirmed = await requestConfirmation({
      eyebrow: "重新生成",
      title: "重新生成这一轮？",
      message: "上一轮可能已经执行工具。请先检查已生成的文件或其他结果；重新生成可能再次执行相同操作。",
      confirmLabel: "重新生成",
      danger: true,
    });
    if (!confirmed) return;
    if (snapshot.state === "missing") {
      const fenced = await request(`/api/chat/submissions/${encodeURIComponent(turn.queueId)}/abandon`, {
        method: "POST",
        body: JSON.stringify({ conversation_id: job.conversationId }),
      });
      if (fenced.state !== "abandoned") {
        toast("原请求状态已变化，请再次检查。", true);
        return;
      }
    }
    const userIndex = job.messages.findIndex((message) => message?.role === "user" && message.message_id === turn.queueId);
    if (userIndex < 0) {
      toast("找不到暂停轮次的原始消息，请重新输入。", true);
      return;
    }
    const newTurn = {
      ...turn,
      queueId: globalThis.crypto?.randomUUID?.() || `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      createdAt: new Date().toISOString(),
    };
    job.branchId = globalThis.crypto?.randomUUID?.() || `branch-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    job.branchFromMessageId = turn.queueId;
    job.branchReason = "resume_paused_turn";
    job.messages = job.messages.slice(0, userIndex).filter((message) => message && !message.streaming);
    job.sessionId = "";
    job.pausedMessage = null;
    job.pausedTurn = null;
    job.currentTurn = null;
    job.pauseRequested = false;
    job.status = "queued";
    syncActiveDirectChatState();
    renderTasks();
    void runDirectChatTurn(job, newTurn);
  } catch (error) {
    toast(error.message, true);
  } finally {
    job.resumePending = false;
  }
}

// Keep the legacy action available for older rendered controls.
async function cancelDirectChatJob(conversationId = state.directConversationId) {
  return pauseDirectChatJob(conversationId);
}

// Compatibility marker for older live-control snapshots: data-action="cancel-direct-chat".

function runStatusLabel(run) {
  if (run.pause_requested) return "暂停中";
  if (run.cancel_requested) return "停止中";
  return ({ queued: "排队中", planning: "规划中", running: "执行中", verifying: "核验中", needs_confirmation: "待确认", waiting_input: "等待回答", paused: "已暂停", completed: "已完成", failed: "失败", cancelled: "已停止" })[run.status] || run.status;
}

function progressWidthClass(value) {
  const percent = Math.max(0, Math.min(100, Math.round(Number(value || 0))));
  return `progress-pct-${Math.round(percent / 5) * 5}`;
}

function upsertRun(run) {
  const index = state.runs.findIndex((item) => item.run_id === run.run_id);
  if (index >= 0) state.runs[index] = run;
  else state.runs.unshift(run);
  state.runs.sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)));
  renderTasks();
  renderComposerSendButtons();
}

function renderSources() {
  const query = state.sourceQuery.toLowerCase();
  const sources = (state.notebook?.sources || []).filter((source) => [source.title, source.doi, source.doc_id].join(" ").toLowerCase().includes(query));
  const knowledgeCounts = state.notebook?.knowledge_counts || {};
  const documentCount = Number(knowledgeCounts.documents ?? state.notebook?.counts?.sources ?? 0) || 0;
  byId("sourceCount").textContent = documentCount;
  const countSummary = byId("knowledgeCountSummary");
  if (countSummary) {
    const labels = [
      ["documents", "文档"],
      ["summaries", "摘要"],
      ["sections", "章节"],
      ["evidence_spans", "证据片段"],
      ["vectors", "向量"],
    ];
    countSummary.innerHTML = labels
      .map(([key, label]) => `<span><b>${Number(knowledgeCounts[key] || 0).toLocaleString("zh-CN")}</b>${label}</span>`)
      .join("");
  }
  if (!sources.length) {
    sourceList.innerHTML = `<div class="source-empty">${query ? "未找到文献" : "暂无来源"}</div>`;
    return;
  }
  sourceList.innerHTML = "";
  const template = byId("sourceTemplate");
  sources.forEach((source) => {
    const item = template.content.cloneNode(true);
    item.querySelector("strong").textContent = compact(source.title || source.doc_id, 110);
    item.querySelector("small").textContent = source.doi || source.publication_year || source.doc_id;
    item.querySelector(".source-open").addEventListener("click", () => openSourceReader(source));
    sourceList.appendChild(item);
  });
}

async function legacyAskQuestion(event, inputId) {
  event.preventDefault();
  const input = byId(inputId);
  const key = composerKey(inputId);
  const images = imagePayloadForComposer(key);
  const audio = audioPayloadForComposer(key);
  if (state.composerRecordings[key]) {
    toast("正在录音，请再次点击录制按钮停止后再发送。", true);
    return;
  }
  const sourceFiles = sourcePayloadForComposer(key);
  const question = input.value.trim() || (sourceFiles.length ? `请将「${sourceFiles[0].name}」制作成一份科研幻灯片。` : images.length ? "请分析我粘贴的图片，并结合当前资料库回答。" : audio.length ? "请根据我上传的语音回答。" : "");
  if (!question) return;
  const mode = composerMode(inputId);
  const isDirectConversation = !state.notebook && mode === "general";
  if (isDirectConversation && (composerSubmissionInFlight || state.streaming || activeDirectChatController)) {
    toast("上一条回复仍在处理，请等待完成。", true);
    return;
  }
  const isStandaloneSlides = mode === "slides" && sourceFiles.length > 0;
  if (!state.notebook && !isDirectConversation && !isStandaloneSlides) {
    toast("写作需要先打开资料库；制作幻灯片可直接添加 PDF、Word、Markdown、TXT 或 HTML。", true);
    return;
  }
  if (sourceFiles.length && mode !== "slides") {
    toast("文档附件用于制作幻灯片，已切换到“幻灯片”模式", true);
    setComposerMode("slides");
    return;
  }
  if (mode === "slides" && !state.notebook && !sourceFiles.length) {
    toast("请先添加 PDF、Word、Markdown、TXT 或 HTML；制作幻灯片不需要资料库。", true);
    return;
  }
  if (images.length && mode === "slides") {
    toast("制作幻灯片请使用文档附件；图片提问会交给通用对话处理。", true);
    return;
  }
  if (audio.length && !localAudioModelReady()) {
    toast("请先在设置 → 本地模型 → 模型市场下载 Qwen3 ASR 0.6B-hf", true);
    openSettings("local-models");
    return;
  }
  composerSubmissionInFlight = true;
  renderComposerSendButtons();
  const button = event.currentTarget.querySelector("button[type=submit]");
  if (!button) {
    composerSubmissionInFlight = false;
    toast("发送控件未准备好，请刷新后重试。", true);
    return;
  }
  button.disabled = true;
  let streamingMessage = null;
  byId("conversationTitle").textContent = compact(question, 80);
  applyContextPanelPreset(isDirectConversation || isStandaloneSlides ? "none" : ["writing", "deep-research"].includes(mode) ? "review" : "evidence");
  setView("conversation");
  byId("answerArea").innerHTML = `<div class="conversation-thread"><div class="user-turn"><div class="user-turn-bubble">${composerSourcePreviewMarkup(sourceFiles)}${composerImagePreviewMarkup(images)}${composerAudioPreviewMarkup(audio)}<p>${renderAssistantInline(question)}</p></div></div></div>`;
  if (["writing", "deep-research"].includes(mode)) renderReviewDocument({ title: question, status: "planning", progress: 0 }, null);
  try {
    if (isDirectConversation) {
      const userMessage = {
        role: "user", content: question, images, audio,
        message_id: globalThis.crypto?.randomUUID?.() || `chat-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        created_at: new Date().toISOString(),
      };
      const messages = [...state.directMessages, userMessage];
      const startedAt = performance.now();
      streamingMessage = { role: "assistant", content: "", reasoning: "", streaming: true, processing_started_at: startedAt, model: modelIdentitySnapshot(), created_at: new Date().toISOString() };
      state.directMessages = [...messages, streamingMessage];
      renderDirectConversation();
      let completed = false;
      await streamChatWithRecovery({
        messages, audio, model: streamingMessage.model, thinking_level: currentThinkingLevel(),
        web_search: state.webSearchMode,
        idempotency_key: userMessage.message_id,
      }, (eventType, event) => {
        if (eventType === "delta") {
          streamingMessage.content += String(event.content || "");
          scheduleDirectConversationRender();
          return;
        }
        if (eventType === "CUSTOM" && event.name === "model_reasoning") {
          streamingMessage.reasoning = event.value?.reset ? "" : streamingMessage.reasoning + String(event.value?.delta || "");
          scheduleDirectConversationRender();
          return;
        }
        if (eventType === "done") {
          if (Array.isArray(event.user_images)) userMessage.images = event.user_images;
          const message = {
            ...event.message,
            model: modelIdentitySnapshot(event.model || event.message?.model || streamingMessage.model),
            usage: event.message?.usage || streamingMessage.usage,
            reasoning: event.message?.reasoning || streamingMessage.reasoning,
            reasoning_open: streamingMessage.reasoning_open,
            created_at: event.message?.created_at || new Date().toISOString(),
            processing_ms: Math.max(0, Math.round(performance.now() - startedAt)),
          };
          const messageIndex = state.directMessages.indexOf(streamingMessage);
          if (messageIndex >= 0) state.directMessages[messageIndex] = message;
          completed = true;
          // B1: context usage %
          updateSessionStats(event.stats || null);
          scheduleDirectConversationRender();
          return;
        }
        // A1: track session ID for compaction/resume
        if (eventType === "session") {
          state.sessionId = String(event.session_id || "");
          if (state.sessionId) window.localStorage.setItem("scansci.active.session", state.sessionId);
          return;
        }
        // A2: tool execution progress
        if (eventType === "tool.completed" || eventType === "tool.failed") {
          state.toolProgress = { name: event.name, status: eventType === "tool.completed" ? "done" : "error", result: event.result };
          scheduleDirectConversationRender();
          return;
        }
        if (eventType === "status" && event.tool_name) {
          state.toolProgress = { name: event.tool_name, status: "running" };
          scheduleDirectConversationRender();
          return;
        }
        // B2: auto-retry notification
        if (eventType === "status" && event.subtype === "retry") {
          const attempt = Number(event.attempt || 1);
          const delay = Math.round(Number(event.delay_ms || 0) / 1000);
          toast(delay ? `正在重试（第 ${attempt} 次，${delay}s 后）…` : `正在重试（第 ${attempt} 次）…`);
          return;
        }
        if (eventType === "compaction") {
          if (event.status === "started") toast("上下文过长，正在自动压缩…");
          else if (event.status === "completed") {
            const before = Number(event.tokens_before || 0);
            const after = Number(event.tokens_after || 0);
            toast(before && after ? `上下文已压缩：${formatTokenCount(before)} → ${formatTokenCount(after)} tokens` : "上下文已压缩");
          }
          return;
        }
        // B3: compatibility fallback
        if (eventType === "compatibility.fallback") {
          toast("Pi Agent 当前不可用，已回退到直连模型——回答质量可能受限");
          return;
        }
      }, {
        onRetry: () => {
          streamingMessage.content = "";
          streamingMessage.reasoning = "";
          streamingMessage.error = "";
          streamingMessage.failure = null;
          streamingMessage.streaming = true;
          renderDirectConversation();
        },
      });
      if (!completed) throw new Error("The model stream ended before a final response was received.");
      input.value = "";
      clearComposerAudio(key);
      return;
    }
    const { workflowType, workflowInput } = composerRun(mode, question, images, sourceFiles);
    const run = await createResearchRun(workflowType, workflowInput);
    state.activeTaskId = run.run_id;
    window.localStorage.setItem("scansci.active.task", run.run_id);
    upsertRun(run);
    if (mode === "download") {
      state.pendingBatchIdentifiers = [];
      state.pendingBatchFilename = "";
      renderHomeBatchAttachment();
    }
    renderRun(run);
    watchRun(run.run_id, (next) => {
      if (state.activeView === "conversation" && state.activeTaskId === next.run_id) renderRun(next);
    });
    input.value = "";
    clearComposerImages(key);
    clearComposerAudio(key);
    clearComposerSources(key);
  } catch (error) {
    if (isDirectConversation && streamingMessage) {
      streamingMessage.streaming = false;
      // Keep any partial provider output, but do not present a fake answer
      // sentence when the request failed before the first delta.
      streamingMessage.content = String(streamingMessage.content || "");
      streamingMessage.error = error.message;
      streamingMessage.failure = error.failure || null;
      renderDirectConversation();
    } else {
      byId("answerArea").innerHTML = `<div class="error-state">${escapeHtml(error.message)}</div>`;
    }
  } finally {
    composerSubmissionInFlight = false;
    button.disabled = false;
    renderComposerSendButtons();
  }
}

async function askQuestion(event, inputId) {
  event.preventDefault();
  // `Event.currentTarget` is only guaranteed while the synchronous submit
  // handler is running. General mode may await the route preview below; by
  // then browsers reset `currentTarget` to null. Capture the form/button
  // before the first await so click-to-send and Enter-to-send share a stable
  // submission path.
  const form = event.currentTarget;
  const button = form?.querySelector?.("button[type=submit]");
  const input = byId(inputId);
  if (!input || !button) {
    console.error("ScanSci composer submission is missing its input or submit button", { inputId });
    toast("发送控件未准备好，请刷新后重试。", true);
    return;
  }
  const activatedSendButton = event.submitter === button;
  const currentRun = activeResearchRun();
  const currentJob = !state.activeTaskId ? directChatJob() : null;
  // A completed/failed run can remain selected in the history state.  It must
  // not turn the composer into a no-op: only an interactive run owns the
  // send button for pause/resume.  Otherwise the next message starts a new
  // task directly, without requiring the user to click “新建研究” first.
  const currentRunIsInteractive = currentRun && (
    currentRun.status === "paused" ||
    (!currentRun.pause_requested && ["queued", "planning", "running", "verifying"].includes(String(currentRun.status || "")))
  );
  if (activatedSendButton && currentRunIsInteractive) {
    if (currentRun.status === "paused" && !currentRun.pause_requested) {
      resumeRun(currentRun.run_id).catch((error) => toast(error.message, true));
    } else if (!currentRun.pause_requested && ["queued", "planning", "running", "verifying"].includes(String(currentRun.status || ""))) {
      pauseRun(currentRun.run_id).catch((error) => toast(error.message, true));
    }
    return;
  }
  if (activatedSendButton && currentJob && !input.value.trim()) {
    if (currentJob.status === "paused") resumeDirectChatJob(currentJob.conversationId);
    else if (!currentJob.pauseRequested) pauseDirectChatJob(currentJob.conversationId);
    return;
  }
  const key = composerKey(inputId);
  const selectedSkillIds = composerSkillIds(key, input.value);
  const selectedSkills = skillRecordsForIds(selectedSkillIds);
  const imageSelection = inputId === "reviewQuestionInput" ? null : selectedComposerImageModel();
  const selectedMode = imageSelection ? "general" : composerMode(inputId);
  const images = imagePayloadForComposer(key);
  const audio = audioPayloadForComposer(key);
  if (state.composerRecordings[key]) {
    toast("正在录音，请再次点击录制按钮停止后再发送。", true);
    return;
  }
  const sourceFiles = sourcePayloadForComposer(key);
  const activeRun = state.runs.find((item) => item.run_id === state.activeTaskId);
  if (imageSelection) {
    const provider = state.settings?.providers?.find(p => p.id === imageSelection.provider_id);
    const model = provider?.models?.find(m => m.id === imageSelection.model_id);
    if (!model?.capabilities?.includes("image") || !composerModelAvailability(model, provider).selectable) {
      toast("所选生图模型尚不可用，请重新选择或检查模型服务配置。", true);
      return;
    }
    if (images.length || sourceFiles.length || audio.length) {
      toast("当前生图入口使用文字描述；请移除附件后发送。", true);
      return;
    }
  }
  // The selected historical task is the source of truth.  `activeView` can
  // briefly be `home` during navigation/refresh even though the task id is
  // already restored, which previously routed a follow-up to a brand-new
  // direct chat and silently dropped the task context.
  const isTaskConversation = inputId === "chatQuestionInput" && Boolean(activeRun);
  let question = input.value.trim() || (selectedMode === "download" && state.pendingBatchIdentifiers.length
    ? state.pendingBatchIdentifiers.join("\n")
    : sourceFiles.length
    ? selectedMode === "slides"
      ? `请将《${sourceFiles[0].name}》制作成一份科研幻灯片。`
      : `请阅读《${sourceFiles[0].name}》并概括最重要的信息。`
    : images.length ? "请分析我粘贴的图片。" : audio.length ? "请根据我上传的语音回答。" : "");
  if (!question) return;
  if (state.onboardingOpen && state.onboardingMode === "welcome") {
    await finishFirstRun("已开始研究", { focusComposer: false });
  }
  let mode = ["research", "academic"].includes(selectedMode) ? resolveResearchComposerMode(question) : selectedMode;

  // An open historical task is a chat thread. Composer modes only affect new
  // tasks; a pure greeting after a finished evidence task is the small
  // exception so it can receive a normal conversational reply.
  const taskIsFinished = !activeRun || ["completed", "failed", "cancelled", "paused"].includes(String(activeRun.status || ""));
  let isTaskFollowUp = isTaskConversation;
  const casualKnowledgeMessage = mode === "knowledge"
    && taskIsFinished
    && !images.length
    && !sourceFiles.length
    && !audio.length
    && isCasualConversationMessage(question);
  if (casualKnowledgeMessage) {
    mode = "general";
  }
  isTaskFollowUp = isTaskFollowUp && !casualKnowledgeMessage;
  const isLikelyDirectConversation = !isTaskFollowUp && !state.notebook && ["general", "writing"].includes(selectedMode);
  const activeConversationJob = !isTaskFollowUp ? directChatJob() : null;
  const hasExistingDirectHistory = isLikelyDirectConversation && state.directMessages.some(
    (message) => message?.role === "user" && String(message.content || "").trim(),
  );
  if (isLikelyDirectConversation && composerSubmissionInFlight) {
    toast("这条消息正在提交，请稍候。", true);
    return;
  }
  if (isTaskFollowUp && (sourceFiles.length || images.length || audio.length)) {
    toast("当前对话暂不支持追加附件，请先发送文字反馈。", true);
    return;
  }
  // Claim the composer before the optional route preview.  The preview is an
  // await point; without this synchronous guard, Enter plus a click could
  // create two identical model requests before the submit button is disabled.
  composerSubmissionInFlight = true;
  renderComposerSendButtons();
  const isReviewWorkflow = inputId === "reviewQuestionInput";
  const selectedKnowledge = imageSelection ? [] : selectedKnowledgeNotebooks();
  const searchableKnowledgeSelected = selectedKnowledge.some((notebook) => Number(notebook.counts?.sources || 0) > 0);
  const writingArtifactRoute = !imageSelection && !isTaskFollowUp && !isReviewWorkflow && !images.length && !sourceFiles.length && !audio.length
    ? (state.agentMode ? null : academicWritingArtifactRoute(mode, question, { searchableKnowledgeSelected }))
    : null;
  let routedTask = null;
  // General input stays general by default.  For an explicit, multi-step
  // product request the host may offer a durable route; the server repeats
  // this decision when creating the run, so this preview is never authority.
  if (!state.agentMode && !imageSelection && selectedMode === "general" && !activeConversationJob && !hasExistingDirectHistory && !isTaskFollowUp && !isReviewWorkflow && !images.length && !sourceFiles.length && !audio.length) {
    try {
      const decision = await previewFreeformTask(question, selectedSkillIds);
      if (decision?.route === "durable_run" && decision?.workflow_type) {
        routedTask = decision;
        mode = String(decision.presentation_mode || mode);
      }
    } catch (error) {
      // A routing preview is an enhancement, not a gate for normal chat.
      console.warn("Freeform task preview unavailable", error);
    }
  }
  const isDirectConversation = !isReviewWorkflow && !routedTask && !writingArtifactRoute && (mode === "general" || mode === "writing");
  const directChatMode = isDirectConversation && searchableKnowledgeSelected && !casualKnowledgeMessage ? "knowledge" : mode;
  const isStandaloneSlides = mode === "slides" && sourceFiles.length > 0;
  if (mode === "knowledge" && !selectedKnowledge.length && !isTaskFollowUp) {
    composerSubmissionInFlight = false;
    toast("知识库问答需要先选择一个知识库；通用和写作模式可直接使用。", true);
    return;
  }
  if (isDirectConversation && selectedKnowledge.length && !searchableKnowledgeSelected && !isTaskFollowUp) {
    composerSubmissionInFlight = false;
    toast("所选知识库还没有可检索内容。请等待导入或索引完成，或改选其他知识库。", true);
    return;
  }
  if (["novelty", "idea"].includes(mode) && !state.notebook && !isTaskFollowUp) {
    composerSubmissionInFlight = false;
    const workflowLabel = mode === "novelty" ? "证据查新" : "研究构思";
    toast(`${workflowLabel}需要一个知识库来保存全文和句级证据；请先新建或选择知识库。`, true);
    return;
  }
  // A topic alone is sufficient to start a presentation project.  Source
  // material enriches the result but is not a precondition: shortcuts are
  // aids for getting started, not permission gates.
  if (images.length && mode === "slides") {
    composerSubmissionInFlight = false;
    toast("制作幻灯片请使用文档附件；图片提问会交给通用对话处理。", true);
    return;
  }
  if (audio.length && !["general", "writing", "knowledge"].includes(mode)) {
    composerSubmissionInFlight = false;
    toast("语音提问目前用于通用对话、写作或知识库问答。", true);
    return;
  }
  if (audio.length && !localAudioModelReady()) {
    composerSubmissionInFlight = false;
    toast("请先在设置 → 本地模型 → 模型市场下载 Qwen3 ASR 0.6B-hf。", true);
    openSettings("local-models");
    return;
  }
  if (mode === "academic" && !isTaskFollowUp && !routedTask) {
    // Academic planning returns before the main request finally block.
    // Release the claim explicitly after its await completes below.
    composerSubmissionInFlight = false;
    try {
      await openAcademicSearchPlan(question, { inputId, key, sourceFiles, images, skills: selectedSkillIds });
    } catch (error) {
      toast(`无法生成检索计划：${error.message}`, true);
    }
    return;
  }

  const directTurn = isDirectConversation
    ? directTurnConfiguration({
        question,
        key,
        directChatMode,
        selectedKnowledge,
        selectedSkillIds,
        selectedSkills,
        images,
        audio,
        sourceFiles,
      })
    : null;
  if (directTurn) {
    try {
      await stageDirectTurnAttachments(directTurn);
    } catch (error) {
      composerSubmissionInFlight = false;
      renderComposerSendButtons();
      toast(`附件暂存失败：${error.message}`, true);
      return;
    }
  }
  if (isDirectConversation && activeConversationJob) {
    composerSubmissionInFlight = false;
    await submitToRunningDirectChat(activeConversationJob, directTurn, input);
    return;
  }

  const plannedWorkflowType = String(
    routedTask?.workflow_type
      || writingArtifactRoute?.workflowType
      || (isReviewWorkflow ? "literature_review" : mode === "deep-research" ? "deep_research" : ""),
  );
  const plannedResearchDocument = ["literature_review", "deep_research"].includes(plannedWorkflowType);
  button.disabled = true;
  let streamingMessage = null;
  const hasDirectUserHistory = isDirectConversation && state.directMessages.some(
    (message) => message?.role === "user" && String(message.content || "").trim(),
  );
  if (!isDirectConversation || !hasDirectUserHistory) {
    byId("conversationTitle").textContent = isDirectConversation
      ? compact(directConversationTitle([...state.directMessages, { role: "user", content: question }]), 80)
      : compact(question, 80);
  }
  if (!isTaskFollowUp) state.reviewDocumentOpen = false;
  applyContextPanelPreset(directChatMode === "knowledge" ? "knowledge" : "none");
  setView("conversation");
  if (isTaskFollowUp) {
    renderPendingTaskFollowUp(activeRun, question, selectedSkills);
  } else {
    state.conversationAutoFollow = true;
    byId("answerArea").innerHTML = `<div class="conversation-thread"><div class="user-turn"><div class="user-turn-bubble">${messageSkillTokensMarkup(selectedSkills)}${composerSourcePreviewMarkup(sourceFiles)}${composerImagePreviewMarkup(images)}${composerAudioPreviewMarkup(audio)}<p>${renderAssistantInline(question)}</p></div></div></div>`;
    followLatestConversationMessage();
  }
  if (plannedResearchDocument || (mode === "knowledge" && state.evidenceOutputMode === "review")) {
    renderReviewDocument({ title: question, workflow_type: plannedWorkflowType, input: { question, document_kind: writingArtifactRoute?.workflowInput?.document_kind || "" }, status: "planning", progress: 0 }, null);
  }
  try {
    if (isTaskFollowUp) {
      // Clear the composer before waiting for the task endpoint.  Follow-up
      // requests can take several seconds while the task is queued; keeping
      // the submitted text in the input makes a still-connected thread look
      // like the message was never sent (and invites duplicate submissions).
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      const result = await continueTaskConversation(activeRun.run_id, question, selectedSkillIds);
      const run = result.run;
      state.activeTaskId = run.run_id;
      window.localStorage.setItem("scansci.active.task", run.run_id);
      upsertRun(run);
      state.lastRunRenderKey = "";
      renderRun(run);
      state.sessionId = `research-run-${run.run_id}`;
      window.localStorage.setItem("scansci.active.session", state.sessionId);
      void restoreSessionStats(estimateRunSessionStats(run));
      clearComposerSkills(key);
      if (["queued", "planning", "running", "verifying"].includes(String(run.status || ""))) {
        watchRun(run.run_id, (next) => {
          if (state.activeView === "conversation" && state.activeTaskId === next.run_id) renderRun(next);
        });
      }
      return;
    }
    if (isDirectConversation) {
      beginDirectChatJob(directTurn);
      clearSubmittedDirectComposer(directTurn, input);
      return;
    }

    const { workflowType, workflowInput } = routedTask
      ? {
          workflowType: "auto",
          workflowInput: { question, task_origin: "freeform", skills: selectedSkillIds },
        }
      : writingArtifactRoute || composerRun(mode, question, images, sourceFiles);
    if (selectedSkillIds.length) workflowInput.skills = selectedSkillIds;
    if (isReviewWorkflow) Object.assign(workflowInput, reviewWorkflowPreferences());
    const run = await createResearchRun(workflowType, workflowInput);
    state.activeTaskId = run.run_id;
    window.localStorage.setItem("scansci.active.task", run.run_id);
    upsertRun(run);
    state.sessionId = `research-run-${run.run_id}`;
    window.localStorage.setItem("scansci.active.session", state.sessionId);
    updateSessionStats(estimateRunSessionStats(run));
    renderRun(run);
    watchRun(run.run_id, (next) => {
      if (state.activeView === "conversation" && state.activeTaskId === next.run_id) renderRun(next);
    });
    input.value = "";
    clearComposerSkills(key);
    clearComposerImages(key);
    clearComposerAudio(key);
    clearComposerSources(key);
  } catch (error) {
    if (error?.name === "AbortError") {
      if (streamingMessage) {
        streamingMessage.streaming = false;
        streamingMessage.error = "已停止生成";
        renderDirectConversation();
      }
      return;
    }
    if (isTaskFollowUp && activeRun) {
      if (!input.value.trim()) {
        input.value = question;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
      renderFailedTaskFollowUp(activeRun, question, error, selectedSkills);
    } else if (isDirectConversation && streamingMessage) {
      streamingMessage.streaming = false;
      // Keep any partial provider output, but do not present a fake answer
      // sentence when the request failed before the first delta.
      streamingMessage.content = String(streamingMessage.content || "");
      streamingMessage.error = error.message;
      streamingMessage.failure = error.failure || null;
      renderDirectConversation();
      persistDirectConversation().catch((historyError) => console.warn("Failed to save failed direct conversation", historyError));
    } else {
      byId("answerArea").innerHTML = `<div class="error-state">${escapeHtml(error.message)}</div>`;
    }
  } finally {
    composerSubmissionInFlight = false;
    button.disabled = false;
    syncActiveDirectChatState();
    renderComposerSendButtons();
  }
}

function formatProcessingDuration(milliseconds) {
  const totalSeconds = Math.max(0, Number(milliseconds || 0)) / 1000;
  if (totalSeconds < 1) return "不足 1 秒";
  if (totalSeconds >= 60) {
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = Math.round(totalSeconds % 60);
    return `${minutes}分 ${seconds}秒`;
  }
  return `${totalSeconds < 10 ? totalSeconds.toFixed(1) : Math.round(totalSeconds)} 秒`;
}

function formatTokenCount(value) {
  const count = Number(value);
  if (!Number.isFinite(count) || count < 0) return "";
  return new Intl.NumberFormat("zh-CN").format(Math.round(count));
}

function estimateTokenCount(text) {
  const value = String(text || "");
  if (!value) return 0;
  const cjk = (value.match(/[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]/g) || []).length;
  const remainder = value.length - cjk;
  return Math.max(1, Math.ceil(cjk * 0.92 + remainder / 3.8));
}

function formatMessageTime(value) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part) => String(part).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function providerForId(providerId) {
  const id = String(providerId || "");
  return (state.settings?.providers || []).find((item) => String(item.id) === id)
    || (state.presets?.providers || []).find((item) => String(item.id) === id)
    || activeModel().provider
    || { id: id || "scansci-managed", name: "ScanSci" };
}

function messageModelIdentity(model = null) {
  const active = activeModel();
  const providerId = String(model?.provider_id || active.provider?.id || "scansci-managed");
  const modelId = String(model?.model_id || active.model?.id || "");
  const configuredProvider = providerForId(providerId);
  // A message may be rendered long after the user has selected another
  // provider.  Prefer the identity captured with that message, while still
  // using the current catalog to resolve a missing display name for older
  // conversations.
  const provider = {
    ...configuredProvider,
    id: providerId,
    name: String(model?.provider_name || configuredProvider?.name || providerId || "ScanSci"),
    logo: String(model?.provider_logo || configuredProvider?.logo || providerId || "scansci-managed"),
  };
  const configuredModel = provider?.models?.find((item) => String(item.id) === modelId);
  return {
    provider,
    providerId,
    modelId,
    modelName: String(model?.model_name || configuredModel?.name || modelId || "ScanSci"),
  };
}

function modelIdentitySnapshot(model = null) {
  const identity = messageModelIdentity(model);
  return {
    provider_id: identity.providerId,
    model_id: identity.modelId,
    provider_name: identity.provider?.name || identity.providerId,
    provider_logo: identity.provider?.logo || identity.providerId,
    model_name: identity.modelName,
  };
}

function messageUsageValues(usage) {
  const source = usage && typeof usage === "object" ? usage : {};
  const tokens = source.tokens && typeof source.tokens === "object" ? source.tokens : source;
  const provided = [
    "prompt_tokens",
    "input_tokens",
    "input",
    "completion_tokens",
    "output_tokens",
    "output",
    "total_tokens",
    "total",
  ].some((key) => Object.prototype.hasOwnProperty.call(tokens, key));
  const prompt = Number(tokens.prompt_tokens ?? tokens.input_tokens ?? tokens.input ?? 0) || 0;
  const completion = Number(tokens.completion_tokens ?? tokens.output_tokens ?? tokens.output ?? 0) || 0;
  const hasDirectional = [
    "prompt_tokens",
    "input_tokens",
    "input",
    "completion_tokens",
    "output_tokens",
    "output",
  ].some((key) => Object.prototype.hasOwnProperty.call(tokens, key));
  const providerTotal = Number(tokens.total_tokens ?? tokens.total ?? 0) || 0;
  const total = hasDirectional ? prompt + completion : providerTotal;
  return { prompt, completion, total, provided };
}

function messageUsageMarkup(role, content, usage, promptContent = "") {
  const measured = messageUsageValues(usage);
  const prompt = measured.provided ? measured.prompt : (role === "assistant" ? estimateTokenCount(promptContent) : 0);
  const completion = measured.provided ? measured.completion : (role === "assistant" ? estimateTokenCount(content) : 0);
  const total = measured.provided
    ? measured.total
    : (role === "assistant" ? prompt + completion : estimateTokenCount(content));
  if (!measured.provided && !total) return "";
  const detail = role === "assistant"
    ? ` <span aria-label="输入 Tokens">↑${formatTokenCount(prompt)}</span> <span aria-label="输出 Tokens">↓${formatTokenCount(completion)}</span>`
    : "";
  return `<p class="message-usage">Tokens: ${formatTokenCount(total)}${detail}</p>`;
}

function messageFooterMarkup(content, usageMarkup, isUser, {
  editable = false,
  editing = false,
  messageIndex = "",
} = {}) {
  const copyText = String(content || "");
  const copyButton = copyText.trim() && !editing
    ? `<button type="button" class="message-copy-button" data-action="copy-conversation-message" data-copy-text="${escapeHtml(copyText)}" aria-label="复制消息" title="复制消息">${uiIcon("copy")}</button>`
    : "";
  const editButton = isUser && editable && !editing
    ? `<button type="button" class="message-edit-button" data-action="edit-last-conversation-message" data-message-index="${escapeHtml(String(messageIndex))}" aria-label="编辑最后一条消息" title="编辑消息">${uiIcon("pen")}</button>`
    : "";
  const editActions = isUser && editable && editing
    ? `<button type="button" class="message-edit-save" data-action="save-last-message-edit" aria-label="保存并重新生成" title="保存并重新生成">${uiIcon("check")}</button><button type="button" class="message-edit-cancel" data-action="cancel-last-message-edit" aria-label="取消编辑" title="取消编辑">${uiIcon("x")}</button>`
    : "";
  const actionButtons = editing ? editActions : `${copyButton}${editButton}`;
  const actionMarkup = actionButtons
    ? `<div class="message-hover-actions message-edit-actions" role="group" aria-label="消息操作">${actionButtons}</div>`
    : "";
  if (!actionMarkup && !usageMarkup) return "";
  return `<footer class="message-footer">${isUser ? `${usageMarkup}${actionMarkup}` : `${actionMarkup}${usageMarkup}`}</footer>`;
}

function userAvatarMarkup() {
  const avatar = profileAvatars[state.profileAvatar] || profileAvatars.male;
  return `<span class="message-avatar is-user"><img src="${escapeHtml(avatar.src)}" alt="" /></span>`;
}

function conversationMessageMarkup({
  role,
  content = "",
  contentMarkup = "",
  createdAt = "",
  usage = null,
  model = null,
  label = "",
  processing = "",
  extra = "",
  classes = "",
  promptContent = "",
  editable = false,
  editing = false,
  messageIndex = "",
  imageReply = false,
} = {}) {
  const isUser = role === "user";
  const identity = messageModelIdentity(model);
  const body = contentMarkup || (isUser
    ? `<div class="user-turn-bubble"><p>${renderAssistantInline(content)}</p></div>`
    : `<div class="answer-sentence">${renderAssistantContent(content)}</div>`);
  const avatar = isUser ? userAvatarMarkup() : `<span class="message-avatar is-assistant">${providerLogo(identity.provider)}</span>`;
  const modelName = String(identity.modelName || "").trim();
  const providerName = String(identity.provider?.name || "").trim();
  const name = isUser ? "你" : uniqueModelProviderLabel(modelName, providerName, " | ") || "ScanSci";
  const badge = label ? `<b>${escapeHtml(label)}</b>` : "";
  const time = `<time datetime="${escapeHtml(createdAt || "")}">${escapeHtml(formatMessageTime(createdAt))}</time>`;
  const usageMarkup = messageUsageMarkup(role, content, usage, promptContent);
  const footer = messageFooterMarkup(imageReply && content === "图片已生成并保存。" ? "" : content, imageReply ? "" : usageMarkup, isUser, { editable, editing, messageIndex });
  return `<div class="conversation-message ${isUser ? "user-turn is-user" : "assistant-turn is-assistant"} ${escapeHtml(classes)}">${isUser ? "" : avatar}<div class="message-body"><header class="message-meta"><strong>${escapeHtml(name)}</strong>${badge}${time}</header>${processing}${body}${extra}${footer}</div>${isUser ? avatar : ""}</div>`;
}

function compactProcessTrace(message) {
  const source = Array.isArray(message.trace) ? message.trace : [];
  const unique = [];
  const seen = new Set();
  source.forEach((item) => {
    if (!item || typeof item !== "object") return;
    const rawTitle = String(item.title || "").trim();
    const detail = String(item.detail || "").trim();
    if (!rawTitle && !detail) return;
    const title = rawTitle || "处理步骤";
    const key = `${title}\n${detail}`;
    if (seen.has(key)) return;
    seen.add(key);
    unique.push({ ...item, title: title || "处理步骤", detail });
  });
  return unique.slice(-8);
}

function processTraceMarkup(message, duration, messageIndex = null) {
  const trace = compactProcessTrace(message);
  const reasoning = String(message.reasoning || "");
  const rawTraceCount = Array.isArray(message.trace) ? message.trace.length : trace.length;
  const live = Boolean(message.streaming && Number(message.processing_started_at || 0));
  if (!trace.length && !reasoning && !live && duration <= 0) return "";
  const liveDuration = live ? Math.max(0, performance.now() - Number(message.processing_started_at)) : duration;
  const latest = trace.length ? trace[trace.length - 1] : null;
  const phase = latest?.title ? escapeHtml(latest.title) : reasoning ? (live ? "思考中" : "模型思考") : "";
  const phasePrefix = phase ? `${phase} · ` : "";
  const recordCount = rawTraceCount > 0 ? ` · ${rawTraceCount} 条记录` : "";
  const status = live
    ? `${phasePrefix}已处理 <time data-processing-timer="${escapeHtml(String(message.processing_started_at))}">${formatProcessingDuration(liveDuration)}</time>${recordCount}`
    : duration > 0 ? `${phasePrefix}已处理 <time>${formatProcessingDuration(duration)}</time>${recordCount}` : `${phase || "正在处理"}${recordCount}`;
  const rows = trace.map((item) => `<li><strong>${escapeHtml(item.title || "处理步骤")}</strong><span>${escapeHtml(item.detail || "")}</span></li>`).join("");
  const thought = reasoning ? `<div class="answer-reasoning" aria-label="模型思考内容">${escapeHtml(reasoning)}</div>` : "";
  const isOpen = typeof message.reasoning_open === "boolean" ? message.reasoning_open : live && (trace.length || reasoning);
  const indexAttribute = messageIndex === null ? "" : ` data-message-index="${messageIndex}"`;
  return `<details class="answer-processing ${live ? "is-live" : ""}"${indexAttribute} ${isOpen ? "open" : ""} aria-label="本次对话处理过程"><summary>${status}${uiIcon("chevron-right", "answer-processing-chevron")}</summary>${rows ? `<ol>${rows}</ol>` : ""}${thought}</details>`;
}

function reuseImagePrompt(prompt) {
  const input = byId("chatQuestionInput");
  if (!input || !prompt) return;
  if (input.value.trim() && input.value !== prompt) {
    toast("输入框已有草稿，请先保留或清空后再复用描述。");
    input.focus();
    return;
  }
  input.value = prompt;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.focus();
}

function renameDirectConversation() {
  const id = state.directConversationId;
  if (!id || state.activeTaskId || directChatJob()) return;
  const input = byId("conversationTitleInput");
  input.dataset.conversationId = id;
  input.value = state.directConversations.find(item => item.conversation_id === id)?.title || byId("conversationTitle").textContent;
  byId("conversationTitle").hidden = true;
  input.hidden = false;
  input.focus();
  input.select();
}

byId("conversationTitleInput").addEventListener("blur", () => {
  byId("conversationTitleInput").hidden = true;
  byId("conversationTitle").hidden = false;
});
byId("conversationTitleInput").addEventListener("keydown", async (event) => {
  if (event.isComposing || !["Enter", "Escape"].includes(event.key)) return;
  event.preventDefault();
  event.stopPropagation();
  const input = event.currentTarget;
  const id = input.dataset.conversationId;
  const title = input.value.trim();
  if (event.key === "Escape") { input.blur(); return; }
  if (!title || directChatJob(id)) return;
  try {
    const saved = await request("/api/chat/history/" + encodeURIComponent(id) + "/title", {
      method: "POST", body: JSON.stringify({ title, title_source: "user" }),
    });
    upsertDirectConversation(saved);
    if (state.directConversationId === id && !state.activeTaskId) byId("conversationTitle").textContent = compact(title, 80);
    input.blur();
    renderTasks();
  } catch (error) { toast(error.message, true); }
});

function generatedImageArtifactsMarkup(artifacts = [], { promptContent = "", usageMarkup = "" } = {}) {
  const rows = (Array.isArray(artifacts) ? artifacts : []).filter((artifact) => artifact?.artifact_type === "image").map((artifact) => {
    const assetUrl = String(artifact.asset_url || "").trim();
    if (!assetUrl.startsWith("/api/generated-images/") || assetUrl.slice("/api/generated-images/".length).includes("/")) return "";
    const details = [artifact.provider_name || artifact.provider_id, artifact.model_id, artifact.size].filter(Boolean).join(" · ");
    const prompt = String(promptContent || artifact.title || "");
    const size = /^(\d{1,5})[x×*](\d{1,5})$/.exec(String(artifact.size || ""));
    const aspect = size && Number(size[2]) > 0 ? Math.max(.25, Math.min(4, Number(size[1]) / Number(size[2]))) : 1;
    return `<figure class="generated-image-artifact" style="--image-aspect:${aspect}"><a href="${escapeHtml(assetUrl)}" data-action="open-image-preview" data-image-src="${escapeHtml(assetUrl)}" data-image-alt="${escapeHtml(artifact.title || "AI 生成图片")}" aria-label="放大查看生成图片"><img src="${escapeHtml(assetUrl)}" alt="${escapeHtml(artifact.title || "AI 生成图片")}" loading="lazy" /></a><figcaption><details class="generated-image-prompt"><summary><strong>本次图片 · ${escapeHtml(artifact.title || "AI 生成图片")}</strong></summary><p>${escapeHtml(prompt)}</p></details><div class="generated-image-toolbar"><a class="generated-image-download" href="${escapeHtml(assetUrl)}" download>下载原图</a><button type="button" data-action="reuse-image-prompt" data-prompt="${escapeHtml(prompt)}">复用描述</button><details class="generated-image-details"><summary>详情</summary><div><small>${escapeHtml(details)}</small>${usageMarkup}</div></details></div></figcaption></figure>`;
  }).filter(Boolean);
  return rows.length ? `<div class="generated-image-artifacts">${rows.join("")}</div>` : "";
}

function renderDirectLiveControls() {
  const surface = byId("chatLiveControls");
  const input = byId("chatQuestionInput");
  const form = byId("chatAskForm");
  const send = form?.querySelector("button[type='submit']");
  if (!surface) return;
  const job = !state.activeTaskId ? directChatJob() : null;
  // Streaming updates render the conversation on animation frames. Preserve
  // the live queue nodes while a native drag is in progress so a delayed
  // token cannot replace the source or drop target underneath the pointer.
  if (job && directQueueDrag.queueId && directQueueDrag.conversationId === job.conversationId) return;
  if (!job) {
    surface.hidden = true;
    surface.innerHTML = "";
    surface.classList.remove("has-queue");
    form?.classList.remove("has-live-direct-job");
    form?.classList.remove("has-queued-direct-turn");
    if (input) input.placeholder = selectedComposerImageModel() ? "描述你想生成的图片（仅使用本条文字）" : "继续追问或提出新的研究问题";
    if (send) send.setAttribute("aria-label", "发送问题");
    renderComposerSendButtons();
    return;
  }
  const paused = job.status === "paused" && !job.pauseRequested;
  if (!job.queue.length) {
    surface.hidden = true;
    surface.innerHTML = "";
    surface.classList.remove("has-queue");
    form?.classList.add("has-live-direct-job");
    form?.classList.remove("has-queued-direct-turn");
    if (input) input.placeholder = paused ? "点击播放键检查结果或重新生成" : "输入下一条消息";
    if (send) send.setAttribute("aria-label", paused ? "检查暂停结果" : "发送下一条消息");
    renderComposerSendButtons();
    return;
  }
  const edit = directQueueEdit?.conversationId === job.conversationId ? directQueueEdit : null;
  const queueRows = job.queue.map((turn) => {
    const queueId = escapeHtml(turn.queueId);
    const steerDisabled = paused || turn.imageModel || job.currentTurn?.imageModel || job.steeringPending ? "disabled" : "";
    if (edit?.queueId === turn.queueId) {
      return `<li class="direct-live-queue-item is-editing" data-direct-queue-item data-queue-id="${queueId}" data-queue-conversation-id="${escapeHtml(job.conversationId)}"><input type="text" data-queue-edit-input data-queue-id="${queueId}" value="${escapeHtml(edit.value)}" aria-label="编辑队列消息" autocomplete="off" spellcheck="false" /><div class="direct-live-queue-actions" role="group" aria-label="保存队列消息"><button type="button" class="direct-live-queue-action is-save" data-action="save-direct-queue-edit" data-queue-id="${queueId}" aria-label="保存队列消息" title="保存">${uiIcon("check")}<span>保存</span></button><button type="button" class="direct-live-queue-action" data-action="cancel-direct-queue-edit" data-queue-id="${queueId}" aria-label="取消编辑" title="取消">${uiIcon("x")}</button></div></li>`;
    }
    return `<li class="direct-live-queue-item" data-direct-queue-item data-queue-id="${queueId}" data-queue-conversation-id="${escapeHtml(job.conversationId)}"><button type="button" class="direct-live-queue-drag" data-direct-queue-drag draggable="true" aria-label="拖动以重新排序" title="拖动以重新排序">${uiIcon("grip-vertical")}</button><span class="direct-live-queue-text" title="${escapeHtml(turn.question)}">${escapeHtml(compact(turn.question, 64))}</span><div class="direct-live-queue-actions" role="group" aria-label="这条队列消息的操作"><button type="button" class="direct-live-queue-action is-immediate" data-action="set-queued-direct-mode" data-queue-id="${queueId}" data-queue-mode="steer" aria-label="立即执行这条消息" title="立即执行" ${steerDisabled}>${uiIcon("send")}<span>立即</span></button><button type="button" class="direct-live-queue-action" data-action="edit-direct-follow-up" data-queue-id="${queueId}" aria-label="编辑这条队列消息" title="编辑">${uiIcon("pen")}</button><button type="button" class="direct-live-queue-action is-danger" data-action="remove-direct-follow-up" data-queue-id="${queueId}" aria-label="删除这条队列消息" title="删除">${uiIcon("trash")}</button></div></li>`;
  }).join("");
  surface.hidden = false;
  surface.classList.add("has-queue");
  surface.innerHTML = `<div class="direct-live-queue"><ol aria-label="后续消息队列，共 ${job.queue.length} 条">${queueRows}</ol></div>`;
  form?.classList.add("has-live-direct-job");
  form?.classList.add("has-queued-direct-turn");
  if (input) input.placeholder = paused ? "点击播放键检查结果或重新生成" : "输入下一条消息";
  if (send) send.setAttribute("aria-label", paused ? "检查暂停结果" : "加入后续队列");
  renderComposerSendButtons();
}

async function setQueuedDirectTurnMode(queueId, mode) {
  const job = directChatJob();
  if (!job) return;
  const index = job.queue.findIndex((turn) => turn.queueId === String(queueId || ""));
  if (index < 0) return;
  const turn = job.queue[index];
  if (mode === "steer" && (turn.imageModel || job.currentTurn?.imageModel)) {
    toast("生图请求按队列执行，暂不支持中途调整。");
    return;
  }
  turn.deliveryMode = mode === "steer" ? "steer" : "follow-up";
  if (turn.deliveryMode !== "steer") {
    renderDirectLiveControls();
    persistDirectChatJob(job).catch((error) => console.warn("Direct chat queue mode could not be saved", error));
    return;
  }
  if (job.steeringPending || job.status === "paused") return;
  job.queue.splice(index, 1);
  job.steeringPending = true;
  renderDirectLiveControls();
  persistDirectChatJob(job).catch((error) => console.warn("Direct chat queue could not be saved", error));
  const steered = await steerDirectChat(job, turn);
  if (!steered) fallbackSteerDirectChat(job, turn);
  renderDirectLiveControls();
  renderTasks();
}

function removeQueuedDirectTurn(queueId) {
  const job = directChatJob();
  if (!job) return;
  const before = job.queue.length;
  job.queue = job.queue.filter((turn) => turn.queueId !== String(queueId || ""));
  if (job.queue.length !== before) toast("已移除后续消息");
  renderDirectLiveControls();
  renderTasks();
  persistDirectChatJob(job).catch((error) => console.warn("Direct chat queue removal could not be saved", error));
}

function updateProcessingTimers() {
  const timers = [...document.querySelectorAll("[data-processing-timer], [data-direct-job-timer]")];
  if (!timers.length) {
    if (state.processingTimer) {
      window.clearInterval(state.processingTimer);
      state.processingTimer = 0;
    }
    return;
  }
  const now = performance.now();
  timers.forEach((timer) => {
    const startedAt = Number(timer.dataset.processingTimer || timer.dataset.directJobTimer || 0);
    timer.textContent = formatProcessingDuration(Math.max(0, now - startedAt));
  });
  if (!state.processingTimer) state.processingTimer = window.setInterval(updateProcessingTimers, 250);
}

const knowledgeRetrievalToolNames = new Set([
  "search_local_evidence",
  "catalog_library_documents",
  "kb_search",
  "zotero_search",
  "zotero_fulltext",
  "obsidian_search",
  "obsidian_read",
  "notion_search",
  "notion_read",
]);

function traceToolNames(message) {
  const trace = Array.isArray(message.trace) ? message.trace : [];
  return trace.map((item) => {
    const explicitName = item?.tool_name || item?.tool || item?.name;
    if (explicitName) return String(explicitName);
    const detail = String(item?.detail || "");
    return detail.match(/(?:ScanSci 工具：|^)([a-z][a-z0-9_-]*)/i)?.[1] || "";
  }).filter(Boolean);
}

function directKnowledgeReceiptMarkup(message) {
  const scopes = Array.isArray(message.knowledgeScopes) ? message.knowledgeScopes : [];
  if (!scopes.length) return "";
  const names = scopes.map((scope) => String(scope.title || "知识库")).filter(Boolean);
  if (!names.length) return "";
  const reader = message.reader_answer || {};
  const catalog = reader.presentation === "catalog" ? (reader.catalog || {}) : null;
  const catalogIsList = catalog?.operation === "list";
  const retrieved = traceToolNames(message).some((name) => knowledgeRetrievalToolNames.has(name));
  const citationCount = Number(reader.citation_count || (reader.citations || []).length || 0);
  const citationsVerified = Boolean(message.citation_verification?.passed);
  const stateLabel = catalog ? (catalogIsList ? "已检索题录" : "已统计") : (retrieved ? "已检索" : "已选择");
  const scopeLabel = names.join("、");
  const description = catalog
    ? (catalogIsList ? `已在 ${scopeLabel} 中检索题录` : `已对 ${scopeLabel} 进行文献去重统计`)
    : (retrieved
    ? `本次回答已检索：${scopeLabel}`
    : `本次回答已选择：${scopeLabel}`);
  const evidenceCount = catalog
    ? `<em title="按文献记录去重，不是原文片段数">${Number(catalog.document_count || 0)} 篇</em>`
    : (citationsVerified && citationCount
    ? `<em title="每个脚标均可回跳到原文片段">${citationCount} 处原文</em>`
    : "");
  return `<div class="direct-knowledge-receipt ${retrieved ? "is-retrieved" : ""}" title="${escapeHtml(description)}" aria-label="${escapeHtml(description)}">${uiIcon("library")}<strong>${stateLabel}</strong><span>${escapeHtml(scopeLabel)}</span>${evidenceCount}</div>`;
}

function interactionMarkup(interaction) {
  if (!interaction || interaction.resolved) return "";
  const payload = interaction.payload || {};
  const kind = interaction.kind || "ask_user";
  const question = payload.question || payload.summary || (kind === "plan" ? "请确认执行计划" : "需要你的选择");
  const planSteps = Array.isArray(payload.steps)
    ? `<ol>${payload.steps.map((step) => `<li><strong>${escapeHtml(step.title || step.id || "步骤")}</strong>${step.description ? `<span>${escapeHtml(step.description)}</span>` : ""}</li>`).join("")}</ol>`
    : "";
  const options = kind === "plan"
    ? [
      { id: "approve", label: "批准并继续" },
      { id: "revise", label: "需要修改" },
      { id: "cancel", label: "取消" },
    ]
    : (Array.isArray(payload.options) ? payload.options : []);
  const optionButtons = options.map((option) => `<button type="button" data-action="respond-agent-interaction" data-run-id="${escapeHtml(interaction.run_id || "")}" data-interaction-id="${escapeHtml(interaction.interaction_id || "")}" data-interaction-kind="${escapeHtml(kind)}" data-response-id="${escapeHtml(option.id || option.label || "")}">${escapeHtml(option.label || option.id || "选择")}</button>`).join("");
  const freeform = kind !== "plan" && (payload.allow_freeform !== false || !options.length)
    ? `<div class="agent-interaction-freeform"><input type="text" data-interaction-input placeholder="输入你的回答" /><button type="button" data-action="respond-agent-interaction" data-run-id="${escapeHtml(interaction.run_id || "")}" data-interaction-id="${escapeHtml(interaction.interaction_id || "")}" data-interaction-kind="${escapeHtml(kind)}" data-freeform="true">继续</button></div>`
    : "";
  return `<section class="agent-interaction-card ${kind === "plan" ? "is-plan" : "is-question"}"><header><span>${kind === "plan" ? "执行计划" : "需要你的决定"}</span><b>任务已安全暂停</b></header><h3>${escapeHtml(question)}</h3>${payload.reason ? `<p>${escapeHtml(payload.reason)}</p>` : ""}${planSteps}<div class="agent-interaction-actions">${optionButtons}</div>${freeform}</section>`;
}

function renderPendingTaskFollowUp(run, question, skills = []) {
  state.conversationAutoFollow = true;
  state.lastRunRenderKey = "";
  renderRun(run);
  const answerArea = byId("answerArea");
  const runShell = answerArea?.querySelector(".run-shell");
  if (!answerArea || !runShell) return;
  let thread = runShell.querySelector(".task-conversation .conversation-thread");
  if (!thread) {
    runShell.insertAdjacentHTML("beforeend", '<section class="task-conversation is-pending" aria-label="任务消息"><div class="conversation-thread"></div></section>');
    thread = runShell.querySelector(".task-conversation .conversation-thread");
  }
  const userMessage = conversationMessageMarkup({
    role: "user",
    content: question,
    contentMarkup: `<div class="user-turn-bubble">${messageSkillTokensMarkup(skills)}<p>${renderAssistantInline(question)}</p></div>`,
    createdAt: new Date().toISOString(),
    classes: "is-pending-follow-up",
  });
  const pendingAnswer = conversationMessageMarkup({
    role: "assistant",
    content: "",
    contentMarkup: '<div class="answer-sentence"></div>',
    createdAt: new Date().toISOString(),
    model: modelIdentitySnapshot(),
    label: "自动",
    extra: '<div class="generation-indicator" role="status" aria-label="正在生成回复"><span class="generation-dots" aria-hidden="true"><i></i><i></i><i></i></span></div>',
    classes: "is-pending-follow-up",
    promptContent: question,
  });
  thread?.insertAdjacentHTML("beforeend", `${userMessage}${pendingAnswer}`);
  followLatestConversationMessage();
}

function renderFailedTaskFollowUp(run, question, error, skills = []) {
  const scrollSnapshot = conversationScrollSnapshot();
  state.lastRunRenderKey = "";
  renderRun(run);
  const answerArea = byId("answerArea");
  const runShell = answerArea?.querySelector(".run-shell");
  if (!answerArea || !runShell) return;
  let thread = runShell.querySelector(".task-conversation .conversation-thread");
  if (!thread) {
    runShell.insertAdjacentHTML("beforeend", '<section class="task-conversation" aria-label="任务消息"><div class="conversation-thread"></div></section>');
    thread = runShell.querySelector(".task-conversation .conversation-thread");
  }
  const userMessage = conversationMessageMarkup({
    role: "user",
    content: question,
    contentMarkup: `<div class="user-turn-bubble">${messageSkillTokensMarkup(skills)}<p>${renderAssistantInline(question)}</p></div>`,
    createdAt: new Date().toISOString(),
  });
  const failedAnswer = conversationMessageMarkup({
    role: "assistant",
    content: "这次追问没有完成，原有对话和任务结果仍然保留。",
    createdAt: new Date().toISOString(),
    model: run?.model || modelIdentitySnapshot(),
    label: "未完成",
    extra: `<p class="stream-error">${escapeHtml(error?.message || "请求失败，请稍后重试。")}</p>`,
    promptContent: question,
  });
  thread?.insertAdjacentHTML("beforeend", `${userMessage}${failedAnswer}`);
  restoreConversationScroll(scrollSnapshot);
}

function directEvidenceAnswerMarkup(message, index, cursor = "") {
  const reader = message.reader_answer || {};
  const sentences = Array.isArray(reader.sentences) ? reader.sentences : [];
  if (reader.presentation === "catalog") {
    const catalog = reader.catalog || {};
    const isList = catalog.operation === "list";
    const documentCount = Number(catalog.document_count || 0);
    const totalDocuments = Number(catalog.total_documents || 0);
    const terms = Array.isArray(catalog.match_terms) ? catalog.match_terms.filter(Boolean) : [];
    const items = Array.isArray(catalog.items) ? catalog.items : [];
    const hiddenCount = Number(catalog.hidden_count || 0);
    const termsMarkup = terms.length
      ? `<span class="knowledge-catalog-terms">${terms.map((term) => `<code>${escapeHtml(term)}</code>`).join("")}</span>`
      : "";
    const itemRows = items.map((item) => {
      const title = String(item.title || item.doc_id || "未命名文献");
      const year = item.publication_year ? `<time>${escapeHtml(String(item.publication_year))}</time>` : "";
      const href = String(item.reader_url || "");
      const label = href
        ? `<a href="${escapeHtml(href)}" title="打开原文阅读器">${escapeHtml(title)}</a>`
        : `<span>${escapeHtml(title)}</span>`;
      return `<li>${label}${year}</li>`;
    }).join("");
    const results = itemRows
      ? `<details class="knowledge-catalog-results"><summary>查看命中的题录 <span>${items.length}${hiddenCount ? "+" : ""} 篇</span></summary><ol>${itemRows}</ol>${hiddenCount ? `<p>为保持对话简洁，另外 ${hiddenCount} 篇可在资料库中继续筛选。</p>` : ""}</details>`
      : "";
    const scopeNote = reader.scope_note
      ? `<p class="direct-evidence-scope">${escapeHtml(reader.scope_note)}</p>`
      : "";
    return `<section class="direct-evidence-answer knowledge-catalog-answer" data-direct-evidence-answer="${index}"><header><div><small>${isList ? "题录检索" : "目录统计"} · 已索引文献</small><strong>${documentCount}<em>篇</em></strong></div><span>共 ${totalDocuments} 篇</span></header>${termsMarkup}${scopeNote}${results}${cursor}</section>`;
  }
  if (reader.presentation === "article") {
    const article = citationTextMarkup(reader.text || message.content || "", reader.citations || []);
    const scopeNote = reader.scope_note
      ? `<p class="direct-evidence-scope">${escapeHtml(reader.scope_note)}</p>`
      : "";
    return `<div class="direct-evidence-answer evidence-grounded-article" data-direct-evidence-answer="${index}">${scopeNote}<div class="answer-sentence">${article}${cursor}</div></div>`;
  }
  if (!sentences.length) {
    return message.content
      ? `<div class="answer-sentence">${citationTextMarkup(message.content, reader.citations || [])}${cursor}</div>`
      : "";
  }
  const body = sentences.map((sentence) => {
    const citations = (sentence.citation_ids || []).map(citationMarkerMarkup).join("");
    return `<p class="answer-sentence">${escapeHtml(sentence.text || "")} ${citations}</p>`;
  }).join("");
  const scopeNote = reader.presentation === "synthesis" && reader.scope_note
    ? `<p class="direct-evidence-scope">${escapeHtml(reader.scope_note)}</p>`
    : "";
  return `<div class="direct-evidence-answer" data-direct-evidence-answer="${index}">${scopeNote}${body}${cursor}</div>`;
}

function directFailureMarkup(message, index) {
  const failure = message.failure && typeof message.failure === "object" ? message.failure : {};
  const reason = String(failure.message || message.error || "这次回复没有完成，请稍后重试。");
  const detail = String(failure.detail || "").trim();
  const retryable = failure.retryable !== false;
  const retryAction = retryable
    ? `<button type="button" class="stream-retry-button" data-action="retry-direct-message" data-message-index="${index}">重试</button>`
    : "";
  return `<section class="stream-error-card" role="alert"><strong>这次回复没有完成</strong><p>${escapeHtml(reason)}</p>${detail && detail !== reason ? `<small>${escapeHtml(detail)}</small>` : ""}<div class="stream-error-actions">${retryAction}</div></section>`;
}

function lastDirectUserMessageIndex(messages = state.directMessages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.role === "user") return index;
  }
  return -1;
}

function directTurnFromHistoryMessage(message, question, messages = state.directMessages) {
  const selectedSkillIds = Array.isArray(message?.skills)
    ? message.skills
      .map((item) => String(item?.id || item || "").trim())
      .filter(Boolean)
    : [];
  const selectedSkills = selectedSkillIds.map((skillId) => skillRecord(skillId)).filter(Boolean);
  const knowledgeScopes = Array.isArray(message?.knowledgeScopes)
    ? message.knowledgeScopes
      .map((scope) => ({
        notebook_id: String(scope?.notebook_id || "").trim(),
        title: String(scope?.title || "").trim(),
      }))
      .filter((scope) => scope.notebook_id)
    : [];
  const messageIndex = messages.indexOf(message);
  const nextAssistant = messageIndex >= 0
    ? messages.slice(messageIndex + 1).find((item) => item?.role === "assistant")
    : null;
  const directChatMode = String(message?.mode || nextAssistant?.mode || (knowledgeScopes.length ? "knowledge" : "general"));
  return {
    queueId: globalThis.crypto?.randomUUID?.() || `turn-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    question: String(question || "").trim(),
    key: "chat",
    directChatMode,
    model: modelIdentitySnapshot(),
    imageModel: message?.image_model || null,
    selectedSkillIds,
    selectedSkills,
    images: Array.isArray(message?.images) ? message.images : [],
    audio: Array.isArray(message?.audio) ? message.audio : [],
    sourceFiles: Array.isArray(message?.sources) ? message.sources : [],
    knowledgeScopes,
    notebookId: knowledgeScopes[0]?.notebook_id || "",
    notebookIds: knowledgeScopes.map((scope) => scope.notebook_id),
    knowledgeScope: null,
    knowledgeScopePayloads: [],
    thinkingLevel: currentThinkingLevel(),
    webSearch: state.webSearchMode,
    agentMode: message?.agent_mode === true,
    deliveryMode: "follow-up",
    createdAt: new Date().toISOString(),
  };
}

function beginEditingLastConversationMessage(messageIndex) {
  if (state.activeTaskId || directChatJob()) return;
  const index = Number(messageIndex);
  const messages = Array.isArray(state.directMessages) ? state.directMessages : [];
  const lastIndex = lastDirectUserMessageIndex(messages);
  const message = messages[index];
  if (!Number.isInteger(index) || index !== lastIndex || message?.role !== "user") return;
  directMessageEdit = {
    conversationId: String(state.directConversationId || ""),
    messageIndex: index,
    value: String(message.content || ""),
  };
  renderDirectConversation();
  window.setTimeout(() => {
    const input = document.querySelector("[data-last-message-edit-input]");
    input?.focus({ preventScroll: true });
    input?.setSelectionRange(input.value.length, input.value.length);
  }, 0);
}

function cancelEditingLastConversationMessage() {
  if (!directMessageEdit) return;
  directMessageEdit = null;
  renderDirectConversation();
}

function saveEditingLastConversationMessage() {
  const edit = directMessageEdit;
  if (!edit) return;
  const conversationId = String(state.directConversationId || "");
  if (!conversationId || edit.conversationId !== conversationId) {
    directMessageEdit = null;
    return;
  }
  if (directChatJob(conversationId)) {
    toast("当前回复仍在处理，请等待完成后再编辑。", true);
    return;
  }
  const messages = Array.isArray(state.directMessages) ? state.directMessages : [];
  const index = Number(edit.messageIndex);
  const lastIndex = lastDirectUserMessageIndex(messages);
  const message = messages[index];
  if (!Number.isInteger(index) || index !== lastIndex || message?.role !== "user") {
    cancelEditingLastConversationMessage();
    return;
  }
  const question = String(edit.value || "").trim();
  if (!question) {
    toast("消息不能为空", true);
    return;
  }
  const turn = directTurnFromHistoryMessage(message, question, messages);
  turn.branchId = globalThis.crypto?.randomUUID?.() || `branch-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  turn.branchFromMessageId = String(message.message_id || "");
  turn.branchReason = "edit_last_user_message";
  const retainedMessages = messages
    .slice(0, index)
    .filter((item) => item && !item.streaming);
  directMessageEdit = null;
  state.directMessages = retainedMessages;
  state.sessionId = null;
  state.sessionStats = null;
  state.sessionTokens = 0;
  state.contextUsagePercent = 0;
  window.localStorage.removeItem("scansci.active.session");
  state.conversationAutoFollow = true;
  renderDirectConversation({ forceFollow: true });
  beginDirectChatJob(turn);
  toast("已按修改后的消息重新生成");
}

async function retryDirectMessage(index) {
  if (composerSubmissionInFlight || directChatJob()) {
    toast("当前对话仍在处理；可将这条重试加入后续队列，或先停止当前回复。", true);
    return;
  }
  const failedIndex = Number(index);
  const failed = state.directMessages[failedIndex];
  if (!failed || failed.role !== "assistant") return;
  const userIndex = [...state.directMessages.slice(0, failedIndex)]
    .map((item, itemIndex) => ({ item, itemIndex }))
    .reverse()
    .find(({ item }) => item.role === "user")?.itemIndex;
  const user = Number.isInteger(userIndex) ? state.directMessages[userIndex] : null;
  if (!user?.content) {
    toast("找不到这条消息对应的问题，请重新输入。", true);
    return;
  }
  const conversationId = String(state.directConversationId || "");
  try {
    const snapshot = await request(`/api/chat/submissions/${encodeURIComponent(user.message_id)}?conversation_id=${encodeURIComponent(conversationId)}`);
    if (directChatJob(conversationId) || state.directConversationId !== conversationId || state.directMessages[failedIndex] !== failed) return;
    if (snapshot.state === "running") {
      toast("原请求仍在执行，请稍后再检查结果。", true);
      return;
    }
    if (snapshot.state === "completed" && snapshot.terminal) {
      const result = snapshot.terminal.result || snapshot.terminal;
      const recoveredSessionId = String(result.agent_runtime?.session?.session_id || state.sessionId || "");
      state.directMessages[failedIndex] = {
        ...failed,
        ...(result.message || {}),
        message_id: failed.message_id,
        parent_message_id: user.message_id,
        model: modelIdentitySnapshot(result.model || result.message?.model || failed.model),
        streaming: false,
        paused: false,
        error: "",
        failure: null,
      };
      try {
        await persistDirectConversationSnapshot({ conversationId, messages: state.directMessages, sessionId: recoveredSessionId });
      } catch (error) {
        state.directMessages[failedIndex] = failed;
        throw error;
      }
      state.sessionId = recoveredSessionId || null;
      renderDirectConversation();
      toast("已恢复上一轮完成的结果");
      return;
    }
    if (!["uncertain", "unreplayable", "failed", "completed", "missing", "abandoned"].includes(snapshot.state)) {
      toast("无法确认原请求状态，请查看历史后再操作。", true);
      return;
    }
    const confirmed = await requestConfirmation({
      eyebrow: "重新生成",
      title: "重新生成这条回复？",
      message: "上一轮可能已经执行工具。请先检查已生成的文件或其他结果；重新生成可能再次执行相同操作。",
      confirmLabel: "重新生成",
      danger: true,
    });
    if (!confirmed) return;
    if (snapshot.state === "missing") {
      const fenced = await request(`/api/chat/submissions/${encodeURIComponent(user.message_id)}/abandon`, {
        method: "POST",
        body: JSON.stringify({ conversation_id: conversationId }),
      });
      if (fenced.state !== "abandoned") {
        toast("原请求状态已变化，请再次检查。", true);
        return;
      }
    }
    if (directChatJob(conversationId) || state.directConversationId !== conversationId || state.directMessages[failedIndex] !== failed) return;
    const turn = directTurnFromHistoryMessage(user, user.content, state.directMessages);
    turn.branchId = globalThis.crypto?.randomUUID?.() || `branch-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    turn.branchFromMessageId = String(user.message_id || "");
    turn.branchReason = "retry_failed_assistant";
    state.directMessages = state.directMessages
      .slice(0, userIndex)
      .filter((message) => message && !message.streaming);
    state.sessionId = null;
    state.sessionStats = null;
    state.sessionTokens = 0;
    state.contextUsagePercent = 0;
    window.localStorage.removeItem("scansci.active.session");
    state.conversationAutoFollow = true;
    renderDirectConversation({ forceFollow: true });
    beginDirectChatJob(turn);
    toast("正在重新生成，失败的尝试已保留在分支记录中");
  } catch (error) {
    toast(error.message, true);
  }
}

function renderDirectConversation({ forceFollow = false } = {}) {
  const conversationId = String(state.directConversationId || "");
  const activeJob = directChatJob(conversationId);
  if (directMessageEdit && (directMessageEdit.conversationId !== conversationId || activeJob)) {
    directMessageEdit = null;
  }
  const lastUserIndex = lastDirectUserMessageIndex(state.directMessages);
  const scrollSnapshot = conversationScrollSnapshot();
  const turns = state.directMessages.map((message, index) => {
    if (message.role === "user") {
      const editable = index === lastUserIndex && !activeJob;
      const editing = editable && directMessageEdit?.messageIndex === index;
      const attachments = `${messageSkillTokensMarkup(message.skills || [])}${composerSourcePreviewMarkup(message.sources || [])}${composerImagePreviewMarkup(message.images || [])}${composerAudioPreviewMarkup(message.audio || [])}`;
      const userContent = editing
        ? `<div class="user-turn-bubble is-editing">${attachments}<textarea rows="3" data-last-message-edit-input data-message-index="${index}" aria-label="编辑最后一条消息" autocomplete="off" spellcheck="false">${escapeHtml(directMessageEdit.value)}</textarea><p class="message-edit-hint">Ctrl/⌘ + Enter 保存并重新生成 · Esc 取消</p></div>`
        : `<div class="user-turn-bubble">${attachments}<p>${renderAssistantInline(message.content)}</p></div>`;
      return conversationMessageMarkup({
        role: "user",
        content: message.content,
        contentMarkup: userContent,
        createdAt: message.created_at,
        editable,
        editing,
        messageIndex: index,
      });
    }
    const duration = Number(message.processing_ms || 0);
    const processing = `${directKnowledgeReceiptMarkup(message)}${processTraceMarkup(message, duration, index)}`;
    const cursor = message.streaming && message.content ? '<span class="stream-caret" aria-label="正在生成"></span>' : "";
    const answer = message.reader_answer
      ? directEvidenceAnswerMarkup(message, index, cursor)
      : (message.content ? `<div class="answer-sentence">${renderAssistantContent(message.content)}${cursor}</div>` : "");
    const promptContent = [...state.directMessages.slice(0, index)]
      .reverse().find((item) => item.role === "user")?.content || "";
    const generatedImages = generatedImageArtifactsMarkup(message.artifacts, {
      promptContent, usageMarkup: messageUsageMarkup("assistant", message.content, message.usage, promptContent),
    });
    const visionRoute = message.agent_runtime?.vision_route || null;
    const visionFallback = message.agent_runtime?.vision_fallback || null;
    const visionFallbackNotice = visionFallback?.to_model
      ? `<p class="vision-route-notice is-fallback">原视觉模型 ${escapeHtml(visionFallback.from_model || "当前模型")} 暂时不可用，已自动切换到 ${escapeHtml(visionFallback.to_provider_name || visionFallback.to_provider || "备用服务商")} 的 ${escapeHtml(visionFallback.to_model)}。</p>`
      : "";
    const visionNotice = visionRoute?.model_id
      ? `${visionFallbackNotice}<p class="vision-route-notice">图片由 ${escapeHtml(visionRoute.provider_name || visionRoute.provider_id || "本地视觉模型")} 的 ${escapeHtml(visionRoute.model_id)} 处理${visionRoute.mode === "cloud" ? "（云端）" : visionRoute.mode === "ocr-fallback" ? "（OCR + 文本模型）" : "（本地）"}</p>`
      : visionFallbackNotice;
    const generation = message.streaming ? '<div class="generation-indicator" role="status" aria-label="正在生成回复"><span class="generation-dots" aria-hidden="true"><i></i><i></i><i></i></span></div>' : "";
    const paused = message.paused ? '<p class="stream-paused" role="status">已暂停，点击播放图标检查结果；重新生成前会提示确认</p>' : "";
    const error = message.error && !message.paused ? directFailureMarkup(message, index) : "";
    const interaction = interactionMarkup(message.interaction);
    const modeLabel = composerModeLabels[message.mode] || "通用对话";
    return conversationMessageMarkup({
      role: "assistant",
      content: message.content,
      contentMarkup: visionNotice + answer + generatedImages,
      createdAt: message.created_at,
      usage: message.usage,
      model: message.model,
      label: modeLabel,
      processing,
      extra: `${interaction}${generation}${paused}${error}`,
      classes: "direct-answer",
      promptContent,
      imageReply: Boolean(generatedImages),
    });
  }).join("");
  byId("answerArea").innerHTML = `<article class="conversation-thread">${turns}</article>`;
  byId("answerArea").querySelectorAll(".answer-processing[data-message-index]").forEach((details) => {
    details.querySelector("summary")?.addEventListener("click", () => {
      const message = state.directMessages[Number(details.dataset.messageIndex)];
      if (message) message.reasoning_open = !details.open;
    });
  });
  renderDirectLiveControls();
  updateProcessingTimers();
  state.directMessages.forEach((message, index) => {
    if (message.role !== "assistant" || !message.reader_answer?.citations?.length) return;
    const scope = byId("answerArea")?.querySelector(`[data-direct-evidence-answer="${index}"]`);
    if (scope) bindCitationInteractions({ reader_answer: message.reader_answer }, scope);
  });
  restoreConversationScroll(scrollSnapshot, { forceFollow });
}

function composerMode(inputId) {
  if (inputId === "reviewQuestionInput") return "writing";
  const selectId = inputId === "homeQuestionInput" ? "homeModeSelect" : "chatModeSelect";
  return byId(selectId)?.value || "general";
}

function setAgentMode(enabled, { announce = true } = {}) {
  state.agentMode = enabled === true;
  window.localStorage.setItem("scansci.agent-mode", String(state.agentMode));
  document.querySelectorAll(".composer-agent-button").forEach((button) => {
    button.classList.toggle("is-active", state.agentMode);
    button.setAttribute("aria-pressed", String(state.agentMode));
  });
  if (announce) toast(state.agentMode
    ? "Agent 已开启：Pi 会按任务规划并调用已授权的工具。"
    : "Agent 已关闭：普通对话继续使用自动路由。");
}

function isCasualConversationMessage(text = "") {
  const normalized = String(text || "")
    .trim()
    .toLocaleLowerCase()
    .replace(/[\s,，。.!！?？~～]+/g, "");
  return [
    "你好", "您好", "嗨", "哈喽", "早上好", "下午好", "晚上好", "晚安",
    "谢谢", "多谢", "hello", "hi", "hey", "thankyou", "thanks",
    "goodmorning", "goodafternoon", "goodevening", "goodnight", "bye",
  ].includes(normalized);
}

function resolveResearchComposerMode(text) {
  if (["academic", "deep-research"].includes(state.researchWorkflow)) return state.researchWorkflow;
  const query = String(text || "");
  if (/(深度|系统综述|研究进展|证据分歧|争议|开放问题|全面调研|deep research)/i.test(query)) return "deep-research";
  return "academic";
}

function academicWritingIsEditingOnly(text = "") {
  const request = String(text || "").trim();
  if (!request) return false;
  const asksForSources = /(联网|检索|调研|综述|文献|证据|引用|参考文献|来源|研究进展|研究现状|争议|开放问题|doi)/i.test(request);
  const editingTask = /(润色|改写|翻译|校对|纠错|降重|续写|压缩|精简|调整语气|修改语法|优化表达)/i.test(request);
  return editingTask && !asksForSources;
}

function academicWritingArtifactRoute(mode, text, { searchableKnowledgeSelected = false } = {}) {
  if (mode !== "writing") return null;
  const documentMetadata = {
    document_kind: "academic_writing",
    task_origin: "academic_writing",
    writing_brief: {
      audience: "researcher",
      tone: "academic",
      length: "long",
      focus: "形成可追溯的学术稿件；实质性论断必须绑定可预览、可回到原始来源的引用",
    },
  };
  // “强制联网” is authoritative. Deep Research intentionally uses only
  // task-acquired public sources, so the document can say exactly where each
  // citation came from instead of silently mixing it with a personal library.
  if (state.webSearchMode === "on") {
    return {
      workflowType: "deep_research",
      workflowInput: { question: text, limit: 36, max_search_rounds: 2, max_fulltext: 4, ...documentMetadata },
    };
  }
  if (searchableKnowledgeSelected) {
    return {
      workflowType: "literature_review",
      workflowInput: { question: text, ...documentMetadata },
    };
  }
  // In automatic mode, ordinary academic composition is source-backed by
  // default. Pure editing remains a low-latency direct conversation, and
  // “不联网” always keeps the request with the selected writing model.
  if (state.webSearchMode === "auto" && !academicWritingIsEditingOnly(text)) {
    return {
      workflowType: "deep_research",
      workflowInput: { question: text, limit: 36, max_search_rounds: 2, max_fulltext: 4, ...documentMetadata },
    };
  }
  return null;
}

function composerRun(mode, text, images = [], sourceFiles = []) {
  // Keep routing lazy.  Building an object containing parseNoveltyPrompt(text)
  // eagerly ran the novelty validator for writing, slides, and general chat as
  // well, which made every mode show the same two-part novelty error.
  if (mode === "writing") return { workflowType: "literature_review", workflowInput: { question: text } };
  if (mode === "academic") return { workflowType: "academic_search", workflowInput: { ...parseAcademicSearchPrompt(text), limit: 24, per_source: 10 } };
  if (mode === "deep-research") {
    return { workflowType: "deep_research", workflowInput: { question: text, limit: 36, max_search_rounds: 2, max_fulltext: 4 } };
  }
  if (mode === "idea") {
    return { workflowType: "research_idea", workflowInput: { ...parseResearchIdeaPrompt(text), limit: 40, max_search_rounds: 2, max_fulltext: 6, retrieval_quality: "precision" } };
  }
  if (mode === "novelty") {
    return { workflowType: "novelty_check", workflowInput: { ...parseNoveltyPrompt(text), limit: 40, max_search_rounds: 2, max_fulltext: 6, retrieval_quality: "precision" } };
  }
  if (mode === "knowledge") {
    if (state.evidenceOutputMode === "review") {
      return {
        workflowType: "literature_review",
        workflowInput: {
          question: text,
          writing_brief: {
            audience: "researcher",
            tone: "academic",
            length: "long",
            focus: "按主题组织不同来源的共同发现、分歧、证据边界与开放问题",
          },
        },
      };
    }
    return { workflowType: "ask", workflowInput: { question: text, task_mode: "evidence" } };
  }
  if (mode === "slides") {
    return sourceFiles.length
      ? { workflowType: "pdf_to_ppt", workflowInput: { topic: text, source_files: sourceFiles, template_id: state.selectedSlideTemplateId } }
      : { workflowType: "ppt_project", workflowInput: { topic: text, template_id: state.selectedSlideTemplateId } };
  }
  if (mode === "download") {
    const identifiers = extractBatchIdentifiers(text);
    if (identifiers.length > 1) {
      return { workflowType: "paper_download_batch", workflowInput: { identifiers, strategy: state.downloadStrategy } };
    }
    if (identifiers.length === 1) {
      return { workflowType: "paper_download", workflowInput: { identifier: identifiers[0], strategy: state.downloadStrategy } };
    }
    return { workflowType: "paper_search_download", workflowInput: { ...parsePaperSearchDownloadPrompt(text), strategy: state.downloadStrategy } };
  }
  return { workflowType: "ask", workflowInput: { question: text, ...(images.length ? { images } : {}) } };
}

function parseAcademicSearchPrompt(text) {
  const rawQuery = String(text || "").trim();
  const labelledTopic = rawQuery.match(/(?:^|\n)\s*(?:研究主题|检索主题|主题|topic|research\s+topic)\s*[:：]\s*([^\n]+)/i);
  const query = String(labelledTopic?.[1] || rawQuery).trim();
  if (!query) throw new Error("请填写研究主题");
  // `raw_query` preserves the user's request in the task history. The backend
  // independently re-extracts `query` before it contacts any academic API.
  return { query, raw_query: rawQuery };
}

const academicProviderLabels = Object.freeze({
  openalex: "OpenAlex",
  "semantic-scholar": "Semantic Scholar",
  crossref: "Crossref",
  pubmed: "PubMed",
  "europe-pmc": "Europe PMC",
  arxiv: "arXiv",
  openreview: "OpenReview",
  dblp: "DBLP",
});

function closeAcademicSearchPlanDialog({ clear = true } = {}) {
  const dialog = byId("academicSearchPlanDialog");
  if (dialog?.open) dialog.close();
  if (clear) state.academicSearchPlanDraft = null;
}

function renderAcademicSearchPlanDialog() {
  const draft = state.academicSearchPlanDraft;
  if (!draft) return;
  const plan = draft.plan || {};
  const topic = String(plan.normalized_topic || plan.topic || draft.request.query || "—");
  const variants = Array.isArray(plan.query_variants) ? plan.query_variants : [];
  const selected = new Set(Array.isArray(plan.providers) ? plan.providers : []);
  byId("academicSearchPlanTopic").textContent = topic;
  byId("academicSearchPlanQueries").value = variants.join("\n");
  byId("academicSearchPlanYear").value = draft.yearFrom || "";
  byId("academicSearchPlanLimit").value = String(draft.limit || 24);
  const sourceContainer = byId("academicSearchPlanSources");
  sourceContainer.innerHTML = Object.entries(academicProviderLabels).map(([id, label]) => `<label class="academic-search-plan-source"><input type="checkbox" name="academic-search-provider" value="${escapeHtml(id)}" ${selected.has(id) ? "checked" : ""} /><span>${escapeHtml(label)}</span></label>`).join("");
}

async function openAcademicSearchPlan(question, { inputId, key, sourceFiles = [], images = [], skills = [] } = {}) {
  const requestPayload = parseAcademicSearchPrompt(question);
  const plan = await request("/api/academic-search/plan", {
    method: "POST",
    body: JSON.stringify(requestPayload),
  });
  state.academicSearchPlanDraft = {
    request: requestPayload,
    plan,
    inputId,
    key,
    sourceFiles,
    images,
    skills,
    limit: 24,
    // Preserve a temporal constraint inferred by the host planner.  The
    // dialog remains editable, but a request such as "2022 年以来" must not
    // silently become an unbounded provider search after confirmation.
    yearFrom: plan.year_from ?? "",
  };
  renderAcademicSearchPlanDialog();
  const dialog = byId("academicSearchPlanDialog");
  if (dialog && !dialog.open) dialog.showModal();
  window.setTimeout(() => byId("academicSearchPlanQueries")?.focus(), 0);
}

function reviewedAcademicSearchInput() {
  const draft = state.academicSearchPlanDraft;
  if (!draft) throw new Error("检索计划已失效，请重新提交问题。");
  const queryVariants = [...new Set(String(byId("academicSearchPlanQueries")?.value || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean))].slice(0, 3);
  const providers = [...document.querySelectorAll('input[name="academic-search-provider"]:checked')]
    .map((input) => String(input.value || ""))
    .filter(Boolean);
  if (!queryVariants.length) throw new Error("请至少保留一条检索式。");
  if (!providers.length) throw new Error("请至少选择一个公开学术来源。");
  const yearValue = String(byId("academicSearchPlanYear")?.value || "").trim();
  const year = yearValue ? Number(yearValue) : null;
  if (year !== null && (!Number.isInteger(year) || year < 1800 || year > 2100)) {
    throw new Error("起始年份需要在 1800 到 2100 之间。");
  }
  const limit = Math.max(1, Math.min(50, Number(byId("academicSearchPlanLimit")?.value || draft.limit || 24)));
  return {
    query: draft.request.query,
    raw_query: draft.request.raw_query,
    providers,
    limit,
    per_source: limit > 24 ? 18 : 10,
    ...(draft.skills?.length ? { skills: draft.skills } : {}),
    ...(year !== null ? { year_from: year } : {}),
    search_plan: {
      query_variants: queryVariants,
      providers,
      reviewed_by_user: true,
    },
  };
}

async function startReviewedAcademicSearch() {
  const draft = state.academicSearchPlanDraft;
  const submit = byId("academicSearchPlanSubmit");
  if (!draft || !submit) return;
  submit.disabled = true;
  try {
    const input = reviewedAcademicSearchInput();
    const run = await createResearchRun("academic_search", input);
    closeAcademicSearchPlanDialog();
    state.activeTaskId = run.run_id;
    window.localStorage.setItem("scansci.active.task", run.run_id);
    state.sessionId = `research-run-${run.run_id}`;
    window.localStorage.setItem("scansci.active.session", state.sessionId);
    state.conversationAutoFollow = true;
    byId("conversationTitle").textContent = compact(draft.request.raw_query, 80);
    applyContextPanelPreset("none");
    setView("conversation");
    upsertRun(run);
    updateSessionStats(estimateRunSessionStats(run));
    renderRun(run);
    watchRun(run.run_id, (next) => {
      if (state.activeView === "conversation" && state.activeTaskId === next.run_id) renderRun(next);
    });
    const sourceInput = byId(draft.inputId);
    if (sourceInput) sourceInput.value = "";
    clearComposerSkills(draft.key);
    clearComposerImages(draft.key);
    clearComposerSources(draft.key);
  } finally {
    submit.disabled = false;
  }
}

function parsePaperSearchDownloadPrompt(text) {
  const clean = String(text || "").trim();
  const authorLabel = clean.match(/(?:^|\n)\s*(?:作者|author)\s*[:：]\s*([^\n]+)/i);
  const peterReich = /Peter\s+B\.?\s+Reich/i.test(clean) ? "Peter B. Reich" : "";
  const topicLabel = clean.match(/(?:^|\n)\s*(?:主题|topic)\s*[:：]\s*([^\n]+)/i);
  const limitMatch = clean.match(/(?:TOP\s*|前\s*|数量\s*[:：]?\s*)(\d{1,2})/i);
  const yearMatch = clean.match(/(19\d{2}|20\d{2})\s*年?\s*(?:以来|之后|至今)/);
  const author = String(authorLabel?.[1] || peterReich).trim();
  const query = String(topicLabel?.[1] || (author ? "" : clean)).trim();
  if (!query && !author) throw new Error("请给出检索主题或作者姓名");
  return {
    query,
    author,
    limit: Math.max(1, Math.min(50, Number(limitMatch?.[1] || 20))),
    sort: /(?:TOP\s*\d+|高被引|被引量|引用量|most[- ]cited)/i.test(clean) ? "cited_by_count" : "relevance",
    ...(yearMatch ? { year_from: Number(yearMatch[1]) } : {}),
  };
}

function parseResearchIdeaPrompt(text) {
  const clean = String(text || "").trim();
  if (!clean) throw new Error("研究构思需要一个明确的研究方向。");
  const labeled = clean.match(/(?:^|\n)\s*(?:方向|研究方向|direction)\s*[：:]\s*([\s\S]*?)(?=\n\s*(?:约束|限制|constraints?)\s*[：:]|$)(?:[\s\S]*?\n\s*(?:约束|限制|constraints?)\s*[：:]\s*([\s\S]+))?$/i);
  if (labeled) return { direction: labeled[1].trim(), constraints: String(labeled[2] || "").trim() };
  return { direction: clean, constraints: "" };
}

function parseNoveltyPrompt(text) {
  const clean = String(text || "").trim();
  const labeled = clean.match(/(?:^|\n)\s*(?:问题|研究问题|problem)\s*[：:]\s*([\s\S]*?)(?=\n\s*(?:新颖性|创新点|主张|novelty)\s*[：:]|$)[\s\S]*?\n\s*(?:新颖性|创新点|主张|novelty)\s*[：:]\s*([\s\S]+)$/i);
  if (labeled) return { problem: labeled[1].trim(), novelty: labeled[2].trim() };
  const blocks = clean.split(/\n\s*\n/).map((value) => value.trim()).filter(Boolean);
  if (blocks.length >= 2) return { problem: blocks[0], novelty: blocks.slice(1).join("\n\n") };
  throw new Error("证据查新需要两部分：先写“问题：…”，再写“新颖性：…”。");
}

function reviewWorkflowPreferences() {
  const sourceDocIds = [...document.querySelectorAll("[data-review-source]:checked")]
    .map((item) => String(item.value || "").trim())
    .filter(Boolean);
  if (sourceDocIds.length < 2) throw new Error("文献综述请至少选择两篇用于比较和写作的来源。");
  return {
    source_doc_ids: sourceDocIds,
    writing_brief: {
      audience: byId("reviewAudience")?.value || "researcher",
      tone: byId("reviewTone")?.value || "academic",
      length: byId("reviewLength")?.value || "standard",
      focus: byId("reviewFocus")?.value?.trim() || "",
    },
  };
}

function setReviewSourceSelection(checked) {
  document.querySelectorAll("[data-review-source]").forEach((item) => { item.checked = checked; });
  updateReviewSourceCount();
}

function updateReviewSourceCount() {
  const target = document.querySelector("[data-review-selected-count]");
  if (target) target.textContent = String(document.querySelectorAll("[data-review-source]:checked").length);
}

function setEvidenceOutputMode(value) {
  const next = value === "review" ? "review" : "answer";
  if (state.evidenceOutputMode === next) return;
  state.evidenceOutputMode = next;
  renderHomeModeWorkbench("knowledge");
  const placeholder = next === "review"
    ? "输入综述主题；将组织原文证据并逐句引用"
    : "基于已选知识库提问，关键结论会附原文证据";
  for (const id of ["homeQuestionInput", "chatQuestionInput"]) {
    const composer = byId(id);
    if (composer && composerMode(id) === "knowledge") composer.placeholder = placeholder;
  }
}

const composerModeLabels = { general: "自由输入", academic: "学术搜索", knowledge: "证据问答", research: "研究", writing: "学术写作", slides: "学术 PPT", download: "文献下载" };
const composerModeIcons = { general: "wand", academic: "search", knowledge: "message-circle", research: "search", writing: "pen", slides: "presentation", download: "download" };
const modeWorkbenchContent = {
  academic: {
    title: "学术搜索",
    description: "从问题出发：先找关键论文；需要系统调研时，启动外部深度研究。",
    hint: "选择一种方式，填写问题后发送",
    tools: [],
    examples: [
      { id: "find-papers", workflow: "academic", icon: "search", title: "快速找到关键论文", text: "多源检索、去重和 DOI 核验，先得到可继续筛选的论文集合。", prompt: "请检索 2022 年以来关于「检索增强生成中的事实一致性评估」的关键论文，优先方法论文与公开基准。", tags: ["多源检索", "DOI 核验", "结果去重"] },
      { id: "deep-research", workflow: "deep-research", icon: "book", title: "深度研究", text: "自动拆解问题、分轮检索、追踪证据缺口，并交付带来源的研究报告。", prompt: "请围绕以下问题进行深度研究：先制定检索计划，再执行多轮学术检索与来源对照；只依据可追溯的公开学术来源输出结论，并清楚标记证据不足处。\n\n研究问题：", tags: ["多轮检索", "来源对照", "研究报告"] },
    ],
  },
  knowledge: {
    title: "证据问答",
    description: "只基于已选择的本地知识库回答；每个可验证结论都可回到对应原文证据。",
    tools: [
      { id: "evidence-answer", evidenceOutput: "answer", label: "问答", icon: "message-circle" },
      { id: "evidence-review", evidenceOutput: "review", label: "证据综述", icon: "book-open" },
    ],
    examples: [
      { id: "compare-sources", icon: "message-circle", title: "比较不同来源的结论", text: "区分共同发现、分歧之处和证据强弱；答案中的关键句可定位到原文。", prompt: "请比较已选择知识库中不同文献对以下问题的结论，说明一致观点、分歧与证据局限，并为每个关键结论标注原文证据：\n\n问题：", tags: ["仅本地资料", "原文证据", "观点比较"] },
      { id: "find-evidence", icon: "search", title: "从资料中找依据", text: "把一个具体判断拆成可验证的证据片段，并指出现有资料是否足够支持。", prompt: "请在已选择知识库中查找支持或反驳以下判断的原文证据；如果资料不足，请明确说明缺少什么：\n\n判断：", tags: ["支持 / 反驳", "证据片段", "不足提示"] },
    ],
  },
  writing: {
    title: "写作起步",
    description: "选择一个常见结构，或直接写下你的要求。",
    tools: [
      { id: "review", label: "文献综述", icon: "book", prompt: "请围绕以下主题撰写一篇有逐句证据引用的中文文献综述：\n\n研究主题：\n时间范围：近 5 年\n重点：研究进展、方法差异、争议与未来方向" },
      { id: "draft", label: "论文初稿", icon: "file-plus", prompt: "请根据我的研究问题和现有资料起草论文初稿。\n\n研究问题：\n核心发现：\n目标读者或期刊：" },
      { id: "polish", label: "改写润色", icon: "wand", prompt: "请在不改变事实与引用的前提下，润色下面这段学术文字，使论证更紧凑、术语更一致：\n\n" },
      { id: "citation", label: "引用格式", icon: "copy", prompt: "请检查并统一以下内容的文内引用和参考文献格式。\n\n目标格式：GB/T 7714（作者-出版年）\n内容：\n" },
    ],
    examples: [
      { id: "topic-review", icon: "wand", title: "我只有一个主题", text: "从研究主题出发，梳理进展、技术路线、挑战和未来方向。", prompt: "请写一篇关于「钙钛矿太阳能电池稳定性提升策略」的中文综述初稿，重点梳理研究进展、技术路线、挑战和未来方向。", tags: ["中文初稿", "研究进展", "未来方向"] },
      { id: "target-journal", icon: "book", title: "我有目标期刊", text: "按目标期刊的读者、篇幅和结构组织综述。", prompt: "请写一篇关于「单细胞测序在肿瘤微环境研究中的应用」的综述，目标投稿期刊为 Nature Reviews Cancer。", tags: ["目标期刊", "投稿要求", "生成配图"] },
      { id: "bounded-sources", icon: "filter", title: "我想限定文献范围", text: "只使用指定年代、期刊或资料库中的证据。", prompt: "请写一篇关于「机器学习在材料性能预测中的应用」的中文综述初稿，只参考近 5 年英文论文，并逐句标注证据来源。", tags: ["近 5 年", "英文论文", "逐句引用"] },
      { id: "outline-writing", icon: "sliders", title: "我想按指定大纲写", text: "把已有章节结构转成可继续编辑的证据化初稿。", prompt: "请按以下大纲撰写「固态锂电池界面稳定性研究进展」的中文综述：\n1. 电解质材料\n2. 界面反应机制\n3. 表征方法\n4. 改性策略\n5. 开放问题", tags: ["指定大纲", "章节写作", "证据化"] },
    ],
  },
  slides: {
    title: "选择幻灯片的起点",
    description: "可以从文件、研究主题或现成大纲生成可编辑演示文稿。",
    tools: [
      { id: "source", label: "从文件生成", icon: "file-plus", action: "upload" },
      { id: "outline", label: "从大纲生成", icon: "sliders", prompt: "请根据以下大纲制作一套学术汇报幻灯片：\n\n汇报主题：\n听众：\n时长：\n大纲：\n" },
      { id: "template", label: "选择模板", icon: "layout", action: "template" },
      { id: "speaker-notes", label: "演讲者备注", icon: "message-circle", prompt: "请为下面的学术汇报生成幻灯片，并为每页补充简洁的演讲者备注：\n\n主题：\n听众：\n预计时长：" },
    ],
    examples: [
      { id: "paper-talk", icon: "file-plus", title: "把论文做成汇报", text: "提取论文问题、方法、结果和局限，生成组会或答辩演示。", prompt: "请把我添加的论文制作成 12 页组会汇报，重点解释研究问题、方法、主要发现与局限。", tags: ["组会汇报", "12 页", "保留引用"] },
      { id: "proposal", icon: "presentation", title: "制作开题答辩", text: "围绕研究背景、问题、方案、创新点和计划组织叙事。", prompt: "请制作一套硕士开题答辩幻灯片。主题：\n研究问题：\n拟采用方法：\n预期创新：\n研究计划：", tags: ["开题答辩", "研究方案", "时间计划"] },
      { id: "conference", icon: "audio", title: "准备学术会议报告", text: "按限定时长压缩内容，突出贡献并生成讲稿提示。", prompt: "请制作一套 15 分钟学术会议报告幻灯片，突出工作的核心贡献、关键证据和可复现细节，并生成演讲者备注。", tags: ["15 分钟", "核心贡献", "讲稿提示"] },
      { id: "teaching", icon: "book", title: "制作课程讲义", text: "从概念到案例组织教学节奏，并加入回顾问题。", prompt: "请制作一套面向研究生的课程幻灯片。主题：Transformer 的注意力机制。要求包含直观解释、公式、示例和课后思考题。", tags: ["研究生课程", "概念图解", "思考题"] },
    ],
  },
  download: {
    title: "批量下载论文",
    description: "提供清单，或让 ScanSci 先检索再获取可用全文。",
    guides: [
      { id: "list", icon: "file-plus", index: "01", title: "我已有文献清单", text: "添加 TXT、BIB 或 CSV，自动识别 DOI 与 arXiv ID。" },
      { id: "topic", icon: "search", index: "02", title: "按主题检索后下载", text: "写明主题、年份、数量等限制，先筛选再批量获取。", prompt: "主题：\n限制：近 5 年、开放获取优先\n数量：20" },
      { id: "peter-reich", icon: "book", index: "03", title: "按学者高被引论文下载", text: "直接运行一个作者与被引量排序的完整示例。", prompt: "作者：Peter B. Reich\n排序：被引量\n数量：20" },
    ],
  },
};

function homePromptFromWorkbench(prompt, message = "已填入输入框，可继续修改") {
  const input = byId("homeQuestionInput");
  if (!input || !prompt) return;
  input.value = prompt;
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  toast(message);
}

function renderHomeBatchAttachment() {
  const attachment = byId("homePaperBatchAttachment");
  if (!attachment) return;
  const count = state.pendingBatchIdentifiers.length;
  attachment.hidden = !count;
  attachment.innerHTML = count
    ? `${uiIcon("file-plus")}<span><strong>${escapeHtml(state.pendingBatchFilename || "文献清单")}</strong><small>已识别 ${count} 个 DOI / arXiv ID</small></span><button type="button" data-action="clear-home-batch" aria-label="移除文献清单">${uiIcon("x")}</button>`
    : "";
  hydrateIcons(attachment);
}

function renderDownloadGuides(content) {
  const count = state.pendingBatchIdentifiers.length;
  const cards = content.guides.map((guide) => {
    const isList = guide.id === "list";
    const action = isList ? (count ? "start-home-batch-download" : "pick-home-batch-file") : "apply-download-guide";
    const useLabel = isList ? (count ? `下载已识别的 ${count} 篇` : "添加清单") : guide.id === "peter-reich" ? "使用这个示例" : "填写检索条件";
    return `<button type="button" class="download-guide-card ${isList && count ? "has-attachment" : ""}" data-action="${action}" ${isList ? "" : `data-download-guide="${escapeHtml(guide.id)}"`}><span class="download-guide-index">${escapeHtml(guide.index)}</span><span class="download-guide-icon">${uiIcon(guide.icon)}</span><strong>${escapeHtml(guide.title)}</strong><p>${escapeHtml(guide.text)}</p><span class="download-guide-use">${escapeHtml(useLabel)} ${uiIcon(isList && !count ? "plus" : "arrow-up-right")}</span></button>`;
  }).join("");
  return `<header class="mode-workbench-head download-workbench-head"><div><h2>${escapeHtml(content.title)}</h2><p>${escapeHtml(content.description)}</p></div><span>PDF 保存到本地下载目录</span></header><div class="download-guide-grid">${cards}</div>`;
}

function renderInlineSlideTemplateGallery() {
  const selected = selectedSlideTemplate();
  if (!state.slideTemplatesAvailable || !state.slideTemplates.length) {
    return `<header class="mode-workbench-head slide-template-gallery-head"><div><h2>选择演示模板</h2><p>模板库暂不可用，请稍后重试。</p></div><span class="slide-template-gallery-selection is-unavailable">暂不可用</span></header>`;
  }
  const cards = state.slideTemplates.map((template) => {
    const isSelected = template.id === selected?.id;
    const description = compact(template.description || template.use_cases || template.summary || "用于学术汇报", 52);
    return `<article class="slide-template-gallery-card ${isSelected ? "is-selected" : ""}"><button type="button" class="slide-template-gallery-select" data-action="select-inline-slide-template" data-template-id="${escapeHtml(template.id)}" aria-pressed="${String(isSelected)}" aria-label="使用 ${escapeHtml(template.name)} 模板"><span class="slide-template-gallery-preview"><img src="${escapeHtml(template.preview_url)}" alt="${escapeHtml(template.name)} 模板封面" loading="lazy" />${isSelected ? `<span class="slide-template-gallery-check">${uiIcon("check")}</span>` : ""}</span><span class="slide-template-gallery-copy"><strong>${escapeHtml(template.name)}</strong><small>${escapeHtml(description)}</small></span></button><button type="button" class="slide-template-gallery-preview-action" data-action="preview-inline-slide-template" data-template-id="${escapeHtml(template.id)}" aria-label="预览 ${escapeHtml(template.name)} 模板">${uiIcon("eye")}<span>预览</span></button></article>`;
  }).join("");
  return `<header class="mode-workbench-head slide-template-gallery-head"><div><h2>选择演示模板</h2><p>选择模板即可用于本次演示；需要查看完整风格时点击“预览”。</p></div><span class="slide-template-gallery-selection">${uiIcon("presentation")}已选 · ${escapeHtml(selected?.name || "模板")}</span></header><div class="slide-template-gallery" role="group" aria-label="学术 PPT 模板">${cards}</div>`;
}

function evidenceReviewWorkbenchContent() {
  const baseContent = modeWorkbenchContent.knowledge;
  return {
    ...baseContent,
    title: "证据综述",
    description: "把当前资料库整理成一篇带原文引用的长篇综述。",
    hint: "先确认资料范围，再输入主题；引用可回到原文",
    examples: [
      { id: "evidence-review-progress", icon: "book-open", title: "梳理研究进展", text: "组织关键路线、共同发现、分歧与证据边界。", prompt: "请基于已选择知识库撰写一篇证据综述，主题是：\n\n", tags: ["原文证据", "逐句引用", "研究进展"] },
      { id: "evidence-review-compare", icon: "git-compare", title: "比较不同研究", text: "按问题、方法、发现和局限组织多篇资料。", prompt: "请基于已选择知识库撰写一篇证据综述，比较以下主题中的不同研究、共同发现、分歧与开放问题：\n\n", tags: ["跨文献比较", "证据分歧", "开放问题"] },
    ],
  };
}

function currentModeWorkbenchContent(mode) {
  return mode === "knowledge" && state.evidenceOutputMode === "review"
    ? evidenceReviewWorkbenchContent()
    : modeWorkbenchContent[mode];
}

function renderEvidenceReviewMethodGuide() {
  const notebooks = selectedKnowledgeNotebooks();
  const sourceCount = notebooks.reduce((sum, notebook) => sum + Number(notebook.counts?.sources || 0), 0);
  const scopeLabel = notebooks.length
    ? `${notebooks.map((notebook) => compact(knowledgeScopeTitle(notebook), 20)).join("、")} · ${sourceCount} 个文档可检索`
    : "先在输入框下方选择要使用的知识库";
  return `<section class="evidence-review-method" aria-label="证据综述使用方法"><header><div><span>HOW IT WORKS</span><h3>三步完成证据综述</h3></div><p>只依据当前资料库，不补写没有来源的结论。</p></header><ol><li><b>1</b><div><strong>确认资料范围</strong><small>${escapeHtml(scopeLabel)}</small></div><button type="button" data-action="open-knowledge-scope">更改</button></li><li><b>2</b><div><strong>输入综述主题</strong><small>说明对象、比较维度或时间范围即可。</small></div></li><li><b>3</b><div><strong>阅读并核验</strong><small>关键结论附引用；点击可查看原文片段。</small></div></li></ol><footer><span>${uiIcon("shield-check")} 输出：摘要、主题章节、共识与分歧、开放问题、原文引用</span><button type="button" data-action="apply-mode-example" data-mode-example="evidence-review-progress">填入示例 ${uiIcon("arrow-up-right")}</button></footer></section>`;
}

function renderHomeModeWorkbench(mode) {
  const workbench = byId("homeModeWorkbench");
  const landing = workbench?.closest(".home-landing");
  const isEvidenceReview = mode === "knowledge" && state.evidenceOutputMode === "review";
  const content = currentModeWorkbenchContent(mode);
  const title = byId("homeLandingTitle");
  if (title) title.textContent = mode === "academic" ? "想检索哪些论文？" : mode === "knowledge" ? (isEvidenceReview ? "想整理什么证据？" : "想从资料中验证什么？") : mode === "writing" ? "今天写点什么？" : mode === "slides" ? "今天讲点什么？" : mode === "research" ? "今天研究什么？" : mode === "download" ? "想获取哪些论文？" : "今天想做什么？";
  if (!workbench || !landing) return;
  landing.classList.toggle("has-mode-workbench", Boolean(content));
  workbench.hidden = !content;
  if (!content) {
    workbench.replaceChildren();
    renderHomeBatchAttachment();
    return;
  }
  if (mode === "download") {
    workbench.innerHTML = renderDownloadGuides(content);
    renderHomeBatchAttachment();
    hydrateIcons(workbench);
    return;
  }
  if (mode === "slides") {
    workbench.innerHTML = renderInlineSlideTemplateGallery();
    renderHomeBatchAttachment();
    hydrateIcons(workbench);
    return;
  }
  const tools = content.tools.map((tool) => {
    const selected = tool.evidenceOutput
      ? mode === "knowledge" && tool.evidenceOutput === state.evidenceOutputMode
      : tool.workflow && tool.workflow === state.researchWorkflow;
    return `<button type="button" class="mode-workbench-tool ${selected ? "is-selected" : ""}" data-action="apply-mode-tool" data-mode-tool="${escapeHtml(tool.id)}" aria-pressed="${String(Boolean(selected))}">${uiIcon(tool.icon)}<span>${escapeHtml(tool.label)}</span></button>`;
  }).join("");
  const examples = content.examples.map((item) => `<button type="button" class="mode-example-card" data-action="apply-mode-example" data-mode-example="${escapeHtml(item.id)}"><span class="mode-example-icon">${uiIcon(item.icon)}</span><span class="mode-example-title">${escapeHtml(item.title)}</span><span class="mode-example-copy">${escapeHtml(item.text)}</span><span class="mode-example-tags">${item.tags.map((tag) => `<em>${escapeHtml(tag)}</em>`).join("")}</span><span class="mode-example-use">使用案例 ${uiIcon("arrow-up-right")}</span></button>`).join("");
  const toolRow = tools ? `<div class="mode-workbench-tools" aria-label="${escapeHtml(composerModeLabels[mode])}功能">${tools}</div>` : "";
  const guide = isEvidenceReview ? renderEvidenceReviewMethodGuide() : "";
  workbench.innerHTML = `<header class="mode-workbench-head"><div><h2>${escapeHtml(content.title)}</h2><p>${escapeHtml(content.description)}</p></div><span>${escapeHtml(content.hint || "点击案例会填入输入框，可继续修改")}</span></header>${toolRow}${guide}<div class="mode-example-grid">${examples}</div>`;
  renderHomeBatchAttachment();
  hydrateIcons(workbench);
}

function closeComposerModePickers() {
  document.querySelectorAll("[data-mode-picker]").forEach((picker) => {
    picker.classList.remove("is-open");
    picker.querySelector("[data-action='toggle-composer-mode']")?.setAttribute("aria-expanded", "false");
  });
}

const webSearchLabels = { auto: "联网：自动", on: "联网：开启", off: "联网：关闭" };

function closeWebSearchPickers(except = null) {
  document.querySelectorAll("[data-web-search-picker]").forEach((picker) => {
    if (picker === except) return;
    picker.classList.remove("is-open");
    picker.querySelector("[data-action='toggle-web-search']")?.setAttribute("aria-expanded", "false");
  });
}

function toggleWebSearchPicker(trigger) {
  const picker = trigger.closest("[data-web-search-picker]");
  if (!picker) return;
  const shouldOpen = !picker.classList.contains("is-open");
  closeAttachmentMenus();
  closeComposerModePickers();
  closeComposerModelPickers();
  closeComposerThinkingPickers();
  closeWebSearchPickers();
  if (shouldOpen) {
    picker.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
  }
}

function setWebSearchMode(mode, { announce = true } = {}) {
  const safeMode = Object.hasOwn(webSearchLabels, mode) ? mode : "auto";
  state.webSearchMode = safeMode;
  window.localStorage.setItem("scansci.web-search.mode", safeMode);
  document.querySelectorAll("[data-web-search-picker]").forEach((picker) => {
    const trigger = picker.querySelector("[data-action='toggle-web-search']");
    const label = picker.querySelector("[data-web-search-label]");
    if (label) label.textContent = webSearchLabels[safeMode];
    trigger?.setAttribute("aria-label", `联网搜索：${({ auto: "自动", on: "开启", off: "关闭" })[safeMode]}`);
    trigger?.classList.toggle("is-on", safeMode === "on");
    trigger?.classList.toggle("is-off", safeMode === "off");
    picker.querySelectorAll("[data-web-search-value]").forEach((option) => {
      const selected = option.dataset.webSearchValue === safeMode;
      option.classList.toggle("is-selected", selected);
      option.setAttribute("aria-selected", String(selected));
    });
  });
  if (announce) {
    const message = safeMode === "on"
      ? "联网搜索已开启：下一轮将先检索外部学术来源。"
      : safeMode === "off"
        ? "联网搜索已关闭：查询不会发送给外部检索服务。"
        : "联网搜索设为自动：由 Pi 根据问题决定是否检索。";
    toast(message);
  }
}

function closeComposerModelPickers(except = null) {
  document.querySelectorAll("[data-composer-model]").forEach((picker) => {
    if (picker === except) return;
    picker.classList.remove("is-open");
    picker.querySelector("[data-action='toggle-composer-model']")?.setAttribute("aria-expanded", "false");
  });
}

function fitComposerModelPicker(picker) {
  const trigger = picker.querySelector("[data-action='toggle-composer-model']");
  const popover = picker.querySelector(".composer-model-popover");
  if (!trigger || !popover) return;
  let top = 12;
  let bottom = window.innerHeight - 12;
  for (let parent = picker.parentElement; parent; parent = parent.parentElement) {
    if (!/(auto|scroll|hidden|clip)/.test(getComputedStyle(parent).overflowY)) continue;
    const bounds = parent.getBoundingClientRect();
    top = Math.max(top, bounds.top + 12);
    bottom = Math.min(bottom, bounds.bottom - 12);
  }
  const anchor = trigger.getBoundingClientRect();
  const above = Math.max(0, anchor.top - top - 10);
  const below = Math.max(0, bottom - anchor.bottom - 10);
  const preferBelow = Boolean(picker.closest(".home-landing.has-mode-workbench"));
  const openBelow = preferBelow ? below >= 160 || below >= above : above < 160 && below > above;
  picker.classList.toggle("is-below", openBelow);
  picker.classList.toggle("is-above", !openBelow);
  popover.style.setProperty("--composer-popover-available", `${Math.floor(openBelow ? below : above)}px`);
}

function closeComposerThinkingPickers(except = null) {
  document.querySelectorAll("[data-composer-thinking]").forEach((picker) => {
    if (picker === except) return;
    picker.classList.remove("is-open");
    picker.querySelector("[data-action='toggle-composer-thinking']")?.setAttribute("aria-expanded", "false");
  });
}

function toggleComposerModelPicker(trigger) {
  const picker = trigger.closest("[data-composer-model]");
  if (!picker) return;
  const shouldOpen = !picker.classList.contains("is-open");
  closeComposerModePickers();
  closeAttachmentMenus();
  closeComposerThinkingPickers();
  closeWebSearchPickers();
  closeComposerModelPickers();
  if (shouldOpen) {
    fitComposerModelPicker(picker);
    picker.querySelector(".composer-model-popover")?.scrollTo(0, 0);
    picker.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
  }
}

function toggleComposerThinkingPicker(trigger) {
  if (trigger.disabled) return;
  const picker = trigger.closest("[data-composer-thinking]");
  if (!picker) return;
  const shouldOpen = !picker.classList.contains("is-open");
  closeComposerModePickers();
  closeAttachmentMenus();
  closeComposerModelPickers();
  closeWebSearchPickers();
  closeComposerThinkingPickers();
  if (shouldOpen) {
    picker.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
  }
}

function closeAttachmentMenus(except = null) {
  document.querySelectorAll("[data-attachment-picker]").forEach((picker) => {
    if (picker === except) return;
    picker.classList.remove("is-open");
    picker.querySelector("[data-action='toggle-attachment-menu']")?.setAttribute("aria-expanded", "false");
  });
}

function toggleAttachmentMenu(trigger) {
  const picker = trigger.closest("[data-attachment-picker]");
  if (!picker) return;
  const shouldOpen = !picker.classList.contains("is-open");
  closeComposerModePickers();
  closeWebSearchPickers();
  closeAttachmentMenus();
  if (shouldOpen) {
    picker.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
  }
}

function openLibraryPathDialog(kind = "folder") {
  const dialog = byId("libraryPathDialog");
  const input = byId("libraryPathInput");
  if (!dialog || !input) return;
  const descriptors = {
    files: {
      title: "链接本地文件",
      label: "文件路径（每行一个）",
      hint: "原文件保留在原位置；ScanSci 只生成本地解析结果与可重建索引。",
      placeholder: "例如 D:\\Research\\paper.html",
    },
    folder: {
      title: "链接本地文件夹",
      label: "文件夹路径",
      hint: "文件夹保留在原位置；ScanSci 会递归读取支持的文件并建立本地索引。",
      placeholder: "例如 D:\\Research\\papers",
    },
    obsidian: {
      title: "链接 Obsidian Vault",
      label: "Vault 文件夹路径",
      hint: "递归读取 Markdown 笔记；.obsidian 配置与附件不会写入搜索科学。",
      placeholder: "例如 D:\\Notes\\Research Vault",
    },
    zotero: {
      title: "连接 Zotero 文献库",
      label: "Zotero storage 文件夹路径",
      hint: "会连接本机 PDF 书架并保留原文件位置；需在“文档处理”启用解析后，PDF 正文才会加入问答。",
      placeholder: "例如 C:\\Users\\你\\Zotero\\storage",
    },
    "zotero-data": {
      title: "手动配置 Zotero",
      label: "Zotero 数据目录",
      hint: "选择包含 zotero.sqlite 的文件夹，不要选择 storage 子文件夹；ScanSci 只读这个目录。",
      placeholder: "例如 C:\\Users\\你\\Zotero",
    },
    empty: {
      title: "创建空知识库",
      label: "知识库名称",
      hint: "只创建一个本地检索容器；之后添加的文件仍保留原位置，ScanSci 仅保存索引。",
      placeholder: "例如 博士论文核心文献",
    },
  };
  state.libraryImportKind = Object.hasOwn(descriptors, kind) ? kind : "folder";
  const descriptor = descriptors[state.libraryImportKind];
  byId("libraryPathTitle").textContent = descriptor.title;
  byId("libraryPathLabel").textContent = descriptor.label;
  byId("libraryPathHint").textContent = descriptor.hint;
  byId("libraryPathSubmit").textContent = state.libraryImportKind === "empty" ? "创建" : "链接";
  input.placeholder = descriptor.placeholder;
  const rememberedZoteroPath = state.zoteroConnectionIssue?.dataDir
    || state.notebook?.metadata?.zotero?.configured_data_dir
    || "";
  input.value = state.libraryImportKind === "files"
    ? ""
    : state.libraryImportKind === "folder"
      ? String(state.notebook?.root_path || "")
      : state.libraryImportKind === "zotero-data"
        ? String(rememberedZoteroPath)
        : "";
  if (!dialog.open) dialog.showModal();
  window.setTimeout(() => input.focus(), 0);
}

function closeLibraryPathDialog() {
  const dialog = byId("libraryPathDialog");
  if (dialog?.open) dialog.close();
}

async function chooseLibraryFolder(kind = "folder", notebookId = "") {
  state.libraryImportNotebookId = notebookId || state.notebook?.notebook_id || "";
  state.libraryImportGuided = false;
  closeAttachmentMenus();
  const nativePicker = window.pywebview?.api?.choose_library_folder;
  if (typeof nativePicker !== "function") {
    await chooseBrowserLibraryFolder(kind, notebookId);
    return;
  }
  const path = String(await nativePicker() || "").trim();
  if (!path) return;
  if (kind === "zotero") await registerZoteroLibrary(path);
  else await bindLibraryFolder(path, kind, notebookId);
}

async function chooseZoteroDataDirectory(notebookId = "") {
  const targetNotebookId = notebookId || state.notebook?.notebook_id || "";
  state.libraryImportNotebookId = targetNotebookId;
  state.libraryImportGuided = false;
  closeAttachmentMenus();
  const nativePicker = window.pywebview?.api?.choose_library_folder;
  if (typeof nativePicker !== "function") {
    openLibraryPathDialog("zotero-data");
    return;
  }
  const path = String(await nativePicker() || "").trim();
  if (path) await connectLocalZotero(targetNotebookId, path);
}

async function chooseOnboardingLibraryFolder(kind = "folder") {
  closeAttachmentMenus();
  const nativePicker = window.pywebview?.api?.choose_library_folder;
  if (typeof nativePicker !== "function") {
    await chooseBrowserLibraryFolder(kind);
    return;
  }
  const path = String(await nativePicker() || "").trim();
  if (path) await bindLibraryFolder(path, kind);
}

function promptBrowserForFolder() {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.setAttribute("webkitdirectory", "");
    input.setAttribute("directory", "");
    input.tabIndex = -1;
    input.style.cssText = "position:fixed;inset:0;opacity:0;pointer-events:none;width:1px;height:1px;";
    const finish = () => {
      const files = Array.from(input.files || []);
      input.remove();
      resolve(files);
    };
    input.addEventListener("change", finish, { once: true });
    document.body.append(input);
    input.click();
  });
}

async function chooseBrowserLibraryFolder(kind = "folder", notebookId = "") {
  const files = await promptBrowserForFolder();
  if (!files.length) return;
  // A browser's directory picker deliberately withholds the original absolute
  // path.  Prefer the desktop bridge when it exists, and never pretend that a
  // browser-only preview has created an original-location binding.
  const sourcePath = files.map((file) => String(file?.path || "").trim()).find(Boolean);
  if (sourcePath) {
    await bindLibraryFolder(sourcePath, kind, notebookId);
    return;
  }
  const folderName = String(files[0]?.webkitRelativePath || "").split("/")[0] || "所选文件夹";
  toast(`已选择“${folderName}”。浏览器预览不能读取原始文件夹路径；请在桌面应用中完成原位置绑定。`, true);
}

async function chooseLibraryFiles(notebookId = state.notebook?.notebook_id || "") {
  state.libraryImportNotebookId = notebookId || state.notebook?.notebook_id || "";
  closeAttachmentMenus();
  const nativePicker = window.pywebview?.api?.choose_library_files;
  if (typeof nativePicker !== "function") {
    openLibraryPathDialog("files");
    return;
  }
  const paths = Array.from(await nativePicker() || []).map(String).filter(Boolean);
  if (paths.length) await importLibraryFiles(paths, notebookId);
}

async function chooseComposerSources(key = "home") {
  closeAttachmentMenus();
  const safeKey = key === "home" ? "home" : "chat";
  const nativePicker = window.pywebview?.api?.choose_library_files;
  if (typeof nativePicker === "function") {
    const paths = Array.from(await nativePicker() || []).map(String).filter(Boolean);
    addComposerSourcePaths(safeKey, paths);
    return;
  }
  byId(`${safeKey}SourceFileInput`)?.click();
}

async function choosePresentationSources(key = "home") {
  closeAttachmentMenus();
  const safeKey = key === "home" ? "home" : "chat";
  const nativePicker = window.pywebview?.api?.choose_presentation_sources;
  if (typeof nativePicker === "function") {
    const paths = Array.from(await nativePicker() || []).map(String).filter(Boolean);
    addComposerSourcePaths(safeKey, paths);
    return;
  }
  byId(`${safeKey}SourceFileInput`)?.click();
}

async function bindLibraryFolder(path, kind = "folder", notebookId = "") {
  toast(kind === "obsidian" ? "Vault 已绑定，正在后台读取笔记…" : "文件夹已绑定，正在后台建立索引…");
  const result = await request("/api/library/bind-folder", {
    method: "POST",
    body: JSON.stringify({ notebook_id: notebookId, path, library_kind: kind }),
  });
  await applyLibraryImport(result, kind === "obsidian" ? "Obsidian Vault 已绑定" : "文件夹已绑定");
  return result;
}

function guidedImportJobMarkup() {
  const job = state.libraryImportJob;
  if (!job) return "";
  const progress = Math.max(0, Math.min(100, Math.round(Number(job.progress || 0) * 100)));
  const failed = job.state === "failed";
  const completed = job.state === "completed";
  const icon = failed ? "triangle-alert" : completed ? "check" : "loader-circle";
  const action = failed
    ? `<button type="button" data-action="retry-guided-library-import">${uiIcon("refresh")}重试</button>`
    : "";
  return `<section class="data-import-progress ${failed ? "is-failed" : completed ? "is-complete" : "is-running"}" aria-live="polite"><span>${uiIcon(icon)}</span><div><strong>${escapeHtml(job.phase || "正在接入资料")}</strong><p>${escapeHtml(job.detail || "正在建立本地可检索索引")}</p><div class="data-import-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><i class="${progressWidthClass(progress)}"></i></div><small>${failed ? escapeHtml(job.error || "资料未能完成解析") : completed ? "已完成 · 资料仍保留在原位置" : `${progress}% · 可留在此页查看进度`}</small></div>${action}</section>`;
}

async function startGuidedLibraryImport(path, kind = "folder", notebookId = "", dataDir = "") {
  if (kind === "zotero") return connectLocalZotero(notebookId, dataDir || path);
  return bindLibraryFolder(path, kind, notebookId);
}

async function pollGuidedLibraryImport() {
  const jobId = state.libraryImportJob?.job_id;
  if (!jobId) return;
  try {
    const previousJobState = state.libraryImportJob?.state;
    const job = await request(`/api/library/import-jobs/${encodeURIComponent(jobId)}`);
    state.libraryImportJob = job;
    if (job.state === "completed" && job.result && state.libraryImportAppliedJobId !== jobId) {
      state.libraryImportAppliedJobId = jobId;
      await applyLibraryImport(job.result, "资料接入完成");
    }
    const notebookId = String(job.notebook_id || state.notebook?.notebook_id || "");
    if (notebookId && ["queued", "running"].includes(job.state)) {
      state.knowledgeIndexStatuses[notebookId] = {
        state: "importing",
        progress: Number(job.progress || 0),
        error: "",
      };
    }
    if (notebookId && job.state === "failed") {
      state.knowledgeIndexStatuses[notebookId] = {
        state: "failed",
        progress: Number(job.progress || 0),
        error: String(job.error || "资料索引未完成"),
      };
      if (previousJobState !== "failed") {
        const failureDetail = String(
          job.detail || job.error || "无法遍历所选路径，请确认文件夹存在且有读取权限。",
        );
        toast(`资料接入失败：${failureDetail}`, true);
      }
      if (String(job.library_kind || "") === "zotero") {
        const notebook = (state.workspace?.notebooks || []).find((item) => String(item.notebook_id) === notebookId) || state.notebook;
        state.zoteroConnectionIssue = {
          notebookId,
          dataDir: String(job.data_dir || notebook?.metadata?.zotero?.configured_data_dir || ""),
          message: String(job.error || "Zotero 全文索引未完成"),
          diagnostic: null,
        };
      }
    }
    if (notebookId && job.state === "completed") {
      await refreshKnowledgeIndexStatus(notebookId);
    }
    if (state.activeView === "mode" && state.activeMode === "library") renderMode();
    renderWorkspace();
    renderResourceOnboarding();
    if (["queued", "running"].includes(job.state)) window.setTimeout(pollGuidedLibraryImport, 650);
  } catch (error) {
    state.libraryImportJob = {
      ...state.libraryImportJob,
      state: "failed",
      phase: "无法读取接入状态",
      detail: "原文件没有被修改；请在设置中重新开始接入。",
      error: error.message || "资料接入状态请求失败",
    };
    toast(`资料接入失败：${error.message || "无法读取导入任务状态，请稍后重试。"}`, true);
    renderResourceOnboarding();
  }
}

async function importLibraryFiles(paths, notebookId = state.notebook?.notebook_id || "") {
  toast(`正在链接 ${paths.length} 个文件并建立本地索引…`);
  const result = await request("/api/library/files", {
    method: "POST",
    body: JSON.stringify({ notebook_id: notebookId, paths }),
  });
  await applyLibraryImport(result, `已链接 ${result.added_files || paths.length} 个文件`);
}

async function importLibraryDroppedFiles(files, notebookId) {
  const usable = Array.from(files || []).filter((file) => file?.name).slice(0, 12);
  if (!usable.length) return;
  const paths = usable.map((file) => String(file.path || "")).filter(Boolean);
  if (paths.length === usable.length) {
    await importLibraryFiles(paths, notebookId);
    return;
  }
  toast(`正在从拖入的 ${usable.length} 个文件建立索引…`);
  const payloadFiles = [];
  for (const file of usable) payloadFiles.push({ name: file.name, data_url: await fileToDataUrl(file) });
  const result = await request("/api/library/uploads", {
    method: "POST",
    body: JSON.stringify({ notebook_id: notebookId, files: payloadFiles }),
  });
  await applyLibraryImport(result, `已添加 ${result.added_files || usable.length} 个文件`);
}

async function createEmptyLibrary(title) {
  const result = await request("/api/library", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
  await applyLibraryImport(result, `已创建「${result.notebook?.title || title}」`);
}

async function deletePersonalLibrary(notebookId) {
  const notebook = (state.workspace?.notebooks || []).find((item) => String(item.notebook_id) === String(notebookId));
  if (!notebook || knowledgeSourceKind(notebook) !== "personal") {
    toast("只能从这里移除个人知识库。", true);
    return;
  }
  const title = knowledgeScopeTitle(notebook);
  const confirmed = await requestConfirmation({
    eyebrow: "移除个人知识库",
    title: "移除这个个人知识库？",
    subject: title,
    message: "这会移除 ScanSci 中的资料记录和本地检索索引；原文件和文件夹不会被删除，之后仍可重新链接。",
    confirmLabel: "移除知识库",
    cancelLabel: "保留",
    danger: true,
  });
  if (!confirmed) return;
  const result = await request(`/api/library/${encodeURIComponent(String(notebook.notebook_id))}/delete`, {
    method: "POST",
    body: "{}",
  });
  state.workspace = result.workspace || { ...state.workspace, notebooks: (state.workspace?.notebooks || []).filter((item) => item.notebook_id !== notebook.notebook_id) };
  state.knowledgeScopeIds = (state.knowledgeScopeIds || []).filter((id) => String(id) !== String(notebook.notebook_id));
  persistKnowledgeScopes();
  delete state.knowledgeIndexStatuses[String(notebook.notebook_id)];
  const remaining = state.workspace?.notebooks || [];
  state.notebook = remaining.find((item) => String(item.notebook_id) !== String(notebook.notebook_id)) || null;
  state.knowledgeSubscope = null;
  state.knowledgeQuery = "";
  state.knowledgePreviewSourceId = "";
  window.localStorage.setItem("scansci.knowledge.scope", String(state.notebook?.notebook_id || ""));
  renderWorkspace();
  renderMode();
  if (state.notebook) void ensureActiveKnowledgeIndex(state.notebook.notebook_id);
  toast(`已移除「${title}」；原文件未删除。`);
}

async function registerZoteroLibrary(path) {
  toast("正在连接 Zotero 文献书架…");
  const result = await request("/api/library/zotero", {
    method: "POST",
    body: JSON.stringify({ notebook_id: state.notebook?.notebook_id || "", path }),
  });
  await applyLibraryImport(result, `已连接 Zotero 文献库 · ${result.zotero?.pdf_count || 0} 篇 PDF`);
}

async function connectLocalZotero(notebookId = "", dataDir = "") {
  const targetNotebookId = notebookId || state.notebook?.notebook_id || "";
  const configuredDataDir = String(dataDir || "").trim();
  state.zoteroConnectionIssue = null;
  toast("正在读取本机 Zotero 文献元数据…");
  try {
    const payload = { notebook_id: targetNotebookId };
    if (configuredDataDir) payload.data_dir = configuredDataDir;
    const result = await request("/api/library/zotero/local", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const indexedCount = Number(result.notebook?.counts?.sources || 0);
    const itemCount = Number(result.zotero?.item_count || 0);
    const message = indexedCount
      ? `已连接本机 Zotero · 已建立 ${indexedCount} 个可检索文档`
      : itemCount
        ? `已读取 Zotero 的 ${itemCount} 条文献，但未找到可检索的 PDF 正文`
        : "未读取到 Zotero 文献，请确认本机资料库中已有条目";
    await applyLibraryImport(result, message);
    return result;
  } catch (error) {
    const failurePayload = error?.payload || {};
    const failedNotebook = failurePayload.notebook || null;
    if (failurePayload.workspace) state.workspace = failurePayload.workspace;
    if (failedNotebook) {
      state.notebook = (state.workspace?.notebooks || []).find((item) => String(item.notebook_id) === String(failedNotebook.notebook_id)) || failedNotebook;
    }
    const issueNotebookId = failedNotebook?.notebook_id || targetNotebookId;
    let diagnostic = null;
    try {
      const statusResult = await request("/api/library/zotero/status", {
        method: "POST",
        body: JSON.stringify({ data_dir: configuredDataDir }),
      });
      diagnostic = statusResult.zotero || null;
    } catch (_statusError) {
      // The connection card remains useful even if the diagnostic probe is unavailable.
    }
    state.zoteroConnectionIssue = {
      notebookId: String(issueNotebookId || ""),
      dataDir: configuredDataDir,
      message: String(error?.message || "无法连接本机 Zotero"),
      diagnostic,
    };
    closeLibraryPathDialog();
    if (state.activeView === "mode" && state.activeMode === "library") renderMode();
    renderWorkspace();
    toast("Zotero 连接未完成，请按页面提示配置数据目录", true);
    return null;
  }
}

async function connectNotion(notebookId = "") {
  const setup = await openNotionWizard();
  if (!setup) return;
  const { token, rootPageId, title } = setup;
  toast("正在连接 Notion 并同步页面…");
  const result = await request("/api/library/notion", {
    method: "POST",
    body: JSON.stringify({ notebook_id: notebookId, token, root_page_id: rootPageId, title }),
  });
  await applyLibraryImport(result, `Notion 已同步 · ${result.page_count || 0} 个页面`);
}

async function applyLibraryImport(result, message) {
  state.workspace = result.workspace || await request("/api/workspace");
  if (result.zotero?.connected) state.zoteroConnectionIssue = null;
  const notebookId = result.notebook?.notebook_id || state.notebook?.notebook_id;
  state.notebook = (state.workspace.notebooks || []).find((item) => item.notebook_id === notebookId) || result.notebook || null;
  state.knowledgeQuery = "";
  state.knowledgeVisibleLimit = 200;
  state.knowledgePreviewSourceId = "";
  const activeNotebookId = String(state.notebook?.notebook_id || notebookId || "");
  const previousScopeIds = new Set((state.knowledgeScopeIds || []).map(String));
  state.knowledgeScopeIds = sanitizeKnowledgeScopeIds();
  if (activeNotebookId && notebookHasSearchableContent(state.notebook) && !state.knowledgeScopeIds.includes(activeNotebookId)) {
    state.knowledgeScopeIds.push(activeNotebookId);
  }
  if (Array.isArray(state.knowledgeScopeDraftIds)) {
    state.knowledgeScopeDraftIds = [...new Set([
      ...state.knowledgeScopeDraftIds.map(String),
      ...state.knowledgeScopeIds.filter((id) => !previousScopeIds.has(String(id))).map(String),
    ])];
  }
  persistKnowledgeScopes();
  state.capabilities = await request("/api/capabilities");
  if (result.local_ai && activeNotebookId) {
    state.localAiStatuses[activeNotebookId] = result.local_ai;
    if (result.local_ai.state === "preparing") scheduleLocalAiStatusPoll(activeNotebookId);
  }
  closeLibraryPathDialog();
  renderWorkspace();
  if (byId("knowledgeScopeDialog")?.open) renderKnowledgeScopeDialog();
  if (state.onboardingOpen) renderResourceOnboarding();
  if (state.activeView === "mode" && state.activeMode === "library") renderMode();
  if (result.index_run) {
    upsertRun(result.index_run);
    watchRun(result.index_run.run_id);
  }
  if (result.model_install) {
    mergeLocalModelInstall(result.model_install);
    if (["queued", "downloading"].includes(result.model_install.state)) {
      scheduleLocalModelInstallPoll();
    }
  }
  if (result.import_job?.job_id) {
    state.libraryImportJob = result.import_job;
    state.libraryImportAppliedJobId = "";
    const jobNotebookId = String(result.import_job.notebook_id || notebookId || "");
    if (jobNotebookId) {
      state.knowledgeIndexStatuses[jobNotebookId] = {
        state: "importing",
        progress: Number(result.import_job.progress || 0),
        error: "",
      };
    }
    window.setTimeout(pollGuidedLibraryImport, 250);
  }
  const indexing = result.index_run
    ? " · 正在后台构建本地语义索引"
    : result.import_job
      ? " · 正在后台读取 PDF 并建立可检索证据"
    : ["queued", "downloading"].includes(result.model_install?.state)
      ? " · 正在通过国内源安装高质量检索组件，完成后会自动索引"
      : "";
  toast(`${message} · ${state.notebook?.counts?.sources || 0} 个文档${indexing}`);
}

function toggleComposerModePicker(trigger) {
  const picker = trigger.closest("[data-mode-picker]");
  if (!picker) return;
  const shouldOpen = !picker.classList.contains("is-open");
  closeAttachmentMenus();
  closeWebSearchPickers();
  closeComposerModePickers();
  if (shouldOpen) {
    picker.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
  }
}

function setComposerMode(mode, { preserveResearchWorkflow = false } = {}) {
  const safeMode = composerModeLabels[mode] ? mode : "general";
  // A research sub-workflow is a one-mode constraint, not a global user
  // preference.  Never let a previous novelty/deep-research selection leak
  // into writing, slides, general chat, or a newly selected research session.
  if (!["research", "academic"].includes(safeMode) || !preserveResearchWorkflow) {
    state.researchWorkflow = safeMode === "academic" ? "academic" : "";
  }
  if (safeMode !== "knowledge") state.evidenceOutputMode = "answer";
  for (const id of ["homeModeSelect", "chatModeSelect"]) {
    const input = byId(id);
    if (input) input.value = safeMode;
  }
  document.querySelectorAll(".composer-agent-button").forEach((button) => {
    button.hidden = !["general", "writing"].includes(safeMode);
  });
  document.querySelectorAll("[data-composer-mode-shortcut]").forEach((button) => {
    const selected = button.dataset.modeValue === safeMode;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  const placeholders = {
    academic: "输入论文主题、论文标题、DOI 或 arXiv ID",
    knowledge: state.evidenceOutputMode === "review" ? "输入综述主题；将组织原文证据并逐句引用" : "基于已选知识库提问，关键结论会附原文证据",
    research: "输入研究问题；Pi 会自动选择搜索、深研、查新或构思流程",
    writing: "写下主题、材料范围和要求",
    slides: "描述汇报主题，或添加论文、文档与现成大纲",
    download: "输入 DOI 或 arXiv ID，例如 10.1038/...",
  };
  for (const id of ["homeQuestionInput", "chatQuestionInput"]) {
    const composer = byId(id);
    if (composer) composer.placeholder = placeholders[safeMode] || "描述你想完成的事";
  }
  renderHomeModeWorkbench(safeMode);
  renderKnowledgeScopeSurfaces();
  syncSlideTemplateDocks();
}

function selectedSlideTemplate() {
  return state.slideTemplates.find((item) => item.id === state.selectedSlideTemplateId) || state.slideTemplates[0] || null;
}

function previewSlideTemplate() {
  return state.slideTemplates.find((item) => item.id === state.previewSlideTemplateId) || selectedSlideTemplate();
}

function inlineSlidePreviewTemplate() {
  return state.slideTemplates.find((item) => item.id === state.inlineSlidePreviewTemplateId) || selectedSlideTemplate();
}

function renderInlineSlidePreview() {
  const template = inlineSlidePreviewTemplate();
  const title = byId("inlineSlidePreviewTitle");
  const description = byId("inlineSlidePreviewDescription");
  const stage = byId("inlineSlidePreviewStage");
  const pages = byId("inlineSlidePreviewPages");
  if (!template || !title || !description || !stage || !pages) return;
  if (!state.inlineSlidePreviewPage || !(template.pages || []).some((page) => page.file === state.inlineSlidePreviewPage)) {
    state.inlineSlidePreviewPage = template.pages?.[0]?.file || "";
  }
  const activePage = (template.pages || []).find((page) => page.file === state.inlineSlidePreviewPage) || template.pages?.[0];
  const selected = template.id === selectedSlideTemplate()?.id;
  title.textContent = template.name;
  description.textContent = template.description || template.tone || template.summary || "学术演示模板";
  stage.innerHTML = `<img src="${escapeHtml(activePage?.preview_url || template.preview_url)}" alt="${escapeHtml(template.name)} · ${escapeHtml(activePage?.label || "模板预览")}" />`;
  pages.innerHTML = (template.pages || []).map((page) => `<button type="button" class="inline-slide-preview-page ${page.file === activePage?.file ? "is-active" : ""}" data-action="preview-inline-slide-page" data-template-page="${escapeHtml(page.file)}"><img src="${escapeHtml(page.preview_url)}" alt="" /><span>${escapeHtml(page.label)}</span></button>`).join("");
  const useButton = byId("inlineSlidePreviewUse");
  if (useButton) {
    useButton.querySelector("[data-inline-slide-preview-use-label]").textContent = selected ? "正在使用此模板" : "使用此模板";
    useButton.disabled = selected;
  }
}

function openInlineSlidePreview(templateId) {
  const template = state.slideTemplates.find((item) => item.id === templateId);
  if (!template) {
    toast("未找到所选模板", true);
    return;
  }
  state.inlineSlidePreviewTemplateId = template.id;
  state.inlineSlidePreviewPage = "";
  renderInlineSlidePreview();
  const dialog = byId("inlineSlidePreviewDialog");
  if (dialog && !dialog.open) dialog.showModal();
}

function closeInlineSlidePreview() {
  const dialog = byId("inlineSlidePreviewDialog");
  if (dialog?.open) dialog.close();
}

function selectInlinePreviewedSlideTemplate() {
  const template = inlineSlidePreviewTemplate();
  if (!template) return;
  selectSlideTemplate(template.id);
  closeInlineSlidePreview();
}

function syncSlideTemplateDocks() {
  const template = selectedSlideTemplate();
  document.querySelectorAll("[data-slide-template-dock]").forEach((dock) => {
    const key = dock.dataset.composerKey || "home";
    const mode = byId(`${key}ModeSelect`)?.value || "general";
    // Source-to-PPT is a first-class presentation flow as well: choosing a
    // source must not make template selection disappear.
    // The landing page exposes templates as an immediate visual gallery.
    // Keep this compact trigger for the chat composer, where the dialog is
    // still useful without taking over the conversation.
    dock.hidden = mode !== "slides" || key === "home";
    const label = dock.querySelector("[data-slide-template-label]");
    if (label) label.textContent = template?.name || (state.slideTemplatesAvailable ? "选择模板" : "模板库暂不可用");
    const button = dock.querySelector("[data-action='open-slide-templates']");
    if (button) button.disabled = !state.slideTemplatesAvailable;
  });
}

function openSlideTemplateDialog() {
  if (!state.slideTemplatesAvailable) {
    toast("未找到演示模板库", true);
    return;
  }
  state.previewSlideTemplateId = state.selectedSlideTemplateId || state.slideTemplates[0].id;
  state.previewSlidePage = "";
  state.slideTemplateQuery = "";
  const search = byId("slideTemplateSearch");
  if (search) search.value = "";
  renderSlideTemplateBrowser();
  const dialog = byId("slideTemplateDialog");
  if (dialog && !dialog.open) dialog.showModal();
  window.setTimeout(() => search?.focus(), 0);
}

function closeSlideTemplateDialog() {
  const dialog = byId("slideTemplateDialog");
  if (dialog?.open) dialog.close();
}

function renderSlideTemplateBrowser() {
  const query = state.slideTemplateQuery.trim().toLocaleLowerCase("zh-CN");
  const templates = state.slideTemplates.filter((template) => {
    const haystack = [template.name, template.id, template.description, template.summary, template.use_cases, ...(template.keywords || [])].join(" ").toLocaleLowerCase("zh-CN");
    return !query || haystack.includes(query);
  });
  byId("slideTemplateCount").textContent = `${templates.length} 个模板`;
  byId("slideTemplateList").innerHTML = templates.length ? templates.map((template) => {
    const selected = template.id === state.selectedSlideTemplateId;
    const previewing = template.id === state.previewSlideTemplateId;
    return `<button type="button" class="slide-template-option ${previewing ? "is-previewing" : ""} ${selected ? "is-selected" : ""}" data-action="preview-slide-template" data-template-id="${escapeHtml(template.id)}" role="option" aria-selected="${previewing}"><img class="slide-template-option-thumb" src="${escapeHtml(template.preview_url)}" alt="" /><span class="slide-template-option-copy"><strong>${escapeHtml(template.name)}</strong><small>${escapeHtml(compact(template.description || template.use_cases || template.summary, 44))}</small></span><span class="slide-template-option-check">✓</span></button>`;
  }).join("") : '<div class="slide-template-empty">没有匹配模板</div>';

  const template = previewSlideTemplate();
  if (!template) {
    byId("slideTemplatePreview").innerHTML = '<div class="slide-template-empty">暂无可预览模板</div>';
    byId("slideTemplateSelection").textContent = "";
    return;
  }
  if (!state.previewSlidePage || !(template.pages || []).some((page) => page.file === state.previewSlidePage)) {
    state.previewSlidePage = template.pages?.[0]?.file || "";
  }
  const activePage = (template.pages || []).find((page) => page.file === state.previewSlidePage) || template.pages?.[0];
  byId("slideTemplatePreview").innerHTML = `<div class="slide-template-preview-head"><div><h3>${escapeHtml(template.name)}</h3><p>${escapeHtml(template.description || template.tone || template.summary)}</p></div><svg class="slide-template-color" viewBox="0 0 18 18" role="img" aria-label="主色 ${escapeHtml(template.primary_color)}"><circle cx="9" cy="9" r="7" fill="${escapeHtml(template.primary_color)}"></circle></svg></div><div class="slide-template-stage"><img src="${escapeHtml(activePage?.preview_url || template.preview_url)}" alt="${escapeHtml(template.name)} · ${escapeHtml(activePage?.label || "模板预览")}" /></div><div class="slide-template-pages">${(template.pages || []).map((page) => `<button type="button" class="slide-template-page ${page.file === activePage?.file ? "is-active" : ""}" data-action="preview-slide-page" data-template-page="${escapeHtml(page.file)}"><img src="${escapeHtml(page.preview_url)}" alt="" /><span>${escapeHtml(page.label)}</span></button>`).join("")}</div>`;
  byId("slideTemplateSelection").textContent = `${template.name} · 16:9`;
}

function selectPreviewedSlideTemplate() {
  const template = previewSlideTemplate();
  if (!template) return;
  selectSlideTemplate(template.id, { closeDialog: true });
}

function selectSlideTemplate(templateId, { closeDialog = false } = {}) {
  const template = state.slideTemplates.find((item) => item.id === templateId);
  if (!template) {
    toast("未找到所选模板", true);
    return;
  }
  state.selectedSlideTemplateId = template.id;
  window.localStorage.setItem("scansci.slides.template", template.id);
  syncSlideTemplateDocks();
  if (closeDialog) closeSlideTemplateDialog();
  if (byId("homeModeSelect")?.value === "slides") renderHomeModeWorkbench("slides");
  if (state.activeView === "mode" && state.activeMode === "ppt") renderMode();
  toast(`已选择「${template.name}」`);
}

async function createResearchRun(workflowType, input = {}) {
  const usesExternalResearch = workflowType === "academic_search" || workflowType === "deep_research";
  const isHostRouted = workflowType === "auto";
  const standalone = isHostRouted || usesExternalResearch || workflowType === "pdf_to_ppt" || workflowType === "paper_download" || workflowType === "paper_download_batch" || workflowType === "paper_search_download";
  if (!state.notebook && !standalone) throw new Error("请先打开一个资料库");
  const knowledgePayload = usesExternalResearch
    ? {}
    : {
        ...(state.notebook ? { notebook_id: state.notebook.notebook_id } : {}),
        ...(activeKnowledgeScopePayload() ? { knowledge_scope: activeKnowledgeScopePayload() } : {}),
      };
  const suppliedKey = String(input.idempotency_key || input.request_id || "").trim();
  const generatedKey = suppliedKey || `ui-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
  return request("/api/runs", {
    method: "POST",
    body: JSON.stringify({ workflow_type: workflowType, ...knowledgePayload, ...input, thinking_level: currentThinkingLevel(), idempotency_key: generatedKey }),
  });
}

async function previewFreeformTask(question, skills = []) {
  const selectedSkills = [...new Set([...(skills || []), ...extractSkillMentions(question)].map((item) => String(item || "").toLowerCase()).filter(Boolean))].slice(0, 4);
  return request("/api/task-routing/preview", {
    method: "POST",
    body: JSON.stringify({
      question,
      skills: selectedSkills,
      ...(state.notebook ? { notebook_id: state.notebook.notebook_id } : {}),
    }),
  });
}

async function continueTaskConversation(runId, content, skills = []) {
  const selectedSkills = [...new Set([...(skills || []), ...extractSkillMentions(content)].map((item) => String(item || "").toLowerCase()).filter(Boolean))].slice(0, 4);
  const storageKey = `scansci.task.followup.${runId}`;
  let pending = null;
  try { pending = JSON.parse(window.localStorage.getItem(storageKey) || "null"); } catch (_) { /* stale local state */ }
  const payload = pending?.content === content && pending?.payload?.idempotency_key ? pending.payload : {
      content,
      ...(selectedComposerImageModel() ? { image_model: selectedComposerImageModel(), image_request_id: globalThis.crypto.randomUUID() } : {}),
      thinking_level: currentThinkingLevel(),
      chat_mode: composerMode("chatQuestionInput"),
      agent_mode: state.agentMode,
      web_search: state.webSearchMode,
      skills: selectedSkills,
      idempotency_key: `ui-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`,
  };
  window.localStorage.setItem(storageKey, JSON.stringify({ content, payload }));
  const endpoint = `/api/runs/${encodeURIComponent(runId)}/messages`;
  let result;
  try {
    result = await request(endpoint, { method: "POST", body: JSON.stringify(payload) });
  } catch (error) {
    if (error?.payload?.error?.code !== "submission_uncertain" || pending?.content !== content) throw error;
    const confirmed = await requestConfirmation({
      eyebrow: "结果待核实",
      title: "重新提交这条追问？",
      message: "上次请求可能已经执行工具。请先查看任务消息和生成的文件；确认需要再次执行后再继续。",
      confirmLabel: "重新提交",
      danger: true,
    });
    if (!confirmed) throw error;
    const retryPayload = {
      ...payload,
      idempotency_key: `ui-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`,
      ...(payload.image_model ? { image_request_id: globalThis.crypto.randomUUID() } : {}),
    };
    window.localStorage.setItem(storageKey, JSON.stringify({ content, payload: retryPayload }));
    result = await request(endpoint, { method: "POST", body: JSON.stringify(retryPayload) });
  }
  window.localStorage.removeItem(storageKey);
  return result;
}

async function watchRun(runId, onUpdate = () => {}) {
  let run = state.runs.find((item) => item.run_id === runId);
  const terminal = new Set(["completed", "failed", "cancelled", "paused", "needs_confirmation", "waiting_input"]);
  let afterSequence = Number(run?.last_event_sequence || 0);
  let hasMore = false;
  const replayedEvents = new Map((run?.events || []).map((event) => [Number(event.sequence || 0), event]));
  for (let attempt = 0; attempt < 1800 && (!run || !terminal.has(run.status) || hasMore); attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, attempt < 4 ? 180 : 650));
    try {
      const payload = await request(`/api/runs/${encodeURIComponent(runId)}/events?after_sequence=${afterSequence}&limit=200`);
      const snapshot = payload?.snapshot || payload?.run || payload;
      const nextEvents = Array.isArray(payload?.events) ? payload.events : [];
      nextEvents.forEach((event) => replayedEvents.set(Number(event.sequence || 0), event));
      run = {
        ...snapshot,
        events: [...replayedEvents.values()].filter((event) => Number(event.sequence || 0) > 0).sort((a, b) => Number(a.sequence || 0) - Number(b.sequence || 0)),
      };
      afterSequence = Math.max(afterSequence, Number(payload?.last_sequence || run.last_event_sequence || 0));
      hasMore = Boolean(payload?.has_more);
      upsertRun(run);
      onUpdate(run);
      if (hasMore) {
        attempt = Math.max(-1, attempt - 1);
        continue;
      }
    } catch (error) {
      toast(error.message, true);
      return;
    }
  }
}

function renderAnswer(result) {
  const scrollSnapshot = conversationScrollSnapshot();
  applyContextPanelPreset("evidence");
  const reader = result.reader_answer || {};
  const sentences = reader.sentences || [];
  const answerMarkup = sentences.length ? sentences.map((sentence) => {
    const citations = (sentence.citation_ids || []).map(citationMarkerMarkup).join("");
    return `<p class="answer-sentence">${escapeHtml(sentence.text)} ${citations}</p>`;
  }).join("") : '<p class="answer-sentence">当前资料不足以生成可核验回答。</p>';
  const limitations = (result.answer?.limitations || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const sufficient = result.adequacy?.is_sufficient;
  const now = result.created_at || new Date().toISOString();
  const userMessage = conversationMessageMarkup({ role: "user", content: result.question, createdAt: now });
  const answerText = sentences.map((sentence) => sentence.text).join("\n") || "当前资料不足以生成可核验回答。";
  const assistantMessage = conversationMessageMarkup({
    role: "assistant",
    content: answerText,
    contentMarkup: answerMarkup,
    createdAt: result.completed_at || now,
    usage: result.usage,
    model: result.model,
    label: sufficient ? "资料可支持" : "资料不足",
    extra: limitations ? `<div class="answer-limitations"><strong>限制</strong><ul>${limitations}</ul></div>` : "",
    promptContent: result.question,
  });
  byId("answerArea").innerHTML = `<article class="conversation-thread">${userMessage}${assistantMessage}</article>`;
  bindCitationInteractions(result);
  restoreConversationScroll(scrollSnapshot);
}

function runUserPromptText(run) {
  if (run.workflow_type === "academic_search") return String(run.input?.raw_query || run.input?.query || run.title || "");
  if (run.workflow_type === "novelty_check") {
    return `研究问题：${run.input?.problem || ""}\n\n主张的新颖性：${run.input?.novelty || ""}`.trim();
  }
  if (run.workflow_type === "research_idea") {
    return `研究方向：${run.input?.direction || ""}${run.input?.constraints ? `\n\n现实约束：${run.input.constraints}` : ""}`.trim();
  }
  if (run.workflow_type === "paper_download") return String(run.input?.identifier || run.title || "");
  if (run.workflow_type === "paper_download_batch") {
    return `文献清单 · ${(run.input?.identifiers || []).length} 篇\n${(run.input?.identifiers || []).join("\n")}`;
  }
  if (run.workflow_type === "paper_search_download") {
    return [
      run.input?.author ? `作者：${run.input.author}` : "",
      run.input?.query ? `主题：${run.input.query}` : "",
      `数量：${run.input?.limit || 20} 篇`,
    ].filter(Boolean).join("\n\n");
  }
  return String(run.input?.question || run.input?.query || run.title || "");
}

function runUserPromptMarkup(run) {
  const text = runUserPromptText(run);
  if (run.workflow_type === "novelty_check") {
    return `<strong>研究问题</strong><br>${renderAssistantInline(run.input?.problem || "")}<br><br><strong>主张的新颖性</strong><br>${renderAssistantInline(run.input?.novelty || "")}`;
  }
  if (run.workflow_type === "research_idea") {
    return `<strong>研究方向</strong><br>${renderAssistantInline(run.input?.direction || "")}${run.input?.constraints ? `<br><br><strong>现实约束</strong><br>${renderAssistantInline(run.input.constraints)}` : ""}`;
  }
  if (run.workflow_type === "paper_download_batch") {
    return `<strong>文献清单 · ${escapeHtml(String((run.input?.identifiers || []).length))} 篇</strong><br>${renderAssistantInline((run.input?.identifiers || []).slice(0, 8).join("\n"))}`;
  }
  if (run.workflow_type === "paper_search_download") {
    return `${run.input?.author ? `<strong>作者</strong><br>${renderAssistantInline(run.input.author)}<br><br>` : ""}${run.input?.query ? `<strong>主题</strong><br>${renderAssistantInline(run.input.query)}<br><br>` : ""}<strong>数量</strong><br>${escapeHtml(String(run.input?.limit || 20))} 篇`;
  }
  return renderAssistantInline(text);
}

function runAggregateUsage(run) {
  return (run.messages || []).reduce((total, message) => {
    const usage = messageUsageValues(message.usage);
    total.prompt_tokens += usage.prompt;
    total.completion_tokens += usage.completion;
    total.total_tokens += usage.total;
    total.cache_read_tokens += Number(message.usage?.cache_read_tokens || message.usage?.cacheRead || 0) || 0;
    total.cache_write_tokens += Number(message.usage?.cache_write_tokens || message.usage?.cacheWrite || 0) || 0;
    return total;
  }, { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 });
}

function estimateRunSessionStats(run) {
  const messages = Array.isArray(run.messages) ? run.messages : [];
  const messageTokens = estimateTokenCount(runUserPromptText(run))
    + messages.reduce((sum, message) => sum + estimateTokenCount(message.content), 0);
  const artifactText = run.output_artifact
    ? `${run.output_artifact.title || ""}\n${run.output_artifact.summary || ""}\n${JSON.stringify(run.output_artifact.payload || {})}`
    : "";
  const artifactTokens = Math.min(12000, estimateTokenCount(artifactText));
  const systemTools = Math.max(120, (run.stages || []).length * 54 + (run.tool_calls || []).length * 92);
  const systemPrompt = 620;
  const other = artifactTokens;
  const contextTokens = Math.max(1, messageTokens + systemTools + systemPrompt + other);
  const usage = runAggregateUsage(run);
  const totalTokens = usage.total_tokens || contextTokens;
  const contextWindow = modelContextWindowFor(run.model?.provider_id, run.model?.model_id) || 128000;
  return {
    estimated: true,
    tokens: {
      input: usage.prompt_tokens || messageTokens + systemTools + systemPrompt,
      output: usage.completion_tokens || artifactTokens,
      cacheRead: usage.cache_read_tokens,
      cacheWrite: usage.cache_write_tokens,
      total: totalTokens,
    },
    contextUsage: {
      tokens: contextTokens,
      contextWindow,
      percent: contextWindow ? Math.min(100, contextTokens / contextWindow * 100) : 0,
    },
    contextBreakdown: {
      message: messageTokens,
      systemTools,
      mcpTools: 0,
      skills: 0,
      systemPrompt,
      other,
      total: contextTokens,
    },
    totalMessages: Math.max(1, messages.length + 1),
    userMessages: messages.filter((message) => message.role === "user").length + 1,
  };
}

function runFailureSummary(run) {
  const raw = String(run.error?.message || run.recovery?.detail || "").trim();
  if (run.workflow_type === "pdf-translation") return raw || "文献翻译未完成，请检查当前模型与 OCR 设置。";
  if (/scansci-pdf.*(?:json|检索结果)|(?:json|检索结果).*scansci-pdf/i.test(raw)) {
    return "文献检索服务返回了无法读取的结果，下载尚未开始。";
  }
  if (/Publisher-declared PDF|已找到全文入口.*(?:下载失败|不可访问)|公开存档回退失败/i.test(raw)) {
    return "已找到全文入口，但出版商或机构代理未允许下载可校验的 PDF。";
  }
  if (/ERR_CONNECTION_CLOSED|ECONNRESET|ECONNREFUSED|connection (?:closed|reset|refused)/i.test(raw)) {
    return "访问出版商或机构代理时连接中断，尚未取得可校验的文件。";
  }
  if (/timed?\s*out|TimeoutExpired|ETIMEDOUT|超时/i.test(raw)) {
    return "外部服务响应超时；当前进度已经保留，可以从未完成处继续。";
  }
  if (/(?:^|\D)(?:401|403)(?:\D|$)|unauthori[sz]ed|forbidden|invalid api key|api key.*(?:invalid|missing)/i.test(raw)) {
    return "模型或外部服务拒绝了当前凭据，请检查对应服务的连接配置。";
  }
  if (/(?:^|\D)429(?:\D|$)|rate.?limit|too many requests|限流/i.test(raw)) {
    return "外部服务触发了限流；当前进度已经保留，稍后可以从未完成处继续。";
  }
  if (/empty response|空响应|returned no (?:content|text)/i.test(raw)) {
    return "模型返回了空响应，ScanSci 已保留上下文，可改用兼容模型或从当前步骤重试。";
  }
  if (/ENOENT|file not found|no such file|文件不存在/i.test(raw)) {
    return "任务需要的本地文件不存在或已经移动，请检查文件位置后继续。";
  }
  return String(run.recovery?.message || "执行阶段未能完成；此前成功的进度和本地结果已经保留。");
}

function runCompletionSummary(run) {
  const artifact = run.output_artifact;
  const payload = artifact?.payload || {};
  if (run.workflow_type === "pdf-translation") {
    if (run.status === "completed") return "译文 PDF 已保存到翻译库，可从上方打开。";
    if (run.status === "cancelled") return "翻译已取消；已保存的 PDF 会保留在翻译库。";
    if (run.status === "paused") return "应用重启中断了翻译，请从首页重新提交原始 PDF。";
    return "";
  }
  const items = Array.isArray(payload.items) ? payload.items : [];
  const completed = items.filter((item) => item.status === "completed").length
    || Number(payload.completed || 0)
    || (Array.isArray(payload.files) && payload.files.length ? 1 : 0);
  const failed = items.filter((item) => item.status === "failed").length;
  if (run.status === "completed" && String(run.workflow_type || "").startsWith("paper_")) {
    const indexStageCompleted = (run.stages || []).some((stage) => stage.status === "completed" && /index|索引/i.test(`${stage.key || ""} ${stage.title || ""}`));
    const indexed = Number(payload.indexed || payload.indexed_count || (indexStageCompleted ? completed : 0));
    return `完成了。我按要求获取并校验了 ${completed || Number(payload.completed || 0)} 篇全文${indexed ? `，其中 ${indexed} 篇已完成全文索引` : ""}${failed ? `；另有 ${failed} 篇未能获取` : ""}。下面的文件图标可以直接打开文献，文件夹图标可以定位到本地位置。`;
  }
  if (run.status === "completed" && ["slide_outline", "slide_deck_project", "presentation_deck"].includes(artifact?.artifact_type)) {
    const slides = payload.outline?.slides || payload.slides || [];
    return `已经完成这份演示文稿${slides.length ? `，共 ${slides.length} 页` : ""}。成品保存在本地，你可以点击下面的文件图标直接打开。`;
  }
  if (run.status === "completed" && ["literature_review", "deep_research_report"].includes(artifact?.artifact_type)) {
    return `综述已经完成。我保留了可核验的证据引用、研究比较和证据边界；你可以继续在这条对话里要求我压缩、改写或补充某一节。`;
  }
  if (run.status === "completed") {
    return String(artifact?.summary || payload.message || "已经完成这项任务，结果和可继续追问的上下文都保留在当前对话中。");
  }
  if (run.status === "failed") {
    return "";
  }
  const retained = completed ? `，并保留了 ${completed} 个已经落盘的结果` : "";
  if (run.status === "paused") {
    return `任务暂时停在这里${retained}。当前进度没有丢失；继续这条对话后，我会从未完成的阶段接着处理。`;
  }
  if (run.status === "cancelled") return `任务已经停止，已完成的阶段和本地文件仍然保留。`;
  return "";
}

function runArtifactFiles(run) {
  const artifact = run.output_artifact || {};
  const payload = artifact.payload || {};
  const itemFiles = (Array.isArray(payload.items) ? payload.items : [])
    .flatMap((item) => Array.isArray(item.files) ? item.files : [])
    .filter(Boolean);
  const files = [
    ...itemFiles,
    ...(Array.isArray(payload.files) ? payload.files : []),
    payload.pptx_path,
    payload.project_path,
    artifact.file_path,
  ].filter(Boolean).map(String);
  return [...new Set(files)].slice(0, 8);
}

function runCompletionMessageMarkup(run) {
  const content = runCompletionSummary(run);
  if (!content) return "";
  const artifact = run.output_artifact || {};
  const payload = artifact.payload || {};
  const citations = citationRecordsForRun(run);
  const resources = run.workflow_type === "pdf-translation" ? [] : runArtifactFiles(run);
  const files = resources.filter((path) => localResourceKind(path) !== "folder");
  const folders = [
    payload.output_dir,
    payload.output_directory,
    payload.folder_path,
    artifact.folder_path,
    ...resources.filter((path) => localResourceKind(path) === "folder"),
    ...files.map(localPathParent),
  ].filter(Boolean).map(String);
  const uniqueFolders = [...new Set(folders)];
  const resourceLinks = files.length || uniqueFolders.length
    ? `<div class="delivery-resources" aria-label="本地交付文件">
        ${files.length ? `<div class="delivery-resource-line"><span>文件</span><div>${files.map((file) => localFileLinkMarkup(file, "", { inline: true })).join("")}</div></div>` : ""}
        ${uniqueFolders.length ? `<div class="delivery-resource-line"><span>所在文件夹</span><div>${uniqueFolders.map((folder) => localFileLinkMarkup(folder, localPathLeaf(folder), { folder: true, inline: true })).join("")}</div></div>` : ""}
      </div>`
    : "";
  return conversationMessageMarkup({
    role: "assistant",
    content,
    contentMarkup: `<div class="answer-sentence">${citationTextMarkup(content, citations)}</div>`,
    createdAt: run.completed_at || run.updated_at,
    model: run.model,
    label: run.status === "completed" ? "任务交付" : "任务状态",
    extra: resourceLinks,
    classes: "run-delivery-message",
    promptContent: runUserPromptText(run),
  });
}

function runControlPlaneMarkup(run) {
  const interaction = run.interaction && Object.keys(run.interaction).length ? run.interaction : null;
  const recovery = run.recovery && Object.keys(run.recovery).length ? run.recovery : null;
  const advisorEvent = Array.isArray(run.events)
    ? [...run.events].reverse().find((item) => item?.type === "advisor.reviewed")
    : null;
  const advisor = run.advisor_report || (advisorEvent && typeof advisorEvent.payload === "object" ? advisorEvent.payload : null);
  const branchMeta = run.parent_run_id
    ? `<span class="run-control-chip">${uiIcon("git-branch")}分支任务</span>`
    : "";
  const background = run.background
    ? `<span class="run-control-chip">${uiIcon("refresh")}${escapeHtml(run.workflow_type === "evidence_index" ? evidenceIndexRunTitle(run) : "后台任务")}</span>`
    : "";
  let panel = "";
  if (interaction) {
    const summary = interaction.summary || interaction.payload?.summary || "需要你的决定";
    panel = `<section class="run-control-panel is-interaction"><header><strong>${interaction.kind === "plan" ? "计划确认" : "需要你的回答"}</strong><span>任务已安全暂停</span></header><p>${escapeHtml(summary)}</p></section>`;
  } else if (recovery) {
    const failedStage = (run.stages || []).find((stage) => stage.status === "failed");
    panel = `<section class="run-control-panel is-recovery"><header><strong>${escapeHtml(runFailureSummary(run))}</strong><span>${escapeHtml(failedStage?.title || "未完成步骤")}</span></header></section>`;
  }
  if (advisor?.recommended_next_action && advisor.recommended_next_action !== "none") {
    const findings = Array.isArray(advisor.findings) ? advisor.findings : [];
    const nextAction = String(advisor.recommended_next_action);
    const finding = String(findings[0]?.message || "A durable completion check found a gap.");
    panel += `<section class="run-control-panel is-advisor"><header><strong>研究任务核查</strong><span>${escapeHtml(({ passed: "已通过", needs_review: "待核查", insufficient: "交付不足" })[advisor.verdict] || "待核查")}</span></header><p>${escapeHtml(findings[0]?.message ? finding : "任务完成核查发现待补充项。")}</p><div><button type="button" data-action="advisor-action" data-run-id="${escapeHtml(run.run_id)}" data-advisor-action="${escapeHtml(nextAction)}">创建补充核查任务</button></div></section>`;
  }
  return `${branchMeta || background ? `<div class="run-control-chips">${background}${branchMeta}</div>` : ""}${panel}`;
}

function runSummaryActionsMarkup(run) {
  if (run.workflow_type === "pdf-translation") {
    return ["queued", "planning", "running", "verifying"].includes(run.status)
      ? `<button type="button" class="run-action stop" data-action="cancel-translation-run" data-run-id="${escapeHtml(run.run_id)}">取消翻译</button>`
      : "";
  }
  const interaction = run.interaction && Object.keys(run.interaction).length ? run.interaction : null;
  if (interaction) {
    const interactionId = String(interaction.interaction_id || "");
    return `<button type="button" class="run-summary-action is-primary" data-action="respond-run-interaction" data-run-id="${escapeHtml(run.run_id)}" data-interaction-id="${escapeHtml(interactionId)}" data-decision="approve">继续</button><button type="button" class="run-summary-action" data-action="respond-run-interaction" data-run-id="${escapeHtml(run.run_id)}" data-interaction-id="${escapeHtml(interactionId)}" data-decision="cancel">取消</button>`;
  }
  const recovery = run.recovery && Object.keys(run.recovery).length ? run.recovery : null;
  if (recovery) {
    const primaryAction = (recovery.actions || []).find((action) => action.kind !== "branch");
    return primaryAction
      ? `<button type="button" class="run-summary-action is-primary" data-action="recover-run" data-run-id="${escapeHtml(run.run_id)}" data-recovery-action="${escapeHtml(primaryAction.kind || primaryAction.id || "retry")}">${escapeHtml(primaryAction.label || "继续")}</button>`
      : "";
  }
  if (run.pausable) {
    return `<button type="button" class="run-action stop" data-action="pause-run" data-run-id="${escapeHtml(run.run_id)}">暂停</button>`;
  }
  if (run.resumable) {
    const label = run.status === "needs_confirmation" ? "确认计划并执行" : run.workflow_type.startsWith("paper_") ? "继续下载并交付" : "继续";
    return `<button type="button" class="run-action" data-action="resume-run" data-run-id="${escapeHtml(run.run_id)}">${label}</button>`;
  }
  return "";
}

function compactRecentRunEvents(events) {
  const groups = [];
  for (const item of (Array.isArray(events) ? events.slice(-3).reverse() : [])) {
    const summary = String(item.summary || item.type || "任务更新");
    const existing = groups.find((group) => group.summary === summary);
    if (existing) existing.count += 1;
    else groups.push({ item, summary, count: 1 });
  }
  return groups;
}

function renderRun(run) {
  const renderKey = JSON.stringify({
    runId: run.run_id,
    status: run.status,
    pauseRequested: Boolean(run.pause_requested),
    pausable: Boolean(run.pausable),
    resumable: Boolean(run.resumable),
    progress: Math.round(Number(run.progress || 0) * 100),
    stages: (run.stages || []).map((stage) => [stage.stage_id, stage.status, stage.summary, stage.error_message]),
    artifact: run.output_artifact?.file_path || run.output_artifact?.summary || "",
    interaction: run.interaction || {},
    recovery: run.recovery || {},
    advisor: run.advisor_report || null,
    parent: run.parent_run_id || "",
    events: (run.events || []).map((item) => [item.event_id, item.type, item.summary, item.created_at]),
    messages: (run.messages || []).map((message) => [message.message_id, message.role, message.content, message.processing_ms, message.created_at, message.usage]),
  });
  if (state.lastRunRenderKey === renderKey) return;
  state.lastRunRenderKey = renderKey;
  const answerArea = byId("answerArea");
  if (state.runExecutionDetailsRunId !== String(run.run_id || "")) {
    state.runExecutionDetailsRunId = String(run.run_id || "");
    state.runExecutionDetailsOpen = false;
  }
  const scrollSnapshot = conversationScrollSnapshot();
  byId("conversationTitle").textContent = ["literature_review", "deep_research"].includes(run.workflow_type) ? researchDocumentPresentation(run).conversationTitle : compact(runDisplayTitle(run), 80);
  const percent = Math.round((run.progress || 0) * 100);
  const evidenceIndex = run.workflow_type === "evidence_index" ? evidenceIndexContext(run) : null;
  const stageCalls = new Map((run.tool_calls || []).map((call) => [call.stage_id, call]));
  const stageRows = (run.stages || []).map((stage) => {
    const call = stageCalls.get(stage.stage_id);
    const isIndexBuild = Boolean(evidenceIndex && stage.key === "build");
    const stageTitle = isIndexBuild
      ? `正在优化「${evidenceIndex.title}」的语义检索`
      : stage.title;
    const reuseDetail = evidenceIndex?.migrated
      ? ` · 已从旧索引迁移复用 ${evidenceIndex.migrated.toLocaleString("zh-CN")} 条向量`
      : evidenceIndex?.reused
        ? ` · 已复用 ${evidenceIndex.reused.toLocaleString("zh-CN")} 条缓存`
        : "";
    const indexDetail = evidenceIndex?.total
      ? `已处理 ${evidenceIndex.completed.toLocaleString("zh-CN")} / ${evidenceIndex.total.toLocaleString("zh-CN")} 条原文证据${reuseDetail}`
      : "正在准备原文证据的语义检索";
    const detail = isIndexBuild
      ? (stage.error_message || indexDetail)
      : (stage.error_message || stage.summary || (call ? `${call.tool_name}${call.status === "running" ? " · 调用中" : ""}` : "等待执行"));
    return { stage, call, title: stageTitle, detail };
  });
  const stages = stageRows.map(({ stage, call, title, detail }) => `<li class="run-stage ${escapeHtml(stage.status)}"><span class="stage-node">${stage.status === "completed" ? "✓" : stage.status === "running" ? "·" : stage.status === "failed" ? "!" : stage.position + 1}</span><div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(detail)}</small></div>${call ? `<code>${escapeHtml(call.tool_name)}</code>` : ""}</li>`).join("");
  const currentStageRow = stageRows.find(({ stage }) => stage.status === "running")
    || stageRows.find(({ stage }) => ["pending", "queued", "waiting"].includes(String(stage.status || "")))
    || stageRows.find(({ stage }) => stage.status === "failed");
  const artifact = run.output_artifact;
  const partialDownloadMarkup = partialDownloadArtifactMarkup(run);
  let resultMarkup = "";
  if (artifact?.payload && artifact.artifact_type === "research_idea_card") {
    resultMarkup = researchIdeaCardMarkup(artifact.payload);
  } else if (artifact?.payload && artifact.artifact_type === "novelty_assessment") {
    resultMarkup = noveltyAssessmentMarkup(artifact.payload);
  } else if (artifact?.payload && artifact.artifact_type === "academic_search_result") {
    resultMarkup = academicSearchArtifactMarkup(artifact.payload);
  } else if (artifact?.payload && ["evidence_answer", "literature_review", "deep_research_report"].includes(artifact.artifact_type)) {
    resultMarkup = evidenceArtifactMarkup(artifact.payload);
  } else if (artifact?.payload && ["slide_outline", "slide_deck_project", "presentation_deck"].includes(artifact.artifact_type)) {
    resultMarkup = slideProjectArtifactMarkup(artifact.payload, run.run_id);
  } else if (artifact?.payload && artifact.artifact_type === "downloaded_paper") {
    resultMarkup = downloadedPaperArtifactMarkup(artifact);
  } else if (artifact?.artifact_type === "translated_pdf") {
    const details = artifact.payload || {};
    const warnings = Array.isArray(details.warnings) ? details.warnings : [];
    resultMarkup = `<article class="artifact-card"><span class="artifact-type">文献翻译</span><h3>${escapeHtml(artifact.title || "译文 PDF")}</h3><p>已保存 ${escapeHtml(String(details.pages || "?"))} 页到翻译库。${escapeHtml(artifact.file_path || "")}</p><button type="button" class="secondary-button" data-action="open-translation-run-pdf" data-run-id="${escapeHtml(run.run_id)}">打开译文 PDF</button> <button type="button" class="secondary-button" data-action="show-translation-library">查看翻译库</button>${warnings.length ? `<p>${escapeHtml(warnings.join("；"))}</p>` : ""}</article>`;
  } else if (artifact) {
    resultMarkup = genericArtifactMarkup(artifact);
  } else if (run.workflow_type === "evidence_index" && run.status === "completed") {
    const cache = (run.stages || []).find((stage) => stage.key === "build")?.output?.vector_cache || {};
    const migrated = Math.max(0, Number(run?.input?.migrated_vectors || 0));
    const reuseNote = migrated
      ? `已从旧索引迁移复用 ${escapeHtml(String(migrated))} 条向量；`
      : "";
    resultMarkup = `<div class="run-state-message"><strong>「${escapeHtml(evidenceIndex?.title || "当前知识库")}」的语义检索已就绪</strong><p>${reuseNote}已索引 ${escapeHtml(String(cache.completed || 0))}/${escapeHtml(String(cache.total || 0))} 条原文证据，后续检索会直接复用持久缓存。</p></div>`;
  } else if (partialDownloadMarkup) {
    const stateMessage = run.status === "paused"
      ? `<div class="run-state-message"><strong>任务已暂停</strong><p>${escapeHtml(run.error?.message || "可以从当前阶段继续。")}</p></div>`
      : run.status === "failed"
        ? `<div class="run-state-message is-error"><strong>下载阶段未完成</strong><p>${escapeHtml(runFailureSummary(run))}</p></div>`
        : `<div class="run-live"><span></span><div><strong>${escapeHtml(runStatusLabel(run))}</strong><p>已保存中间结果，正在继续下载。</p></div></div>`;
    resultMarkup = `${partialDownloadMarkup}${stateMessage}`;
  } else if (run.status === "failed") {
    resultMarkup = "";
  } else if (run.status === "cancelled") {
    resultMarkup = '<div class="run-state-message"><strong>任务已停止</strong><p>已完成的阶段和工具记录仍然保留，可以继续。</p></div>';
  } else if (run.status === "paused") {
    resultMarkup = `<div class="run-state-message"><strong>任务已暂停</strong><p>${escapeHtml(run.error?.message || "可以从当前阶段继续。")}</p></div>`;
  } else {
    resultMarkup = "";
  }
  const actions = run.workflow_type === "pdf-translation" ? "" : `<button type="button" class="run-action" data-action="branch-run" data-run-id="${escapeHtml(run.run_id)}">建立分支</button>`;
  if (["literature_review", "deep_research"].includes(run.workflow_type)) {
    const reviewModel = artifact?.payload ? buildReviewDocumentModel(run, artifact) : null;
    state.reviewDocument = reviewModel;
    applyContextPanelPreset(state.reviewDocumentOpen ? "review" : "none");
    renderReviewDocument(run, artifact, reviewModel);
    byId("answerArea").innerHTML = reviewTaskMarkup(run, reviewModel, { percent, stages, actions });
    if (run.status === "completed" && scrollSnapshot.top === 0) {
      byId("answerArea").scrollTop = 0;
      updateConversationScrollAffordance();
    } else {
      restoreConversationScroll(scrollSnapshot);
    }
    bindRunCitations(run);
    return;
  }
  state.reviewDocument = null;
  state.reviewDocumentOpen = false;
  // The primary artifact owns the canvas. Only workflows whose normal use
  // depends on sources receive a persistent context panel; citations can
  // still open the evidence reader temporarily from every artifact.
  applyContextPanelPreset(contextPanelPresetForRun(run));
  const userPromptText = evidenceIndex ? evidenceIndexRunTitle(run) : runUserPromptText(run);
  const userPrompt = evidenceIndex ? evidenceIndexRunTitle(run) : runUserPromptMarkup(run);
  const userMessage = conversationMessageMarkup({
    role: "user",
    content: userPromptText,
    contentMarkup: `<div class="user-turn-bubble">${messageSkillTokensMarkup(skillRecordsForIds(run.input?.skills || []))}${composerSourcePreviewMarkup(run.input?.source_files || [])}${composerImagePreviewMarkup(run.input?.images || [])}<p>${userPrompt}</p></div>`,
    createdAt: run.created_at,
  });
  const workflowLabel = ({ evidence_index: "语义检索", ask: "证据问答", literature_review: "证据综述", academic_search: "学术搜索", deep_research: "深度研究", research_idea: "研究构思", novelty_check: "证据查新", paper_download: "文献下载", paper_download_batch: "批量下载", paper_search_download: "检索并下载", ppt_outline: "幻灯片大纲", ppt_project: "演示项目", pdf_to_ppt: "PPTX", "pdf-translation": "文献翻译" })[run.workflow_type] || "科研任务";
  const active = !["completed", "failed", "cancelled", "paused"].includes(String(run.status || ""));
  const runResult = resultMarkup ? `<section class="run-result">${resultMarkup}</section>` : "";
  const activityTitle = active
    ? (currentStageRow?.title || (evidenceIndex ? evidenceIndexRunTitle(run) : runStatusLabel(run)))
    : ({ completed: "已完成", failed: "任务未完成", cancelled: "已停止", paused: "已暂停" })[run.status] || runStatusLabel(run);
  const summaryTitle = run.workflow_type === "pdf-translation"
    ? (active ? "正在翻译" : run.status === "completed" ? "翻译完成" : "翻译未完成")
    : active ? "正在研究" : run.status === "failed" ? "研究未完成" : run.status === "completed" ? "研究完成" : activityTitle;
  const detailsMeta = `${activityTitle}${percent || run.status === "completed" ? ` · ${percent}%` : ""}`;
  const summaryActions = runSummaryActionsMarkup(run);
  const summaryIcon = active ? "refresh" : run.status === "failed" ? "x" : "check";
  const recentEvents = compactRecentRunEvents(run.events);
  const eventTrace = recentEvents.length
    ? `<div class="run-event-trace"><div class="run-event-trace-label">最近动态 · ${escapeHtml(String(run.event_count || recentEvents.length))}</div><ol>${recentEvents.map(({ item, summary, count }) => `<li><span>${escapeHtml(summary)}${count > 1 ? `<small class="run-event-repeat">×${count}</small>` : ""}</span><time>${escapeHtml(formatMessageTime(item.created_at))}</time></li>`).join("")}</ol></div>`
    : "";
  const detailsOpen = state.runExecutionDetailsRunId === String(run.run_id || "") && state.runExecutionDetailsOpen;
  const indexContext = evidenceIndex
    ? `<p class="run-index-context">资料库：<strong>${escapeHtml(evidenceIndex.title)}</strong>${evidenceIndex.sourceCount ? ` · ${escapeHtml(String(evidenceIndex.sourceCount))} 个文档` : ""}<span>原文件与原文证据无需重新导入</span></p>`
    : "";
  const executionLog = `<details class="run-execution-log ${evidenceIndex ? "is-evidence-index" : ""}" ${detailsOpen ? "open" : ""}><summary><span>${uiIcon(summaryIcon)}</span><div><strong>${escapeHtml(summaryTitle)}</strong><small>${escapeHtml(detailsMeta)}</small></div><div class="run-summary-actions">${summaryActions}</div>${uiIcon("chevron-right", "run-execution-chevron")}</summary><section class="run-card"><header class="run-card-head"><div><span class="run-kind">${escapeHtml(workflowLabel)}</span><h2>${escapeHtml(runDisplayTitle(run))}</h2>${indexContext}</div><div class="run-head-actions"><span class="run-status ${escapeHtml(run.status)}">${escapeHtml(runStatusLabel(run))}</span>${actions}</div></header>${runControlPlaneMarkup(run)}<div class="run-progress"><i class="${progressWidthClass(percent)}"></i></div><ol class="run-stage-list">${stages}</ol>${eventTrace}</section></details>${runResult}`;
  byId("answerArea").innerHTML = `<article class="run-shell">${userMessage}${executionLog}${runCompletionMessageMarkup(run)}</article>`;
  const executionDetails = answerArea.querySelector(".run-execution-log");
  executionDetails?.addEventListener("toggle", () => {
    state.runExecutionDetailsOpen = executionDetails.open;
    state.runExecutionDetailsRunId = String(run.run_id || "");
  });
  const taskConversation = taskConversationMarkup(run);
  if (taskConversation) answerArea.querySelector(".run-shell")?.insertAdjacentHTML("beforeend", taskConversation);
  bindRunCitations(run);
  restoreConversationScroll(scrollSnapshot);
}

function taskConversationMarkup(run) {
  const messages = Array.isArray(run.messages) ? run.messages : [];
  if (!messages.length) return "";
  const runCitations = citationRecordsForRun(run);
  const turns = messages.map((message, index) => {
    const promptContent = message.role === "assistant"
      ? [...messages.slice(0, index)].reverse().find((item) => item.role === "user")?.content || runUserPromptText(run)
      : "";
    const reader = message.reader_answer || message.metadata?.reader_answer || {};
    const citations = reader.citations?.length ? reader.citations : runCitations;
    return conversationMessageMarkup({
      role: message.role,
      content: message.content,
      contentMarkup: message.role === "assistant" && citations.length
        ? `<div class="answer-sentence">${citationTextMarkup(message.content, citations)}</div>`
        : "",
      createdAt: message.created_at,
      usage: message.usage,
      model: run.model,
      processing: message.role === "assistant" ? processTraceMarkup(message, Number(message.processing_ms || 0)) : "",
      classes: message.role === "assistant" ? "direct-answer" : "",
      promptContent,
    });
  }).join("");
  return `<section class="task-conversation" aria-label="任务消息"><div class="conversation-thread">${turns}</div></section>`;
}

function evidenceArtifactMarkup(result) {
  const reader = result.reader_answer || {};
  const sentences = reader.sentences || [];
  const answerMarkup = sentences.length ? sentences.map((sentence) => {
    const citations = (sentence.citation_ids || []).map(citationMarkerMarkup).join("");
    return `<p class="answer-sentence">${escapeHtml(sentence.text)} ${citations}</p>`;
  }).join("") : '<p class="answer-sentence">当前资料不足以生成可核验回答。</p>';
  const limitations = (result.answer?.limitations || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const sufficient = result.adequacy?.is_sufficient;
  const vision = result.image_analysis?.text ? `<section class="vision-analysis"><header><span>◉</span><div><strong>用户图片分析</strong><small>${escapeHtml(result.image_analysis?.model || "视觉模型")}</small></div></header><p>${escapeHtml(result.image_analysis.text)}</p><footer>基于你上传的图片生成，不作为资料库引文。</footer></section>` : "";
  return `<div class="assistant-turn artifact-answer">${vision}<div class="answer-meta"><span>ScanSci</span><b>${sufficient ? "资料可支持" : "资料不足"}</b></div>${answerMarkup}${limitations ? `<div class="answer-limitations"><strong>限制</strong><ul>${limitations}</ul></div>` : ""}</div>`;
}

function slideProjectArtifactMarkup(payload, runId = "") {
  const outline = payload.outline || payload;
  const template = payload.template || outline.template || selectedSlideTemplate();
  const slides = outline.slides || [];
  const nativeEasySlides = payload.render_mode === "native" || String(payload.renderer || "").startsWith("easyslides");
  const slideCount = Number(payload.slide_count) || slides.length;
  const qualityPassed = payload.quality_gate?.status === "pass";
  state.activeSlidePlan = nativeEasySlides ? null : (payload.slide_plan || null);
  if (payload.pptx_path) {
    const download = runId ? `<button type="button" class="primary-button slide-download-button" data-action="save-presentation" data-presentation-path="${escapeHtml(payload.pptx_path)}" data-presentation-name="${escapeHtml(payload.download_name || "ScanSci-演示文稿.pptx")}">下载 PPTX</button>` : "";
    const enhancedDownload = !nativeEasySlides && payload.slide_plan ? '<button type="button" class="secondary-button slide-enhanced-button" data-action="export-pptxgenjs">重新排版导出</button>' : "";
    const sourceNames = (payload.sources || []).map((source) => source.name).filter(Boolean).join(" · ");
    const preview = runId
      ? `<img class="slide-project-cover" src="/api/runs/${encodeURIComponent(runId)}/preview" alt="${escapeHtml(outline.title || "演示文稿预览")}" />`
      : (template?.preview_url ? `<img class="slide-project-cover" src="${escapeHtml(template.preview_url)}" alt="${escapeHtml(template.name || "所选模板预览")}" />` : `<div class="slide-project-cover">${uiIcon("presentation")}<b>PPTX</b></div>`);
    const templateName = template?.name ? ` · ${template.name}` : "";
    const resultLabel = qualityPassed || nativeEasySlides ? "已生成" : "已生成 · 需要检查";
    const exportHint = nativeEasySlides
      ? "下载后可继续编辑。"
      : "可下载 PPTX，也可重新排版导出。";
    return `<div class="slide-project-artifact is-pptx">${preview}<div class="slide-project-copy"><span>${escapeHtml(resultLabel)}</span><h3>${escapeHtml(outline.title || "科研幻灯片")}</h3><p>${escapeHtml(`${slideCount} 页${templateName}`)}</p>${sourceNames ? `<small>来源：${escapeHtml(sourceNames)}</small>` : ""}${payload.pptx_path ? `<div class="artifact-file-link">${localFileLinkMarkup(payload.pptx_path, payload.download_name || localPathLeaf(payload.pptx_path), { inline: true })}</div>` : ""}<p class="slide-export-hint">${escapeHtml(exportHint)}</p><div class="slide-download-actions">${enhancedDownload}${download}</div></div></div>`;
  }
  const preview = template?.preview_url ? `<img class="slide-project-cover" src="${escapeHtml(template.preview_url)}" alt="${escapeHtml(template.name || "幻灯片模板")}" />` : '<div class="slide-project-cover"></div>';
  const slideSummary = slides.length ? `${slides.length} 页 · ${outline.evidence_linked ? "已绑定来源" : "待绑定来源"}` : "演示项目";
  const projectActions = payload.project_path
    ? `<div class="artifact-file-link">${window.pywebview?.api ? localFileLinkMarkup(payload.project_path, "打开项目", { folder: true, inline: true }) : ""}${runId ? runArtifactDownloadLinkMarkup(runId, payload.project_path, { folder: true, label: "下载项目 ZIP" }) : ""}</div>`
    : "";
  return `<div class="slide-project-artifact">${preview}<div class="slide-project-copy"><span>演示文稿</span><h3>${escapeHtml(template?.name || "学术幻灯片")}</h3><p>${escapeHtml(slideSummary)}</p>${projectActions}</div></div>`;
}

async function exportActiveSlidePlan(button) {
  if (!state.activeSlidePlan) throw new Error("当前任务没有可导出的幻灯片计划");
  if (!window.ScanSciPptxExporter) throw new Error("高质量 PPTX 导出组件尚未加载");
  const original = button?.textContent || "高质量导出";
  if (button) {
    button.disabled = true;
    button.textContent = "正在生成…";
  }
  try {
    const result = await window.ScanSciPptxExporter.export(state.activeSlidePlan, state.activeSlidePlan.title);
    const saved = await savePresentationToDevice(result.file_path, result.download_name);
    if (!saved.cancelled) toast(`已保存 ${saved.path || result.download_name}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = original;
    }
  }
}

async function savePresentationToDevice(sourcePath, suggestedName) {
  const nativeSave = window.pywebview?.api?.save_presentation_copy;
  if (typeof nativeSave === "function") {
    const result = await nativeSave(String(sourcePath || ""), String(suggestedName || "ScanSci-演示文稿.pptx"));
    if (!result?.ok && !result?.cancelled) throw new Error(result?.message || "保存 PPTX 失败");
    return result || { ok: false, cancelled: true };
  }
  const response = await fetch(`/api/presentations/${encodeURIComponent(String(suggestedName || "").replace(/^.*[\\/]/, ""))}`);
  if (!response.ok) throw new Error("无法读取待下载的 PPTX");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = suggestedName || "ScanSci-演示文稿.pptx";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 3000);
  return { ok: true, path: suggestedName };
}

function splitReviewSentences(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return [];
  return text.split(/(?<=[。！？!?])\s*|(?<=\.)(?=\s+[A-Z\u3400-\u9fff])/u).map((item) => item.trim()).filter(Boolean);
}

function normalizeReviewParagraph(value, index = 0) {
  if (typeof value === "string") {
    return {
      id: `paragraph-${index + 1}`,
      text: value,
      citation_ids: [],
      sentences: splitReviewSentences(value).map((text) => ({ text, citation_ids: [] })),
    };
  }
  const text = String(value?.text || value?.rendered_text || "");
  const citationIds = (value?.citation_ids || []).map(String);
  const suppliedSentences = Array.isArray(value?.sentences) ? value.sentences : [];
  const sentences = suppliedSentences.length
    ? suppliedSentences.map((sentence) => ({
      text: String(sentence?.text || ""),
      citation_ids: (sentence?.citation_ids || []).map(String),
    })).filter((sentence) => sentence.text)
    // Older artifacts only know which sources support the paragraph as a
    // whole. Keep that scope intact instead of guessing a sentence mapping.
    : (text ? [{ text, citation_ids: [...citationIds] }] : []);
  return {
    id: String(value?.id || `paragraph-${index + 1}`),
    text,
    citation_ids: citationIds,
    sentences,
  };
}

function reviewDisplayTitle(run, supplied = {}) {
  const question = String(run?.input?.question || run?.title || "").replace(/\s+/g, " ").trim();
  const candidate = String(supplied?.title || "").replace(/\s+/g, " ").trim();
  const folded = `${candidate} ${question}`.toLocaleLowerCase();
  if (folded.includes("transformer") && folded.includes("bert") && /\bgpt-?3\b/i.test(folded)) {
    return "Transformer、BERT 与 GPT-3：架构、训练与能力边界";
  }
  const compactQuestion = question.replace(/\s+/g, "").toLocaleLowerCase();
  const compactCandidate = candidate.replace(/\s+/g, "").toLocaleLowerCase();
  const instructionLike = /^(请|请你|帮我|基于)|必须|每个实质性段落|不得/.test(candidate);
  const replaysQuestion = compactQuestion && compactCandidate
    && (compactQuestion.includes(compactCandidate) || compactCandidate.includes(compactQuestion));
  if (candidate && candidate.length <= 72 && !instructionLike && !replaysQuestion) {
    return candidate.replace(/[。；;：:\s]+$/, "");
  }
  const comparison = question.match(/(?:比较|对比)\s*([^。；;]+?)(?=(?:，?必须|，?需要|，?请|，?每个|，?不得|。|；|;|$))/);
  if (comparison?.[1]) {
    const subject = comparison[1].replace(/^(?:一下|原始论文中的)/, "").replace(/^[\s，,：:]+|[\s，,：:。；;]+$/g, "");
    if (subject) return `${subject.slice(0, 52)}：比较综述`;
  }
  let topic = question;
  const colon = topic.search(/[：:]/);
  if (colon >= 0 && /请|帮我|基于|撰写|生成/.test(topic.slice(0, colon))) topic = topic.slice(colon + 1);
  topic = topic.split(/必须|每个实质性段落|不得|请分别|需要分别/, 1)[0]
    .replace(/^(?:请|请你|帮我|请基于[^，,。；;]{0,60})\s*/, "")
    .replace(/^(?:撰写|写|生成|整理)(?:一篇|一份)?(?:中文)?(?:文献)?综述[：:]?\s*/, "")
    .replace(/(?:有哪些|有什么)?(?:可用)?证据[？?]?$/, "")
    .replace(/^[\s，,：:。；;？?"'“”]+|[\s，,：:。；;？?"'“”]+$/g, "");
  const documentKind = String(run?.input?.document_kind || "");
  const suffix = documentKind === "academic_writing"
    ? "学术稿件"
    : run?.workflow_type === "deep_research" ? "研究报告" : "证据综述";
  return topic ? `${topic.slice(0, 52)}：${suffix}` : documentKind === "academic_writing" ? "学术写作稿件" : run?.workflow_type === "deep_research" ? "深度研究报告" : "文献证据综述";
}

function researchDocumentPresentation(run = {}, model = {}) {
  const workflowType = String(run?.workflow_type || model?.workflowType || "literature_review");
  const documentKind = String(run?.input?.document_kind || model?.documentKind || "");
  const academicWriting = documentKind === "academic_writing";
  const deepResearch = workflowType === "deep_research";
  const evidenceLevel = String(model?.evidenceLevel || "");
  const originLabel = deepResearch
    ? evidenceLevel === "task_acquired_fulltext" || evidenceLevel === "fulltext"
      ? "本次联网调查取得的全文"
      : evidenceLevel === "external_source_abstracts"
        ? "公开学术摘要"
        : "联网学术来源"
    : "所选知识库原文";
  return {
    conversationTitle: academicWriting ? "学术写作" : deepResearch ? "深度研究" : "证据综述",
    toolbarTitle: academicWriting ? (deepResearch ? "联网学术写作" : "学术写作稿件") : deepResearch ? "深度研究报告" : "证据综述稿件",
    kicker: academicWriting ? "学术写作" : deepResearch ? "深度研究" : "证据综述",
    agentLabel: academicWriting ? "ScanSci 学术写作智能体" : deepResearch ? "ScanSci 深度研究智能体" : "ScanSci 写作智能体",
    requestLabel: academicWriting ? "本次写作请求" : "本次研究请求",
    openLabel: academicWriting || deepResearch ? "打开报告" : "打开稿件",
    originLabel,
  };
}

function evidenceIndexContext(run) {
  const notebookId = String(run?.notebook_id || "");
  const notebook = (state.workspace?.notebooks || []).find((item) => String(item.notebook_id) === notebookId);
  const buildStage = (run?.stages || []).find((stage) => stage.key === "build") || {};
  const output = buildStage.output || {};
  const total = Math.max(0, Number(output.total || run?.input?.total_vectors || 0));
  const reportedCompleted = Number(output.completed || 0);
  const completed = Math.min(
    total || Number.MAX_SAFE_INTEGER,
    Math.max(reportedCompleted, Math.round(Number(run?.progress || 0) * total)),
  );
  return {
    title: String(notebook?.title || run?.input?.notebook_title || "当前知识库"),
    sourceCount: Math.max(0, Number(notebook?.counts?.sources || 0)),
    completed,
    total,
    reused: Math.max(0, Number(run?.input?.cached_vectors || 0)),
    migrated: Math.max(0, Number(run?.input?.migrated_vectors || 0)),
  };
}

function evidenceIndexRunTitle(run) {
  const context = evidenceIndexContext(run);
  return `正在优化「${context.title}」的语义检索`;
}

function runDisplayTitle(run) {
  if (run?.workflow_type === "evidence_index") return evidenceIndexRunTitle(run);
  if (!["literature_review", "deep_research"].includes(run?.workflow_type)) return String(run?.title || "未命名研究");
  return reviewDisplayTitle(run, run?.output_artifact?.payload?.review_document || {});
}

function buildReviewDocumentModel(run, artifact) {
  const payload = artifact?.payload || {};
  const supplied = payload.review_document || {};
  const legacy = !payload.review_document;
  const answer = payload.answer && typeof payload.answer === "object" ? payload.answer : {};
  const reader = payload.reader_answer || answer.reader_answer || {};
  const sentenceSource = [reader.sentences, answer.sentences]
    .find((items) => Array.isArray(items) && items.length)
    || String(reader.text || answer.text || "").split(/\n\s*\n/).filter(Boolean);
  const sentences = sentenceSource.map(normalizeReviewParagraph).filter((item) => item.text);
  const citationSource = [supplied.references, reader.citations, answer.citations, payload.citations, artifact?.citations]
    .find((items) => Array.isArray(items) && items.length) || [];
  const citations = citationSource.map((citation, index) => ({
    ...citation,
    citation_id: String(citation.citation_id || citation.id || citation.source_id || index + 1),
    paper: citation.paper || citation.title || citation.doc_id || `来源 ${index + 1}`,
  }));
  const abstract = normalizeReviewParagraph(supplied.abstract || sentences[0] || artifact?.summary || "当前资料库尚未形成可展示的摘要。", 0);
  let sections = [];
  if (Array.isArray(supplied.sections) && supplied.sections.length) {
    sections = supplied.sections.map((section, index) => ({
      id: String(section.id || `review-section-${index + 1}`),
      title: String(section.title || `主题 ${index + 1}`),
      paragraphs: (section.paragraphs || section.content || []).map(normalizeReviewParagraph).filter((item) => item.text),
    }));
  } else {
    const synthesis = (sentences.length > 1 ? sentences.slice(1) : sentences).filter((item) => item.text);
    sections = [{ id: "review-synthesis", title: "证据综合", paragraphs: synthesis.length ? synthesis : [abstract] }];
  }
  const limitations = (supplied.limitations || payload.answer?.limitations || []).map(String);
  const comparisonTable = {
    columns: (supplied.comparison_table?.columns || []).map(String),
    rows: (supplied.comparison_table?.rows || []).map((row) => ({
      cells: (row?.cells || []).map(String),
      citation_ids: (row?.citation_ids || []).map(String),
    })).filter((row) => row.cells.length),
  };
  const controversies = (supplied.controversies || []).map(normalizeReviewParagraph).filter((item) => item.text);
  const openQuestions = (supplied.open_questions || []).map((item, index) => ({
    ...normalizeReviewParagraph(item, index),
    basis: String(item?.basis || ""),
  })).filter((item) => item.text);
  const scope = typeof supplied.scope === "string" ? supplied.scope : String(supplied.scope?.description || "");
  const discoveryLeads = (payload.discovery?.items || []).slice(0, 20).map((item) => ({
    title: String(item.title || "未命名文献"),
    doi: String(item.doi || ""),
    year: item.year || "",
    venue: String(item.venue || ""),
    url: String(item.url || item.oa_url || ""),
    sources: (item.sources || [item.source]).filter(Boolean).map(String),
    acquired: Boolean((payload.acquisition?.acquired || []).some((record) => String(record.doi || "").toLowerCase() === String(item.doi || "").toLowerCase() && item.doi)),
  }));
  const documentCount = Number(payload.adequacy?.document_count || new Set(citations.map((item) => item.doc_id).filter(Boolean)).size || 0);
  const evidenceNotice = String(payload.evidence_notice || "");
  const evidenceLevel = String(payload.evidence_level || payload.evidence_status || "");
  const researchTrace = payload.research_trace || {};
  const answerability = String(payload.adequacy?.answerability || answer.answerability || "");
  const needsReview = answerability === "needs_review" || Boolean(answer.review_required);
  const insufficientEvidence = !needsReview && Boolean(payload.answer?.insufficient_evidence || payload.adequacy?.is_sufficient === false);
  const verified = !legacy && !insufficientEvidence && Boolean(payload.citation_verification?.passed ?? payload.answer?.citation_verification?.passed ?? payload.verification?.supported_claims?.length);
  const title = reviewDisplayTitle(run, supplied);
  const model = {
    title,
    workflowType: String(run?.workflow_type || "literature_review"),
    documentKind: String(run?.input?.document_kind || ""),
    requestText: runUserPromptText(run),
    writingBrief: run?.input?.writing_brief && typeof run.input.writing_brief === "object" ? { ...run.input.writing_brief } : {},
    abstract,
    sections,
    scope,
    comparisonTable,
    controversies,
    openQuestions,
    limitations,
    citations,
    discoveryLeads,
    documentCount,
    citationCount: Number(reader.citation_count || citations.length || 0),
    evidenceNotice,
    evidenceLevel,
    answerability,
    needsReview,
    sourceScope: payload.source_scope && typeof payload.source_scope === "object" ? { ...payload.source_scope } : {},
    researchTrace,
    verified,
    insufficientEvidence,
    legacy,
    generatedAt: artifact?.created_at || run.updated_at || "",
  };
  model.outline = [
    { id: "review-abstract", title: "摘要" },
    ...sections.map((section) => ({ id: section.id, title: section.title })),
    ...(comparisonTable.rows.length ? [{ id: "review-comparison", title: "研究对比" }] : []),
    ...(controversies.length ? [{ id: "review-controversies", title: "证据分歧与争议" }] : []),
    ...(openQuestions.length ? [{ id: "review-open-questions", title: "开放问题" }] : []),
    ...(discoveryLeads.length ? [{ id: "review-discovery", title: "检索候选" }] : []),
    { id: "review-limitations", title: "证据边界" },
    { id: "review-references", title: "参考文献" },
  ];
  model.markdown = reviewDocumentMarkdown(model);
  return model;
}

function reviewDocumentMarkdown(model) {
  const citationSuffix = (ids = []) => ids.length ? ` ${ids.map((id) => `[${id}]`).join("")}` : "";
  const citedText = (item) => (item.sentences?.length ? item.sentences : [item])
    .map((sentence) => `${sentence.text}${citationSuffix(sentence.citation_ids)}`)
    .join(" ");
  const lines = [
    `# ${model.title}`,
    "",
    ...(model.requestText ? ["> 原始请求", ">", ...String(model.requestText).split("\n").map((line) => `> ${line}`), ""] : []),
    "## 摘要",
    "",
    citedText(model.abstract),
  ];
  model.sections.forEach((section) => {
    lines.push("", `## ${section.title}`, "");
    section.paragraphs.forEach((paragraph) => lines.push(citedText(paragraph), ""));
  });
  if (model.comparisonTable.rows.length) {
    const escapeCell = (value) => String(value).replaceAll("|", "\\|").replaceAll("\n", " ");
    const tableHeaders = [...model.comparisonTable.columns, "证据"];
    lines.push("", "## 研究对比", "", `| ${tableHeaders.map(escapeCell).join(" | ")} |`, `| ${tableHeaders.map(() => "---").join(" | ")} |`);
    model.comparisonTable.rows.forEach((row) => lines.push(`| ${[...row.cells, citationSuffix(row.citation_ids).trim()].map(escapeCell).join(" | ")} |`));
  }
  if (model.controversies.length) {
    lines.push("", "## 证据分歧与争议", "");
    model.controversies.forEach((item) => lines.push(`- ${citedText(item)}`));
  }
  if (model.openQuestions.length) {
    lines.push("", "## 开放问题", "");
    model.openQuestions.forEach((item) => lines.push(`- ${citedText(item)}${item.basis ? `\n  - 依据：${item.basis}` : ""}`));
  }
  if (model.discoveryLeads.length) {
    lines.push("", "## 检索候选（尚未自动视为证据）", "");
    model.discoveryLeads.forEach((item) => lines.push(`- ${item.title}${item.year ? ` (${item.year})` : ""}${item.doi ? ` — ${item.doi}` : ""}${item.acquired ? " — 已获取全文" : ""}`));
  }
  lines.push("", "## 证据边界", "");
  if (model.limitations.length) model.limitations.forEach((item) => lines.push(`- ${item}`));
  else if (model.workflowType === "deep_research") lines.push("- 本稿件仅综合本次联网研究任务取得的可追溯来源；未检索到的研究不代表不存在。");
  else lines.push("- 本稿件仅综合当前项目资料库中的可核验证据；未覆盖的研究方向不代表不存在相关工作。");
  lines.push("", "## 参考文献", "");
  model.citations.forEach((citation, index) => {
    const sourceUrl = citationPublicSourceUrl(citation);
    const source = sourceUrl ? ` — ${sourceUrl}` : citation.doi ? ` — https://doi.org/${citation.doi}` : "";
    lines.push(`${citation.citation_id || index + 1}. ${citation.paper}${source}`);
  });
  return lines.join("\n").trim();
}

function reviewCitationButtons(ids = []) {
  return ids.map((id) => `<button type="button" class="review-inline-citation" data-action="open-review-citation" data-citation-id="${escapeHtml(id)}" aria-label="预览证据 ${escapeHtml(id)}" aria-haspopup="dialog" title="预览证据 ${escapeHtml(id)}">${escapeHtml(id)}</button>`).join("");
}

function reviewCitedTextMarkup(item) {
  const sentences = item?.sentences?.length ? item.sentences : [item || {}];
  return sentences.map((sentence) => `${escapeHtml(sentence.text || "")}${reviewCitationButtons(sentence.citation_ids || [])}`).join(" ");
}

function bindReviewCitationInteractions(model, scope = byId("reviewDocumentPanel")) {
  if (!scope || !model) return;
  const citations = new Map((model.citations || []).map((citation) => [String(citation.citation_id), citation]));
  scope.querySelectorAll('[data-action="open-review-citation"]').forEach((marker) => {
    const citation = citations.get(String(marker.dataset.citationId || ""));
    if (!citation) return;
    marker.addEventListener("pointerenter", () => showCitationPreview(citation, marker));
    marker.addEventListener("pointerleave", deferCitationPreviewHide);
    marker.addEventListener("focus", () => showCitationPreview(citation, marker));
    marker.addEventListener("blur", deferCitationPreviewHide);
    marker.addEventListener("keydown", (event) => {
      if (event.key === "Escape") hideCitationPreview();
    });
  });
  const preview = byId("citationPreview");
  if (preview) {
    preview.onpointerenter = clearCitationPreviewTimer;
    preview.onpointerleave = deferCitationPreviewHide;
  }
}

function reviewDocumentProgressMarkup(run) {
  const stages = [...(run.stages || [])].sort((left, right) => Number(left.position || 0) - Number(right.position || 0));
  const percent = Math.max(0, Math.min(100, Math.round(Number(run.progress || 0) * 100)));
  const current = stages.find((stage) => stage.status === "running")
    || stages.find((stage) => stage.key === run.current_stage)
    || stages.find((stage) => stage.status === "pending")
    || null;
  const completed = stages.filter((stage) => stage.status === "completed").length;
  const currentOutput = current?.output || {};
  const partialCount = Number(currentOutput.completed || 0);
  const partialTotal = Number(currentOutput.total || 0);
  const currentDetail = current?.summary
    || (partialTotal > 0 ? `${partialCount} / ${partialTotal}` : "")
    || (current ? "正在处理此阶段" : "正在准备研究步骤");
  const rows = stages.map((stage) => {
    const output = stage.output || {};
    const done = Number(output.completed || 0);
    const total = Number(output.total || 0);
    const detail = stage.error_message
      || stage.summary
      || (total > 0 ? `${done} / ${total}` : stage.status === "running" ? "正在处理" : stage.status === "completed" ? "已完成" : "等待开始");
    const marker = stage.status === "completed" ? "✓" : stage.status === "failed" ? "!" : stage.status === "running" ? "" : String(Number(stage.position || 0) + 1);
    return `<li class="${escapeHtml(stage.status || "pending")}"><span>${escapeHtml(marker)}</span><div><strong>${escapeHtml(stage.title || "研究步骤")}</strong><small>${escapeHtml(detail)}</small></div></li>`;
  }).join("");
  return `<section class="review-document-progress" aria-live="polite"><header><div><span>研究进展</span><strong>${percent}%</strong></div><p>${escapeHtml(current?.title || runStatusLabel(run))}</p><small>${escapeHtml(currentDetail)}</small></header><div class="review-progress-track" role="progressbar" aria-label="深度研究进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><i class="${progressWidthClass(percent)}"></i></div>${rows ? `<ol>${rows}</ol>` : ""}<footer><span>已完成 ${completed} / ${stages.length || 1} 个步骤</span><span>${escapeHtml(runStatusLabel(run))}</span></footer></section>`;
}

function reviewTaskMarkup(run, model, { percent, stages, actions }) {
  const presentation = researchDocumentPresentation(run, model || {});
  const outline = model?.outline || [
    { id: "review-abstract", title: "摘要" },
    { id: "review-synthesis", title: "主题证据综合" },
    { id: "review-limitations", title: "证据边界与开放问题" },
    { id: "review-references", title: "参考文献" },
  ];
  const outlineMarkup = outline.map((item, index) => `<li><button type="button" data-action="scroll-review-section" data-section-id="${escapeHtml(item.id)}"><b>${String(index + 1).padStart(2, "0")}</b><span>${escapeHtml(item.title)}</span><i>›</i></button></li>`).join("");
  const completed = (run.stages || []).filter((stage) => stage.status === "completed").length;
  const total = (run.stages || []).length;
  const summary = run.status === "completed" ? "全部步骤已完成" : runStatusLabel(run);
  const failure = run.status === "failed" ? `<div class="review-workflow-error"><strong>${escapeHtml(presentation.toolbarTitle)}没有生成</strong><p>${escapeHtml(runFailureSummary(run))}</p><button type="button" data-action="open-settings" data-settings-panel="models">配置写作模型</button></div>` : "";
  const userMessage = conversationMessageMarkup({
    role: "user",
    content: runUserPromptText(run),
    contentMarkup: `<div class="user-turn-bubble">${messageSkillTokensMarkup(skillRecordsForIds(run.input?.skills || []))}<p>${runUserPromptMarkup(run)}</p></div>`,
    createdAt: run.created_at,
    classes: "review-conversation-message",
  });
  return `<article class="review-task-shell">${userMessage}<div class="review-agent-head"><div class="review-agent-identity"><img src="/scansci-mark.png" alt="" /><span>${escapeHtml(presentation.agentLabel)}</span></div><span class="review-agent-meta">${percent}% · ${escapeHtml(runStatusLabel(run))}</span></div>${failure}<section class="review-outline-card"><span>Research outline</span><h2>${escapeHtml(model?.title || runDisplayTitle(run))}</h2><ol class="review-outline-list">${outlineMarkup}</ol><div class="review-run-actions">${actions}<button type="button" class="review-open-document" data-action="open-review-document">${escapeHtml(model ? presentation.openLabel : "查看研究进度")}</button></div></section><details class="review-steps"><summary>${escapeHtml(summary)}<span>${completed}/${total}</span></summary><ol class="run-stage-list">${stages}</ol></details>${runCompletionMessageMarkup(run)}${taskConversationMarkup(run)}</article>`;
}

function reviewRequestContextMarkup(run, model, presentation) {
  const request = String(model?.requestText || runUserPromptText(run) || "").trim();
  if (!request) return "";
  const steps = (run.stages || [])
    .map((stage) => String(stage.title || "").trim())
    .filter(Boolean);
  const stepMarkup = steps.length
    ? `<ol>${steps.map((step) => `<li>${escapeHtml(step)}</li>`).join("")}</ol>`
    : "<p>检索来源、抽取证据、生成正文并核验引用。</p>";
  return `<details class="review-request-context"><summary><span>${escapeHtml(presentation.requestLabel)}</span><strong>${escapeHtml(compact(request, 92))}</strong><em>查看原始请求与生成方式</em>${uiIcon("chevron-down")}</summary><div class="review-request-context-body"><section><span>原始请求（实际提交内容）</span><p>${escapeHtml(request)}</p><button type="button" data-action="return-review-conversation">返回完整对话</button></section><section><span>本页如何形成</span>${stepMarkup}<p class="review-request-origin">引用证据来自：${escapeHtml(presentation.originLabel)}。正文与引用锚点分开生成，并在交付前校验对应关系。</p></section></div></details>`;
}

function renderReviewDocument(run, artifact, model = null) {
  const target = byId("reviewDocumentPanel");
  if (!target) return;
  const ready = Boolean(model);
  const presentation = researchDocumentPresentation(run, model || {});
  const percent = Math.max(0, Math.min(100, Math.round(Number(run.progress || 0) * 100)));
  const currentStage = (run.stages || []).find((stage) => stage.status === "running")
    || (run.stages || []).find((stage) => stage.key === run.current_stage);
  const title = ready ? presentation.toolbarTitle : compact(runDisplayTitle(run) || `正在生成${presentation.toolbarTitle}`, 72);
  const summary = ready ? (model.legacy ? "旧版任务 · 仅包含证据摘录，请重新生成" : model.needsReview ? `需要复核 · ${model.documentCount} 篇来源 · ${model.citationCount} 个证据锚点` : model.insufficientEvidence ? `证据不足 · ${model.discoveryLeads.length} 条检索线索` : `${model.documentCount} 篇来源 · ${model.citationCount} 个证据锚点${model.verified ? " · 引用已核验" : ""}`) : `${percent}% · ${currentStage?.title || runStatusLabel(run)}`;
  const tabButtons = `<nav class="review-document-tabs" aria-label="稿件视图"><button type="button" class="is-active" data-action="review-document-tab" data-review-tab="preview" ${ready ? "" : "disabled"}>预览</button><button type="button" data-action="review-document-tab" data-review-tab="source" ${ready ? "" : "disabled"}>Markdown</button></nav>`;
  const toolbar = `<header class="review-panel-toolbar"><div class="review-toolbar-leading"><button type="button" class="review-back-conversation" data-action="return-review-conversation">${uiIcon("arrow-left")}<span>返回对话</span></button><div class="review-document-identity"><span class="review-file-icon">${uiIcon("file-plus")}</span><div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(summary)}</small></div></div></div><div class="review-toolbar-cluster">${tabButtons}<div class="review-toolbar-actions"><button type="button" class="review-save-note" data-action="save-review-note" ${ready ? "" : "disabled"}>保存为笔记</button><button type="button" class="review-icon-button" data-action="copy-review-document" aria-label="复制稿件" title="复制稿件" ${ready ? "" : "disabled"}>${uiIcon("copy")}</button><button type="button" class="review-icon-button" data-action="refresh-review-document" aria-label="刷新稿件" title="刷新稿件">${uiIcon("refresh")}</button><button type="button" class="review-icon-button" data-action="download-review-document" aria-label="下载 Markdown" title="下载 Markdown" ${ready ? "" : "disabled"}>${uiIcon("download")}</button></div></div></header>`;
  if (!ready) {
    const failed = run.status === "failed";
    const body = failed
      ? `<div class="review-document-empty is-error"><div><strong>未生成稿件</strong><p>${escapeHtml(runFailureSummary(run))}</p></div></div>`
      : `<div class="review-document-progress-shell">${reviewDocumentProgressMarkup(run)}</div>`;
    target.innerHTML = `${toolbar}<div class="review-document-body">${body}</div>`;
    return;
  }
  const sections = model.sections.map((section) => `<section id="${escapeHtml(section.id)}"><h2>${escapeHtml(section.title)}</h2>${section.paragraphs.map((paragraph) => `<p>${reviewCitedTextMarkup(paragraph)}</p>`).join("")}</section>`).join("");
  const comparison = model.comparisonTable.rows.length ? `<section id="review-comparison"><h2>研究对比</h2><div class="review-table-wrap"><table class="review-comparison-table"><thead><tr>${model.comparisonTable.columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}<th>证据</th></tr></thead><tbody>${model.comparisonTable.rows.map((row) => `<tr>${row.cells.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}<td class="review-table-citations">${reviewCitationButtons(row.citation_ids)}</td></tr>`).join("")}</tbody></table></div></section>` : "";
  const controversies = model.controversies.length ? `<section id="review-controversies"><h2>证据分歧与争议</h2><div class="review-finding-list">${model.controversies.map((item, index) => `<article><span>${String(index + 1).padStart(2, "0")}</span><p>${reviewCitedTextMarkup(item)}</p></article>`).join("")}</div></section>` : "";
  const openQuestions = model.openQuestions.length ? `<section id="review-open-questions"><h2>开放问题</h2><div class="review-question-grid">${model.openQuestions.map((item, index) => `<article><span>Q${index + 1}</span><h3>${reviewCitedTextMarkup(item)}</h3>${item.basis ? `<p>${escapeHtml(item.basis)}</p>` : ""}</article>`).join("")}</div></section>` : "";
  const discovery = model.discoveryLeads.length ? `<section id="review-discovery"><h2>检索候选</h2><p class="review-discovery-notice">这些记录来自多源学术搜索；只有标记“已获取全文”的论文才可能进入上方句级证据链。</p><ol class="review-reference-list">${model.discoveryLeads.map((item) => `<li><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml([item.year, item.venue, item.doi].filter(Boolean).join(" · "))}</span><br /><small>${escapeHtml(item.sources.join(" + "))}${item.acquired ? " · 已获取全文" : " · 发现线索"}</small></li>`).join("")}</ol></section>` : "";
  const defaultLimitation = run.workflow_type === "deep_research"
    ? "本报告只使用本次联网研究任务取得且可追溯的学术来源；未检索到的研究不代表不存在。"
    : "本稿件仅综合当前知识库中的可核验证据；未覆盖的研究方向不代表不存在相关工作。";
  const limitations = model.limitations.length ? model.limitations.map((item) => `<p>${escapeHtml(item)}</p>`).join("") : `<p>${escapeHtml(defaultLimitation)}</p>`;
  const references = model.citations.length ? model.citations.map((citation) => `<li><strong>${escapeHtml(citation.paper)}</strong><span>${escapeHtml([citation.section, citation.doi].filter(Boolean).join(" · "))}</span><br /><button type="button" data-action="open-review-citation" data-citation-id="${escapeHtml(citation.citation_id)}">预览证据与来源</button></li>`).join("") : "<li>当前稿件没有可回跳引用。</li>";
  const legacyNotice = model.legacy ? `<div class="review-legacy-notice"><strong>这不是完整综述</strong><p>该任务由旧版流程生成，只包含检索摘录。请回到写作模式重新生成，新的流程会完成章节检索、跨论文比较、争议分析和开放问题。</p></div>` : "";
  const fulltextEvidence = ["fulltext", "task_acquired_fulltext"].includes(model.evidenceLevel);
  const evidenceNotice = model.evidenceNotice ? `<div class="review-evidence-notice"><strong>${fulltextEvidence ? "全文证据链" : "证据范围"}</strong><p>${escapeHtml(model.evidenceNotice)}</p></div>` : "";
  const requestContext = reviewRequestContextMarkup(run, model, presentation);
  const preview = `<div class="review-document-view review-preview-view is-active" data-review-view="preview"><article class="review-paper"><div class="review-paper-kicker">${escapeHtml(presentation.kicker)} <span>${model.legacy ? "旧版摘录" : model.needsReview ? "需要复核" : model.insufficientEvidence ? "证据不足" : model.verified ? "引用已核验" : "待人工复核"}</span></div><h1>${escapeHtml(model.title)}</h1><div class="review-paper-meta"><span><b>${model.documentCount}</b> 篇来源</span><span><b>${model.citationCount}</b> 个证据锚点</span><span>${escapeHtml(presentation.originLabel)}</span><span>${model.needsReview ? "相关性临界，建议补充检索或人工复核" : model.insufficientEvidence ? "证据不足，未生成科学结论" : model.verified ? "引用核验通过" : model.legacy ? "旧版摘录" : "建议人工复核"}</span></div>${requestContext}${legacyNotice}${evidenceNotice}<section id="review-abstract"><h2>摘要</h2><p class="review-lead">${reviewCitedTextMarkup(model.abstract)}</p></section>${sections}${comparison}${controversies}${openQuestions}${discovery}<section id="review-limitations"><h2>证据边界</h2><div class="review-limitations">${limitations}</div></section><section id="review-references"><h2>参考文献</h2><ol class="review-reference-list">${references}</ol></section></article></div>`;
  const source = `<div class="review-document-view review-source-view" data-review-view="source"><pre><code>${escapeHtml(model.markdown)}</code></pre></div>`;
  target.innerHTML = `${toolbar}<div class="review-document-body">${preview}${source}<aside class="review-evidence-drawer" id="reviewEvidenceDrawer" aria-live="polite"></aside></div>`;
  bindReviewCitationInteractions(model, target);
}

function researchIdeaCardMarkup(payload) {
  const candidate = payload.candidate || {};
  const bottleneck = payload.bottleneck || {};
  const coherence = payload.coherence_audit || {};
  const falsifiability = payload.falsifiability_audit || {};
  const implementability = payload.implementability_audit || {};
  const nextGate = payload.required_next_gate || {};
  const gates = payload.quality_gates || {};
  const gateLabels = [
    ["grounded_bottleneck", "证据瓶颈"],
    ["coherence", "机制连贯"],
    ["falsifiability", "可证伪"],
    ["implementability", "可实现"],
    ["novelty", "独立查新"],
  ];
  const gateStrip = gateLabels.map(([key, label]) => {
    const pending = key === "novelty" && !gates[key];
    const passed = Boolean(gates[key]);
    return `<span class="idea-gate ${passed ? "is-passed" : pending ? "is-pending" : "is-blocked"}"><i>${passed ? "✓" : pending ? "→" : "!"}</i><b>${escapeHtml(label)}</b><em>${passed ? "通过" : pending ? "待审" : "未通过"}</em></span>`;
  }).join("");
  const citedClaims = (items = []) => items.map((item) => {
    const citations = (item.citation_ids || []).map(citationMarkerMarkup).join("");
    return `<li><p>${escapeHtml(item.text || "")}</p><span>${citations}</span></li>`;
  }).join("");
  const mechanismSteps = (candidate.mechanism_steps || []).map((step, index) => `<li><span>${String(index + 1).padStart(2, "0")}</span><div><strong>${escapeHtml(step.action || "方法步骤")}</strong><p><b>输入</b>${escapeHtml(step.input || "—")}</p><p><b>输出</b>${escapeHtml(step.output || "—")}</p></div></li>`).join("");
  const implementationByStep = new Map((implementability.enriched_steps || []).map((item) => [String(item.step_id || ""), item]));
  const implementationSteps = (candidate.mechanism_steps || []).map((step) => {
    const spec = implementationByStep.get(String(step.id || "")) || {};
    const dependencies = (spec.dependencies || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("");
    return `<article><header><b>${escapeHtml(step.id || "STEP")}</b><strong>${escapeHtml(spec.implementation || step.action || "尚未形成实现规格")}</strong></header>${dependencies ? `<div class="idea-dependencies">${dependencies}</div>` : ""}<p><em>验收</em>${escapeHtml(spec.acceptance_check || "待补充")}</p></article>`;
  }).join("");
  const openHoles = (implementability.underspecified_points || []).filter((item) => item.severity === "open");
  const holesMarkup = openHoles.map((item) => `<li><b>${escapeHtml(item.step_id || "未定位")}</b><span>${escapeHtml(item.hole || "未说明")}</span>${item.fill ? `<small>${escapeHtml(item.fill)}</small>` : ""}</li>`).join("");
  const dryRun = coherence.dry_run || {};
  const trace = (dryRun.step_trace || []).map((item, index) => `<li><b>${String(index + 1).padStart(2, "0")}</b><span>${escapeHtml(typeof item === "string" ? item : item.result || item.output || item.action || JSON.stringify(item))}</span></li>`).join("");
  const falsification = candidate.falsification || {};
  const falsificationFields = [
    ["关键实验", falsification.experiment],
    ["结果指标", falsification.outcome_metric],
    ["预期方向", falsification.expected_direction],
    ["承重变量", falsification.load_bearing_variable],
    ["负对照", falsification.negative_control],
    ["终止条件", falsification.failure_condition],
  ].map(([label, value]) => `<div><span>${label}</span><p>${escapeHtml(value || "未定义")}</p></div>`).join("");
  const evidence = [
    ...(bottleneck.evidence_claims || []),
    ...(candidate.evidence_basis || []),
  ];
  const limitations = (payload.limitations || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const ideaStatus = payload.status === "ready_for_novelty_check" ? "is-ready" : "is-revision";
  return `<article class="research-idea-card ${ideaStatus}"><header class="idea-hero"><div><span>ONE CANDIDATE · EVIDENCE FIRST</span><h3>${escapeHtml(candidate.title || payload.title || "研究候选")}</h3><p>${escapeHtml(candidate.hook || payload.reader_answer?.text || "")}</p></div><aside><small>当前状态</small><b>${payload.status === "ready_for_novelty_check" ? "待查新" : "需修订"}</b><em>不宣称已创新</em></aside></header><div class="idea-gate-strip" aria-label="研究构思质量门">${gateStrip}</div><section class="idea-thesis"><span>核心机制</span><p>${escapeHtml(candidate.core_mechanism || "尚未形成可审查的核心机制。")}</p></section><div class="idea-two-column"><section class="idea-panel idea-bottleneck"><header><span>01</span><div><small>LOAD-BEARING BOTTLENECK</small><h4>承重瓶颈</h4></div></header><p class="idea-lead">${escapeHtml(bottleneck.bottleneck_statement || bottleneck.reason || "证据不足，尚不能诊断瓶颈。")}</p>${bottleneck.why_load_bearing ? `<p>${escapeHtml(bottleneck.why_load_bearing)}</p>` : ""}<ul class="idea-evidence-claims">${citedClaims(bottleneck.evidence_claims || [])}</ul></section><section class="idea-panel idea-mechanism"><header><span>02</span><div><small>MECHANISM PATH</small><h4>机制路径</h4></div></header><ol>${mechanismSteps || "<li>尚无可执行步骤。</li>"}</ol></section></div><section class="idea-panel idea-evidence"><header><span>03</span><div><small>FULL-TEXT BASIS</small><h4>全文证据依据</h4></div></header><ul class="idea-evidence-claims">${citedClaims(evidence) || "<li><p>当前没有形成可回跳的全文证据链。</p></li>"}</ul></section><section class="idea-panel idea-falsification"><header><span>04</span><div><small>KILL THE IDEA EARLY</small><h4>可证伪设计</h4></div></header><div class="idea-falsification-grid">${falsificationFields}</div></section><div class="idea-two-column idea-audits"><section class="idea-panel"><header><span>05</span><div><small>MODEL-SIMULATED · NOT EXECUTED</small><h4>连贯性演算</h4></div></header><div class="idea-simulation-label">${uiIcon("brain")} 模型结构化模拟，不是真实代码执行</div><p><b>例子：</b>${escapeHtml(dryRun.example || "未提供")}</p><ol class="idea-trace">${trace || "<li><span>没有有效演算轨迹。</span></li>"}</ol>${dryRun.result ? `<p><b>演算结果：</b>${escapeHtml(dryRun.result)}</p>` : ""}</section><section class="idea-panel"><header><span>06</span><div><small>BUILD SPECIFICATION</small><h4>可实现性规格</h4></div></header><div class="idea-implementation">${implementationSteps || "<p>尚未形成实现规格。</p>"}</div>${holesMarkup ? `<div class="idea-open-holes"><strong>仍需补齐</strong><ul>${holesMarkup}</ul></div>` : ""}</section></div><section class="idea-next-gate"><div><span>${uiIcon("shield-check")}</span><div><small>REQUIRED NEXT GATE</small><h4>最后再做独立证据查新</h4><p>构思通过内部质量门不等于新颖。下一步会用同一问题与核心机制检索潜在重合工作。</p></div></div><button type="button" data-action="prepare-idea-novelty" data-novelty-problem="${escapeHtml(nextGate.problem || payload.direction || "")}" data-novelty-claim="${escapeHtml(nextGate.novelty || candidate.core_mechanism || "")}">带入证据查新 ${uiIcon("arrow-right")}</button></section>${limitations ? `<footer class="idea-limitations"><strong>结论边界</strong><ul>${limitations}</ul></footer>` : ""}</article>`;
}

function noveltyAssessmentMarkup(payload) {
  const verdict = payload.verdict || {};
  const adequacy = payload.evidence_adequacy || {};
  const coverage = payload.coverage || {};
  const axisLabels = {
    problem_framing: "问题设定",
    core_mechanism: "核心机制",
    key_insight: "关键洞见",
    application_domain: "应用领域",
  };
  const statusLabels = { match: "重合", partial: "部分重合", differ: "差异", unknown: "证据未知" };
  const priorWorks = (payload.prior_works || []).map((work, index) => {
    const axes = Object.entries(axisLabels).map(([key, label]) => {
      const status = work.axes?.[key] || "unknown";
      return `<span class="novelty-axis ${escapeHtml(status)}"><b>${escapeHtml(label)}</b><em>${escapeHtml(statusLabels[status] || status)}</em></span>`;
    }).join("");
    const citations = (work.citation_ids || []).map(citationMarkerMarkup).join("");
    return `<article class="novelty-prior-work"><header><span>${String(index + 1).padStart(2, "0")}</span><div><h4>${escapeHtml(work.paper || "未命名文献")}</h4><small>${escapeHtml(`${Number(work.matching_axis_count || 0)} / 4 轴存在重合风险`)}</small></div>${citations}</header><div class="novelty-axis-row">${axes}</div>${work.summary ? `<p>${escapeHtml(work.summary)}</p>` : ""}</article>`;
  }).join("");
  const limitations = (payload.limitations || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const providerText = (coverage.providers_succeeded || []).join(" · ") || "未记录";
  const level = verdict.level == null ? "—" : String(verdict.level);
  const delta = payload.delta_statement
    ? `<section class="novelty-delta"><span>可辩护差异</span><p>${escapeHtml(payload.delta_statement)}</p></section>`
    : "";
  const unresolved = verdict.code === "unresolved";
  return `<article class="novelty-report ${unresolved ? "is-unresolved" : ""}"><header class="novelty-verdict"><div><span>PRIOR-ART OVERLAP · FULL-TEXT GROUNDED</span><h3>${escapeHtml(verdict.label || "查新审查")}</h3><p>${escapeHtml(payload.reader_answer?.text || "")}</p></div><div class="novelty-level"><b>${escapeHtml(level)}</b><span>${verdict.level == null ? "未定级" : "风险级别"}</span></div></header><div class="novelty-audit-strip"><span><b>${escapeHtml(String(coverage.query_count || 0))}</b> 检索式</span><span><b>${escapeHtml(String(coverage.deduplicated_count || 0))}</b> 候选工作</span><span><b>${escapeHtml(String(adequacy.document_count || 0))}</b> 全文文献</span><span><b>${escapeHtml(String(adequacy.citation_count || 0))}</b> 证据片段</span></div><p class="novelty-source-line">检索来源：${escapeHtml(providerText)}</p>${delta}<section class="novelty-prior-list"><header><span>逐论文四轴审查</span><small>差异结论无法由引文证明时必须标记“证据未知”</small></header>${priorWorks || '<div class="novelty-empty">当前没有形成可引用的逐论文重合判断。</div>'}</section><section class="novelty-caveat"><strong>${uiIcon("shield-check")} 结论边界</strong><ul>${limitations || "<li>未检出强重合不等于证明新颖。</li>"}</ul></section></article>`;
}

function localPathLeaf(path) {
  return String(path || "").split(/[\\/]/).filter(Boolean).pop() || String(path || "");
}

function localPathParent(path) {
  const value = String(path || "").trim().replace(/[\\/]+$/, "");
  const separator = Math.max(value.lastIndexOf("\\"), value.lastIndexOf("/"));
  return separator > 2 ? value.slice(0, separator) : "";
}

function localFileLinkMarkup(path, label = "", { folder = false, compact = false, inline = false } = {}) {
  const value = String(path || "").trim();
  if (!value) return "";
  const name = label || localPathLeaf(value);
  const kind = localResourceKind(value, { folder });
  const classes = ["local-artifact-link", compact ? "is-compact" : "", inline ? "is-inline" : ""].filter(Boolean).join(" ");
  return `<span class="${classes}" title="${escapeHtml(value)}"><button type="button" class="local-artifact-open" data-action="open-local-path" data-local-path="${escapeHtml(value)}" aria-label="${kind === "folder" ? "打开文件夹" : "打开文件"} ${escapeHtml(name)}"><span class="local-artifact-icon is-${escapeHtml(kind)}">${uiIcon(localResourceIcon(kind))}</span><span>${escapeHtml(name)}</span></button>${kind === "folder" || inline ? "" : `<button type="button" class="local-artifact-reveal" data-action="reveal-local-path" data-local-path="${escapeHtml(value)}" aria-label="在文件夹中显示 ${escapeHtml(name)}" title="在文件夹中显示">${uiIcon("folder-open")}</button>`}</span>`;
}

function runArtifactDownloadLinkMarkup(runId, path, { folder = false, label = "" } = {}) {
  const value = String(path || "").trim();
  if (!runId || !value) return "";
  const name = label || localPathLeaf(value);
  const downloadName = `${localPathLeaf(value) || "ScanSci-演示项目"}${folder ? ".zip" : ""}`;
  const href = `/api/runs/${encodeURIComponent(String(runId))}/download`;
  return `<span class="local-artifact-link is-inline"><a class="local-artifact-open" href="${escapeHtml(href)}" download="${escapeHtml(downloadName)}" aria-label="下载 ${escapeHtml(name)}"><span class="local-artifact-icon is-archive">${uiIcon("download")}</span><span>${escapeHtml(name)}</span></a></span>`;
}

async function openLocalArtifact(path, { reveal = false } = {}) {
  const method = reveal ? "reveal_local_path" : "open_local_path";
  const api = window.pywebview?.api;
  if (!api || typeof api[method] !== "function") {
    throw new Error("请在 ScanSci 桌面应用中打开本地文件");
  }
  const result = await api[method](String(path || ""));
  if (!result?.ok) throw new Error(result?.message || "本地文件无法打开");
  return result;
}

function downloadedPaperArtifactMarkup(artifact) {
  const payload = artifact.payload || {};
  const papers = new Map((payload.papers || []).map((paper) => [String(paper.identifier || paper.doi || paper.arxiv_id || "").toLowerCase(), paper]));
  const items = Array.isArray(payload.items) && payload.items.length
    ? payload.items
    : [{ identifier: payload.identifier || artifact.title, status: payload.ok === false ? "failed" : "completed", files: payload.files || [] }];
  const completed = Number(payload.completed ?? items.filter((item) => item.status === "completed").length);
  const failed = Number(payload.failed ?? items.filter((item) => item.status === "failed").length);
  const pending = Math.max(0, items.length - completed - failed);
  const rows = items.map((item, index) => {
    const paper = papers.get(String(item.identifier || "").toLowerCase()) || {};
    const title = paper.title || item.identifier || `论文 ${index + 1}`;
    const status = item.status === "completed" ? "已保存" : item.status === "failed" ? "未获取" : item.status || "等待中";
    const files = Array.isArray(item.files) ? item.files.map((file) => localFileLinkMarkup(file, "", { compact: true })).join("") : "";
    const meta = [item.identifier, paper.year, paper.cited_by_count ? `${paper.cited_by_count} 次引用` : ""].filter(Boolean).join(" · ");
    return `<div class="downloaded-paper-row ${escapeHtml(item.status || "pending")}"><span>${item.status === "completed" ? "✓" : item.status === "failed" ? "!" : index + 1}</span><div><strong>${escapeHtml(title)}</strong>${meta ? `<small>${escapeHtml(meta)}</small>` : ""}${files ? `<div class="downloaded-paper-files">${files}</div>` : ""}${item.error ? `<em>${escapeHtml(item.error)}</em>` : ""}</div><b>${escapeHtml(status)}</b></div>`;
  }).join("");
  return `<article class="downloaded-paper-artifact"><header><div><span>FULL TEXT</span><h3>${escapeHtml(payload.message || artifact.summary || "文献下载已完成")}</h3></div><div class="downloaded-paper-metrics"><span><b>${completed}</b> 已保存</span><span><b>${failed}</b> 未获取</span>${pending ? `<span><b>${pending}</b> 待处理</span>` : ""}</div></header>${payload.output_dir ? `<div class="artifact-folder-link">${localFileLinkMarkup(payload.output_dir, localPathLeaf(payload.output_dir), { folder: true, inline: true })}</div>` : ""}<div class="downloaded-paper-list">${rows}</div></article>`;
}

function partialDownloadArtifactMarkup(run) {
  if (!String(run.workflow_type || "").startsWith("paper_")) return "";
  const stage = (run.stages || []).find((item) => item.key === "execute") || (run.stages || []).find((item) => item.kind === "tool");
  const output = stage?.output || {};
  const items = Array.isArray(output.items) ? output.items : [];
  if (!items.length) return "";
  const payload = {
    ...output,
    items,
    papers: Array.isArray(output.papers) ? output.papers : [],
    output_dir: output.output_dir || run.input?.output_dir || "",
    message: run.status === "paused" ? "任务中断前已保存的下载结果" : "下载进行中的结果",
  };
  const artifact = { title: run.title, summary: payload.message, payload };
  const note = run.status === "paused"
    ? `<p class="run-partial-result-note">这些是应用重启前已经落盘的结果；点击“继续”会从未完成阶段接着下载。</p>`
    : `<p class="run-partial-result-note">结果会边下载边保存，任务完成后将转换为最终交付记录。</p>`;
  return `${downloadedPaperArtifactMarkup(artifact)}${note}`;
}

function academicSearchArtifactMarkup(payload) {
  const plan = payload.search_plan || {};
  const gate = payload.quality_gate || {};
  const items = Array.isArray(payload.items) ? payload.items : [];
  const accepted = Number(gate.accepted_count ?? payload.count ?? items.length);
  const rejected = Number(gate.rejected_count || 0);
  const insufficient = ["insufficient", "no_candidates"].includes(String(gate.status || ""));
  const variants = Array.isArray(payload.query_variants) && payload.query_variants.length
    ? payload.query_variants
    : (Array.isArray(plan.query_variants) ? plan.query_variants : []);
  const providers = Array.isArray(payload.providers_requested) && payload.providers_requested.length
    ? payload.providers_requested
    : (Array.isArray(plan.providers) ? plan.providers : []);
  const succeededProviders = Array.isArray(payload.providers_succeeded) ? payload.providers_succeeded : [];
  const providerCounts = payload.provider_counts && typeof payload.provider_counts === "object" ? payload.provider_counts : {};
  const providerErrors = payload.provider_errors && typeof payload.provider_errors === "object" ? payload.provider_errors : {};
  const queryChips = variants.slice(0, 3).map((item) => `<code>${escapeHtml(item)}</code>`).join("");
  const providerText = providers.map((item) => academicProviderLabels[String(item)] || String(item).replace(/-/g, " ")).join(" · ") || "暂无可用来源";
  const sourceCoverage = `${succeededProviders.length} / ${providers.length || succeededProviders.length} 个来源可用`;
  const sourceRows = providers.map((provider) => {
    const name = String(provider);
    const label = academicProviderLabels[name] || name;
    const error = String(providerErrors[name] || "").trim();
    const count = Number(providerCounts[name] || 0);
    return `<li class="${error ? "is-failed" : succeededProviders.includes(name) ? "is-ready" : "is-empty"}"><span>${escapeHtml(label)}</span><small>${error ? "暂不可用" : `${count} 条记录`}</small>${error ? `<em title="${escapeHtml(error)}">${escapeHtml(error)}</em>` : ""}</li>`;
  }).join("");
  const qualityLine = insufficient
    ? `<div class="academic-search-gate is-warning"><strong>没有交付不相关的文献</strong><span>${escapeHtml(gate.reason || "请检查主题措辞，或补充英文同义词后重试。")}</span></div>`
    : `<div class="academic-search-gate"><strong>主题相关性已核验</strong><span>保留 ${escapeHtml(String(accepted))} 条${rejected ? `，筛除 ${escapeHtml(String(rejected))} 条泛匹配候选` : ""}</span></div>`;
  const rows = items.length ? items.map((paper, index) => {
    const authors = Array.isArray(paper.authors) ? paper.authors.slice(0, 3).join("、") : "";
    const details = [authors, paper.year, paper.venue].filter(Boolean).join(" · ");
    const source = Array.isArray(paper.sources) ? paper.sources.join(" · ") : paper.source || "";
    const destination = String(paper.oa_url || paper.url || (paper.doi ? `https://doi.org/${paper.doi}` : "")).trim();
    const title = escapeHtml(paper.title || `候选文献 ${index + 1}`);
    const action = destination
      ? `<button type="button" class="academic-paper-open" data-action="open-external" data-url="${escapeHtml(destination)}">查看来源</button>`
      : "";
    return `<li><span>${index + 1}</span><div><strong title="${title}">${title}</strong>${details ? `<small>${escapeHtml(details)}</small>` : ""}${paper.doi ? `<code>DOI ${escapeHtml(paper.doi)}</code>` : ""}</div><aside><small>${escapeHtml(source)}</small>${action}</aside></li>`;
  }).join("") : "";
  const diagnostics = `<details class="academic-search-details"><summary>检索详情</summary><div><p><b>主题</b>${escapeHtml(plan.normalized_topic || plan.topic || payload.query || "")}</p><p><b>检索式</b>${queryChips || "<span>未记录</span>"}</p><p><b>来源</b>${escapeHtml(providerText)}</p><p><b>候选</b>${escapeHtml(String(payload.candidate_count || 0))} 条原始记录，去重后 ${escapeHtml(String(payload.deduplicated_count || 0))} 条</p><section class="academic-source-coverage"><strong>来源覆盖</strong><span>${escapeHtml(sourceCoverage)}</span><ul>${sourceRows || "<li><span>暂无来源状态</span></li>"}</ul></section></div></details>`;
  const discoveryNotice = `<p class="academic-discovery-notice">这是公开元数据检索的候选集，未自动写入知识库；需要引用时，请先取得合法全文并完成证据索引。</p>`;
  return `<article class="academic-search-artifact ${insufficient ? "is-insufficient" : ""}"><header><div><span>ACADEMIC DISCOVERY</span><h3>${escapeHtml(plan.normalized_topic || plan.topic || payload.query || "学术检索")}</h3></div><b>${insufficient ? "需调整检索式" : `${accepted} 条高相关`}</b></header>${qualityLine}${rows ? `<ol class="academic-paper-list">${rows}</ol>` : `<div class="academic-search-empty"><strong>没有可交付的高相关候选</strong><p>已保留本次检索计划和来源诊断；请把主题写得更具体，或补充英文术语后重试。</p></div>`}${discoveryNotice}${diagnostics}</article>`;
}

function genericArtifactMarkup(artifact) {
  const payload = artifact.payload || {};
  const rows = payload.items || payload.results || payload.slides || payload.files || [];
  const rowMarkup = Array.isArray(rows) && rows.length ? `<div class="artifact-items">${rows.slice(0, 12).map((row, index) => {
    const item = typeof row === "string" ? { title: row } : row;
    const title = item.title || item.name || item.identifier || item.doi || item.path || `结果 ${index + 1}`;
    const meta = item.cas_partition || item.jcr_quartile || item.purpose || item.status || item.source || "";
    return `<div><span>${index + 1}</span><p><strong>${escapeHtml(title)}</strong>${meta ? `<small>${escapeHtml(meta)}</small>` : ""}</p></div>`;
  }).join("")}</div>` : "";
  const filePath = artifact.file_path ? `<div class="artifact-file-link">${localFileLinkMarkup(artifact.file_path, "", { inline: true })}</div>` : "";
  const external = payload.external_url ? `<button type="button" class="secondary-button" data-action="open-external" data-url="${escapeHtml(payload.external_url)}">在网页中继续 ↗</button>` : "";
  return `<article class="artifact-card"><span class="artifact-type">${escapeHtml(artifact.artifact_type)}</span><h3>${escapeHtml(artifact.title)}</h3><p>${escapeHtml(artifact.summary || payload.message || "研究产物已保存")}</p>${filePath}${rowMarkup}${external}</article>`;
}

function bindRunCitations(result, scope = byId("answerArea")) {
  if (result?.output_artifact || Array.isArray(result?.messages)) {
    bindCitationInteractions({ reader_answer: { citations: citationRecordsForRun(result) } }, scope);
    return;
  }
  bindCitationInteractions(result, scope);
}

async function openDirectConversation(conversationId, { record = true } = {}) {
  const id = String(conversationId || "").trim();
  if (!id) return;
  const requestGeneration = ++navigationRequestGeneration;
  let job = directChatJob(id);
  let conversation = null;
  try {
    conversation = await request(`/api/chat/history/${encodeURIComponent(id)}`);
  } catch (error) {
    // A just-started background run may be visible in the sidebar a few
    // milliseconds before its first durable history write completes.
    if (requestGeneration !== navigationRequestGeneration) return;
    if (!job) throw error;
    conversation = directJobSummary(job);
  }
  if (requestGeneration !== navigationRequestGeneration) return;
  if (!job) job = restoreDirectChatJob(conversation);
  state.activeTaskId = "";
  state.reviewDocument = null;
  state.reviewDocumentOpen = false;
  directMessageEdit = null;
  state.directConversationId = id;
  state.directMessages = job?.messages || ensureDirectMessageIds(
    Array.isArray(conversation.messages) ? conversation.messages : [],
    id,
  );
  state.sessionId = job?.sessionId || conversation.session_id || null;
  state.lastRunRenderKey = "";
  window.localStorage.removeItem("scansci.active.task");
  window.localStorage.setItem("scansci.active.direct", id);
  const latestAssistant = [...state.directMessages].reverse().find((message) => message.role === "assistant");
  const mode = String(latestAssistant?.mode || "general");
  applyContextPanelPreset(mode === "knowledge" ? "knowledge" : "none");
  byId("conversationTitle").textContent = compact(job?.title || conversation.title || directConversationTitle(state.directMessages), 80);
  setView("conversation", { record });
  renderModelSelectors();
  syncActiveDirectChatState();
  renderDirectConversation({ forceFollow: false });
  if (job?.sessionStats) updateSessionStats(job.sessionStats);
  else void restoreSessionStats();
  renderTasks();
}

async function openTask(id, { record = true } = {}) {
  const requestGeneration = ++navigationRequestGeneration;
  try {
    const run = await request(`/api/runs/${encodeURIComponent(id)}`);
    if (requestGeneration !== navigationRequestGeneration) return;
    // Opening history is read-only. Resume and recovery stay behind their
    // explicit controls so application launch cannot restart model work.
    const displayRun = run;
    upsertRun(displayRun);
    state.activeTaskId = displayRun.run_id;
    state.directConversationId = "";
    directMessageEdit = null;
    syncActiveDirectChatState();
    state.reviewDocumentOpen = false;
    window.localStorage.setItem("scansci.active.task", displayRun.run_id);
    window.localStorage.removeItem("scansci.active.direct");
    state.sessionId = `research-run-${displayRun.run_id}`;
    window.localStorage.setItem("scansci.active.session", state.sessionId);
    void restoreSessionStats(estimateRunSessionStats(displayRun));
    if (displayRun.workflow_type === "pdf_to_ppt") {
      const composer = byId("chatQuestionInput");
      if (composer) composer.placeholder = "通用模式可继续讨论；幻灯片模式可选择模板后重新制作";
    }
    byId("conversationTitle").textContent = ["literature_review", "deep_research"].includes(run.workflow_type) ? researchDocumentPresentation(run).conversationTitle : compact(runDisplayTitle(run), 80);
    // A direct-chat render can leave the previous task's render key behind.
    // Invalidate it whenever a history item is opened so the fetched run,
    // including its durable message history, replaces the visible thread.
    state.lastRunRenderKey = "";
    setView("conversation", { record });
    renderModelSelectors();
    try {
      renderRun(displayRun);
    } catch (error) {
      // Keep a failed renderer from leaving the previous direct-chat thread
      // on screen.  The diagnostic is visible in the task surface and also
      // helps identify malformed historical payloads during recovery.
      byId("answerArea").innerHTML = `<div class="error-state">历史任务显示失败：${escapeHtml(error.message || error)}</div>`;
      console.error("Failed to render historical task", error);
    }
    if (!["completed", "failed", "cancelled", "paused"].includes(displayRun.status)) {
      watchRun(displayRun.run_id, (next) => {
        if (state.activeView === "conversation" && state.activeTaskId === next.run_id) renderRun(next);
      });
    }
  } catch (error) {
    if (requestGeneration === navigationRequestGeneration) toast(error.message, true);
  }
}

const modeDefinitions = {
  library: { overline: "本地研究空间", title: "知识库" },
  tools: { overline: "ScanSci Suite", title: "功能" },
  review: { overline: "Evidence Studio", title: "文献综述" },
  atlas: { overline: "Paper Atlas", title: "研究图谱" },
  ppt: { overline: "演示文稿", title: "PPT" },
  download: { overline: "scansci-pdf · open access", title: "论文获取" },
  journal: { overline: "Journal Scout", title: "期刊查询" },
  verify: { overline: "Citation Lab", title: "引文核查" },
};

function renderMode() {
  const definition = modeDefinitions[state.activeMode] || modeDefinitions.tools;
  byId("modeOverline").textContent = definition.overline;
  byId("modeTitle").textContent = definition.title;
  document.querySelectorAll("[data-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.mode === state.activeMode));
  const target = byId("modeContent");
  target.classList.toggle("is-library-mode", state.activeMode === "library");
  document.querySelector(".mode-view")?.classList.toggle("is-library-mode", state.activeMode === "library");
  if (state.activeMode === "library") {
    target.innerHTML = renderImaLibraryMode();
    syncKnowledgeLibraryKindLabel();
    syncKnowledgeTreeControl();
  }
  else if (state.activeMode === "review") target.innerHTML = renderReviewMode();
  else if (state.activeMode === "journal") target.innerHTML = renderJournalMode();
  else if (state.activeMode === "verify") target.innerHTML = renderVerifyMode();
  else if (state.activeMode === "atlas") target.innerHTML = renderAtlasMode();
  else if (state.activeMode === "download") target.innerHTML = renderDownloadMode();
  else if (state.activeMode === "ppt") target.innerHTML = renderPptMode();
  else target.innerHTML = renderToolsMode();
}

function modeIntro(text, meta = "") {
  return `<div class="mode-intro"><p>${escapeHtml(text)}</p>${meta ? `<span>${escapeHtml(meta)}</span>` : ""}</div>`;
}

function syncKnowledgeLibraryKindLabel() {
  if (state.activeMode !== "library") return;
  const label = document.querySelector(".ima-library-heading > div > span");
  if (label && knowledgeSourceKind(state.notebook) === "notion") label.textContent = "Notion";
}

function syncKnowledgeTreeControl() {
  if (state.activeMode !== "library") return;
  const toolbar = document.querySelector(".ima-library-toolbar");
  if (!toolbar) return;
  let button = toolbar.querySelector('[data-action="toggle-knowledge-folders"]');
  if (!button) {
    toolbar.insertAdjacentHTML(
      "beforeend",
      '<button type="button" class="ima-tree-toggle" data-action="toggle-knowledge-folders"></button>',
    );
    button = toolbar.querySelector('[data-action="toggle-knowledge-folders"]');
  }
  const expanded = state.knowledgeTreeExpanded !== false;
  button.innerHTML = `${uiIcon("chevron-down")}${expanded ? "全部收起" : "全部展开"}`;
  button.setAttribute("aria-label", expanded ? "全部收起文件夹" : "全部展开文件夹");
  button.title = expanded ? "收起所有文件夹" : "展开所有文件夹";
}

function libraryKindCopy(kind) {
  if (kind === "translation") return { title: "翻译库", detail: "本地保存的译文 PDF", icon: "book" };
  if (kind === "zotero") return { title: "Zotero", detail: "本机文献数据库", icon: "library" };
  if (kind === "obsidian") return { title: "Obsidian", detail: "Vault 或笔记文件夹", icon: "book" };
  if (kind === "notion") return { title: "Notion", detail: "页面与数据库同步", icon: "book" };
  if (kind === "empty") return { title: "自建知识库", detail: "逐步添加本地文件", icon: "file-plus" };
  if (kind === "files") return { title: "本地文件", detail: "多个独立文献文件", icon: "file-plus" };
  return { title: "本地文件夹", detail: "绑定原位置并建立索引", icon: "folder-open" };
}

function pathLeaf(path) {
  const raw = String(path || "").trim();
  if (!raw || raw === ".") return "当前资料目录";
  const parts = raw.split(/[\\/]+/).filter(Boolean);
  return parts.at(-1) || "未命名知识库";
}

function libraryBookMarkup(source, index, { external = false } = {}) {
  const details = source.doi || (source.publication_year ? `${source.publication_year} · 文献` : external ? "Zotero PDF · 等待解析" : "已建立证据索引");
  const tag = external ? "span" : "button";
  const action = external ? "" : ` type="button" data-action="open-source-reader" data-doc-id="${escapeHtml(source.doc_id)}"`;
  return `<${tag} class="knowledge-book book-tone-${index % 6}${external ? " is-external" : ""}"${action}><span class="knowledge-book-head"><small>${external ? "ZOTERO" : `文献 ${String(index + 1).padStart(2, "0")}`}</small>${uiIcon(external ? "book" : "file-plus")}</span><strong>${escapeHtml(compact(source.title || source.doc_id || "未命名文献", 68))}</strong><em>${escapeHtml(compact(String(details), 52))}</em></${tag}>`;
}

function renderLibraryMode() {
  const notebooks = state.workspace?.notebooks || [];
  const active = state.notebook;
  const zoteroLibraries = notebooks.filter((item) => String(item.metadata?.library_kind || "") === "zotero");
  const obsidianLibraries = notebooks.filter((item) => String(item.metadata?.library_kind || "") === "obsidian");
  const notionLibraries = notebooks.filter((item) => String(item.metadata?.library_kind || "") === "notion");
  const localLibraries = notebooks.filter((item) => !["translation", "zotero", "obsidian", "notion"].includes(String(item.metadata?.library_kind || "folder")));
  const totalSources = notebooks.reduce((sum, item) => sum + Number(item.counts?.sources || 0), 0);
  const sourceRows = notebooks.map((notebook) => {
    const kindKey = String(notebook.metadata?.library_kind || "folder");
    const kind = libraryKindCopy(kindKey);
    const selected = notebook.notebook_id === active?.notebook_id;
    const count = Number(notebook.counts?.sources || 0);
    const zotero = notebook.metadata?.zotero || {};
    const itemCount = kindKey === "zotero" ? Number(zotero.item_count || zotero.pdf_count || 0) : count;
    const path = kindKey === "zotero"
      ? "通过 Zotero 本机接口连接"
      : kindKey === "empty"
        ? "等待添加本地文件"
        : String(notebook.root_path || "等待添加文件");
    const addAction = kindKey === "zotero"
      ? `<button type="button" class="knowledge-source-quiet" data-action="choose-zotero-library" data-notebook-id="${escapeHtml(notebook.notebook_id)}">刷新</button>`
      : `<button type="button" class="knowledge-source-quiet" data-action="choose-library-files" data-notebook-id="${escapeHtml(notebook.notebook_id)}">添加文件</button>`;
    const dropAttributes = kindKey === "zotero" ? "" : ` data-library-dropzone data-notebook-id="${escapeHtml(notebook.notebook_id)}"`;
    return `<article class="knowledge-source-row ${selected ? "is-selected" : ""}"${dropAttributes}><span class="knowledge-source-mark ${escapeHtml(kindKey)}">${uiIcon(kind.icon)}</span><div class="knowledge-source-copy"><span>${escapeHtml(kind.title)}${selected ? " · 本轮检索范围" : ""}</span><h3>${escapeHtml(notebook.title || pathLeaf(notebook.root_path))}</h3><p title="${escapeHtml(path)}">${escapeHtml(compact(path, 72))}</p></div><div class="knowledge-source-count"><strong>${itemCount}</strong><span>${kindKey === "zotero" ? "条文献记录" : "个文档"}</span></div><div class="knowledge-source-actions">${addAction}<button type="button" class="knowledge-source-select" data-action="select-notebook" data-notebook-id="${escapeHtml(notebook.notebook_id)}">${selected ? `${uiIcon("check")} 已选择` : "用于对话"}</button></div></article>`;
  }).join("");
}

function knowledgeSourceKind(notebook) {
  const kind = String(notebook?.metadata?.library_kind || "folder");
  if (kind === "translation") return "translation";
  if (kind === "zotero" || notebook?.metadata?.zotero) return "zotero";
  if (kind === "obsidian" || notebook?.metadata?.obsidian) return "obsidian";
  if (kind === "notion" || notebook?.metadata?.notion) return "notion";
  return "personal";
}

function knowledgeItemUsesPdfIcon(item, notebook) {
  const candidates = [item?.pdf_path, item?.path, item?.source_url, item?.title].filter(Boolean);
  const attachments = Array.isArray(item?.attachments) ? item.attachments : [];
  const explicitPdf = String(item?.type || "").toLowerCase() === "pdf"
    || candidates.some((value) => sourceSuffix(value) === ".pdf")
    || attachments.some((attachment) => {
      const contentType = String(attachment?.content_type || attachment?.contentType || "").toLowerCase();
      return contentType === "application/pdf" || sourceSuffix(attachment?.path || attachment?.name || "") === ".pdf";
    });
  // Zotero rows represent literature documents in this view. Some local
  // Zotero snapshots expose the PDF count but omit child-attachment paths
  // from their compact item sample, so the document icon remains stable.
  return explicitPdf || knowledgeSourceKind(notebook) === "zotero";
}

function knowledgeSourceItems(notebook) {
  const kind = knowledgeSourceKind(notebook);
  if (kind === "translation") return notebook?.translation_files || [];
  const indexed = (notebook?.sources || []).map((source) => ({
    ...source,
    id: source.source_id || source.doc_id || source.title,
    title: source.title || source.doc_id || "未命名文件",
    // A Notion page has a public source URL, but its managed Markdown path is
    // the thing that preserves the synced parent-chain.  Keep that path for
    // tree rendering while the preview still receives source_url separately.
    path: kind === "notion" ? source.html_path || source.source_url || "" : source.source_url || source.html_path || "",
    type: sourceTypeLabel(kind === "notion" ? source.html_path || source.title || "" : source.source_url || source.title || ""),
  }));
  if (indexed.length || kind !== "zotero") return indexed;
  const items = notebook?.metadata?.zotero?.items || [];
  return items.map((item, index) => ({
    ...item,
    id: item.key || `zotero-${index}`,
    title: item.title || "未命名文献",
    path: item.pdf_path || item.url || item.doi || "",
    type: item.pdf_path ? "PDF" : "Zotero",
  }));
}

function knowledgeItemSearchText(item, notebook) {
  const zoteroRecord = knowledgeSourceKind(notebook) === "zotero"
    ? zoteroMetadataItemForKnowledgeItem(item, notebook)
    : null;
  return [
    knowledgeFolderName(item, notebook),
    item.title,
    item.type,
    item.path,
    item.publication,
    item.creators,
    item.doi,
    item.date,
    ...zoteroTagValues(item),
    ...zoteroTagValues(zoteroRecord),
  ].filter(Boolean).join(" ").toLowerCase();
}

function knowledgeFolderName(item, notebook) {
  if (knowledgeSourceKind(notebook) === "zotero") {
    const collectionNames = new Map(
      (notebook?.metadata?.zotero?.collections || []).map((collection) => [
        String(collection.key),
        String(collection.name || collection.key),
      ]),
    );
    const collectionName = (item.collections || [])
      .map((key) => collectionNames.get(String(key)))
      .find(Boolean);
    return collectionName || "未分类";
  }
  const raw = String(item.path || item.source_url || "");
  const parts = raw.split(/[\\/]+/).filter(Boolean);
  if (parts.length > 1) return parts.at(-2);
  return pathLeaf(notebook?.root_path) || "全部文件";
}

function renderKnowledgeFileRow(item, notebook, items) {
  const active = String(item.id) === String(state.knowledgePreviewSourceId || items[0]?.id);
  const usesPdfIcon = knowledgeItemUsesPdfIcon(item, notebook);
  const searchText = knowledgeItemSearchText(item, notebook);
  const title = String(item.title || pathLeaf(item.path) || "未命名文件");
  return `<button type="button" class="ima-file-row ${active ? "is-active" : ""}" data-action="preview-knowledge-source" data-source-id="${escapeHtml(String(item.id))}" data-search-text="${escapeHtml(searchText)}" title="${escapeHtml(title)}" aria-label="预览：${escapeHtml(title)}"><span class="ima-file-icon ${usesPdfIcon ? "is-pdf" : ""}"><img src="${usesPdfIcon ? "/pdf-document.svg" : knowledgeLogoUrl(knowledgeSourceKind(notebook))}" alt="" /></span><span class="ima-file-copy"><strong>${escapeHtml(compact(title, 88))}</strong><small>${escapeHtml(item.type || sourceTypeLabel(item.path || item.title))}${item.publication_year ? ` · ${escapeHtml(String(item.publication_year))}` : ""}</small></span></button>`;
}

function notionTreeLabel(segment) {
  return String(segment || "Notion 页面")
    .replace(/\.(?:md|markdown)$/i, "")
    .replace(/--[a-f0-9]{10}$/i, "")
    .trim() || "Notion 页面";
}

function notionRelativeSegments(item, notebook) {
  const raw = String(item.html_path || item.path || "").replace(/\\/g, "/");
  const root = String(notebook?.root_path || "").replace(/\\/g, "/").replace(/\/+$/, "");
  const local = root && raw.toLowerCase().startsWith(`${root.toLowerCase()}/`)
    ? raw.slice(root.length + 1)
    : raw;
  const parts = local.split("/").filter(Boolean).filter((part) => part !== "manifest.json");
  return parts.length ? parts : [`${notionTreeLabel(item.title)}--${String(item.id || "page").slice(-10)}.md`];
}

function buildNotionTree(notebook, items) {
  const root = { label: "", item: null, children: new Map() };
  items.forEach((item) => {
    let node = root;
    notionRelativeSegments(item, notebook).forEach((part) => {
      const key = notionTreeLabel(part);
      if (!node.children.has(key)) node.children.set(key, { label: key, item: null, children: new Map() });
      node = node.children.get(key);
    });
    node.item = item;
  });
  return root;
}

function notionTreeCount(node) {
  return (node.item ? 1 : 0) + [...node.children.values()].reduce((total, child) => total + notionTreeCount(child), 0);
}

function renderNotionTreeNode(node, notebook, items, depth = 0) {
  const children = [...node.children.values()].sort((left, right) => left.label.localeCompare(right.label, "zh-CN"));
  if (!children.length) return node.item ? renderKnowledgeFileRow(node.item, notebook, items) : "";
  const count = notionTreeCount(node);
  const pageTrigger = node.item
    ? `<button type="button" class="ima-notion-page-trigger" data-action="preview-knowledge-source" data-source-id="${escapeHtml(String(node.item.id))}" title="打开 ${escapeHtml(node.label)}">${escapeHtml(node.label)}</button>`
    : `<strong>${escapeHtml(node.label)}</strong>`;
  return `<details class="ima-folder ima-notion-folder" data-notion-depth="${depth}"${state.knowledgeTreeExpanded !== false ? " open" : ""}><summary>${uiIcon("chevron-right")}<span>${uiIcon("folder-open")}</span>${pageTrigger}<small>${count}</small></summary><div>${children.map((child) => renderNotionTreeNode(child, notebook, items, depth + 1)).join("")}</div></details>`;
}

function renderNotionTree(notebook, items) {
  const tree = buildNotionTree(notebook, items);
  return [...tree.children.values()]
    .sort((left, right) => left.label.localeCompare(right.label, "zh-CN"))
    .map((node) => renderNotionTreeNode(node, notebook, items))
    .join("");
}

function renderZoteroConnectionGuide(notebook, issue) {
  const diagnostic = issue?.diagnostic || {};
  const selectedPath = issue?.dataDir
    || diagnostic.data_dir
    || notebook?.metadata?.zotero?.configured_data_dir
    || "";
  const databaseLabel = diagnostic.database_readable
    ? "数据目录已找到且可读"
    : diagnostic.installed
      ? "数据目录已找到，但数据库不可读"
      : "尚未找到可读的数据目录";
  const apiLabel = diagnostic.api_running ? "本机 API 已开启" : "本机 API 未开启（可选）";
  const databaseClass = diagnostic.database_readable ? "is-ready" : "is-warning";
  const apiClass = diagnostic.api_running ? "is-ready" : "is-neutral";
  const databaseDetail = diagnostic.database_error ? String(diagnostic.database_error) : "";
  return `<section class="zotero-connect-guide" role="alert" aria-live="polite">
    <header class="zotero-connect-guide-head">
      <span class="zotero-guide-mark">${uiIcon("triangle-alert")}</span>
      <div><span class="zotero-guide-eyebrow">ZOTERO 连接未完成</span><strong>只需要配置一个目录</strong><p>ScanSci 需要读取 Zotero 数据目录中的文献元数据和附件位置。</p></div>
    </header>
    <div class="zotero-guide-target"><span>要配置什么</span><code>包含 zotero.sqlite 的文件夹</code><small>通常是 Zotero 文件夹本身，不是 storage 子文件夹。</small></div>
    <ol class="zotero-guide-steps">
      <li>打开 Zotero：<b>设置 → 高级 → 文件和文件夹 → 显示数据目录</b>。</li>
      <li>点击“选择数据目录”，选中刚才打开的文件夹。</li>
      <li>如果仍然失败，再在同一页打开“允许本机其他应用与 Zotero 通信”，然后重新检测。</li>
    </ol>
    <div class="zotero-guide-checks"><span class="${databaseClass}"><i></i>${databaseLabel}</span><span class="${apiClass}"><i></i>${apiLabel}</span></div>
    ${selectedPath ? `<p class="zotero-guide-path" title="${escapeHtml(selectedPath)}">当前尝试：<code>${escapeHtml(selectedPath)}</code></p>` : ""}
    <div class="zotero-guide-actions"><button type="button" class="primary-button" data-action="choose-zotero-data-directory" data-notebook-id="${escapeHtml(notebook?.notebook_id || "")}">${uiIcon("folder-open")}选择数据目录</button><button type="button" class="secondary-button" data-action="retry-zotero-connection" data-notebook-id="${escapeHtml(notebook?.notebook_id || "")}">${uiIcon("refresh-cw")}重新检测</button></div>
    <details class="zotero-guide-error"><summary>查看刚才的错误</summary><p>${escapeHtml(issue?.message || "未返回具体错误")}</p>${databaseDetail ? `<small>数据库检测：${escapeHtml(databaseDetail)}</small>` : ""}</details>
  </section>`;
}

function renderKnowledgeTree(notebook, items) {
  const zoteroIssue = knowledgeSourceKind(notebook) === "zotero"
    && state.zoteroConnectionIssue
    && String(state.zoteroConnectionIssue.notebookId || "") === String(notebook?.notebook_id || "")
    ? renderZoteroConnectionGuide(notebook, state.zoteroConnectionIssue)
    : "";
  if (!items.length) {
    if (knowledgeSourceKind(notebook) === "zotero") {
      if (zoteroIssue) return zoteroIssue;
      const connected = Boolean(notebook?.metadata?.zotero?.connected);
      return `<div class="ima-library-empty"><strong>${connected ? "未找到可检索的 PDF 正文" : "尚未连接本机 Zotero"}</strong><span>${connected ? "请确认文献已附加可读取的 PDF，再重新读取。" : "连接后会自动读取文献与可访问的 PDF。"}</span><button type="button" data-action="choose-zotero-library" data-notebook-id="${escapeHtml(notebook?.notebook_id || "")}">${uiIcon(connected ? "refresh-cw" : "link")} ${connected ? "重新读取 Zotero" : "连接本机 Zotero"}</button></div>`;
    }
    const binding = notebook?.metadata?.local_binding || {};
    if (binding.state === "bound") {
      const status = state.knowledgeIndexStatuses[String(notebook?.notebook_id || "")] || {};
      const failed = String(binding.index_state || "") === "failed" || status.state === "failed";
      const progress = Math.max(0, Math.min(100, Math.round(Number(status.progress || 0) * 100)));
      const detail = failed
        ? String(status.error || binding.error || "索引未完成，原文件仍保持原位。")
        : `${progress ? `${progress}% · ` : ""}文件夹已绑定，正在后台建立可检索索引。`;
      const action = failed
        ? `<button type="button" data-action="retry-bound-library-import" data-notebook-id="${escapeHtml(notebook?.notebook_id || "")}">${uiIcon("refresh-cw")} 重试索引</button>`
        : "";
      return `<div class="ima-library-empty is-binding"><strong>${failed ? "文件夹已绑定，但索引未完成" : "文件夹已绑定"}</strong><span>${escapeHtml(detail)}</span>${action}</div>`;
    }
    return `<div class="ima-library-empty">此知识库尚未链接文件或文件夹</div>`;
  }
  if (knowledgeSourceKind(notebook) === "notion") return renderNotionTree(notebook, items);
  const groups = new Map();
  items.forEach((item) => {
    const folder = knowledgeFolderName(item, notebook);
    if (!groups.has(folder)) groups.set(folder, []);
    groups.get(folder).push(item);
  });
  return `${zoteroIssue}${[...groups.entries()].map(([folder, entries]) => `<details class="ima-folder"${state.knowledgeTreeExpanded !== false ? " open" : ""}><summary>${uiIcon("chevron-right")}<span>${uiIcon("folder-open")}</span><strong>${escapeHtml(folder)}</strong><small>${entries.length}</small></summary><div>${entries.map((item) => renderKnowledgeFileRow(item, notebook, items)).join("")}</div></details>`).join("")}`;
}

// Only personal libraries can be added manually; translation entries arrive from completed tasks.
function renderKnowledgeSourceGroup(title, kind, notebooks) {
  const connectionAction = kind === "zotero"
    ? "choose-zotero-library"
    : kind === "obsidian"
      ? "choose-obsidian-vault"
      : kind === "notion"
        ? "connect-notion"
        : "create-empty-library";
  const isPersonal = kind === "personal";
  const headerAction = isPersonal && notebooks.length
    ? `<button type="button" data-action="create-empty-library" aria-label="新建个人知识库" title="新建个人知识库">${uiIcon("plus")}</button>`
    : "";
  const entries = notebooks.map((notebook) => `<button type="button" class="ima-source-entry ${notebook.notebook_id === state.notebook?.notebook_id ? "is-active" : ""}" data-action="activate-library" data-notebook-id="${escapeHtml(notebook.notebook_id)}"><img src="${knowledgeLogoUrl(kind)}" alt="" /><span>${escapeHtml(notebook.title || pathLeaf(notebook.root_path))}</span></button>`).join("");
  const emptyConnection = !notebooks.length
    ? kind === "translation"
      ? `<div class="ima-source-entry is-empty is-placeholder" aria-label="翻译库暂无译文"><img src="${knowledgeLogoUrl(kind)}" alt="" /><span>暂无译文</span></div>`
      : `<button type="button" class="ima-source-entry is-empty" data-action="${connectionAction}" aria-label="${isPersonal ? "新建个人知识库" : `连接 ${escapeHtml(title)}`}"><img src="${knowledgeLogoUrl(kind)}" alt="" /><span>${isPersonal ? "新建个人知识库" : `连接 ${escapeHtml(title)}`}</span></button>`
    : "";
  return `<section class="ima-source-group"><header><span>${escapeHtml(title)}</span>${headerAction}</header>${entries || emptyConnection}</section>`;
}

function knowledgeIndexStatusMarkup(notebook) {
  if (knowledgeSourceKind(notebook) === "translation") return `<span class="ima-index-status is-empty" title="文件夹中的 PDF 已关联；全文检索索引尚未建立">仅文件关联</span>`;
  const notebookId = String(notebook?.notebook_id || "");
  const status = state.knowledgeIndexStatuses[notebookId] || {};
  const localAi = state.localAiStatuses[notebookId] || {};
  const localAiState = String(localAi.state || "");
  // Catalog-only Zotero records are not evidence. Count indexed sources only.
  const sourceCount = Number(notebook?.counts?.sources ?? notebook?.sources?.length ?? 0);
  const basicReady = Number(status.total ?? sourceCount) > 0;
  const binding = notebook?.metadata?.local_binding || {};
  const bindingState = String(binding.index_state || "");
  const fallbackState = bindingState === "failed"
    ? "failed"
    : bindingState === "queued"
      ? "importing"
      : basicReady
        ? "pending"
        : "empty";
  // The Host marks user-initiated semantic optimization as optional.
  const statusState = String(status.state === "optional" ? "pending" : status.state || fallbackState);
  const progress = Math.max(
    0,
    Math.min(100, Math.round(Number(localAiState === "preparing" ? localAi.progress : status.progress || 0) * 100)),
  );
  const labels = {
    ready: "语义检索已就绪",
    // Source text can be searched as soon as document evidence has been
    // materialized.  The later vector/reranker pass improves ranking, but it
    // must not make a populated Zotero shelf look empty or unusable.
    indexing: sourceCount ? `全文可检索 · 优化${progress ? ` ${progress}%` : ""}` : `正在建立索引${progress ? ` ${progress}%` : ""}`,
    importing: `正在建立索引${progress ? ` ${progress}%` : ""}`,
    installing: `正在安装检索组件${progress ? ` ${progress}%` : ""}`,
    degraded: basicReady ? "基础检索可用 · 可优化语义索引" : "部分可用",
    failed: basicReady && bindingState !== "failed" ? "基础检索可用 · 语义优化失败" : "同步失败",
    pending: basicReady ? "基础检索可用 · 可优化语义索引" : "等待同步",
    empty: "尚无内容",
  };
  const localLabel = localAiState === "preparing"
    ? (basicReady ? "基础检索可用 · 正在准备语义检索" : "正在准备本地 AI")
    : basicReady && (localAiState === "fallback" || localAiState === "error") && bindingState !== "failed"
      ? "基础检索可用 · 语义优化待重试"
      : "";
  const label = localLabel || labels[statusState] || "等待同步";
  const retryable = ["failed", "degraded", "pending"].includes(statusState);
  const localRetryable = ["fallback", "error"].includes(localAiState);
  const action = localRetryable ? "retry-local-ai" : retryable ? "retry-evidence-index" : "refresh-evidence-index";
  const visualState = localAiState === "preparing"
    ? "indexing"
    : localRetryable
      ? "degraded"
      : basicReady && statusState === "pending"
        ? "ready"
        : statusState;
  const optimizationHint = basicReady && ["pending", "degraded", "failed"].includes(statusState) && bindingState !== "failed"
    ? "基础全文检索已可用；点击可手动优化语义索引。"
    : "";
  const details = [optimizationHint, status.error, localAi.message, localAi.error].filter(Boolean).join(" · ");
  const title = details ? `检索索引：${label}。${String(details)}` : `检索索引：${label}`;
  return `<button type="button" class="ima-index-status is-${escapeHtml(visualState)}" data-knowledge-index-status data-action="${action}" data-notebook-id="${escapeHtml(notebookId)}" title="${escapeHtml(title)}" ${statusState === "empty" ? "disabled" : ""}><svg viewBox="0 0 12 12" aria-hidden="true"><circle class="track" cx="6" cy="6" r="4.5"></circle><circle class="value" cx="6" cy="6" r="4.5" pathLength="100" stroke-dasharray="${progress} 100"></circle></svg><span>${escapeHtml(label)}</span></button>`;
}

function syncKnowledgeIndexBadge(notebookId) {
  if (state.activeMode !== "library" || state.notebook?.notebook_id !== notebookId) return;
  const current = document.querySelector("[data-knowledge-index-status]");
  if (!current) return;
  const template = document.createElement("template");
  template.innerHTML = knowledgeIndexStatusMarkup(state.notebook).trim();
  current.replaceWith(template.content.firstElementChild);
}

function focusKnowledgeFileSearch() {
  if (!state.knowledgeSearchOpen) {
    state.knowledgeSearchOpen = true;
    renderMode();
  }
  window.setTimeout(() => {
    const search = document.querySelector("[data-knowledge-file-search]");
    if (!search) return;
    search.focus({ preventScroll: true });
    if (search.value) search.select();
  }, 0);
}

function closeKnowledgeFileSearch() {
  if (!state.knowledgeSearchOpen) return;
  state.knowledgeSearchOpen = false;
  state.knowledgeQuery = "";
  state.knowledgeVisibleLimit = 200;
  renderMode();
  window.setTimeout(() => document.querySelector('[data-action="focus-knowledge-file-search"]')?.focus({ preventScroll: true }), 0);
}

async function openTranslationDialog({ showLocation = false } = {}) {
  const dialog = byId("translationDialog");
  if (!dialog) return;
  const library = await request("/api/translations/library");
  const root = String(library.root_path || "");
  byId("translationParentPath").value = root.replace(/[\\/]翻译库$/, "") || state.workspace?.workspace_directory || "";
  updateTranslationLocation();
  byId("translationLocation").open = showLocation;
  dialog.querySelector(".translation-runtime").open = false;
  byId("translationStatus").replaceChildren();
  byId("translationStatus").hidden = true;
  const model = state.settings?.active_model || {};
  const provider = (state.settings?.providers || []).find((item) => item.id === model.provider_id) || {};
  const destination = provider.auth_mode === "local" || String(provider.id || "").startsWith("local-") ? "本机" : "远程服务";
  const ocr = state.settings?.document_processing?.ocr || {};
  byId("translationRouteStatus").textContent = `翻译模型：${model.model_id || "未选择"}${model.model_id ? `（${destination}）` : ""} · OCR：${ocr.enabled === false ? "已关闭" : ocr.provider || "Tesseract"}。扫描 PDF 的版面翻译当前需要 Tesseract。`;
  if (!dialog.open) dialog.showModal();
  hydrateIcons(dialog);
  if (translationRunId) refreshTranslationStatus({ resume: true }).catch((error) => toast(error.message, true));
}

function updateTranslationLocation() {
  const path = byId("translationParentPath")?.value.trim() || "";
  const label = byId("translationLocationPath");
  if (!label) return;
  const workspacePath = String(state.workspace?.workspace_directory || "").replace(/[\\/]+$/, "");
  const isWorkspace = path.replace(/[\\/]+$/, "").toLowerCase() === workspacePath.toLowerCase();
  label.textContent = isWorkspace || !path ? "当前工作区" : path.split(/[\\/]/).filter(Boolean).pop();
  label.title = path || "当前工作区";
}

function closeTranslationDialog() {
  byId("translationDialog")?.close();
}

async function pickTranslationFolder() {
  const picker = window.pywebview?.api?.choose_library_folder;
  if (typeof picker !== "function") {
    byId("translationParentPath")?.focus();
    return;
  }
  const path = String(await picker() || "").trim();
  if (path) {
    byId("translationParentPath").value = path;
    updateTranslationLocation();
  }
}

async function saveTranslationFolder(notify = true) {
  const parentPath = byId("translationParentPath")?.value.trim() || "";
  const result = await request("/api/translations/library", {
    method: "POST", body: JSON.stringify({ parent_path: parentPath }),
  });
  state.workspace = result.workspace;
  state.notebook = (state.workspace.notebooks || []).find((item) => item.notebook_id === state.notebook?.notebook_id)
    || (state.workspace.notebooks || []).find((item) => item.notebook_id === "translation-library")
    || state.notebook;
  if (state.activeMode === "library") renderMode();
  updateTranslationLocation();
  if (notify) {
    byId("translationLocation").open = false;
    toast("翻译库已关联");
  }
  return result;
}

async function submitTranslation() {
  const file = byId("translationFile")?.files?.[0];
  if (!file || !file.name.toLowerCase().endsWith(".pdf")) throw new Error("请选择 PDF 文件");
  if (!file.size || file.size > 50 * 1024 * 1024) throw new Error("PDF 文件须小于 50 MB");
  const button = byId("translationSubmit");
  button.disabled = true;
  const status = byId("translationStatus");
  status.hidden = false;
  status.textContent = "正在准备翻译…";
  try {
    await saveTranslationFolder(false);
    const started = await request("/api/translations", {
      method: "POST",
      body: JSON.stringify({
        filename: file.name,
        data_url: await fileToDataUrl(file, "无法读取 PDF"),
        target_language: byId("translationLanguage").value,
      }),
    });
    translationRunId = started.run_id;
    window.localStorage.setItem("scansci.translation.run-id", translationRunId);
    await refreshTranslationStatus();
  } finally {
    button.disabled = false;
  }
}

function renderTranslationStatus(run) {
  if (!byId("translationDialog")?.open || run.run_id !== translationRunId) return;
  const target = byId("translationStatus");
  target.hidden = false;
  const stage = (run.stages || []).find((item) => item.status === "running") || {};
  const progress = Math.round(Number(run.progress || 0) * 100);
  if (run.status === "completed") {
    target.innerHTML = `<strong>已保存到翻译库</strong><p>${escapeHtml(run.output_artifact?.file_path || "")}</p><button type="button" class="secondary-button" data-action="open-translation-result">打开译文 PDF</button> <button type="button" class="secondary-button" data-action="show-translation-library">查看翻译库</button>`;
  } else if (run.status === "cancelled") {
    target.innerHTML = "<strong>翻译已取消</strong><p>没有生成译文 PDF。</p>";
  } else if (run.status === "failed") {
    const error = String(run.error?.message || "翻译失败");
    const runnerStopped = /model runner has unexpectedly stopped|本地模型运行中断/i.test(error);
    const saveFailed = (run.stages || [])[0]?.status === "completed";
    const detail = runnerStopped ? "本地模型运行中断。请检查 Ollama，或换用较小模型后重试。" : error;
    target.innerHTML = `<strong>翻译未完成</strong><p>${escapeHtml(detail)}</p><p>${saveFailed ? "保存阶段失败，请检查目标文件夹。" : "没有生成译文 PDF。"}</p>${runnerStopped ? '<button type="button" class="secondary-button" data-action="configure-translation-model">管理模型</button>' : saveFailed ? '<button type="button" class="secondary-button" data-action="show-translation-library">查看翻译库</button>' : ""}`;
  } else {
    target.innerHTML = `<strong>正在翻译 · ${progress}%</strong><p>${escapeHtml(stage.summary || stage.title || "请稍候")}</p><button type="button" class="secondary-button" data-action="cancel-translation">取消任务</button>`;
  }
}

function watchTranslationRun(runId) {
  if (translationWatchers.has(runId)) return;
  translationWatchers.add(runId);
  void watchRun(runId, (run) => {
    renderTranslationStatus(run);
    if (run.status === "completed") refreshTranslationLibrary().catch(() => {});
  }).finally(() => translationWatchers.delete(runId));
}

async function refreshTranslationStatus({ resume = false } = {}) {
  if (!translationRunId) return;
  const run = await request(`/api/translations/${encodeURIComponent(translationRunId)}`);
  upsertRun(run);
  const finished = ["completed", "failed", "cancelled"].includes(run.status);
  if (!resume || !finished) renderTranslationStatus(run);
  if (finished) {
    if (run.status === "completed") refreshTranslationLibrary().catch(() => {});
  } else watchTranslationRun(translationRunId);
}

function renderImaLibraryMode() {
  const notebooks = state.workspace?.notebooks || [];
  const active = state.notebook || notebooks[0] || null;
  const kind = knowledgeSourceKind(active);
  const allItems = knowledgeSourceItems(active);
  const items = allItems;
  const query = String(state.knowledgeQuery || "").trim().toLowerCase();
  const matchingItems = query
    ? items.filter((item) => knowledgeItemSearchText(item, active).includes(query))
    : items;
  const visibleLimit = Math.max(200, Number(state.knowledgeVisibleLimit) || 200);
  const visibleItems = matchingItems.slice(0, visibleLimit);
  const remainingItems = Math.max(0, matchingItems.length - visibleItems.length);
  const totalItemCount = kind === "zotero"
    ? Number(active?.metadata?.zotero?.item_count || items.length)
    : items.length;
  const indexedSourceCount = Number(active?.counts?.sources || items.length || 0);
  // Zotero keeps bibliography records and locally readable full text
  // separately.  Show both, so users never mistake a metadata total for the
  // number of documents available to evidence search.
  const contentCountLabel = kind === "zotero"
    ? `全文 ${indexedSourceCount} / 文献 ${totalItemCount}`
    : `${totalItemCount}`;
  const previewId = state.knowledgePreviewSourceId || items[0]?.id || "";
  const preview = items.find((item) => String(item.id) === String(previewId)) || items[0] || null;
  const personal = notebooks.filter((notebook) => knowledgeSourceKind(notebook) === "personal");
  const translations = notebooks.filter((notebook) => knowledgeSourceKind(notebook) === "translation");
  const zotero = notebooks.filter((notebook) => knowledgeSourceKind(notebook) === "zotero");
  const obsidian = notebooks.filter((notebook) => knowledgeSourceKind(notebook) === "obsidian");
  const activeTitle = active?.title || "个人知识库";
  const activeReady = notebookHasSearchableContent(active);
  const linked = activeReady && (state.knowledgeScopeIds || []).includes(active.notebook_id);
  const zoteroConnected = Boolean(active?.metadata?.zotero?.connected);
  const zoteroActionLabel = zoteroConnected ? "重新读取 Zotero" : "连接本机 Zotero";
  const libraryScopeControl = activeReady
    ? `<button type="button" class="ima-scope-toggle ${linked ? "is-active" : ""}" data-action="toggle-active-library-scope">${linked ? `@${escapeHtml(activeTitle)}` : "用于对话"}</button>`
    : kind === "zotero"
      ? `<button type="button" class="ima-scope-toggle" data-action="choose-zotero-library" data-notebook-id="${escapeHtml(active?.notebook_id || "")}">${escapeHtml(zoteroActionLabel)}</button>`
      : `<span class="ima-scope-toggle is-disabled">${kind === "translation" ? "仅文件，未建立检索索引" : "导入后可用于对话"}</span>`;
  const linkedKinds = knowledgeLocalBindingKinds(active);
  const localLibraryActions = [
    `<button type="button" data-action="choose-library-files" data-notebook-id="${escapeHtml(active?.notebook_id || "")}">${uiIcon("link")}${linkedKinds.file ? "添加文件" : "链接文件"}</button>`,
    `<button type="button" data-action="choose-library-folder" data-notebook-id="${escapeHtml(active?.notebook_id || "")}">${uiIcon("folder-open")}${linkedKinds.folder ? "添加文件夹" : "链接文件夹"}</button>`,
  ].filter(Boolean).join("") || `<span class="ima-library-linked-status" title="文件与文件夹已连接">已全部连接</span>`;
  const libraryToolbarActions = kind === "translation"
    ? `<span class="ima-library-linked-status">${escapeHtml(active?.root_path || "译文由文献翻译任务保存到本地")}</span><button type="button" data-action="open-translation" data-open-location="true">${uiIcon("folder-open")}选择保存位置</button>`
    : kind === "zotero"
      ? `<button type="button" data-action="choose-zotero-library" data-notebook-id="${escapeHtml(active?.notebook_id || "")}">${uiIcon(zoteroConnected ? "refresh-cw" : "link")} ${escapeHtml(zoteroActionLabel)}</button>`
      : localLibraryActions;
  const previewPanel = preview && (activeReady || kind === "translation")
    ? `<article class="ima-preview-card"><span>${escapeHtml(preview.type || sourceTypeLabel(preview.path || preview.title))}</span><h3>${escapeHtml(preview.title)}</h3><p>${escapeHtml(preview.creators || preview.doi || preview.path || "本地链接文件")}</p>${preview.publication ? `<small>${escapeHtml(preview.publication)}</small>` : ""}${kind === "translation" ? `<button type="button" class="secondary-button" data-action="open-translation-file" data-file-id="${escapeHtml(preview.id)}" data-file-title="${escapeHtml(preview.title)}">打开译文 PDF</button>` : ""}</article>`
    : !activeReady
    ? `<div class="ima-preview-empty">${uiIcon("circle-alert")}<span>${kind === "translation" ? "完成文献翻译后，译文会保存在这里" : "导入资料后可基于证据问答"}</span></div>`
    : `<div class="ima-preview-empty">${uiIcon("message-circle")}<span>基于知识库问答</span></div>`;
  const suggestions = activeReady ? [
    `总结「${activeTitle}」中的核心主题`,
    `比较「${activeTitle}」中不同文献的观点`,
    `找出「${activeTitle}」尚未解决的问题`,
  ] : [];
  let resultLabel = query
    ? `${matchingItems.length} 个结果${remainingItems ? ` · 已显示 ${visibleItems.length}` : ""}`
    : remainingItems
      ? `已显示 ${visibleItems.length}`
      : "";
  const loadMore = remainingItems
    ? `<button type="button" class="ima-library-load-more" data-action="load-more-knowledge-items">加载更多（剩余 ${remainingItems}）</button>`
    : "";
  const searchOpen = Boolean(state.knowledgeSearchOpen);
  const topSearch = searchOpen
    ? `<div class="ima-library-appbar-search-field"><span>${uiIcon("search")}</span><input type="search" data-knowledge-file-search value="${escapeHtml(state.knowledgeQuery || "")}" placeholder="输入文件名、作者或路径" aria-label="搜索当前知识库" /><button type="button" data-action="close-knowledge-file-search" aria-label="收起搜索并清除筛选" title="收起搜索并清除筛选">${uiIcon("x")}</button></div>`
    : `<button type="button" class="ima-library-appbar-search" data-action="focus-knowledge-file-search" aria-label="搜索当前知识库" aria-expanded="false" title="搜索当前知识库（Ctrl+F）">${uiIcon("search")}</button>`;
  const returnToGuide = state.onboardingOpen && state.onboardingMode === "welcome"
    ? `<button type="button" class="ima-library-onboarding-back" data-action="return-first-run">${uiIcon("arrow-left")}返回引导</button>`
    : uiIcon("folder-open");
  return `<section class="ima-library-layout ${state.knowledgePreviewCollapsed ? "is-preview-collapsed" : ""}">
    <header class="ima-library-appbar">
      <div class="ima-library-appbar-context">${returnToGuide}<strong>${escapeHtml(activeTitle)}</strong></div>
      <div class="ima-library-appbar-actions">
        ${topSearch}
        <button type="button" class="ima-preview-toggle" data-action="toggle-knowledge-preview" aria-label="${state.knowledgePreviewCollapsed ? "展开详情栏" : "折叠详情栏"}" title="${state.knowledgePreviewCollapsed ? "展开详情栏" : "折叠详情栏"}">${uiIcon(state.knowledgePreviewCollapsed ? "panel-left" : "panel-right")}</button>
      </div>
    </header>
    <aside class="ima-library-sources">${renderKnowledgeSourceGroup("个人知识库", "personal", personal)}${renderKnowledgeSourceGroup("翻译库", "translation", translations)}${renderKnowledgeSourceGroup("Zotero", "zotero", zotero)}${renderKnowledgeSourceGroup("Obsidian", "obsidian", obsidian)}${renderKnowledgeSourceGroup("Notion", "notion", notebooks.filter((notebook) => knowledgeSourceKind(notebook) === "notion"))}</aside>
    <main class="ima-library-main"><header class="ima-library-title"><img src="${knowledgeLogoUrl(kind)}" alt="" /><div class="ima-library-heading"><h2>${escapeHtml(activeTitle)}</h2><div><span>${kind === "personal" ? "个人知识库" : kind === "translation" ? "翻译库" : kind === "zotero" ? "Zotero" : kind === "obsidian" ? "Obsidian" : "Notion"}</span>${knowledgeIndexStatusMarkup(active)}</div></div><div class="ima-library-title-actions">${libraryScopeControl}${kind === "personal" && active ? `<button type="button" class="ima-library-delete" data-action="delete-personal-library" data-notebook-id="${escapeHtml(active.notebook_id)}" title="移除个人知识库" aria-label="移除个人知识库 ${escapeHtml(activeTitle)}">${uiIcon("trash")}移除</button>` : ""}</div></header><div class="ima-library-toolbar">${libraryToolbarActions}</div><section class="ima-library-tree"><header><strong>内容 (${contentCountLabel})</strong><span data-knowledge-search-count>${escapeHtml(resultLabel)}</span></header>${renderKnowledgeTree(active, visibleItems)}${loadMore}</section></main>
    <aside class="ima-library-preview"><div class="ima-library-preview-content">${previewPanel}<section class="ima-library-suggestions"><span>你可以这样开始</span>${suggestions.map((prompt) => `<button type="button" data-action="use-knowledge-suggestion" data-prompt="${escapeHtml(prompt)}">${escapeHtml(prompt)}${uiIcon("chevron-right")}</button>`).join("")}</section></div></aside>
  </section>`;
}

function renderToolsMode() {
  // Paper retrieval is a primary sidebar destination, not a duplicate tool card.
  const tools = (state.capabilities?.tools || []).filter((tool) => tool.id !== "paper-download");
  const modeByTool = { "paper-download": "download", "journal-scout": "journal", "citation-lab": "verify", "paper-atlas": "atlas", "ppt-studio": "ppt", "evidence-trace": "review" };
  const cards = tools.map((tool) => `<button type="button" class="tool-card" data-action="open-mode" data-mode="${escapeHtml(modeByTool[tool.id] || "tools")}"><span class="tool-status ${escapeHtml(tool.status)}"></span><strong>${escapeHtml(tool.name)}</strong><p>${escapeHtml(tool.description)}</p><small>${tool.status === "ready" ? "可用" : tool.status === "external" ? "网页接力" : tool.status === "needs-data" ? "需要资料" : "未检测到"}</small></button>`).join("");
  const usable = tools.filter((tool) => ["ready", "external"].includes(tool.status)).length;
  return `${modeIntro("同一项目、同一份证据，按任务调用不同工具。", `${usable}/${tools.length} 可用`)}<section class="tool-grid">${cards}</section>`;
}

function renderReviewMode() {
  const sources = state.notebook?.sources || [];
  const sourceCount = sources.length;
  const sourceChoices = sources.map((source, index) => `<label class="review-source-choice"><input type="checkbox" data-review-source value="${escapeHtml(source.doc_id)}" checked /><span><b>${String(index + 1).padStart(2, "0")}</b><strong>${escapeHtml(compact(source.title || source.doc_id || "未命名来源", 84))}</strong><small>${escapeHtml([source.publication_year, source.doi].filter(Boolean).join(" · ") || "本地可检索来源")}</small></span></label>`).join("");
  const sourcePanel = sources.length
    ? `<details class="review-source-scope" open><summary><span>写作来源</span><strong><i data-review-selected-count>${sourceCount}</i> / ${sourceCount} 篇已选</strong></summary><div class="review-source-tools"><p>回答和正文只使用勾选来源；引用可回跳到原文证据。</p><div><button type="button" data-action="select-all-review-sources">全选</button><button type="button" data-action="clear-review-sources">清空</button></div></div><div class="review-source-list">${sourceChoices}</div></details>`
    : `<div class="review-source-empty"><strong>当前没有可用来源</strong><p>请先在知识库中导入论文，再开始证据约束写作。</p><button type="button" data-action="open-mode" data-mode="library">前往知识库</button></div>`;
  return `${modeIntro("先构建问题与证据矩阵，再写段落；每个结论保留原文跳转。", `${sourceCount} 篇来源`)}
    <section class="workflow-strip"><div><b>1</b><span>定义问题</span></div><div><b>2</b><span>检索证据</span></div><div><b>3</b><span>比较研究</span></div><div><b>4</b><span>写作与核验</span></div></section>
    <form class="mode-form review-form" id="reviewAskForm"><label class="review-question-field"><span>综述问题</span><textarea id="reviewQuestionInput" rows="4" placeholder="例如：AI 在生态监测中的主要应用、证据强度与研究空白是什么？"></textarea></label>${sourcePanel}<fieldset class="review-writing-brief"><legend>写作简报</legend><label><span>读者</span><select id="reviewAudience"><option value="researcher">科研人员</option><option value="student">研究生</option><option value="general">跨学科读者</option></select></label><label><span>语气</span><select id="reviewTone"><option value="academic">严谨学术</option><option value="concise">简洁直接</option><option value="teaching">解释清楚</option></select></label><label><span>篇幅</span><select id="reviewLength"><option value="standard">标准</option><option value="short">简短</option><option value="long">深入</option></select></label><label class="review-focus-field"><span>特别关注（可选）</span><input id="reviewFocus" maxlength="300" placeholder="例如：优先比较方法差异与证据局限" /></label></fieldset><footer class="review-form-footer"><p>${uiIcon("check")} 仅依据已选来源 · 事实句必须带可回跳引用</p><button type="submit" class="primary-button" ${sources.length ? "" : "disabled"}>检索证据并写作</button></footer></form>`;
}

function renderJournalMode() {
  return `${modeIntro("查询期刊名、ISSN、中科院分区、JCR 与预警信息。", "Journal Scout")}
    <form class="mode-form inline-mode-form" id="journalSearchForm"><label><span>期刊</span><input id="journalQuery" required placeholder="Nature Communications 或 ISSN" /></label><button type="submit" class="primary-button">查询</button></form><div class="mode-results" id="modeResults"></div>`;
}

function renderVerifyMode() {
  return `${modeIntro("粘贴参考文献，核对 Crossref/OpenAlex 元数据；也可连同正文检查断言支持度。", "Citation Lab")}
    <form class="mode-form" id="referenceAnalyzeForm"><label><span>正文与参考文献</span><textarea id="referenceText" rows="10" required placeholder="粘贴正文 + References，或只粘贴参考文献列表"></textarea></label><div class="form-row"><label class="compact-field"><span>模式</span><select id="referenceMode"><option value="references">仅核查参考文献</option><option value="full">正文与断言一起核查</option></select></label><button type="submit" class="primary-button">开始核查</button></div></form><div class="mode-results" id="modeResults"></div>`;
}

function renderAtlasMode() {
  return `${modeIntro("从题目、关键词或 DOI 找到种子论文，再进入引用与相似论文网络。", "Paper Atlas")}
    <form class="mode-form inline-mode-form" id="atlasSearchForm"><label><span>种子论文</span><input id="atlasQuery" required placeholder="论文题目、关键词或 DOI" /></label><button type="submit" class="primary-button">构建入口</button></form><div class="mode-results" id="modeResults"></div>`;
}

function renderDownloadMode() {
  const strategies = [
    ["oa_first", "开放获取优先"],
    ["gray_oa", "灰色文献与开放存档"],
    ["legal_only", "仅开放与出版商直链"],
  ];
  const selected = strategies.find(([id]) => id === state.downloadStrategy) || strategies[0];
  const menu = strategies.map(([id, label]) => `<button type="button" class="${id === state.downloadStrategy ? "is-selected" : ""}" data-action="select-download-strategy" data-download-strategy="${id}"><span>${escapeHtml(label)}</span>${id === state.downloadStrategy ? uiIcon("check") : ""}</button>`).join("");
  const batchCount = state.pendingBatchIdentifiers.length;
  const transportOptions = [["snowflake", "Snowflake（CDN 域前置，最抗封锁）"], ["obfs4", "obfs4 网桥"], ["none", "直连（不推荐）"]];
  const transportMenu = transportOptions.map(([id, label]) => `<button type="button" class="${id === state.torTransport ? "is-selected" : ""}" data-action="select-tor-transport" data-tor-transport="${id}"><span>${escapeHtml(label)}</span>${id === state.torTransport ? uiIcon("check") : ""}</button>`).join("");
  const torControls = `<label class="paper-batch-tor"><input type="checkbox" id="useTorToggle" data-action="toggle-use-tor" ${state.useTor ? "checked" : ""} /><span>Tor 轮换（匿名，较慢）</span></label>${state.useTor ? `<div class="paper-batch-tor paper-batch-tor-sub"><span>传输方式</span><button type="button" class="paper-strategy-trigger paper-tor-transport" data-action="toggle-tor-transport" aria-haspopup="listbox" aria-expanded="${state.torTransportOpen}">${escapeHtml(transportOptions.find(([id]) => id === state.torTransport)?.[1] || "Snowflake")}${uiIcon("chevron-down")}</button>${state.torTransportOpen ? `<div class="paper-strategy-menu paper-tor-transport-menu" role="listbox">${transportMenu}</div>` : ""}</div><label class="paper-batch-tor-every"><span>每</span><input type="number" id="torRotateEvery" min="1" max="20" value="${state.torRotateEvery}" data-action="set-tor-rotate" /><span>篇换 IP</span></label>` : ""}`;
  const batchPreview = batchCount
    ? `<div class="paper-batch-preview"><div class="paper-batch-preview-head"><span>已识别 ${batchCount} 个标识符</span><button type="button" class="paper-batch-clear" data-action="clear-batch-identifiers">清空</button></div><ul class="paper-batch-list">${state.pendingBatchIdentifiers.map((id) => `<li>${escapeHtml(id)}</li>`).join("")}</ul>${torControls}<button type="button" class="primary-button paper-batch-submit" data-action="start-batch-download">批量获取 (${batchCount})</button></div>`
    : "";
  return `<section class="paper-fetch-stage"><div class="paper-fetch-card"><span class="paper-fetch-eyebrow">SCANSCI · FULL TEXT</span><h1>获取论文全文</h1><p>输入 DOI 或 arXiv ID，优先从开放获取与灰色文献存档中查找可直接保存的 PDF。</p><form id="paperDownloadForm" class="paper-fetch-composer"><input id="paperIdentifier" required placeholder="输入 DOI 或 arXiv ID，例如 10.1038/..." autofocus /><button type="submit">获取</button></form><div class="paper-batch-dropzone"><label for="paperBatchFile"><span>批量获取：上传 .txt / .bib / .csv 文件，按行或字段解析 DOI 与 arXiv ID</span></label><input type="file" id="paperBatchFile" accept=".txt,.bib,.csv,text/plain" data-action="pick-batch-file" /></div>${batchPreview}<div class="paper-fetch-options"><div class="paper-strategy"><span>来源策略</span><button type="button" class="paper-strategy-trigger" data-action="toggle-download-strategy" aria-haspopup="listbox" aria-expanded="${state.downloadStrategyOpen}">${escapeHtml(selected[1])}${uiIcon("chevron-down")}</button>${state.downloadStrategyOpen ? `<div class="paper-strategy-menu" role="listbox">${menu}</div>` : ""}</div><span>文件会保存到本机</span></div><p class="paper-fetch-footnote">无需资料库、无需模型 API。灰色文献包括机构仓储、预印本和公开报告；不会绕过付费墙。</p></div><div class="mode-results paper-fetch-results" id="modeResults"></div></section>`;
}

function renderPptMode() {
  const template = selectedSlideTemplate();
  return `${modeIntro("先生成有来源绑定的叙事大纲，再创建可编辑 PPTX。")}
    <section class="ppt-layout"><form class="mode-form" id="pptOutlineForm"><label><span>汇报主题</span><input id="pptTopic" placeholder="默认使用当前项目名称" /></label><label><span>模板</span><button type="button" class="secondary-button" data-action="open-slide-templates">${escapeHtml(template?.name || "选择演示模板")}</button></label><div class="form-row"><button type="submit" class="primary-button">生成大纲</button><button type="button" class="secondary-button" data-action="create-ppt-project">创建演示文稿</button></div></form><div class="ppt-preview" id="modeResults"><div class="ppt-placeholder"><span>16:9</span><p>大纲会显示在这里</p></div></div></section>`;
}

function renderJournalResults(payload) {
  const rows = payload.items || [];
  byId("modeResults").innerHTML = rows.length ? rows.map((row) => `<a class="result-card" href="${escapeHtml(row.detail_url)}" target="_blank" rel="noopener"><div><strong>${escapeHtml(row.title)}</strong><p>${escapeHtml([row.issn, row.eissn].filter(Boolean).join(" / "))}</p></div><div class="result-tags"><span>${escapeHtml(row.cas_partition || "未标注分区")}</span><span>${escapeHtml(row.jcr_quartile || "JCR -")}</span><span>IF ${escapeHtml(row.impact_factor ?? "-")}</span>${row.warning ? '<i>预警</i>' : ""}</div></a>`).join("") : '<div class="mode-empty">没有找到匹配期刊。</div>';
}

function renderReferenceResults(payload) {
  const values = Object.values(payload.reference_results || {});
  const healthy = values.filter((item) => item.status === "green").length;
  const risk = values.filter((item) => item.status === "red").length;
  byId("modeResults").innerHTML = `<div class="result-summary"><div><b>${values.length}</b><span>参考文献</span></div><div><b>${healthy}</b><span>元数据健康</span></div><div><b>${risk}</b><span>高风险</span></div></div>${values.slice(0, 30).map((item) => `<article class="verification-row ${escapeHtml(item.status || "white")}"><div><strong>${escapeHtml(item.official?.title || item.ref_id)}</strong><p>${escapeHtml(item.reason || item.label || "")}</p></div><span>${escapeHtml(item.label || item.status || "待核验")}</span></article>`).join("")}`;
}

function renderAtlasResults(payload) {
  const rows = payload.items || [];
  if (!rows.length && payload.external_url) {
    byId("modeResults").innerHTML = `<a class="external-fallback" href="${escapeHtml(payload.external_url)}" target="_blank" rel="noopener"><span>↗</span><div><strong>在 Paper Atlas 网页继续</strong><p>${escapeHtml(payload.message || "图谱服务当前需要在网页中打开。")}</p></div></a>`;
    return;
  }
  byId("modeResults").innerHTML = rows.length ? rows.map((row) => `<a class="result-card" href="${escapeHtml(payload.external_url || "https://paperatlas.scansci.com/")}" target="_blank" rel="noopener"><div><strong>${escapeHtml(row.title || row.paper_id)}</strong><p>${escapeHtml((row.authors || []).slice(0, 3).join(", "))} · ${escapeHtml(row.year || "")}</p></div><div class="result-tags"><span>${escapeHtml(row.citation_count || 0)} 引用</span><span>打开图谱 ↗</span></div></a>`).join("") : '<div class="mode-empty">没有找到匹配论文。</div>';
}

function renderPptOutline(payload) {
  const slides = payload.slides || payload.outline?.slides || [];
  const template = payload.template || payload.outline?.template;
  const projectLink = payload.project_path ? `<div class="artifact-file-link">${localFileLinkMarkup(payload.project_path, "打开项目", { folder: true, inline: true })}</div>` : "";
  const templateCard = template ? `<div class="slide-project-artifact"><img class="slide-project-cover" src="${escapeHtml(template.preview_url)}" alt="" /><div class="slide-project-copy"><span>演示模板</span><h3>${escapeHtml(template.name)}</h3><p>${escapeHtml(template.description || template.tone || template.summary || "")}</p>${projectLink}</div></div>` : "";
  byId("modeResults").innerHTML = `${templateCard}<div class="slide-list">${slides.map((slide) => `<article><b>${escapeHtml(slide.index)}</b><div><strong>${escapeHtml(slide.title)}</strong><p>${escapeHtml(slide.purpose)}</p></div><span>${(slide.source_ids || []).length} 来源</span></article>`).join("")}</div>`;
}

const ONBOARDING_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B";
const ONBOARDING_RERANKER_MODEL = "Qwen/Qwen3-Reranker-0.6B";
const ONBOARDING_RETRIEVAL_MODELS = [ONBOARDING_EMBEDDING_MODEL, ONBOARDING_RERANKER_MODEL];
const ONBOARDING_CHAT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct";
const ONBOARDING_AUDIO_MODEL = "Qwen/Qwen3-ASR-0.6B-hf";
// Keep the recommended vision path inside ScanSci's own local-model market.
// Ollama remains available as an advanced manual connection, but it is not a
// prerequisite for the guided download flow.
const ONBOARDING_VISION_MODEL = "openbmb/MiniCPM-V-4.6-BNB";
const ONBOARDING_RESOURCE_ORDER = ["embedding", "reranking", "chat", "vision", "audio"];
const RESOURCE_GUIDE_STEPS = [
  { id: "retrieval", label: "研究检索组件", eyebrow: "第 1 页 · 研究检索组件", title: "先把研究检索准备好", description: "嵌入模型负责召回，重排模型负责筛选；这是最推荐优先配置的一组本地能力。", resourceIds: ["embedding", "reranking"] },
  { id: "knowledge", label: "知识库链接", eyebrow: "第 2 页 · 知识库链接", title: "把你的资料链接进来", description: "需要检索个人资料时，再选择知识库和资料源。没有语义模型时会继续使用关键词检索。", resourceIds: [] },
  { id: "multimodal", label: "视觉与语音", eyebrow: "第 3 页 · 视觉与语音", title: "按需添加视觉和语音", description: "图片理解与语音转写彼此独立；没有对应需求时可以直接跳过。", resourceIds: ["vision", "audio"] },
  { id: "chat", label: "小型本地对话模型", eyebrow: "第 4 页 · 可选能力", title: "最后再考虑本地对话", description: "小型本地对话模型不是必需项。只有在你希望离线对话或网络不稳定时，才建议安装。", resourceIds: ["chat"] },
];

const ONBOARDING_RESOURCE_DEFINITIONS = {
  embedding: {
    id: "embedding",
    jobId: `model:${ONBOARDING_EMBEDDING_MODEL}`,
    legacyJobId: "retrieval-core",
    models: [ONBOARDING_EMBEDDING_MODEL],
    runtime: "huggingface",
    compatibleKinds: ["embedding"],
    eyebrow: "推荐 · 语义检索",
    title: "嵌入模型",
    description: "把问题和文献内容转换为向量，提升知识库检索的召回率。",
    detail: "Qwen3 Embedding 0.6B",
    icon: "sparkles",
  },
  reranking: {
    id: "reranking",
    jobId: `model:${ONBOARDING_RERANKER_MODEL}`,
    legacyJobId: "retrieval-core",
    models: [ONBOARDING_RERANKER_MODEL],
    runtime: "huggingface",
    compatibleKinds: ["reranking"],
    eyebrow: "推荐 · 结果优化",
    title: "重排模型",
    description: "对候选文献和证据片段再次排序，减少无关结果。",
    detail: "Qwen3 Reranker 0.6B",
    icon: "filter",
  },
  chat: {
    id: "chat",
    jobId: `model:${ONBOARDING_CHAT_MODEL}`,
    models: [ONBOARDING_CHAT_MODEL],
    runtime: "huggingface",
    // A multimodal generative checkpoint can also provide ordinary text chat.
    // Keep the recommended download as a small chat-only model, but reuse a
    // compatible model that is already present instead of asking the user to
    // download a second checkpoint.
    compatibleKinds: ["chat", "vision"],
    eyebrow: "可选 · 本地对话",
    title: "小型本地对话模型",
    description: "在网络不稳定或想离线工作时，提供一个可在本机运行的基础对话模型。",
    detail: "Qwen2.5 1.5B Instruct",
    icon: "brain",
  },
  vision: {
    id: "vision",
    jobId: `model:${ONBOARDING_VISION_MODEL}`,
    models: [ONBOARDING_VISION_MODEL],
    runtime: "huggingface",
    compatibleKinds: ["vision"],
    vision: true,
    eyebrow: "可选 · 视觉理解",
    title: "视觉模型",
    description: "直接由 ScanSci 在本机运行，读取图片、图表和扫描页面。",
    detail: "MiniCPM-V 4.6 · BNB 4-bit",
    icon: "eye",
  },
  audio: {
    id: "audio",
    jobId: `model:${ONBOARDING_AUDIO_MODEL}`,
    models: [ONBOARDING_AUDIO_MODEL],
    runtime: "huggingface",
    compatibleKinds: ["audio"],
    audio: true,
    eyebrow: "可选 · 语音转写",
    title: "语音模型",
    description: "把上传或录制的语音转成文字，再交给当前对话模型。",
    detail: "Qwen3 ASR 0.6B",
    icon: "audio",
  },
};

const LEGACY_RETRIEVAL_RESOURCE = {
  id: "retrieval",
  jobId: "retrieval-core",
  models: ONBOARDING_RETRIEVAL_MODELS,
  runtime: "huggingface",
  compatibleKinds: ["embedding", "reranking"],
  eyebrow: "推荐 · 知识库能力",
  title: "研究检索组件",
  description: "用于语义检索、知识库问答和证据重排；没有它仍可使用基础关键词检索。",
  detail: "Qwen3 Embedding + Reranker",
  icon: "sparkles",
};

function onboardingPreferences() {
  return {
    welcome_dismissed: Boolean(state.settings?.onboarding?.welcome_dismissed),
    resource_setup_completed: Boolean(state.settings?.onboarding?.resource_setup_completed),
    data_setup_completed: Boolean(state.settings?.onboarding?.data_setup_completed),
  };
}

function localModelSupportsResource(model, resource) {
  if (!model?.ready || model.runtime_compatible === false) return false;
  const kinds = Array.isArray(resource?.compatibleKinds) ? resource.compatibleKinds : [];
  if (!kinds.includes(String(model.kind || ""))) return false;
  if (resource?.id !== "chat") return true;
  // The scanner keeps a few classifier checkpoints under the generic chat
  // kind. They are not text-generation models and must not satisfy the local
  // conversation-model recommendation.
  const architecture = String(model.architecture || "").toLowerCase();
  const marker = `${model.id || ""} ${model.model_type || ""}`.toLowerCase();
  return !/(classification|tokenclassification|bertmodel)/.test(architecture)
    && !/(embedding|rerank|reward|asr|whisper)/.test(marker)
    && /(causallm|conditionalgeneration)/.test(architecture);
}

function localModelResourcePreference(model, resource) {
  const preferredIds = Array.isArray(resource?.models) ? resource.models : [];
  if (preferredIds.includes(String(model?.id || ""))) return 1000;
  if (resource?.id !== "chat") return 0;
  const marker = `${model?.id || ""} ${model?.name || ""} ${model?.model_type || ""} ${model?.architecture || ""}`.toLowerCase();
  let score = String(model?.kind || "") === "chat" ? 300 : 100;
  // Prefer general-purpose instruction/chat families over a vision-specialist
  // fallback when both are installed.  This makes Qwen3.5 satisfy the guide
  // without causing MiniCPM-V to hide a better text model.
  if (/(instruct|chat|qwen3[_\-.]?5|qwen2|llama|gemma|mistral|phi)/.test(marker)) score += 200;
  if (/(minicpmv|minicpm-v|llava)/.test(marker)) score -= 50;
  return score;
}

function resourceInstallSnapshot(resource) {
  const installed = state.localModelMarket?.installed || [];
  const jobs = state.localModelInstall?.jobs || [];
  const resourceId = String(resource || "retrieval");
  const definition = ONBOARDING_RESOURCE_DEFINITIONS[resourceId] || LEGACY_RETRIEVAL_RESOURCE;
  const usesOllama = Boolean(definition.ollama);
  const localRuntimeMode = String(state.localRuntime?.mode || "");
  const runtimeNeedsUpdate = Boolean(state.localRuntime?.update_required);
  const runtimeReady = usesOllama
    ? Boolean(state.ollama?.reachable)
    : Boolean(state.localRuntime?.installed) && !runtimeNeedsUpdate && (!definition.audio || ["source", "embedded", "component"].includes(localRuntimeMode));
  const isInstalledReady = (modelId) => installed.some((item) => item.id === modelId && item.ready && item.runtime_compatible !== false);
  const preferredReady = definition.models.every(isInstalledReady);
  const compatibleModel = usesOllama
    ? null
    : installed
      .filter((item) => localModelSupportsResource(item, definition))
      .sort((left, right) => localModelResourcePreference(right, definition) - localModelResourcePreference(left, definition))[0] || null;
  const ready = usesOllama ? Boolean(state.ollama?.model_ready) : preferredReady || Boolean(compatibleModel);
  const directJob = jobs.find((item) => item.job_id === definition.jobId) || null;
  // The legacy retrieval endpoint used one combined job for embedding and
  // reranking.  Keep that job visible in the download center, but do not
  // project it onto both capability cards: the guided flow now starts one
  // model job at a time and each card must own its own progress.
  const legacyJob = definition.legacyJobId ? jobs.find((item) => item.job_id === definition.legacyJobId) || null : null;
  const job = directJob;
  const runtimeJob = usesOllama ? null : state.localRuntime?.install_job || null;
  const runtimeJobState = String(runtimeJob?.state || "idle");
  const runtimeActive = ["queued", "installing"].includes(runtimeJobState);
  const runtimeFailed = ["failed", "cancelled", "interrupted"].includes(runtimeJobState);
  const pendingResourceId = String(state.pendingLocalModelResource || "");
  const ownsRuntimeTask = Boolean(pendingResourceId && pendingResourceId === resourceId);
  const waitingForSharedRuntime = !runtimeReady && (runtimeActive || runtimeFailed) && !ownsRuntimeTask;
  const jobState = String(job?.state || "idle");
  const active = ["queued", "downloading", "installing"].includes(jobState);
  const failed = ["failed", "cancelled", "interrupted"].includes(jobState);
  const paused = jobState === "paused";
  const displayJob = !runtimeReady && (runtimeActive || runtimeFailed) && ownsRuntimeTask ? runtimeJob : job;
  const progress = ready || (!usesOllama && jobState === "ready")
    ? 100
    : Math.max(0, Math.min(100, Math.round(Number(displayJob?.progress || 0) * 100)));
  return {
    ...definition,
    job: displayJob,
    legacyJob,
    runtimeReady,
    runtimeNeedsUpdate,
    ownsRuntimeTask,
    waitingForSharedRuntime,
    compatibleModel,
    usingExistingModel: Boolean(compatibleModel && !preferredReady),
    ollama: usesOllama,
    state: !runtimeReady
      ? ownsRuntimeTask && runtimeActive ? "runtime_installing" : ownsRuntimeTask && runtimeFailed ? "runtime_failed" : "runtime_required"
      : ready || (!usesOllama && jobState === "ready") ? "ready" : active ? jobState : paused ? "paused" : failed ? jobState : "idle",
    progress,
  };
}

function resourceInstallStatusCopy(resource) {
  if (resource.state === "runtime_required" && resource.runtimeNeedsUpdate) return {
    label: "更新本地运行组件",
    hint: `检测到本地运行组件 ${state.localRuntime?.version || "旧版本"}，需要更新到 ${state.localRuntime?.required_version || "当前版本"}。已下载模型不会重复下载。`,
  };
  if (resource.state === "runtime_required" && resource.waitingForSharedRuntime) return {
    label: "等待共享运行组件",
    hint: "本地运行组件正在准备；这个模型尚未开始下载，完成后可单独启动。",
  };
  if (resource.state === "runtime_required") return {
    label: "需要准备本地能力",
    hint: state.localRuntime?.install_available
      ? "首次使用时会自动准备 ScanSci 本地运行能力，随后继续下载模型。"
      : "自动准备通道暂不可用；可从官方发布页下载本地运行组件后安装。",
  };
  if (resource.state === "runtime_installing") return {
    label: `安装运行组件 ${resource.progress}%`,
    hint: resource.job?.message || "正在下载并校验本地运行组件。",
  };
  if (resource.state === "runtime_failed") return {
    label: resource.job?.state === "interrupted" ? "安装已中断" : "安装未完成",
    hint: resource.job?.error || resource.job?.message || "可以重试；已下载内容会自动复用。",
  };
  if (resource.state === "ready") {
    if (resource.usingExistingModel && resource.compatibleModel) {
      return {
        label: "已有可用模型",
        hint: `检测到 ${resource.compatibleModel.name || resource.compatibleModel.id}，ScanSci 会自动使用，无需下载推荐模型。`,
      };
    }
    return { label: "已就绪", hint: "已保存在本机，可随时使用。" };
  }
  if (resource.state === "queued") return { label: "准备下载", hint: "正在连接可用下载源。" };
  if (resource.state === "installing") return { label: `下载中 ${resource.progress}%`, hint: resource.job?.current_model || resource.detail };
  if (resource.state === "downloading") return { label: `下载中 ${resource.progress}%`, hint: resource.job?.current_model || resource.detail };
  if (resource.state === "paused") return { label: "已暂停", hint: "可以继续下载，已接收内容会自动复用。" };
  if (resource.state === "interrupted") return { label: "下载已中断", hint: "可以继续下载，已接收内容会自动复用。" };
  if (resource.state === "failed") return { label: resource.job?.state === "interrupted" ? "下载已中断" : "下载未完成", hint: resource.job?.error || resource.job?.message || "可重试；已下载内容会继续复用。" };
  return { label: "尚未下载", hint: resource.detail };
}

function resourceSetupCard(resource) {
  const copy = resourceInstallStatusCopy(resource);
  const active = ["queued", "downloading", "installing", "runtime_installing"].includes(resource.state);
  const storageLocked = state.onboardingMode === "resources"
    && resource.state !== "ready"
    && !onboardingStorageDirectoriesConfigured();
  const retryable = ["failed", "interrupted", "cancelled"].includes(resource.state);
  const actionLabel = resource.state === "ready"
    ? "已就绪"
    : resource.state === "runtime_required"
      ? resource.runtimeNeedsUpdate ? "更新本地运行组件" : resource.waitingForSharedRuntime ? "选择此模型" : state.localRuntime?.install_available ? "准备本地能力" : "查看安装选项"
    : ["failed", "runtime_failed", "interrupted", "cancelled"].includes(resource.state)
      ? resource.state === "runtime_failed" ? "继续安装" : "重试下载"
    : resource.state === "paused"
      ? "继续下载"
      : active
        ? "正在下载"
        : "立即下载";
  const progress = active
    ? `<div class="resource-download-detail"><small>${escapeHtml(downloadJobTelemetry(resource.job) || "正在建立下载连接")}</small><div class="resource-setup-progress" role="progressbar" aria-label="${escapeHtml(resource.title)} 下载进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${resource.progress}"><span class="${progressWidthClass(resource.progress)}"></span></div></div>`
    : "";
  const action = storageLocked
    ? `<button type="button" class="resource-setup-storage-required" data-action="focus-onboarding-storage">先设置存储目录</button>`
    : ["runtime_required", "runtime_failed"].includes(resource.state)
    ? `<button type="button" data-action="start-onboarding-resource" data-resource-id="${escapeHtml(resource.id)}">${uiIcon(resource.state === "runtime_failed" ? "refresh" : "download")}${escapeHtml(actionLabel)}</button>`
    : resource.state === "runtime_installing"
      ? `<span class="resource-install-running">${uiIcon("loader-circle")}</span>`
    : resource.state === "ready"
      ? `<span>${uiIcon("check")}</span>`
      : `<button type="button" data-action="start-onboarding-resource" data-resource-id="${escapeHtml(resource.id)}" ${active ? "disabled" : ""}>${uiIcon(retryable ? "refresh" : "download")}${escapeHtml(actionLabel)}</button>`;
  const gpuBadge = resource.gpu === "required" ? `<em class="resource-gpu-badge is-required" title="需要 NVIDIA 显卡（GPU）">${uiIcon("gpu")} 需要 GPU</em>`
    : resource.gpu === "recommended" ? `<em class="resource-gpu-badge is-recommended" title="推荐使用 NVIDIA 显卡（GPU）以获得更好性能">${uiIcon("gpu")} 推荐 GPU</em>`
    : resource.gpu === "cpu" ? `<em class="resource-gpu-badge is-cpu" title="可在纯 CPU 上运行">${uiIcon("cpu")} CPU 可用</em>`
    : "";
  return `<article class="resource-setup-card is-${escapeHtml(resource.state)}"><div class="resource-setup-mark">${uiIcon(resource.icon)}</div><div class="resource-setup-copy"><span>${escapeHtml(resource.eyebrow)}</span><h3>${escapeHtml(resource.title)} ${gpuBadge}</h3><p>${escapeHtml(resource.description)}</p><small>${escapeHtml(copy.hint)}</small>${progress}</div><div class="resource-setup-action"><b>${escapeHtml(copy.label)}</b>${action}</div></article>`;
}

function resourceSetupCardsMarkup() {
  return ONBOARDING_RESOURCE_ORDER.map((resourceId) => resourceInstallSnapshot(resourceId)).map(resourceSetupCard).join("");
}

function resourceSettingsRow(resource) {
  const copy = resourceInstallStatusCopy(resource);
  const status = resource.state === "ready"
    ? `<span class="resource-settings-row-ready">${uiIcon("check")} 已就绪</span>`
    : ["queued", "downloading", "installing", "runtime_installing"].includes(resource.state)
      ? `<span class="resource-settings-row-state">下载任务在本地模型中</span>`
      : ["runtime_required", "runtime_failed"].includes(resource.state)
        ? `<span class="resource-settings-row-state">需要本地运行时</span>`
        : ["failed", "interrupted", "cancelled"].includes(resource.state)
          ? `<span class="resource-settings-row-state">下载未完成</span>`
          : `<span class="resource-settings-row-state">按需配置</span>`;
  const detail = resource.state === "ready"
    ? resource.detail
    : resource.id === "vision"
      ? "图片优先使用可用的本地视觉模型；没有时自动尝试云端视觉或 OCR。"
      : copy.hint;
  return `<article class="resource-settings-row is-${escapeHtml(resource.state)}"><span class="resource-settings-row-icon">${uiIcon(resource.icon)}</span><div class="resource-settings-row-copy"><strong>${escapeHtml(resource.title)}</strong><small>${escapeHtml(detail)}</small></div><div class="resource-settings-row-end"><div class="resource-settings-row-status">${status}</div><button type="button" class="resource-settings-row-action is-quiet" data-action="open-local-models">管理</button></div></article>`;
}

function connectedDataSourceCount() {
  return (state.workspace?.notebooks || []).filter((notebook) => {
    const kind = String(notebook?.metadata?.library_kind || "");
    return kind && kind !== "empty";
  }).length;
}

function onboardingSourceIcon(kind) {
  if (kind === "zotero") return '<img src="/zotero-logo.svg" alt="" />';
  if (kind === "obsidian") return '<img src="/obsidian-logo.svg" alt="" />';
  if (kind === "notion") return '<img src="/notion-logo.png" alt="" />';
  return uiIcon("folder-open");
}

function onboardingSourceCard({ kind, action, title, description, actionLabel }) {
  const notebooks = state.workspace?.notebooks || [];
  const count = notebooks.filter((notebook) => {
    const libraryKind = String(notebook?.metadata?.library_kind || "");
    if (kind === "folder") {
      return libraryKind && !["empty", "zotero", "obsidian", "notion"].includes(libraryKind);
    }
    return libraryKind === kind;
  }).length;
  const job = state.libraryImportJob;
  const importing = ["queued", "running"].includes(job?.state) && ["folder", "obsidian", "zotero"].includes(kind);
  const status = count ? `已连接 ${count} 个` : importing ? "正在接入" : "未连接";
  return `<article class="data-source-card ${count ? "is-connected" : ""} ${importing ? "is-importing" : ""}"><span class="data-source-card-icon ${escapeHtml(kind)}">${onboardingSourceIcon(kind)}</span><div><header><strong>${escapeHtml(title)}</strong><i>${escapeHtml(status)}</i></header><p>${escapeHtml(description)}</p><button type="button" data-action="${escapeHtml(action)}" ${importing ? "disabled" : ""}>${uiIcon(count ? "plus" : "arrow-up-right")}${escapeHtml(actionLabel)}</button></div></article>`;
}

function installResourceOnboardingDragHandle(host) {
  const backdrop = host?.querySelector?.(".resource-onboarding-backdrop");
  if (!backdrop || backdrop.querySelector(".resource-onboarding-window-drag")) return;
  const handle = document.createElement("div");
  handle.className = "resource-onboarding-window-drag pywebview-drag-region";
  handle.dataset.titlebarDrag = "";
  handle.setAttribute("aria-hidden", "true");
  backdrop.prepend(handle);
}

function resourceOnboardingDialog() {
  return byId("resourceOnboarding")?.querySelector?.('[role="dialog"]') || null;
}

function focusResourceOnboardingDialog() {
  const dialog = resourceOnboardingDialog();
  if (!dialog || dialog.contains(document.activeElement)) return;
  const target = dialog.querySelector('.resource-guide-close, button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled)');
  (target || dialog).focus({ preventScroll: true });
}

function trapResourceOnboardingFocus(event) {
  const dialog = resourceOnboardingDialog();
  if (!dialog || event.key !== "Tab") return false;
  const controls = [...dialog.querySelectorAll('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')]
    .filter((element) => !element.hidden && element.offsetParent !== null);
  if (!controls.length) {
    event.preventDefault();
    dialog.focus({ preventScroll: true });
    return true;
  }
  const first = controls[0];
  const last = controls[controls.length - 1];
  if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
    event.preventDefault();
    first.focus();
  }
  return true;
}

function restoreOnboardingFocus() {
  const settingsFallback = state.activeView === "settings"
    ? document.querySelector('[data-action="reopen-resource-onboarding"], [data-action="open-resource-guide"]')
    : null;
  const target = onboardingReturnFocus?.isConnected && !["BODY", "HTML"].includes(onboardingReturnFocus.tagName)
    ? onboardingReturnFocus
    : settingsFallback || byId("homeQuestionInput");
  onboardingReturnFocus = null;
  window.setTimeout(() => target?.focus?.({ preventScroll: true }), 0);
}

function renderDataOnboarding() {
  const sources = connectedDataSourceCount();
  return `<div class="resource-onboarding-backdrop"><section class="resource-onboarding-card" role="dialog" aria-modal="true" aria-labelledby="resourceOnboardingTitle"><aside class="resource-onboarding-aside"><span class="resource-onboarding-brand">ScanSci · FIRST RUN</span><div class="resource-onboarding-glyph">${uiIcon("library")}</div><h1 id="resourceOnboardingTitle">把资料留在原处，<br />让证据变得可用。</h1><p>选择你愿意连接的资料源。ScanSci 只读取内容、建立本地索引与引用定位，不会移动或上传原文件。</p><div class="resource-onboarding-note"><span>${uiIcon("map-pin")}</span><p>每条回答都可回到文档、章节和原文证据片段。扫描件无法读取时会明确提示你启用 OCR。</p></div></aside><main class="resource-onboarding-main data-onboarding-main"><header><div><span>资料接入</span><h2>从一个资料源开始就够了</h2><p>连接后会自动完成：文档 → 章节 → 原文证据片段。其他资料源可随后在知识库中添加。</p></div><span class="resource-onboarding-step">02 / 02</span></header><div class="data-source-grid">${onboardingSourceCard({ kind: "folder", action: "onboarding-connect-folder", title: "本地文件夹", description: "递归读取论文、报告、Markdown 和常见办公文档。", actionLabel: "选择文件夹" })}${onboardingSourceCard({ kind: "zotero", action: "onboarding-connect-zotero", title: "Zotero", description: "连接本机文献库与其已管理的论文附件。", actionLabel: "连接 Zotero" })}${onboardingSourceCard({ kind: "obsidian", action: "onboarding-connect-obsidian", title: "Obsidian", description: "保留 Vault 的笔记层级，并为每个段落建立定位。", actionLabel: "选择 Vault" })}${onboardingSourceCard({ kind: "notion", action: "onboarding-connect-notion", title: "Notion", description: "使用你的 Integration 同步已授权的页面和数据库。", actionLabel: "连接 Notion" })}</div>${guidedImportJobMarkup()}<footer><p>${sources ? `<strong>已连接 ${sources} 个资料源。</strong> 你可完成配置；资料仍会在后台建立语义索引。` : "不接入资料也可先体验 ScanSci；随时可在 设置 · 默认能力 或 知识库 中继续。"}</p><div><button type="button" class="resource-skip-button" data-action="back-resource-onboarding">上一步</button><button type="button" class="resource-skip-button" data-action="skip-resource-onboarding">暂时跳过</button>${sources ? `<button type="button" class="resource-finish-button" data-action="finish-data-onboarding">完成并开始 ${uiIcon("arrow-up-right")}</button>` : ""}</div></footer></main></section></div>`;
}

function resourceGuideStepIndex() {
  const index = Number(state.resourceGuideStep);
  return Number.isInteger(index) ? Math.max(0, Math.min(RESOURCE_GUIDE_STEPS.length - 1, index)) : 0;
}

function resourceGuideStepRail(currentIndex) {
  return RESOURCE_GUIDE_STEPS.map((step, index) => `<li class="resource-guide-step ${index === currentIndex ? "is-current" : index < currentIndex ? "is-complete" : ""}"><span>${index < currentIndex ? uiIcon("check") : index + 1}</span><small>${escapeHtml(step.label)}</small></li>`).join("");
}

function resourceGuideKnowledgePage() {
  const sourceCount = connectedDataSourceCount();
  return `<div class="resource-guide-page is-knowledge">
    <section class="guide-destination resource-guide-destination is-library"><span class="guide-destination-icon">${uiIcon("library")}</span><div><span>工作区 · 知识库</span><h3>${sourceCount ? `已连接 ${sourceCount} 个资料源` : "选择知识库，再链接资料"}</h3><p>知识库负责管理文件夹、Zotero 和其他资料源，并建立文档、章节与证据片段的定位。</p><div class="guide-destination-tags"><b>原文件不移动</b><b>可随时添加</b><b>支持 Zotero</b></div></div><button type="button" class="guide-destination-action" data-action="open-data-onboarding">${sourceCount ? "管理知识库" : "打开知识库"} ${uiIcon("arrow-up-right")}</button></section>
    <div class="resource-guide-note-grid"><article><span>${uiIcon("folder-open")}</span><div><strong>资料仍在原处</strong><p>连接只是建立本地索引，不会复制、移动或上传你的原文件。</p></div></article><article><span>${uiIcon("map-pin")}</span><div><strong>回答可回到证据</strong><p>后续回答会尽量返回文档、章节和原文片段位置。</p></div></article></div>
  </div>`;
}

function resourceGuidePageMarkup(step) {
  if (step.id === "knowledge") return resourceGuideKnowledgePage();
  const resources = step.resourceIds.map((resourceId) => resourceSetupCard(resourceInstallSnapshot(resourceId))).join("");
  if (step.id === "retrieval") {
    return `<div class="resource-guide-page is-retrieval">${onboardingStorageMarkup()}${runtimeComponentCardMarkup("node", { compact: true })}<div class="resource-guide-model-grid">${resources}</div><aside class="resource-guide-cuda-card"><span class="resource-guide-cuda-icon">${uiIcon("gpu")}</span><div><strong>CUDA 加速（可选）</strong><p>检测到 NVIDIA GPU 时会自动优先使用；没有 CUDA 也可以继续使用 CPU，不影响基础检索。</p><span class="resource-guide-cuda-status" data-cuda-status>正在检测本机 CUDA…</span></div></aside></div>`;
  }
  if (step.id === "chat") {
    return `<div class="resource-guide-page is-chat"><div class="resource-guide-optional-banner">${uiIcon("info")}<span>这是可选的第四页，不安装也不影响 ScanSci 的基础对话、联网模型和关键词检索。</span></div><div class="resource-guide-model-grid is-single">${resources}</div></div>`;
  }
  return `<div class="resource-guide-page is-multimodal"><div class="resource-guide-model-grid">${resources}</div><div class="guide-tip resource-guide-tip">${uiIcon("sparkles")}<span>视觉模型用于图片和图表理解；语音模型用于把录音转成文字。两者可以分别下载。</span></div></div>`;
}

function renderResourceGuideOverlay() {
  const snapshots = ONBOARDING_RESOURCE_ORDER.map((resourceId) => resourceInstallSnapshot(resourceId));
  const readyCount = snapshots.filter((resource) => resource.state === "ready").length;
  const currentIndex = resourceGuideStepIndex();
  const step = RESOURCE_GUIDE_STEPS[currentIndex];
  const lastStep = currentIndex === RESOURCE_GUIDE_STEPS.length - 1;
  const backButton = currentIndex > 0 ? `<button type="button" class="resource-skip-button" data-action="resource-guide-back">上一步</button>` : "";
  const nextButton = lastStep
    ? `<button type="button" class="resource-finish-button" data-action="close-resource-guide" data-resource-guide-result="complete">完成向导 ${uiIcon("check")}</button>`
    : `<button type="button" class="resource-finish-button" data-action="resource-guide-next">下一页 ${uiIcon("arrow-right")}</button>`;
  return `<div class="resource-onboarding-backdrop resource-guide-backdrop"><section class="resource-onboarding-card resource-guide-card resource-guide-wizard" role="dialog" aria-modal="true" aria-labelledby="resourceGuideTitle" tabindex="-1"><aside class="resource-onboarding-aside resource-guide-aside"><span class="resource-onboarding-brand">ScanSci · LOCAL GUIDE</span><div class="resource-onboarding-glyph">${uiIcon(step.id === "knowledge" ? "library" : step.id === "multimodal" ? "eye" : step.id === "chat" ? "brain" : "download")}</div><h1 id="resourceGuideTitle">把本地能力<br />一步步配好。</h1><p>按照研究检索、知识库、视觉语音和本地对话的顺序配置；每一页都可以跳过。</p><ol class="resource-guide-step-rail">${resourceGuideStepRail(currentIndex)}</ol><div class="resource-onboarding-note"><span>${uiIcon("shield-check")}</span><p>模型只保存在这台电脑上。没有下载模型，也不影响基础对话和关键词检索。</p></div></aside><main class="resource-onboarding-main resource-guide-main"><header><div><span>${escapeHtml(step.eyebrow)}</span><h2>${escapeHtml(step.title)}</h2><p>${escapeHtml(step.description)}</p></div><div class="resource-guide-header-actions"><span class="resource-guide-count">${readyCount}/${snapshots.length} 已就绪</span><button type="button" class="resource-guide-close" data-action="close-resource-guide" data-resource-guide-result="skip" aria-label="关闭本地能力引导">${uiIcon("x")}</button></div></header><div class="resource-guide-wizard-body">${resourceGuidePageMarkup(step)}</div><footer><p>第 ${currentIndex + 1} / ${RESOURCE_GUIDE_STEPS.length} 页 · 关闭后可从“本地模型”页再次打开。</p><div><button type="button" class="resource-skip-button" data-action="close-resource-guide" data-resource-guide-result="skip">暂时跳过</button>${backButton}${nextButton}</div></footer></main></section></div>`;
}

function openResourceGuideOverlay({ returnToWelcome = false } = {}) {
  if (state.onboardingMode !== "resources") onboardingReturnFocus = document.activeElement;
  state.onboardingGuideFromWelcome = returnToWelcome || (state.onboardingOpen && state.onboardingMode === "welcome");
  state.onboardingMode = "resources";
  state.resourceGuideStep = 0;
  state.onboardingOpen = true;
  renderResourceOnboarding();
  refreshCudaStatus();
  void refreshInstalledModelInventory();
}

async function closeResourceGuideOverlay(result = "skip") {
  const returnToWelcome = state.onboardingGuideFromWelcome;
  state.onboardingGuideFromWelcome = false;
  state.onboardingMode = returnToWelcome ? "welcome" : "";
  state.resourceGuideStep = 0;
  const restoreFocus = () => {
    if (returnToWelcome && state.activeView === "home") {
      window.setTimeout(() => byId("firstRunWelcome")?.querySelector('[data-action="onboarding-open-models"]')?.focus({ preventScroll: true }), 0);
    } else restoreOnboardingFocus();
  };
  if (returnToWelcome && result !== "complete") {
    renderResourceOnboarding();
    restoreFocus();
    return;
  }
  const persistence = persistOnboardingPreferences(
    returnToWelcome
      ? { resource_setup_completed: true }
      : result === "complete"
        ? { welcome_dismissed: true, resource_setup_completed: true }
        : { welcome_dismissed: true },
    result === "complete"
      ? "本地能力配置已完成；之后可随时再次打开引导"
      : "已关闭本地能力配置；需要时可在本地模型页重新打开",
    { close: !returnToWelcome },
  );
  restoreFocus();
  await persistence;
}

function renderLegacyResourceOnboarding() {
  const host = byId("resourceOnboarding");
  if (!host) return;
  if (!state.onboardingOpen || !state.settings) {
    host.hidden = true;
    host.innerHTML = "";
    return;
  }
  if (state.onboardingStep === "sources") {
    host.hidden = false;
    host.innerHTML = renderDataOnboarding();
    installResourceOnboardingDragHandle(host);
    hydrateIcons(host);
    return;
  }
  host.hidden = false;
  host.innerHTML = `<div class="resource-onboarding-backdrop"><section class="resource-onboarding-card" role="dialog" aria-modal="true" aria-labelledby="resourceOnboardingTitle"><aside class="resource-onboarding-aside"><span class="resource-onboarding-brand">ScanSci · FIRST RUN</span><div class="resource-onboarding-glyph">${uiIcon("sparkles")}</div><h1 id="resourceOnboardingTitle">先把研究桌面<br />准备好。</h1><p>ScanSci 已包含基础能力；需要下载的模型由你决定，并始终保存在这台电脑上。</p><div class="resource-onboarding-note"><span>${uiIcon("shield-check")}</span><p>下载可断点续传。跳过不会影响基础使用，之后可在设置里继续。</p></div></aside><main class="resource-onboarding-main"><header><div><span>本地模型</span><h2>按能力分别添加</h2><p>嵌入和重排负责知识库检索；视觉、语音和本地对话按需开启。</p></div><span class="resource-onboarding-step">01 / 02</span></header><div class="resource-setup-cards">${resourceSetupCardsMarkup()}</div><footer><p>每项模型都可单独下载。跳过不会影响基础使用，之后可在设置里继续。</p><div><button type="button" class="resource-skip-button" data-action="skip-resource-onboarding">暂时跳过</button><button type="button" class="resource-finish-button" data-action="advance-resource-onboarding">下一步：接入资料 ${uiIcon("arrow-right")}</button></div></footer></main></section></div>`;
  installResourceOnboardingDragHandle(host);
  hydrateIcons(host);
}

function onboardingStorageMarkup() {
  const directories = generalPreferences().directories;
  const picker = (setting, value, label, hint) => `<div class="onboarding-storage-row"><div><strong>${escapeHtml(label)}</strong><small>${escapeHtml(hint)}</small><code>${escapeHtml(value || copy("storageDirectoryPlaceholder"))}</code></div><button type="button" class="guide-destination-action" data-action="choose-storage-directory" data-directory-setting="${escapeHtml(setting)}">${escapeHtml(copy("chooseDirectory"))}</button></div>`;
  return `<section class="onboarding-storage-panel"><header><span>${uiIcon("folder-open")}</span><div><strong>下载前先确认存储位置</strong><p>嵌入模型、重排模型和其他本地模型都会写入模型缓存目录；Transformers 运行组件会写入运行组件目录。留空则使用应用默认目录。</p></div></header>${picker("model_cache", directories.model_cache, copy("modelCacheDirectory"), copy("modelCacheDirectoryHint"))}${picker("local_runtime", directories.local_runtime, copy("localRuntimeDirectory"), copy("localRuntimeDirectoryHint"))}<small class="onboarding-storage-footnote">${escapeHtml(copy("storageDirectoryRestartHint"))}</small></section>`;
}

function onboardingStorageDirectoriesConfigured() {
  const directories = generalPreferences().directories;
  return Boolean(String(directories.model_cache || "").trim() && String(directories.local_runtime || "").trim());
}

function ensureOnboardingStorageConfigured() {
  if (state.onboardingMode !== "resources" || onboardingStorageDirectoriesConfigured()) return true;
  toast("先设置存储目录再开始下载");
  document.querySelector(".onboarding-storage-panel")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  return false;
}

function renderFirstRunWelcome() {
  const host = byId("firstRunWelcome");
  if (!host) return;
  const visible = Boolean(
    state.settings
    && state.onboardingOpen
    && state.onboardingMode === "welcome"
    && state.activeView === "home",
  );
  host.hidden = !visible;
  host.closest(".home-view")?.classList.toggle("has-first-run-guide", visible);
  if (!visible) {
    host.innerHTML = "";
    return;
  }
  const disabled = state.onboardingPersisting ? " disabled" : "";
  host.setAttribute("aria-busy", String(state.onboardingPersisting));
  host.innerHTML = `<div class="first-run-welcome-copy"><span class="first-run-welcome-mark">${uiIcon("book-open")}</span><div><h2 id="firstRunWelcomeTitle">从一个研究问题开始</h2><p>在上方直接描述任务，或选择下方功能。资料和本地模型用到时再连接。</p></div></div><div class="first-run-welcome-actions"><button type="button" data-action="onboarding-open-knowledge"${disabled}>${uiIcon("library")}连接知识库</button><button type="button" data-action="onboarding-open-models"${disabled}>${uiIcon("download")}配置本地能力</button><button type="button" class="first-run-dismiss" data-action="finish-first-run" aria-label="关闭新手引导" title="关闭新手引导"${disabled}>${uiIcon("x")}</button></div>`;
  host.setAttribute("aria-labelledby", "firstRunWelcomeTitle");
  hydrateIcons(host);
}

async function finishFirstRun(message = "已进入研究工作区", { focusComposer = true } = {}) {
  state.onboardingMode = "";
  await persistOnboardingPreferences({ welcome_dismissed: true }, message, { close: true });
  if (focusComposer) window.setTimeout(() => byId("homeQuestionInput")?.focus({ preventScroll: true }), 0);
}

function renderResourceOnboarding() {
  renderFirstRunWelcome();
  const steps = [
    ["welcome", "认识工作区"],
    ["models", "按需配置模型"],
    ["knowledge", "连接研究资料"],
  ];
  const current = steps.some(([id]) => id === state.onboardingStep) ? state.onboardingStep : "welcome";
  const currentIndex = Math.max(0, steps.findIndex(([id]) => id === current));
  const stepRail = steps.map(([id, label], index) => `<li class="guide-step ${id === current ? "is-current" : index < currentIndex ? "is-complete" : ""}"><span>${index < currentIndex ? uiIcon("check") : index + 1}</span><small>${label}</small></li>`).join("");
  const panels = {
    welcome: `<header><div><span>认识 ScanSci</span><h2>先熟悉研究桌面</h2><p>这里是你的研究工作台。模型和资料都按需配置，不会因为第一次打开就强迫你下载或连接任何东西。</p></div><span class="resource-onboarding-step">01 / 03</span></header><div class="guide-feature-grid"><article><span class="guide-feature-icon">${uiIcon("message-circle")}</span><div><strong>新建研究</strong><p>直接描述你想完成的事，ScanSci 会根据任务选择合适的研究流程。</p></div></article><article><span class="guide-feature-icon">${uiIcon("library")}</span><div><strong>知识库</strong><p>你的文件、Zotero 和笔记都从这里接入，原文件仍留在原来的位置。</p></div></article><article><span class="guide-feature-icon">${uiIcon("settings")}</span><div><strong>设置</strong><p>模型、运行时和外观都在设置中管理，随时可以回来调整。</p></div></article></div><div class="guide-tip">${uiIcon("sparkles")}<span>你现在就可以跳过引导，先试着提一个问题。</span></div><footer><button type="button" class="resource-skip-button" data-action="skip-resource-onboarding">跳过引导</button><button type="button" class="resource-finish-button" data-action="onboarding-next">下一步 ${uiIcon("arrow-right")}</button></footer>`,
    models: `<header><div><span>默认能力</span><h2>需要时，再下载模型</h2><p>本地模型不是使用 ScanSci 的前置条件。需要更好的语义检索、离线对话、视觉或语音能力时，从默认能力页打开按需配置。</p></div><span class="resource-onboarding-step">02 / 03</span></header><div class="guide-destination"><span class="guide-destination-icon">${uiIcon("download")}</span><div><span>设置 · 默认能力</span><h3>按能力查看默认推荐</h3><p>嵌入、重排、对话、视觉和语音分组显示；每张卡片都写清用途、大小和下载状态。</p><div class="guide-destination-tags"><b>默认推荐</b><b>可跳过</b><b>下载后本地保存</b></div></div><button type="button" class="guide-destination-action" data-action="onboarding-open-models">打开本地能力引导 ${uiIcon("arrow-up-right")}</button></div><div class="guide-tip">${uiIcon("shield-check")}<span>没有模型时仍可使用基础关键词检索和已配置的云端模型。</span></div><footer><button type="button" class="resource-skip-button" data-action="onboarding-back">上一步</button><div><button type="button" class="resource-skip-button" data-action="skip-resource-onboarding">跳过引导</button><button type="button" class="resource-finish-button" data-action="onboarding-next">下一步 ${uiIcon("arrow-right")}</button></div></footer>`,
    knowledge: `<header><div><span>资料接入</span><h2>资料，从知识库开始</h2><p>连接文件夹、个人文件、Zotero 或其他资料源都在知识库页面完成。你可以先创建空的个人知识库，之后再添加内容。</p></div><span class="resource-onboarding-step">03 / 03</span></header><div class="guide-destination is-library"><span class="guide-destination-icon">${uiIcon("library")}</span><div><span>工作区 · 知识库</span><h3>选择知识库，再链接文件或文件夹</h3><p>知识库负责管理资料和索引；对话框里的“知识库”按钮只负责选择本轮要检索的范围。</p><div class="guide-destination-tags"><b>原文件不移动</b><b>可随时添加</b><b>支持 Zotero</b></div></div><button type="button" class="guide-destination-action" data-action="onboarding-open-knowledge">打开知识库 ${uiIcon("arrow-up-right")}</button></div><div class="guide-tip">${uiIcon("map-pin")}<span>先不接入也没关系，随时可以从左侧“知识库”进入。</span></div><footer><button type="button" class="resource-skip-button" data-action="onboarding-back">上一步</button><button type="button" class="resource-finish-button" data-action="skip-resource-onboarding">完成引导 ${uiIcon("check")}</button></footer>`,
  };
  const host = byId("resourceOnboarding");
  if (!host) return;
  if (!state.onboardingOpen || !state.settings) {
    host.hidden = true;
    host.innerHTML = "";
    return;
  }
  if (state.onboardingMode === "welcome") {
    host.hidden = true;
    host.innerHTML = "";
    return;
  }
  if (state.onboardingMode === "resources") {
    host.hidden = false;
    host.innerHTML = renderResourceGuideOverlay();
    installResourceOnboardingDragHandle(host);
    hydrateIcons(host);
    window.setTimeout(focusResourceOnboardingDialog, 0);
    return;
  }
  host.hidden = false;
  host.innerHTML = `<div class="resource-onboarding-backdrop"><section class="resource-onboarding-card guide-onboarding-card" role="dialog" aria-modal="true" aria-labelledby="resourceOnboardingTitle"><aside class="resource-onboarding-aside guide-onboarding-aside"><span class="resource-onboarding-brand">ScanSci · GUIDE</span><div class="resource-onboarding-glyph">${uiIcon(current === "knowledge" ? "library" : current === "models" ? "settings" : "sparkles")}</div><h1 id="resourceOnboardingTitle">把研究桌面<br />用顺手。</h1><p>三步认识 ScanSci，配置按需进行，想跳过也完全可以。</p><ol class="guide-step-rail">${stepRail}</ol><div class="resource-onboarding-note"><span>${uiIcon("shield-check")}</span><p>设置和资料只保存在这台电脑上；你可以随时回到对应页面继续。</p></div></aside><main class="resource-onboarding-main guide-onboarding-main">${panels[current]}</main></section></div>`;
  installResourceOnboardingDragHandle(host);
  hydrateIcons(host);
}

function resourceInstallGuideGroup(resourceIds, eyebrow, title, description) {
  const cards = resourceIds
    .map((resourceId) => resourceInstallSnapshot(resourceId))
    .map(resourceSetupCard)
    .join("");
  return `<section class="resource-install-group"><header><div><span>${escapeHtml(eyebrow)}</span><h2>${escapeHtml(title)}</h2><p>${escapeHtml(description)}</p></div><em>${resourceIds.length} 项</em></header><div class="resource-install-grid">${cards}</div></section>`;
}

function renderResourceSetupSettings() {
  const sourceCount = connectedDataSourceCount();
  const snapshots = ONBOARDING_RESOURCE_ORDER.map((resourceId) => resourceInstallSnapshot(resourceId));
  const readyCount = snapshots.filter((resource) => resource.state === "ready").length;
  const activeCount = snapshots.filter((resource) => ["queued", "downloading", "installing", "runtime_installing"].includes(resource.state)).length;
  const summary = activeCount
    ? `${readyCount}/${snapshots.length} 项已就绪 · ${activeCount} 项正在处理`
    : `${readyCount}/${snapshots.length} 项已就绪 · 其余按需安装`;
  const dataSummary = sourceCount ? `已连接 ${sourceCount} 个资料源` : "尚未接入资料";
  return `<section class="resource-settings-page resource-install-page"><header class="resource-install-heading"><div><span>LOCAL MODELS</span><h1>本地模型</h1><p>模型下载和本地组件都从这里管理。下载完成后，去“默认能力”选择它们的用途。</p></div><button type="button" class="quiet-text-button" data-action="reopen-resource-onboarding">查看使用引导</button></header>
    <section class="resource-install-summary"><div class="resource-install-summary-mark">${uiIcon(readyCount ? "check" : "download")}</div><div><strong>${escapeHtml(summary)}</strong><p>模型只保存在这台电脑上；已完成的资源不会重复显示下载进度。</p></div><button type="button" class="resource-install-summary-action" data-action="open-settings" data-settings-panel="defaults">选择默认能力 ${uiIcon("arrow-right")}</button></section>
    ${resourceInstallGuideGroup(["embedding", "reranking"], "知识库检索", "让文献更容易被找到", "嵌入模型建立语义索引，重排模型帮助 ScanSci 从候选片段中挑出更相关的证据。")}
    ${resourceInstallGuideGroup(["chat", "vision", "audio"], "本地助手与多模态", "按需添加离线能力", "本地对话、图片理解和语音转写彼此独立；不需要的能力可以跳过。")}
    ${localRuntimeChannelRecoveryMarkup(state.localRuntime)}
    <section class="resource-install-data-card"><span>${uiIcon(sourceCount ? "check" : "library")}</span><div><strong>资料接入</strong><p>${escapeHtml(dataSummary)}${sourceCount ? " · 已建立本地索引" : " · 连接后自动建立文档、章节与证据片段"}</p></div><button type="button" class="resource-install-data-action" data-action="open-data-onboarding">${sourceCount ? "管理资料" : "接入资料"} ${uiIcon("arrow-right")}</button></section>
    <p class="resource-install-footnote">没有安装检索模型时，ScanSci 仍会使用基础关键词检索；运行外部 Ollama、LM Studio 或 llama.cpp 连接，请到“本地模型”中的高级设置。</p></section>`;
}

async function persistOnboardingPreferences(patch, message, { close = false } = {}) {
  if (!state.settings || state.onboardingPersisting) return;
  state.onboardingPersisting = true;
  state.settings.onboarding = { ...onboardingPreferences(), ...patch };
  if (close) state.onboardingOpen = false;
  renderResourceOnboarding();
  try {
    await persistSettings(message);
  } finally {
    state.onboardingPersisting = false;
    renderResourceOnboarding();
  }
}

async function startOnboardingResource(resourceId) {
  await refreshInstalledModelInventory({ render: false });
  const resource = resourceInstallSnapshot(resourceId);
  if (["ready", "queued", "downloading", "installing"].includes(resource.state)) return;
  if (!ensureOnboardingStorageConfigured()) return;
  if (["runtime_required", "runtime_installing", "runtime_failed"].includes(resource.state)) {
    state.pendingLocalModelResource = resource.id;
    if (resource.state === "runtime_installing") {
      renderResourceOnboarding();
      toast("本地运行能力正在准备；完成后会自动继续下载模型。");
      return;
    }
    if (state.localRuntime?.install_available) {
      const job = await request("/api/local-runtime/install", { method: "POST", body: "{}" });
      state.localRuntime = { ...(state.localRuntime || {}), install_job: job };
      scheduleLocalRuntimeInstallPoll();
      renderResourceOnboarding();
      renderDownloadActivity();
      toast("正在准备本地能力，完成后会自动继续下载模型。");
      return;
    }
    state.activeView = "settings";
    state.activeSettings = "local-models";
    renderWorkspace();
    document.querySelector(".local-runtime-disclosure")?.setAttribute("open", "");
    toast("当前发行包没有可用的自动准备通道；请在本地模型页安装 ScanSci 本地运行组件后重试。", true);
    return;
  }
  const endpoint = resource.id === "retrieval" ? "/api/resources/retrieval/download" : "/api/local-models/download";
  const payload = resource.id === "retrieval"
    ? {}
    : { id: resource.models[0], runtime: resource.runtime || "huggingface" };
  const job = await request(endpoint, { method: "POST", body: JSON.stringify(payload) });
  mergeLocalModelInstall(job);
  scheduleLocalModelInstallPoll();
  renderResourceOnboarding();
  if (state.activeView === "settings" && state.activeSettings === "resources") renderSettings();
  renderDownloadActivity();
  toast(`${resource.title} 已开始下载；右上角可持续查看进度。`);
}

const knowledgeSettingsPreviewModels = {
  embedding: [
    { id: "auto", name: "Agent 自动选择（推荐）", meta: "按本机可用性 · 不固定模型", note: "有合适的本地模型就使用；否则自动回退到基础检索" },
    { id: "qwen3-embedding-0.6b", name: "Qwen3 Embedding 0.6B", meta: "本地 · 614 MB · 1024 维", note: "适合中文科研文献，速度和效果平衡" },
    { id: "bge-m3", name: "BAAI/bge-m3", meta: "本地 · 2.2 GB · 1024 维", note: "多语言兼容方案，资源占用更高" },
    { id: "offline-keyword", name: "基础关键词检索", meta: "内置 · 无需模型", note: "没有向量索引时的可靠回退" },
  ],
  reranking: [
    { id: "auto", name: "Agent 自动选择（推荐）", meta: "按本机可用性 · 不固定模型", note: "有重排模型就使用；没有时直接返回嵌入检索结果" },
    { id: "qwen3-reranker-0.6b", name: "Qwen3 Reranker 0.6B", meta: "本地 · 640 MB · 已安装", note: "对候选片段重新排序，提高命中质量" },
    { id: "bge-reranker-v2-m3", name: "BAAI/bge-reranker-v2-m3", meta: "本地 · 2.2 GB · 可下载", note: "多语言重排方案，适合跨语言资料库" },
    { id: "no-reranker", name: "不使用重排", meta: "基础模式 · 更快", note: "直接使用嵌入检索结果" },
  ],
};

function knowledgeSettingsPreviewModel(role) {
  const selectedId = state.knowledgeSettingsPreview?.[role] || "";
  return (knowledgeSettingsPreviewModels[role] || []).find((item) => item.id === selectedId)
    || knowledgeSettingsPreviewModels[role]?.[0]
    || { id: "", name: "未指定", meta: "使用系统回退", note: "" };
}

function knowledgeSettingsPreviewOptions(role) {
  const selectedId = state.knowledgeSettingsPreview?.[role] || "";
  return (knowledgeSettingsPreviewModels[role] || []).map((item) => `<option value="${escapeHtml(item.id)}" data-model-name="${escapeHtml(item.name)}" data-model-meta="${escapeHtml(item.meta)}" ${item.id === selectedId ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("");
}

function renderKnowledgeSettingsPreview() {
  const preview = state.knowledgeSettingsPreview || {};
  const notebook = state.notebook || {};
  const title = notebook.title || "光伏生态文献";
  const sourceCount = Number(notebook.counts?.sources || 420);
  const chunkCount = Number(notebook.counts?.evidence || 3186);
  const embedding = knowledgeSettingsPreviewModel("embedding");
  const reranking = knowledgeSettingsPreviewModel("reranking");
  const embeddingChanged = !["auto", "qwen3-embedding-0.6b"].includes(embedding.id);
  const rerankingChanged = !["auto", "qwen3-reranker-0.6b", "no-reranker"].includes(reranking.id);
  const pendingNotice = embeddingChanged
    ? `<div class="knowledge-settings-notice is-warning">${uiIcon("info")}<span><strong>保存后需要重建向量索引。</strong>原文档不会被修改，重建完成前仍可继续使用当前索引。</span></div>`
    : "";
  const advanced = preview.advancedOpen
    ? `<div class="knowledge-settings-advanced-body">
        <div class="knowledge-settings-advanced-grid">
          <label class="knowledge-settings-field"><span>文本切分</span><select aria-label="文本切分方式"><option selected>按语义段落</option><option>按固定长度</option><option>按标题层级</option></select><small>改变切分方式后需要重建索引。</small></label>
          <label class="knowledge-settings-field"><span>每次返回片段</span><select aria-label="每次返回片段数量"><option>6 个</option><option selected>8 个</option><option>12 个</option><option>16 个</option></select><small>只影响回答时送给模型的证据数量。</small></label>
          <label class="knowledge-settings-field"><span>最低相似度</span><select aria-label="最低相似度"><option>不限制</option><option selected>0.35</option><option>0.45</option><option>0.55</option></select><small>过滤明显不相关的片段。</small></label>
        </div>
        <p class="knowledge-settings-advanced-note">高级设置只作用于“${escapeHtml(title)}”。如果你不确定，保持默认即可。</p>
      </div>`
    : "";
  return `<section class="knowledge-settings-preview" aria-labelledby="knowledgeSettingsTitle">
    <header class="knowledge-settings-heading">
      <div class="knowledge-settings-breadcrumb"><button type="button" data-action="preview-open-library">${uiIcon("arrow-left")}知识库</button><span>/</span><strong>${escapeHtml(title)}</strong></div>
      <div class="knowledge-settings-heading-status"><span class="knowledge-settings-status-pill">${uiIcon("check")}检索已就绪</span><span>仅对当前知识库生效</span></div>
      <h1 id="knowledgeSettingsTitle">检索设置</h1>
      <p>控制这个知识库如何找到和排序文献；其他知识库和默认助手模型不会被改变。</p>
    </header>

    <main class="knowledge-settings-main">
      <section class="knowledge-settings-index-card">
        <header class="knowledge-settings-card-heading"><div><span class="knowledge-settings-kicker">知识库</span><h2>${escapeHtml(title)}</h2><p>已建立本地索引，回答时会优先使用这里的资料。</p></div><span class="knowledge-settings-index-state">${uiIcon("check")}索引可用</span></header>
        <div class="knowledge-settings-metrics"><div><b>${sourceCount.toLocaleString("zh-CN")}</b><span>篇文献</span></div><div><b>${chunkCount.toLocaleString("zh-CN")}</b><span>个证据片段</span></div><div><b>2026/08/07</b><span>最近更新</span></div></div>
        ${embeddingChanged ? `<button type="button" class="knowledge-settings-quiet-action" data-action="preview-knowledge-rebuild">${uiIcon("refresh")}重建索引</button>` : ""}
      </section>

      <section class="knowledge-settings-model-card">
        <header class="knowledge-settings-section-heading"><div><span class="knowledge-settings-kicker">检索模型</span><h2>找到并排序相关内容</h2><p>嵌入模型决定召回范围，重排模型决定结果顺序。</p></div></header>
        <div class="knowledge-settings-model-list">
          <article class="knowledge-settings-model-row">
            <span class="knowledge-settings-model-icon is-embedding">${uiIcon("database")}</span>
            <div class="knowledge-settings-model-copy"><span>嵌入模型</span><strong>${escapeHtml(embedding.name)}</strong><p>把文献和问题转换成可比较的向量；更换后需要重建索引。</p></div>
            <div class="knowledge-settings-model-control"><select data-preview-knowledge-select="embedding" aria-label="选择嵌入模型">${knowledgeSettingsPreviewOptions("embedding")}</select><small class="${embeddingChanged ? "is-warning" : ""}">${embeddingChanged ? "保存后重建索引" : "已启用"}</small></div>
          </article>
          <article class="knowledge-settings-model-row">
            <span class="knowledge-settings-model-icon is-reranking">${uiIcon("filter")}</span>
            <div class="knowledge-settings-model-copy"><span>重排模型</span><strong>${escapeHtml(reranking.name)}</strong><p>对召回的候选片段重新排序；更换后不需要重建向量。</p></div>
            <div class="knowledge-settings-model-control"><select data-preview-knowledge-select="reranking" aria-label="选择重排模型">${knowledgeSettingsPreviewOptions("reranking")}</select><small class="${rerankingChanged ? "is-warning" : ""}">${rerankingChanged ? "待保存" : "已启用"}</small></div>
          </article>
        </div>
        ${pendingNotice}
      </section>

      <section class="knowledge-settings-advanced-section ${preview.advancedOpen ? "is-open" : ""}">
        <button type="button" class="knowledge-settings-advanced-trigger" data-action="toggle-preview-knowledge-advanced" aria-expanded="${preview.advancedOpen ? "true" : "false"}"><span><b>高级检索设置</b><small>切分、返回数量和阈值</small></span><span class="knowledge-settings-advanced-arrow">${uiIcon("chevron-down")}</span></button>
        ${advanced}
      </section>

      <footer class="knowledge-settings-actions"><span>配置仅保存到当前知识库</span><button type="button" class="knowledge-settings-save" data-action="preview-knowledge-save">保存检索设置</button></footer>
    </main>
  </section>`;
}

function systemOcrLanguageKey() {
  const values = state.settings?.document_processing?.ocr?.languages;
  return (Array.isArray(values) ? values : ["zh", "en"]).map((value) => String(value || "").trim()).filter(Boolean).sort().join(",");
}

function selectedOcrProvider() {
  return String(state.settings?.document_processing?.ocr?.provider || "tesseract").trim().toLowerCase() || "tesseract";
}

async function refreshSystemOcrStatus({ force = false } = {}) {
  const requestedKey = systemOcrLanguageKey();
  const provider = selectedOcrProvider();
  const current = state.systemOcrStatus || {};
  if (current.loading || (!force && current.provider === provider && current.requestedKey === requestedKey && current.checkedAt)) return current;
  const providerTitle = provider === "system" ? "Windows OCR 引擎" : provider === "tesseract" ? "Tesseract OCR" : "OCR 服务";
  state.systemOcrStatus = { ...current, loading: true, provider, requestedKey, message: `正在检测 ${providerTitle}…` };
  if (state.activeView === "settings" && ["defaults", "document-processing"].includes(state.activeSettings)) renderSettings();
  try {
    const params = new URLSearchParams();
    if (requestedKey) params.set("languages", requestedKey);
    params.set("provider", provider);
    const query = `?${params.toString()}`;
    const result = await request(`/api/settings/document-processing/ocr/status${query}`);
    state.systemOcrStatus = { ...result, loading: false, provider, requestedKey, checkedAt: new Date().toISOString() };
  } catch (error) {
    state.systemOcrStatus = {
      ...current,
      loading: false,
      provider,
      requestedKey,
      checkedAt: new Date().toISOString(),
      available: false,
      message: `${providerTitle} 检测失败：${error.message || "未知错误"}`,
    };
  }
  if (state.activeView === "settings" && ["defaults", "document-processing"].includes(state.activeSettings)) renderSettings();
  return state.systemOcrStatus;
}

async function installTesseractOcr() {
  const languages = systemOcrLanguageKey().split(",").filter(Boolean);
  const started = await request("/api/settings/document-processing/ocr/install", {
    method: "POST",
    body: JSON.stringify({ languages }),
  });
  state.systemOcrStatus = { ...(state.systemOcrStatus || {}), install: started };
  if (state.activeView === "settings") renderSettings();
  for (let attempt = 0; attempt < 600; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
    const status = await refreshSystemOcrStatus({ force: true });
    const installState = String(status?.install?.state || "idle");
    if (["ready", "failed"].includes(installState)) {
      if (installState === "ready") toast("Tesseract OCR 已就绪");
      else toast(status?.install?.error || "Tesseract OCR 安装未完成", true);
      return status;
    }
  }
  throw new Error("Tesseract OCR 安装等待超时，请点击重新检测。");
}

async function loadArchivedSettingsRecords() {
  if (state.archiveSettingsLoading) return;
  state.archiveSettingsLoading = true;
  state.archiveSettingsError = "";
  if (state.activeView === "settings" && state.activeSettings === "archive") renderSettings();
  try {
    const [runsPayload, directPayload] = await Promise.all([
      request("/api/runs?view=archived&limit=200"),
      request("/api/chat/history?view=archived&limit=200"),
    ]);
    state.archivedRuns = Array.isArray(runsPayload?.runs) ? runsPayload.runs : [];
    state.archivedConversations = Array.isArray(directPayload?.conversations) ? directPayload.conversations : [];
    state.archiveSettingsLoaded = true;
  } catch (error) {
    state.archiveSettingsError = error.message || "无法读取归档对话";
  } finally {
    state.archiveSettingsLoading = false;
    if (state.activeView === "settings" && state.activeSettings === "archive") renderSettings();
  }
}

function archiveSettingsRecords() {
  const query = String(state.archiveSettingsQuery || "").trim().toLocaleLowerCase();
  const runs = (state.archiveSettingsLoaded ? state.archivedRuns : state.runs.filter((item) => Boolean(item.archived)))
    .map((item) => ({ ...item, settingsRecordKind: "run" }));
  const conversations = (state.archivedConversations || [])
    .map((item) => ({ ...item, settingsRecordKind: "direct" }));
  return [...runs, ...conversations]
    .filter((item) => {
      if (!query) return true;
      const title = item.settingsRecordKind === "direct" ? item.title : runDisplayTitle(item);
      return [title, item.preview, item.status, item.updated_at, "归档对话"].join(" ").toLocaleLowerCase().includes(query);
    })
    .sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
}

function renderArchiveSettings() {
  if (!state.archiveSettingsLoaded && !state.archiveSettingsLoading) {
    window.setTimeout(() => loadArchivedSettingsRecords(), 0);
  }
  const records = archiveSettingsRecords();
  const total = state.archiveSettingsLoaded ? records.length : (state.runs.filter((item) => Boolean(item.archived)).length + state.archivedConversations.length);
  const rows = records.length
    ? `<div class="archive-settings-list">${records.slice(0, 100).map((record) => {
      const isDirect = record.settingsRecordKind === "direct";
      const title = isDirect ? (record.title || "直接对话") : runDisplayTitle(record);
      const action = isDirect ? "open-direct-conversation" : "open-task";
      const identifier = isDirect ? record.conversation_id : record.run_id;
      const keyAttribute = isDirect ? "data-conversation-id" : "data-task-id";
      const meta = isDirect ? "直接对话" : (runStatusLabel(record) || "研究对话");
      return `<article class="archive-settings-row"><button type="button" class="archive-settings-open" data-action="${action}" ${keyAttribute}="${escapeHtml(identifier)}"><span class="archive-settings-icon">${uiIcon(isDirect ? "message-circle" : "archive")}</span><span class="archive-settings-copy"><strong>${escapeHtml(compact(title, 60))}</strong><small>${escapeHtml(meta)} · ${escapeHtml(record.updated_at || "")}</small></span></button><div class="archive-settings-actions"><button type="button" class="settings-inline-link" data-action="${isDirect ? "restore-direct-conversation" : "restore-task"}" ${keyAttribute}="${escapeHtml(identifier)}">${uiIcon("archive-restore")}恢复</button><button type="button" class="settings-inline-link is-danger" data-action="${isDirect ? "delete-direct-conversation" : "delete-task"}" ${keyAttribute}="${escapeHtml(identifier)}">${uiIcon("trash")}删除</button></div></article>`;
    }).join("<div class=\"archive-settings-row-gap\" aria-hidden=\"true\"></div>")}</div>`
    : `<div class="archive-settings-empty"><span>${uiIcon("archive")}</span><strong>${state.archiveSettingsLoading ? "正在读取归档对话" : "还没有已归档对话"}</strong><p>${state.archiveSettingsError ? escapeHtml(state.archiveSettingsError) : "从历史对话中归档的内容会显示在这里。"}</p></div>`;
  return `<main class="archive-settings-page settings-minimal-page">
    ${settingsHeading("已归档对话", "查看已经从历史对话移除的内容；你可以打开、恢复或永久删除。")}
    <section class="archive-settings-card">
      <header class="archive-settings-card-header"><div><h2>归档对话</h2><p>归档不会删除原始资料或导出的文件。</p></div><span class="archive-settings-count">${escapeHtml(String(total))} 个归档</span></header>
      <label class="archive-settings-search">${uiIcon("search")}<input id="archiveSettingsSearch" type="search" value="${escapeHtml(state.archiveSettingsQuery)}" placeholder="搜索已归档对话" autocomplete="off" /></label>
      ${rows}
    </section>
  </main>`;
}

function renderStorageSettings() {
  const general = generalPreferences();
  const workspacePathValue = String(state.workspace?.workspace_path || "").trim();
  const workspacePath = workspacePathValue || "使用应用默认工作区";
  const workspaceDirectory = String(state.workspace?.workspace_directory || "").trim()
    || workspaceDirectoryFromFilePath(workspacePathValue);
  const defaultWorkspace = String(general.directories?.default_workspace || "").trim()
    || workspaceDirectory || "使用应用默认工作区";
  const conversationWorkspace = String(general.directories?.conversation_workspace || "").trim()
    || workspaceDirectory || "使用应用默认对话目录";
  const installedCount = Number(state.localModelMarket?.installed?.length || 0);
  const pathButton = (path) => path && !path.startsWith("使用应用默认") ? `<button type="button" class="settings-inline-link" data-action="reveal-local-path" data-local-path="${escapeHtml(path)}">打开位置</button>` : "";
  const row = (icon, title, detail, path, extra = "") => `<article class="storage-settings-row"><span class="storage-settings-icon">${uiIcon(icon)}</span><div class="storage-settings-copy"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p></div><span class="storage-settings-path" title="${escapeHtml(path)}">${escapeHtml(path)}</span>${extra || pathButton(path)}</article>`;
  return `<main class="storage-settings-page settings-minimal-page">
    ${settingsHeading("存储", "查看 ScanSci 在此设备上的工作区、对话和本地模型数据位置。")}
    <section class="storage-settings-card">
      <header class="storage-settings-card-header"><div><h2>当前数据位置</h2><p>路径保持与现有工作区兼容；调整默认目录请前往常规设置。</p></div><span class="storage-settings-badge">${escapeHtml(`${installedCount} 个本地模型`)}</span></header>
      <div class="storage-settings-list">
        ${row("database", "工作区数据", "资料库索引、研究记录和证据数据。", workspacePath)}
        ${row("folder", "默认工作目录", "新建资料库和研究项目使用的目录。", defaultWorkspace)}
        ${row("message-circle", "对话工作目录", "未绑定资料库的对话文件保存位置。", conversationWorkspace)}
      </div>
      <footer class="storage-settings-footer"><span>${uiIcon("info")}数据不会因软件更新被自动删除。</span><button type="button" class="settings-primary-button" data-action="open-general-directories">管理目录</button></footer>
    </section>
  </main>`;
}

function renderSettings() {
  if (["routing", "document-processing"].includes(state.activeSettings)) state.activeSettings = "defaults";
  if (state.activeSettings === "resources") state.activeSettings = "local-models";
  if (["skills", "mcp", "plugins"].includes(state.activeSettings)) state.activeSettings = "general";
  applyAppearancePreferences();
  document.querySelectorAll(".settings-nav").forEach((button) => button.classList.toggle("is-active", button.dataset.settingsPanel === state.activeSettings));
  const target = byId("settingsContent");
  if (!state.settings) {
    target.innerHTML = '<div class="error-state">设置尚未载入。</div>';
    return;
  }
  let settingsMarkup = "";
  // “resources” is a legacy deep-link.  Model installation now lives in the
  // rebuilt local-models page; never render the retired resource page.
  if (state.activeSettings === "knowledge-preview") settingsMarkup = renderKnowledgeSettingsPreview();
  else if (state.activeSettings === "defaults") settingsMarkup = renderDefaultCapabilitiesSettings();
  else if (state.activeSettings === "models") settingsMarkup = renderModelsSettings();
  else if (state.activeSettings === "local-models") settingsMarkup = renderLocalModelsSettingsPage();
  else if (state.activeSettings === "runtime") settingsMarkup = renderRuntimeSettings();
  else if (state.activeSettings === "document-processing") settingsMarkup = renderDocumentProcessingSettings();
  else if (state.activeSettings === "about") settingsMarkup = renderSoftwareUpdateSettings();
  else if (state.activeSettings === "archive") settingsMarkup = renderArchiveSettings();
  else if (state.activeSettings === "storage") settingsMarkup = renderStorageSettings();
  else settingsMarkup = renderGeneralSettings();
  target.innerHTML = `<div class="settings-surface">${settingsMarkup}</div>`;
  hydrateIcons(target);
  hydrateSettingsSelects(target);
  renderDownloadActivity();
  if (["defaults", "document-processing"].includes(state.activeSettings)) refreshSystemOcrStatus().catch(() => {});
  if (state.activeSettings === "local-models" && !state.localModelMarket?.loaded && !state.localModelMarket?.loading) {
    void refreshLocalModelMarket().catch((error) => toast(error.message, true));
  }
}

function renderExtensions() {
  const target = byId("extensionsContent");
  if (!target) return;
  const skills = mergedExtensionSkills();
  const plugins = (state.settings?.plugins || []).filter((item) => !item.uninstalled);
  const tab = state.activeExtensions;
  const panels = {
    plugins: renderExtensionPlugins(plugins),
    skills: renderExtensionSkills(skills),
    market: renderExtensionMarket(),
  };
  const tabs = [
    ["plugins", "插件", plugins.length],
    ["skills", "技能", skills.length],
    ["market", "市场", state.extensions.marketplace?.length || 0],
  ].map(([id, label, count]) => `<button type="button" class="extension-tab ${tab === id ? "is-active" : ""}" data-extension-tab="${id}" aria-current="${tab === id ? "page" : "false"}"><span>${label}</span><small>${escapeHtml(count)}</small></button>`).join("");
  target.innerHTML = `<div class="extensions-shell">
    ${renderExtensionUpdateSummary()}
    <nav class="extension-tabs" aria-label="插件和技能页面">${tabs}</nav>
    <section class="extension-panel">${panels[tab]}</section>
  </div>${renderExtensionDetail()}`;
  const refreshButton = document.querySelector(".extensions-refresh");
  if (refreshButton) {
    const isLoading = Boolean(state.extensionUpdates.loading);
    refreshButton.disabled = isLoading;
    refreshButton.querySelector(".extensions-refresh-label").textContent = isLoading
      ? "检查中"
      : state.extensionUpdates.error
        ? "重试"
        : "检查更新";
  }
}

function extensionSkillUpdate(id) {
  return (state.extensionUpdates.skills || []).find((item) => String(item.id || "") === String(id || "")) || null;
}

function extensionPluginUpdate(id) {
  return (state.extensionUpdates.plugins || []).find((item) => String(item.id || "") === String(id || "")) || null;
}

function renderExtensionUpdateSummary() {
  const updateCount = (state.extensionUpdates.skills || []).filter((item) => item.available).length
    + (state.extensionUpdates.mcp || []).filter((item) => item.available).length
    + (state.extensionUpdates.plugins || []).filter((item) => item.available).length;
  const checked = state.extensionUpdates.checked_at ? new Date(state.extensionUpdates.checked_at) : null;
  const checkedText = checked && !Number.isNaN(checked.getTime())
    ? `上次检查 ${checked.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`
    : "尚未检查扩展更新";
  const message = state.extensionUpdates.loading
    ? "正在检查 Skill、MCP 和内置插件…"
    : state.extensionUpdates.error
      ? "检查更新失败"
      : updateCount
        ? `发现 ${updateCount} 项可更新内容`
        : checkedText;
  const tone = state.extensionUpdates.error ? "is-error" : updateCount ? "is-available" : "";
  return `<section class="extension-update-summary ${tone}"><span class="extension-update-summary-mark">${uiIcon(state.extensionUpdates.loading ? "refresh" : updateCount ? "sparkles" : "shield-check")}</span><div><strong>${escapeHtml(message)}</strong><small>${state.extensionUpdates.error ? escapeHtml(state.extensionUpdates.error) : "后台只检查版本；下载和替换仍需你确认"}</small></div></section>`;
}

function mergedExtensionSkills() {
  const installed = Array.isArray(state.extensions.skills) ? state.extensions.skills : [];
  const configured = Array.isArray(state.settings?.skills) ? state.settings.skills : [];
  if (!installed.length) return configured.filter((item) => !item?.uninstalled);

  const configuredById = new Map(
    configured.map((item) => [String(item?.id || ""), item]).filter(([id]) => id),
  );
  return installed
    .map((item) => {
      const setting = configuredById.get(String(item?.id || ""));
      return setting
        ? { ...item, enabled: Boolean(setting.enabled), uninstalled: Boolean(setting.uninstalled) }
        : item;
    })
    .filter((item) => !item?.uninstalled);
}

function renderExtensionDetail() {
  const detail = state.extensionDetail;
  if (!detail) return "";
  const records = detail.kind === "skills" ? mergedExtensionSkills() : (state.settings.plugins || []);
  const item = records.find((row) => row.id === detail.id);
  if (!item) return "";
  const title = detail.kind === "skills" ? "Skill" : "插件";
  const operations = Array.isArray(item.skills) && item.skills.length ? `<section class="extension-detail-operations"><span>包含能力</span>${item.skills.map((skill) => `<p>${escapeHtml(skill)}</p>`).join("")}</section>` : "";
  const security = detail.kind === "skills" ? item.security_scan : null;
  const securityCounts = security?.counts || {};
  const securityMarkup = security?.verdict ? `<section class="extension-detail-security is-${escapeHtml(String(security.verdict).toLowerCase())}"><span>${uiIcon("shield-check")}</span><div><small>安装安全检查</small><strong>${escapeHtml(security.verdict)}</strong><p>${escapeHtml(security.scanned_at || "")} · ${escapeHtml(String(Number(securityCounts.critical || 0) + Number(securityCounts.high || 0)))} 高风险 · ${escapeHtml(String(Number(securityCounts.medium || 0)))} 需审查</p></div></section>` : "";
  const remove = item.builtin ? "" : `<button type="button" class="extension-remove" data-action="uninstall-extension" data-extension-kind="${detail.kind}" data-extension-id="${escapeHtml(item.id)}">卸载</button>`;
  return `<div class="extension-detail-backdrop" data-action="close-extension-detail"><section class="extension-detail-card" data-action="extension-detail-content" role="dialog" aria-modal="true" aria-label="${escapeHtml(item.name)} 详情"><header>${extensionRecordMark(detail.kind, item)}<div><span>${title}</span><h2>${escapeHtml(item.name)}</h2></div><button type="button" data-action="close-extension-detail" aria-label="关闭">${uiIcon("x")}</button></header><p>${escapeHtml(item.description || "尚未添加说明")}</p>${operations}${securityMarkup}<footer>${remove}<label class="extension-switch"><input type="checkbox" data-action="toggle-record" data-record-kind="${detail.kind}" data-record-id="${escapeHtml(item.id)}" ${item.enabled ? "checked" : ""} /><span>${item.enabled ? "启用" : "已停用"}</span></label></footer></section></div>`;
}

const BUILTIN_PLUGIN_LOGOS = Object.freeze({
  zotero: "/zotero-logo.svg",
  documents: "/codex-plugin-documents.png",
  pdf: "/codex-plugin-pdf.png",
  spreadsheets: "/codex-plugin-spreadsheets.png",
  presentations: "/codex-plugin-presentations.png",
  latex: "/codex-plugin-latex.png",
});

function extensionRecordMark(kind, item) {
  if (kind === "skills") {
    return `<div class="extension-record-mark skill-mark">${uiIcon("wand")}</div>`;
  }
  const logo = BUILTIN_PLUGIN_LOGOS[String(item?.id || "")];
  if (logo) {
    return `<div class="extension-record-mark plugin-mark has-logo plugin-${escapeHtml(item.id)}"><img src="${logo}" alt="" /></div>`;
  }
  return `<div class="extension-record-mark plugin-mark">${uiIcon("puzzle")}</div>`;
}

function renderExtensionPlugins(plugins) {
  const rows = plugins.length ? plugins.map((plugin) => {
    const runtime = plugin.runtime || {};
    const runtimeText = runtime.ready === false ? (runtime.detail || "运行环境未就绪") : (runtime.detail || "可用");
    const update = extensionPluginUpdate(plugin.id)
      || (plugin.builtin && state.update?.available
        ? {
          available: true,
          message: "随应用更新",
        }
        : null);
    const updateMarkup = update?.available
      ? `<button type="button" class="extension-update-button" data-action="${update.can_install ? "install-app-update" : "check-app-update"}">应用更新</button>`
      : plugin.builtin ? `<span class="extension-status is-bundled">随应用更新</span>` : "";
    return `<article class="extension-record plugin-record"><button type="button" class="extension-record-main" data-action="open-extension-detail" data-extension-kind="plugins" data-extension-id="${escapeHtml(plugin.id)}">${extensionRecordMark("plugins", plugin)}<div class="extension-record-copy"><div class="extension-record-title"><h3>${escapeHtml(plugin.name)}</h3><span>${plugin.builtin ? "内置" : "插件"}</span></div><p>${escapeHtml(plugin.description || "尚未添加说明")}</p></div></button><div class="extension-record-actions"><span class="extension-status ${runtime.ready === false ? "is-missing" : "is-ready"}">${escapeHtml(runtimeText)}</span>${updateMarkup}<label class="extension-switch"><input type="checkbox" data-action="toggle-record" data-record-kind="plugins" data-record-id="${escapeHtml(plugin.id)}" ${plugin.enabled ? "checked" : ""} /><span>${plugin.enabled ? "启用" : "已停用"}</span></label></div></article>`;
  }).join("") : `<div class="extension-empty"><span>${uiIcon("puzzle")}</span><strong>还没有插件来源</strong><p>登记受信任的本地路径或远程来源后，可在这里统一启停和维护。</p></div>`;
  return `<div class="extension-panel-summary"><p>内置办公与 LaTeX 插件由 Pi 直接调用；它们随 ScanSci 应用包安全更新，MCP 服务器在左侧独立管理。</p><span class="panel-count">${plugins.length} 项</span></div>
    <section class="extension-record-list">${rows}</section>
    <form class="extension-form plugin-form" id="extensionPluginForm"><div class="extension-form-copy"><strong>登记插件来源</strong><span>仅保存元数据，不会自动启动或执行插件。</span></div><label><span>名称</span><input name="plugin-name" required maxlength="100" placeholder="例如：文献管理连接器" /></label><label><span>来源</span><input name="plugin-source" required maxlength="500" placeholder="本地路径或受信任的插件地址" /></label><label class="extension-form-wide"><span>说明（可选）</span><input name="plugin-description" maxlength="400" placeholder="它会为研究流程提供什么能力？" /></label><button type="submit" class="extension-primary">登记插件</button></form>`;
}

function renderExtensionSkills(skills) {
  const rows = skills.length ? skills.map((skill) => {
    const status = skill.available ? "可用" : "缺少文件";
    const update = extensionSkillUpdate(skill.id);
    const updateMarkup = update?.available
      ? `<button type="button" class="extension-update-button" data-action="update-skill" data-extension-id="${escapeHtml(skill.id)}">更新</button>`
      : skill.builtin ? `<span class="extension-status is-bundled">随应用更新</span>` : update?.state === "manual" ? `<span class="extension-status is-manual">手动来源</span>` : update?.state === "error" ? `<span class="extension-status is-missing">检查失败</span>` : "";
    return `<article class="extension-record skill-record"><button type="button" class="extension-record-main" data-action="open-extension-detail" data-extension-kind="skills" data-extension-id="${escapeHtml(skill.id)}">${extensionRecordMark("skills", skill)}<div class="extension-record-copy"><div class="extension-record-title"><h3>${escapeHtml(skill.name || skill.id)}</h3></div><p>${escapeHtml(skill.description || "尚未添加说明")}</p></div></button><div class="extension-record-actions"><span class="extension-status ${skill.available ? "is-ready" : "is-missing"}">${status}</span>${updateMarkup}<label class="extension-switch"><input type="checkbox" data-action="toggle-record" data-record-kind="skills" data-record-id="${escapeHtml(skill.id)}" ${skill.enabled ? "checked" : ""} /><span>${skill.enabled ? "启用" : "已停用"}</span></label></div></article>`;
  }).join("") : `<div class="extension-empty"><span>${uiIcon("wand")}</span><strong>还没有可用技能</strong><p>从本地文件夹、Git 仓库、压缩包或市场中安装一个 Skill。</p></div>`;
  return `<section class="extension-record-list skill-list">${rows}</section>`;
}

function renderExtensionMarket() {
  const query = state.marketplaceQuery.trim().toLowerCase();
  const allItems = state.extensions.marketplace || [];
  const items = allItems.filter((item) => !query || [item.name, item.slug, item.source].join(" ").toLowerCase().includes(query));
  const sources = [...new Set(allItems.map((item) => item.source).filter(Boolean))].slice(0, 8);
  const sourceChips = sources.map((source) => `<span>${escapeHtml(source)}</span>`).join("");
  const empty = state.extensions.marketplaceLoaded ? `<div class="extension-empty market-empty"><span>${uiIcon("search")}</span><strong>没有匹配的市场技能</strong><p>换一个关键词，或点击刷新市场以同步公开目录。</p></div>` : `<div class="extension-empty market-empty"><span>${uiIcon("refresh")}</span><strong>正在连接技能市场</strong><p>首次加载会同步公开目录；之后可手动刷新。</p></div>`;
  const cards = items.length ? items.map((item) => `<article class="market-card"><div class="market-card-top"><span class="market-orb">${uiIcon("wand")}</span><a href="${escapeHtml(safeExternalUrl(item.url))}" target="_blank" rel="noopener" aria-label="在浏览器打开 ${escapeHtml(item.name || item.slug)}">${uiIcon("arrow-up-right")}</a></div><h3>${escapeHtml(item.name || item.slug)}</h3><p>${escapeHtml(item.source || "公开来源")}</p><div class="market-card-meta"><span>${item.installs ? `${Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(item.installs)} 安装` : "待同步安装数"}</span><span>${escapeHtml(item.sourceType || "Skill")}</span></div><button type="button" class="market-install" data-action="install-market-skill" data-market-skill-id="${escapeHtml(item.id)}">安装到技能库 <b>${uiIcon("plus")}</b></button></article>`).join("") : empty;
  return `<div class="market-status-row"><span class="market-connection ${state.extensions.marketplaceOffline ? "is-offline" : ""}">${state.extensions.marketplaceOffline ? "离线示例" : "市场已连接"}</span></div>
    <section class="market-toolbar"><label class="market-search">${uiIcon("search")}<input id="extensionsMarketSearch" type="search" value="${escapeHtml(state.marketplaceQuery)}" placeholder="搜索技能名称、描述或来源" autocomplete="off" /></label><div class="market-source-chips"><b>来源</b><span class="is-selected">全部</span>${sourceChips}</div></section>
    <section class="market-grid">${cards}</section>`;
}

function safeExternalUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["https:", "http:"].includes(url.protocol) ? url.href : "https://skills.sh";
  } catch {
    return "https://skills.sh";
  }
}

async function refreshExtensions({ marketOnly = false, quiet = false, includeMarket = marketOnly || state.activeExtensions === "market" } = {}) {
  const requests = [];
  if (includeMarket) requests.push(request("/api/skills/market"));
  if (!marketOnly) requests.push(request("/api/skills"));
  const responses = await Promise.all(requests);
  const market = includeMarket ? responses.shift() : null;
  if (market) {
    state.extensions.marketplace = market.items || [];
    state.extensions.marketplaceOffline = Boolean(market.offline);
    state.extensions.marketplaceLoaded = true;
  }
  if (!marketOnly) {
    const installed = responses[0];
    state.extensions.skills = installed.skills || [];
    state.extensions.libraryPath = installed.library_path || "";
  }
  if (state.activeView === "extensions") renderExtensions();
  if (!quiet && market) toast(market.offline ? "市场暂不可用，正在显示可安装示例" : "市场已刷新");
}

async function refreshExtensionUpdates({ quiet = false } = {}) {
  if (state.extensionUpdates.loading) return;
  state.extensionUpdates = { ...state.extensionUpdates, loading: true, error: "" };
  if (state.activeView === "extensions") renderExtensions();
  try {
    const payload = await request("/api/extension-updates");
    const skillPayload = payload.skills || {};
    const mcpPayload = payload.mcp || {};
    state.extensionUpdates = {
      checked_at: payload.checked_at || "",
      loading: false,
      skills: skillPayload.skills || [],
      mcp: mcpPayload.updates || [],
      plugins: payload.plugins || [],
      app: payload.app || null,
      error: "",
    };
    if (state.activeView === "extensions") renderExtensions();
    if (!quiet) {
      const count = (state.extensionUpdates.skills || []).filter((item) => item.available).length
        + (state.extensionUpdates.mcp || []).filter((item) => item.available).length
        + (state.extensionUpdates.plugins || []).filter((item) => item.available).length;
      toast(count ? `发现 ${count} 项可更新内容` : "Skill、MCP 和插件均已是最新");
    }
    return payload;
  } catch (error) {
    state.extensionUpdates = { ...state.extensionUpdates, loading: false, error: error.message || "暂时无法检查更新" };
    if (state.activeView === "extensions") renderExtensions();
    if (!quiet) throw error;
    return null;
  }
}

const skillSecurityVerdictMeta = Object.freeze({
  SAFE: { label: "可以安装", summary: "未发现阻断项", icon: "shield-check" },
  REVIEW: { label: "需要审查", summary: "发现需要人工判断的风险", icon: "triangle-alert" },
  BLOCKED: { label: "已阻止", summary: "发现高风险或严重问题", icon: "lock-keyhole" },
});

function skillSecurityFindingMarkup(finding) {
  const location = finding.path ? `${finding.path}${finding.line ? `:${finding.line}` : ""}` : "Skill 包";
  const evidence = finding.evidence ? `<code>${escapeHtml(finding.evidence)}</code>` : "";
  return `<li class="skill-security-finding is-${escapeHtml(String(finding.severity || "info").toLowerCase())}"><span>${escapeHtml(finding.severity || "INFO")}</span><div><strong>${escapeHtml(finding.title || "安全发现")}</strong><p>${escapeHtml(finding.detail || "")}</p><small>${escapeHtml(location)}</small>${evidence}</div></li>`;
}

function renderSkillSecurityReview() {
  const pending = state.skillInstallReview;
  const target = byId("skillSecurityContent");
  const installButton = byId("skillSecurityInstall");
  if (!pending || !target || !installButton) return;
  const scan = pending.scan || {};
  const verdict = String(scan.verdict || "BLOCKED").toUpperCase();
  const meta = skillSecurityVerdictMeta[verdict] || skillSecurityVerdictMeta.BLOCKED;
  const isUpdate = pending.operation === "update";
  const scanners = (scan.scanners || []).map((scanner) => {
    const status = String(scanner.status || "FAIL").toUpperCase();
    const icon = status === "PASS" ? "check" : status === "WARN" ? "triangle-alert" : "x";
    return `<li class="is-${status.toLowerCase()}"><span>${uiIcon(icon)}</span><div><strong>${escapeHtml(scanner.name || scanner.id || "Scanner")}</strong><small>${scanner.finding_count ? `${escapeHtml(String(scanner.finding_count))} 项发现` : "通过"}</small></div><b>${escapeHtml(status)}</b></li>`;
  }).join("");
  const findings = (scan.findings || []).map(skillSecurityFindingMarkup).join("");
  const packageNames = (scan.packages || []).map((item) => escapeHtml(item.name || "Skill")).join("、");
  const counts = scan.counts || {};
  const findingSummary = `${Number(counts.critical || 0) + Number(counts.high || 0)} 高风险 · ${Number(counts.medium || 0)} 需审查 · ${Number(counts.low || 0)} 低风险`;
  const acknowledgement = verdict === "REVIEW"
    ? `<label class="skill-security-ack"><input type="checkbox" id="skillSecurityAcknowledge" /><span><strong>我已阅读全部风险并确认${isUpdate ? "更新" : "安装"}</strong><small>我理解这个 Skill 可能执行动态代码或包含难以自动审查的内容。</small></span></label>`
    : "";
  const actionLabel = isUpdate ? (verdict === "REVIEW" ? "理解风险并更新" : "确认更新") : (verdict === "REVIEW" ? "理解风险并安装" : "确认安装");
  target.innerHTML = `<section class="skill-security-verdict is-${verdict.toLowerCase()}"><span>${uiIcon(meta.icon)}</span><div><small>${escapeHtml(verdict)}</small><h3>${escapeHtml(isUpdate ? meta.label.replace("安装", "更新") : meta.label)}</h3><p>${escapeHtml(scan.recommendation || meta.summary)}</p></div><b>${escapeHtml(findingSummary)}</b></section>
    <dl class="skill-security-provenance"><div><dt>来源</dt><dd title="${escapeHtml(scan.source_label || "")}">${escapeHtml(scan.source_label || "未知来源")}</dd></div><div><dt>隔离快照</dt><dd><code>${escapeHtml(String(scan.fingerprint || "").slice(0, 26))}…</code></dd></div><div><dt>内容</dt><dd>${escapeHtml(String(scan.package_count || 0))} 个 Skill · ${escapeHtml(String(scan.file_count || 0))} 个文件 · ${escapeHtml(formatFileSize(scan.byte_count || 0))}${packageNames ? ` · ${packageNames}` : ""}</dd></div></dl>
    <section class="skill-security-scanners"><header><strong>内置扫描器</strong><span>不会运行 Skill 中的任何代码</span></header><ul>${scanners}</ul></section>
    ${findings ? `<details class="skill-security-findings" ${verdict !== "SAFE" ? "open" : ""}><summary><span>安全发现</span><b>${escapeHtml(String((scan.findings || []).length))}</b></summary><ol>${findings}</ol></details>` : '<div class="skill-security-clean"><span>✓</span><p><strong>静态检查通过</strong><small>仍请确认来源可信，并只授予任务需要的权限。</small></p></div>'}
    ${acknowledgement}`;
  installButton.hidden = verdict === "BLOCKED";
  installButton.disabled = verdict === "BLOCKED" || verdict === "REVIEW";
  installButton.textContent = actionLabel;
  const title = byId("skillSecurityTitle");
  if (title) title.textContent = isUpdate ? "更新前安全检查" : "安装前安全检查";
  const description = byId("skillSecurityDescription");
  if (description) description.textContent = isUpdate ? "扫描远程新版本；确认后只会替换这份已经检查的内容。" : "扫描的是隔离快照；确认后只会安装这份已经检查的内容。";
  const expiry = new Date(pending.expires_at || "");
  byId("skillSecurityExpiry").textContent = Number.isNaN(expiry.getTime()) ? "隔离快照会自动清理" : `隔离快照 ${expiry.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} 失效`;
}

async function installSkill(sourceType, source) {
  if (state.skillInstallBusy) {
    toast("正在检查另一个 Skill，请稍候");
    return;
  }
  state.skillInstallBusy = true;
  toast("正在隔离并检查 Skill…");
  try {
    const result = await request("/api/skills/scan", { method: "POST", body: JSON.stringify({ source_type: sourceType, source }) });
    state.skillInstallReview = { ...result, sourceType, source, operation: "install" };
    renderSkillSecurityReview();
    const dialog = byId("skillSecurityDialog");
    if (dialog && !dialog.open) dialog.showModal();
    const verdict = String(result.scan?.verdict || "BLOCKED").toUpperCase();
    toast(verdict === "BLOCKED" ? "安全检查已阻止安装" : verdict === "REVIEW" ? "检查完成，请人工审查风险" : "安全检查通过，请确认安装", verdict === "BLOCKED");
  } finally {
    state.skillInstallBusy = false;
  }
}

async function scanSkillUpdate(recordId) {
  if (state.skillInstallBusy) {
    toast("正在检查另一个 Skill，请稍候");
    return;
  }
  state.skillInstallBusy = true;
  toast("正在获取 Skill 新版本并检查…");
  try {
    const result = await request("/api/skills/update/scan", { method: "POST", body: JSON.stringify({ record_id: recordId }) });
    state.skillInstallReview = { ...result, operation: "update", recordId };
    renderSkillSecurityReview();
    const dialog = byId("skillSecurityDialog");
    if (dialog && !dialog.open) dialog.showModal();
    const verdict = String(result.scan?.verdict || "BLOCKED").toUpperCase();
    toast(verdict === "BLOCKED" ? "新版本未通过安全检查" : verdict === "REVIEW" ? "更新版本需要人工审查" : "新版本检查通过，请确认更新", verdict === "BLOCKED");
  } finally {
    state.skillInstallBusy = false;
  }
}

function closeSkillSecurityReview({ discard = true } = {}) {
  const pending = state.skillInstallReview;
  state.skillInstallReview = null;
  const dialog = byId("skillSecurityDialog");
  if (dialog?.open) dialog.close();
  if (discard && pending?.scan_id) {
    request("/api/skills/scan/cancel", { method: "POST", body: JSON.stringify({ scan_id: pending.scan_id }) }).catch(() => {});
  }
}

async function confirmSkillInstall() {
  const pending = state.skillInstallReview;
  const button = byId("skillSecurityInstall");
  if (!pending || !button) return;
  const verdict = String(pending.scan?.verdict || "BLOCKED").toUpperCase();
  const acknowledgeRisk = Boolean(byId("skillSecurityAcknowledge")?.checked);
  if (verdict === "BLOCKED") return;
  if (verdict === "REVIEW" && !acknowledgeRisk) {
    toast("请先阅读风险并勾选确认", true);
    return;
  }
  button.disabled = true;
  const isUpdate = pending.operation === "update";
  button.textContent = isUpdate ? "正在更新…" : "正在安装…";
  try {
    const requestOptions = {
      method: "POST",
      body: JSON.stringify({ scan_id: pending.scan_id, decision: isUpdate ? "update" : "install", acknowledge_risk: acknowledgeRisk }),
    };
    const result = isUpdate
      ? await request("/api/skills/update", requestOptions)
      : await request("/api/skills/install", requestOptions);
    state.settings = result.settings || state.settings;
    state.extensions.skills = result.skills || [];
    closeSkillSecurityReview({ discard: false });
    byId("skillInstallForm")?.reset();
    renderModelSelectors();
    renderExtensions();
    if (isUpdate) {
      await refreshExtensionUpdates({ quiet: true });
      toast(`已安全更新 ${result.updated?.name || "Skill"}`);
    } else {
      const count = (result.installed || []).length;
      toast(count ? `已安全安装 ${count} 个 Skill` : "Skill 已安全安装");
    }
  } catch (error) {
    renderSkillSecurityReview();
    throw error;
  }
}

async function installMarketSkill(skillId) {
  await installSkill("marketplace", skillId);
}

function settingsHeading(title, description) {
  return `<header class="settings-page-heading settings-heading"><div><h1>${escapeHtml(title)}</h1><p>${escapeHtml(description)}</p></div><span class="save-indicator">${escapeHtml(copy("localSaved"))}</span></header>`;
}

function renderGeneralSettings() {
  const { provider, model } = activeModel();
  const appearance = appearancePreferences();
  const general = generalPreferences();
  const applicationWorkspaceDirectory = String(state.workspace?.workspace_directory || "").trim()
    || workspaceDirectoryFromFilePath(state.workspace?.workspace_path);
  const defaultWorkspacePlaceholder = applicationWorkspaceDirectory || copy("defaultWorkspacePlaceholder");
  const conversationWorkspacePlaceholder = applicationWorkspaceDirectory || copy("conversationWorkspacePlaceholder");
  const tab = ["appearance", "conversation", "directories"].includes(state.generalSettingsTab)
    ? state.generalSettingsTab
    : "appearance";
  state.generalSettingsTab = tab;
  const option = (value, label, selected) => `<option value="${escapeHtml(value)}" ${selected === value ? "selected" : ""}>${escapeHtml(label)}</option>`;
  const accentHex = {
    jade: "#1F7D4E",
    ocean: "#1875B6",
    plum: "#7652AD",
    amber: "#BB7518",
  };
  const accentOption = (value) => `<option value="${escapeHtml(value)}" data-accent-color="${accentHex[value]}" ${appearance.accent === value ? "selected" : ""}>${accentHex[value]}</option>`;
  const modelLabel = provider
    ? uniqueModelProviderLabel(provider.name, model?.name || "")
    : (appearance.locale === "en" ? "Not selected" : "未选择");
  const tabs = [
    ["appearance", copy("generalTabAppearance"), "settings"],
    ["conversation", copy("generalTabConversation"), "message-circle"],
    ["directories", copy("generalTabDirectories"), "folder"],
  ].map(([id, label, icon]) => `<button type="button" class="settings-tab ${tab === id ? "is-active" : ""}" data-action="switch-general-tab" data-settings-tab="${id}" role="tab" aria-selected="${tab === id ? "true" : "false"}">${uiIcon(icon)}<span>${label}</span></button>`).join("");
  const appearanceContent = `<form id="generalPreferencesForm" class="settings-minimal-form">
      <section class="settings-minimal-section"><h2>${escapeHtml(copy("appearanceTitle"))}</h2>
        <label class="settings-row"><span><strong>${escapeHtml(copy("interfaceLanguage"))}</strong><small>${escapeHtml(copy("interfaceLanguageHint"))}</small></span><select name="appearance-locale">${option("zh-CN", "简体中文", appearance.locale)}${option("en", "English", appearance.locale)}</select></label>
        <label class="settings-row"><span><strong>${escapeHtml(copy("appearanceTheme"))}</strong><small>${escapeHtml(copy("appearanceThemeHint"))}</small></span><select name="appearance-theme">${option("system", copy("system"), appearance.theme)}${option("light", copy("light"), appearance.theme)}${option("dark", copy("dark"), appearance.theme)}</select></label>
        <label class="settings-row"><span><strong>${escapeHtml(copy("accentColor"))}</strong><small>${escapeHtml(copy("accentColorHint"))}</small></span><select name="appearance-accent">${["jade", "ocean", "plum", "amber"].map(accentOption).join("")}</select></label>
        <label class="settings-row"><span><strong>${escapeHtml(copy("fontScale"))}</strong><small>${escapeHtml(copy("fontScaleHint"))}</small></span><select name="appearance-font-scale">${option("small", copy("fontSmall"), appearance.font_scale)}${option("medium", copy("fontMedium"), appearance.font_scale)}${option("large", copy("fontLarge"), appearance.font_scale)}</select></label>
      </section>
      <footer class="settings-minimal-actions"><span>${escapeHtml(copy("localSaved"))}</span><button type="submit" class="save-button">${escapeHtml(copy("saveAppearance"))}</button></footer>
    </form>
    <section class="settings-minimal-section settings-info-section"><h2>${escapeHtml(copy("currentWorkspace"))}</h2>
      <div class="settings-row is-static"><span><strong>${escapeHtml(state.notebook?.title || copy("noWorkspace"))}</strong><small>${escapeHtml(copy("currentModel"))}</small></span><span class="settings-row-value">${escapeHtml(modelLabel)}</span></div>
    </section>`;
  const toggleControl = (name, checked, label) => `<label class="settings-switch-control"><input type="checkbox" data-general-toggle="${name}" aria-label="${escapeHtml(label)}" ${checked ? "checked" : ""} /><span aria-hidden="true"></span></label>`;
  const conversationContent = `<form id="generalConversationForm" class="settings-minimal-form">
      <section class="settings-minimal-section"><h2>${escapeHtml(copy("generalTabConversation"))}</h2><p class="settings-section-intro">${escapeHtml(copy("conversationDescription"))}</p>
        <label class="settings-row"><span><strong>${escapeHtml(copy("sendShortcut"))}</strong><small>${escapeHtml(copy("sendShortcutHint"))}</small></span><select name="conversation-send-shortcut">${option("enter", copy("sendEnter"), general.conversation.send_shortcut)}${option("shift-enter", copy("sendShiftEnter"), general.conversation.send_shortcut)}</select></label>
        <label class="settings-row"><span><strong>${escapeHtml(copy("completionNotifications"))}</strong><small>${escapeHtml(copy("completionNotificationsHint"))}</small></span>${toggleControl("completion_notifications", general.conversation.completion_notifications, copy("completionNotifications"))}</label>
        <label class="settings-row"><span><strong>${escapeHtml(copy("agentCompletionNotifications"))}</strong><small>${escapeHtml(copy("agentCompletionNotificationsHint"))}</small></span>${toggleControl("agent_completion_notifications", general.conversation.agent_completion_notifications, copy("agentCompletionNotifications"))}</label>
        <label class="settings-row"><span><strong>${escapeHtml(copy("subagentCompletionNotifications"))}</strong><small>${escapeHtml(copy("subagentCompletionNotificationsHint"))}</small></span>${toggleControl("subagent_completion_notifications", general.conversation.subagent_completion_notifications, copy("subagentCompletionNotifications"))}</label>
      </section>
    </form>`;
  const directoryControl = (name, value, placeholder, setting) => {
    const displayValue = String(value || "").trim() || placeholder;
    return `<div class="settings-path-control"><input name="${name}" value="${escapeHtml(displayValue)}" placeholder="${escapeHtml(placeholder)}" autocomplete="off" /><div class="settings-path-actions"><button type="button" class="settings-inline-link" data-action="choose-general-directory" data-directory-setting="${escapeHtml(setting)}">${escapeHtml(copy("chooseDirectory"))}</button><button type="button" class="settings-inline-link" data-action="reset-general-directory" data-directory-setting="${escapeHtml(setting)}">${escapeHtml(copy("resetDefault"))}</button></div></div>`;
  };
  const directoriesContent = `<form id="generalDirectoriesForm" class="settings-minimal-form">
      <section class="settings-minimal-section"><h2>${escapeHtml(copy("directoriesTitle"))}</h2><p class="settings-section-intro">${escapeHtml(copy("directoriesDescription"))}</p>
        <label class="settings-row settings-row-path"><span><strong>${escapeHtml(copy("defaultWorkspace"))}</strong><small>${escapeHtml(copy("defaultWorkspaceHint"))}</small></span>${directoryControl("directory-default-workspace", general.directories.default_workspace, defaultWorkspacePlaceholder, "default_workspace")}</label>
        <label class="settings-row settings-row-path"><span><strong>${escapeHtml(copy("conversationWorkspace"))}</strong><small>${escapeHtml(copy("conversationWorkspaceHint"))}</small></span>${directoryControl("directory-conversation-workspace", general.directories.conversation_workspace, conversationWorkspacePlaceholder, "conversation_workspace")}</label>
        <label class="settings-row settings-row-path"><span><strong>${escapeHtml(copy("modelCacheDirectory"))}</strong><small>${escapeHtml(copy("modelCacheDirectoryHint"))}</small></span>${directoryControl("directory-model-cache", general.directories.model_cache, copy("storageDirectoryPlaceholder"), "model_cache")}</label>
        <label class="settings-row settings-row-path"><span><strong>${escapeHtml(copy("localRuntimeDirectory"))}</strong><small>${escapeHtml(copy("localRuntimeDirectoryHint"))}</small></span>${directoryControl("directory-local-runtime", general.directories.local_runtime, copy("storageDirectoryPlaceholder"), "local_runtime")}</label>
        <label class="settings-row settings-row-path"><span><strong>${escapeHtml(copy("vectorIndexDirectory"))}</strong><small>${escapeHtml(copy("vectorIndexDirectoryHint"))}</small></span>${directoryControl("directory-vector-index", general.directories.vector_index, copy("storageDirectoryPlaceholder"), "vector_index")}</label>
        <p class="settings-section-note">${escapeHtml(copy("storageDirectoryRestartHint"))}</p>
      </section>
    </form>`;
  const configurableTabContent = {
    conversation: conversationContent,
    directories: directoriesContent,
  }[tab] || appearanceContent;
  return `<section class="settings-minimal-page general-settings-page">
    <header class="settings-page-heading"><div><h1>${escapeHtml(copy("generalTitle"))}</h1><p>${escapeHtml(copy("generalDescription"))}</p></div><span class="save-indicator">${escapeHtml(copy("generalAutoApply"))}</span></header>
    <nav class="settings-tab-strip" aria-label="${escapeHtml(copy("generalTabsLabel"))}" role="tablist">${tabs}</nav>
    <div class="settings-tab-panel" role="tabpanel">${tab === "appearance" ? appearanceContent : configurableTabContent}</div>
  </section>`;
}

function updateStatusCopy(update = state.update || {}) {
  const status = ["checking", "installing", "restarting"].includes(update.state) ? update.state : (update.available ? "available" : update.state || "idle");
  if (status === "checking") return "正在获取最新版本信息…";
  if (status === "installing") return "正在下载并校验更新包…";
  if (status === "restarting") return "更新已准备好，正在重启 ScanSci…";
  if (update.available) return `发现新版本 v${update.latest_version || "—"}，已在右上角提示。`;
  if (status === "error") return update.error === "missing_release_public_key" ? update.message : "暂时无法连接更新服务，可稍后再试。";
  if (update.checked_at) return "当前已是最新版本。";
  return "尚未检查更新。";
}

function renderSoftwareUpdateSettings() {
  const update = state.update || {};
  const isBusy = ["checking", "installing", "restarting"].includes(update.state);
  const hasUpdate = Boolean(update.available);
  const version = update.current_version || "—";
  const latestVersion = update.latest_version || version;
  const checkAction = hasUpdate && update.can_install ? "install-app-update" : "check-app-update";
  const checkLabel = isBusy ? (update.state === "installing" ? "正在更新" : "检查中") : (hasUpdate && update.can_install ? "立即更新" : "检查更新");
  const notes = (update.release_notes || []).flatMap((section) => section.items || []).slice(0, 2);
  const releaseSummary = hasUpdate
    ? (notes.length ? notes.join(" · ") : "新版本的更新说明已准备就绪。")
    : "发现新版本后，可在这里查看发布说明。";
  const checkedAt = formatUpdateTime(update.checked_at).replace(/^检查于/, "") || "尚未检查";
  return `<main class="software-update-page settings-minimal-page">
    <header class="settings-page-heading">
      <div><h1>软件更新</h1><p>管理 ScanSci 的版本、更新通道与自动检查。</p></div>
      <span class="save-indicator">${escapeHtml(checkedAt)}</span>
    </header>
    <section class="software-update-card software-update-summary">
      <header>
        <div class="software-update-heading-copy"><span class="settings-overline">SCANSCI DESKTOP</span><h2>保持应用为最新版本</h2><p>${escapeHtml(updateStatusCopy(update))}</p></div>
        <img class="software-update-mark" src="/scansci-mark.png" alt="ScanSci" />
      </header>
      <div class="software-update-status-row">
        <div><strong>当前版本</strong><span class="software-update-version">v${escapeHtml(version)}</span></div>
        <button type="button" class="settings-primary-button software-update-check" data-action="${checkAction}" ${isBusy ? "disabled" : ""}>${checkLabel}</button>
      </div>
    </section>
    <section class="software-update-card software-update-details" aria-label="更新设置">
      <div class="software-update-row"><div><strong>自动检查更新</strong><p>启动 ScanSci 时在后台检查稳定版更新。</p></div><label class="software-update-switch"><input type="checkbox" data-update-auto-check ${state.autoCheckUpdates ? "checked" : ""} /><span aria-hidden="true"></span></label></div>
      <div class="software-update-row"><div><strong>更新通道</strong><p>仅接收经过验证的稳定版本。</p></div><span class="software-update-value">稳定版</span></div>
      <div class="software-update-row"><div><strong>最新版本</strong><p>${escapeHtml(releaseSummary)}</p></div><span class="software-update-value ${hasUpdate ? "is-update" : ""}">${hasUpdate ? `v${escapeHtml(latestVersion)}` : `v${escapeHtml(version)}`}</span></div>
    </section>
  </main>`;
}

function selectedProvider() {
  const providers = state.settings?.providers || [];
  const selected = providers.find((provider) => provider.id === state.selectedProviderId);
  if (selected && !isLocalModelProvider(selected)) return selected;
  return providers.find((provider) => !isLocalModelProvider(provider)) || selected || providers[0];
}

function verifiedLocalServiceModels() {
  const models = new Map();
  const installed = state.localModelMarket?.installed || [];
  const snapshots = new Map(installed.map((item) => [String(item.id), item]));
  const ready = (item) => Boolean(item?.ready) && item.runtime_compatible !== false;
  const add = (model) => {
    const id = String(model.model_id || model.id || "");
    if (id && !models.has(id)) models.set(id, { ...model, id, group: "本地模型" });
  };
  for (const provider of state.settings?.providers || []) {
    if (!isLocalModelProvider(provider) || provider.id === "local-evidence" || !provider.enabled) continue;
    for (const model of provider.models || []) {
      const snapshot = snapshots.get(String(model.id));
      const health = modelHealthEntry(provider.id, model.id);
      if (model.available === false || (snapshot && !ready(snapshot))) continue;
      if (health ? ["ready", "connected", "configured"].includes(health.status) : ready(snapshot)) add(model);
    }
  }
  for (const model of state.settings?.local_models || []) {
    if (model.runtime === "builtin" || model.enabled === false || model.runtime_compatible === false) continue;
    if (ready(snapshots.get(String(model.model_id)))) add(model);
  }
  for (const item of installed) {
    if (!ready(item)) continue;
    const capabilities = Array.isArray(item.capabilities) ? item.capabilities
      : ["embedding", "reranking", "vision", "audio", "image"].includes(item.kind) ? [item.kind] : [];
    add({ ...item, capabilities });
  }
  return [...models.values()];
}

function localServiceModelsMarkup(models, query = "") {
  const visible = models.filter((model) => !query || `${model.name || ""} ${model.id} 本地模型`.toLocaleLowerCase().includes(query));
  if (!visible.length) return "";
  return `<section class="cherry-model-group" data-local-model-group><header><strong>本地模型</strong></header>${visible.map((model) => `<article class="cherry-model-row"><span class="cherry-model-mark">${uiIcon("cpu")}</span><div class="cherry-model-copy"><button type="button" data-action="open-settings" data-settings-panel="local-models">${escapeHtml(model.name || model.id)}</button><small>${escapeHtml(model.id)}${model.context_window ? ` · ${escapeHtml(model.context_window)}` : ""}</small></div>${modelCapabilityChips(model, -1, { readOnly: true })}<button type="button" class="cherry-icon-button" data-action="open-settings" data-settings-panel="local-models" aria-label="管理本地模型">${uiIcon("settings")}</button></article>`).join("")}</section>`;
}

function renderModelsSettings() {
  const provider = selectedProvider();
  if (!provider) return `${settingsHeading("模型服务", "添加并选择用于研究对话的模型提供商。")}<div class="empty-records">还没有可用提供商。</div>`;
  state.selectedProviderId = provider.id;
  const allProviders = state.settings.providers || [];
  const localServiceModels = provider.id === "scansci-managed" ? verifiedLocalServiceModels() : [];
  const query = state.providerQuery.trim().toLocaleLowerCase();
  const matchesQuery = (item) => !query || `${item.name} ${item.category || ""} ${item.summary || ""}`.toLocaleLowerCase().includes(query);
  const catalogProviders = allProviders.filter((item) => !isLocalModelProvider(item) && matchesQuery(item));
  // ON/OFF is the single source of truth for the provider's enabled state in
  // the catalog. A second health/status label made the row wrap and repeated
  // the same enabled/disabled meaning in a less scannable form.
  const providerRow = (item, sortable = item.kind !== "local") => `<article class="cherry-provider-item ${item.id === provider.id ? "is-active" : ""} ${item.enabled ? "is-enabled" : "is-disabled"}" ${sortable ? `draggable="true" data-provider-drag-id="${escapeHtml(item.id)}"` : ""}><span class="cherry-provider-drag" aria-hidden="true" title="拖拽排序">${uiIcon("grip-vertical")}</span><button type="button" class="cherry-provider-button" data-action="select-provider" data-provider-id="${escapeHtml(item.id)}" aria-current="${item.id === provider.id ? "page" : "false"}">${providerLogo(item)}<span>${escapeHtml(item.name)}</span></button><button type="button" class="cherry-provider-status" data-action="toggle-provider-enabled" data-provider-id="${escapeHtml(item.id)}" aria-pressed="${item.enabled ? "true" : "false"}" aria-label="${item.enabled ? "停用服务商" : "启用服务商"}" title="${item.enabled ? "停用服务商" : "启用服务商"}"><span aria-hidden="true"></span></button></article>`;
  const providerItems = catalogProviders.map((item) => providerRow(item, !query)).join("");
  // Local runtime guidance belongs to the dedicated Local Models page.
  const localRuntimeNotice = "";
  const groupedModels = new Map();
  const modelQuery = state.modelQuery.trim().toLocaleLowerCase();
  provider.models.forEach((model, index) => {
    if (modelQuery && !`${model.name || ""} ${model.id || ""} ${model.group || ""}`.toLocaleLowerCase().includes(modelQuery)) return;
    const group = String(model.group || "默认模型");
    if (!groupedModels.has(group)) groupedModels.set(group, []);
    groupedModels.get(group).push({ model, index });
  });
  const modelRows = [...groupedModels.entries()].map(([group, models]) => `<section class="cherry-model-group"><header><strong>${escapeHtml(group)}</strong></header>${models.map(({ model, index }) => {
    const shown = model.show_in_composer !== false;
    const name = model.name || model.id;
    return `<article class="cherry-model-row ${model.available === false ? "is-unavailable" : ""} ${state.editingModelIndex === index ? "is-selected" : ""}"><span class="cherry-model-mark">${escapeHtml((group || "M").slice(0, 1).toUpperCase())}</span><div class="cherry-model-copy"><button type="button" data-action="edit-model" data-model-index="${index}">${escapeHtml(name)}</button><small>${escapeHtml(model.id)}${model.context_window ? ` · ${escapeHtml(model.context_window)}` : ""}${model.available === false ? ` · ${escapeHtml(model.unavailable_reason || "模型目录当前不可访问")}` : ""}${shown ? "" : " · 已从对话候选隐藏"}</small></div>${modelCapabilityChips(model, index)}<details class="cherry-model-more"><summary aria-label="编辑模型能力">${uiIcon("settings")}</summary>${modelCapabilityChips(model, index, { showAll: true })}</details><div class="cherry-model-row-actions"><button class="cherry-model-visibility" type="button" data-action="toggle-model-composer-visibility" data-model-index="${index}" aria-label="在对话候选中显示 ${escapeHtml(name)}" aria-pressed="${shown ? "true" : "false"}" title="${shown ? "从对话候选中隐藏" : "在对话候选中显示"}">${uiIcon(shown ? "eye" : "eye-off")}</button><button class="cherry-row-remove" type="button" data-action="remove-model" data-model-index="${index}" aria-label="移除模型">${uiIcon("minus")}</button></div></article>`;
  }).join("")}</section>`).join("");
  const kindOptions = ["local", "openai-compatible", "anthropic-compatible"].map((kind) => `<option value="${kind}" ${provider.kind === kind ? "selected" : ""}>${kind === "local" ? "本地" : kind}</option>`).join("");
  const customNameField = isBuiltInProvider(provider) ? `<input type="hidden" name="provider-name" value="${escapeHtml(provider.name)}" />` : `<label class="cherry-field"><span>服务商名称</span><input name="provider-name" value="${escapeHtml(provider.name)}" required maxlength="80" /></label>`;
  const kindField = isBuiltInProvider(provider) ? `<input type="hidden" name="provider-kind" value="${escapeHtml(provider.kind)}" />` : `<label class="cherry-field cherry-kind-field"><span>接口类型</span><select name="provider-kind">${kindOptions}</select></label>`;
  const isManaged = provider.auth_mode === "managed";
  const isLocalConnection = provider.auth_mode === "local";
  const providerUsesEnvironmentKey = provider.api_key_source === "environment";
  const providerKeyLabel = providerUsesEnvironmentKey ? "环境变量" : "已保存";
  const providerKeyHint = providerUsesEnvironmentKey
    ? `当前使用环境变量 <code>${escapeHtml(provider.api_key_env || "")}</code>；设置页不会覆盖它。`
    : provider.api_key_source === "endpoint_mismatch"
    ? `环境变量 <code>${escapeHtml(provider.api_key_env || "")}</code> 仅用于默认 API 地址；当前地址请单独保存密钥。`
    : provider.api_key_configured
      ? "密钥已安全保存；此电脑会记住，并在适用的知识库中复用。输入新值可替换。"
      : "只需输入一次；此电脑会在适用的知识库中复用。";
  const keyField = isManaged ? `<p class="cherry-field-hint">云端模型由 ScanSci 托管提供，无需配置 API 密钥；本地模型通过这台电脑的运行时调用。</p>` : isLocalConnection ? `<p class="cherry-field-hint">本机运行时连接无需 API 密钥。请到“运行时”页面检测连接和模型能力。</p>` : provider.kind !== "local" ? `<div class="cherry-key-row"><label class="cherry-field cherry-secret-field"><span>API 密钥${provider.api_key_configured ? `<em>${providerKeyLabel}</em>` : ""}</span><span class="cherry-secret-control"><input name="provider-api-key" type="password" autocomplete="new-password" autocapitalize="off" spellcheck="false" placeholder="${provider.api_key_configured ? "已保存；输入新值以替换" : "输入后仅保存在系统凭据管理器"}" /></span></label><button type="button" class="cherry-detect-button" data-action="test-provider" ${!provider.api_key_configured ? "disabled" : ""}>检 测</button></div><p class="cherry-field-hint">${providerKeyHint}</p>` : `<p class="cherry-field-hint">内置证据引擎不需要 API 密钥。</p>`;
  const remoteRagField = provider.id === "siliconflow"
    ? `<div class="cherry-endpoint-mode"><div class="cherry-endpoint-mode-copy"><strong>允许云端知识库检索</strong><small>开启后，本工作区的论文片段和检索问题会发送至 ${escapeHtml(provider.base_url || "当前 API 地址")} 用于嵌入、重排；关闭后改用本地检索。更换 API 地址后需重新开启。</small></div><label class="cherry-toggle cherry-endpoint-toggle"><input name="provider-remote-rag-allowed" type="checkbox" ${provider.remote_rag_allowed ? "checked" : ""} aria-label="允许云端服务处理本工作区的论文片段和检索问题" /><span aria-hidden="true"></span></label></div>`
    : "";
  const isZhipu = provider.id === "zhipu";
  const codingPlanEnabled = isZhipu && Boolean(provider.coding_plan_enabled);
  const codingPlanField = isZhipu
    ? `<div class="cherry-endpoint-mode"><div class="cherry-endpoint-mode-copy"><strong>GLM Coding Plan</strong><small>启用智谱 Coding Plan 专用 API 地址</small></div><label class="cherry-toggle cherry-endpoint-toggle"><input name="provider-coding-plan" type="checkbox" ${codingPlanEnabled ? "checked" : ""} aria-label="启用 GLM Coding Plan" /><span aria-hidden="true"></span></label></div>`
    : "";
  const endpointLabel = isZhipu
    ? `API 地址 <i>⌁</i><b class="cherry-endpoint-mode-label">${codingPlanEnabled ? "Coding Plan" : "标准 API"}</b>`
    : "API 地址 <i>⌁</i>";
  const restoreDefaultButton = isBuiltInProvider(provider) ? '<button type="button" class="cherry-restore-default" data-action="restore-provider-default">恢复默认</button>' : "";
  const providerHeaderActions = `${restoreDefaultButton}${provider.api_key_configured && !providerUsesEnvironmentKey && !isManaged ? '<button type="button" class="cherry-text-button" data-action="remove-provider-key" title="移除当前知识库及同地址的共享密钥；其他旧工作区的独立密钥需分别移除">移除密钥</button>' : ""}<button type="button" class="cherry-text-button" data-action="refresh-model-health" ${state.modelHealth?.loading ? "检查中…" : "刷新状态"}</button><button type="submit" class="cherry-save-button">保存</button>`;
  const endpointPreview = provider.id === "jev"
    ? `POST ${provider.base_url || JEV_DEFAULT_ENDPOINT}`
    : provider.id === "opencode-go"
    ? "按所选模型自动选择 /chat/completions、/responses 或 /messages"
    : provider.id === "paratera" && provider.base_url
      ? "通义万相：/p003/text2image → /p003/tasks/{task_id}；CogView：/images/generations"
    : provider.base_url
      ? `${provider.base_url.replace(/\/$/, "")}/chat/completions`
      : "请填写服务商 API 地址";
  const providerGuide = provider.id === "zhipu"
    ? `<p class="cherry-field-hint cherry-zhipu-guide"><span data-zhipu-guide-copy>${codingPlanEnabled ? "当前使用 Coding Plan 专用地址；该地址仅适用于 GLM Coding Plan。" : "当前使用标准 API 地址；开启 Coding Plan 后会切换到专用地址。"}</span><a href="https://docs.bigmodel.cn/cn/coding-plan/quick-start" target="_blank" rel="noopener noreferrer">查看官方说明 ↗</a></p>`
    : provider.id === "opencode-go"
    ? '<p class="cherry-field-hint">先在 OpenCode Zen 开通 Go 套餐并复制 API Key；保存后可直接获取模型列表。<a href="https://dev.opencode.ai/docs/go/" target="_blank" rel="noopener noreferrer">查看官方说明 ↗</a></p>'
    : provider.id === "jev"
      ? '<p class="cherry-field-hint">保存 TypeSafe API Key 后点击“检测”，ScanSci 会发送一次最小结构化判断请求验证接入。<a href="https://docs.typesafe.ai/api" target="_blank" rel="noopener noreferrer">查看 API 文档 ↗</a></p>'
    : provider.id === "paratera"
      ? '<p class="cherry-field-hint">保存密钥后，可在输入框选择 WanX2.1-T2I-Plus 生图，按服务商计费；也可在“默认能力”中设为图像生成模型。<a href="https://ai.paratera.com/document/llm/modalAbility/image" target="_blank" rel="noopener noreferrer">生图说明与价格 ↗</a></p>'
    : "";
  return `<section class="cherry-model-services"><aside class="cherry-provider-catalog"><label class="cherry-provider-search"><svg class="cherry-search-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.75" cy="10.75" r="6.75"></circle><path d="m16 16 5 5"></path></svg><input id="modelServiceSearch" type="search" value="${escapeHtml(state.providerQuery)}" placeholder="搜索模型平台..." autocomplete="off" /></label><div class="cherry-provider-scroll">${providerItems || '<div class="cherry-provider-empty">没有匹配的模型平台</div>'}${localRuntimeNotice}</div><button class="cherry-add-provider" type="button" data-action="add-provider">＋&nbsp; 添加</button></aside><main class="cherry-provider-panel"><form id="modelProviderForm"><header class="cherry-provider-header"><div><div class="cherry-provider-name">${providerLogo(provider)}<h1>${escapeHtml(provider.name)}</h1><button type="button" class="cherry-mini-gear" aria-label="服务商设置">⚙</button></div>${kindField}</div><div class="cherry-provider-header-actions">${providerHeaderActions}<span class="cherry-provider-header-divider" aria-hidden="true"></span><label class="cherry-toggle"><input name="provider-enabled" type="checkbox" ${provider.enabled ? "checked" : ""} /><span></span></label></div></header><section class="cherry-connection-section">${customNameField}${keyField}${remoteRagField}${codingPlanField}<label class="cherry-field"><span>${endpointLabel}</span><input name="provider-base-url" value="${escapeHtml(provider.base_url || "")}" placeholder="https://api.example.com/v1" maxlength="500" ${isManaged ? "readonly" : ""} /></label>${providerGuide}<p class="cherry-endpoint-preview">预览：${escapeHtml(endpointPreview)}</p></section><section class="cherry-model-section"><header><div class="cherry-model-section-title"><h2>模型</h2><b>${provider.models.length + localServiceModels.length}</b></div><label class="cherry-model-search"><span>⌕</span><input id="modelListSearch" type="search" value="${escapeHtml(state.modelQuery)}" placeholder="搜索模型..." aria-label="搜索模型" autocomplete="off" /></label><div class="cherry-model-actions"><button type="button" class="cherry-fetch-button" data-action="fetch-provider-models" ${provider.kind === "local" || !provider.model_listing ? "disabled" : ""}>↻&nbsp; 获取模型列表</button><button type="button" class="cherry-plus-button" data-action="add-model" aria-label="添加模型">＋</button></div></header><div class="cherry-model-list">${(modelRows + localServiceModelsMarkup(localServiceModels, modelQuery)) || `<div class="cherry-provider-empty">${modelQuery ? "没有匹配的模型" : "尚未添加模型"}</div>`}</div></section><footer class="cherry-provider-footer"><button type="button" class="cherry-remove-provider" data-action="remove-provider" ${isBuiltInProvider(provider) ? "disabled" : ""}>移除服务商</button></footer>${modelEditorMarkup(provider)}</form></main></section>`;
}

function renderLegacyLocalModelsSettings() {
  const presets = (state.presets?.local_models || []).map((item) => `<button type="button" class="quiet-add-chip" data-action="add-local-preset" data-preset-id="${escapeHtml(item.id)}">＋ ${escapeHtml(item.name)}</button>`).join("");
  const installedItems = state.localModelMarket?.installed || [];
  const runtime = state.localRuntime || { installed: false, install_available: false, mode: "missing" };
  const runtimeReady = Boolean(runtime.installed) && !runtime.update_required;
  const runtimeJob = runtime.install_job || {};
  const runtimeInstalling = ["queued", "installing"].includes(runtimeJob.state);
  const runtimeNeedsRetry = ["failed", "cancelled", "interrupted"].includes(runtimeJob.state);
  const runtimeProgress = Math.max(0, Math.min(100, Math.round(Number(runtimeJob.progress || 0) * 100)));
  const runtimeAction = runtimeReady
    ? `<span class="local-model-primary-ready">${uiIcon("check")} 运行时已就绪</span>`
    : runtimeInstalling
      ? `<span class="local-model-primary-state">${escapeHtml(runtimeJob.message || "正在安装")} ${runtimeProgress}%<small>${escapeHtml(downloadJobTelemetry(runtimeJob))}</small></span>`
    : runtimeNeedsRetry && runtime.install_available
      ? `<button type="button" class="local-model-primary-action" data-action="install-local-runtime">${uiIcon("refresh")}继续安装</button>`
    : runtime.install_available
      ? `<button type="button" class="local-model-primary-action" data-action="install-local-runtime">${uiIcon("download")}${runtime.update_required ? "更新本地运行组件" : "安装本地运行能力"}</button>`
      : `<button type="button" class="local-model-primary-action" data-action="open-local-runtime-setup">${uiIcon("settings")}查看本地运行设置</button>`;
  const runtimeDescription = runtimeReady
    ? "可使用已安装的本地模型；模型下载完成后会自动校验。"
    : runtime.update_required
      ? `检测到本地运行组件 ${runtime.version || "旧版本"}，需要更新到 ${runtime.required_version || "当前版本"}；已下载模型不会重复下载。`
    : runtimeInstalling
      ? "由 ScanSci 提供，正在下载、校验并自检；进度会持续保留。"
    : runtimeNeedsRetry
      ? runtimeJob.error || runtimeJob.message || "安装未完成；继续安装会复用已下载内容。"
    : runtime.install_available
      ? "由 ScanSci 提供并按需安装；核心程序保持轻量，安装完成后可下载本地模型。"
      : runtime.install_unavailable_reason || "当前发行渠道未提供本地运行能力；可连接 Ollama、LM Studio 使用已有模型，也可稍后手动安装。";
  const capabilityLabel = (kind) => ({ chat: "对话", embedding: "嵌入", reranking: "重排", vision: "视觉", audio: "语音" }[kind] || "通用");
  const usableInstalledCount = installedItems.filter((item) => item.ready && item.runtime_compatible !== false).length;
  const incompleteInstalledCount = installedItems.filter((item) => !item.ready).length;
  const incompatibleInstalledCount = installedItems.filter((item) => item.runtime_compatible === false || item.runtime_probe_state === "failed").length;
  const installedSummary = `${usableInstalledCount} 可用${incompleteInstalledCount ? ` · ${incompleteInstalledCount} 未完成` : ""}${incompatibleInstalledCount ? ` · ${incompatibleInstalledCount} 不兼容` : ""}`;
  const installed = installedItems.map((item) => {
    const size = `${(Number(item.size_bytes || 0) / 1024 / 1024 / 1024).toFixed(1)} GB`;
    const kind = item.kind || (/(embedding|embed|bge|gte|e5-)/i.test(item.id || "") ? "embedding" : /(rerank)/i.test(item.id || "") ? "reranking" : "chat");
    const unsupportedAudio = kind === "audio" && item.runtime_compatible === false;
    const unavailable = item.runtime_compatible === false || item.runtime_probe_state === "failed";
    const pendingProbe = item.runtime_probe_state === "pending";
    const status = unavailable ? (unsupportedAudio ? "当前格式不可运行" : "不可用") : pendingProbe ? "待验证" : item.ready ? "已就绪" : "下载未完成";
    const detail = (unavailable || pendingProbe) && item.runtime_message ? `<small class="quiet-model-warning">${escapeHtml(item.runtime_message)}</small>` : "";
    return `<article class="quiet-model-row"><span class="quiet-model-mark">${kind === "chat" ? "◎" : "◇"}</span><div><strong>${escapeHtml(item.name)}</strong><div class="local-capability-tags"><span>${capabilityLabel(kind)}</span>${item.format ? `<span>${escapeHtml(item.format)}</span>` : ""}</div>${detail}</div><span class="quiet-row-note">${status}</span><span class="quiet-row-size">${size}</span></article>`;
  }).join("") || '<div class="quiet-empty">未发现本地模型。</div>';
  const ollama = state.ollama || {};
  const audioRuntimeReady = runtimeReady && ["source", "embedded", "component"].includes(String(state.localRuntime?.mode || ""));
  const catalog = (state.localModelMarket?.catalog || []).map((item) => {
    const isOllama = String(item.runtime || "").toLowerCase() === "ollama";
    const ready = isOllama ? Boolean(ollama.model_ready || item.ready) : Boolean(item.ready);
    const installed = isOllama ? ready : Boolean(item.installed);
    const canDownload = isOllama ? Boolean(ollama.reachable) : item.kind === "audio" ? audioRuntimeReady : runtimeReady;
    const job = (state.localModelInstall?.jobs || []).find((candidate) => (
      Array.isArray(candidate?.models) && candidate.models.includes(item.id)
    )) || null;
    const jobState = String(job?.state || "");
    const jobActive = ["queued", "downloading", "installing", "pausing", "cancelling"].includes(jobState);
    const jobRetryable = ["failed", "cancelled", "interrupted", "paused"].includes(jobState);
    const action = installed
      ? `<span class="quiet-row-note">已安装</span>`
      : jobActive
        ? `<span class="quiet-row-note">${escapeHtml(downloadJobStatus(job).label)} · ${escapeHtml(downloadJobProgressSummary(job))}</span>`
      : jobRetryable && job?.job_id
        ? `<button type="button" class="quiet-text-button" data-action="control-download-task" data-download-kind="model" data-download-action="${jobState === "paused" ? "resume" : "retry"}" data-job-id="${escapeHtml(job.job_id)}">${jobState === "paused" ? "继续" : "重试"}</button>`
      : canDownload
        ? `<button type="button" class="quiet-text-button" data-action="download-local-model" data-model-repo="${escapeHtml(item.id)}" data-model-runtime="${escapeHtml(item.runtime || "huggingface")}">下载</button>`
        : isOllama
          ? `<button type="button" class="quiet-text-button" data-action="open-ollama-setup">连接已有 Ollama</button>`
          : `<button type="button" class="quiet-text-button" data-action="open-local-runtime-setup">查看运行时</button>`;
    const status = isOllama && !ollama.reachable && !installed ? "需要 Ollama" : ready ? "已就绪" : job ? downloadJobStatus(job).label : "未下载";
    return `<article class="quiet-model-row">${item.icon_url ? `<img class="model-market-icon" data-site-icon="true" src="/api/site-icon?url=${encodeURIComponent(item.icon_url)}" alt="" loading="lazy" decoding="async" />` : `<span class="quiet-model-mark is-muted">${isOllama ? "◉" : "↓"}</span>`}<div><strong>${escapeHtml(item.name)}</strong><p>${escapeHtml(item.description || (isOllama ? "本地视觉模型" : "本地模型"))}${item.size_hint ? ` · ${escapeHtml(item.size_hint)}` : ""}</p><div class="local-capability-tags"><span>${capabilityLabel(item.kind)}</span><span>${status}</span></div></div>${action}</article>`;
  }).join("") || `<div class="quiet-empty">${state.localModelMarket?.loading ? "正在读取模型市场…" : "市场目录暂不可用。"}</div>`;
  const runtimeRows = (state.settings.local_models || []).map((item, index) => ({ item, index })).filter(({ item }) => item.runtime !== "builtin").map(({ item, index }) => `<details class="quiet-runtime-row"><summary><span class="quiet-model-mark">◌</span><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.runtime)} · ${item.enabled ? "可用" : "已停用"}</small></span><span class="quiet-row-note">配置</span></summary><div class="quiet-runtime-fields"><label><span>名称</span><input data-local-name="${index}" value="${escapeHtml(item.name)}" /></label><label><span>运行时</span><input data-local-runtime="${index}" value="${escapeHtml(item.runtime)}" /></label><label><span>地址</span><input data-local-url="${index}" value="${escapeHtml(item.base_url || "")}" placeholder="http://127.0.0.1:11434/v1" /></label><label><span>模型 ID</span><input data-local-model="${index}" value="${escapeHtml(item.model_id || "")}" placeholder="例如 qwen3:8b" /></label><label class="quiet-switch"><input type="checkbox" data-local-enabled="${index}" ${item.enabled ? "checked" : ""} /><span>启用</span></label><div><button type="button" class="quiet-text-button" data-action="test-local-model" data-local-id="${escapeHtml(item.id)}">测试连接</button><button type="button" class="quiet-danger-button" data-action="remove-local-model" data-local-index="${index}">移除</button></div></div></details>`).join("") || '<div class="quiet-empty">尚未添加外部本地运行时。</div>';
  const capabilityCards = ONBOARDING_RESOURCE_ORDER
    .map((resourceId) => resourceInstallSnapshot(resourceId))
    .map(resourceSetupCard)
    .join("");
  return `<section class="quiet-settings-page local-models-page local-models-page--compact"><header class="quiet-page-heading"><div><h1>本地模型</h1><p>这里是本地运行时、已安装模型和下载任务的唯一管理入口。</p></div><button type="button" class="quiet-text-button" data-action="refresh-local-model-market">刷新</button></header>
    <section class="local-capability-section"><header><div><h2>能力状态</h2><p>每项能力独立安装、校验和启用；完成后不再显示下载进度。</p></div><span>嵌入 · 重排 · 对话 · 视觉 · 语音</span></header><div class="local-capability-grid">${capabilityCards}</div><p class="local-model-fallback">没有安装语义检索模型时，ScanSci 仍会使用基础关键词检索。</p></section>
    <details class="local-model-disclosure"><summary><span>已发现的模型</span><em>${installedItems.length}</em></summary><div class="quiet-model-list">${installed}</div></details>
    <details class="local-model-disclosure local-runtime-disclosure"><summary><span>本地运行时</span><em>${runtimeReady ? "已就绪" : "需要配置"}</em></summary><div class="local-model-disclosure-body"><p>${escapeHtml(runtimeDescription)}</p><div class="quiet-add-chips">${presets}</div><form id="localModelsForm" class="quiet-runtime-list">${runtimeRows}<footer><button type="submit" class="quiet-primary-button">保存更改</button></footer></form></div></details>
    <details class="local-model-disclosure"><summary><span>模型市场</span><em>按需下载</em></summary><div class="local-model-disclosure-body"><form id="localModelMarketSearch" class="local-model-market-search"><input name="query" type="search" value="${escapeHtml(state.localModelMarket?.query || "")}" placeholder="搜索模型，例如 embedding、reranker、Qwen" /><button type="submit" class="quiet-text-button">搜索</button></form><div class="quiet-model-list">${catalog}</div></div></details></section>`;
}

const LOCAL_MODEL_CATEGORY_GROUPS = [
  {
    id: "retrieval",
    eyebrow: "知识库能力",
    title: "知识库检索",
    description: "先安装嵌入和重排模型，才能获得更好的语义检索和证据排序。",
    resources: ["embedding", "reranking"],
  },
  {
    id: "multimodal",
    eyebrow: "按需启用",
    title: "对话与多模态",
    description: "本地对话、图片理解和语音转写互相独立，按照你的电脑和工作方式选择。",
    resources: ["chat", "vision", "audio"],
  },
];

const LOCAL_MODEL_RECOMMENDATION_META = {
  embedding: { label: "嵌入模型", icon: "sparkles", size: "约 1 GB", runtime: "Transformers" },
  reranking: { label: "重排模型", icon: "filter", size: "约 1 GB", runtime: "Transformers" },
  chat: { label: "本地对话模型", icon: "brain", size: "约 3 GB", runtime: "Transformers" },
  vision: { label: "视觉模型", icon: "eye", size: "约 1.1 GB", runtime: "Transformers" },
  audio: { label: "语音模型", icon: "audio", size: "约 2 GB", runtime: "Transformers" },
};

function localRecommendationCatalogItem(resource) {
  const definition = ONBOARDING_RESOURCE_DEFINITIONS[resource];
  const modelId = definition?.models?.[0] || "";
  const catalogItem = (state.localModelMarket?.catalog || []).find((item) => item.id === modelId) || {};
  const meta = LOCAL_MODEL_RECOMMENDATION_META[resource] || {};
  return {
    ...catalogItem,
    id: modelId,
    kind: catalogItem.kind || resource,
    runtime: catalogItem.runtime || definition?.runtime || "huggingface",
    name: catalogItem.name || definition?.detail || modelId,
    description: catalogItem.description || definition?.description || "ScanSci 推荐的本地模型。",
    size_hint: catalogItem.size_hint || meta.size || "按模型大小计算",
  };
}

function localRecommendationStatus(resource) {
  const job = resource.job || {};
  const jobState = String(job.state || "");
  if (resource.state === "ready") return { label: "已就绪", tone: "ready" };
  if (["queued", "downloading", "installing", "runtime_installing"].includes(resource.state)) {
    return { label: `${downloadJobStatus(job).label} · ${resource.progress}%`, tone: "loading" };
  }
  if (resource.state === "runtime_required") return { label: "需要准备本地能力", tone: "muted" };
  if (resource.state === "runtime_failed") return { label: "运行时未完成", tone: "error" };
  if (resource.state === "paused" || jobState === "paused") return { label: "已暂停", tone: "warning" };
  if (["failed", "cancelled", "interrupted"].includes(resource.state) || ["failed", "cancelled", "interrupted"].includes(jobState)) {
    return { label: "下载未完成", tone: "error" };
  }
  return { label: "尚未下载", tone: "muted" };
}

function localRecommendationAction(resource, item) {
  const job = resource.job || {};
  const jobState = String(job.state || "");
  const active = ["queued", "downloading", "installing", "pausing", "cancelling", "runtime_installing"].includes(resource.state) || ["queued", "downloading", "installing"].includes(jobState);
  if (resource.state === "ready") return `<span class="local-model-card-ready">${uiIcon("check")} 已安装</span>`;
  if (active) return `<span class="local-model-card-progress-label">${escapeHtml(downloadJobTelemetry(job) || "正在准备本地模型")} · ${resource.progress}%</span>`;
  if (["runtime_required", "runtime_failed"].includes(resource.state)) {
    return `<button type="button" class="local-model-card-button is-secondary" data-action="start-onboarding-resource" data-resource-id="${escapeHtml(resource.id)}">${uiIcon(resource.state === "runtime_failed" ? "refresh" : "download")} ${resource.state === "runtime_failed" ? "继续准备" : "准备本地能力"}</button>`;
  }
  if (["paused", "failed", "cancelled", "interrupted"].includes(resource.state) || ["paused", "failed", "cancelled", "interrupted"].includes(jobState)) {
    if (job.job_id) {
      const action = jobState === "paused" ? "resume" : "retry";
      return `<button type="button" class="local-model-card-button" data-action="control-download-task" data-download-kind="model" data-download-action="${action}" data-job-id="${escapeHtml(job.job_id)}">${uiIcon(action === "resume" ? "play" : "refresh")} ${action === "resume" ? "继续下载" : "重试下载"}</button>`;
    }
  }
  return `<button type="button" class="local-model-card-button" data-action="start-onboarding-resource" data-resource-id="${escapeHtml(resource.id)}">${uiIcon("download")} 下载</button>`;
}

function localRecommendationCard(resourceId) {
  const definition = ONBOARDING_RESOURCE_DEFINITIONS[resourceId];
  const meta = LOCAL_MODEL_RECOMMENDATION_META[resourceId] || {};
  const resource = resourceInstallSnapshot(resourceId);
  const item = localRecommendationCatalogItem(resourceId);
  const status = localRecommendationStatus(resource);
  return `<article class="local-model-recommendation is-${escapeHtml(status.tone)}"><header><span class="local-model-recommendation-icon">${uiIcon(meta.icon || definition.icon)}</span><div><span class="local-model-recommendation-type">${escapeHtml(meta.label || definition.title)}</span><h3>${escapeHtml(item.name)}</h3></div><em class="local-model-recommendation-status is-${escapeHtml(status.tone)}">${escapeHtml(status.label)}</em></header><p>${escapeHtml(item.description)}</p><div class="local-model-recommendation-meta"><span>${escapeHtml(item.size_hint || meta.size || "按模型大小计算")}</span><b>默认推荐</b></div><footer>${localRecommendationAction(resource, item)}</footer></article>`;
}

function localModelMarketRow(item, recommendedIds) {
  const isOllama = String(item.runtime || "").toLowerCase() === "ollama";
  const ollama = state.ollama || {};
  const incompatible = item.runtime_compatible === false;
  const ready = !incompatible && (isOllama ? Boolean(ollama.model_ready || item.ready) : Boolean(item.ready));
  const installed = isOllama ? ready : Boolean(item.installed);
  const runtime = state.localRuntime || {};
  const canDownload = isOllama ? Boolean(ollama.reachable) : item.kind === "audio" ? Boolean(runtime.installed) && !runtime.update_required && ["source", "embedded", "component"].includes(String(runtime.mode || "")) : Boolean(runtime.installed) && !runtime.update_required;
  const job = (state.localModelInstall?.jobs || []).find((candidate) => Array.isArray(candidate?.models) && candidate.models.includes(item.id)) || null;
  const jobState = String(job?.state || "");
  const active = ["queued", "downloading", "installing", "pausing", "cancelling"].includes(jobState);
  const retryable = ["failed", "cancelled", "interrupted", "paused"].includes(jobState);
  const action = incompatible
    ? `<span class="quiet-row-note">格式不兼容</span>`
    : installed
    ? `<span class="quiet-row-note">已安装</span>`
    : active
      ? `<span class="quiet-row-note">${escapeHtml(downloadJobStatus(job).label)} · ${escapeHtml(downloadJobProgressSummary(job))}</span>`
      : retryable && job?.job_id
        ? `<button type="button" class="quiet-text-button" data-action="control-download-task" data-download-kind="model" data-download-action="${jobState === "paused" ? "resume" : "retry"}" data-job-id="${escapeHtml(job.job_id)}">${jobState === "paused" ? "继续" : "重试"}</button>`
        : canDownload
          ? `<button type="button" class="quiet-text-button" data-action="download-local-model" data-model-repo="${escapeHtml(item.id)}" data-model-runtime="${escapeHtml(item.runtime || "huggingface")}">下载</button>`
          : isOllama
            ? `<button type="button" class="quiet-text-button" data-action="open-ollama-setup">连接已有 Ollama</button>`
            : `<button type="button" class="quiet-text-button" data-action="open-local-runtime-setup">查看运行时</button>`;
  const status = incompatible ? "当前格式不可运行" : isOllama && !ollama.reachable && !installed ? "需要 Ollama" : ready ? "已就绪" : job ? downloadJobStatus(job).label : "未下载";
  const kind = item.kind || "chat";
  return `<article class="quiet-model-row ${recommendedIds.has(item.id) ? "is-recommended" : ""}"><span class="quiet-model-mark is-muted">${isOllama ? "◉" : "↓"}</span><div><strong>${escapeHtml(item.name || item.id)}</strong><p>${escapeHtml(item.description || (isOllama ? "本地视觉模型" : "本地模型"))}${item.size_hint ? ` · ${escapeHtml(item.size_hint)}` : ""}</p><div class="local-capability-tags"><span>${escapeHtml(({ chat: "对话", embedding: "嵌入", reranking: "重排", vision: "视觉", audio: "语音" }[kind] || "通用"))}</span><span>${escapeHtml(status)}</span></div></div>${action}</article>`;
}

function localRuntimeChannelRecoveryMarkup(runtime = state.localRuntime || {}) {
  const releaseUrl = runtime.manifest_release_url || "https://github.com/Rimagination/scansci-portal/releases/tag/local-runtime-v1.0.0";
  if (runtime.update_required) {
    const updateAction = runtime.install_available
      ? `<button type="button" class="quiet-primary-button" data-action="install-local-runtime">${uiIcon("download")} 更新本地运行组件</button>`
      : `<a href="${escapeHtml(releaseUrl)}" target="_blank" rel="noopener noreferrer">打开组件发布页 ${uiIcon("arrow-up-right")}</a>`;
    return `<section class="local-runtime-recovery is-update-required"><header><div><span>本地运行组件</span><strong>更新本地运行组件</strong></div>${updateAction}</header><div class="local-runtime-manual-fallback"><div><strong>检测到 ${escapeHtml(runtime.version || "旧版本")}，当前需要 ${escapeHtml(runtime.required_version || "新版本")}</strong><p>只更新独立运行组件；已下载模型不会重复下载，更新完成后会继续复用。</p></div>${runtime.install_available ? "" : `<button type="button" class="quiet-primary-button" data-action="choose-local-runtime-files">选择本地组件包</button>`}</div></section>`;
  }
  if (runtime.installed && !runtime.update_required) return "";
  const report = runtime.channels;
  const channels = Array.isArray(report?.channels) ? report.channels : [];
  const checked = Boolean(report?.checked_at);
  const available = Boolean(report?.available);
  const summary = !checked
    ? runtime.install_unavailable_reason || "自动下载通道尚未检查"
    : available
      ? "自动下载通道可用"
      : "自动下载暂不可用，可手动安装";
  const channelRows = channels.length
    ? `<div class="local-runtime-channel-list">${channels.map((item) => `<div><span>${escapeHtml(item.label || "下载通道")}</span><b class="${item.valid ? "is-ready" : "is-failed"}">${item.valid ? "可用" : "不可用"}</b></div>`).join("")}</div>`
    : `<p class="local-runtime-channel-empty">点击“检查通道”后，ScanSci 会逐个验证清单是否可读；启动时不会因为网络探测而卡住。</p>`;
  const checking = Boolean(runtime.channelsChecking);
  return `<section class="local-runtime-recovery"><header><div><span>下载通道</span><strong>${escapeHtml(checking ? "正在检查自动通道…" : summary)}</strong></div><button type="button" class="quiet-text-button" data-action="check-local-runtime-channels" ${checking ? "disabled" : ""}>${uiIcon("refresh")} ${checking ? "检查中…" : checked ? "重新检查" : "检查通道"}</button></header>${channelRows}<div class="local-runtime-manual-fallback"><div><strong>网络仍不可用？可以手动安装</strong><p>从官方发布页下载 ZIP；如果是分片包，请把 JSON 清单和全部分片一起选中，ScanSci 会校验后再安装。</p></div><div class="local-runtime-recovery-actions"><button type="button" class="quiet-primary-button" data-action="choose-local-runtime-files">选择本地文件</button><a href="${escapeHtml(releaseUrl)}" target="_blank" rel="noopener noreferrer">打开官方发布页 ${uiIcon("arrow-up-right")}</a></div></div></section>`;
}

function renderStrataSetupMarkup() {
  const entry = (state.settings?.local_models || []).find((item) => item.runtime === "strata" && item.id === "strata");
  const routeChanged = Boolean(entry && state.strataDraftUrl && state.strataDraftUrl.replace(/\/$/, "") !== String(entry.base_url || "").replace(/\/$/, ""));
  const candidate = state.strataCandidateConnection?.base_url === state.strataDraftUrl ? state.strataCandidateConnection : null;
  const connection = candidate || (!routeChanged && entry ? state.modelHealth?.runtime_connections?.[entry.id] : null);
  const models = (connection?.models || []).filter(Boolean);
  const current = String(candidate ? models[0] || "" : entry?.model_id || models[0] || "");
  const selectedHealth = modelHealthEntry(`local-runtime-${entry?.id || "strata"}`, current);
  const verified = !candidate && selectedHealth?.status === "ready";
  const connectionFailed = Boolean(state.strataProbeError || (entry?.enabled && connection && !connection.ok));
  const stage = connectionFailed ? "连接异常" : candidate ? "候选地址待验证" : !entry ? "尚未连接" : !entry.enabled ? "已停用" : !connection ? "等待检测" : verified ? "Agent 已验证" : "模型已发现 · 待验证";
  const detail = state.strataProbeError || connection?.error || (candidate ? "候选地址已连接。验证通过后切换助手，当前默认模型继续可用。" : connection?.ok ? `已发现 ${models.length} 个模型入口。验证通过后可设为助手。` : routeChanged ? "点击重新检测连接；当前默认模型继续可用。" : "先安装并启动 Strata，再检测连接。");
  const hardware = state.strataPreflight;
  const vram = Number(hardware?.vram_gib || 0) > 0 ? `${hardware.vram_gib} GiB` : "未检出";
  const hardwareNote = hardware
    ? `本机内存 ${escapeHtml(hardware.ram_gib || "未知")} GiB · NVIDIA 显存 ${escapeHtml(vram)}。实际可运行性由 Strata 安装器和模型加载确认。`
    : "安装前可查看本机内存和 NVIDIA 显存；模型所需磁盘空间以 Strata 安装器为准。";
  const selector = connection?.ok && models.length
    ? `<label class="strata-model-picker"><span>已加载模型</span><select data-strata-model>${models.map((model) => `<option value="${escapeHtml(model)}" ${model === current ? "selected" : ""}>${escapeHtml(model)}</option>`).join("")}</select></label>`
    : "";
  const verifyAction = entry && connection?.ok && models.length
    ? `<button type="button" class="quiet-primary-button" data-action="activate-strata" data-local-id="${escapeHtml(entry.id)}" ${state.strataProbeRunning ? "disabled" : ""}>${state.strataProbeRunning ? "正在验证模型…" : "验证并设为助手"}</button>`
    : "";
  return `<section class="strata-setup local-runtime-recovery"><header><div><span>可选 · 外部本地推理</span><strong>Strata</strong></div><b class="strata-setup-stage ${connectionFailed ? "is-error" : ""}">${connectionFailed ? uiIcon("circle-alert") : ""}${escapeHtml(stage)}</b></header><p>下载官方发布包，解压后双击 START-HERE.bat 完成安装和启动。首次模型下载约 70 GB 起；模型文件保存在本机。</p><label class="strata-model-picker"><span>本机地址</span><input data-strata-url value="${escapeHtml(state.strataDraftUrl || entry?.base_url || "http://127.0.0.1:8080/v1")}" aria-label="Strata 本机地址" /></label><p class="strata-setup-detail ${connectionFailed ? "is-error" : ""}" role="status">${escapeHtml(detail)}</p><div class="strata-setup-actions"><a href="https://github.com/Niko1221/Strata/releases/latest" target="_blank" rel="noopener noreferrer">打开 Strata 官方发布页 ${uiIcon("arrow-up-right")}</a><button type="button" class="quiet-text-button" data-action="check-strata-hardware">查看本机条件</button><button type="button" class="quiet-text-button" data-action="connect-strata">${entry ? "重新检测连接" : "连接已运行的 Strata"}</button>${verifyAction}${entry ? '<button type="button" class="quiet-text-button" data-action="disconnect-strata">移除连接</button>' : ""}</div><small class="strata-hardware-note">${hardwareNote}</small>${selector}<small>ScanSci 只连接本机地址；嵌入、重排和文件读取继续由 ScanSci 管理。</small></section>`;
}

function renderExternalRuntimeConnectionsMarkup() {
  const presets = (state.presets?.local_models || [])
    .filter((item) => ["ollama", "lm-studio", "llama.cpp"].includes(String(item.runtime || "").toLowerCase()))
    .map((item) => `<button type="button" class="quiet-add-chip" data-action="add-local-preset" data-preset-id="${escapeHtml(item.id)}">＋ ${escapeHtml(item.name)}</button>`)
    .join("");
  const manualRuntimeItems = (state.settings?.local_models || [])
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => !["builtin", "local-huggingface", "strata"].includes(String(item.runtime || "").toLowerCase()));
  const runtimeAddressCount = new Set(manualRuntimeItems.map(({ item }) => `${item.runtime}|${item.base_url}`)).size
    + Number((state.settings?.local_models || []).some((item) => item.id === "strata" && item.runtime === "strata"));
  const runtimeRows = manualRuntimeItems
    .map(({ item, index }) => {
      const runtimeName = { ollama: "Ollama", "lm-studio": "LM Studio", "llama.cpp": "llama.cpp", strata: "Strata" }[String(item.runtime || "").toLowerCase()] || item.runtime || "本地运行时";
      return `<article class="local-runtime-row"><header class="local-runtime-row-header"><div class="local-runtime-row-copy"><span class="local-runtime-row-icon">${uiIcon("cpu")}</span><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(runtimeName)} · ${item.enabled ? "已启用" : "已停用"}</small></div></div><button type="button" class="local-runtime-remove" data-action="remove-local-model" data-local-index="${index}">移除</button></header><details class="local-runtime-edit"><summary>编辑连接</summary><div class="quiet-runtime-fields"><label><span>名称</span><input data-local-name="${index}" value="${escapeHtml(item.name)}" /></label><label><span>运行时</span><input data-local-runtime="${index}" value="${escapeHtml(item.runtime)}" /></label><label><span>地址</span><input data-local-url="${index}" value="${escapeHtml(item.base_url || "")}" placeholder="http://127.0.0.1:11434/v1" /></label><label><span>模型 ID</span><input data-local-model="${index}" value="${escapeHtml(item.model_id || "")}" placeholder="例如 qwen3:8b" /></label><label class="quiet-switch"><input type="checkbox" data-local-enabled="${index}" ${item.enabled ? "checked" : ""} /><span>允许助手使用</span></label><div><button type="button" class="quiet-text-button" data-action="test-local-model" data-local-id="${escapeHtml(item.id)}">测试连接</button></div></div></details></article>`;
    })
    .join("") || '<div class="quiet-empty">没有手动连接也没关系，ScanSci 会优先检测本地能力。</div>';
  return `<section class="runtime-connections-panel"><header><div><h2>外部运行时</h2><p>连接你已经运行的 Ollama、LM Studio、llama.cpp 或 Strata。ScanSci 负责模型路由和工具授权。</p></div><span class="runtime-connection-count">${runtimeAddressCount ? `${runtimeAddressCount} 个服务地址` : "可选"}</span></header>${renderStrataSetupMarkup()}${presets ? `<div class="local-runtime-add"><strong>添加已有运行时</strong><div class="quiet-add-chips">${presets}</div></div>` : ""}<form id="localModelsForm" class="quiet-runtime-list">${runtimeRows}<footer><button type="submit" class="quiet-primary-button">保存连接</button></footer></form></section>`;
}

function renderRuntimeSettings() {
  const runtime = state.localRuntime || { installed: false, install_available: false, mode: "missing" };
  const runtimeReady = Boolean(runtime.installed) && !runtime.update_required;
  const runtimeJob = runtime.install_job || {};
  const runtimeInstalling = ["queued", "installing", "downloading", "pausing", "cancelling"].includes(String(runtimeJob.state || ""));
  const runtimeChecking = Boolean(runtime.checking);
  const runtimeNeedsRetry = ["failed", "cancelled", "interrupted"].includes(String(runtimeJob.state || ""));
  const runtimeProgress = Math.max(0, Math.min(100, Math.round(Number(runtimeJob.progress || 0) * 100)));
  const runtimeStatus = runtimeReady
    ? "已就绪"
    : runtimeInstalling
      ? `准备中 ${runtimeProgress}%`
      : runtime.update_required
        ? "需要更新"
      : runtimeNeedsRetry
          ? "安装未完成"
          : "尚未安装";
  const runtimeAction = runtimeReady
    ? `<span class="runtime-primary-status is-ready">${uiIcon("check")} 运行时已就绪</span>`
    : runtimeInstalling
      ? `<span class="runtime-primary-status">${escapeHtml(runtimeJob.message || "正在准备本地运行时")} · ${runtimeProgress}%</span>`
      : runtime.install_available
        ? `<button type="button" class="quiet-primary-button" data-action="install-local-runtime">${uiIcon(runtimeNeedsRetry || runtime.update_required ? "refresh" : "download")} ${runtimeNeedsRetry ? "继续安装" : runtime.update_required ? "更新运行时" : "安装运行时"}</button>`
        : `<button type="button" class="quiet-primary-button" data-action="choose-local-runtime-files">${uiIcon("download")} 选择本地组件包</button>`;
  const runtimeRecovery = runtime.update_required ? localRuntimeChannelRecoveryMarkup(runtime)
    : (!runtimeReady && (runtimeInstalling || runtimeNeedsRetry || runtime.channels?.checked_at) ? localRuntimeChannelRecoveryMarkup(runtime) : "");
  const runtimeMode = runtime.mode === "component"
    ? "独立组件"
    : runtime.mode === "system"
      ? "复用系统"
      : runtime.mode === "embedded"
        ? "随应用提供"
        : runtime.mode === "source"
          ? "开发环境"
          : "未配置";
  return `<section class="settings-minimal-page runtime-settings-page">
    <header class="settings-page-heading"><div><h1>运行时</h1><p>用户可以在这里安装、更新或重新检测本地运行组件；模型权重仍由“本地模型”单独管理。</p></div><span class="save-indicator">按需安装</span></header>
    <section class="settings-runtime-card runtime-lifecycle-card"><header><div><h2>本地 AI 运行时</h2><p>为本地 Transformers 模型提供加载、推理和健康检查能力。它是独立组件，不会让主程序包变大。</p></div><span class="runtime-status-pill is-${runtimeReady ? "ready" : runtimeInstalling ? "loading" : "pending"}">${escapeHtml(runtimeStatus)}</span></header><div class="runtime-lifecycle-summary"><div><span>版本</span><strong>${escapeHtml(runtime.version || "—")}</strong></div><div><span>安装方式</span><strong>${escapeHtml(runtimeMode)}</strong></div><div><span>模型权重</span><strong>按需下载</strong></div></div><p class="runtime-lifecycle-boundary">这里只管理运行组件本身，不选择模型，也不修改 Ollama、LM Studio 等外部连接。已下载模型不会重复下载；模型已存在；更新运行组件后可直接使用。</p><footer>${runtimeAction}<button type="button" class="quiet-text-button" data-action="refresh-local-runtime" ${runtimeInstalling || runtimeChecking ? "disabled" : ""}>${uiIcon("refresh")} ${runtimeChecking ? "检测中…" : "重新检测"}</button></footer></section>
    ${runtimeRecovery}
    ${renderExternalRuntimeConnectionsMarkup()}
  </section>`;
}

// Keep the legacy cleanup in a shared helper so the local-model page itself
// does not carry a duplicate routing-card selector in its rendered function.
function stripLegacyLocalAgentRouting(page) {
  return page.replace(/<section class="local-agent-routing-card">[\s\S]*?<\/section>\s*/, "");
}

function renderLocalModelsSettings() {
  const installedItems = state.localModelMarket?.installed || [];
  const ollamaItems = (state.ollama?.models || []).filter((item) => String(item?.name || item?.model || "").trim());
  const isUnavailableInstalledModel = (item) => item.runtime_compatible === false || item.runtime_probe_state === "failed";
  const usableInstalledCount = installedItems.filter((item) => (
    item.ready && !isUnavailableInstalledModel(item) && item.runtime_probe_state !== "pending"
  )).length;
  const unavailableInstalledCount = installedItems.filter(isUnavailableInstalledModel).length;
  const incompleteInstalledCount = installedItems.length - usableInstalledCount - unavailableInstalledCount;
  const installedSummary = [
    installedItems.length ? `${usableInstalledCount} 快照可用${incompleteInstalledCount ? ` · ${incompleteInstalledCount} 未完成` : ""}${unavailableInstalledCount ? ` · ${unavailableInstalledCount} 不可用` : ""}` : "",
    ollamaItems.length ? `${ollamaItems.length} 个 Ollama 模型` : "",
  ].filter(Boolean).join(" · ") || "0 已安装";
  const runtime = state.localRuntime || { installed: false, install_available: false, mode: "missing" };
  const runtimeReady = Boolean(runtime.installed) && !runtime.update_required;
  const ollama = state.ollama || {};
  const installedSnapshots = installedItems.map((item) => {
    const size = `${(Number(item.size_bytes || 0) / 1024 / 1024 / 1024).toFixed(1)} GB`;
    const kind = item.kind || (/(embedding|embed|bge|gte|e5-)/i.test(item.id || "") ? "embedding" : /(rerank)/i.test(item.id || "") ? "reranking" : "chat");
    const unsupportedAudio = kind === "audio" && item.runtime_compatible === false;
    const incompatible = item.runtime_compatible === false || item.runtime_probe_state === "failed";
    const pendingProbe = item.runtime_probe_state === "pending";
    const status = incompatible ? (unsupportedAudio ? "当前格式不可运行" : "不可用") : pendingProbe ? "待验证" : item.ready ? "可用" : "未完成";
    const statusClass = incompatible ? "is-unavailable" : pendingProbe || !item.ready ? "is-incomplete" : "is-ready";
    const icon = item.icon_url
      ? `<img class="model-market-icon" data-site-icon="true" src="/api/site-icon?url=${encodeURIComponent(item.icon_url)}" alt="" loading="lazy" decoding="async" />`
      : `<span class="quiet-model-mark">${kind === "chat" ? "◎" : "◇"}</span>`;
    const probeable = (kind === "vision" || (kind === "chat" && /ForCausalLM|ForConditionalGeneration/i.test(String(item.architecture || ""))))
      && item.model_files_present !== false;
    const probeAction = incompatible && probeable
      ? `<button type="button" class="quiet-text-button" data-action="retry-local-model-probe" data-model-id="${escapeHtml(item.id)}">重新检测</button>`
      : "";
    const warning = (incompatible || pendingProbe) && item.runtime_message
      ? `<small class="quiet-model-warning" title="${escapeHtml(item.runtime_message)}">${escapeHtml(item.runtime_message)}</small>`
      : "";
    return `<article class="quiet-model-row local-installed-model-row">${icon}<div class="local-installed-model-copy"><strong>${escapeHtml(item.name)}</strong><div class="local-capability-tags"><span>${escapeHtml(({ chat: "对话", embedding: "嵌入", reranking: "重排", vision: "视觉", audio: "语音" }[kind] || "通用"))}</span>${item.format ? `<span>${escapeHtml(item.format)}</span>` : ""}</div>${warning}</div><span class="quiet-row-note local-installed-status ${statusClass}">${status}</span><span class="quiet-row-size">${size}</span><div class="local-installed-actions">${probeAction}<button type="button" class="quiet-danger-button local-installed-remove" data-action="delete-installed-local-model" data-model-id="${escapeHtml(item.id)}" title="删除 ${escapeHtml(item.name)}" aria-label="删除 ${escapeHtml(item.name)}">${uiIcon("trash")}</button></div></article>`;
  }).join("");
  const installedOllama = ollamaItems.map((item) => {
    const name = String(item.name || item.model || "").trim();
    const size = Number(item.size || 0) > 0 ? `${(Number(item.size) / 1024 / 1024 / 1024).toFixed(1)} GB` : "—";
    return `<article class="quiet-model-row local-installed-model-row"><span class="quiet-model-mark">◉</span><div class="local-installed-model-copy"><strong>${escapeHtml(name)}</strong><div class="local-capability-tags"><span>Ollama</span><span>已安装</span></div></div><span class="quiet-row-note local-installed-status is-ready">已安装</span><span class="quiet-row-size">${size}</span><div class="local-installed-actions"><span class="quiet-row-note">由 Ollama 管理</span></div></article>`;
  }).join("");
  const installed = installedSnapshots + installedOllama || '<div class="quiet-empty">未发现本地模型。</div>';
  const audioRuntimeReady = runtimeReady && ["source", "embedded", "component"].includes(String(state.localRuntime?.mode || ""));
  const marketCatalog = (state.localModelMarket?.catalog || []).map((item) => {
    const isOllama = String(item.runtime || "").toLowerCase() === "ollama";
    const ready = Boolean(item.ready);
    const installed = isOllama ? ready : Boolean(item.installed);
    const canDownload = isOllama ? Boolean(ollama.reachable) : item.kind === "audio" ? audioRuntimeReady : runtimeReady;
    const job = (state.localModelInstall?.jobs || []).find((candidate) => (
      Array.isArray(candidate?.models) && candidate.models.includes(item.id)
    )) || null;
    const jobState = String(job?.state || "");
    const jobActive = ["queued", "downloading", "installing", "pausing", "cancelling"].includes(jobState);
    const jobRetryable = ["failed", "cancelled", "interrupted", "paused"].includes(jobState);
    const action = installed
      ? `<span class="quiet-row-note">已安装</span>`
      : jobActive
        ? `<span class="quiet-row-note">${escapeHtml(downloadJobStatus(job).label)} · ${escapeHtml(downloadJobProgressSummary(job))}</span>`
      : jobRetryable && job?.job_id
        ? `<button type="button" class="quiet-text-button" data-action="control-download-task" data-download-kind="model" data-download-action="${jobState === "paused" ? "resume" : "retry"}" data-job-id="${escapeHtml(job.job_id)}">${jobState === "paused" ? "继续" : "重试"}</button>`
      : canDownload
        ? `<button type="button" class="quiet-text-button" data-action="download-local-model" data-model-repo="${escapeHtml(item.id)}" data-model-runtime="${escapeHtml(item.runtime || "huggingface")}">下载</button>`
        : isOllama
          ? `<button type="button" class="quiet-text-button" data-action="open-ollama-setup">连接已有 Ollama</button>`
          : `<button type="button" class="quiet-text-button" data-action="open-local-runtime-setup">查看运行时</button>`;
    const status = isOllama && !ollama.reachable && !installed ? "需要 Ollama" : ready ? "已就绪" : job ? downloadJobStatus(job).label : "未下载";
    const icon = item.icon_url
      ? `<img class="model-market-icon" data-site-icon="true" src="/api/site-icon?url=${encodeURIComponent(item.icon_url)}" alt="" loading="lazy" decoding="async" />`
      : `<span class="quiet-model-mark is-muted">${isOllama ? "◉" : "↓"}</span>`;
    return `<article class="quiet-model-row">${icon}<div><strong>${escapeHtml(item.name)}</strong><p>${escapeHtml(item.description || "本地模型")}${item.size_hint ? ` · ${escapeHtml(item.size_hint)}` : ""}</p><div class="local-capability-tags"><span>${escapeHtml(({ chat: "对话", embedding: "嵌入", reranking: "重排", vision: "视觉", audio: "语音" }[item.kind] || "通用"))}</span><span>${status}</span></div></div>${action}</article>`;
  }).join("") || '<div class="quiet-empty">市场目录暂不可用。</div>';
  return `<section class="quiet-settings-page local-models-page local-models-page--managed">
    ${renderLocalModelRecommendation()}
    <details class="local-model-disclosure local-model-market-disclosure" open><summary><span>模型市场</span><em>按需下载</em></summary><div class="local-model-disclosure-body"><form id="localModelMarketSearch" class="local-model-market-search"><input name="query" type="search" value="${escapeHtml(state.localModelMarket?.query || "")}" placeholder="搜索模型，例如 embedding、reranker、Qwen" aria-label="搜索模型市场" /><button type="submit" class="quiet-text-button">搜索</button></form><div class="quiet-model-list">${marketCatalog}</div></div></details>
    <section class="local-installed-panel"><header><div><span>INSTALLED</span><h2>已安装模型</h2><p>这里显示本地模型快照及 Ollama 中已有的模型。</p></div><b>${escapeHtml(installedSummary)}</b></header><div class="quiet-model-list local-installed-model-list">${installed}</div></section>
    </section>`;
}

function renderLocalModelRecommendation() {
  const choice = state.localModelMarket?.recommendation;
  if (!choice) return '<section class="local-model-recommendation"><span>本机推荐</span><p>正在检测硬件与模型状态…</p></section>';
  if (!choice.available) return `<section class="local-model-recommendation"><span>本机推荐</span><p>${escapeHtml(choice.reason || "暂时没有合适的本地主力模型。")}</p></section>`;
  const hardware = choice.hardware || {};
  const gpu = hardware.gpu_name ? `${hardware.gpu_name} · ${hardware.vram_gib} GB 显存` : "未检测到 NVIDIA 显卡";
  const memory = hardware.ram_gib ? `${hardware.ram_gib} GB 内存` : "内存待确认";
  const disk = hardware.storage_free_gib == null ? "" : ` · 模型盘剩余 ${hardware.storage_free_gib} GB`;
  const active = state.settings?.active_model?.model_id === choice.id
    && String(state.settings?.active_model?.provider_id || "").startsWith("local-runtime-ollama-");
  const job = (state.localModelInstall?.jobs || []).find((item) => item.job_id === `ollama:${choice.id}`);
  const installing = ["queued", "downloading", "pausing", "cancelling"].includes(String(job?.state || ""));
  const action = active
    ? '<span class="local-model-recommendation-status">当前主力模型</span>'
    : choice.installed
      ? `<button type="button" class="quiet-primary-button" data-action="activate-recommended-model" data-model-id="${escapeHtml(choice.id)}">验证并设为主力</button>`
      : installing
        ? `<span class="local-model-recommendation-status">${escapeHtml(downloadJobStatus(job).label)} · ${escapeHtml(downloadJobProgressSummary(job))}</span>`
        : choice.storage_blocked
          ? '<span class="local-model-recommendation-status">模型盘空间不足</span>'
          : choice.runtime_reachable
            ? `<button type="button" class="quiet-primary-button" data-action="download-local-model" data-model-repo="${escapeHtml(choice.id)}" data-model-runtime="ollama">在已有 Ollama 中安装</button>`
            : '<button type="button" class="quiet-text-button" data-action="open-ollama-setup">连接 Ollama 后安装</button>';
  return `<section class="local-model-recommendation"><div><span>已有 Ollama · 本机推荐</span><h2>${escapeHtml(choice.name)}</h2><p>${escapeHtml(choice.reason)}</p><small>${escapeHtml(gpu)} · ${escapeHtml(memory)}${escapeHtml(disk)}</small><small>下载约 ${escapeHtml(choice.download_gb)} GB · Pi 任务上下文 ${escapeHtml(choice.context_tokens)} tokens；安装不会替换现有模型。</small></div>${action}</section>`;
}

// Local model routing is automatic and is already represented by the default
// capabilities page. Keep the local-model page focused on installation and
// runtime management instead of rendering the same routing matrix twice.
function renderLocalModelsSettingsPage() {
  const page = renderLocalModelsSettings();
  const installed = Array.isArray(state.localModelMarket?.installed)
    ? state.localModelMarket.installed
    : [];
  const ready = installed.filter((item) => (
    item?.ready && item?.runtime_compatible !== false && item?.runtime_probe_state !== "pending"
  ));
  const conversationReady = ready.filter((item) => {
    const capabilities = Array.isArray(item?.capabilities) ? item.capabilities : [];
    return capabilities.includes("chat") || capabilities.includes("vision") || item?.kind === "vision";
  }).length;
  const auxiliaryReady = ready.filter((item) => {
    const capabilities = Array.isArray(item?.capabilities) ? item.capabilities : [item?.kind];
    return capabilities.some((capability) => ["embedding", "reranking", "audio"].includes(String(capability)));
  }).length;
  const detectionNote = installed.length
    ? `已发现 ${installed.length} 个本地模型；${conversationReady} 个可用于对话/视觉，${auxiliaryReady} 个用于检索、重排或语音。不同入口只显示可用模型。`
    : "尚未发现本地模型；下载完成后重新检测，系统会自动识别模型能力。";
  return stripLegacyLocalAgentRouting(page)
    .replace(
      '<section class="local-installed-panel">',
      `<section class="local-model-detection-note"><span>${escapeHtml(detectionNote)}</span><button type="button" class="quiet-text-button" data-action="refresh-local-model-market">${state.localModelMarket?.loading ? "检测中…" : `${uiIcon("refresh")}重新检测`}</button></section><section class="local-installed-panel">`,
    );
}

function renderDocumentProcessingFormMarkup(formId = "documentProcessingForm", embedded = false) {
  const processing = state.settings.document_processing || {};
  const ocr = processing.ocr || { provider: "tesseract", base_url: "", languages: ["zh", "en"], enabled: true };
  const mineru = processing.mineru || { provider: "mineru", base_url: "https://mineru.net", enabled: false };
  const ocrLanguages = new Set(ocr.languages || []);
  const ocrLanguageOptions = [["zh", "中文"], ["en", "English"]]
    .map(([value, label]) => `<option value="${value}" ${ocrLanguages.has(value) ? "selected" : ""}>${label}</option>`)
    .join("");
  const tesseractSelected = ocr.provider === "tesseract";
  const paddleSelected = ocr.provider === "paddle";
  const deepseekSelected = ocr.provider === "deepseek";
  const systemStatus = state.systemOcrStatus || {};
  const systemTone = systemStatus.loading ? "checking" : systemStatus.available && systemStatus.requested_supported !== false ? "ready" : "warning";
  const ocrStatusName = tesseractSelected ? "Tesseract OCR" : "Windows OCR";
  const systemTitle = systemStatus.loading
    ? `正在检测 ${ocrStatusName}`
    : tesseractSelected
      ? systemStatus.available && systemStatus.requested_supported !== false
        ? "Tesseract 已就绪"
        : "Tesseract 尚未就绪"
    : systemStatus.available && systemStatus.requested_supported !== false
      ? "检测到 Windows OCR 引擎可用"
      : systemStatus.available
        ? "检测到引擎，但缺少当前语言包"
        : "未检测到 Windows OCR 引擎";
  const installedLanguages = Array.isArray(systemStatus.languages) && systemStatus.languages.length
    ? `已安装识别语言：${systemStatus.languages.slice(0, 8).join("、")}${systemStatus.languages.length > 8 ? " 等" : ""}`
    : tesseractSelected ? "未检测到 Tesseract 语言数据" : "需要安装 Windows OCR 语言包后才能使用";
  const ocrSolution = systemStatus.solution || (systemStatus.available && systemStatus.requested_supported !== false
    ? installedLanguages
    : tesseractSelected
      ? "解决：安装 Tesseract 后点击重新检测；中文识别还需要 chi_sim.traineddata。"
      : "解决：打开 Windows 设置 → 时间和语言 → 语言和区域，安装对应语言包；也可以切换为 Tesseract。");
  const systemOcrReady = !systemStatus.loading && systemStatus.available && systemStatus.requested_supported !== false;
  const tesseractInstall = systemStatus.install || {};
  const tesseractInstalling = ["queued", "installing", "downloading"].includes(String(tesseractInstall.state || ""));
  const ocrRepairAction = tesseractSelected
    ? `<button type="button" class="system-ocr-refresh is-primary" data-action="install-tesseract-ocr" ${systemStatus.loading || tesseractInstalling ? "disabled" : ""}>${tesseractInstalling ? `${Math.round(Number(tesseractInstall.progress || 0) * 100)}%` : systemStatus.available ? "安装缺失语言" : "安装 Tesseract"}</button>`
    : "";
  const systemOcrGuide = systemOcrReady ? "" : `<aside class="system-ocr-status is-${systemTone} is-${tesseractSelected ? "tesseract" : "windows"}"><span class="system-ocr-status-icon">${systemStatus.loading ? "…" : systemStatus.available && systemStatus.requested_supported !== false ? uiIcon("check") : uiIcon("info")}</span><div class="system-ocr-status-copy"><strong>${systemTitle}</strong><p>${escapeHtml(tesseractInstalling ? tesseractInstall.message || "正在安装 Tesseract OCR…" : systemStatus.message || `${ocrStatusName} 可用于本地识别图片文字。`)}</p><small>${escapeHtml(tesseractInstall.error || ocrSolution || installedLanguages)}</small></div><div class="system-ocr-status-actions">${ocrRepairAction}<button type="button" class="system-ocr-refresh" data-action="refresh-system-ocr-status" ${systemStatus.loading || tesseractInstalling ? "disabled" : ""}>重新检测</button></div></aside>`;
  const ocrConnection = deepseekSelected ? `<div class="document-service-fields"><label class="setting-field"><span>硅基流动 API 地址</span><input name="ocr-base-url" value="${escapeHtml(ocr.base_url || "https://api.siliconflow.cn/v1")}" placeholder="https://api.siliconflow.cn/v1" maxlength="500" /></label><label class="setting-field"><span>硅基流动 API 密钥</span><input name="ocr-api-key" type="password" autocomplete="new-password" placeholder="${ocr.api_key_configured ? "已保存在系统凭据管理器；输入新值以替换" : "粘贴硅基流动 API 密钥"}" /></label></div><p class="document-service-note">使用模型 <code>deepseek-ai/DeepSeek-OCR</code> 将图片发送到硅基流动；未配置密钥或调用失败时自动回退到本地 OCR。<a href="https://cloud.siliconflow.cn/models?target=deepseek-ai/DeepSeek-OCR" target="_blank" rel="noreferrer">查看模型与额度 ${uiIcon("arrow-up-right")}</a></p>` : ocr.provider === "custom" ? `<div class="document-service-fields"><label class="setting-field"><span>API 地址</span><input name="ocr-base-url" value="${escapeHtml(ocr.base_url || "")}" placeholder="https://ocr.example.com/v1" maxlength="500" /></label><label class="setting-field"><span>API 密钥</span><input name="ocr-api-key" type="password" autocomplete="new-password" placeholder="${ocr.api_key_configured ? "已保存在系统凭据管理器；输入新值以替换" : "可选，保存后仅存于系统凭据管理器"}" /></label></div>` : tesseractSelected || ocr.provider === "system" ? systemOcrGuide : "";
  const paddleGuide = paddleSelected ? `<aside class="paddle-ocr-guide is-configuring"><span class="paddle-ocr-guide-icon">P</span><div class="paddle-ocr-guide-main"><header><strong>PaddleOCR</strong><em>飞桨 AI Studio</em></header><p>需要 PaddleOCR 时填写 Access Token；令牌只保存在这台电脑。</p><label class="setting-field paddle-ocr-token-field"><span>Access Token</span><input name="ocr-api-key" type="password" autocomplete="new-password" placeholder="${ocr.api_key_configured ? "已保存；输入新值以替换" : "粘贴 AI Studio Access Token"}" ${ocr.api_key_configured ? "" : "required"} /></label></div><div class="paddle-ocr-guide-actions"><a href="https://aistudio.baidu.com/account/accessToken" target="_blank" rel="noreferrer">获取 Token ${uiIcon("arrow-up-right")}</a></div></aside>` : "";
  const mineruName = mineru.provider === "mineru" ? "MinerU" : "自定义文档解析服务";
  const heading = embedded
    ? `<header class="default-tools-heading"><div><span>文档处理工具</span><h2>OCR 与文档解析</h2><p>这里选择的是处理工具，不是对话模型。密钥只保存在这台电脑的系统凭据管理器中。</p></div><em>按需配置</em></header>`
    : settingsHeading("文档处理", "配置扫描页识别与学术 PDF 解析服务。密钥不会写入工作区文件。");
  return `${heading}
    <form id="${escapeHtml(formId)}" class="document-processing-form${embedded ? " embedded-document-processing-form" : ""}">
      <section class="document-service-card"><div class="document-service-heading"><div><span class="document-service-icon">O</span><div><h2>OCR ${settingHelpMarkup("用于识别图片内文字。", "OCR 说明")}</h2><p>从扫描 PDF、图像和无法直接复制的页面提取文字。</p></div></div><label class="switch-label"><input name="ocr-enabled" type="checkbox" ${ocr.enabled ? "checked" : ""} />启用</label></div><div class="document-service-rule"></div><label class="document-select-row"><span>OCR 服务提供商</span><select name="ocr-provider"><option value="tesseract" ${tesseractSelected ? "selected" : ""}>Tesseract OCR</option><option value="system" ${ocr.provider === "system" ? "selected" : ""}>Windows OCR</option><option value="paddle" ${paddleSelected ? "selected" : ""}>PaddleOCR（AI Studio）</option><option value="deepseek" ${deepseekSelected ? "selected" : ""}>DeepSeek-OCR（硅基流动）</option><option value="custom" ${ocr.provider === "custom" ? "selected" : ""}>自定义 OCR API</option></select></label>${paddleGuide}<label class="document-language-row"><span>识别语言</span><select name="ocr-language" multiple size="2" aria-label="识别语言">${ocrLanguageOptions}</select></label>${ocrConnection}</section>
      <section class="document-service-card"><div class="document-service-heading"><div><span class="document-service-icon">M</span><div><h2>文档处理 ${settingHelpMarkup("用于按版面解析论文，保留段落、表格、公式和图片结构。", "文档处理说明")}</h2><p>按版面保留论文的段落、表格、公式与图片结构。</p></div></div><label class="switch-label"><input name="mineru-enabled" type="checkbox" ${mineru.enabled ? "checked" : ""} />启用</label></div><div class="document-service-rule"></div><label class="document-select-row"><span>文档处理服务商</span><select name="mineru-provider"><option value="mineru" ${mineru.provider === "mineru" ? "selected" : ""}>MinerU</option><option value="custom" ${mineru.provider === "custom" ? "selected" : ""}>自定义解析 API</option></select></label><div class="document-service-fields"><label class="setting-field"><span>${escapeHtml(mineruName)} API 密钥</span><input name="mineru-api-key" type="password" autocomplete="new-password" placeholder="${mineru.api_key_configured ? "已保存在系统凭据管理器；输入新值以替换" : "输入后仅保存至系统凭据管理器"}" /></label><label class="setting-field"><span>API 地址</span><input name="mineru-base-url" value="${escapeHtml(mineru.base_url || "")}" placeholder="https://mineru.net" maxlength="500" /></label></div><p class="document-service-note">密钥仅保存在当前电脑的系统凭据管理器中；启用后，PDF 导入会优先使用 MinerU，失败时回退本地解析。</p></section>
      <div class="settings-footer-actions"><button type="submit" class="save-button">保存文档处理配置</button></div>
    </form>`.replace('<h2>OCR 服务</h2>', '<h2>OCR</h2>').replace('<h2>文档解析</h2>', '<h2>文档处理</h2>');
}

function renderDocumentProcessingSettings() {
  return renderDocumentProcessingFormMarkup();
}

function collectDocumentProcessingForm(formId = "") {
  const form = byId(formId) || byId("documentProcessingForm") || byId("defaultDocumentProcessingForm");
  if (!form) return state.settings.document_processing;
  const languageOptions = [...form.querySelectorAll('select[name="ocr-language"] option:checked')];
  const languageInputs = [...form.querySelectorAll('input[name="ocr-language"]:checked')];
  state.settings.document_processing = {
    ocr: {
      provider: form.elements["ocr-provider"].value,
      base_url: form.elements["ocr-base-url"]?.value.trim() || "",
      languages: (languageOptions.length ? languageOptions : languageInputs).map((input) => input.value),
      enabled: form.elements["ocr-enabled"].checked,
    },
    mineru: {
      provider: form.elements["mineru-provider"].value,
      base_url: form.elements["mineru-base-url"].value.trim(),
      enabled: form.elements["mineru-enabled"].checked,
    },
  };
  return state.settings.document_processing;
}

function uniqueModelLabelParts(parts) {
  const seen = new Set();
  return parts
    .map((part) => String(part || "").trim())
    .filter((part) => {
      const key = part.toLocaleLowerCase();
      if (!part || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function modelOptionLabel(meta, modelName) {
  const name = String(modelName || "").trim();
  const metaParts = String(meta || "")
    .split("·")
    .map((part) => part.trim())
    .filter(Boolean);
  if (metaParts[metaParts.length - 1] === name) metaParts.pop();
  return [...uniqueModelLabelParts(metaParts), name].filter(Boolean).join(" · ");
}

function modelOptionMeta(provider, source = "") {
  const providerName = String(provider?.name || "").trim();
  if (provider?.kind === "local") return providerName || "本地";
  if (provider?.auth_mode === "local") {
    const runtime = String(provider?.runtime || "").trim().toLowerCase();
    return {
      ollama: "Ollama",
      "lm-studio": "LM Studio",
      "llama.cpp": "llama.cpp",
      "local-huggingface": "本地 Hugging Face",
    }[runtime] || "本地运行时";
  }
  const sourceName = provider?.auth_mode === "managed" ? "ScanSci" : source;
  return uniqueModelLabelParts([sourceName, providerName]).join(" · ");
}

function localModelOptionMeta(model) {
  const runtime = String(model?.runtime || "").trim().toLowerCase();
  return {
    builtin: "ScanSci",
    ollama: "Ollama",
    "lm-studio": "LM Studio",
    "llama.cpp": "llama.cpp",
    "local-huggingface": "本地 Hugging Face",
  }[runtime] || "本地";
}

function settingsModelOptionAttributes(modelName, modelMeta) {
  return `data-model-name="${escapeHtml(modelName)}" data-model-meta="${escapeHtml(modelMeta)}"`;
}

function capabilityOptionKey(capability, modelName, modelMeta) {
  if (capability !== "audio") return "";
  return [modelName, modelMeta]
    .map((value) => String(value || "").trim().toLocaleLowerCase().replace(/\s+/g, " "))
    .join("::");
}

function isPreferredAudioModel(model) {
  const modelId = String(model?.model_id || model?.id || "").trim().toLocaleLowerCase();
  return modelId === "qwen/qwen3-asr-0.6b-hf" || modelId === "qwen3-asr-0.6b-hf";
}

function modelTargetOptions(selected = "", capability = "") {
  const automatic = selected === "auto" || selected === "local:builtin-evidence";
  const options = [`<option value="auto" ${automatic ? "selected" : ""} ${settingsModelOptionAttributes("Agent 自动选择（推荐）", "ScanSci Agent")}>Agent 自动选择（推荐）</option>`];
  const seenCapabilityOptions = new Set();
  const addOption = (value, modelName, modelMeta) => {
    const optionKey = capabilityOptionKey(capability, modelName, modelMeta);
    if (optionKey && seenCapabilityOptions.has(optionKey)) return;
    if (optionKey) seenCapabilityOptions.add(optionKey);
    options.push(`<option value="${escapeHtml(value)}" ${value === selected ? "selected" : ""} ${settingsModelOptionAttributes(modelName, modelMeta)}>${escapeHtml(modelOptionLabel(modelMeta, modelName))}</option>`);
  };
  for (const provider of (state.settings.providers || []).filter(isProviderUsable)) {
    if (provider.id === "local-evidence") continue;
    for (const model of provider.models || []) {
      if (capability && !(model.capabilities || []).includes(capability)) continue;
      const value = `provider:${provider.id}:${model.id}`;
      const source = provider.kind === "local" ? "本地" : provider.auth_mode === "managed" ? "ScanSci" : "API";
      const modelName = String(model.name || model.id);
      const modelMeta = modelOptionMeta(provider, source);
      addOption(value, modelName, modelMeta);
    }
  }
  const localModels = [...(state.settings.local_models || [])].sort((left, right) => {
    if (capability !== "audio") return 0;
    return Number(isPreferredAudioModel(right)) - Number(isPreferredAudioModel(left));
  });
  for (const model of localModels) {
    if (model.enabled === false || model.runtime_compatible === false) continue;
    const capabilities = new Set(model.capabilities || []);
    const builtinRetrieval = model.runtime === "builtin" && ["embedding", "reranking", "retrieval"].includes(capability);
    if (builtinRetrieval) continue;
    if (capability && !capabilities.has(capability) && !builtinRetrieval) continue;
    const value = `local:${model.id}`;
    const modelName = String(model.name || model.id);
    const modelMeta = localModelOptionMeta(model);
    addOption(value, modelName, modelMeta);
  }
  return options.join("");
}

function defaultConversationModelOptions(selected = "") {
  const options = ['<option value="">未指定（使用系统回退）</option>'];
  const seen = new Set();
  for (const provider of (state.settings.providers || [])) {
    for (const model of provider.models || []) {
      const value = `${provider.id}::${model.id}`;
      if (seen.has(value) || !isConversationModel(model)) continue;
      const availability = conversationModelAvailability(model, provider);
      if (!availability.visible) continue;
      seen.add(value);
      const source = isLocalModelProvider(provider) ? "\u672c\u5730" : provider.auth_mode === "managed" ? "ScanSci" : "API";
      const modelName = String(model.name || model.id);
      const modelMeta = modelOptionMeta(provider, source);
      const label = `${modelOptionLabel(modelMeta, modelName)}${availability.selectable ? "" : ` · ${availability.label}`}`;
      options.push(`<option value="${escapeHtml(value)}" ${value === selected ? "selected" : ""} ${availability.selectable ? "" : "disabled"} ${settingsModelOptionAttributes(modelName, modelMeta)}>${escapeHtml(label)}</option>`);
    }
  }
  return options.join("");
}

function settingHelpMarkup(description, label = "查看说明") {
  const text = String(description || "").trim();
  if (!text) return "";
  return `<span class="setting-help"><button type="button" class="setting-help-button" data-help-toggle aria-label="${escapeHtml(label)}" aria-expanded="false">?</button><span class="setting-help-popover" role="tooltip">${escapeHtml(text)}</span></span>`;
}

function defaultCapabilityRow({ id, title, description, capability, conversation = false }) {
  const roles = state.settings.model_roles || {};
  const options = conversation
    ? defaultConversationModelOptions(`${state.settings.active_model?.provider_id || ""}::${state.settings.active_model?.model_id || ""}`)
    : modelTargetOptions(roles[id] || "", capability);
  return `<div class="default-capability-row"><span class="default-capability-copy"><span class="default-capability-label"><strong>${escapeHtml(title)}</strong>${settingHelpMarkup(description, `${title}说明`)}</span></span><select name="${conversation ? "default-conversation-model" : `model-role-${id}`}" ${conversation ? "data-default-conversation-model" : `data-model-role="${escapeHtml(id)}"`}>${options}</select></div>`;
}

function defaultCapabilityPanel(eyebrow, title, description, rows) {
  return `<section class="settings-minimal-section default-capability-panel"><header><h2>${escapeHtml(title)}</h2></header><div class="default-capability-list">${rows}</div></section>`;
}

function renderDefaultCapabilitiesSettings() {
  const assistantRows = defaultCapabilityRow({ conversation: true, title: "默认助手模型", description: "对话、任务、写作和演示统一使用。" });
  const multimodalRows = [
    { id: "vision", title: "视觉模型", description: "理解图片、图表和扫描页面。", capability: "vision" },
    { id: "image", title: "图像生成", description: "根据文字生成并保存图片。", capability: "image" },
    { id: "audio", title: "语音模型", description: "把录音转成文字。", capability: "audio" },
  ].map((item) => defaultCapabilityRow(item)).join("");
  const retrievalRows = [
    { id: "embedding", title: "嵌入模型", description: "建立知识库语义索引。", capability: "embedding" },
    { id: "reranking", title: "重排模型", description: "提高证据片段排序。", capability: "reranking" },
    { id: "retrieval", title: "基础检索", description: "语义模型不可用时的关键词回退。", capability: "retrieval" },
  ].map((item) => defaultCapabilityRow(item)).join("");
  const remoteRetrievalSelected = ["embedding", "reranking"].some((role) => String(state.settings.model_roles?.[role] || "").startsWith("provider:siliconflow:"));
  const siliconflow = (state.settings.providers || []).find((provider) => provider.id === "siliconflow");
  const remoteRetrievalNotice = remoteRetrievalSelected && !siliconflow?.remote_rag_allowed
    ? '<p class="default-capability-consent-warning" role="status">⚠ 已选择硅基流动检索模型。请在“模型服务 → 硅基流动”开启“允许云端知识库检索”；授权前继续使用本地检索。</p>'
    : "";
  return `<section class="settings-minimal-page default-capabilities-page"><header class="settings-page-heading default-capabilities-heading"><div><h1>默认能力</h1></div><button type="button" class="quiet-text-button" data-action="open-resource-guide">添加本地能力 ${uiIcon("arrow-right")}</button></header>
    <form id="defaultCapabilitiesForm"><div class="settings-minimal-sections">
      ${defaultCapabilityPanel("助手", "默认助手", "对话、任务、写作和演示使用同一个默认模型。", assistantRows)}
      ${defaultCapabilityPanel("多模态", "视觉、图像与语音", "图像理解、图像生成和语音能力可以分别选择模型。", multimodalRows)}
      ${defaultCapabilityPanel("知识库", "语义检索", "嵌入和重排可以独立选择；基础检索始终可用。", retrievalRows)}${remoteRetrievalNotice}
    </div><footer class="settings-minimal-actions"><span>配置仅保存在此电脑</span><button type="submit" class="save-button">保存默认能力</button></footer></form>
    <section class="default-tools-section default-tools-disclosure">${renderDocumentProcessingFormMarkup("defaultDocumentProcessingForm", true)}</section>
  </section>`;
}

// Keep the old function name as a compatibility shim for deep links created by
// earlier releases.  The visible settings entry is now “默认能力”.
function renderRoutingSettings() {
  return renderDefaultCapabilitiesSettings();
}

function renderRecordsSettings(kind) {
  const definitions = {
    skills: { title: "技能", description: "配置研究任务可用的本地技能。", singular: "技能", field: "path", label: "Skill 路径", placeholder: "例如 builtin:literature-search" },
    mcp: { title: "MCP 服务器", description: "登记 MCP 服务的启动命令；ScanSci 不会在保存时自动启动任何进程。", singular: "MCP 服务器", field: "command", label: "启动命令", placeholder: "例如 npx @modelcontextprotocol/server-filesystem" },
    plugins: { title: "插件管理", description: "登记本地或远程插件来源，按需启用。", singular: "插件", field: "source", label: "插件来源", placeholder: "本地路径或可信插件地址" },
  };
  const definition = definitions[kind];
  const records = kind === "mcp" ? state.settings.mcp_servers : state.settings[kind];
  const recordCards = records.length ? records.map((record) => {
    const auxiliary = kind === "skills" ? record.path : kind === "mcp" ? `${record.command}${record.args ? ` ${record.args}` : ""}` : record.source;
    return `<article class="settings-record"><div><h3>${escapeHtml(record.name)}</h3><p>${escapeHtml(record.description || "未添加说明")}</p>${auxiliary ? `<code>${escapeHtml(auxiliary)}</code>` : ""}</div><div class="record-actions"><label class="switch-label"><input type="checkbox" data-action="toggle-record" data-record-kind="${kind}" data-record-id="${escapeHtml(record.id)}" ${record.enabled ? "checked" : ""} />启用</label><button type="button" class="delete-button" data-action="remove-record" data-record-kind="${kind}" data-record-id="${escapeHtml(record.id)}" aria-label="删除">×</button></div></article>`;
  }).join("") : '<div class="empty-records">尚未配置。添加后会保存在当前电脑。</div>';
  const extra = kind === "mcp" ? '<label class="setting-field full-field"><span>参数（可选）</span><input name="record-args" placeholder="--stdio" maxlength="1000" /></label>' : "";
  return `${settingsHeading(definition.title, definition.description)}<section class="settings-records">${recordCards}</section><form class="record-form" data-record-form="${kind}"><label class="setting-field"><span>名称</span><input name="record-name" required maxlength="100" placeholder="${definition.singular}名称" /></label><label class="setting-field"><span>${definition.label}</span><input name="record-value" required maxlength="500" placeholder="${definition.placeholder}" /></label><label class="setting-field full-field"><span>说明（可选）</span><input name="record-description" maxlength="400" placeholder="简短说明它会做什么" /></label>${extra}<button type="submit" class="save-button">＋ 添加${definition.singular}</button></form>`;
}

function mcpCatalogItems() {
  const query = state.mcpMarketplaceQuery.trim().toLocaleLowerCase();
  const discipline = state.mcpMarketplaceDiscipline || "all";
  const rows = (state.mcpMarketplace.items || []).filter((item) => {
    const categories = Array.isArray(item.disciplines) ? item.disciplines : [];
    if (discipline !== "all" && !categories.includes(discipline)) return false;
    if (!query) return true;
    return [item.title, item.id, item.description, ...(item.tags || [])].join(" ").toLocaleLowerCase().includes(query);
  });
  if (state.mcpMarketplaceSort === "new") {
    rows.sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")) || String(left.title).localeCompare(String(right.title)));
  } else if (state.mcpMarketplaceSort === "name") {
    rows.sort((left, right) => String(left.title || "").localeCompare(String(right.title || ""), "zh-Hans-CN"));
  } else {
    rows.sort((left, right) => Number(right.rank || 0) - Number(left.rank || 0) || String(left.title).localeCompare(String(right.title)));
  }
  return rows;
}

function mcpTransportLabel(item) {
  const value = item.transport || "stdio";
  return value === "streamable-http" ? "远程 HTTP" : value === "sse" ? "SSE" : "本地 stdio";
}

function mcpDisciplineLabel(identifier) {
  return (state.mcpMarketplace.disciplines || []).find((item) => item.id === identifier)?.label || "科研通用";
}

function renderMcpMarketplaceSettings() {
  const catalogue = state.mcpMarketplace;
  if (!catalogue.loaded && !catalogue.loading) {
    window.setTimeout(() => loadMcpMarketplace().catch((error) => toast(error.message, true)), 0);
  }
  const items = mcpCatalogItems();
  const installed = state.settings?.mcp_servers || [];
  const installedIds = new Set(installed.map((item) => item.catalog_id).filter(Boolean));
  const tabs = [
    ["public", "发现 MCP", `${catalogue.items?.length || 0}`],
    ["mine", "我的服务器", `${installed.length}`],
  ].map(([id, label, count]) => `<button type="button" class="mcp-market-tab ${state.mcpMarketplaceTab === id ? "is-active" : ""}" data-action="mcp-set-tab" data-mcp-tab="${id}" aria-current="${state.mcpMarketplaceTab === id ? "page" : "false"}">${escapeHtml(label)}<span>${escapeHtml(count)}</span></button>`).join("");
  const controls = `<div class="mcp-market-controls"><label class="mcp-market-search">${uiIcon("search")}<input id="mcpMarketplaceSearch" type="search" value="${escapeHtml(state.mcpMarketplaceQuery)}" placeholder="搜索 MCP、数据源或能力..." autocomplete="off" /></label><label class="mcp-market-select"><span>学科</span><select id="mcpMarketplaceDiscipline">${(catalogue.disciplines || [{ id: "all", label: "全部学科" }]).map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === state.mcpMarketplaceDiscipline ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}</select></label><div class="mcp-sort-control" aria-label="排序"><span>排序</span>${[["hot", "热门"], ["new", "最新"], ["name", "名称"]].map(([id, label]) => `<button type="button" data-action="mcp-set-sort" data-mcp-sort="${id}" class="${state.mcpMarketplaceSort === id ? "is-active" : ""}">${label}</button>`).join("")}</div></div>`;
  const content = state.mcpMarketplaceTab === "mine"
    ? renderMyMcpServers(installed)
    : renderMcpMarketplaceCards(items, installedIds, catalogue.loading);
  return `<section class="mcp-marketplace">
    <header class="mcp-market-hero"><div class="mcp-market-hero-copy"><p class="mcp-market-eyebrow">工具市场</p><h1>MCP 广场</h1><p>为研究任务挑选可连接的工具、数据和服务。</p></div><div class="mcp-market-orbit" aria-hidden="true"><i></i><b></b><em></em></div></header>
    <div class="mcp-market-toolbar"><nav class="mcp-market-tabs" aria-label="MCP 市场页面">${tabs}</nav><div class="mcp-market-toolbar-actions"><button type="button" class="mcp-create-button" data-action="open-mcp-manual">${uiIcon("plus")}创建 MCP</button><button type="button" class="mcp-sync-button" data-action="sync-mcp-marketplace" ${catalogue.loading ? "disabled" : ""}>${uiIcon("refresh")}${catalogue.loading ? "正在同步" : "同步官方目录"}</button></div></div>
    ${state.mcpMarketplaceTab === "public" ? controls : ""}
    ${content}
    <footer class="mcp-market-note">${uiIcon("info")}<span>“加入我的服务器”只保存连接配置，不会自动下载、启动或执行任何 MCP 进程。首次使用前请核对来源、权限和所需密钥。</span></footer>
    ${renderMcpCreateDialog()}
  </section>`;
}

function renderMcpMarketplaceView() {
  const target = byId("mcpMarketplaceContent");
  if (!target) return;
  target.innerHTML = renderMcpMarketplaceSettings();
}

function refreshMcpMarketplaceSurface() {
  if (state.activeView === "mcp") renderMcpMarketplaceView();
  else if (state.activeView === "settings" && state.activeSettings === "mcp") renderSettings();
}

function renderMcpMarketplaceCards(items, installedIds, loading) {
  if (loading && !items.length) return `<div class="mcp-market-loading">${uiIcon("refresh")}<span>正在读取本地科研目录…</span></div>`;
  if (!items.length) return `<div class="mcp-market-empty">${uiIcon("server")}<strong>没有匹配的 MCP</strong><p>更换学科或关键词，或同步官方目录以获得新的公开服务器。</p></div>`;
  return `<section class="mcp-market-grid">${items.map((item) => {
    const joined = installedIds.has(item.id);
    const disciplines = (item.disciplines || []).slice(0, 2).map((identifier) => `<span class="mcp-discipline-tag">${escapeHtml(mcpDisciplineLabel(identifier))}</span>`).join("");
    const tags = (item.tags || []).slice(0, 2).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("");
    return `<article class="mcp-market-card"><div class="mcp-card-top"><span class="mcp-card-icon">${uiIcon("server")}</span></div><h2 title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</h2><p>${escapeHtml(item.description)}</p><div class="mcp-card-tags">${disciplines}${tags}</div><footer><span class="mcp-card-transport">${escapeHtml(mcpTransportLabel(item))}</span></footer><button type="button" class="mcp-install-button ${joined ? "is-added" : ""}" data-action="${joined ? "mcp-set-tab" : "install-mcp-marketplace"}" ${joined ? 'data-mcp-tab="mine"' : `data-mcp-id="${escapeHtml(item.id)}"`}>${joined ? `${uiIcon("check")}已加入` : `${uiIcon("plus")}加入我的服务器`}</button></article>`;
  }).join("")}</section>`;
}

function renderMyMcpServers(servers) {
  const records = servers.length ? servers.map((server) => {
    const connector = ({ zotero: "Zotero", obsidian: "Obsidian", literature: "文献 provider", general: "通用" })[server.connector_kind] || "通用";
    const update = (state.mcpMarketplace.updates || []).find((item) => String(item.id || "") === String(server.id || ""));
    const updateMarkup = update?.available ? `<button type="button" class="mcp-update-button" data-action="update-mcp-marketplace" data-record-id="${escapeHtml(server.id)}">更新</button>` : "";
    const permission = server.allow_write
      ? "已授权写操作"
      : server.allow_reversible
        ? "已授权文献产物写入；普通写操作未授权"
        : "只读工具；产物写入未授权";
    return `<article class="mcp-owned-record"><span class="mcp-owned-icon">${uiIcon("server")}</span><div><header><h2>${escapeHtml(server.name)}</h2><span>${escapeHtml(server.source || "自定义 MCP")} · ${connector}</span></header><p>${escapeHtml(server.description || "未添加说明")}</p><small>${escapeHtml(server.transport === "streamable-http" ? server.endpoint : [server.command, server.args].filter(Boolean).join(" ") || "等待填写连接信息")}</small><small>${permission}</small></div><div class="mcp-owned-actions"><button type="button" data-action="test-mcp-server" data-record-id="${escapeHtml(server.id)}">测试</button>${updateMarkup}<label class="mcp-enabled-switch"><input type="checkbox" data-action="toggle-record" data-record-kind="mcp" data-record-id="${escapeHtml(server.id)}" ${server.enabled ? "checked" : ""} /><span>${server.enabled ? "启用" : "停用"}</span></label><button type="button" data-action="remove-record" data-record-kind="mcp" data-record-id="${escapeHtml(server.id)}" aria-label="移除 ${escapeHtml(server.name)}">${uiIcon("x")}</button></div></article>`;
  }).join("") : `<div class="mcp-market-empty is-mine">${uiIcon("server")}<strong>还没有已保存的 MCP</strong><p>从“发现 MCP”添加官方目录中的服务器，或登记自己的本地/远程连接。</p></div>`;
  return `<section class="mcp-owned-list">${records}</section><button type="button" class="mcp-manual-trigger" data-action="open-mcp-manual">${uiIcon("plus")}创建自定义 MCP</button>`;
}

function renderMcpCreateDialog() {
  if (!state.mcpManualOpen) return "";
  const mode = state.mcpCreateMode;
  const isStdio = mode === "stdio";
  const disciplines = state.mcpMarketplace.disciplines || [{ id: "all", label: "全部学科" }];
  const steps = `<ol class="mcp-create-steps" aria-label="创建步骤"><li class="is-current"><span>1</span>连接类型</li><li class="${mode ? "is-current" : ""}"><span>2</span>连接信息</li><li class="${mode ? "is-current" : ""}"><span>3</span>保存</li></ol>`;
  const choose = `<section class="mcp-create-choice"><div class="mcp-create-intro"><span class="mcp-create-mark">${uiIcon("plus")}</span><div><p>创建 MCP</p><h2>先选择连接类型</h2><span>选择后再填写必要信息；保存不会启动本地命令或访问远程服务。</span></div></div><div class="mcp-create-options"><button type="button" class="mcp-create-option" data-action="mcp-select-create-mode" data-mcp-create-mode="stdio"><span class="mcp-create-option-icon">${uiIcon("terminal")}</span><span><strong>本地 stdio</strong><small>连接本机命令行工具，例如 npx、uvx 或已安装的程序。</small></span><i>${uiIcon("arrow-right")}</i></button><button type="button" class="mcp-create-option" data-action="mcp-select-create-mode" data-mcp-create-mode="remote"><span class="mcp-create-option-icon is-remote">${uiIcon("globe")}</span><span><strong>远程服务</strong><small>连接团队部署或云端提供的 HTTP / SSE MCP 端点。</small></span><i>${uiIcon("arrow-right")}</i></button></div></section>`;
  const fields = isStdio
    ? `<div class="mcp-create-connection"><span class="mcp-create-connection-label">本地连接</span><label class="is-wide"><span>启动命令</span><input name="mcp-command" required maxlength="500" placeholder="例如：npx" autocomplete="off" /></label><label class="is-wide"><span>参数</span><input name="mcp-args" maxlength="1000" placeholder="例如：-y @scope/server" autocomplete="off" /></label><p>${uiIcon("shield-check")}命令仅作为连接配置保存，搜索科学不会在此时运行它。</p></div>`
    : `<div class="mcp-create-connection"><span class="mcp-create-connection-label">远程连接</span><label><span>传输协议</span><select name="mcp-transport"><option value="streamable-http">Streamable HTTP</option><option value="sse">Server-Sent Events</option></select></label><label class="is-wide"><span>服务端点</span><input name="mcp-endpoint" type="url" required maxlength="500" placeholder="https://example.org/mcp" autocomplete="url" /></label><p>${uiIcon("shield-check")}保存前只校验地址格式，不会向该端点发送请求。</p></div>`;
  const form = `<form id="mcpManualForm" class="mcp-create-form" data-mcp-create-mode="${escapeHtml(mode)}"><header><button type="button" class="mcp-create-back" data-action="mcp-create-back">${uiIcon("arrow-left")}返回类型</button><div><span>${isStdio ? "本地 stdio MCP" : "远程 MCP 服务"}</span><h2>填写连接信息</h2></div></header><div class="mcp-create-form-grid"><section class="mcp-create-basics"><label><span>名称</span><input name="mcp-name" required maxlength="100" placeholder="例如：Zotero MCP" autofocus /></label><label><span>关联连接器</span><select name="mcp-connector-kind"><option value="general">通用 MCP</option><option value="literature">文献 provider</option><option value="zotero">Zotero</option><option value="obsidian">Obsidian</option></select></label><label><span>适用学科</span><select name="mcp-discipline">${disciplines.filter((item) => item.id !== "all").map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</option>`).join("")}</select></label><label class="is-wide"><span>用途说明</span><textarea name="mcp-description" maxlength="400" rows="3" placeholder="它会为研究流程提供什么能力？"></textarea></label><label class="is-wide"><span>标签（可选）</span><input name="mcp-tags" maxlength="200" placeholder="例如：文献、笔记、只读检索（用逗号分隔）" /></label><label class="is-wide mcp-write-permission"><input name="mcp-allow-write" type="checkbox" /><span>允许模型看到新增、修改、删除等写操作（默认关闭）</span></label><label class="is-wide mcp-write-permission"><input name="mcp-allow-reversible" type="checkbox" /><span>文献 provider 允许下载文件等可逆产物（仅 literature 类型）</span></label><label class="is-wide mcp-write-permission"><input name="mcp-deferred" type="checkbox" checked /><span>按需发现工具并延迟连接（推荐，减少上下文和启动等待）</span></label></section>${fields}</div><aside class="mcp-create-review"><span>${uiIcon("lock-keyhole")}执行边界</span><p>保存不会启动连接；启用后，Pi 仅在任务需要时连接并发现工具。文献 provider 只开放已声明的检索和下载语义工具。</p></aside><footer><button type="button" data-action="close-mcp-manual">取消</button><button type="submit" class="mcp-create-save">${uiIcon("check")}保存到我的服务器</button></footer></form>`;
  return `<div class="mcp-create-overlay" role="presentation"><section class="mcp-create-dialog" role="dialog" aria-modal="true" aria-labelledby="mcpCreateTitle"><header class="mcp-create-dialog-top"><div><span>自定义连接</span><h1 id="mcpCreateTitle">创建 MCP</h1></div><button type="button" data-action="close-mcp-manual" aria-label="关闭创建 MCP">${uiIcon("x")}</button></header>${steps}${mode ? form : choose}</section></div>`;
}

async function loadMcpMarketplace({ force = false } = {}) {
  if (state.mcpMarketplace.loading || (state.mcpMarketplace.loaded && !force)) return state.mcpMarketplace;
  state.mcpMarketplace.loading = true;
  refreshMcpMarketplaceSurface();
  try {
    const payload = await request("/api/mcp/marketplace");
    state.mcpMarketplace = { ...payload, loaded: true, loading: false };
    maybeAutoSyncMcpMarketplace();
    return state.mcpMarketplace;
  } finally {
    if (state.mcpMarketplace.loading) state.mcpMarketplace.loading = false;
    refreshMcpMarketplaceSurface();
  }
}

function maybeAutoSyncMcpMarketplace() {
  const last = Number(window.localStorage.getItem("scansci.mcp.market.last-sync") || 0);
  if (last && Date.now() - last < 24 * 60 * 60 * 1000) return;
  window.localStorage.setItem("scansci.mcp.market.last-sync", String(Date.now()));
  syncMcpMarketplace({ quiet: true }).catch(() => {});
}

async function syncMcpMarketplace({ quiet = false } = {}) {
  state.mcpMarketplace.loading = true;
  refreshMcpMarketplaceSurface();
  try {
    const payload = await request("/api/mcp/marketplace/sync", { method: "POST", body: "{}" });
    state.mcpMarketplace = { ...payload, loaded: true, loading: false };
    const count = payload.sync?.fetched || payload.cached_count || 0;
    window.localStorage.setItem("scansci.mcp.market.last-sync", String(Date.now()));
    if (!quiet) toast(`已从官方目录同步 ${count} 个科研相关 MCP`);
  } catch (error) {
    state.mcpMarketplace.loading = false;
    throw error;
  }
  refreshMcpMarketplaceSurface();
}

async function installMcpMarketplaceServer(identifier) {
  const payload = await request("/api/mcp/marketplace/install", { method: "POST", body: JSON.stringify({ id: identifier }) });
  state.settings = payload.settings;
  renderModelSelectors();
  refreshMcpMarketplaceSurface();
  toast(payload.created ? "已加入我的服务器；尚未启动任何 MCP 进程" : "该 MCP 已在我的服务器中");
}

async function addManualMcpServer(form) {
  const mode = form.dataset.mcpCreateMode === "remote" ? "remote" : "stdio";
  const command = (form.elements["mcp-command"]?.value || "").trim();
  const endpoint = (form.elements["mcp-endpoint"]?.value || "").trim();
  const name = form.elements["mcp-name"].value.trim();
  if (!name) throw new Error("请为这个 MCP 填写名称。");
  if (mode === "stdio" && !command) throw new Error("请填写本地 MCP 的启动命令。");
  if (mode === "remote") {
    if (!endpoint) throw new Error("请填写远程 MCP 的服务端点。");
    try {
      const url = new URL(endpoint);
      if (!["http:", "https:"].includes(url.protocol)) throw new Error("unsupported protocol");
    } catch (_) {
      throw new Error("远程端点需要是以 http:// 或 https:// 开头的有效地址。");
    }
  }
  const tags = (form.elements["mcp-tags"]?.value || "").split(/[,，]/).map((tag) => tag.trim()).filter(Boolean).slice(0, 8);
  const connectorKind = form.elements["mcp-connector-kind"].value || "general";
  state.settings.mcp_servers.push({
    id: `mcp-${Date.now()}`,
    name,
    description: form.elements["mcp-description"].value.trim(),
    command,
    args: (form.elements["mcp-args"]?.value || "").trim(),
    endpoint,
    transport: mode === "stdio" ? "stdio" : form.elements["mcp-transport"].value,
    discipline: form.elements["mcp-discipline"].value || "general",
    connector_kind: connectorKind,
    provider_kind: connectorKind === "literature" ? "literature" : "",
    allow_write: Boolean(form.elements["mcp-allow-write"].checked),
    allow_reversible: connectorKind === "literature" && Boolean(form.elements["mcp-allow-reversible"]?.checked),
    deferred: Boolean(form.elements["mcp-deferred"].checked),
    tags,
    source: "自定义 MCP",
    enabled: true,
  });
  state.mcpManualOpen = false;
  state.mcpCreateMode = "";
  state.mcpMarketplaceTab = "mine";
  await persistSettings("已保存 MCP 配置；将在相关任务中按需连接");
}

async function testMcpServer(serverId) {
  const result = await request("/api/mcp/test", {
    method: "POST",
    body: JSON.stringify({ server_id: serverId }),
  });
  if (!result.server_count) {
    const detail = result.diagnostics?.at(-1)?.error || "没有发现可用工具";
    throw new Error(detail);
  }
  toast(`MCP 已连接 · 发现 ${result.tool_count || 0} 个可用工具`);
}

function collectProviderForm() {
  const form = byId("modelProviderForm");
  const provider = selectedProvider();
  if (!form || !provider) return provider;
  provider.name = form.elements["provider-name"].value.trim() || provider.name;
  provider.kind = form.elements["provider-kind"].value;
  provider.base_url = form.elements["provider-base-url"].value.trim();
  if (provider.id === "zhipu" && form.elements["provider-coding-plan"]) {
    provider.coding_plan_enabled = Boolean(form.elements["provider-coding-plan"].checked);
    if (provider.coding_plan_enabled) {
      provider.coding_plan_base_url = provider.base_url || ZHIPU_CODING_PLAN_BASE_URL;
      provider.standard_base_url = String(provider.standard_base_url || ZHIPU_STANDARD_BASE_URL).trim();
    } else {
      provider.standard_base_url = provider.base_url || ZHIPU_STANDARD_BASE_URL;
      provider.coding_plan_base_url = String(provider.coding_plan_base_url || ZHIPU_CODING_PLAN_BASE_URL).trim();
    }
    provider.base_url = provider.coding_plan_enabled ? provider.coding_plan_base_url : provider.standard_base_url;
  }
  provider.enabled = form.elements["provider-enabled"]?.checked ?? provider.enabled;
  if (provider.id === "siliconflow") provider.remote_rag_allowed = Boolean(form.elements["provider-remote-rag-allowed"]?.checked);
  provider.models = provider.models.map((model, index) => {
    const id = form.querySelector(`[data-model-id="${index}"]`)?.value.trim() || model.id;
    return {
      ...model,
      id,
      name: form.querySelector(`[data-model-name="${index}"]`)?.value.trim() || model.name || id,
      group: form.querySelector(`[data-model-group="${index}"]`)?.value.trim() || model.group || "默认模型",
      context_window: form.querySelector(`[data-model-context="${index}"]`)?.value.trim() || "",
    };
  });
  return provider;
}

function collectLocalModelsForm() {
  state.settings.local_models = (state.settings.local_models || []).map((model, index) => {
    const urlInput = document.querySelector(`[data-local-url="${index}"]`);
    if (!urlInput) return model;
    return {
      ...model,
      name: document.querySelector(`[data-local-name="${index}"]`)?.value.trim() || model.name,
      runtime: document.querySelector(`[data-local-runtime="${index}"]`)?.value.trim() || model.runtime,
      base_url: urlInput.value.trim(),
      model_id: document.querySelector(`[data-local-model="${index}"]`)?.value.trim() || "",
      enabled: document.querySelector(`[data-local-enabled="${index}"]`)?.checked ?? model.enabled,
    };
  });
  return state.settings.local_models;
}

async function refreshCudaStatus() {
  try {
    state.cuda = await request("/api/cuda-status");
  } catch (_error) {
    state.cuda = { available: false, message: "" };
  }
  renderCudaStatus();
}

function renderCudaStatus() {
  const targets = document.querySelectorAll("[data-cuda-status]");
  const status = state.cuda || {};
  const markup = status.available
    ? `<span class="cuda-status is-ready" title="${escapeHtml(status.devices?.[0]?.name || "GPU")}">${uiIcon("gpu")} CUDA 已就绪 · ${escapeHtml(status.devices?.[0]?.name || "")}</span>`
    : status.message
      ? `<span class="cuda-status is-missing" title="${escapeHtml(status.message)}">${uiIcon("cpu")} CPU 模式 · ${escapeHtml(status.torch_version || "")}</span>`
      : "";
  targets.forEach((el) => { el.innerHTML = markup; });
}

async function refreshLocalModelMarket() {
  const query = String(state.localModelMarket?.query || "").trim();
  state.localModelMarket = { ...(state.localModelMarket || {}), loading: true };
  refreshCudaStatus();
  try {
    const [installed, catalog, runtime, ollama] = await Promise.all([
      request("/api/local-models/installed"),
      request(`/api/local-models/market${query ? `?q=${encodeURIComponent(query)}` : ""}`),
      request("/api/local-runtime").catch(() => state.localRuntime),
      request("/api/ollama/status").catch(() => state.ollama),
    ]);
    state.localModelMarket = { installed: installed.models || [], catalog: catalog.items || [], recommendation: catalog.recommendation || null, source: catalog.source || "", query, loading: false, loaded: true };
    state.localRuntime = { ...(state.localRuntime || {}), ...(runtime || {}) };
    state.ollama = ollama || state.ollama;
    state.settings = await request("/api/settings");
    await refreshModelHealth({ render: false });
    renderModelSelectors();
    if (state.activeView === "settings" && ["models", "local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
  } catch (error) {
    state.localModelMarket = { ...(state.localModelMarket || {}), loading: false };
    if (state.activeView === "settings" && state.activeSettings === "local-models") renderSettings();
    throw error;
  }
}

async function refreshLocalRuntimeStatus() {
  state.localRuntime = { ...(state.localRuntime || {}), checking: true };
  if (state.activeView === "settings" && state.activeSettings === "runtime") renderSettings();
  try {
    const runtime = await request(`/api/local-runtime?refresh=${Date.now()}`);
    state.localRuntime = { ...(state.localRuntime || {}), ...(runtime || {}), checking: false };
    if (state.activeView === "settings" && state.activeSettings === "runtime") renderSettings();
    toast(state.localRuntime.update_required ? "本地运行组件需要更新" : state.localRuntime.installed ? "本地运行时已重新检测" : "尚未安装本地运行组件");
    return state.localRuntime;
  } catch (error) {
    state.localRuntime = { ...(state.localRuntime || {}), checking: false };
    if (state.activeView === "settings" && state.activeSettings === "runtime") renderSettings();
    throw error;
  }
}

async function refreshInstalledModelInventory({ render = true } = {}) {
  try {
    // A cache-busting query is intentional here: opening the guide is an
    // explicit request to re-scan the shared model directory after an app
    // update or a model download completed in another process.
    const installed = await request(`/api/local-models/installed?refresh=${Date.now()}`);
    state.localModelMarket = {
      ...(state.localModelMarket || {}),
      installed: installed.models || [],
    };
    if (render && state.onboardingOpen) renderResourceOnboarding();
    return state.localModelMarket.installed;
  } catch (_error) {
    return state.localModelMarket?.installed || [];
  }
}

async function updateMcpMarketplaceServer(identifier) {
  const payload = await request("/api/mcp/marketplace/update", { method: "POST", body: JSON.stringify({ id: identifier }) });
  state.settings = payload.settings || state.settings;
  state.mcpMarketplace.updates = (state.mcpMarketplace.updates || []).map((item) => item.id === identifier ? (payload.update || item) : item);
  renderModelSelectors();
  refreshMcpMarketplaceSurface();
  toast(payload.updated ? "MCP 配置已更新；尚未启动连接进程" : "这个 MCP 已是最新版本");
}

async function checkLocalRuntimeChannels() {
  state.localRuntime = { ...(state.localRuntime || {}), channelsChecking: true };
  if (state.activeView === "settings" && ["local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
  try {
    const report = await request("/api/local-runtime/channels");
    state.localRuntime = { ...(state.localRuntime || {}), channels: report, channelsChecking: false };
    if (state.activeView === "settings" && ["local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
    const healthy = (report.channels || []).filter((item) => item.valid).length;
    toast(healthy ? `已找到 ${healthy} 个可用资源通道` : "自动资源通道暂不可用，可使用本地文件安装", !healthy);
    return report;
  } catch (error) {
    state.localRuntime = { ...(state.localRuntime || {}), channelsChecking: false };
    if (state.activeView === "settings" && ["local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
    throw error;
  }
}

async function chooseLocalRuntimeFiles() {
  const picker = window.pywebview?.api?.choose_local_runtime_files;
  if (typeof picker !== "function") {
    toast("浏览器预览不能读取本地路径，请在 ScanSci 桌面应用中选择组件文件。", true);
    return;
  }
  const paths = Array.from(await picker() || []).map(String).filter(Boolean);
  if (!paths.length) return;
  const job = await request("/api/local-runtime/install-local", {
    method: "POST",
    body: JSON.stringify({ paths }),
  });
  state.localRuntime = { ...(state.localRuntime || {}), install_job: job };
  scheduleLocalRuntimeInstallPoll();
  if (state.activeView === "settings" && ["local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
  renderDownloadActivity();
  toast("已开始校验本地组件；校验通过后才会启用。");
}

async function refreshModelHealth({ render = true } = {}) {
  state.modelHealth = { ...(state.modelHealth || {}), loading: true };
  try {
    const snapshot = await request("/api/model-health");
    state.modelHealth = { ...(snapshot || {}), loading: false };
    applyModelSelectionCatalog(state.modelHealth);
  } catch (error) {
    state.modelHealth = { ...(state.modelHealth || {}), loading: false, error: error?.message || "无法读取模型状态" };
    if (render) throw error;
  }
  if (render) {
    renderModelSelectors();
    if (state.activeView === "settings" && ["models", "local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
  }
  return state.modelHealth;
}

function collectModelRoleForm() {
  document.querySelectorAll("[data-model-role]").forEach((select) => {
    state.settings.model_roles[select.dataset.modelRole] = select.value;
  });
  return state.settings.model_roles;
}

function collectDefaultCapabilitiesForm() {
  const form = byId("defaultCapabilitiesForm");
  if (!form) return state.settings.model_roles;
  const conversation = String(form.elements["default-conversation-model"]?.value || "");
  if (conversation.includes("::")) {
    const separator = conversation.indexOf("::");
    const providerId = conversation.slice(0, separator);
    const modelId = conversation.slice(separator + 2);
    if (providerId && modelId) {
      state.settings.active_model = { provider_id: providerId, model_id: modelId };
      // The product exposes one user-facing assistant model.  Keep the
      // legacy role fields synchronized so task, writing, and slide flows use
      // exactly the same model without making users configure them separately.
      const reference = `provider:${providerId}:${modelId}`;
      for (const role of ["reasoning", "writing", "slides"]) state.settings.model_roles[role] = reference;
    }
  }
  return collectModelRoleForm();
}

function ensureActiveModel() {
  const { provider, model } = activeModel();
  if (!provider || !model) return;
  state.settings.active_model = { provider_id: provider.id, model_id: model.id };
}

async function persistSettings(message = "设置已保存") {
  ensureActiveModel();
  const settings = { ...state.settings, local_models: (state.settings.local_models || []).filter((model) => !model.discovered) };
  const saved = await request("/api/settings", { method: "POST", body: JSON.stringify({ settings }) });
  state.settings = saved;
  applyAppearancePreferences();
  state.selectedProviderId = selectedProvider()?.id || state.settings.providers[0]?.id || "";
  renderModelSelectors();
  if (state.activeView === "settings") renderSettings();
  if (state.activeView === "extensions") renderExtensions();
  if (state.activeView === "mcp") renderMcpMarketplaceView();
  const migrated = Number(saved?.storage?.vector_index_migration?.migrated || 0);
  toast(migrated > 0 ? `${message} 已迁移 ${migrated} 个知识库索引` : message);
}

function newRecord(kind, form) {
  const value = form.elements["record-value"].value.trim();
  const name = form.elements["record-name"].value.trim();
  const description = form.elements["record-description"].value.trim();
  const id = `${kind}-${Date.now()}`;
  if (kind === "skills") return { id, name, description, path: value, enabled: true };
  if (kind === "mcp") return { id, name, description, command: value, args: form.elements["record-args"].value.trim(), enabled: true };
  return { id, name, description, source: value, enabled: true };
}

async function handleSettingsAction(action, element) {
  if (action === "open-resource-guide") {
    openResourceGuideOverlay();
    return;
  }
  if (action === "focus-onboarding-storage") {
    document.querySelector(".onboarding-storage-panel")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    toast("先设置存储目录再开始下载");
    return;
  }
  if (action === "close-resource-guide") {
    await closeResourceGuideOverlay(element.dataset.resourceGuideResult || "skip");
    return;
  }
  if (action === "resource-guide-next") {
    state.resourceGuideStep = Math.min(RESOURCE_GUIDE_STEPS.length - 1, resourceGuideStepIndex() + 1);
    renderResourceOnboarding();
    refreshCudaStatus();
    return;
  }
  if (action === "resource-guide-back") {
    state.resourceGuideStep = Math.max(0, resourceGuideStepIndex() - 1);
    renderResourceOnboarding();
    refreshCudaStatus();
    return;
  }
  if (action === "start-onboarding-resource") {
    await startOnboardingResource(element.dataset.resourceId || "retrieval");
    return;
  }
  if (action === "reopen-resource-onboarding") {
    openResourceGuideOverlay();
    return;
  }
  if (action === "open-data-onboarding") {
    openMode("library");
    return;
  }
  if (action === "finish-first-run") {
    await finishFirstRun();
    return;
  }
  if (action === "return-first-run") {
    setView("home");
    window.setTimeout(() => byId("firstRunWelcome")?.querySelector('[data-action="onboarding-open-knowledge"]')?.focus({ preventScroll: true }), 0);
    return;
  }
  if (action === "onboarding-next") {
    state.onboardingStep = state.onboardingStep === "welcome" ? "models" : "knowledge";
    renderResourceOnboarding();
    return;
  }
  if (action === "onboarding-back") {
    state.onboardingStep = state.onboardingStep === "knowledge" ? "models" : "welcome";
    renderResourceOnboarding();
    return;
  }
  if (action === "onboarding-open-models") {
    openResourceGuideOverlay({ returnToWelcome: true });
    return;
  }
  if (action === "onboarding-open-knowledge") {
    openMode("library");
    return;
  }
  if (action === "advance-resource-onboarding" || action === "finish-resource-onboarding") {
    state.onboardingStep = "models";
    renderResourceOnboarding();
    return;
  }
  if (action === "back-resource-onboarding") {
    state.onboardingStep = "welcome";
    renderResourceOnboarding();
    return;
  }
  if (action === "skip-resource-onboarding") {
    state.onboardingMode = "";
    await persistOnboardingPreferences({ welcome_dismissed: true }, "已暂时跳过首次配置；可随时在设置中继续", { close: true });
    return;
  }
  if (action === "finish-data-onboarding") {
    await persistOnboardingPreferences(
      {
        welcome_dismissed: true,
        resource_setup_completed: resourceInstallSnapshot("retrieval").state === "ready",
        data_setup_completed: connectedDataSourceCount() > 0,
      },
      "资料接入配置已完成",
      { close: true },
    );
    return;
  }
  if (action === "onboarding-connect-folder") {
    await chooseOnboardingLibraryFolder("folder");
    return;
  }
  if (action === "onboarding-connect-obsidian") {
    await chooseOnboardingLibraryFolder("obsidian");
    return;
  }
  if (action === "onboarding-connect-zotero") {
    await connectLocalZotero();
    return;
  }
  if (action === "onboarding-connect-notion") {
    await connectNotion();
    return;
  }
  if (action === "retry-guided-library-import") {
    const failed = state.libraryImportJob || {};
    await startGuidedLibraryImport(
      String(failed.path || ""),
      String(failed.library_kind || "folder"),
      String(failed.notebook_id || ""),
      String(failed.data_dir || ""),
    );
    return;
  }
  if (action === "retry-bound-library-import") {
    const notebook = (state.workspace?.notebooks || []).find((item) => item.notebook_id === element.dataset.notebookId);
    const binding = notebook?.metadata?.local_binding || {};
    const path = String(binding.source_path || notebook?.root_path || "").trim();
    if (!path) throw new Error("找不到已绑定的文件夹路径，请重新选择文件夹。");
    await bindLibraryFolder(path, knowledgeSourceKind(notebook), notebook.notebook_id);
    return;
  }
  if (action === "open-local-models") {
    state.activeSettings = "local-models";
    renderSettings();
    return;
  }
  if (action === "choose-storage-directory") {
    const setting = ["model_cache", "local_runtime", "vector_index"].includes(element.dataset.directorySetting)
      ? element.dataset.directorySetting
      : "model_cache";
    const picker = window.pywebview?.api?.choose_library_folder;
    if (typeof picker !== "function") {
      toast("浏览器预览不能读取本机目录，请在 ScanSci 桌面应用中选择文件夹。", true);
      return;
    }
    const selected = String(await picker() || "").trim();
    if (!selected) return;
    const general = generalPreferences();
    general.directories[setting] = selected;
    state.settings.general = general;
    await persistSettings("存储目录设置已保存；新下载将使用新位置");
    renderResourceOnboarding();
    return;
  }
  if (action === "choose-general-directory") {
    const setting = ["default_workspace", "conversation_workspace", "model_cache", "local_runtime", "vector_index"].includes(element.dataset.directorySetting)
      ? element.dataset.directorySetting
      : "default_workspace";
    const picker = window.pywebview?.api?.choose_library_folder;
    if (typeof picker !== "function") {
      toast("浏览器预览不能读取本机目录，请在 ScanSci 桌面应用中选择文件夹。", true);
      return;
    }
    const selected = String(await picker() || "").trim();
    if (!selected) return;
    const general = generalPreferences();
    general.directories[setting] = selected;
    state.settings.general = general;
    await persistSettings("目录设置已保存");
    return;
  }
  if (action === "open-general-directories") {
    state.activeSettings = "general";
    state.generalSettingsTab = "directories";
    renderSettings();
    return;
  }
  if (action === "reset-general-directory") {
    const setting = ["default_workspace", "conversation_workspace", "model_cache", "local_runtime", "vector_index"].includes(element.dataset.directorySetting)
      ? element.dataset.directorySetting
      : "default_workspace";
    const general = generalPreferences();
    general.directories[setting] = "";
    state.settings.general = general;
    await persistSettings("已恢复默认目录");
    return;
  }
  if (action === "switch-general-tab") {
    const nextTab = String(element.dataset.settingsTab || "appearance");
    state.generalSettingsTab = ["appearance", "conversation", "directories"].includes(nextTab) ? nextTab : "appearance";
    renderSettings();
    return;
  }
  if (action === "configure-paddle-ocr") {
    const processing = collectDocumentProcessingForm();
    processing.ocr.provider = "paddle";
    processing.ocr.base_url = "";
    renderSettings();
    return;
  }
  if (action === "select-provider") {
    collectProviderForm();
    state.selectedProviderId = element.dataset.providerId;
    state.editingModelIndex = -1;
    state.modelQuery = "";
    renderSettings();
    return;
  }
  if (action === "toggle-provider-enabled") {
    collectProviderForm();
    const provider = state.settings.providers.find((item) => item.id === element.dataset.providerId);
    if (!provider) return;
    provider.enabled = !provider.enabled;
    const active = state.settings.active_model || {};
    if (!provider.enabled && active.provider_id === provider.id) {
      const fallback = state.settings.providers.find((item) => item.id !== provider.id && isProviderUsable(item) && item.models?.length);
      if (fallback) state.settings.active_model = { provider_id: fallback.id, model_id: fallback.models[0].id };
    }
    await persistSettings(`${provider.name} 已${provider.enabled ? "启用" : "停用"}`);
    return;
  }
  if (action === "restore-provider-default") {
    collectProviderForm();
    const provider = selectedProvider();
    const preset = (state.presets?.providers || []).find((item) => item.id === provider?.id);
    if (!provider || !preset) return;
    const confirmed = await requestConfirmation({
      eyebrow: "恢复默认配置",
      title: `恢复 ${provider.name} 的默认配置？`,
      message: "默认地址、模型与能力标签将被恢复，已保存的 API 密钥不会被删除。",
      confirmLabel: "恢复默认",
    });
    if (!confirmed) return;
    const index = state.settings.providers.findIndex((item) => item.id === provider.id);
    if (index < 0) return;
    state.settings.providers[index] = { ...structuredClone(preset), enabled: false, api_key_configured: Boolean(provider.api_key_configured) };
    state.editingModelIndex = -1;
    state.modelQuery = "";
    await persistSettings(`${provider.name} 已恢复默认配置`);
    return;
  }
  if (action === "add-provider") {
    collectProviderForm();
    const provider = { id: `provider-${Date.now()}`, name: "新服务商", logo: "custom", kind: "openai-compatible", base_url: "", category: "自定义提供商", summary: "自定义 OpenAI 或 Anthropic 兼容服务。", auth_mode: "key", model_listing: true, enabled: true, models: [{ id: "model", name: "新模型", group: "默认模型", context_window: "", capabilities: ["reasoning"] }] };
    state.settings.providers.push(provider);
    state.selectedProviderId = provider.id;
    state.editingModelIndex = 0;
    renderSettings();
    return;
  }
  if (action === "add-provider-preset") {
    collectProviderForm();
    const preset = (state.presets?.providers || []).find((item) => item.id === element.dataset.presetId);
    if (!preset) return;
    const existing = state.settings.providers.find((item) => item.id === preset.id);
    if (existing) {
      state.selectedProviderId = existing.id;
      renderSettings();
      toast(`${existing.name} 已在列表中`);
      return;
    }
    state.settings.providers.push(structuredClone(preset));
    state.selectedProviderId = preset.id;
    renderSettings();
    return;
  }
  if (action === "remove-provider") {
    if (state.settings.providers.length < 2) return;
    const provider = selectedProvider();
    state.settings.providers = state.settings.providers.filter((item) => item.id !== provider.id);
    state.selectedProviderId = state.settings.providers[0].id;
    ensureActiveModel();
    await persistSettings("提供商已移除");
    return;
  }
  if (action === "remove-provider-key") {
    const provider = selectedProvider();
    if (!provider || provider.kind === "local") return;
    if (provider.api_key_source === "environment") {
      toast(`当前使用环境变量 ${provider.api_key_env || "API_KEY"}，设置页不会覆盖它`, true);
      return;
    }
    provider.enabled = false;
    await persistSettings("已移除提供商密钥");
    state.settings = await request(`/api/settings/providers/${encodeURIComponent(provider.id)}/api-key`, { method: "POST", body: JSON.stringify({ api_key: "" }) });
    await refreshModelHealth({ render: false });
    renderModelSelectors();
    renderSettings();
    return;
  }
  if (action === "add-model") {
    const provider = collectProviderForm();
    provider.models.push({ id: `model-${provider.models.length + 1}`, name: "新模型", group: "默认模型", context_window: "", capabilities: ["reasoning"] });
    state.editingModelIndex = provider.models.length - 1;
    renderSettings();
    return;
  }
  if (action === "edit-model") {
    collectProviderForm();
    state.editingModelIndex = Number(element.dataset.modelIndex);
    renderSettings();
    return;
  }
  if (action === "close-model-editor" || action === "save-model-editor") {
    collectProviderForm();
    state.editingModelIndex = -1;
    renderSettings();
    return;
  }
  if (action === "toggle-model-capability") {
    const provider = collectProviderForm();
    const model = provider?.models?.[Number(element.dataset.modelIndex)];
    const capability = String(element.dataset.capability || "");
    if (!model || !modelCapabilityDefinitions.some(([id]) => id === capability)) return;
    const selected = new Set(Array.isArray(model.capabilities) ? model.capabilities : []);
    if (selected.has(capability)) selected.delete(capability);
    else selected.add(capability);
    model.capabilities = [...selected];
    renderSettings();
    return;
  }
  if (action === "fetch-provider-models") {
    const form = byId("modelProviderForm");
    const provider = collectProviderForm();
    if (!provider || provider.kind === "local") return;
    const apiKey = String(form?.elements["provider-api-key"]?.value || "").trim();
    if (apiKey) provider.enabled = true;
    await persistSettings(apiKey ? "连接信息已保存，正在读取模型列表…" : "连接信息已保存，正在读取模型列表…");
    if (apiKey) {
      state.settings = await request(`/api/settings/providers/${encodeURIComponent(provider.id)}/api-key`, { method: "POST", body: JSON.stringify({ api_key: apiKey }) });
    }
    const result = await request(`/api/settings/providers/${encodeURIComponent(provider.id)}/models`, { method: "POST", body: "{}" });
    const saved = state.settings.providers.find((item) => item.id === provider.id);
    if (!saved) return;
    const previous = new Map((saved.models || []).map((item) => [item.id, item]));
    const fetched = Array.isArray(result.models) ? result.models : [];
    if (fetched.length) {
      saved.models = fetched.map((item) => ({ ...previous.get(item.id), ...item }));
      await persistSettings(`已同步 ${fetched.length} 个模型`);
    } else {
      renderSettings();
      toast("服务商没有返回可用模型；可手动添加模型 ID");
    }
    return;
  }
  if (action === "toggle-model-composer-visibility") {
    const provider = collectProviderForm();
    const model = provider?.models?.[Number(element.dataset.modelIndex)];
    if (!model) return;
    model.show_in_composer = model.show_in_composer === false;
    await persistSettings(model.show_in_composer ? "模型已显示在对话候选中" : "模型已从对话候选中隐藏");
    return;
  }
  if (action === "remove-model") {
    const provider = collectProviderForm();
    if (provider.models.length < 2) return;
    provider.models.splice(Number(element.dataset.modelIndex), 1);
    state.editingModelIndex = -1;
    ensureActiveModel();
    renderSettings();
    return;
  }
  if (action === "test-provider") {
    const provider = collectProviderForm();
    await persistSettings("正在测试连接…");
    const result = await request(`/api/settings/providers/${encodeURIComponent(provider.id)}/test`, { method: "POST", body: "{}" });
    await refreshModelHealth({ render: false });
    renderSettings();
    toast(`${result.provider || provider.name}：${result.message || "连接正常"}`);
    return;
  }
  if (action === "refresh-model-health") {
    await refreshModelHealth();
    toast("模型可用状态已刷新");
    return;
  }
  if (action === "add-local-preset") {
    collectLocalModelsForm();
    const preset = (state.presets?.local_models || []).find((item) => item.id === element.dataset.presetId);
    if (!preset) return;
    state.localRuntimeManualOpen = true;
    const existing = state.settings.local_models.find((item) => item.id === preset.id);
    if (existing) {
      renderSettings();
      document.querySelector(".local-manual-runtime-disclosure")?.setAttribute("open", "");
      toast(`${existing.name} 已在列表中`);
      return;
    }
    state.settings.local_models.push(structuredClone(preset));
    renderSettings();
    document.querySelector(".local-manual-runtime-disclosure")?.setAttribute("open", "");
    return;
  }
  if (action === "check-strata-hardware") {
    state.strataDraftUrl = String(document.querySelector("[data-strata-url]")?.value || "").trim();
    state.strataPreflight = await request("/api/strata/preflight");
    renderSettings();
    return;
  }
  if (action === "connect-strata") {
    collectLocalModelsForm();
    const preset = (state.presets?.local_models || []).find((item) => item.id === "strata");
    if (!preset) {
      toast("无法读取 Strata 连接预设，请刷新页面", true);
      return;
    }
    const baseUrl = String(document.querySelector("[data-strata-url]")?.value || "").trim();
    state.strataDraftUrl = baseUrl;
    if (!/^http:\/\/(?:127\.0\.0\.1|\[::1\]):\d+\/v1\/?$/.test(baseUrl)) {
      state.strataProbeError = "Strata 地址须为本机 HTTP 地址，例如 http://127.0.0.1:8080/v1";
      state.strataCandidateConnection = null;
      renderSettings();
      toast(state.strataProbeError, true);
      return;
    }
    let result;
    try {
      result = await request("/api/local-models/test-strata", {
        method: "POST", body: JSON.stringify({ base_url: baseUrl }),
      });
    } catch (error) {
      state.strataProbeError = error.message;
      state.strataCandidateConnection = null;
      renderSettings();
      toast(error.message, true);
      return;
    }
    if (!result.ok) {
      state.strataProbeError = result.error || "尚未检测到已加载模型的 Strata 服务";
      state.strataCandidateConnection = null;
      renderSettings();
      toast(state.strataProbeError, true);
      return;
    }
    const existing = (state.settings.local_models || []).find((item) => item.id === "strata");
    const inUse = state.settings.active_model?.provider_id === "local-runtime-strata"
      || Object.values(state.settings.model_roles || {}).some((role) => String(role).startsWith("provider:local-runtime-strata:"));
    state.strataProbeError = "";
    if (existing && inUse && existing.base_url.replace(/\/$/, "") !== baseUrl.replace(/\/$/, "")) {
      state.strataCandidateConnection = { ...result, base_url: baseUrl };
      renderSettings();
      toast("候选地址已连接；验证后再切换默认助手");
      return;
    }
    state.strataCandidateConnection = null;
    if (existing) {
      existing.enabled = true;
      existing.base_url = baseUrl;
    } else state.settings.local_models.push({ ...structuredClone(preset), base_url: baseUrl, enabled: true });
    await persistSettings("Strata 连接已保存");
    await refreshModelHealth();
    state.strataDraftUrl = "";
    return;
  }
  if (action === "disconnect-strata") {
    collectLocalModelsForm();
    state.settings.local_models = (state.settings.local_models || []).filter((item) => item.id !== "strata" && !(item.runtime === "strata" && item.discovered));
    state.strataProbeError = "";
    state.strataDraftUrl = "";
    state.strataCandidateConnection = null;
    await persistSettings("Strata 连接已移除");
    await refreshModelHealth();
    return;
  }
  if (action === "activate-strata") {
    if (state.strataProbeRunning) return;
    collectLocalModelsForm();
    const modelId = String(document.querySelector("[data-strata-model]")?.value || "").trim();
    if (!modelId) return;
    const baseUrl = String(document.querySelector("[data-strata-url]")?.value || "").trim();
    state.strataDraftUrl = baseUrl;
    state.strataProbeRunning = true;
    state.strataProbeError = "";
    renderSettings();
    try {
      const result = await request("/api/local-models/activate-strata", {
        method: "POST",
        body: JSON.stringify({ local_model_id: element.dataset.localId, model_id: modelId, base_url: baseUrl }),
      });
      state.settings = result.settings;
      state.strataDraftUrl = "";
      state.strataCandidateConnection = null;
      await refreshModelHealth();
      renderModelSelectors();
      toast(`${modelId} 已通过流式回答、证据引用和工具调用验证，现为默认助手`);
    } catch (error) {
      state.strataProbeError = error.message;
      toast(error.message, true);
    } finally {
      state.strataProbeRunning = false;
      renderSettings();
    }
    return;
  }
  if (action === "remove-local-model") {
    collectLocalModelsForm();
    const removed = state.settings.local_models.splice(Number(element.dataset.localIndex), 1)[0];
    state.localRuntimeManualOpen = true;
    renderSettings();
    if (removed) toast(`${removed.name || "本地连接"} 已移除；点击“保存手动连接”后生效`);
    document.querySelector(".local-manual-runtime-disclosure")?.setAttribute("open", "");
    return;
  }
  if (action === "retry-local-model-probe") {
    const modelId = String(element.dataset.modelId || "").trim();
    if (!modelId) return;
    element.disabled = true;
    try {
      const result = await request("/api/local-models/recheck", {
        method: "POST",
        body: JSON.stringify({ id: modelId }),
      });
      await refreshLocalModelMarket();
      toast(result.message || "本地模型已重新检测");
    } catch (error) {
      await refreshLocalModelMarket();
      toast(error.message || "本地模型检测失败", true);
    } finally {
      if (element.isConnected) element.disabled = false;
    }
    return;
  }
  if (action === "delete-installed-local-model") {
    const modelId = String(element.dataset.modelId || "").trim();
    if (!modelId) return;
    const model = (state.localModelMarket?.installed || []).find(
      (item) => String(item?.id || "").trim().toLowerCase() === modelId.toLowerCase(),
    );
    const modelName = String(model?.name || modelId);
    const confirmed = await requestConfirmation({
      eyebrow: "删除本地模型",
      title: "删除这个已安装模型？",
      subject: modelName,
      message: "模型文件会从本机移除；之后仍可在模型市场重新下载。",
      confirmLabel: "删除模型",
      cancelLabel: "保留",
      danger: true,
    });
    if (!confirmed) return;
    element.disabled = true;
    try {
      await request("/api/local-models/delete", {
        method: "POST",
        body: JSON.stringify({ id: modelId }),
      });
      await refreshLocalModelMarket();
      toast(`${modelName} 已删除`);
    } finally {
      if (element.isConnected) element.disabled = false;
    }
    return;
  }
  if (action === "test-local-model") {
    collectLocalModelsForm();
    await persistSettings("正在测试本地运行时…");
    const result = await request(`/api/settings/local-models/${encodeURIComponent(element.dataset.localId)}/test`, { method: "POST", body: "{}" });
    if (!result.ok) {
      toast(result.error || "本地服务尚未就绪", true);
      return;
    }
    toast(`${result.runtime || "本地模型"}：${result.message || "连接正常"}`);
    return;
  }
  if (action === "refresh-local-model-market") {
    refreshLocalModelMarket().catch((error) => toast(error.message, true));
    return;
  }
  if (action === "refresh-runtime-components") {
    refreshRuntimeComponents().then(() => toast("运行组件状态已更新")).catch((error) => toast(error.message, true));
    return;
  }
  if (action === "install-runtime-component") {
    element.disabled = true;
    startRuntimeComponentInstall(element.dataset.componentId || "")
      .catch((error) => toast(error.message, true))
      .finally(() => { if (element.isConnected) element.disabled = false; });
    return;
  }
  if (action === "choose-runtime-component-files") {
    chooseRuntimeComponentFiles(element.dataset.componentId || "").catch((error) => toast(error.message, true));
    return;
  }
  if (action === "refresh-system-ocr-status") {
    await refreshSystemOcrStatus({ force: true });
    return;
  }
  if (action === "install-tesseract-ocr") {
    element.disabled = true;
    installTesseractOcr().catch((error) => toast(error.message, true));
    return;
  }
  if (action === "refresh-local-runtime") {
    refreshLocalRuntimeStatus().catch((error) => toast(error.message, true));
    return;
  }
  if (action === "check-local-runtime-channels") {
    checkLocalRuntimeChannels().catch((error) => toast(error.message, true));
    return;
  }
  if (action === "choose-local-runtime-files") {
    chooseLocalRuntimeFiles().catch((error) => toast(error.message, true));
    return;
  }
  if (action === "open-local-runtime-setup") {
    // Legacy deep links may still call openSettings("local-models"); the
    // visible destination is now the dedicated runtime page.
    openSettings("runtime");
    return;
  }
  if (action === "open-ollama-setup") {
    openSettings("runtime");
    toast("如果已经安装 Ollama，请启动它并在运行时页面确认连接地址。", true);
    return;
  }
  if (action === "install-local-runtime") {
    element.disabled = true;
    element.textContent = "正在准备…";
    request("/api/local-runtime/install", { method: "POST", body: "{}" })
      .then((job) => {
        state.localRuntime = { ...(state.localRuntime || {}), install_job: job };
        scheduleLocalRuntimeInstallPoll();
        if (state.activeView === "settings") renderSettings();
        renderDownloadActivity();
        toast("本地运行组件已开始下载；右上角可持续查看进度。");
      })
      .catch((error) => toast(error.message, true))
      .finally(() => { if (element.isConnected) element.disabled = false; });
    return;
  }
  if (action === "save-local-model-roles") {
    collectModelRoleForm();
    await persistSettings("资料库默认嵌入与重排模型已保存");
    return;
  }
  if (action === "download-local-model") {
    const repoId = element.dataset.modelRepo || "";
    if (!repoId) return;
    element.disabled = true;
    element.textContent = "准备下载…";
    request("/api/local-models/download", { method: "POST", body: JSON.stringify({ id: repoId, runtime: element.dataset.modelRuntime || "" }) })
      .then((job) => {
        mergeLocalModelInstall(job);
        scheduleLocalModelInstallPoll();
    if (state.activeView === "settings" && ["local-models", "runtime", "resources"].includes(state.activeSettings)) renderSettings();
        renderDownloadActivity();
        toast(`${repoId} 已开始下载；右上角可持续查看进度。`);
      })
      .catch((error) => toast(error.message, true))
      .finally(() => { if (element.isConnected) element.disabled = false; });
    return;
  }
  if (action === "activate-recommended-model") {
    const modelId = element.dataset.modelId || "";
    element.disabled = true;
    element.textContent = "正在验证模型…";
    try {
      const result = await request("/api/local-models/activate-recommended", {
        method: "POST",
        body: JSON.stringify({ id: modelId }),
      });
      state.settings = result.settings;
      await refreshLocalModelMarket();
      toast(`${modelId} 已通过生成、工具调用和证据引用验证，现为主力模型。`);
    } catch (error) {
      toast(error.message, true);
    } finally {
      if (element.isConnected) element.disabled = false;
    }
    return;
  }
  if (action === "install-retrieval-models") {
    startOnboardingResource("retrieval").catch((error) => toast(error.message, true));
    return;
  }
  if (action === "remove-record") {
    const kind = element.dataset.recordKind;
    const key = kind === "mcp" ? "mcp_servers" : kind;
    state.settings[key] = state.settings[key].filter((record) => record.id !== element.dataset.recordId);
    await persistSettings("配置已移除");
  }
  if (action === "remove-plugin-record") {
    state.settings.plugins = state.settings.plugins.filter((plugin) => plugin.id !== element.dataset.pluginId);
    await persistSettings("插件记录已移除");
  }
}

function switchReviewDocumentTab(tabName) {
  const safeTab = tabName === "source" ? "source" : "preview";
  byId("reviewDocumentPanel")?.querySelectorAll("[data-review-tab]").forEach((button) => button.classList.toggle("is-active", button.dataset.reviewTab === safeTab));
  byId("reviewDocumentPanel")?.querySelectorAll("[data-review-view]").forEach((view) => view.classList.toggle("is-active", view.dataset.reviewView === safeTab));
}

function showReviewCitation(citationId) {
  const citation = state.reviewDocument?.citations?.find((item) => String(item.citation_id) === String(citationId));
  const drawer = byId("reviewEvidenceDrawer");
  if (!citation || !drawer) return;
  hideCitationPreview();
  const sourceMeta = String(citation.section || "").trim() || "证据来源";
  const sourceDetails = evidenceDetailsMarkup(citation);
  const readerButton = safeReaderUrl(citation) ? `<button type="button" data-action="open-review-evidence-reader" data-citation-id="${escapeHtml(citation.citation_id)}">在应用中定位原文</button>` : "";
  const externalUrl = citationPublicSourceUrl(citation);
  const externalButton = externalUrl ? `<a class="review-evidence-link" href="${escapeHtml(externalUrl)}" target="_blank" rel="noopener noreferrer">打开原始来源 ${uiIcon("arrow-up-right")}</a>` : "";
  drawer.innerHTML = `<div class="review-evidence-head"><div><span>Evidence ${escapeHtml(citation.citation_id)}</span><strong>${escapeHtml(citation.paper)}</strong></div><button type="button" class="review-icon-button" data-action="close-review-evidence" aria-label="关闭证据">×</button></div><div class="review-evidence-body"><small>${escapeHtml(sourceMeta)}</small>${citationContextMarkup(citation, 900) || `<blockquote>${escapeHtml(compact(citation.exact_quote || "当前引用没有保存原文摘录。", 900))}</blockquote>`}${sourceDetails}${readerButton}${externalButton}</div>`;
  hydrateIcons(drawer);
  drawer.classList.add("is-open");
}

async function copyTextToClipboard(text) {
  const value = String(text || "");
  if (!value) return;
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("无法复制这条消息。");
}

async function copyConversationMessage(button) {
  await copyTextToClipboard(button?.dataset.copyText || "");
  button?.classList.add("is-copied");
  button?.setAttribute("aria-label", "已复制");
  toast("消息已复制");
  window.setTimeout(() => {
    button?.classList.remove("is-copied");
    button?.setAttribute("aria-label", "复制消息");
  }, 1200);
}

async function copyReviewDocument() {
  if (!state.reviewDocument?.markdown) return;
  await copyTextToClipboard(state.reviewDocument.markdown);
  toast("研究稿件 Markdown 已复制");
}

function downloadReviewDocument() {
  if (!state.reviewDocument?.markdown) return;
  const blob = new Blob([state.reviewDocument.markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${state.reviewDocument.title.replace(/[\\/:*?"<>|]+/g, "-").slice(0, 64) || "ScanSci-研究稿件"}.md`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function saveReviewAsNote() {
  openReviewSaveDialog();
}

function openImagePreview(src, alt = "用户图片") {
  const imageSrc = String(src || "").trim();
  if (!imageSrc) return;
  let dialog = byId("imagePreviewDialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "imagePreviewDialog";
    dialog.className = "image-preview-dialog";
    dialog.setAttribute("aria-label", "图片预览");
    dialog.innerHTML = `<div class="image-preview-shell"><button type="button" class="image-preview-close" data-action="close-image-preview" aria-label="关闭图片预览">${uiIcon("x")}</button><img id="imagePreviewImage" alt="" /></div>`;
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener("cancel", () => dialog.close());
    document.body.appendChild(dialog);
  }
  const image = byId("imagePreviewImage");
  if (image) {
    image.src = imageSrc;
    image.alt = String(alt || "用户图片");
  }
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

function closeImagePreview() {
  const dialog = byId("imagePreviewDialog");
  if (dialog?.open) dialog.close();
}

document.addEventListener("change", (event) => {
  const select = event.target.closest?.("select[data-preview-knowledge-select]");
  if (!select || state.activeSettings !== "knowledge-preview") return;
  const role = select.dataset.previewKnowledgeSelect === "reranking" ? "reranking" : "embedding";
  state.knowledgeSettingsPreview[role] = select.value;
  renderSettings();
});

document.addEventListener("click", (event) => {
  const helpButton = event.target.closest?.("[data-help-toggle]");
  const openHelp = document.querySelector(".setting-help.is-open");
  if (openHelp && (!helpButton || !openHelp.contains(helpButton))) {
    openHelp.classList.remove("is-open");
    openHelp.querySelector("[data-help-toggle]")?.setAttribute("aria-expanded", "false");
  }
  if (helpButton) {
    event.preventDefault();
    event.stopPropagation();
    const help = helpButton.closest(".setting-help");
    const shouldOpen = !help?.classList.contains("is-open");
    help?.classList.toggle("is-open", shouldOpen);
    helpButton.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
    return;
  }
  if (!event.target.closest("[data-settings-select]")) closeSettingsSelects();
  if (state.updateCardOpen && !event.target.closest("[data-app-update]")) toggleAppUpdateCard(false);
  if (!event.target.closest("[data-mode-picker]")) closeComposerModePickers();
  if (!event.target.closest("[data-composer-model]")) closeComposerModelPickers();
  if (!event.target.closest("[data-composer-thinking]")) closeComposerThinkingPickers();
  if (!event.target.closest("[data-context-usage]")) closeContextUsagePopovers();
  if (!event.target.closest("[data-web-search-picker]")) closeWebSearchPickers();
  if (!event.target.closest("[data-attachment-picker]")) closeAttachmentMenus();
  if (!event.target.closest("[data-profile-picker]")) closeProfileAvatarPicker();
  if (!event.target.closest(".task-row") && state.historyMenuRunId) {
    state.historyMenuRunId = "";
    renderTasks();
  }
  if (!event.target.closest(".skill-suggestions, .composer-skill-strip, #homeQuestionInput, #chatQuestionInput")) closeSkillSuggestions();
  const extensionTab = event.target.closest("[data-extension-tab]");
  if (extensionTab) {
    state.activeExtensions = extensionTab.dataset.extensionTab || "skills";
    if (state.activeView === "extensions") {
      renderExtensions();
      recordNavigation();
      if (state.activeExtensions === "market") refreshExtensions({ marketOnly: true, quiet: true }).catch((error) => toast(error.message, true));
    }
    return;
  }
  const settingsNav = event.target.closest("[data-settings-panel]");
  if (settingsNav && !settingsNav.dataset.action) {
    state.activeSettings = settingsNav.dataset.settingsPanel;
    if (state.activeView === "settings") {
      renderSettings();
      const settingsContent = byId("settingsContent");
      if (settingsContent) {
        settingsContent.scrollTop = 0;
        settingsContent.scrollLeft = 0;
      }
      recordNavigation();
      if (state.activeSettings === "mcp") loadMcpMarketplace().catch((error) => toast(error.message, true));
    }
    return;
  }
  const element = event.target.closest("[data-action]");
  if (!element) return;
  const action = element.dataset.action;
  if (action === "confirm-dialog-content" || action === "review-save-dialog-content") return;
  if (action === "cancel-confirm-dialog") settleConfirmation(false);
  else if (action === "close-review-save-dialog") closeReviewSaveDialog();
  else if (action === "choose-review-save-folder") chooseReviewSaveFolder().catch((error) => toast(error.message, true));
  else if (action === "confirm-review-save-note") commitReviewAsNote().catch((error) => toast(error.message, true));
  else if (action === "accept-confirm-dialog") settleConfirmation(true);
  else if (action === "jump-conversation-latest") followLatestConversationMessage({ smooth: true });
  else if (action === "jump-conversation-turn") jumpConversationTurn(element.dataset.turnIndex);
  else if (action === "minimize-window") controlDesktopWindow("minimize_window").catch((error) => toast(error.message, true));
  else if (action === "toggle-maximize-window") controlDesktopWindow("toggle_maximize_window").catch((error) => toast(error.message, true));
  else if (action === "close-window") controlDesktopWindow("close_window").catch((error) => toast(error.message, true));
  else if (action === "toggle-app-update") toggleAppUpdateCard();
  else if (action === "close-app-update") toggleAppUpdateCard(false);
  else if (action === "check-app-update") refreshAppUpdate().catch((error) => toast(error.message, true));
  else if (action === "install-app-update") installAppUpdate().catch((error) => toast(error.message, true));
  else if (action === "preview-open-library") openMode("library");
  else if (action === "toggle-preview-knowledge-advanced") {
    state.knowledgeSettingsPreview.advancedOpen = !state.knowledgeSettingsPreview.advancedOpen;
    renderSettings();
  }
  else if (action === "preview-knowledge-use-recommended") {
    const role = element.dataset.previewRole === "reranking" ? "reranking" : "embedding";
    state.knowledgeSettingsPreview[role] = "auto";
    renderSettings();
  }
  else if (action === "preview-knowledge-rebuild") toast("预览：索引检查完成，当前配置可以继续使用");
  else if (action === "preview-knowledge-save") toast("预览：检索设置已保存");
  else if (action === "open-download-center") openSettings("local-models");
  else if (action === "control-download-task") {
    element.disabled = true;
    controlDownloadTask(element.dataset.jobId || "", element.dataset.downloadAction || "", element.dataset.downloadKind || "model")
      .catch((error) => toast(error.message, true))
      .finally(() => { if (element.isConnected) element.disabled = false; });
  }
  else if (action === "toggle-attachment-menu") {
    event.preventDefault();
    toggleAttachmentMenu(element);
  }
  else if (action === "choose-composer-image") {
    const key = element.dataset.composerKey === "home" ? "home" : "chat";
    byId(`${key}ImageFileInput`)?.click();
  }
  else if (action === "choose-composer-audio") {
    const key = element.dataset.composerKey === "home" ? "home" : "chat";
    byId(`${key}AudioFileInput`)?.click();
  }
  else if (action === "toggle-composer-recording") {
    const key = element.dataset.composerKey === "home" ? "home" : "chat";
    toggleComposerRecording(key);
  }
  else if (action === "choose-composer-source") chooseComposerSources(element.dataset.composerKey === "home" ? "home" : "chat").catch((error) => toast(error.message, true));
  else if (action === "choose-presentation-sources") choosePresentationSources(element.dataset.composerKey === "home" ? "home" : "chat").catch((error) => toast(error.message, true));
  else if (action === "remove-composer-image") removeComposerImage(element.dataset.composerKey === "home" ? "home" : "chat", element.dataset.imageId || "");
  else if (action === "remove-composer-audio") removeComposerAudio(element.dataset.composerKey === "home" ? "home" : "chat", element.dataset.audioId || "");
  else if (action === "remove-composer-source") removeComposerSource(element.dataset.composerKey === "home" ? "home" : "chat", element.dataset.sourceId || "");
  else if (action === "open-image-preview") { event.preventDefault(); openImagePreview(element.dataset.imageSrc || "", element.dataset.imageAlt || "用户图片"); }
  else if (action === "close-image-preview") closeImagePreview();
  else if (action === "reuse-image-prompt") reuseImagePrompt(element.dataset.prompt || "");
  else if (action === "rename-direct-conversation") renameDirectConversation();
  else if (action === "use-file-suggestion") useFileSuggestion(element.dataset.composerKey === "home" ? "home" : "chat", element.dataset.fileName || "当前文件", element.dataset.suggestion || "总结");
  else if (action === "open-ingestion-source") {
    const url = element.dataset.sourceUrl || "";
    const name = element.dataset.sourceName || "附件";
    if (name.toLowerCase().endsWith(".pdf") && window.ScanSciPdfViewer) window.ScanSciPdfViewer.open(url, name);
    else window.open(url, "_blank", "noopener,noreferrer");
  }
  else if (action === "choose-library-folder") chooseLibraryFolder("folder", element.dataset.notebookId || "").catch((error) => toast(error.message, true));
  else if (action === "open-translation") openTranslationDialog({ showLocation: element.dataset.openLocation === "true" }).catch((error) => toast(error.message, true));
  else if (action === "close-translation") closeTranslationDialog();
  else if (action === "configure-translation-model") { closeTranslationDialog(); openSettings("models"); }
  else if (action === "pick-translation-folder") pickTranslationFolder().catch((error) => toast(error.message, true));
  else if (action === "save-translation-folder") saveTranslationFolder().catch((error) => toast(error.message, true));
  else if (action === "cancel-translation") request(`/api/translations/${encodeURIComponent(translationRunId)}/cancel`, { method: "POST", body: "{}" }).then(() => refreshTranslationStatus()).catch((error) => toast(error.message, true));
  else if (action === "cancel-translation-run") {
    request(`/api/translations/${encodeURIComponent(element.dataset.runId || "")}/cancel`, { method: "POST", body: "{}" })
      .then((run) => { upsertRun(run); if (state.activeTaskId === run.run_id) renderRun(run); })
      .catch((error) => toast(error.message, true));
  }
  else if (action === "open-translation-result") {
    const url = `/api/translations/${encodeURIComponent(translationRunId)}/pdf`;
    if (window.ScanSciPdfViewer) window.ScanSciPdfViewer.open(url, "译文 PDF");
    else window.open(url, "_blank", "noopener,noreferrer");
  }
  else if (action === "open-translation-run-pdf") {
    const url = `/api/translations/${encodeURIComponent(element.dataset.runId || "")}/pdf`;
    if (window.ScanSciPdfViewer) window.ScanSciPdfViewer.open(url, "译文 PDF");
    else window.open(url, "_blank", "noopener,noreferrer");
  }
  else if (action === "open-translation-file") {
    const url = `/api/translations/files/${encodeURIComponent(element.dataset.fileId || "")}`;
    if (window.ScanSciPdfViewer) window.ScanSciPdfViewer.open(url, element.dataset.fileTitle || "译文 PDF");
    else window.open(url, "_blank", "noopener,noreferrer");
  }
  else if (action === "show-translation-library") {
    refreshTranslationLibrary().then(() => {
      state.notebook = (state.workspace?.notebooks || []).find((item) => item.notebook_id === "translation-library") || state.notebook;
      closeTranslationDialog();
      openMode("library");
    }).catch((error) => toast(error.message, true));
  }
  else if (action === "choose-obsidian-vault") chooseLibraryFolder("obsidian", element.dataset.notebookId || "").catch((error) => toast(error.message, true));
  else if (action === "choose-zotero-library") connectLocalZotero(element.dataset.notebookId || "").catch((error) => toast(error.message, true));
  else if (action === "choose-zotero-data-directory") chooseZoteroDataDirectory(element.dataset.notebookId || "").catch((error) => toast(error.message, true));
  else if (action === "retry-zotero-connection") {
    const notebookId = element.dataset.notebookId || state.notebook?.notebook_id || "";
    const notebook = (state.workspace?.notebooks || []).find((item) => String(item.notebook_id) === String(notebookId)) || state.notebook;
    const dataDir = state.zoteroConnectionIssue?.dataDir
      || notebook?.metadata?.zotero?.configured_data_dir
      || "";
    connectLocalZotero(notebookId, dataDir).catch((error) => toast(error.message, true));
  }
  else if (action === "connect-notion") connectNotion(element.dataset.notebookId || "").catch((error) => toast(error.message, true));
  else if (action === "choose-library-files") chooseLibraryFiles(element.dataset.notebookId || state.notebook?.notebook_id || "").catch((error) => toast(error.message, true));
  else if (action === "retry-evidence-index") {
    const notebookId = element.dataset.notebookId || state.notebook?.notebook_id || "";
    state.knowledgeIndexStatuses[notebookId] = {
      ...(state.knowledgeIndexStatuses[notebookId] || {}),
      state: "indexing",
      error: "",
    };
    syncKnowledgeIndexBadge(notebookId);
    ensureActiveKnowledgeIndex(notebookId, { force: true }).catch((error) => toast(error.message, true));
  }
  else if (action === "retry-local-ai") {
    const notebookId = element.dataset.notebookId || state.notebook?.notebook_id || "";
    refreshLocalAiStatus(notebookId, { prepare: true, force: true }).catch((error) => toast(error.message, true));
  }
  else if (action === "refresh-evidence-index") refreshKnowledgeIndexStatus(element.dataset.notebookId || "").catch(() => {});
  else if (action === "refresh-knowledge-scope-counts") refreshKnowledgeScopeCounts();
  else if (action === "create-empty-library") {
    closeKnowledgeScopeDialog();
    openLibraryPathDialog("empty");
  }
  else if (action === "delete-personal-library") deletePersonalLibrary(element.dataset.notebookId || "").catch((error) => toast(error.message, true));
  else if (action === "focus-knowledge-file-search") focusKnowledgeFileSearch();
  else if (action === "close-knowledge-file-search") closeKnowledgeFileSearch();
  else if (action === "open-knowledge-scope") openKnowledgeScopeDialog();
  else if (action === "close-knowledge-scope") closeKnowledgeScopeDialog();
  else if (action === "apply-knowledge-scope") applyKnowledgeScopeSelection();
  else if (action === "clear-knowledge-scope") {
    setKnowledgeScope(null, { close: false });
    toast("已移除本轮知识库范围");
  }
  else if (action === "remove-knowledge-scope") {
    removeKnowledgeScope(element.dataset.notebookId || "");
  }
  else if (action === "toggle-knowledge-scope-draft") {
    toggleKnowledgeScopeDraft(element.dataset.notebookId || "");
  }
  else if (action === "toggle-notebook-scope") {
    const notebook = (state.workspace?.notebooks || []).find((item) => item.notebook_id === element.dataset.notebookId);
    if (notebook) setKnowledgeScope(notebook, { close: false, toggle: true });
  }
  else if (action === "select-notebook") {
    const notebook = (state.workspace?.notebooks || []).find((item) => item.notebook_id === element.dataset.notebookId);
    if (notebook) {
      if (!notebookHasSearchableContent(notebook)) {
        const connectAction = unavailableKnowledgeAction(notebook);
        if (connectAction === "choose-zotero-library") connectLocalZotero(notebook.notebook_id).catch((error) => toast(error.message, true));
        else if (connectAction === "choose-obsidian-vault") chooseLibraryFolder("obsidian", notebook.notebook_id).catch((error) => toast(error.message, true));
        else if (connectAction === "connect-notion") connectNotion(notebook.notebook_id).catch((error) => toast(error.message, true));
        else chooseLibraryFiles(notebook.notebook_id).catch((error) => toast(error.message, true));
        return;
      }
      setKnowledgeScope(notebook, { close: false });
      toast(`已引用 ${notebook.title || pathLeaf(notebook.root_path)}`);
    }
  }
  else if (action === "select-zotero-collection") {
    const notebook = (state.workspace?.notebooks || []).find((item) => item.notebook_id === element.dataset.notebookId);
    if (notebook) {
      setZoteroCollectionScope(notebook, element.dataset.collectionKey || "", element.dataset.collectionName || "Collection");
      toast(`本轮将使用 Zotero / ${element.dataset.collectionName || "Collection"}`);
    }
  }
  else if (action === "activate-library") {
    const notebook = (state.workspace?.notebooks || []).find((item) => item.notebook_id === element.dataset.notebookId);
    if (notebook) {
      const previousNotebookId = state.notebook?.notebook_id;
      state.notebook = notebook;
      if (String(previousNotebookId || "") !== String(notebook.notebook_id || "")) state.knowledgeSubscope = null;
      state.knowledgePreviewSourceId = "";
      state.knowledgeQuery = "";
      state.knowledgeVisibleLimit = 200;
      window.localStorage.setItem("scansci.knowledge.scope", notebook.notebook_id);
      renderMode();
      if (knowledgeSourceKind(notebook) === "translation") void refreshTranslationLibrary();
      else void ensureActiveKnowledgeIndex();
    }
  }
  else if (action === "load-more-knowledge-items") {
    state.knowledgeVisibleLimit = Math.max(200, Number(state.knowledgeVisibleLimit) || 200) + 200;
    renderMode();
  }
  else if (action === "toggle-knowledge-folders") {
    state.knowledgeTreeExpanded = state.knowledgeTreeExpanded === false;
    window.localStorage.setItem("scansci.knowledge.tree.expanded", String(state.knowledgeTreeExpanded));
    renderMode();
  }
  else if (action === "preview-knowledge-source") {
    event.preventDefault();
    state.knowledgePreviewSourceId = element.dataset.sourceId || "";
    renderMode();
  }
  else if (action === "toggle-active-library-scope") {
    if (state.notebook && notebookHasSearchableContent(state.notebook)) setKnowledgeScope(state.notebook, { close: false, toggle: true });
  }
  else if (action === "use-knowledge-suggestion") {
    if (state.notebook && !(state.knowledgeScopeIds || []).includes(state.notebook.notebook_id)) {
      setKnowledgeScope(state.notebook, { close: false });
    }
    setView("home");
    const input = byId("homeQuestionInput");
    if (input) {
      input.value = element.dataset.prompt || "";
      input.focus();
    }
  }
  else if (action === "export-pptxgenjs") exportActiveSlidePlan(element).catch((error) => toast(error.message, true));
  else if (action === "save-presentation") savePresentationToDevice(element.dataset.presentationPath, element.dataset.presentationName).then((result) => { if (!result?.cancelled) toast(`已保存 ${result.path || element.dataset.presentationName}`); }).catch((error) => toast(error.message, true));
  else if (action === "close-library-dialog") closeLibraryPathDialog();
  else if (action === "close-academic-search-plan") closeAcademicSearchPlanDialog();
  else if (action === "open-slide-templates") openSlideTemplateDialog();
  else if (action === "close-slide-templates") closeSlideTemplateDialog();
  else if (action === "preview-slide-template") {
    state.previewSlideTemplateId = element.dataset.templateId || state.previewSlideTemplateId;
    state.previewSlidePage = "";
    renderSlideTemplateBrowser();
  }
  else if (action === "preview-slide-page") {
    state.previewSlidePage = element.dataset.templatePage || "";
    renderSlideTemplateBrowser();
  }
  else if (action === "select-slide-template") selectPreviewedSlideTemplate();
  else if (action === "select-inline-slide-template") {
    event.preventDefault();
    selectSlideTemplate(element.dataset.templateId || "");
  }
  else if (action === "preview-inline-slide-template") {
    event.preventDefault();
    openInlineSlidePreview(element.dataset.templateId || "");
  }
  else if (action === "preview-inline-slide-page") {
    event.preventDefault();
    state.inlineSlidePreviewPage = element.dataset.templatePage || "";
    renderInlineSlidePreview();
  }
  else if (action === "close-inline-slide-preview") closeInlineSlidePreview();
  else if (action === "select-inline-preview-template") selectInlinePreviewedSlideTemplate();
  else if (action === "open-library") {
    closeAttachmentMenus();
    openMode("library");
  }
  else if (action === "toggle-composer-mode") {
    event.preventDefault();
    toggleComposerModePicker(element);
  }
  else if (action === "toggle-web-search") {
    event.preventDefault();
    toggleWebSearchPicker(element);
  }
  else if (action === "select-web-search") {
    event.preventDefault();
    setWebSearchMode(element.dataset.webSearchValue || "auto");
    closeWebSearchPickers();
    element.closest("[data-web-search-picker]")?.querySelector("[data-action='toggle-web-search']")?.focus();
  }
  else if (action === "select-composer-mode") {
    event.preventDefault();
    const requestedMode = element.dataset.modeValue || "general";
    const currentMode = byId("homeModeSelect")?.value || "general";
    // The home shortcuts are toggle buttons. Clicking the selected
    // shortcut again returns to the clean general/automatic state.
    const nextMode = requestedMode !== "general" && requestedMode === currentMode ? "general" : requestedMode;
    setComposerMode(nextMode);
    closeComposerModePickers();
    element.focus();
  }
  else if (action === "apply-mode-tool") {
    event.preventDefault();
    const mode = byId("homeModeSelect")?.value || "general";
    const tool = modeWorkbenchContent[mode]?.tools.find((item) => item.id === element.dataset.modeTool);
    if (!tool) return;
    if (mode === "knowledge" && tool.evidenceOutput) {
      setEvidenceOutputMode(tool.evidenceOutput);
      byId("homeQuestionInput")?.focus();
      return;
    }
    if (["research", "academic"].includes(mode) && tool.workflow) {
      const fallbackWorkflow = mode === "academic" ? "academic" : "";
      state.researchWorkflow = state.researchWorkflow === tool.workflow ? fallbackWorkflow : tool.workflow;
      renderHomeModeWorkbench(mode);
      renderKnowledgeScopeSurfaces();
    }
    if (tool.action === "upload") byId("homeSourceFileInput")?.click();
    else if (tool.action === "template") openSlideTemplateDialog();
    else homePromptFromWorkbench(tool.prompt);
  }
  else if (action === "apply-mode-example") {
    event.preventDefault();
    const mode = byId("homeModeSelect")?.value || "general";
    const example = currentModeWorkbenchContent(mode)?.examples.find((item) => item.id === element.dataset.modeExample);
    if (example) {
      if (["research", "academic"].includes(mode) && example.workflow) {
        state.researchWorkflow = example.workflow;
        renderHomeModeWorkbench(mode);
        renderKnowledgeScopeSurfaces();
      }
      homePromptFromWorkbench(example.prompt, `已填入“${example.title}”案例`);
    }
  }
  else if (action === "pick-home-batch-file") {
    event.preventDefault();
    byId("homePaperBatchFile")?.click();
  }
  else if (action === "clear-home-batch") {
    event.preventDefault();
    state.pendingBatchIdentifiers = [];
    state.pendingBatchFilename = "";
    renderHomeModeWorkbench("download");
  }
  else if (action === "start-home-batch-download") {
    event.preventDefault();
    if (!state.pendingBatchIdentifiers.length) return;
    const input = byId("homeQuestionInput");
    if (input) input.value = state.pendingBatchIdentifiers.join("\n");
    byId("homeAskForm")?.requestSubmit();
  }
  else if (action === "apply-download-guide") {
    event.preventDefault();
    const guide = modeWorkbenchContent.download.guides.find((item) => item.id === element.dataset.downloadGuide);
    if (guide?.prompt) {
      state.pendingBatchIdentifiers = [];
      state.pendingBatchFilename = "";
      renderHomeModeWorkbench("download");
      homePromptFromWorkbench(guide.prompt, `已填入“${guide.title}”`);
    }
  }
  else if (action === "prepare-idea-novelty") {
    event.preventDefault();
    state.researchWorkflow = "novelty";
    setComposerMode("research", { preserveResearchWorkflow: true });
    const input = byId("chatQuestionInput") || byId("homeQuestionInput");
    if (input) {
      input.value = `问题：${element.dataset.noveltyProblem || ""}\n\n新颖性：${element.dataset.noveltyClaim || ""}`;
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
    toast("已带入证据查新；确认问题与创新主张后即可运行。");
  }
  else if (action === "toggle-composer-model") {
    event.preventDefault();
    toggleComposerModelPicker(element);
  }
  else if (action === "toggle-agent-mode") {
    event.preventDefault();
    setAgentMode(!state.agentMode);
  }
  else if (action === "toggle-context-usage") {
    event.preventDefault();
    toggleContextUsagePopover(element);
  }
  else if (action === "select-composer-model") {
    event.preventDefault();
    setActiveComposerModel(element.dataset.modelValue || "").catch((error) => toast(error.message, true));
  }
  else if (action === "toggle-composer-thinking") {
    event.preventDefault();
    toggleComposerThinkingPicker(element);
  }
  else if (action === "select-composer-thinking") {
    event.preventDefault();
    setComposerThinkingLevel(element.dataset.thinkingValue || "auto");
  }
  else if (action === "select-skill-suggestion") {
    event.preventDefault();
    selectSkillSuggestion(byId(element.dataset.inputId || ""), element.dataset.skillId || "");
  }
  else if (action === "remove-composer-skill") {
    event.preventDefault();
    const key = element.dataset.composerKey === "chat" ? "chat" : "home";
    removeComposerSkill(key, element.dataset.skillId || "");
    byId(`${key}QuestionInput`)?.focus();
  }
  else if (action === "review-document-tab") switchReviewDocumentTab(element.dataset.reviewTab || "preview");
  else if (action === "scroll-review-section") {
    state.reviewDocumentOpen = true;
    applyContextPanelPreset("review");
    switchReviewDocumentTab("preview");
    window.requestAnimationFrame(() => byId(element.dataset.sectionId || "review-abstract")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }
  else if (action === "open-review-citation") showReviewCitation(element.dataset.citationId || "");
  else if (action === "close-review-evidence") byId("reviewEvidenceDrawer")?.classList.remove("is-open");
  else if (action === "open-review-evidence-reader") {
    const citation = state.reviewDocument?.citations?.find((item) => String(item.citation_id) === String(element.dataset.citationId));
    if (citation) showEvidenceReader(citation, { returnPanel: "review" });
  }
  else if (action === "close-evidence-reader") closeEvidenceReader();
  else if (action === "show-evidence-blocks") {
    const frame = byId("evidenceReaderFrame");
    if (frame && state.activeEvidence?.reader_url) frame.src = evidenceReaderFrameUrl(state.activeEvidence.reader_url);
    element.parentElement?.querySelectorAll("button").forEach((button) => button.classList.toggle("is-active", button === element));
  }
  else if (action === "show-evidence-original") {
    const frame = byId("evidenceReaderFrame");
    if (frame && element.dataset.originalUrl) frame.src = element.dataset.originalUrl;
    element.parentElement?.querySelectorAll("button").forEach((button) => button.classList.toggle("is-active", button === element));
  }
  else if (action === "toggle-profile-avatar") {
    event.preventDefault();
    toggleProfileAvatarPicker(element);
  }
  else if (action === "select-profile-avatar") selectProfileAvatar(element.dataset.avatarValue || "male");
  else if (action === "open-source-reader") {
    const source = (state.notebook?.sources || []).find((item) => String(item.doc_id) === String(element.dataset.docId));
    if (source) openSourceReader(source);
  }
  else if (action === "retry-direct-message") retryDirectMessage(element.dataset.messageIndex || "");
  else if (action === "edit-last-conversation-message") beginEditingLastConversationMessage(element.dataset.messageIndex || "");
  else if (action === "save-last-message-edit") saveEditingLastConversationMessage();
  else if (action === "cancel-last-message-edit") cancelEditingLastConversationMessage();
  else if (action === "edit-direct-follow-up") beginEditingQueuedDirectTurn(element.dataset.queueId || "");
  else if (action === "save-direct-queue-edit") saveEditingQueuedDirectTurn();
  else if (action === "cancel-direct-queue-edit") cancelEditingQueuedDirectTurn();
  else if (action === "set-queued-direct-mode") setQueuedDirectTurnMode(element.dataset.queueId || "", element.dataset.queueMode || "follow-up").catch((error) => toast(error.message, true));
  else if (action === "remove-direct-follow-up") removeQueuedDirectTurn(element.dataset.queueId || "");
  else if (action === "cancel-direct-chat") pauseDirectChatJob().catch((error) => toast(error.message, true));
  else if (action === "pause-direct-chat") pauseDirectChatJob().catch((error) => toast(error.message, true));
  else if (action === "resume-direct-chat") resumeDirectChatJob().catch((error) => toast(error.message, true));
  else if (action === "copy-conversation-message") copyConversationMessage(element).catch((error) => toast(error.message, true));
  else if (action === "copy-review-document") copyReviewDocument().catch((error) => toast(error.message, true));
  else if (action === "save-review-note") saveReviewAsNote();
  else if (action === "download-review-document") downloadReviewDocument();
  else if (action === "select-all-review-sources") setReviewSourceSelection(true);
  else if (action === "clear-review-sources") setReviewSourceSelection(false);
  else if (action === "refresh-review-document") {
    if (state.activeTaskId) openTask(state.activeTaskId, { record: false });
  }
  else if (action === "toggle-review-focus") {
    const focused = byId("conversationLayout")?.classList.toggle("is-review-focus");
    element.textContent = focused ? "↙" : "↗";
    element.setAttribute("aria-label", focused ? "退出专注阅读" : "专注阅读");
  }
  else if (["close-review-document", "return-review-conversation"].includes(action)) {
    state.reviewDocumentOpen = false;
    hideCitationPreview();
    applyContextPanelPreset("none");
  }
  else if (action === "open-review-document") {
    state.reviewDocumentOpen = true;
    applyContextPanelPreset("review");
  }
  else if (action === "toggle-evidence-panel-expand") toggleEvidencePanelExpanded();
  else if (action === "toggle-context-panel") toggleContextPanel();
  else if (action === "toggle-sidebar") toggleSidebar();
  else if (action === "history-back") moveNavigation(-1);
  else if (action === "history-forward") moveNavigation(1);
  else if (action === "toggle-history-collapse") toggleHistoryCollapse();
  else if (action === "toggle-history-search") toggleHistorySearch();
  else if (action === "toggle-history-view") toggleHistoryView();
  else if (action === "toggle-task-menu") toggleTaskMenu(element.dataset.taskId || "");
  else if (action === "toggle-direct-menu") toggleDirectMenu(element.dataset.conversationId || "");
  else if (action === "archive-task") archiveTask(element.dataset.taskId || "").catch((error) => toast(error.message, true));
  else if (action === "restore-task") restoreTask(element.dataset.taskId || "").catch((error) => toast(error.message, true));
  else if (action === "delete-task") deleteTask(element.dataset.taskId || "").catch((error) => toast(error.message, true));
  else if (action === "archive-direct-conversation") archiveDirectConversation(element.dataset.conversationId || "").catch((error) => toast(error.message, true));
  else if (action === "restore-direct-conversation") restoreDirectConversation(element.dataset.conversationId || "").catch((error) => toast(error.message, true));
  else if (action === "delete-direct-conversation") deleteDirectConversation(element.dataset.conversationId || "").catch((error) => toast(error.message, true));
  else if (action === "new-task") startTask();
  else if (action === "open-extensions") openExtensions();
  else if (action === "open-mcp-marketplace") openMcpMarketplace();
  else if (action === "close-settings") closeSettings();
  else if (action === "test-mcp-server") testMcpServer(element.dataset.recordId || "").catch((error) => toast(error.message, true));
  else if (action === "check-extension-updates") refreshExtensionUpdates().catch((error) => toast(error.message, true));
  else if (action === "refresh-marketplace") refreshExtensions({ marketOnly: true }).catch((error) => toast(error.message, true));
  else if (action === "install-market-skill") installMarketSkill(element.dataset.marketSkillId || "").catch((error) => toast(error.message, true));
  else if (action === "update-skill") scanSkillUpdate(element.dataset.extensionId || "").catch((error) => toast(error.message, true));
  else if (action === "close-skill-security") closeSkillSecurityReview();
  else if (action === "confirm-skill-install") confirmSkillInstall().catch((error) => toast(error.message, true));
  else if (action === "open-extension-detail") {
    state.extensionDetail = { kind: element.dataset.extensionKind, id: element.dataset.extensionId };
    renderExtensions();
  }
  else if (action === "close-extension-detail") {
    state.extensionDetail = null;
    renderExtensions();
  }
  else if (action === "extension-detail-content") {
    return;
  }
  else if (action === "uninstall-extension") {
    const key = element.dataset.extensionKind === "skills" ? "skills" : "plugins";
    const record = state.settings[key]?.find((row) => row.id === element.dataset.extensionId);
    if (record) {
      record.enabled = false;
      record.uninstalled = true;
      if (key === "skills") state.extensions.skills = (state.extensions.skills || []).filter((row) => row.id !== record.id);
      state.extensionDetail = null;
      persistSettings("已从当前工作区卸载").catch((error) => toast(error.message, true));
    }
  }
  else if (action === "toggle-download-strategy") {
    state.downloadStrategyOpen = !state.downloadStrategyOpen;
    renderMode();
  }
  else if (action === "select-download-strategy") {
    const strategy = element.dataset.downloadStrategy || "oa_first";
    if (["oa_first", "gray_oa", "legal_only"].includes(strategy)) {
      state.downloadStrategy = strategy;
      state.downloadStrategyOpen = false;
      window.localStorage.setItem("scansci.download.strategy", strategy);
      renderMode();
    }
  }
  else if (action === "clear-batch-identifiers") {
    state.pendingBatchIdentifiers = [];
    renderMode();
  }
  else if (action === "toggle-use-tor") {
    state.useTor = element.checked;
    renderMode();
  }
  else if (action === "toggle-tor-transport") {
    state.torTransportOpen = !state.torTransportOpen;
    renderMode();
  }
  else if (action === "select-tor-transport") {
    const transport = element.dataset.torTransport;
    if (["snowflake", "obfs4", "none"].includes(transport)) {
      state.torTransport = transport;
      state.torTransportOpen = false;
      renderMode();
    }
  }
  else if (action === "set-tor-rotate") {
    const value = Number(element.value);
    if (value >= 1 && value <= 20) {
      state.torRotateEvery = value;
    }
  }
  else if (action === "start-batch-download") {
    handlePaperBatchDownload().catch((error) => showModeError(error));
  }
  else if (action === "retry-batch-download") {
    const identifiers = String(element.dataset.identifiers || "")
      .split(/\r?\n|,/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (!identifiers.length) return;
    handlePaperBatchDownload(identifiers).catch((error) => showModeError(error));
  }
  else if (action === "toggle-knowledge-preview") {
    state.knowledgePreviewCollapsed = !state.knowledgePreviewCollapsed;
    window.localStorage.setItem("scansci.knowledge.previewCollapsed", state.knowledgePreviewCollapsed ? "1" : "0");
    renderMode();
  }
  else if (action === "open-mode") openMode(element.dataset.mode || "tools");
  else if (action === "toggle-embedded-browser") {
    window.pywebview?.api?.toggle_browser?.()
      .then((result) => {
        if (!result?.ok) throw new Error(result?.message || "内置浏览器暂时不可用");
        updateEmbeddedBrowserButton(result.open);
      })
      .catch((error) => toast(error.message, true));
  }
  else if (action === "open-external") {
    const url = element.dataset.url || "";
    if (/^https?:\/\//i.test(url)) openEmbeddedBrowser(url).catch((error) => toast(error.message, true));
    else if (url.startsWith("/")) window.open(url, "_blank", "noopener");
  }
  else if (action === "open-local-path") {
    event.preventDefault();
    openLocalArtifact(element.dataset.localPath || "").catch((error) => toast(error.message, true));
  }
  else if (action === "reveal-local-path") {
    openLocalArtifact(element.dataset.localPath || "", { reveal: true }).catch((error) => toast(error.message, true));
  }
  else if (action === "create-ppt-project") createPptProject().catch((error) => toast(error.message, true));
  else if (action === "cancel-run") cancelRun(element.dataset.runId).catch((error) => toast(error.message, true));
  else if (action === "pause-run") pauseRun(element.dataset.runId).catch((error) => toast(error.message, true));
  else if (action === "resume-run") resumeRun(element.dataset.runId).catch((error) => toast(error.message, true));
  else if (action === "respond-agent-interaction") respondAgentInteraction(element).catch((error) => toast(error.message, true));
  else if (action === "respond-run-interaction") respondRunInteraction(element).catch((error) => toast(error.message, true));
  else if (action === "recover-run") recoverRun(element).catch((error) => toast(error.message, true));
  else if (action === "advisor-action") advisorAction(element).catch((error) => toast(error.message, true));
  else if (action === "branch-run") branchRun(element).catch((error) => toast(error.message, true));
  else if (action === "compact-session") compactSession().catch((error) => toast(error.message, true));
  else if (action === "quick-search") {
    setView("conversation");
    window.setTimeout(() => byId("sourceFilter").focus(), 0);
  } else if (action === "open-settings") openSettings(element.dataset.settingsPanel || "general");
  else if (action === "sync-mcp-marketplace") syncMcpMarketplace().catch((error) => toast(error.message, true));
  else if (action === "install-mcp-marketplace") installMcpMarketplaceServer(element.dataset.mcpId || "").catch((error) => toast(error.message, true));
  else if (action === "update-mcp-marketplace") updateMcpMarketplaceServer(element.dataset.recordId || "").catch((error) => toast(error.message, true));
  else if (action === "mcp-set-tab") {
    state.mcpMarketplaceTab = element.dataset.mcpTab === "mine" ? "mine" : "public";
    state.mcpManualOpen = false;
    state.mcpCreateMode = "";
    refreshMcpMarketplaceSurface();
  } else if (action === "mcp-set-sort") {
    state.mcpMarketplaceSort = ["hot", "new", "name"].includes(element.dataset.mcpSort) ? element.dataset.mcpSort : "hot";
    refreshMcpMarketplaceSurface();
  } else if (action === "open-mcp-manual") {
    state.mcpManualOpen = true;
    state.mcpCreateMode = "";
    refreshMcpMarketplaceSurface();
  } else if (action === "mcp-select-create-mode") {
    state.mcpCreateMode = element.dataset.mcpCreateMode === "remote" ? "remote" : "stdio";
    refreshMcpMarketplaceSurface();
    window.setTimeout(() => byId("mcpManualForm")?.elements["mcp-name"]?.focus(), 0);
  } else if (action === "mcp-create-back") {
    state.mcpCreateMode = "";
    refreshMcpMarketplaceSurface();
  } else if (action === "close-mcp-manual") {
    state.mcpManualOpen = false;
    state.mcpCreateMode = "";
    refreshMcpMarketplaceSurface();
  }
  else if (action === "open-conversation") setView("conversation");
  else if (action === "open-task") {
    state.historyMenuRunId = "";
    openTask(element.dataset.taskId);
  }
  else if (action === "open-direct-conversation") {
    state.historyMenuRunId = "";
    openDirectConversation(element.dataset.conversationId).catch((error) => toast(error.message, true));
  }
  else handleSettingsAction(action, element).catch((error) => toast(error.message, true));
});

document.addEventListener("dblclick", (event) => {
  if (!event.target.closest("[data-titlebar-drag]")) return;
  controlDesktopWindow("toggle_maximize_window").catch((error) => toast(error.message, true));
});

document.addEventListener("change", (event) => {
  if (event.target.name === "provider-coding-plan" && event.target.closest("#modelProviderForm")) {
    const provider = selectedProvider();
    const form = event.target.form;
    const endpointInput = form?.elements["provider-base-url"];
    if (!provider || provider.id !== "zhipu" || !endpointInput) return;
    const previousMode = Boolean(provider.coding_plan_enabled);
    const currentUrl = String(endpointInput.value || "").trim();
    if (previousMode) {
      provider.coding_plan_base_url = currentUrl || provider.coding_plan_base_url || ZHIPU_CODING_PLAN_BASE_URL;
    } else {
      provider.standard_base_url = currentUrl || provider.standard_base_url || ZHIPU_STANDARD_BASE_URL;
    }
    provider.coding_plan_enabled = Boolean(event.target.checked);
    const nextUrl = zhipuEndpointValue(provider, provider.coding_plan_enabled);
    endpointInput.value = nextUrl;
    provider.base_url = nextUrl;
    form.querySelector(".cherry-endpoint-mode-label")?.replaceChildren(document.createTextNode(provider.coding_plan_enabled ? "Coding Plan" : "标准 API"));
    const guideCopy = form.querySelector("[data-zhipu-guide-copy]");
    if (guideCopy) guideCopy.textContent = provider.coding_plan_enabled
      ? "当前使用 Coding Plan 专用地址；该地址仅适用于 GLM Coding Plan。"
      : "当前使用标准 API 地址；开启 Coding Plan 后会切换到专用地址。";
    const preview = form.querySelector(".cherry-endpoint-preview");
    if (preview) preview.textContent = `预览：${nextUrl ? `${nextUrl.replace(/\/$/, "")}/chat/completions` : "请填写服务商 API 地址"}`;
    return;
  }
  if (event.target.closest("#generalPreferencesForm")) {
    collectAppearanceForm();
    renderSettings();
    return;
  }
  if (event.target.closest("#generalConversationForm, #generalDirectoriesForm")) {
    collectGeneralSettingsForm();
    persistSettings("常规设置已保存").catch((error) => toast(error.message, true));
    return;
  }
  if (event.target.id === "reviewSaveFolderInput") {
    const files = [...(event.target.files || [])];
    const folderLabel = String(files[0]?.webkitRelativePath || "").split("/")[0].trim();
    if (!files.length || !folderLabel || !state.reviewSaveDialog?.open) return;
    state.reviewSaveDialog.folderPath = "";
    state.reviewSaveDialog.browserDirectoryHandle = null;
    state.reviewSaveDialog.browserFolderLabel = folderLabel;
    state.reviewSaveDialog.browserFolderMode = "input";
    state.reviewSaveDialog.error = "预览环境只能识别文件夹名称；保存时会下载 Markdown。桌面应用可直接写入所选文件夹。";
    renderReviewSaveDialog();
    return;
  }
  if (event.target.matches("[data-review-source]")) {
    updateReviewSourceCount();
    return;
  }
  if (event.target.id === "mcpMarketplaceDiscipline") {
    state.mcpMarketplaceDiscipline = event.target.value || "all";
    refreshMcpMarketplaceSurface();
    return;
  }
  if (["homeModeSelect", "chatModeSelect"].includes(event.target.id)) {
    setComposerMode(event.target.value);
    return;
  }
  if (event.target.matches("[data-composer-image-file]")) {
    const key = event.target.dataset.composerImageFile === "home" ? "home" : "chat";
    const files = [...(event.target.files || [])];
    event.target.value = "";
    addComposerImages(key, files).catch((error) => toast(error.message, true));
    return;
  }
  if (event.target.matches("[data-composer-source-file]")) {
    const key = event.target.dataset.composerSourceFile === "home" ? "home" : "chat";
    const files = [...(event.target.files || [])];
    event.target.value = "";
    addComposerSources(key, files).catch((error) => toast(error.message, true));
    return;
  }
  if (event.target.closest("#documentProcessingForm, #defaultDocumentProcessingForm") && event.target.name === "ocr-language") {
    collectDocumentProcessingForm(event.target.form?.id || "");
    refreshSystemOcrStatus({ force: true }).catch((error) => toast(error.message, true));
    return;
  }
  if (event.target.closest("#documentProcessingForm, #defaultDocumentProcessingForm") && ["ocr-provider", "mineru-provider"].includes(event.target.name)) {
    collectDocumentProcessingForm();
    renderSettings();
    if (event.target.name === "ocr-provider") refreshSystemOcrStatus({ force: true }).catch((error) => toast(error.message, true));
    return;
  }
  if (event.target.dataset.action === "toggle-record") {
    const kind = event.target.dataset.recordKind;
    const key = kind === "mcp" ? "mcp_servers" : kind;
    const recordId = event.target.dataset.recordId;
    const configuredRecords = Array.isArray(state.settings?.[key]) ? state.settings[key] : [];
    const runtimeRecords = kind === "skills" && Array.isArray(state.extensions.skills)
      ? state.extensions.skills
      : configuredRecords;
    const record = configuredRecords.find((item) => item.id === recordId)
      || runtimeRecords.find((item) => item.id === recordId);
    if (record) {
      const enabled = event.target.checked;
      record.enabled = enabled;
      const configuredRecord = configuredRecords.find((item) => item.id === recordId);
      if (configuredRecord) configuredRecord.enabled = enabled;
      const runtimeRecord = runtimeRecords.find((item) => item.id === recordId);
      if (runtimeRecord) runtimeRecord.enabled = enabled;
      persistSettings("启用状态已保存").catch((error) => toast(error.message, true));
    }
  }
});

function clearProviderDragFeedback() {
  document.querySelectorAll(".cherry-provider-item.is-dragging, .cherry-provider-item.is-drop-target").forEach((item) => {
    item.classList.remove("is-dragging", "is-drop-target");
  });
  state.draggedProviderId = "";
}

function clearDirectQueueDragFeedback() {
  document.querySelectorAll(".direct-live-queue-item.is-dragging, .direct-live-queue-item.is-drop-before, .direct-live-queue-item.is-drop-after").forEach((item) => {
    item.classList.remove("is-dragging", "is-drop-before", "is-drop-after");
  });
  directQueueDrag = { conversationId: "", queueId: "", targetId: "", position: "" };
}

document.addEventListener("dragstart", (event) => {
  const handle = event.target.closest?.("[data-direct-queue-drag]");
  const row = handle?.closest("[data-direct-queue-item]");
  if (handle && row) {
    const conversationId = row.dataset.queueConversationId || "";
    const job = directChatJobs.get(conversationId);
    if (job?.queue.length) {
      directQueueDrag = { conversationId, queueId: row.dataset.queueId || "", targetId: "", position: "" };
      row.classList.add("is-dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", directQueueDrag.queueId);
      }
    }
    return;
  }
  const providerRow = event.target.closest?.("[data-provider-drag-id]");
  if (!providerRow || state.activeSettings !== "models") return;
  collectProviderForm();
  state.draggedProviderId = providerRow.dataset.providerDragId || "";
  if (!state.draggedProviderId) return;
  providerRow.classList.add("is-dragging");
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", state.draggedProviderId);
  }
});

document.addEventListener("dragover", (event) => {
  const queueRow = event.target.closest?.("[data-direct-queue-item]");
  if (queueRow && directQueueDrag.queueId && queueRow.dataset.queueConversationId === directQueueDrag.conversationId && queueRow.dataset.queueId !== directQueueDrag.queueId) {
    event.preventDefault();
    const bounds = queueRow.getBoundingClientRect();
    const position = event.clientY < bounds.top + bounds.height / 2 ? "before" : "after";
    document.querySelectorAll(".direct-live-queue-item.is-drop-before, .direct-live-queue-item.is-drop-after").forEach((item) => item.classList.remove("is-drop-before", "is-drop-after"));
    queueRow.classList.add(position === "before" ? "is-drop-before" : "is-drop-after");
    directQueueDrag.targetId = queueRow.dataset.queueId || "";
    directQueueDrag.position = position;
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    return;
  }
  const libraryDropzone = event.target.closest?.("[data-library-dropzone]");
  if (libraryDropzone && Array.from(event.dataTransfer?.types || []).includes("Files")) {
    event.preventDefault();
    libraryDropzone.classList.add("is-drag-over");
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    return;
  }
  const row = event.target.closest?.("[data-provider-drag-id]");
  if (!row || !state.draggedProviderId || row.dataset.providerDragId === state.draggedProviderId) return;
  event.preventDefault();
  document.querySelectorAll(".cherry-provider-item.is-drop-target").forEach((item) => item.classList.remove("is-drop-target"));
  row.classList.add("is-drop-target");
  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
});

document.addEventListener("dragleave", (event) => {
  const queueRow = event.target.closest?.("[data-direct-queue-item]");
  if (queueRow && !queueRow.contains(event.relatedTarget)) queueRow.classList.remove("is-drop-before", "is-drop-after");
  const libraryDropzone = event.target.closest?.("[data-library-dropzone]");
  if (libraryDropzone && !libraryDropzone.contains(event.relatedTarget)) libraryDropzone.classList.remove("is-drag-over");
  event.target.closest?.("[data-provider-drag-id]")?.classList.remove("is-drop-target");
});

document.addEventListener("dragend", clearProviderDragFeedback);
document.addEventListener("dragend", clearDirectQueueDragFeedback);

document.addEventListener("drop", (event) => {
  const queueRow = event.target.closest?.("[data-direct-queue-item]");
  if (queueRow && directQueueDrag.queueId && queueRow.dataset.queueConversationId === directQueueDrag.conversationId) {
    event.preventDefault();
    const job = directChatJobs.get(directQueueDrag.conversationId);
    const sourceId = directQueueDrag.queueId;
    const targetId = queueRow.dataset.queueId || "";
    if (job && sourceId !== targetId) {
      const sourceIndex = job.queue.findIndex((turn) => turn.queueId === sourceId);
      const targetIndexBeforeMove = job.queue.findIndex((turn) => turn.queueId === targetId);
      if (sourceIndex >= 0 && targetIndexBeforeMove >= 0) {
        const bounds = queueRow.getBoundingClientRect();
        const position = directQueueDrag.targetId === targetId && directQueueDrag.position
          ? directQueueDrag.position
          : event.clientY < bounds.top + bounds.height / 2 ? "before" : "after";
        const [moved] = job.queue.splice(sourceIndex, 1);
        const targetIndex = job.queue.findIndex((turn) => turn.queueId === targetId);
        const insertIndex = Math.max(0, Math.min(job.queue.length, targetIndex + (position === "after" ? 1 : 0)));
        job.queue.splice(insertIndex, 0, moved);
        clearDirectQueueDragFeedback();
        toast("队列顺序已更新");
        renderDirectLiveControls();
        renderTasks();
        persistDirectChatJob(job).catch((error) => console.warn("Reordered direct chat queue could not be saved", error));
      }
    }
    clearDirectQueueDragFeedback();
    return;
  }
  const libraryDropzone = event.target.closest?.("[data-library-dropzone]");
  if (libraryDropzone && event.dataTransfer?.files?.length) {
    event.preventDefault();
    libraryDropzone.classList.remove("is-drag-over");
    importLibraryDroppedFiles(event.dataTransfer.files, libraryDropzone.dataset.notebookId || "").catch((error) => toast(error.message, true));
    return;
  }
  const row = event.target.closest?.("[data-provider-drag-id]");
  const sourceId = state.draggedProviderId || event.dataTransfer?.getData("text/plain");
  const targetId = row?.dataset.providerDragId;
  if (!row || !sourceId || !targetId || sourceId === targetId) {
    clearProviderDragFeedback();
    return;
  }
  event.preventDefault();
  const providers = state.settings?.providers || [];
  const cloudProviders = providers.filter((item) => item.kind !== "local");
  const sourceIndex = cloudProviders.findIndex((item) => item.id === sourceId);
  const targetIndex = cloudProviders.findIndex((item) => item.id === targetId);
  if (sourceIndex < 0 || targetIndex < 0) {
    clearProviderDragFeedback();
    return;
  }
  const [moved] = cloudProviders.splice(sourceIndex, 1);
  cloudProviders.splice(targetIndex, 0, moved);
  state.settings.providers = [...cloudProviders, ...providers.filter((item) => item.kind === "local")];
  clearProviderDragFeedback();
  persistSettings("服务商排序已保存").catch((error) => toast(error.message, true));
});

document.addEventListener("keydown", (event) => {
  const openHelp = document.querySelector(".setting-help.is-open");
  if (openHelp && event.key === "Escape") {
    event.preventDefault();
    openHelp.classList.remove("is-open");
    openHelp.querySelector("[data-help-toggle]")?.setAttribute("aria-expanded", "false");
    return;
  }
  if (confirmDialogResolve) {
    if (event.key === "Escape") {
      event.preventDefault();
      settleConfirmation(false);
      return;
    }
    if (trapConfirmationFocus(event)) return;
  }
  if (state.reviewSaveDialog?.open) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeReviewSaveDialog();
      return;
    }
    if (trapReviewSaveFocus(event)) return;
  }
  if (resourceOnboardingDialog()) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeResourceGuideOverlay("skip").catch((error) => toast(error.message, true));
      return;
    }
    if (trapResourceOnboardingFocus(event)) return;
  }
  const lastMessageEditInput = event.target.closest?.("[data-last-message-edit-input]");
  if (lastMessageEditInput) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelEditingLastConversationMessage();
      return;
    }
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing) {
      event.preventDefault();
      saveEditingLastConversationMessage();
      return;
    }
    return;
  }
  const queueEditInput = event.target.closest?.("[data-queue-edit-input]");
  if (queueEditInput) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelEditingQueuedDirectTurn();
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      saveEditingQueuedDirectTurn();
      return;
    }
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f" && state.activeView === "library") {
    event.preventDefault();
    focusKnowledgeFileSearch();
    return;
  }
  const composer = event.target.closest("#homeQuestionInput, #chatQuestionInput, #reviewQuestionInput");
  if (composer && document.querySelector(".skill-suggestions")) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      moveSkillSuggestion(event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      const active = document.querySelector(".skill-suggestion.is-active");
      if (active) {
        event.preventDefault();
        selectSkillSuggestion(composer, active.dataset.skillId || "");
        return;
      }
    }
  }
  if (composer && ["homeQuestionInput", "chatQuestionInput"].includes(composer.id) && event.key === "Backspace" && !event.isComposing && !document.querySelector(".skill-suggestions") && composer.value === "" && composer.selectionStart === 0 && composer.selectionEnd === 0) {
    const key = composerKey(composer.id);
    const lastSkill = composerSkillRecords(key).at(-1);
    if (lastSkill) {
      event.preventDefault();
      removeComposerSkill(key, lastSkill.id);
      return;
    }
  }
  const sendWithShift = composerUsesShiftEnterToSend();
  if (composer && event.key === "Enter" && (sendWithShift ? event.shiftKey : !event.shiftKey) && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    composer.form?.requestSubmit();
    return;
  }
  if (event.key === "Escape") {
    if (state.activeView === "library" && state.knowledgeSearchOpen && event.target.matches?.("[data-knowledge-file-search]")) {
      event.preventDefault();
      closeKnowledgeFileSearch();
      return;
    }
    if (directChatJob()) cancelDirectChatJob().catch(() => {});
    toggleAppUpdateCard(false);
    closeComposerModePickers();
    closeComposerModelPickers();
    closeAttachmentMenus();
    closeProfileAvatarPicker();
    closeSkillSuggestions();
  }
});

document.addEventListener("input", (event) => {
  if (event.target.id === "translationParentPath") {
    updateTranslationLocation();
    return;
  }
  if (event.target.matches?.("[data-last-message-edit-input]")) {
    if (directMessageEdit) directMessageEdit.value = String(event.target.value || "");
    return;
  }
  if (event.target.matches?.("[data-queue-edit-input]")) {
    if (directQueueEdit) directQueueEdit.value = String(event.target.value || "");
    return;
  }
  if (event.target.name === "provider-base-url" && event.target.closest?.("#modelProviderForm")) {
    const provider = selectedProvider();
    if (provider?.id === "zhipu") {
      const value = String(event.target.value || "").trim();
      const codingPlanEnabled = Boolean(event.target.form?.elements["provider-coding-plan"]?.checked);
      provider.base_url = value;
      if (codingPlanEnabled) provider.coding_plan_base_url = value;
      else provider.standard_base_url = value;
      const preview = event.target.form?.querySelector(".cherry-endpoint-preview");
      if (preview) preview.textContent = `预览：${value ? `${value.replace(/\/$/, "")}/chat/completions` : "请填写服务商 API 地址"}`;
    }
    return;
  }
  if (event.target.id === "reviewSaveNewFolderInput") {
    state.reviewSaveDialog.newFolderName = String(event.target.value || "");
    return;
  }
  if (event.target.matches("[data-knowledge-file-search]")) {
    state.knowledgeQuery = String(event.target.value || "");
    state.knowledgeVisibleLimit = 200;
    const cursor = state.knowledgeQuery.length;
    renderMode();
    window.setTimeout(() => {
      const search = document.querySelector("[data-knowledge-file-search]");
      search?.focus();
      search?.setSelectionRange(cursor, cursor);
    }, 0);
    return;
  }
  if (["homeQuestionInput", "chatQuestionInput"].includes(event.target.id)) {
    renderSkillSuggestions(event.target);
    renderComposerSendButtons();
    return;
  }
  if (event.target.id === "historySearch") {
    state.historyQuery = event.target.value;
    renderTasks();
    return;
  }
  if (event.target.id === "extensionsMarketSearch") {
    state.marketplaceQuery = event.target.value;
    renderExtensions();
    window.setTimeout(() => byId("extensionsMarketSearch")?.focus(), 0);
  }
  if (event.target.id === "modelServiceSearch") {
    state.providerQuery = event.target.value;
    renderSettings();
    window.setTimeout(() => byId("modelServiceSearch")?.focus(), 0);
  }
  if (event.target.id === "modelListSearch") {
    state.modelQuery = event.target.value;
    renderSettings();
    window.setTimeout(() => byId("modelListSearch")?.focus(), 0);
  }
  if (event.target.id === "archiveSettingsSearch") {
    state.archiveSettingsQuery = event.target.value;
    renderSettings();
    window.setTimeout(() => byId("archiveSettingsSearch")?.focus(), 0);
  }
  if (event.target.id === "mcpMarketplaceSearch") {
    state.mcpMarketplaceQuery = event.target.value;
    refreshMcpMarketplaceSurface();
    window.setTimeout(() => byId("mcpMarketplaceSearch")?.focus(), 0);
  }
});

// Desktop webviews and IME paths do not always dispatch a bubbling `input`
// event for every keystroke.  Keep Skill mention suggestions in sync with
// keyboard input as well, while preserving the input handler above for paste
// and programmatic text-entry paths.
document.addEventListener("keyup", (event) => {
  if (
    ["homeQuestionInput", "chatQuestionInput"].includes(event.target.id)
    && !["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(event.key)
  ) {
    renderSkillSuggestions(event.target);
  }
});

document.addEventListener("paste", (event) => {
  const input = event.target.closest("#homeQuestionInput, #chatQuestionInput");
  if (!input) return;
  const files = [...(event.clipboardData?.items || [])]
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (!files.length) return;
  event.preventDefault();
  addComposerImages(composerKey(input.id), files).catch((error) => toast(error.message, true));
});

document.addEventListener("change", async (event) => {
  if (event.target.id === "skillSecurityAcknowledge") {
    const button = byId("skillSecurityInstall");
    if (button) button.disabled = !event.target.checked;
    return;
  }
  if (event.target.matches("[data-composer-audio-file]")) {
    const key = event.target.dataset.composerAudioFile === "home" ? "home" : "chat";
    const files = [...(event.target.files || [])];
    event.target.value = "";
    addComposerAudio(key, files).catch((error) => toast(error.message, true));
    return;
  }
  const toggle = event.target.closest("[data-update-auto-check]");
  if (!toggle) return;
  const previous = state.autoCheckUpdates;
  state.autoCheckUpdates = Boolean(toggle.checked);
  const general = generalPreferences();
  general.updates.auto_check = state.autoCheckUpdates;
  state.settings.general = general;
  try {
    await persistSettings(state.autoCheckUpdates ? "已开启自动检查更新" : "已关闭自动检查更新");
  } catch (error) {
    state.autoCheckUpdates = previous;
    general.updates.auto_check = previous;
    state.settings.general = general;
    renderSettings();
    toast(`更新设置保存失败：${error.message}`, true);
  }
});

byId("skillSecurityDialog")?.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeSkillSecurityReview();
});

document.addEventListener("change", (event) => {
  if (event.target.id === "translationFile") {
    const file = event.target.files?.[0];
    const name = byId("translationFileName");
    name.textContent = file?.name || "选择 PDF";
    name.title = file?.name || "";
    return;
  }
  const input = event.target.closest('#paperBatchFile[data-action="pick-batch-file"], #homePaperBatchFile[data-action="pick-home-batch-file"]');
  if (!input) return;
  const file = input.files && input.files[0];
  input.value = "";
  if (!file) return;
  parseBatchFile(file)
    .then((identifiers) => {
      state.pendingBatchIdentifiers = identifiers;
      state.pendingBatchFilename = identifiers.length ? file.name : "";
      if (input.id === "homePaperBatchFile") {
        const composer = byId("homeQuestionInput");
        if (composer) composer.value = "";
        renderHomeModeWorkbench("download");
      }
      else renderMode();
      toast(identifiers.length ? `已解析 ${identifiers.length} 个标识符` : "未在文件中识别到 DOI 或 arXiv ID", !identifiers.length);
    })
    .catch((error) => toast(`解析失败：${error.message}`, true));
});

document.addEventListener("submit", (event) => {
  if (event.target.id === "translationForm") {
    event.preventDefault();
    submitTranslation().catch((error) => {
      const status = byId("translationStatus");
      status.hidden = false;
      status.textContent = error.message;
      toast(error.message, true);
    });
    return;
  }
  if (event.target.id === "localModelMarketSearch") {
    event.preventDefault();
    state.localModelMarket.query = event.target.elements.query.value.trim();
    refreshLocalModelMarket().catch((error) => toast(error.message, true));
    return;
  }
  if (event.target.id === "mcpManualForm") {
    event.preventDefault();
    addManualMcpServer(event.target).catch((error) => toast(error.message, true));
  }
  else if (event.target.id === "academicSearchPlanForm") {
    event.preventDefault();
    startReviewedAcademicSearch().catch((error) => toast(`无法开始联网检索：${error.message}`, true));
  }
  else if (event.target.id === "libraryPathForm") {
    event.preventDefault();
    const value = byId("libraryPathInput").value.trim();
    if (!value) return;
    state.libraryImportGuided = false;
    const operation = state.libraryImportKind === "files"
      ? importLibraryFiles(value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean), state.libraryImportNotebookId || state.notebook?.notebook_id || "")
      : state.libraryImportKind === "empty"
        ? createEmptyLibrary(value)
      : state.libraryImportKind === "zotero"
        ? registerZoteroLibrary(value)
      : state.libraryImportKind === "zotero-data"
        ? connectLocalZotero(state.libraryImportNotebookId || state.notebook?.notebook_id || "", value)
      : bindLibraryFolder(value, state.libraryImportKind, state.libraryImportNotebookId || state.notebook?.notebook_id || "");
    operation.catch((error) => toast(error.message, true));
  }
  else if (event.target.id === "homeAskForm") askQuestion(event, "homeQuestionInput");
  else if (event.target.id === "chatAskForm") askQuestion(event, "chatQuestionInput");
  else if (event.target.id === "reviewAskForm") askQuestion(event, "reviewQuestionInput");
  else if (event.target.id === "journalSearchForm") handleJournalSearch(event).catch((error) => showModeError(error));
  else if (event.target.id === "referenceAnalyzeForm") handleReferenceAnalyze(event).catch((error) => showModeError(error));
  else if (event.target.id === "atlasSearchForm") handleAtlasSearch(event).catch((error) => showModeError(error));
  else if (event.target.id === "paperDownloadForm") handlePaperDownload(event).catch((error) => showModeError(error));
  else if (event.target.id === "pptOutlineForm") handlePptOutline(event).catch((error) => showModeError(error));
  else if (event.target.id === "modelProviderForm") {
    event.preventDefault();
    const provider = collectProviderForm();
    const key = String(event.target.elements["provider-api-key"]?.value || "").trim();
    if (key && provider) provider.enabled = true;
    (async () => {
      await persistSettings(key ? "提供商与密钥已保存" : "提供商已保存");
      if (!key || !provider) return;
      state.settings = await request(`/api/settings/providers/${encodeURIComponent(provider.id)}/api-key`, { method: "POST", body: JSON.stringify({ api_key: key }) });
      await refreshModelHealth({ render: false });
      renderModelSelectors();
      renderSettings();
      toast("密钥已保存到系统凭据管理器");
    })().catch((error) => toast(error.message, true));
  } else if (event.target.id === "localModelsForm") {
    event.preventDefault();
    collectLocalModelsForm();
    persistSettings("本地模型已保存").then(() => refreshModelHealth()).catch((error) => toast(error.message, true));
  } else if (event.target.id === "generalPreferencesForm") {
    event.preventDefault();
    collectAppearanceForm();
    persistSettings(copy("appearanceSaved")).catch((error) => toast(error.message, true));
  } else if (["generalConversationForm", "generalDirectoriesForm"].includes(event.target.id)) {
    event.preventDefault();
    collectGeneralSettingsForm();
    persistSettings("常规设置已保存").catch((error) => toast(error.message, true));
  } else if (event.target.id === "defaultCapabilitiesForm") {
    event.preventDefault();
    collectDefaultCapabilitiesForm();
    persistSettings("默认能力已保存").catch((error) => toast(error.message, true));
  } else if (["documentProcessingForm", "defaultDocumentProcessingForm"].includes(event.target.id)) {
    event.preventDefault();
    const ocrKey = String(event.target.elements["ocr-api-key"]?.value || "").trim();
    const mineruKey = String(event.target.elements["mineru-api-key"]?.value || "").trim();
    collectDocumentProcessingForm(event.target.id);
    (async () => {
      await persistSettings(ocrKey || mineruKey ? "文档处理配置与密钥已保存" : "文档处理配置已保存");
      if (ocrKey) state.settings = await request("/api/settings/document-processing/ocr/api-key", { method: "POST", body: JSON.stringify({ api_key: ocrKey }) });
      if (mineruKey) state.settings = await request("/api/settings/document-processing/mineru/api-key", { method: "POST", body: JSON.stringify({ api_key: mineruKey }) });
      renderSettings();
      if (ocrKey || mineruKey) toast("密钥已保存到系统凭据管理器");
    })().catch((error) => toast(error.message, true));
  } else if (event.target.id === "modelRoleForm") {
    event.preventDefault();
    collectModelRoleForm();
    persistSettings("模型路由已保存").catch((error) => toast(error.message, true));
  } else if (event.target.id === "skillInstallForm") {
    event.preventDefault();
    const sourceType = event.target.elements["source-type"].value;
    const source = event.target.elements.source.value.trim();
    installSkill(sourceType, source).catch((error) => toast(error.message, true));
  } else if (event.target.id === "extensionPluginForm") {
    event.preventDefault();
    const source = event.target.elements["plugin-source"].value.trim();
    const name = event.target.elements["plugin-name"].value.trim();
    if (!source || !name) return;
    state.settings.plugins.push({
      id: `plugin-${Date.now()}`,
      name,
      source,
      description: event.target.elements["plugin-description"].value.trim(),
      enabled: true,
    });
    persistSettings("插件来源已登记").catch((error) => toast(error.message, true));
  } else if (event.target.dataset.recordForm) {
    event.preventDefault();
    const kind = event.target.dataset.recordForm;
    const key = kind === "mcp" ? "mcp_servers" : kind;
    state.settings[key].push(newRecord(kind, event.target));
    persistSettings("配置已保存").catch((error) => toast(error.message, true));
  }
});

function setModeLoading(message) {
  const target = byId("modeResults");
  if (target) target.innerHTML = `<div class="mode-loading"><span></span>${escapeHtml(message)}</div>`;
}

function showModeError(error) {
  const target = byId("modeResults");
  if (target) target.innerHTML = `<div class="error-state">${escapeHtml(error.message)}</div>`;
  toast(error.message, true);
}

async function handleJournalSearch(event) {
  event.preventDefault();
  await startModeRun("journal_search", { query: byId("journalQuery").value, limit: 10 }, "正在查询期刊分区…");
}

async function handleReferenceAnalyze(event) {
  event.preventDefault();
  await startModeRun("citation_analysis", { text: byId("referenceText").value, mode: byId("referenceMode").value }, "正在核对元数据与正文断言…");
}

async function handleAtlasSearch(event) {
  event.preventDefault();
  await startModeRun("paper_atlas", { query: byId("atlasQuery").value }, "正在寻找图谱入口…");
}

async function handlePaperDownload(event) {
  event.preventDefault();
  const strategy = state.downloadStrategy;
  const message = strategy === "oa_first" ? "正在优先查找开放获取版本…" : "正在从合规来源获取文献…";
  await startModeRun("paper_download", { identifier: byId("paperIdentifier").value, strategy }, message);
}

const BATCH_IDENTIFIER_PATTERN = /\b(?:10\.\d{4,9}\/\S+?)(?=[\s"'}\],]|$)|(?:arxiv:\s*)?\d{4}\.\d{4,5}(?:v\d+)?|[a-z]+-[a-z]+\/\d{7}/gi;

function extractBatchIdentifiers(text) {
  const matches = String(text || "").match(BATCH_IDENTIFIER_PATTERN) || [];
  const seen = new Set();
  const cleaned = [];
  for (const raw of matches) {
    const id = raw.trim().replace(/[.,;:)]+$/, "");
    if (!id || seen.has(id)) continue;
    seen.add(id);
    cleaned.push(id);
  }
  return cleaned;
}

async function parseBatchFile(file) {
  const MAX_BYTES = 5 * 1024 * 1024;
  if (file.size > MAX_BYTES) throw new Error("文件过大（上限 5MB）");
  const text = await file.text();
  return extractBatchIdentifiers(text);
}

async function handlePaperBatchDownload(identifiers) {
  const list = (identifiers && identifiers.length ? identifiers : state.pendingBatchIdentifiers) || [];
  if (!list.length) {
    toast("请先上传包含 DOI 或 arXiv ID 的文件", true);
    return;
  }
  const strategy = state.downloadStrategy;
  const payload = { identifiers: list, strategy };
  if (state.useTor) {
    payload.use_tor = true;
    payload.rotate_every = Math.max(1, Number(state.torRotateEvery) || 3);
    payload.tor_transport = state.torTransport;
  }
  state.pendingBatchIdentifiers = [];
  const loadingMsg = state.useTor ? `正在通过 Tor 批量获取 ${list.length} 篇文献（首次需下载 Tor）…` : `正在批量获取 ${list.length} 篇文献…`;
  await startModeRun("paper_download_batch", payload, loadingMsg);
}

async function handlePptOutline(event) {
  event.preventDefault();
  await startModeRun("ppt_outline", { topic: byId("pptTopic").value, template_id: state.selectedSlideTemplateId }, "正在把来源组织成汇报叙事…");
}

async function createPptProject() {
  if (!state.notebook) throw new Error("请先打开一个资料库");
  await startModeRun("ppt_project", { topic: byId("pptTopic")?.value || "", template_id: state.selectedSlideTemplateId }, "正在创建演示文稿并导入来源…");
}

async function startModeRun(workflowType, input, loadingMessage) {
  setModeLoading(loadingMessage);
  const run = await createResearchRun(workflowType, input);
  state.activeTaskId = run.run_id;
  window.localStorage.setItem("scansci.active.task", run.run_id);
  upsertRun(run);
  renderModeRun(run);
  watchRun(run.run_id, (next) => {
    if (state.activeView === "mode" && state.activeTaskId === next.run_id) renderModeRun(next);
  });
}

function batchItemMarkup(items) {
  const ICON = { pending: "○", downloading: "·", completed: "✓", failed: "×", cancelled: "—" };
  return (items || []).map((item) => {
    const status = item.status || "pending";
    return `<li class="paper-batch-item is-${escapeHtml(status)}"><span class="paper-batch-item-icon">${ICON[status] || "○"}</span><div class="paper-batch-item-body"><strong>${escapeHtml(item.identifier || "")}</strong>${item.error ? `<small>${escapeHtml(item.error)}</small>` : Array.isArray(item.files) && item.files.length ? `<small>${item.files.map((f) => escapeHtml(f.split(/[\\/]/).pop())).join("，")}</small>` : ""}</div></li>`;
  }).join("");
}

function renderModeRun(run) {
  if (run.status === "completed" && run.output_artifact) {
    renderModeArtifact(run);
    return;
  }
  const stages = (run.stages || []).map((stage) => `<li class="${escapeHtml(stage.status)}"><span>${stage.status === "completed" ? "✓" : stage.status === "running" ? "·" : stage.position + 1}</span><div><strong>${escapeHtml(stage.title)}</strong><small>${escapeHtml(stage.error_message || stage.summary || "等待执行")}</small></div></li>`).join("");
  const action = run.pausable ? `<button type="button" class="run-action stop" data-action="pause-run" data-run-id="${escapeHtml(run.run_id)}">暂停任务</button>` : run.resumable ? `<button type="button" class="run-action" data-action="resume-run" data-run-id="${escapeHtml(run.run_id)}">${run.status === "needs_confirmation" ? "确认计划并执行" : "从当前阶段继续"}</button>` : "";
  const executeStage = (run.stages || []).find((stage) => stage.kind === "tool");
  const batchItems = run.workflow_type === "paper_download_batch" && executeStage && executeStage.output && Array.isArray(executeStage.output.items) ? `<ul class="paper-batch-progress">${batchItemMarkup(executeStage.output.items)}</ul>` : "";
  byId("modeResults").innerHTML = `<section class="mode-run"><header><div><span>${escapeHtml(runStatusLabel(run))}</span><strong>${escapeHtml(run.title)}</strong></div>${action}</header><div class="run-progress"><i class="${progressWidthClass(Number(run.progress || 0) * 100)}"></i></div><ol>${stages}</ol>${batchItems}${run.status === "failed" ? `<p class="mode-run-error">${escapeHtml(runFailureSummary(run))}</p>` : ""}</section>`;
}

function renderModeArtifact(run) {
  const artifact = run.output_artifact;
  const payload = artifact.payload || {};
  if (run.workflow_type === "journal_search") renderJournalResults(payload);
  else if (run.workflow_type === "citation_analysis") renderReferenceResults(payload);
  else if (run.workflow_type === "paper_atlas") renderAtlasResults(payload);
  else if (run.workflow_type === "paper_download") {
    const files = (payload.files || []).map((file) => localFileLinkMarkup(file, localPathLeaf(file), { inline: true })).join("");
    const fallback = payload.output_dir || artifact.file_path;
    const fileMarkup = files || (fallback ? localFileLinkMarkup(fallback, localPathLeaf(fallback), { inline: true }) : "");
    byId("modeResults").innerHTML = `<div class="download-result"><span>✓</span><div><strong>文献已保存</strong><p>${escapeHtml(payload.identifier || run.title)}</p>${fileMarkup}</div></div>`;
  } else if (run.workflow_type === "paper_download_batch") {
    const items = Array.isArray(payload.items) ? payload.items : [];
    const completed = Number(payload.completed || 0);
    const failed = Number(payload.failed || 0);
    const failedIds = items.filter((item) => item.status === "failed").map((item) => item.identifier);
    const retry = failedIds.length ? `<button type="button" class="run-action" data-action="retry-batch-download" data-identifiers="${escapeHtml(failedIds.join("\n"))}">重试失败项 (${failedIds.length})</button>` : "";
    byId("modeResults").innerHTML = `<section class="mode-run"><header><div><span>批量完成</span><strong>${escapeHtml(run.title)}</strong></div>${retry}</div></header><p class="paper-batch-summary">成功 ${completed}/${payload.total || items.length}，失败 ${failed}</p><ul class="paper-batch-progress">${batchItemMarkup(items)}</ul></section>`;
  } else if (["ppt_outline", "ppt_project"].includes(run.workflow_type)) {
    renderPptOutline(payload);
    if (run.workflow_type === "ppt_project") toast("PPT 项目已创建");
  } else if (run.workflow_type === "pdf_to_ppt") {
    byId("modeResults").innerHTML = slideProjectArtifactMarkup(payload, run.run_id);
    toast("可编辑 PPTX 已生成");
  } else byId("modeResults").innerHTML = genericArtifactMarkup(artifact);
}

async function cancelRun(runId) {
  const run = await request(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST", body: "{}" });
  upsertRun(run);
  if (state.activeView === "conversation") renderRun(run);
  else if (state.activeView === "mode") renderModeRun(run);
  toast(run.cancel_requested ? "停止请求已发送" : "任务已停止");
}

async function pauseRun(runId) {
  const run = await request(`/api/runs/${encodeURIComponent(runId)}/pause`, { method: "POST", body: "{}" });
  upsertRun(run);
  if (state.activeView === "conversation") renderRun(run);
  else if (state.activeView === "mode") renderModeRun(run);
  renderComposerSendButtons();
  toast(run.pause_requested ? "暂停请求已发送" : run.status === "paused" ? "任务已暂停" : "任务正在暂停");
}

async function respondAgentInteraction(element) {
  const runId = String(element.dataset.runId || state.activeStreamRunId || "");
  const interactionId = String(element.dataset.interactionId || "");
  const kind = String(element.dataset.interactionKind || "ask_user");
  let response;
  if (element.dataset.freeform === "true") {
    const value = String(element.closest(".agent-interaction-card")?.querySelector("[data-interaction-input]")?.value || "").trim();
    if (!value) throw new Error("请输入回答后再继续");
    response = { value, text: value };
  } else {
    const selected = String(element.dataset.responseId || "");
    response = kind === "plan"
      ? { decision: selected, action: selected }
      : { selected: [selected], value: selected };
  }
  const result = await request("/api/chat/interactions/respond", {
    method: "POST",
    body: JSON.stringify({ run_id: runId, interaction_id: interactionId, response }),
  });
  if (!result.ok) throw new Error(result.error || "这个选择点已经失效");
  state.directMessages.forEach((message) => {
    if (message.interaction?.interaction_id === interactionId) message.interaction.resolved = true;
  });
  renderDirectConversation();
  toast(kind === "plan" ? "计划决定已提交，任务继续执行" : "回答已提交，任务继续执行");
}

async function respondRunInteraction(element) {
  const runId = String(element.dataset.runId || "");
  const interactionId = String(element.dataset.interactionId || "");
  const decision = String(element.dataset.decision || "approve");
  const run = await request(`/api/runs/${encodeURIComponent(runId)}/interaction`, {
    method: "POST",
    body: JSON.stringify({ interaction_id: interactionId, response: { decision, action: decision } }),
  });
  upsertRun(run);
  renderRun(run);
  if (decision !== "cancel") watchRun(run.run_id, (next) => {
    if (state.activeTaskId === next.run_id) renderRun(next);
  });
}

async function recoverRun(element) {
  const runId = String(element.dataset.runId || "");
  const action = String(element.dataset.recoveryAction || "retry");
  const run = await request(`/api/runs/${encodeURIComponent(runId)}/recover`, {
    method: "POST",
    body: JSON.stringify({ action }),
  });
  upsertRun(run);
  state.activeTaskId = run.run_id;
  renderRun(run);
  watchRun(run.run_id, (next) => {
    if (state.activeTaskId === next.run_id) renderRun(next);
  });
}

async function advisorAction(element) {
  const runId = String(element.dataset.runId || "");
  const action = String(element.dataset.advisorAction || "");
  const run = await request(`/api/runs/${encodeURIComponent(runId)}/advisor-action`, {
    method: "POST",
    body: JSON.stringify({ action }),
  });
  upsertRun(run);
  state.activeTaskId = run.run_id;
  window.localStorage.setItem("scansci.active.task", run.run_id);
  renderRun(run);
  watchRun(run.run_id, (next) => {
    if (state.activeTaskId === next.run_id) renderRun(next);
  });
}

async function branchRun(element) {
  const runId = String(element.dataset.runId || "");
  const run = await request(`/api/runs/${encodeURIComponent(runId)}/branch`, {
    method: "POST",
    body: JSON.stringify({ background: true, execute: true }),
  });
  upsertRun(run);
  state.activeTaskId = run.run_id;
  window.localStorage.setItem("scansci.active.task", run.run_id);
  renderRun(run);
  watchRun(run.run_id, (next) => {
    if (state.activeTaskId === next.run_id) renderRun(next);
  });
  toast("已建立独立分支，原任务保持不变");
}

async function resumeRun(runId) {
  const run = await request(`/api/runs/${encodeURIComponent(runId)}/resume`, { method: "POST", body: "{}" });
  upsertRun(run);
  if (state.activeView === "conversation") renderRun(run);
  else if (state.activeView === "mode") renderModeRun(run);
  watchRun(runId, (next) => {
    if (state.activeTaskId !== next.run_id) return;
    if (state.activeView === "conversation") renderRun(next);
    else if (state.activeView === "mode") renderModeRun(next);
  });
  toast("已从保存的阶段继续");
}

async function compactSession() {
  if (state.streaming) { toast("对话进行中，无法压缩上下文"); return; }
  const sessionId = state.sessionId || state.activeTaskId;
  if (!sessionId) { toast("暂无活跃对话"); return; }
  toast("正在压缩上下文…");
  try {
    const result = await request("/api/chat/compact", { method: "POST", body: JSON.stringify({ session_id: sessionId }) });
    if (result.stats) updateSessionStats(result.stats);
    else if (result.tokens_after && state.sessionStats?.contextUsage) {
      const current = state.sessionStats.contextUsage;
      const contextWindow = Number(current.contextWindow || modelContextWindow() || 0);
      updateSessionStats({
        ...state.sessionStats,
        contextUsage: {
          ...current,
          tokens: Number(result.tokens_after),
          contextWindow,
          percent: contextWindow ? Number(result.tokens_after) / contextWindow * 100 : 0,
        },
      });
    }
    if (result.ok && result.tokens_before) {
      toast(`上下文已压缩：${formatTokenCount(result.tokens_before)} → ${formatTokenCount(result.tokens_after || 0)} tokens`);
    } else {
      toast(result.error || "压缩完成");
    }
  } catch (error) {
    toast(`压缩失败：${error.message}`, true);
  }
}

byId("sourceFilter").addEventListener("input", (event) => {
  state.sourceQuery = event.target.value;
  renderSources();
});

byId("toast")?.addEventListener("click", () => {
  const target = byId("toast");
  if (!target?.classList.contains("is-error")) return;
  target.classList.remove("is-visible");
  window.clearTimeout(toast.timer);
});

byId("slideTemplateSearch").addEventListener("input", (event) => {
  state.slideTemplateQuery = event.target.value;
  renderSlideTemplateBrowser();
});

window.addEventListener("focus", () => {
  if (state.activeView === "mode" && state.activeMode === "library" && knowledgeSourceKind(state.notebook) === "translation") {
    refreshTranslationLibrary().catch(() => {});
  }
});

document.addEventListener("keydown", (event) => {
  if (!event.ctrlKey || event.altKey || event.metaKey) return;
  if (event.key.toLowerCase() === "n") {
    event.preventDefault();
    startTask();
  }
  if (event.key.toLowerCase() === "k") {
    event.preventDefault();
    setView("conversation");
    window.setTimeout(() => byId("sourceFilter").focus(), 0);
  }
});

document.addEventListener("contextmenu", (event) => {
  const directRow = event.target.closest?.(".task-row[data-conversation-id]");
  if (directRow) {
    event.preventDefault();
    toggleDirectMenu(directRow.dataset.conversationId || "");
    return;
  }
  const taskRow = event.target.closest?.(".task-row[data-task-id]");
  if (taskRow) {
    event.preventDefault();
    toggleTaskMenu(taskRow.dataset.taskId || "");
  }
});

// Site icons are optional decoration. A blocked, offline, or icon-less site
// must leave a stable globe affordance instead of a broken-image glyph.
document.addEventListener("error", (event) => {
  const image = event.target?.closest?.("img[data-site-icon]");
  if (!image) return;
  if (image.classList.contains("model-market-icon")) {
    image.remove();
    return;
  }
  image.closest(".site-link-icon")?.classList.add("is-fallback");
}, true);

installSidebarResizer();
installContextPanelResizer();
applySidebarState();
applyContextPanelWidth();
byId("answerArea")?.addEventListener("scroll", () => {
  const answerArea = byId("answerArea");
  if (!answerArea) return;
  const distanceFromBottom = answerArea.scrollHeight - answerArea.scrollTop - answerArea.clientHeight;
  state.conversationAutoFollow = distanceFromBottom < conversationFollowThreshold;
  updateConversationScrollAffordance();
  scheduleConversationTurnNavActive();
}, { passive: true });
const conversationTurnList = byId("conversationTurnList");
if (conversationTurnList) {
  const area = byId("answerArea");
  new MutationObserver(scheduleConversationTurnNav).observe(area, { childList: true, subtree: true, characterData: true });
  new ResizeObserver(scheduleConversationTurnNav).observe(area);
  const updateWave = (y) => {
    conversationTurnNavState.waveY = y;
    byId("conversationTurnNav")?.classList.add("is-hovering");
    if (conversationTurnNavState.waveFrame) return;
    conversationTurnNavState.waveFrame = window.requestAnimationFrame(() => {
      conversationTurnNavState.waveFrame = 0;
      const widths = [52, 40, 28, 20, 12];
      conversationTurnList.querySelectorAll(".conversation-turn-marker").forEach((marker, index) => {
        const position = Math.min(4, Math.abs(conversationTurnNavState.waveY - (index + 0.5) * 20) / 20);
        const lower = Math.floor(position);
        const width = widths[lower] + (widths[Math.min(4, lower + 1)] - widths[lower]) * (position - lower);
        marker.style.setProperty("--wave-width", `${width.toFixed(1)}px`);
      });
    });
  };
  conversationTurnList.addEventListener("pointermove", (event) => {
    const button = event.target.closest(".conversation-turn-marker");
    if (!button) return;
    const index = Number(button.dataset.turnIndex);
    if (index !== conversationTurnNavState.hovered) showConversationTurnPreview(index);
    updateWave(event.clientY - conversationTurnList.getBoundingClientRect().top + conversationTurnList.scrollTop);
  });
  conversationTurnList.addEventListener("pointerleave", () => {
    if (!conversationTurnList.contains(document.activeElement)) hideConversationTurnPreview();
  });
  conversationTurnList.addEventListener("focusin", (event) => {
    const button = event.target.closest(".conversation-turn-marker");
    if (button) {
      const index = Number(button.dataset.turnIndex);
      showConversationTurnPreview(index);
      updateWave((index + 0.5) * 20);
    }
  });
  conversationTurnList.addEventListener("focusout", (event) => {
    if (!conversationTurnList.contains(event.relatedTarget)) hideConversationTurnPreview();
  });
  conversationTurnList.addEventListener("scroll", () => {
    if (conversationTurnNavState.hovered >= 0) showConversationTurnPreview(conversationTurnNavState.hovered);
  }, { passive: true });
}
byId("taskList")?.addEventListener("scroll", () => {
  if (state.historyMenuRunId) positionTaskMenu();
});
window.addEventListener("resize", () => {
  scheduleConversationTurnNav();
  if (state.historyMenuRunId) positionTaskMenu();
  document.querySelectorAll("[data-composer-model].is-open").forEach(fitComposerModelPicker);
});
renderProfileAvatar();
updateChromeControls();
observeIcons();
setComposerMode(byId("homeModeSelect")?.value || "general");
setWebSearchMode(state.webSearchMode, { announce: false });
initialize();
renderAppUpdate();
window.addEventListener("pywebviewready", () => {
  state.updateNative = Boolean(window.pywebview?.api?.install_update);
  const browserButton = document.querySelector('[data-action="toggle-embedded-browser"]');
  if (browserButton) browserButton.hidden = typeof window.pywebview?.api?.toggle_browser !== "function";
  syncEmbeddedBrowserTheme();
  renderAppUpdate();
});
window.addEventListener("scansci-browser-state", (event) => updateEmbeddedBrowserButton(event.detail?.open));
