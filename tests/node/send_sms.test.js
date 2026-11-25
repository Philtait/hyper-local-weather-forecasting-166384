// node --test tests/node/send_sms.test.js   (no network: fetch is replaced by a fake)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const sms = require("../../web/api/send-sms.js");
const builder = require("../../web/js/sms-message.js");

const KEY = "test-key-never-shown";
const SANDBOX = { AT_USERNAME: "sandbox", AT_API_KEY: KEY };
const PHONE = "+254712345678";
const FORECAST = [{ date: "2026-11-25", rain_mm: 7.6, temp_max: 29.4 }];

const STATIONS = [
  { id: "001", area: "Butula", active: true, last_reading_utc: "2026-11-24T17:26:33Z", forecast_variation_mm: 0,
    latest: { time_utc: "2026-11-24T17:26:33Z", temperature_c: 27.2, humidity_pct: 78, rain_since_morning_mm: 3 },
    forecast: FORECAST, insights: [] },
  { id: "061", area: "Butula", active: true, last_reading_utc: "2026-11-24T10:00:00Z", forecast_variation_mm: 4,
    latest: { time_utc: "2026-11-24T10:00:00Z", temperature_c: 25, humidity_pct: null, rain_since_morning_mm: null },
    forecast: FORECAST, insights: [{ key: "rain_delay:advisory:2026-11-26", sms_text: "Station 061, Butula: Rain expected on 26 Nov. Delay spraying." }] },
  { id: "005", area: "Nambale", active: false, last_reading_utc: "2026-11-20T16:27:31Z", latest: null, forecast: [], insights: [] },
];

function dataDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "imarika-sms-"));
  fs.writeFileSync(path.join(dir, "stations.json"), JSON.stringify({ stations: STATIONS }));
  const sentences = ["Moderate rain is expected this week, about 8 mm a day.", "We are at the end of the short rains.",
                     "Some farmers in Busia say cassava fed their families."];
  fs.writeFileSync(path.join(dir, "crop_guide.json"), JSON.stringify({ stations: { "001": { area: "Butula", crops: {
    cassava: { en: { sentences: sentences.map((text) => ({ text, facts: [] })), source: "template", model: null } } } } } }));
  return dir;
}

function fakeFetch(status = "Success") {
  const calls = [];
  const impl = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ SMSMessageData: { Message: "Sent", Recipients: [{ status, number: PHONE }] } }) };
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

const LATEST = { phone: PHONE, scenario: "latest", station: "001", lang: "en" };
const CROP = { phone: PHONE, scenario: "crop_guide", station: "001", crop: "cassava", lang: "en" };
const ALERT = { phone: PHONE, scenario: "alert", station: "061", lang: "en" };
const sentText = (fetchImpl) => new URLSearchParams(fetchImpl.calls[fetchImpl.calls.length - 1].options.body).get("message");

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
    for (const body of [LATEST, CROP, ALERT]) {
      const r = await call(handler, { body });
      assert.equal(r.status, 503);
      assert.equal(r.json.ok, false);
    }
    assert.equal(fetchImpl.calls.length, 0, "no request may leave the server");
  }
});

test("only POST is allowed", async () => {
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl: fakeFetch(), dataDir: dataDir() });
  const r = await call(handler, { method: "GET" });
  assert.equal(r.status, 405);
  assert.equal(r.headers.allow, "POST");
});

test("latest readings: the server builds the text from the station data and sends it to the sandbox only", async () => {
  const fetchImpl = fakeFetch();
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl, dataDir: dataDir() });
  for (const lang of ["en", "sw"]) {
    const r = await call(handler, { body: { ...LATEST, lang } });
    assert.equal(r.status, 200);
    const expected = builder.buildMessage({ stations: STATIONS }, { scenario: "latest", station: "001", lang });
    assert.equal(sentText(fetchImpl), expected.text);
    assert.equal(r.json.chars, expected.chars);
    assert.equal(r.json.parts, expected.parts);
  }
  const { url, options } = fetchImpl.calls[0];
  assert.equal(url, "https://api.sandbox.africastalking.com/version1/messaging");
  const form = new URLSearchParams(options.body);
  assert.equal(form.get("username"), "sandbox");
  assert.equal(form.get("to"), PHONE);
  assert.equal(options.headers.apiKey, KEY);
  assert.match(form.get("message"), /^Imarika Butula, station 001\. Last reading Tue 24 Nov, 20:26: temp 27C/);
});

test("weather alert: sent only when the engine produced advice for the station", async () => {
  const fetchImpl = fakeFetch();
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl, dataDir: dataDir(), limiter: () => true });
  assert.equal((await call(handler, { body: ALERT })).status, 200);
  assert.equal(sentText(fetchImpl), "Station 061, Butula: Rain expected on 26 Nov. Delay spraying.");
  const none = await call(handler, { body: { ...ALERT, station: "001" } });
  assert.equal(none.status, 409);
  assert.equal(fetchImpl.calls.length, 1);
});

test("crop guide: the server builds the text itself", async () => {
  const fetchImpl = fakeFetch();
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl, dataDir: dataDir() });
  const r = await call(handler, { body: CROP });
  assert.equal(r.status, 200);
  const expected = "Imarika Butula: Moderate rain is expected this week, about 8 mm a day. " +
    "Some farmers in Busia say cassava fed their families. We are at the end of the short rains.";
  assert.equal(sentText(fetchImpl), expected);
  assert.equal(r.json.chars, expected.length);
  assert.equal(r.json.parts, 2);
});

test("bad scenario, station, crop, language, phone and extra fields are rejected before any send", async () => {
  const fetchImpl = fakeFetch();
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl, dataDir: dataDir(), limiter: () => true });
  const cases = [
    [{ ...LATEST, scenario: "heavy" }, 400],     // the old test scenarios are gone
    [{ ...LATEST, scenario: "dry" }, 400],
    [{ ...LATEST, scenario: undefined }, 400],
    [{ ...LATEST, station: "999" }, 404],
    [{ ...LATEST, station: "__proto__" }, 404],
    [{ ...LATEST, station: "" }, 400],
    [{ ...LATEST, station: undefined }, 400],
    [{ ...LATEST, station: "005" }, 409],        // not sending data
    [{ ...ALERT, station: "005" }, 409],
    [{ ...CROP, station: "005" }, 409],
    [{ ...CROP, station: "061" }, 409],          // no crop guide for this station
    [{ ...CROP, crop: "rice" }, 400],
    [{ ...CROP, crop: undefined }, 400],
    [{ ...CROP, crop: "maize" }, 409],           // no entry for this crop
    [{ ...LATEST, crop: "maize" }, 400],         // crop only with the crop guide
    [{ ...LATEST, lang: "fr" }, 400],
    [{ ...LATEST, lang: undefined }, 400],
    [{ ...LATEST, phone: "0712345678" }, 400],
    [{ ...LATEST, phone: "+255712345678" }, 400],
    [{ ...LATEST, message: "hello" }, 400],
    [{ ...LATEST, text: "hello" }, 400],
    [{ ...LATEST, advisory: "x" }, 400],
    [{ ...LATEST, to: PHONE }, 400],
    [{ ...LATEST, username: "live" }, 400],
    [{ ...LATEST, station: { id: "001" } }, 400],
    [{ ...LATEST, station: "1".repeat(81) }, 400],
  ];
  for (const [body, status] of cases) {
    const clean = Object.fromEntries(Object.entries(body).filter(([, v]) => v !== undefined));
    const r = await call(handler, { body: clean });
    assert.equal(r.status, status, JSON.stringify(clean) + " -> " + r.raw);
    assert.equal(r.json.ok, false);
    assert.ok(!r.raw.includes(KEY));
  }
  assert.equal((await call(handler, { body: "not json" })).status, 400);
  assert.equal((await call(handler, { body: [LATEST] })).status, 400);
  assert.equal(fetchImpl.calls.length, 0, "nothing was sent");
});

test("a refusal from the sandbox is reported as not sent", async () => {
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl: fakeFetch("InvalidPhoneNumber"), dataDir: dataDir() });
  const r = await call(handler, { body: LATEST });
  assert.equal(r.status, 502);
  assert.equal(r.json.ok, false);
  assert.ok(!r.raw.includes(KEY));
});

test("the rate limit is unchanged: 5 per IP per 10 minutes", async () => {
  assert.deepEqual(sms.RATE_LIMIT, { max: 5, windowMs: 10 * 60 * 1000 });
  let clock = 0;
  const limiter = sms.createRateLimiter(sms.RATE_LIMIT, () => clock);
  const handler = sms.createHandler({ env: SANDBOX, fetchImpl: fakeFetch(), dataDir: dataDir(), limiter });
  for (let i = 0; i < 5; i++) assert.equal((await call(handler, { body: [LATEST, CROP, ALERT][i % 3] })).status, 200);
  assert.equal((await call(handler, { body: LATEST })).status, 429);
  assert.equal((await call(handler, { body: LATEST, ip: "5.6.7.8" })).status, 200);
  clock = 10 * 60 * 1000;
  assert.equal((await call(handler, { body: LATEST })).status, 200);
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
      const handler = sms.createHandler({ env: SANDBOX, fetchImpl, dataDir: dataDir(), limiter: () => true });
      for (const body of [LATEST, CROP, ALERT, { ...LATEST, station: "999" }, { ...LATEST, scenario: "heavy" }]) {
        const r = await call(handler, { body });
        assert.ok(!r.raw.includes(KEY) && !/apikey/i.test(r.raw), r.raw);
        assert.ok(!Object.values(r.headers).join(" ").includes(KEY));
      }
    }
  } finally {
    Object.assign(console, saved);
  }
  assert.deepEqual(logged, [], "the function logs nothing");
});

test("deployed layout: the function reads data/ next to api/, which vercel.json bundles", () => {
  assert.equal(sms.DATA_DIR, path.join(__dirname, "../../web/data"));
  for (const name of ["stations.json", "crop_guide.json"]) assert.ok(fs.existsSync(path.join(sms.DATA_DIR, name)), name);
  assert.ok(!fs.existsSync(path.join(sms.DATA_DIR, "scenarios.json")), "the test scenarios are gone");
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, "../../web/vercel.json"), "utf8"));
  assert.equal(config.functions["api/send-sms.js"].includeFiles, "data/**");
  const source = fs.readFileSync(path.join(__dirname, "../../web/api/send-sms.js"), "utf8");
  assert.match(source, /require\("\.\.\/js\/sms-message\.js"\)/, "a static require that Vercel's file tracer bundles");
  assert.ok(!/fetch\([^)]*(crop_guide|stations)/.test(source), "data is read from the bundle, not fetched over the network");
});

test("the function source contains no production endpoint and does not log", () => {
  const source = fs.readFileSync(path.join(__dirname, "../../web/api/send-sms.js"), "utf8");
  assert.ok(!/https:\/\/api\.africastalking\.com/.test(source));
  assert.ok(!/console\.(log|error|warn|info|debug)/.test(source), "the function does not log");
  assert.ok(!/scenarios\.json|advisory/.test(source), "no test scenarios or old advisory lookup");
});
