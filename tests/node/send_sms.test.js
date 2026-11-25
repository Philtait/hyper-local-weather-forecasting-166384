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

function cropDir() {
  const dir = dataDir();
  const sentences = ["Moderate rain is expected this week, about 8 mm a day.", "We are at the end of the short rains.",
                     "Some farmers in Busia say cassava fed their families."];
  fs.writeFileSync(path.join(dir, "crop_guide.json"), JSON.stringify({ stations: { "001": { area: "Butula", crops: {
    cassava: { en: { sentences: sentences.map((text) => ({ text, facts: [] })), source: "template", model: null } } } } } }));
  return dir;
}

const CROP = { phone: "+254712345678", scenario: "crop_guide", station: "001", crop: "cassava", lang: "en" };

test("crop guide: the server builds the text itself and sends it to the sandbox", async () => {
  const fetchImpl = fakeFetch();
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl, dataDir: cropDir() });
  const r = await call(handler, { body: CROP });
  assert.equal(r.status, 200);
  const form = new URLSearchParams(fetchImpl.calls[0].options.body);
  const expected = "Imarika Butula: Moderate rain is expected this week, about 8 mm a day. " +
    "Some farmers in Busia say cassava fed their families. We are at the end of the short rains.";
  assert.equal(form.get("message"), expected);
  assert.equal(r.json.chars, expected.length);
  assert.ok(expected.length > 160);
  assert.equal(r.json.parts, 2);
  assert.equal(form.get("username"), "sandbox");
  assert.ok(!r.raw.includes(KEY));
});

test("crop guide: bad station, crop, language, phone and extra fields are rejected before any send", async () => {
  const fetchImpl = fakeFetch();
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl, dataDir: cropDir(), limiter: () => true });
  const cases = [
    [{ ...CROP, station: "999" }, 404],
    [{ ...CROP, station: "__proto__" }, 404],
    [{ ...CROP, station: "" }, 400],
    [{ ...CROP, crop: "rice" }, 400],
    [{ ...CROP, crop: "maize" }, 409],          // a crop the guide has no entry for at this station
    [{ ...CROP, lang: "fr" }, 400],
    [{ ...CROP, lang: undefined }, 400],
    [{ ...CROP, phone: "0712345678" }, 400],
    [{ ...CROP, phone: "+255712345678" }, 400],
    [{ ...CROP, message: "hello" }, 400],
    [{ ...CROP, text: "hello" }, 400],
    [{ ...CROP, advisory: "x" }, 400],
    [{ ...CROP, crop: { a: 1 } }, 400],
    [{ phone: "+254712345678", scenario: "heavy", crop: "maize" }, 400],
    [{ phone: "+254712345678", station: "001", crop: "cassava", lang: "en" }, 400],
  ];
  for (const [body, status] of cases) {
    const clean = Object.fromEntries(Object.entries(body).filter(([, v]) => v !== undefined));
    const r = await call(handler, { body: clean });
    assert.equal(r.status, status, JSON.stringify(clean));
    assert.equal(r.json.ok, false);
    assert.ok(!r.raw.includes(KEY));
  }
  assert.equal(fetchImpl.calls.length, 0, "nothing was sent");
});

test("crop guide: the sandbox guard still applies", async () => {
  const fetchImpl = fakeFetch();
  const handler = sms.createHandler({ env: { AT_USERNAME: "live", AT_API_KEY: KEY }, fetchImpl, dataDir: cropDir() });
  assert.equal((await call(handler, { body: CROP })).status, 503);
  assert.equal(fetchImpl.calls.length, 0);
});

test("the rate limit is unchanged: 5 per IP per 10 minutes, crop guide included", async () => {
  assert.deepEqual(sms.RATE_LIMIT, { max: 5, windowMs: 10 * 60 * 1000 });
  let clock = 0;
  const limiter = sms.createRateLimiter(sms.RATE_LIMIT, () => clock);
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl: fakeFetch(), dataDir: cropDir(), limiter });
  for (let i = 0; i < 5; i++) assert.equal((await call(handler, { body: CROP })).status, 200);
  assert.equal((await call(handler, { body: CROP })).status, 429);
  clock = 10 * 60 * 1000;
  assert.equal((await call(handler, { body: CROP })).status, 200);
});

test("no key or headers appear in any log or response", async () => {
  const logged = [];
  const saved = {};
  for (const level of ["log", "info", "warn", "error", "debug"]) {
    saved[level] = console[level];
    console[level] = (...args) => logged.push(args.join(" "));
  }
  try {
    const failing = async () => ({ ok: false, json: async () => ({ SMSMessageData: { Recipients: [{ status: "InvalidPhoneNumber" }] } }) });
    const throwing = async () => { throw new Error("network down " + KEY); };
    for (const fetchImpl of [fakeFetch(), failing, throwing]) {
      const handler = sms.createHandler({ env: SANDBOX, fetchImpl, dataDir: cropDir() });
      for (const body of [CROP, { ...CROP, crop: "rice" }, { phone: "+254712345678", scenario: "heavy" }]) {
        const r = await call(handler, { body });
        assert.ok(!r.raw.includes(KEY) && !/apikey/i.test(r.raw), r.raw);
      }
    }
  } finally {
    Object.assign(console, saved);
  }
  assert.deepEqual(logged, [], "the function logs nothing");
});

test("deployed layout: the function reads data/ next to api/, where vercel.json bundles crop_guide.json", () => {
  assert.equal(sms.DATA_DIR, path.join(__dirname, "../../web/data"));
  assert.ok(fs.existsSync(path.join(sms.DATA_DIR, "crop_guide.json")));
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, "../../web/vercel.json"), "utf8"));
  assert.equal(config.functions["api/send-sms.js"].includeFiles, "data/**");
  const source = fs.readFileSync(path.join(__dirname, "../../web/api/send-sms.js"), "utf8");
  assert.match(source, /require\("\.\.\/js\/sms-message\.js"\)/, "a static require that Vercel's file tracer bundles");
  assert.ok(!/fetch\([^)]*crop_guide/.test(source), "the guide is read from the bundle, not fetched over the network");
});

test("the function source contains no production endpoint", () => {
  const source = fs.readFileSync(path.join(__dirname, "../../web/api/send-sms.js"), "utf8");
  assert.ok(!/https:\/\/api\.africastalking\.com/.test(source));
  assert.ok(!/console\.(log|error|warn)/.test(source), "the function does not log");
});
