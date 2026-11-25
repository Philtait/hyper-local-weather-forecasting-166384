// node --test tests/node/sms_message.test.js   (no network)
// The SMS builder shared by the page and the send function.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const builder = require("../../web/js/sms-message.js");
const sms = require("../../web/api/send-sms.js");

const SOURCE = path.join(__dirname, "../../web/js/sms-message.js");
const DATA = path.join(__dirname, "../../web/data");
const GSM_BASIC = new Set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZ" +
  "ÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà");

/* The module as the browser sees it: no `module`, attached to self. */
function browserBuilder() {
  const context = { self: {} };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(SOURCE, "utf8"), context);
  return context.self.ImarikaSmsMessage;
}

function station(overrides = {}) {
  return { id: "001", area: "Butula", active: true, last_reading_utc: "2026-11-24T17:26:33Z", forecast_variation_mm: 0,
    latest: { time_utc: "2026-11-24T17:26:33Z", temperature_c: 27.2, humidity_pct: 78, rain_since_morning_mm: 3 },
    forecast: [{ date: "2026-11-25", rain_mm: 7.6, temp_max: 29.4 }], insights: [], ...overrides };
}
const latest = (s, lang) => builder.buildLatestMessage(s, lang);

// ------------------------------------------------------------------ latest readings
test("latest readings in English and Kiswahili, flat forecast", () => {
  assert.equal(latest(station(), "en").text,
    "Imarika Butula, station 001. Last reading Tue 24 Nov, 20:26: temp 27C, humidity 78%, rain since 08:00: 3 mm. " +
    "Outlook: about 8 mm rain and up to 29C each day, similar to recent days.");
  assert.equal(latest(station(), "sw").text,
    "Imarika Butula, kituo 001. Kipimo cha mwisho Jumanne 24 Novemba, 20:26: joto nyuzi joto 27, unyevu wa hewa 78%, " +
    "mvua tangu 08:00: milimita 3. Mwelekeo: mvua karibu milimita 8 na joto la juu nyuzi joto 29 kila siku, kama siku za hivi karibuni.");
});

test("non-flat forecast: the outlook names the first forecast day instead of 'similar to recent days'", () => {
  const s = station({ forecast_variation_mm: 4.2 });
  assert.match(latest(s, "en").text, / Outlook Wed 25 Nov: about 8 mm rain and up to 29C\.$/);
  assert.match(latest(s, "sw").text, / Mwelekeo Jumatano 25 Novemba: mvua karibu milimita 8 na joto la juu nyuzi joto 29\.$/);
  assert.ok(!/similar|kama siku/.test(latest(s, "en").text + latest(s, "sw").text));
});

test("missing values are left out, never shown as zero", () => {
  const some = station({ latest: { time_utc: "2026-11-24T17:26:33Z", temperature_c: 22.6, humidity_pct: null } });
  assert.match(latest(some, "en").text, /Last reading Tue 24 Nov, 20:26: temp 23C\. Outlook/);
  assert.match(latest(some, "sw").text, /Kipimo cha mwisho Jumanne 24 Novemba, 20:26: joto nyuzi joto 23\. Mwelekeo/);
  const none = station({ latest: null, forecast: [] });
  assert.equal(latest(none, "en").text, "Imarika Butula, station 001. Last reading Tue 24 Nov, 20:26.");
  assert.equal(latest(none, "sw").text, "Imarika Butula, kituo 001. Kipimo cha mwisho Jumanne 24 Novemba, 20:26.");
  const rain = station({ latest: { time_utc: "2026-11-24T17:26:33Z", rain_since_morning_mm: 0.4 } });
  assert.match(latest(rain, "en").text, /rain since 08:00: 0\.4 mm/);
});

test("an old reading still shows its own date and time; nothing says now or live", () => {
  const stale = station({ latest: { time_utc: "2026-11-20T16:07:02Z", temperature_c: 21 }, last_reading_utc: "2026-11-20T16:07:02Z" });
  assert.match(latest(stale, "en").text, /Last reading Fri 20 Nov, 19:07: temp 21C/);
  assert.match(latest(stale, "sw").text, /Kipimo cha mwisho Ijumaa 20 Novemba, 19:07/);
  for (const lang of ["en", "sw"]) assert.ok(!/\b(now|live|sasa)\b/i.test(latest(stale, lang).text));
});

test("Kenya time is UTC+3 across midnight; 24-hour clock in both languages", () => {
  assert.equal(builder.readingTime("2026-11-24T21:30:00Z", "en"), "Wed 25 Nov, 00:30");
  assert.equal(builder.readingTime("2026-11-24T21:30:00Z", "sw"), "Jumatano 25 Novemba, 00:30");
  assert.equal(builder.readingTime("2026-12-31T22:05:00Z", "sw"), "Ijumaa 1 Januari, 01:05");
  assert.equal(builder.readingTime("not a time", "en"), null);
});

test("a long message drops the outlook, never the reading date and time", () => {
  const long = station({ area: "A".repeat(170) });
  const text = latest(long, "en").text;
  assert.ok(text.length <= 306);
  assert.match(text, /Last reading Tue 24 Nov, 20:26: temp 27C, humidity 78%, rain since 08:00: 3 mm\.$/);
  assert.ok(!text.includes("Outlook"));
});

test("plain GSM characters, no degree sign", () => {
  for (const lang of ["en", "sw"]) {
    const text = latest(station(), lang).text;
    assert.ok(!text.includes("°"));
    for (const ch of text) assert.ok(GSM_BASIC.has(ch), JSON.stringify(ch));
  }
});

// ------------------------------------------------------------------ stations and alerts
test("a station that is not sending data offers no message", () => {
  const off = station({ active: false });
  assert.equal(builder.selectable(off), false);
  assert.equal(latest(off, "en"), null);
  const data = { stations: [off], guide: null };
  assert.deepEqual(builder.available(data, "001"), { latest: false, crop_guide: false, alert: false });
  for (const scenario of builder.SCENARIOS) assert.equal(builder.buildMessage(data, { scenario, station: "001", lang: "en", crop: "maize" }), null);
});

test("the weather alert is offered only when the engine produced advice", () => {
  assert.equal(builder.buildAlertMessage(station()), null);
  assert.equal(builder.available({ stations: [station()] }, "001").alert, false);
  const advised = station({ insights: [{ key: "planting_window:advisory:2026-11-26", sms_text: "Station 001, Butula: Good rain for planting from 26 Nov." }] });
  assert.equal(builder.buildAlertMessage(advised).text, "Station 001, Butula: Good rain for planting from 26 Nov.");
  assert.equal(builder.available({ stations: [advised] }, "001").alert, true);
});

test("unknown scenario, language, station or crop gives no message", () => {
  const data = { stations: [station()], guide: null };
  assert.equal(builder.buildMessage(data, { scenario: "heavy", station: "001", lang: "en" }), null);
  assert.equal(builder.buildMessage(data, { scenario: "latest", station: "001", lang: "fr" }), null);
  assert.equal(builder.buildMessage(data, { scenario: "latest", station: "999", lang: "en" }), null);
  assert.equal(builder.buildMessage(data, { scenario: "latest", station: "__proto__", lang: "en" }), null);
  assert.equal(builder.buildMessage(data, { scenario: "crop_guide", station: "001", crop: "rice", lang: "en" }), null);
  assert.deepEqual(builder.SCENARIOS, ["latest", "crop_guide", "alert"]);
});

// ------------------------------------------------------------------ crop guide
function guide(sentences, area = "Butula") {
  return { stations: { "001": { area, crops: { maize: { en: { sentences: sentences.map((text) => ({ text, facts: [] })),
    source: "ai", model: "m" } } } } } };
}
const SHORT = ["Moderate rain of about 8 mm a day.", "We are at the end of the short rains.", "Maize needs moist weather."];

test("crop guide: prefix, then sentences 1, 3 and 2", () => {
  const built = builder.buildCropMessage(guide(SHORT), "001", "maize", "en");
  assert.equal(built.text, "Imarika Butula: " + [SHORT[0], SHORT[2], SHORT[1]].join(" "));
  assert.equal(built.parts, 1);
});

test("crop guide: within 306 characters, never cutting a sentence", () => {
  const long = ["A".repeat(150) + ".", "B".repeat(140) + ".", "C".repeat(120) + "."];
  const built = builder.buildCropMessage(guide(long), "001", "maize", "en");
  assert.equal(built.text, "Imarika Butula: " + long[0] + " " + long[2]);
  assert.ok(!built.text.includes("BBBBB") && built.chars <= 306 && built.parts === 2);
});

test("SMS parts and GSM characters", () => {
  assert.deepEqual([160, 161, 306, 307].map(builder.smsParts), [1, 2, 2, 3]);
  const built = builder.buildCropMessage(guide(["“Mvua” – 29…", "Café ça.", "Naïve €5."]), "001", "maize", "en");
  assert.equal(built.text, 'Imarika Butula: "Mvua" - 29... Naive EUR5. Café ca.');  // é is in the GSM basic set
  assert.equal(builder.buildCropMessage(guide(["About {RAIN} mm.", SHORT[1], SHORT[2]]), "001", "maize", "en"), null);
});

// ------------------------------------------------------------------ real data: page == server
test("the page preview and the sent text are the same for every station, message and language", async () => {
  const stations = JSON.parse(fs.readFileSync(path.join(DATA, "stations.json"), "utf8")).stations;
  const real = { stations, guide: JSON.parse(fs.readFileSync(path.join(DATA, "crop_guide.json"), "utf8")) };
  const page = browserBuilder();
  const sent = [];
  const fetchImpl = async (url, options) => {
    sent.push(new URLSearchParams(options.body).get("message"));
    return { ok: true, json: async () => ({ SMSMessageData: { Recipients: [{ status: "Success" }] } }) };
  };
  const handler = sms.createHandler({ env: { AT_USERNAME: "sandbox", AT_API_KEY: "k" }, fetchImpl, limiter: () => true });
  const counts = { latest: 0, crop_guide: 0, alert: 0, refused: 0 };
  for (const s of stations) {
    for (const lang of builder.LANGS) {
      const requests = [{ scenario: "latest" }, { scenario: "alert" }, ...builder.CROPS.map((crop) => ({ scenario: "crop_guide", crop }))];
      for (const extra of requests) {
        const request = { station: s.id, lang, ...extra };
        const preview = page.buildMessage(real, request);
        const server = builder.buildMessage(real, request);
        assert.deepEqual(preview && { ...preview }, server && { ...server }, JSON.stringify(request));
        const res = { setHeader() {}, end(text) { this.body = JSON.parse(text); } };
        await handler({ method: "POST", headers: {}, body: { phone: "+254712345678", ...request } }, res);
        if (server) {
          assert.equal(res.statusCode, 200, JSON.stringify(request) + " " + JSON.stringify(res.body));
          assert.equal(sent[sent.length - 1], server.text, JSON.stringify(request));
          assert.ok(server.chars <= 306 && server.parts <= 2);
          for (const ch of server.text) assert.ok(GSM_BASIC.has(ch), JSON.stringify(ch));
          counts[extra.scenario] += 1;
        } else {
          assert.ok(res.statusCode >= 400, JSON.stringify(request));  // the page offers nothing, the server sends nothing
          counts.refused += 1;
        }
      }
      if (!builder.selectable(s)) assert.deepEqual({ ...page.available(real, s.id) }, { latest: false, crop_guide: false, alert: false });
    }
  }
  const active = stations.filter(builder.selectable).length;
  assert.equal(counts.latest, active * 2, "every station sending data has a latest-readings message in both languages");
  assert.ok(counts.crop_guide > 0);
});
