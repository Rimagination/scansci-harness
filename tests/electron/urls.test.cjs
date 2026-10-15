"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { browserUrl, isAppUrl } = require("../../electron/urls.cjs");

test("external browser accepts public HTTP(S) pages", () => {
  assert.equal(browserUrl("pubmed.ncbi.nlm.nih.gov/"), "https://pubmed.ncbi.nlm.nih.gov/");
  assert.equal(browserUrl("https://example.org/paper?q=1"), "https://example.org/paper?q=1");
});

test("external browser rejects local and privileged addresses", () => {
  for (const address of [
    "file:///C:/secret.txt", "javascript:alert(1)", "https://user:pass@example.org/",
    "http://127.0.0.1:8791/", "http://127.1/", "http://2130706433/",
    "http://[::1]/", "http://[::ffff:7f00:1]/", "http://192.168.1.1/", "http://localhost/", "http://foo.localhost/",
    "https://example.org\\@127.0.0.1/",
  ]) assert.throws(() => browserUrl(address), Error, address);
});

test("app origin is exact for loopback and the stable app scheme", () => {
  assert.equal(isAppUrl("http://127.0.0.1:8791/chat", "http://127.0.0.1:8791"), true);
  assert.equal(isAppUrl("http://127.0.0.1:8792/chat", "http://127.0.0.1:8791"), false);
  assert.equal(isAppUrl("https://example.org/", "http://127.0.0.1:8791"), false);
  assert.equal(isAppUrl("scansci://app/api/health", "scansci://app"), true);
  assert.equal(isAppUrl("scansci://evil/api/health", "scansci://app"), false);
});
