"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const app = readFileSync(path.resolve(__dirname, "../../src/scansci_html/web/app.js"), "utf8");
const start = app.indexOf("async function resumeDirectChatJob(");
const end = app.indexOf("\n// Keep the legacy action", start);
assert.ok(start >= 0 && end > start);
const source = `${app.slice(start, end)}\nresumeDirectChatJob`;
const retryStart = app.indexOf("async function retryDirectMessage(");
const retryEnd = app.indexOf("\nfunction renderDirectConversation(", retryStart);
assert.ok(retryStart >= 0 && retryEnd > retryStart);
const retrySource = `${app.slice(retryStart, retryEnd)}\nretryDirectMessage`;

function harness(submissionState, terminal = null, persistenceFails = false, fenceState = "abandoned") {
  const calls = { runs: [], confirmations: 0, fences: 0, saved: 0, notices: [] };
  const originalUser = { role: "user", message_id: "old-key", content: "create a file" };
  const partial = { role: "assistant", message_id: "assistant-old-key", parent_message_id: "old-key", content: "partial", paused: true };
  const job = {
    conversationId: "conversation-1", status: "paused", resumePending: false,
    pausedTurn: { queueId: "old-key", question: "create a file" }, pausedMessage: partial,
    messages: [{ role: "user", message_id: "earlier", content: "earlier" }, originalUser, partial],
    queue: [], sessionId: "old-session", pauseRequested: false,
  };
  let counter = 0;
  const resume = vm.runInNewContext(source, {
    state: { directConversationId: "conversation-1" },
    crypto: { randomUUID: () => `new-id-${++counter}` },
    directChatJob: () => job,
    directChatJobs: new Map([[job.conversationId, job]]),
    request: async (_path, options) => {
      if (options?.method === "POST") {
        calls.fences += 1;
        return { state: fenceState };
      }
      return { state: submissionState, terminal };
    },
    requestConfirmation: async () => { calls.confirmations += 1; return true; },
    persistDirectChatJob: async () => {
      calls.saved += 1;
      if (persistenceFails) throw new Error("history unavailable");
    },
    runDirectChatTurn: (_job, turn) => { calls.runs.push(turn); },
    syncActiveDirectChatState: () => {},
    scheduleDirectConversationRender: () => {},
    renderTasks: () => {},
    toast: (message) => { calls.notices.push(message); },
    Date,
  });
  return { resume, job, calls };
}

test("paused request still running cannot start another execution", async () => {
  const { resume, job, calls } = harness("running");
  await resume();
  assert.equal(job.status, "paused");
  assert.equal(job.pausedTurn.queueId, "old-key");
  assert.equal(calls.runs.length, 0);
  assert.equal(calls.confirmations, 0);
});

test("unrecorded paused request needs confirmation before a new key", async () => {
  const { resume, job, calls } = harness("missing");
  await resume();
  assert.equal(calls.confirmations, 1);
  assert.equal(calls.fences, 1);
  assert.equal(calls.runs.length, 1);
  assert.notEqual(calls.runs[0].queueId, "old-key");
  assert.deepEqual(job.messages.map((message) => message.message_id), ["earlier"]);
});

test("a late original admission blocks the replacement branch", async () => {
  const { resume, calls } = harness("missing", null, false, "running");
  await resume();
  assert.equal(calls.fences, 1);
  assert.equal(calls.runs.length, 0);
});

test("uncertain pause needs confirmation and starts a distinct saved branch", async () => {
  const { resume, job, calls } = harness("uncertain");
  await resume();
  assert.equal(calls.confirmations, 1);
  assert.equal(calls.runs.length, 1);
  assert.notEqual(calls.runs[0].queueId, "old-key");
  assert.equal(job.branchFromMessageId, "old-key");
  assert.equal(job.branchReason, "resume_paused_turn");
  assert.deepEqual(job.messages.map((message) => message.message_id), ["earlier"]);
});

test("completed paused request restores its terminal without running again", async () => {
  const { resume, job, calls } = harness("completed", {
    type: "RUN_FINISHED", result: { message: { role: "assistant", content: "finished" } },
  });
  await resume();
  assert.equal(calls.confirmations, 0);
  assert.equal(calls.runs.length, 0);
  assert.equal(calls.saved, 1);
  assert.equal(job.messages.at(-1).content, "finished");
  assert.equal(job.messages.at(-1).paused, false);
});

test("failed terminal history save keeps the paused turn recoverable", async () => {
  const { resume, job, calls } = harness("completed", {
    type: "RUN_FINISHED", result: { message: { role: "assistant", content: "finished" } },
  }, true);
  await resume();
  assert.equal(job.status, "paused");
  assert.equal(job.pausedTurn.queueId, "old-key");
  assert.equal(job.pausedMessage.content, "partial");
  assert.equal(calls.runs.length, 0);
  assert.ok(calls.notices.some((message) => message.includes("history unavailable")));
});

test("request history must be saved before sending a stream", () => {
  const runStart = app.indexOf("async function runDirectChatTurn(");
  const runEnd = app.indexOf("\nasync function pauseDirectChatJob(", runStart);
  const run = app.slice(runStart, runEnd);
  assert.ok(run.includes("await persistDirectChatJob(job);"));
  assert.ok(run.indexOf("await persistDirectChatJob(job);") < run.indexOf("await streamChatWithRecovery("));
});

function retryHarness(submissionState, terminal = null, fenceState = "abandoned") {
  const calls = { runs: [], confirmations: 0, fences: 0, saved: 0 };
  const user = { role: "user", message_id: "old-key", content: "create a file" };
  const failed = { role: "assistant", message_id: "assistant-old-key", content: "partial", error: "connection lost" };
  const state = { directConversationId: "conversation-1", directMessages: [user, failed], sessionId: null };
  const retry = vm.runInNewContext(retrySource, {
    state,
    crypto: { randomUUID: () => "new-key" },
    window: { localStorage: { removeItem: () => {} } },
    composerSubmissionInFlight: false,
    directChatJob: () => null,
    request: async (_path, options) => {
      if (options?.method === "POST") {
        calls.fences += 1;
        return { state: fenceState };
      }
      return { state: submissionState, terminal };
    },
    requestConfirmation: async () => { calls.confirmations += 1; return true; },
    persistDirectConversationSnapshot: async () => { calls.saved += 1; },
    modelIdentitySnapshot: (model) => model || {},
    directTurnFromHistoryMessage: () => ({ queueId: "new-key", question: user.content }),
    beginDirectChatJob: (turn) => { calls.runs.push(turn); },
    renderDirectConversation: () => {},
    toast: () => {},
    Date,
  });
  return { retry, state, calls };
}

test("failed transport with completed Host result recovers without rerunning", async () => {
  const { retry, state, calls } = retryHarness("completed", {
    type: "RUN_FINISHED", result: { message: { role: "assistant", content: "finished" } },
  });
  await retry(1);
  assert.equal(calls.runs.length, 0);
  assert.equal(calls.saved, 1);
  assert.equal(state.directMessages[1].content, "finished");
  assert.equal(state.directMessages[1].error, "");
});

test("failed transport still running blocks manual retry", async () => {
  const { retry, calls } = retryHarness("running");
  await retry(1);
  assert.equal(calls.runs.length, 0);
  assert.equal(calls.confirmations, 0);
});

test("uncertain failed transport requires confirmation and a new branch key", async () => {
  const { retry, calls } = retryHarness("uncertain");
  await retry(1);
  assert.equal(calls.confirmations, 1);
  assert.equal(calls.runs.length, 1);
  assert.equal(calls.runs[0].queueId, "new-key");
  assert.equal(calls.runs[0].branchFromMessageId, "old-key");
});

test("unrecorded failed transport also requires confirmation", async () => {
  const { retry, calls } = retryHarness("missing");
  await retry(1);
  assert.equal(calls.confirmations, 1);
  assert.equal(calls.fences, 1);
  assert.equal(calls.runs.length, 1);
});
