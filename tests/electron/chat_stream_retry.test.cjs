"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const app = readFileSync(path.resolve(__dirname, "../../src/scansci_html/web/app.js"), "utf8");
const start = app.indexOf("function streamChatWithRecovery(");
const end = app.indexOf("\nfunction scheduleDirectConversationRender", start);
assert.ok(start >= 0 && end > start);
const recoverySource = `${app.slice(start, end)}\nstreamChatWithRecovery`;

function harness(attempts) {
  let requests = 0;
  let retries = 0;
  const events = [];
  const recover = vm.runInNewContext(recoverySource, {
    streamChat: async (_payload, onEvent) => {
      const attempt = attempts[requests++];
      assert.ok(attempt, "unexpected replay request");
      for (const [type, value] of attempt.events) onEvent(type, value);
      if (attempt.error) throw attempt.error;
    },
  });
  return {
    run: () => recover({}, (type, value) => events.push([type, value]), { onRetry: () => { retries += 1; } }),
    counts: () => ({ requests, retries }),
    events,
  };
}

function retryableError() {
  const error = new Error("stream interrupted");
  error.code = "chat_failed";
  error.failure = { retryable: true };
  return error;
}

test("a transport failure after any server event never replays the request", async () => {
  for (const progress of [
    ["ready", {}],
    ["RUN_STARTED", {}],
    ["STEP_STARTED", { stepName: "ingest_attachments" }],
    ["tool.completed", { name: "create_document" }],
    ["CUSTOM", { name: "process_trace", value: [] }],
    ["TEXT_MESSAGE_CONTENT", { delta: "partial answer" }],
  ]) {
    const subject = harness([{ events: [progress], error: retryableError() }]);
    await assert.rejects(subject.run(), /stream interrupted/);
    assert.deepEqual(subject.counts(), { requests: 1, retries: 0 });
    assert.deepEqual(subject.events.at(-1), progress);
  }
});

test("a transport failure before the first event never replays the request", async () => {
  const subject = harness([{ events: [], error: retryableError() }]);
  await assert.rejects(subject.run(), /stream interrupted/);
  assert.deepEqual(subject.counts(), { requests: 1, retries: 0 });
});

test("abort never retries even before a server event", async () => {
  const error = new Error("cancelled");
  error.name = "AbortError";
  const subject = harness([{ events: [], error }]);
  await assert.rejects(subject.run(), /cancelled/);
  assert.deepEqual(subject.counts(), { requests: 1, retries: 0 });
});
