// Run against a locally started Electron app with --remote-debugging-port=9223.
// It only exercises ScanSci's own app target; no external site is visited.
const endpoint = process.argv.find((argument) => argument.startsWith("http://127.0.0.1:")) || "http://127.0.0.1:9223";
let appTarget;
for (let attempt = 0; attempt < 120; attempt += 1) {
  const targets = await (await fetch(`${endpoint}/json/list`)).json();
  appTarget = targets.find((target) => target.url === "scansci://app/");
  if (appTarget) break;
  await new Promise((resolve) => setTimeout(resolve, 100));
}
if (!appTarget) throw new Error("Electron ScanSci app target was not found");

const socket = new WebSocket(appTarget.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});
let nextId = 0;
async function command(method, params = {}, targetSocket = socket) {
  const id = ++nextId;
  const response = new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`CDP timeout: ${method}`)), 15000);
    const listener = (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== id) return;
      clearTimeout(timeout);
      targetSocket.removeEventListener("message", listener);
      resolve(message);
    };
    targetSocket.addEventListener("message", listener);
  });
  targetSocket.send(JSON.stringify({ id, method, params }));
  const message = await response;
  if (message.error || message.result?.exceptionDetails) throw new Error(JSON.stringify(message));
  return message;
}

async function evaluate(expression, targetSocket = socket) {
  const message = await command("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, targetSocket);
  return message.result?.result?.value;
}

async function waitWidth(predicate) {
  let lastWidth = 0;
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const width = await evaluate("innerWidth");
    lastWidth = width;
    if (predicate(width)) return width;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Electron app viewport did not resize; last width ${lastWidth}`);
}

try {
  let ready;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    ready = await evaluate("({title: document.title, origin: location.origin, bridge: !!window.pywebview?.api?.toggle_browser, button: document.querySelector('[data-action=toggle-embedded-browser]')?.hidden === false, open: document.querySelector('[data-action=toggle-embedded-browser]')?.getAttribute('aria-pressed') === 'true', width: innerWidth, control: getComputedStyle(document.documentElement).getPropertyValue('--control-background').trim()})");
    if (ready.bridge && ready.button) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (ready.open || ready.width < 800) {
    await evaluate("window.pywebview.api.toggle_browser()");
    ready = { ...ready, width: await waitWidth((width) => width >= 800) };
  }
  if (!ready.bridge || !ready.button || ready.width < 800 || ready.origin !== "scansci://app") throw new Error(`App bridge not ready: ${JSON.stringify(ready)}`);
  const icons = await evaluate(`(() => ({
    pending: document.querySelectorAll('[data-ui-icon]').length,
    back: [...document.querySelectorAll('.back-button')].every((button) => Boolean(button.querySelector('svg.ui-icon'))),
    refresh: Boolean(document.querySelector('.extensions-refresh svg.ui-icon')),
    agent: [...document.querySelectorAll('.composer-agent-button')].every((button) => Boolean(button.querySelector('svg.ui-icon'))),
  }))()`);
  if (icons.pending || !icons.back || !icons.refresh || !icons.agent) throw new Error(`Visible line icons did not hydrate: ${JSON.stringify(icons)}`);
  const postStatus = await evaluate("(async () => (await fetch('/api/electron-origin-probe', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'})).status)()");
  if (postStatus === 403 || postStatus === 502) throw new Error(`Electron app POST proxy failed: ${postStatus}`);
  const opened = await evaluate("window.pywebview.api.toggle_browser()");
  const expectedSplitWidth = Math.round(ready.width * .56);
  const splitWidth = await waitWidth((width) => width === expectedSplitWidth);
  if (!opened?.ok || !opened.open || splitWidth >= ready.width) throw new Error("Browser split view did not resize the app");
  const alignment = await evaluate(`(() => {
    const canvas = document.querySelector('.app-canvas');
    const titlebar = document.querySelector('.desktop-titlebar');
    return { open: document.documentElement.classList.contains('embedded-browser-open'),
      canvasTop: canvas.getBoundingClientRect().top, titlebarBottom: titlebar.getBoundingClientRect().bottom,
      marginTop: getComputedStyle(canvas).marginTop };
  })()`);
  if (!alignment.open || alignment.canvasTop !== alignment.titlebarBottom || alignment.marginTop !== "0px") {
    throw new Error(`Split content is not aligned: ${JSON.stringify(alignment)}`);
  }
  await command("Page.reload", { ignoreCache: true });
  let restoredSplit = false;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    restoredSplit = await evaluate(`(() => {
      const canvas = document.querySelector('.app-canvas');
      const titlebar = document.querySelector('.desktop-titlebar');
      return Boolean(window.pywebview?.api && document.documentElement?.classList.contains('embedded-browser-open')
        && canvas && titlebar && canvas.getBoundingClientRect().top === titlebar.getBoundingClientRect().bottom);
    })()`);
    if (restoredSplit) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!restoredSplit) throw new Error("Split alignment was not restored after app reload");
  const closed = await evaluate("window.pywebview.api.toggle_browser()");
  const restoredWidth = await waitWidth((width) => width === ready.width).catch(async (error) => {
    console.log("restore diagnostic", await evaluate("({innerWidth, outerWidth, clientWidth: document.documentElement.clientWidth, visualWidth: visualViewport.width, browserPressed: document.querySelector('[data-action=toggle-embedded-browser]')?.getAttribute('aria-pressed')})"));
    throw error;
  });
  if (!closed?.ok || closed.open || restoredWidth !== ready.width) throw new Error(`Closing browser did not restore the app: ${JSON.stringify({ closed, restoredWidth, originalWidth: ready.width })}`);
  if (await evaluate("document.documentElement.classList.contains('embedded-browser-open')")) {
    throw new Error("Browser layout class remained after closing the split view");
  }
  const reopened = await evaluate("window.pywebview.api.toggle_browser()");
  const repeatedSplitWidth = await waitWidth((width) => width === expectedSplitWidth);
  if (!reopened?.open || repeatedSplitWidth !== expectedSplitWidth) throw new Error("Browser split view did not resize on the second opening");
  const reclosed = await evaluate("window.pywebview.api.toggle_browser()");
  const repeatedRestoredWidth = await waitWidth((width) => width === ready.width);
  if (reclosed?.open || repeatedRestoredWidth !== ready.width) throw new Error("Browser split view did not restore on the second closing");
  await command("Emulation.setDeviceMetricsOverride", { width: 900, height: 560, deviceScaleFactor: 1, mobile: false });
  try {
    const menu = await evaluate(`(() => {
      const popover = document.querySelector('#homeModelMenu');
      const trigger = document.querySelector('[aria-controls=homeModelMenu]');
      trigger.click();
      const box = popover.getBoundingClientRect();
      const canvas = popover.closest('.app-canvas').getBoundingClientRect();
      const result = { top: box.top, bottom: box.bottom, canvasTop: canvas.top, canvasBottom: canvas.bottom, viewportHeight: innerHeight, scrollable: popover.scrollHeight > popover.clientHeight };
      return result;
    })()`);
    if (menu.viewportHeight !== 560 || menu.top < menu.canvasTop + 8 || menu.bottom > menu.canvasBottom - 8) {
      throw new Error(`Model menu overflows compact viewport: ${JSON.stringify(menu)}`);
    }
    const dark = await evaluate(`(() => {
      const root = document.documentElement;
      const previous = root.getAttribute('data-theme');
      root.setAttribute('data-theme', 'dark');
      const color = getComputedStyle(document.querySelector('#homeModelMenu')).backgroundColor;
      if (previous === null) root.removeAttribute('data-theme'); else root.setAttribute('data-theme', previous);
      return color;
    })()`);
    if (dark === "rgb(255, 255, 255)") throw new Error("Model menu dark theme was not applied");
    await command("Emulation.setDeviceMetricsOverride", { width: 900, height: 700, deviceScaleFactor: 1, mobile: false });
    const resizedMenu = await evaluate(`(() => {
      const box = document.querySelector('#homeModelMenu').getBoundingClientRect();
      const canvas = document.querySelector('#homeModelMenu').closest('.app-canvas').getBoundingClientRect();
      return { top: box.top, bottom: box.bottom, canvasTop: canvas.top, canvasBottom: canvas.bottom, viewportHeight: innerHeight };
    })()`);
    if (resizedMenu.viewportHeight !== 700 || resizedMenu.top < resizedMenu.canvasTop + 8 || resizedMenu.bottom > resizedMenu.canvasBottom - 8) {
      throw new Error(`Model menu overflows resized viewport: ${JSON.stringify(resizedMenu)}`);
    }
    await command("Emulation.setDeviceMetricsOverride", { width: 700, height: 560, deviceScaleFactor: 1, mobile: false });
    const narrowMenu = await evaluate(`(() => {
      const box = document.querySelector('#homeModelMenu').getBoundingClientRect();
      const canvas = document.querySelector('#homeModelMenu').closest('.app-canvas').getBoundingClientRect();
      return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, canvasLeft: canvas.left, canvasRight: canvas.right, canvasTop: canvas.top, canvasBottom: canvas.bottom, viewportWidth: innerWidth };
    })()`);
    if (narrowMenu.viewportWidth !== 700 || narrowMenu.left < narrowMenu.canvasLeft + 8 || narrowMenu.right > narrowMenu.canvasRight - 8
      || narrowMenu.top < narrowMenu.canvasTop + 8 || narrowMenu.bottom > narrowMenu.canvasBottom - 8) {
      throw new Error(`Model menu overflows narrow viewport: ${JSON.stringify(narrowMenu)}`);
    }
    await evaluate("document.querySelector('[aria-controls=homeModelMenu]').click()");
  } finally {
    await command("Emulation.clearDeviceMetricsOverride");
  }
  console.log(JSON.stringify({ ok: true, title: ready.title, origin: ready.origin, width: ready.width, splitWidth, postStatus, control: ready.control }));
  if (process.argv.includes("--preview")) {
    await evaluate("window.pywebview.api.open_browser('https://example.org/')");
    let guestTarget;
    for (let attempt = 0; attempt < 50; attempt += 1) {
      const targets = await (await fetch(`${endpoint}/json/list`)).json();
      guestTarget = targets.find((target) => target.url === "https://example.org/");
      if (guestTarget) break;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (!guestTarget) throw new Error("External browser did not navigate to example.org");
    const guestSocket = new WebSocket(guestTarget.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      guestSocket.addEventListener("open", resolve, { once: true });
      guestSocket.addEventListener("error", reject, { once: true });
    });
    try {
      const guestAccess = await evaluate("({bridge:typeof window.pywebview, node:typeof require})", guestSocket);
      if (guestAccess.bridge !== "undefined" || guestAccess.node !== "undefined") {
        throw new Error(`External browser gained an app bridge: ${JSON.stringify(guestAccess)}`);
      }
    } finally {
      guestSocket.close();
    }
    console.log("浏览器预览已打开：example.org");
  }
} finally {
  socket.close();
}
