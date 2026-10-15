"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("scansciBrowserToolbar", {
  action: (name, value = "") => ipcRenderer.send("scansci:browser-toolbar", name, value),
  onState: (callback) => ipcRenderer.on("scansci:browser-toolbar-state", (_event, state) => callback(state)),
  onTheme: (callback) => ipcRenderer.on("scansci:browser-toolbar-theme", (_event, palette) => callback(palette)),
});
