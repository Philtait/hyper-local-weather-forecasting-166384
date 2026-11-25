// node --test tests/node/station_logic.test.js   (no network)
// Station page rules: tabs in the hash, charts only with enough data, the corrected rain sentence.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const S = require("../../web/js/station-logic.js");

const WEB = path.join(__dirname, "../../web");
const stations = JSON.parse(fs.readFileSync(path.join(WEB, "data/stations.json"), "utf8")).stations;
const strings = JSON.parse(fs.readFileSync(path.join(WEB, "i18n/en.json"), "utf8"));

function day(date, values) {
  return { date, rain_mm: null, temp_min: null, temp_max: null, humidity_mean: null, readings_received: 0, ...values };
}
function station(history, extra = {}) {
  return { id: "001", active: true, forecast: [{ date: "2026-11-26", rain_mm: 8, temp_max: 29 }], history, ...extra };
}
const sparse = station([
  day("2026-11-19", { rain_mm: 0, temp_min: 18, temp_max: 30, humidity_mean: 70, readings_received: 9 }),
  day("2026-11-20"), day("2026-11-21"), day("2026-11-22"),
  day("2026-11-23", { rain_mm: 0, temp_min: 19, temp_max: 31, humidity_mean: 66, readings_received: 11 }),
  day("2026-11-24"), day("2026-11-25"),
]);
const good = station(["19", "20", "21", "22", "23", "24", "25"].map((d, i) =>
  day("2026-11-" + d, i === 3 ? {} : { rain_mm: i, temp_min: 18 + i, temp_max: 28 + i, humidity_mean: 60 + i, readings_received: 12 })));

test("tabs follow the URL hash: #latest, #days, #working; anything else opens the first tab", () => {
  assert.deepEqual(S.TABS, ["latest", "days", "working"]);
  assert.equal(S.tabFromHash("#days"), "days");
  assert.equal(S.tabFromHash("#working"), "working");
  for (const hash of ["", "#", "#latest", "#bogus", "#station=113"]) assert.equal(S.tabFromHash(hash), "latest");
});

test("a chart needs at least 3 of the last 7 days; never a chart of two points", () => {
  assert.equal(S.MIN_DAYS, 3);
  for (const kind of ["rain", "temperature", "humidity"]) {
    assert.equal(S.chartable(sparse, kind), false, kind);
    assert.equal(S.chartable(good, kind), true, kind);
  }
  assert.equal(S.daysWith(sparse, "rain").length, 2);
});

test("the rain sentence counts measured days and never claims 'about 0 mm in the last 7 days'", () => {
  const r = S.rainSummary(sparse);
  assert.deepEqual([r.measuredDays, r.window, r.totalMm], [2, 7, 0]);
  const text = strings.station.rain_measured.replace("{n}", r.measuredDays).replace("{total}", r.window).replace("{mm}", r.totalMm);
  assert.equal(text, "Rain was measured on 2 of the last 7 days (total 0 mm).");
  assert.equal(strings.station.sparse.replace("{n}", 2), "This station sent data on 2 of the last 7 days, so a chart would be misleading.");
  assert.ok(!JSON.stringify(strings).includes("in the last 7 days.\""), "no old 'about X mm in the last 7 days' sentence");
  assert.ok(!("charts" in strings), "the old chart strings are gone");
});

test("stations sending data but without a forecast show no outlook", () => {
  const none = station(good.history, { forecast: [] });
  assert.equal(S.hasForecast(none), false);
  assert.equal(S.rainSummary(none).forecastDays, 0);
  assert.equal(S.hasForecast(station(good.history, { active: false })), false);
  const failed = stations.filter((s) => s.active && s.forecast_status === "not available");
  assert.ok(failed.length > 0, "the real data has stations whose model failed");
  for (const s of failed) assert.equal(S.hasForecast(s), false, s.id);
});

test("the real data has both a sparse station and one with enough data", () => {
  const active = stations.filter((s) => s.active);
  assert.ok(active.some((s) => !S.chartable(s, "rain")), "a sparse station exists");
  assert.ok(active.some((s) => S.chartable(s, "rain")), "a station with enough data exists");
  for (const s of stations) {
    const w = S.workingSummary(s);
    assert.ok(w.sent >= 0 && w.sent <= w.total && w.total <= 14, s.id);
  }
});

test("charts.js draws only with enough data, uses no hatched bars and the agreed colours", () => {
  const source = fs.readFileSync(path.join(WEB, "js/charts.js"), "utf8");
  for (const use of ["S.chartable(s, \"rain\")", "S.MIN_DAYS", "station.sparse", "station.show_numbers"]) assert.ok(source.includes(use), use);
  assert.ok(!/pattern|hatch/.test(source), "days without a reading are a short grey dash, not hatched bars");
  assert.match(source, /rain: "#2f6fb0"/);
  assert.match(source, /temp: "#8a5a3c"/);
  assert.match(source, /humid: "#0e7490"/);
  assert.match(source, /var FONT = 1[6-9]/);
});
