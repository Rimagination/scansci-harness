"use strict";

const { contextBridge, ipcRenderer } = require("electron");

if (process.isMainFrame) {
  const methods = [
    "minimize_window", "toggle_maximize_window", "close_window",
    "choose_library_folder", "choose_library_files", "choose_local_runtime_files",
    "choose_runtime_component_files", "choose_presentation_sources",
    "open_local_path", "reveal_local_path", "save_presentation_copy",
    "open_browser", "toggle_browser", "set_browser_theme",
  ];
  const api = Object.fromEntries(methods.map((name) => [
    name, (...args) => ipcRenderer.invoke("scansci:desktop", name, args),
  ]));
  contextBridge.exposeInMainWorld("pywebview", { api });
  ipcRenderer.on("scansci:browser-state", (_event, state) => {
    window.dispatchEvent(new CustomEvent("scansci-browser-state", { detail: state }));
  });
  window.addEventListener("DOMContentLoaded", () => {
    window.dispatchEvent(new Event("pywebviewready"));
  }, { once: true });
}
