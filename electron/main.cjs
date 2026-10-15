"use strict";

const { app, BaseWindow, BrowserWindow, WebContentsView, ipcMain, dialog, shell, session, protocol, net } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
const { browserUrl, isAppUrl } = require("./urls.cjs");

const READY_PREFIX = "SCANSCI_BACKEND_READY ";
const APP_ORIGIN = "scansci://app";
const root = path.resolve(__dirname, "..");
let backend;
let backendOrigin = "";
let window;
let appView;
let browserView;
let toolbarView;
let browserOpen = false;
let browserExpanded = false;
let browserTheme = null;
let closing = false;
let guestAttached = false;
let storageRoot = "";

function dataRoot() {
  return storageRoot || (process.env.SCANSCI_ELECTRON_DATA_DIR
    ? path.resolve(process.env.SCANSCI_ELECTRON_DATA_DIR)
    : path.join(process.env.LOCALAPPDATA || app.getPath("userData"), "ScanSciPi"));
}

function startBackend() {
  const packaged = app.isPackaged;
  const executable = packaged
    ? path.join(process.resourcesPath, "backend", "ScanSciBackend.exe")
    : process.env.SCANSCI_PYTHON || "python";
  const args = packaged ? [] : ["-m", "scansci_html.electron_backend"];
  const storage = dataRoot();
  fs.mkdirSync(storage, { recursive: true });
  args.push("--workspace", path.join(storage, "workspace.sqlite"));
  args.push("--evidence-db", path.join(storage, "evidence.sqlite"));
  const env = packaged ? process.env : {
    ...process.env,
    PYTHONPATH: [path.join(root, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
  };
  const child = spawn(executable, args, {
    cwd: packaged ? path.dirname(executable) : root,
    env,
    windowsHide: true,
    stdio: ["pipe", "pipe", "pipe"],
  });
  backend = child;
  child.stderr.resume(); // Never reflect backend output, which may include private data, into the UI.
  return new Promise((resolve, reject) => {
    let settled = false;
    const lines = readline.createInterface({ input: child.stdout });
    const timeout = setTimeout(() => fail(new Error("Python 服务启动超时")), 60000);
    function fail(error) {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      lines.close();
      reject(error);
    }
    child.once("error", fail);
    child.once("exit", () => fail(new Error("Python 服务提前退出")));
    lines.on("line", (line) => {
      if (!line.startsWith(READY_PREFIX) || settled) return;
      try {
        const { port } = JSON.parse(line.slice(READY_PREFIX.length));
        if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("服务端口无效");
        settled = true;
        clearTimeout(timeout);
        lines.close();
        child.stdout.resume();
        resolve(`http://127.0.0.1:${port}`);
      } catch (error) {
        fail(error);
      }
    });
  });
}

function signalBrowserState() {
  if (appView && !appView.webContents.isDestroyed()) {
    appView.webContents.send("scansci:browser-state", { open: browserOpen });
  }
  if (toolbarView && !toolbarView.webContents.isDestroyed()) {
    const history = browserView.webContents.navigationHistory;
    toolbarView.webContents.send("scansci:browser-toolbar-state", {
      url: browserView.webContents.getURL(),
      canGoBack: history.canGoBack(),
      canGoForward: history.canGoForward(),
      expanded: browserExpanded,
    });
  }
}

async function handleAppRequest(request) {
  const url = new URL(request.url);
  if (!isAppUrl(url.href, APP_ORIGIN)
    || (request.initiatorOrigin && !isAppUrl(request.initiatorOrigin, APP_ORIGIN))) {
    return new Response("Forbidden", { status: 403 });
  }
  const target = `${backendOrigin}${url.pathname}${url.search}`;
  const headers = request.method === "GET" || request.method === "HEAD"
    ? new Headers() : new Headers(request.headers);
  if (request.method === "GET") {
    for (const name of ["range", "if-range"]) {
      if (request.headers.has(name)) headers.set(name, request.headers.get(name));
    }
  }
  headers.delete("host");
  headers.delete("content-length");
  if (request.method !== "GET" && request.method !== "HEAD") headers.set("origin", APP_ORIGIN);
  const options = { method: request.method, headers };
  try {
    if (request.method !== "GET" && request.method !== "HEAD" && request.body) {
      // ponytail: Buffer POST bodies for reliable Electron protocol forwarding; the backend caps them at 170 MiB.
      options.body = Buffer.from(await request.arrayBuffer());
    }
    const forward = request.method === "GET" || request.method === "HEAD" ? net.fetch : fetch;
    return await forward(target, options);
  } catch {
    return new Response("Local service unavailable", { status: 502 });
  }
}

function setAppBounds(bounds) {
  // Force a real width change before the final rect; Windows can retain the
  // previous renderer viewport when a hidden split view is shown again.
  appView.setBounds({ ...bounds, width: Math.max(1, bounds.width - 1) });
  appView.setBounds(bounds);
}

function layoutViews() {
  if (!window || !appView) return;
  const [width, height] = window.getContentSize();
  if (!browserOpen) {
    if (guestAttached) for (const view of [browserView, toolbarView]) view.setVisible(false);
    setAppBounds({ x: 0, y: 0, width, height });
    return;
  }
  const left = browserExpanded || width < 1100 ? 0 : Math.round(width * 0.56);
  setAppBounds({ x: 0, y: 0, width: left, height });
  if (!guestAttached) {
    window.contentView.addChildView(browserView);
    window.contentView.addChildView(toolbarView);
    guestAttached = true;
  }
  browserView.setVisible(true);
  toolbarView.setVisible(true);
  toolbarView.setBounds({ x: left, y: 0, width: width - left, height: 48 });
  browserView.setBounds({ x: left, y: 48, width: width - left, height: Math.max(0, height - 48) });
}

function showBrowser(address = "", toggle = false) {
  if (toggle && browserOpen) {
    browserOpen = false;
    browserExpanded = false;
    layoutViews();
    signalBrowserState();
    return { ok: true, open: false };
  }
  let url = "";
  if (address) url = browserUrl(address);
  browserOpen = true;
  layoutViews();
  if (url) browserView.webContents.loadURL(url).catch(() => {});
  else toolbarView.webContents.send("scansci:browser-toolbar-state", {
    url: browserView.webContents.getURL(), focusAddress: true,
    canGoBack: browserView.webContents.navigationHistory.canGoBack(),
    canGoForward: browserView.webContents.navigationHistory.canGoForward(),
    expanded: browserExpanded,
  });
  signalBrowserState();
  return { ok: true, open: true };
}

function existingPath(input) {
  if (typeof input !== "string" || !input.trim()) throw new Error("缺少本地文件路径");
  const target = fs.realpathSync(input);
  if (target === path.parse(target).root) throw new Error("不能直接打开磁盘根目录");
  return target;
}

async function chooseFiles(name, extensions, multiple = true) {
  const result = await dialog.showOpenDialog(window, {
    properties: ["openFile", ...(multiple ? ["multiSelections"] : [])],
    filters: [{ name, extensions }, { name: "所有文件", extensions: ["*"] }],
  });
  return result.canceled ? [] : result.filePaths;
}

async function savePresentation(sourcePath, suggestedName) {
  try {
    const source = existingPath(sourcePath);
    const presentations = path.join(path.dirname(path.join(dataRoot(), "workspace.sqlite")), "presentations");
    if (path.extname(source).toLowerCase() !== ".pptx" || path.dirname(source) !== fs.realpathSync(presentations)) {
      throw new Error("PPTX 文件不在当前工作区");
    }
    const base = path.basename(String(suggestedName || path.basename(source)));
    const filename = base.toLowerCase().endsWith(".pptx") ? base : `${base}.pptx`;
    const result = await dialog.showSaveDialog(window, {
      defaultPath: filename,
      filters: [{ name: "PowerPoint 演示文稿", extensions: ["pptx"] }],
    });
    if (result.canceled || !result.filePath) return { ok: false, cancelled: true };
    const target = path.extname(result.filePath).toLowerCase() === ".pptx"
      ? result.filePath : `${result.filePath}.pptx`;
    if (path.resolve(target) !== source) fs.copyFileSync(source, target);
    return { ok: true, cancelled: false, path: target };
  } catch (error) {
    return { ok: false, cancelled: false, message: String(error.message || error) };
  }
}

async function nativeAction(name, args) {
  switch (name) {
    case "minimize_window": window.minimize(); return { ok: true };
    case "toggle_maximize_window":
      if (window.isMaximized()) window.unmaximize(); else window.maximize();
      return { ok: true, maximized: window.isMaximized() };
    case "close_window": window.close(); return { ok: true };
    case "choose_library_folder": {
      const result = await dialog.showOpenDialog(window, { properties: ["openDirectory"] });
      return result.canceled ? "" : result.filePaths[0];
    }
    case "choose_library_files": return chooseFiles("研究资料", ["pdf", "docx", "pptx", "xlsx", "xls", "csv", "json", "xml", "html", "htm", "md", "markdown", "txt", "rtf", "epub", "zip", "png", "jpg", "jpeg", "webp", "tif", "tiff"]);
    case "choose_local_runtime_files": return chooseFiles("ScanSci 本地运行组件", ["zip", "json", "part", "part1", "part2"]);
    case "choose_runtime_component_files": return chooseFiles(args[0] === "node" ? "Agent 运行组件" : "LaTeX 排版组件", ["zip", "json", "part", "part1", "part2"]);
    case "choose_presentation_sources": return chooseFiles("演示材料", ["pdf", "docx", "pptx", "xlsx", "xls", "csv", "json", "xml", "html", "htm", "md", "markdown", "txt", "rtf", "epub", "png", "jpg", "jpeg", "webp", "tif", "tiff"]);
    case "open_local_path": {
      try {
        const target = existingPath(args[0]);
        const error = await shell.openPath(target);
        return error ? { ok: false, message: error } : { ok: true, path: target, kind: fs.statSync(target).isDirectory() ? "folder" : "file" };
      } catch (error) { return { ok: false, message: String(error.message || error) }; }
    }
    case "reveal_local_path": {
      try {
        const target = existingPath(args[0]);
        if (fs.statSync(target).isDirectory()) await shell.openPath(target);
        else shell.showItemInFolder(target);
        return { ok: true, path: target };
      } catch (error) { return { ok: false, message: String(error.message || error) }; }
    }
    case "save_presentation_copy": return savePresentation(args[0], args[1]);
    case "open_browser": return showBrowser(String(args[0] || ""));
    case "toggle_browser": return showBrowser("", true);
    case "set_browser_theme": {
      const palette = args[0];
      const keys = ["canvas", "surface", "ink", "muted", "rule", "control", "accent"];
      if (!palette || keys.some((key) => !/^#[\da-f]{6}$/i.test(palette[key]))) return { ok: false };
      browserTheme = Object.fromEntries(keys.map((key) => [key, palette[key]]));
      toolbarView.webContents.send("scansci:browser-toolbar-theme", browserTheme);
      return { ok: true };
    }
    default: throw new Error("未知桌面操作");
  }
}

function makeWindow() {
  const appSession = session.fromPartition("persist:scansci-app");
  appSession.protocol.handle("scansci", handleAppRequest);
  const icon = app.isPackaged
    ? path.join(process.resourcesPath, "assets", "scansci.ico")
    : path.join(root, "src", "scansci_html", "web", "scansci.ico");
  window = new BaseWindow({
    title: "ScanSci | 搜索科学", width: 1440, height: 960,
    minWidth: 800, minHeight: 560, frame: false, show: false, icon,
    backgroundColor: "#f4f4f6",
  });
  appView = new WebContentsView({ webPreferences: {
    preload: path.join(__dirname, "preload.cjs"),
    partition: "persist:scansci-app", contextIsolation: true,
    nodeIntegration: false, sandbox: true, webSecurity: true,
  } });
  browserView = new WebContentsView({ webPreferences: {
    partition: "persist:scansci-browser", contextIsolation: true,
    nodeIntegration: false, sandbox: true, webSecurity: true,
  } });
  toolbarView = new WebContentsView({ webPreferences: {
    preload: path.join(__dirname, "browser-toolbar-preload.cjs"),
    contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true,
  } });
  window.contentView.addChildView(appView);
  layoutViews();
  window.on("resize", layoutViews);
  window.on("closed", () => {
    for (const view of [appView, browserView, toolbarView]) view.webContents.close();
    window = null;
  });

  appSession.setPermissionRequestHandler((contents, permission, callback, details) => {
    const audio = permission === "media" && details.mediaTypes?.includes("audio") && !details.mediaTypes?.includes("video");
    const clipboardWrite = permission === "clipboard-sanitized-write";
    callback(contents === appView.webContents && (audio || clipboardWrite) && isAppUrl(details.requestingUrl, APP_ORIGIN));
  });
  appSession.setPermissionCheckHandler((contents, permission, requestingOrigin) =>
    contents === appView.webContents && ["media", "audioCapture", "clipboard-sanitized-write"].includes(permission)
      && isAppUrl(requestingOrigin, APP_ORIGIN));
  const guestSession = session.fromPartition("persist:scansci-browser");
  guestSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  guestSession.setPermissionCheckHandler(() => false);
  guestSession.on("will-download", (event) => event.preventDefault());
  guestSession.webRequest.onBeforeRequest({ urls: ["http://*/*", "https://*/*"] }, (details, callback) => {
    try { browserUrl(details.url); callback({}); }
    catch { callback({ cancel: true }); }
  });

  appView.webContents.on("will-navigate", (event, url) => {
    if (!isAppUrl(url, APP_ORIGIN)) event.preventDefault();
  });
  appView.webContents.setWindowOpenHandler(({ url }) => {
    if (isAppUrl(url, APP_ORIGIN)) {
      const viewer = new BrowserWindow({
        title: "ScanSci 文档", width: 980, height: 760,
        webPreferences: { partition: "persist:scansci-app", contextIsolation: true, nodeIntegration: false, sandbox: true },
      });
      viewer.webContents.on("will-navigate", (event, destination) => {
        if (!isAppUrl(destination, APP_ORIGIN)) event.preventDefault();
      });
      viewer.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
      viewer.loadURL(url);
    } else {
      try { showBrowser(url); } catch { /* Unsupported destination stays closed. */ }
    }
    return { action: "deny" };
  });
  browserView.webContents.on("will-navigate", (event, url) => {
    try { browserUrl(url); } catch { event.preventDefault(); }
  });
  browserView.webContents.on("will-redirect", (event, url) => {
    try { browserUrl(url); } catch { event.preventDefault(); }
  });
  browserView.webContents.setWindowOpenHandler(({ url }) => {
    try { showBrowser(url); } catch { /* Block local and non-web popups. */ }
    return { action: "deny" };
  });
  for (const event of ["did-navigate", "did-navigate-in-page", "did-stop-loading"]) {
    browserView.webContents.on(event, signalBrowserState);
  }
  toolbarView.webContents.on("will-navigate", (event) => event.preventDefault());
  toolbarView.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  toolbarView.webContents.on("did-finish-load", () => {
    signalBrowserState();
    if (browserTheme) toolbarView.webContents.send("scansci:browser-toolbar-theme", browserTheme);
  });
  appView.webContents.on("did-finish-load", () => { layoutViews(); signalBrowserState(); window?.show(); });
  toolbarView.webContents.loadFile(path.join(__dirname, "browser-toolbar.html"));
  appView.webContents.loadURL(`${APP_ORIGIN}/`);
}

ipcMain.handle("scansci:desktop", (event, name, args) => {
  if (!appView || event.sender !== appView.webContents || event.senderFrame !== appView.webContents.mainFrame
    || !isAppUrl(event.senderFrame.url, APP_ORIGIN) || !Array.isArray(args)) {
    throw new Error("桌面操作来源无效");
  }
  return nativeAction(name, args);
});

ipcMain.on("scansci:browser-toolbar", (event, name, value) => {
  if (!toolbarView || event.sender !== toolbarView.webContents || event.senderFrame !== toolbarView.webContents.mainFrame) return;
  try {
    switch (name) {
      case "back": if (browserView.webContents.navigationHistory.canGoBack()) browserView.webContents.navigationHistory.goBack(); break;
      case "forward": if (browserView.webContents.navigationHistory.canGoForward()) browserView.webContents.navigationHistory.goForward(); break;
      case "reload": browserView.webContents.reload(); break;
      case "go": showBrowser(String(value || "")); break;
      case "expand": browserExpanded = !browserExpanded; layoutViews(); signalBrowserState(); break;
      case "close": showBrowser("", true); break;
      default: break;
    }
  } catch (error) {
    dialog.showErrorBox("无法打开网页", String(error.message || error));
  }
});

protocol.registerSchemesAsPrivileged([{ scheme: "scansci", privileges: {
  standard: true, secure: true, supportFetchAPI: true, stream: true,
} }]);
storageRoot = dataRoot();
const userData = path.join(storageRoot, "electron");
fs.mkdirSync(userData, { recursive: true });
app.setPath("userData", userData);
const singleInstance = app.requestSingleInstanceLock();
if (!singleInstance) app.quit();
else {
  app.on("second-instance", () => { if (window) { window.show(); window.focus(); } });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", () => {
    closing = true;
    if (backend && !backend.killed) {
      backend.stdin.end();
      const timer = setTimeout(() => { if (!backend.killed) backend.kill(); }, 3000);
      timer.unref();
    }
  });
  app.whenReady().then(async () => {
    try {
      backendOrigin = await startBackend();
      backend.on("exit", () => {
        if (!closing && window) { dialog.showErrorBox("ScanSci 服务中断", "本地科研服务已停止，请重新启动 ScanSci。"); app.quit(); }
      });
      makeWindow();
    } catch (error) {
      dialog.showErrorBox("ScanSci 启动失败", String(error.message || error));
      app.quit();
    }
  });
}
