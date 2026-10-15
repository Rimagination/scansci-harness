"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../..");
const app = readFileSync(path.join(root, "src/scansci_html/web/app.js"), "utf8");
const html = readFileSync(path.join(root, "src/scansci_html/web/index.html"), "utf8");
const toolbar = readFileSync(path.join(root, "electron/browser-toolbar.html"), "utf8");
const toolbarCss = readFileSync(path.join(root, "electron/browser-toolbar.css"), "utf8");

test("visible app icon references resolve to a line icon", () => {
  const start = app.indexOf("const uiIconPaths = {");
  const end = app.indexOf("\nfunction uiIcon(", start);
  assert.ok(start >= 0 && end > start);
  const icons = vm.runInNewContext(`${app.slice(start, end)}\nuiIconPaths`);
  const names = [
    ...[...app.matchAll(/uiIcon\("([^"]+)"/g)].map((match) => match[1]),
    ...[...`${html}\n${app}`.matchAll(/data-ui-icon="([^"]+)"/g)].map((match) => match[1]),
  ];
  assert.deepEqual([...new Set(names.filter((name) => !icons[name]))], []);
  assert.notEqual(icons.sparkles, icons.info);
});

test("browser toolbar uses stroke icons for each control", () => {
  for (const name of ["back", "forward", "reload", "expand", "close"]) {
    assert.match(toolbar, new RegExp(`<button id="${name}"[^>]*><svg\\b`));
  }
});

test("browser toolbar follows the shared control radius and soft focus", () => {
  assert.match(toolbarCss, /--radius-sm: 8px/);
  assert.match(toolbarCss, /border-radius: var\(--radius-sm\)/);
  assert.doesNotMatch(toolbarCss, /border-radius: 9px|outline: 2px/);
  assert.match(toolbarCss, /outline: 1px solid var\(--accent\)/);
  assert.match(toolbarCss, /box-shadow: var\(--focus-glow\)/);
});

test("source navigation and extension refresh use icon placeholders", () => {
  assert.match(html, /class="source-arrow"><span data-ui-icon="chevron-right"/);
  assert.match(html, /class="extensions-refresh"[^>]*><span data-ui-icon="refresh"/);
  assert.doesNotMatch(html, />[←×↻›]</);
  assert.match(app, /refreshButton\.querySelector\("\.extensions-refresh-label"\)\.textContent/);
});
