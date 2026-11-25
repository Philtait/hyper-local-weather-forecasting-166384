// node --test tests/node   (no network)
// The crop guide SMS builder shared by the page and the send function.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const builder = require("../../web/js/sms-message.js");
const sms = require("../../web/api/send-sms.js");

const SOURCE = path.join(__dirname, "../../web/js/sms-message.js");
const GUIDE_FILE = path.join(__dirname, "../../web/data/crop_guide.json");
const GSM_BASIC = new Set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZ" +
  "ÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà");

/* The module as the browser sees it: no `module`, attached to self. */
function browserBuilder() {
  const context = { self: {} };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(SOURCE, "utf8"), context);
  return context.self.ImarikaSmsMessage;
}

function guide(sentences, area = "Butula") {
  return { stations: { "001": { area, crops: { maize: { en: { sentences: sentences.map((text) => ({ text, facts: [] })),
    source: "ai", model: "m" } } } } } };
}

const SHORT = ["Moderate rain of about 8 mm a day.", "We are at the end of the short rains.", "Maize needs moist weather."];

test("prefix, then sentences 1, 3 and 2", () => {
  const built = builder.buildCropMessage(guide(SHORT), "001", "maize", "en");
  assert.equal(built.text, "Imarika Butula: " + [SHORT[0], SHORT[2], SHORT[1]].join(" "));
  assert.equal(built.chars, built.text.length);
  assert.equal(built.parts, 1);
});

test("stays within 306 characters and never cuts a sentence", () => {
  const long = ["A".repeat(150) + ".", "B".repeat(140) + ".", "C".repeat(120) + "."];
  const built = builder.buildCropMessage(guide(long), "001", "maize", "en");
  assert.ok(built.chars <= 306);
  assert.equal(built.text, "Imarika Butula: " + long[0] + " " + long[2]);  // sentence 2 would not fit: left out whole
  assert.ok(!built.text.includes("BBBBB"));
  assert.equal(built.parts, 2);
});

test("SMS parts: 160 for one message, 153 per part after that", () => {
  assert.equal(builder.smsParts(160), 1);
  assert.equal(builder.smsParts(161), 2);
  assert.equal(builder.smsParts(306), 2);
  assert.equal(builder.smsParts(307), 3);
});

test("plain GSM characters only", () => {
  const built = builder.buildCropMessage(guide(["“Mvua” – nyuzi joto 29…", "Café ça.", "Naïve €5."]),
    "001", "maize", "en");
  // "é" is in the GSM basic set and stays; "ç" and "ï" are not and lose their accents.
  assert.equal(built.text, 'Imarika Butula: "Mvua" - nyuzi joto 29... Naive EUR5. Café ca.');
  for (const ch of built.text) assert.ok(GSM_BASIC.has(ch), JSON.stringify(ch));
});

test("unknown station, crop or language, and unfilled placeholders, give no message", () => {
  const g = guide(SHORT);
  assert.equal(builder.buildCropMessage(g, "999", "maize", "en"), null);
  assert.equal(builder.buildCropMessage(g, "001", "rice", "en"), null);
  assert.equal(builder.buildCropMessage(g, "001", "maize", "fr"), null);
  assert.equal(builder.buildCropMessage(g, "__proto__", "maize", "en"), null);
  assert.equal(builder.buildCropMessage(g, "constructor", "maize", "en"), null);
  assert.equal(builder.buildCropMessage(guide(["About {RAIN} mm.", SHORT[1], SHORT[2]]), "001", "maize", "en"), null);
  assert.deepEqual(builder.CROPS, ["maize", "beans", "sorghum", "cassava", "cowpeas", "sweet potatoes"]);
  assert.deepEqual(builder.LANGS, ["en", "sw"]);
});

test("the page and the send function produce the same text for every station, crop and language", async () => {
  assert.ok(fs.existsSync(GUIDE_FILE), "web/data/crop_guide.json must exist");
  const real = JSON.parse(fs.readFileSync(GUIDE_FILE, "utf8"));
  const page = browserBuilder();
  const sent = [];
  const fetchImpl = async (url, options) => {
    sent.push(new URLSearchParams(options.body).get("message"));
    return { ok: true, json: async () => ({ SMSMessageData: { Recipients: [{ status: "Success" }] } }) };
  };
  const limiter = () => true;
  const handler = sms.createHandler({ env: { AT_USERNAME: "sandbox", AT_API_KEY: "k" }, fetchImpl, limiter });
  let count = 0;
  for (const station of builder.stations(real)) {
    for (const crop of builder.CROPS) {
      for (const lang of builder.LANGS) {
        const server = builder.buildCropMessage(real, station, crop, lang);
        const preview = page.buildCropMessage(real, station, crop, lang);
        assert.ok(server, `${station}/${crop}/${lang}`);
        assert.deepEqual({ ...preview }, { ...server }, `${station}/${crop}/${lang}`);
        assert.ok(server.chars <= 306 && server.parts <= 2);
        assert.ok(server.text.startsWith("Imarika "));
        for (const ch of server.text) assert.ok(GSM_BASIC.has(ch), `${station}/${crop}/${lang}: ${JSON.stringify(ch)}`);
        const res = { setHeader() {}, end(text) { this.body = JSON.parse(text); } };
        await handler({ method: "POST", headers: {}, body: { phone: "+254712345678", scenario: "crop_guide", station, crop, lang } }, res);
        assert.equal(res.statusCode, 200, JSON.stringify(res.body));
        assert.equal(sent[sent.length - 1], server.text, `${station}/${crop}/${lang}: sent text differs from the preview`);
        count += 1;
      }
    }
  }
  assert.equal(count, builder.stations(real).length * 12);
});
