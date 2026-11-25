// node --test tests/node   (no network: fetch is replaced by a fake)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const sms = require("../../web/api/send-sms.js");

const KEY = "test-key-never-shown";
const SANDBOX = { AT_USERNAME: "sandbox", AT_API_KEY: KEY };

function dataDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "imarika-sms-"));
  fs.writeFileSync(path.join(dir, "stations.json"), JSON.stringify({ stations: [
    { id: "083", insights: [{ key: "flood_alert:watch:2026-11-28", sms_text: "Station 083: Flood watch 28 Nov: heavy rain expected." }] },
    { id: "113", insights: [] },
  ] }));
  fs.writeFileSync(path.join(dir, "scenarios.json"), JSON.stringify({ scenarios: [
    { key: "heavy", sms_text: "Scenario: Flood warning." }, { key: "dry", sms_text: null },
  ] }));
  return dir;
}

function fakeFetch(status = "Success") {
  const calls = [];
  const impl = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ SMSMessageData: { Message: "Sent", Recipients: [{ status, number: "+254712345678" }] } }) };
  };
  impl.calls = calls;
  return impl;
}

async function call(handler, { method = "POST", body, ip = "1.2.3.4" } = {}) {
  const headers = {};
  const res = { statusCode: 0, setHeader: (k, v) => { headers[k.toLowerCase()] = v; }, end: (text) => { res.text = text; } };
  await handler({ method, body, headers: { "x-forwarded-for": ip } }, res);
  return { status: res.statusCode, json: JSON.parse(res.text), headers, raw: res.text };
}

test("phone numbers must be Kenyan mobile numbers in +254 format", () => {
  assert.equal(sms.validatePhone("+254 712-345-678"), "+254712345678");
  assert.equal(sms.validatePhone("+254112345678"), "+254112345678");
  for (const bad of ["0712345678", "+25471234567", "+2547123456789", "+255712345678", "+254212345678", "", null]) {
    assert.throws(() => sms.validatePhone(bad), /\+254/);
  }
});

test("refuses to run unless AT_USERNAME is exactly sandbox", async () => {
  for (const username of ["imarika", "Sandbox", " sandbox", "", undefined]) {
    assert.throws(() => sms.checkConfig({ AT_USERNAME: username, AT_API_KEY: KEY }), /sandbox/);
    const fetchImpl = fakeFetch();
    const handler = sms.createHandler({ env: { AT_USERNAME: username, AT_API_KEY: KEY }, fetchImpl, dataDir: dataDir() });
    const r = await call(handler, { body: { phone: "+254712345678", scenario: "heavy" } });
    assert.equal(r.status, 503);
    assert.equal(r.json.ok, false);
    assert.equal(fetchImpl.calls.length, 0, "no request may leave the server");
  }
});

test("only POST is allowed", async () => {
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl: fakeFetch(), dataDir: dataDir() });
  const r = await call(handler, { method: "GET" });
  assert.equal(r.status, 405);
  assert.equal(r.headers.allow, "POST");
});

test("client-supplied text and unknown fields are rejected", async () => {
  const fetchImpl = fakeFetch();
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl, dataDir: dataDir() });
  for (const field of ["text", "message", "to", "username"]) {
    const r = await call(handler, { body: { phone: "+254712345678", scenario: "heavy", [field]: "hello" } });
    assert.equal(r.status, 400);
  }
  assert.equal(fetchImpl.calls.length, 0);
});

test("the message is looked up on the server and sent to the sandbox endpoint only", async () => {
  const fetchImpl = fakeFetch();
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl, dataDir: dataDir() });
  const r = await call(handler, { body: { phone: "+254712345678", station: "083", advisory: "flood_alert:watch:2026-11-28" } });
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, true);
  assert.equal(fetchImpl.calls.length, 1);
  const { url, options } = fetchImpl.calls[0];
  assert.equal(url, "https://api.sandbox.africastalking.com/version1/messaging");
  const form = new URLSearchParams(options.body);
  assert.equal(form.get("username"), "sandbox");
  assert.equal(form.get("to"), "+254712345678");
  assert.equal(form.get("message"), "Station 083: Flood watch 28 Nov: heavy rain expected.");
  assert.equal(options.headers.apiKey, KEY);
  assert.ok(!r.raw.includes(KEY), "the API key is never returned");
});

test("unknown stations, advisories and empty scenarios give clean errors", async () => {
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl: fakeFetch(), dataDir: dataDir() });
  assert.equal((await call(handler, { body: { phone: "+254712345678", station: "999", advisory: "x" } })).status, 404);
  assert.equal((await call(handler, { body: { phone: "+254712345678", station: "083", advisory: "x" } })).status, 404);
  assert.equal((await call(handler, { body: { phone: "+254712345678", scenario: "dry" } })).status, 409);
  assert.equal((await call(handler, { body: { phone: "+254712345678" } })).status, 400);
  assert.equal((await call(handler, { body: "not json" })).status, 400);
});

test("a refusal from the sandbox is reported as not sent", async () => {
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl: fakeFetch("InvalidPhoneNumber"), dataDir: dataDir() });
  const r = await call(handler, { body: { phone: "+254712345678", scenario: "heavy" } });
  assert.equal(r.status, 502);
  assert.equal(r.json.ok, false);
  assert.ok(!r.raw.includes(KEY));
});

test("requests are rate limited per IP address", async () => {
  let clock = 0;
  const limiter = sms.createRateLimiter({ max: 2, windowMs: 1000 }, () => clock);
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl: fakeFetch(), dataDir: dataDir(), limiter });
  const body = { phone: "+254712345678", scenario: "heavy" };
  assert.equal((await call(handler, { body })).status, 200);
  assert.equal((await call(handler, { body })).status, 200);
  assert.equal((await call(handler, { body })).status, 429);
  assert.equal((await call(handler, { body, ip: "5.6.7.8" })).status, 200);
  clock = 1500;
  assert.equal((await call(handler, { body })).status, 200);
});

test("the function source contains no production endpoint", () => {
  const source = fs.readFileSync(path.join(__dirname, "../../web/api/send-sms.js"), "utf8");
  assert.ok(!/https:\/\/api\.africastalking\.com/.test(source));
  assert.ok(!/console\.(log|error|warn)/.test(source), "the function does not log");
});
