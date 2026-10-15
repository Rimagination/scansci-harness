"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const app = readFileSync(path.resolve(__dirname, "../../src/scansci_html/web/app.js"), "utf8");
const start = app.indexOf("async function continueTaskConversation(");
const end = app.indexOf("\nasync function watchRun(", start);
assert.ok(start >= 0 && end > start);
const source = `${app.slice(start, end)}\ncontinueTaskConversation`;

test("lost run follow-up response reuses the persisted request body", async () => {
  const saved = new Map();
  const submitted = [];
  let nextId = 0;
  let fail = true;
  const make = () => vm.runInNewContext(source, {
    window: { localStorage: {
      getItem: (key) => saved.get(key) || null,
      setItem: (key, value) => saved.set(key, value),
      removeItem: (key) => saved.delete(key),
    } },
    crypto: { randomUUID: () => `uuid-${++nextId}` },
    extractSkillMentions: () => [],
    selectedComposerImageModel: () => ({ provider_id: "image", model_id: "fixture" }),
    currentThinkingLevel: () => "medium",
    composerMode: () => "general",
    state: { agentMode: true, webSearchMode: "off" },
    request: async (_path, options) => {
      submitted.push(JSON.parse(options.body));
      if (fail) throw new Error("response lost");
      return { run: { run_id: "run-1" } };
    },
  });

  await assert.rejects(make()("run-1", "draw a bird"), /response lost/);
  assert.equal(saved.size, 1);
  fail = false;
  await make()("run-1", "draw a bird");
  assert.deepEqual(submitted[0], submitted[1]);
  assert.equal(saved.size, 0);
});

test("uncertain follow-up needs explicit confirmation before a fresh key", async () => {
  const saved = new Map([[
    "scansci.task.followup.run-1",
    JSON.stringify({ content: "draw a bird", payload: {
      content: "draw a bird", idempotency_key: "old-key", image_model: { model_id: "fixture" },
      image_request_id: "old-image-key",
    } }),
  ]]);
  const submitted = [];
  let confirmations = 0;
  let nextId = 0;
  const retry = vm.runInNewContext(source, {
    window: { localStorage: {
      getItem: (key) => saved.get(key) || null,
      setItem: (key, value) => saved.set(key, value),
      removeItem: (key) => saved.delete(key),
    } },
    crypto: { randomUUID: () => `new-id-${++nextId}` },
    extractSkillMentions: () => [],
    selectedComposerImageModel: () => null,
    currentThinkingLevel: () => "medium",
    composerMode: () => "general",
    state: { agentMode: true, webSearchMode: "off" },
    requestConfirmation: async () => { confirmations += 1; return true; },
    request: async (_path, options) => {
      const payload = JSON.parse(options.body);
      submitted.push(payload);
      if (payload.idempotency_key === "old-key") {
        const failure = new Error("outcome uncertain");
        failure.payload = { error: { code: "submission_uncertain" } };
        throw failure;
      }
      return { run: { run_id: "run-1" } };
    },
  });

  await retry("run-1", "draw a bird");
  assert.equal(confirmations, 1);
  assert.equal(submitted[0].idempotency_key, "old-key");
  assert.notEqual(submitted[1].idempotency_key, "old-key");
  assert.notEqual(submitted[1].image_request_id, "old-image-key");
  assert.equal(saved.size, 0);
});
