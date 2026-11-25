// node --test tests/node/units.test.js   (no network)
// One rounding rule for rain and temperature on every surface: map panel, list, station page
// (tiles, strip, charts, table) and the SMS builder. "5.6 mm" next to "6 mm" must be impossible.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const U = require("../../web/js/units.js");
const S = require("../../web/js/station-logic.js");
const B = require("../../web/js/sms-message.js");

const WEB = path.join(__dirname, "../../web");
const stations = JSON.parse(fs.readFileSync(path.join(WEB, "data/stations.json"), "utf8")).stations;
const read = (name) => fs.readFileSync(path.join(WEB, "js", name), "utf8");

test("rain: whole mm from 1 mm up, one decimal below 1 mm; temperature: whole degrees", () => {
  assert.equal(U.rainText(5.6), "6");
  assert.equal(U.rainText(10.9), "11");
  assert.equal(U.rainText(1), "1");
  assert.equal(U.rainText(0.96), "1");      // never "1.0"
  assert.equal(U.rainText(0.43), "0.4");
  assert.equal(U.rainText(0.04), "0");
  assert.equal(U.rainText(0), "0");
  assert.equal(U.rainText(null), "");
  assert.equal(U.temp(29.5), 30);
  assert.equal(U.temp(21.4), 21);
  assert.equal(U.temp(undefined), null);
});

test("weekly totals add the daily values as shown, then round by the same rule", () => {
  assert.equal(U.rainTotal([10.9, 10.9, 10.9, 10.9, 10.9, 10.9, 10.9]), 77);   // 7 x "11 mm", not 76
  assert.equal(U.rainTotal([0.3, 0.4]), 0.7);
  assert.equal(U.rainTotal([5, 0.4]), 5);
  for (const s of stations) {
    const r = S.rainSummary(s);
    const shown = (s.active ? s.forecast : []).filter((d) => typeof d.rain_mm === "number").map((d) => Number(U.rainText(d.rain_mm)));
    const sum = shown.reduce((a, b) => a + b, 0);
    assert.equal(r.forecastMm, U.rain(sum), s.id);
  }
});

test("every surface formats rain and temperature only through js/units.js", () => {
  for (const name of ["map.js", "station.js", "charts.js", "sms-message.js", "station-logic.js"]) {
    const src = read(name);
    assert.ok(/U\.(rainText|rainTotal)\(/.test(src), `${name} does not use the shared rain rule`);
    assert.ok(!/oneDecimal|mm\.toFixed|"<1"/.test(src), `${name} has its own rain rounding`);
    assert.ok(!/(Math|I)\.round\([^)]*(rain|temperature_c|temp_max|temp_min)/.test(src), `${name} rounds rain or temperature itself`);
  }
});

test("the same station shows the same rounded rain on the map panel, the station strip, the chart and the SMS", () => {
  // The map panel outlook, the station strip and the chart labels all print U.rainText(day.rain_mm) for the
  // first forecast day (checked above); the SMS must print the very same number.
  const data = { stations, guide: null };
  let checked = 0;
  for (const s of stations) {
    const day = (s.forecast || [])[0];
    if (!B.selectable(s) || !day || typeof day.rain_mm !== "number") continue;
    const shown = U.rainText(day.rain_mm);
    for (const lang of B.LANGS) {
      const msg = B.buildMessage(data, { scenario: "latest", station: s.id, lang });
      if (!msg || !/Outlook|Mwelekeo/.test(msg.text)) continue;
      const m = /about (\d+(?:\.\d)?) mm|milimita (\d+(?:\.\d)?) na/.exec(msg.text);
      assert.ok(m, msg.text);
      assert.equal(m[1] || m[2], shown, `${s.id} ${lang}: ${msg.text}`);
      checked += 1;
    }
  }
  assert.ok(checked > 0, "no station with an outlook in the real data");
});
