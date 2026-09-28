import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const app = readFileSync(new URL("../src/scansci_html/web/app.js", import.meta.url), "utf8");
const render = app.slice(
  app.indexOf("function knowledgeIndexStatusMarkup("),
  app.indexOf("\nfunction syncKnowledgeIndexBadge("),
);
assert.ok(render.startsWith("function knowledgeIndexStatusMarkup("));

function badge({ sources = 2, status = {}, localAi = {}, binding = "", catalog = [] } = {}) {
  const context = vm.createContext({
    state: { knowledgeIndexStatuses: { research: status }, localAiStatuses: { research: localAi } },
    escapeHtml: (value) => String(value).replaceAll("&", "&amp;").replaceAll('"', "&quot;"),
  });
  vm.runInContext(render, context);
  return context.knowledgeIndexStatusMarkup({
    notebook_id: "research",
    counts: { sources },
    metadata: { local_binding: { index_state: binding }, zotero: { items: catalog } },
  });
}

for (const status of [{}, { state: "pending", total: 195 }, { state: "pending", automatic_build_deferred: true }, { state: "optional", total: 693 }]) {
  test(`base index is usable before semantic optimization: ${JSON.stringify(status)}`, () => {
    const html = badge({ status, binding: "ready" });
    assert.match(html, /基础检索可用 · 可优化语义索引/);
    assert.match(html, /点击可手动优化语义索引/);
    assert.match(html, /data-action="retry-evidence-index"/);
    assert.match(html, /is-ready/);
    assert.doesNotMatch(html, /等待同步|语义检索已就绪|disabled/);
  });
}

test("a ready model does not imply a ready semantic index", () => {
  assert.match(badge({ status: { state: "pending" }, localAi: { state: "ready" } }), /可优化语义索引/);
  assert.match(badge({ status: { state: "ready", total: 195 } }), /语义检索已就绪/);
});

test("semantic optimization progress keeps base retrieval visible", () => {
  const html = badge({ status: { state: "indexing", progress: 0.4 } });
  assert.match(html, /全文可检索 · 优化 40%/);
  assert.match(html, /data-action="refresh-evidence-index"/);
});

test("semantic failures preserve the base index and offer an explicit retry", () => {
  assert.match(badge({ status: { state: "failed", error: "model unavailable" } }), /基础检索可用 · 语义优化失败/);
  const html = badge({ status: { state: "pending" }, localAi: { state: "error" } });
  assert.match(html, /基础检索可用 · 语义优化待重试/);
  assert.match(html, /data-action="retry-local-ai"/);
});

test("catalog-only records and empty evidence never claim searchable text", () => {
  for (const options of [
    { sources: 0, catalog: [{ title: "Catalog entry without a PDF" }] },
    { sources: 2, status: { state: "empty", total: 0 } },
    { sources: 0, localAi: { state: "error" } },
  ]) {
    const html = badge(options);
    assert.match(html, /尚无内容/);
    assert.match(html, /disabled/);
    assert.doesNotMatch(html, /基础检索可用|全文可检索/);
  }
});

test("an import failure remains visible instead of suggesting semantic success", () => {
  assert.match(badge({ status: { state: "failed" }, binding: "failed" }), /同步失败/);
  assert.match(badge({ sources: 0, status: { state: "importing", progress: 0.2 } }), /正在建立索引 20%/);
});
