// node --test tests/node/map_logic.test.js   (no network)
// Weather map rules: marker states, default station, hash, alert badges; checked on the real site data.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const M = require("../../web/js/map-logic.js");

const DATA = path.join(__dirname, "../../web/data");
const stations = JSON.parse(fs.readFileSync(path.join(DATA, "stations.json"), "utf8")).stations;
const meta = JSON.parse(fs.readFileSync(path.join(DATA, "meta.json"), "utf8"));

test("no more than three marker states, and all three appear in the real data", () => {
  assert.deepEqual(M.STATES, ["today", "recent", "off"]);
  const day = M.dataDay(meta, stations);
  assert.deepEqual(M.presentStates(stations, day), ["today", "recent", "off"]);
  for (const s of stations) {
    const code = M.stateOf(s, day);
    assert.ok(M.STATES.includes(code));
    if (!s.active) assert.equal(code, "off");
    if (code === "today") assert.equal(M.eatDay(s.last_reading_utc), day);
    if (code === "recent") assert.ok(s.active && M.eatDay(s.last_reading_utc) < day);
  }
  const counts = { today: 0, recent: 0, off: 0 };
  for (const s of stations) counts[M.stateOf(s, day)] += 1;
  assert.equal(counts.today + counts.recent, stations.filter((s) => s.active).length);
  assert.equal(counts.today + counts.recent, meta.counts.stations_reporting);
});

test("the marker day is the Kenya-time day of the newest reading", () => {
  assert.equal(M.eatDay("2026-11-25T13:35:58Z"), "2026-11-25");
  assert.equal(M.eatDay("2026-11-24T21:30:00Z"), "2026-11-25");  // 00:30 EAT
  assert.equal(M.dataDay({ latest_reading_utc: "2026-11-24T20:59:00Z" }, []), "2026-11-24");
  assert.equal(M.dataDay({}, [{ last_reading_utc: "2026-11-23T10:00:00Z" }, { last_reading_utc: "2026-11-25T10:00:00Z" }]), "2026-11-25");
});

test("the default station is the one with the freshest reading", () => {
  const first = M.freshest(stations);
  const newest = stations.filter((s) => s.active).map((s) => s.last_reading_utc).sort().pop();
  assert.equal(first.last_reading_utc, newest);
  assert.ok(first.active);
  assert.equal(M.freshest([{ id: "a", active: false, last_reading_utc: "2026-11-26T00:00:00Z" },
                           { id: "b", active: true, last_reading_utc: "2026-11-20T00:00:00Z" }]).id, "b");
});

test("map.html#station=113 selects that station; unknown or malformed hashes do not", () => {
  const id = stations[0].id;
  assert.equal(M.stationFromHash("#station=" + id, stations), id);
  assert.equal(M.stationFromHash("#station-" + id, stations), id);   // the older form still works
  assert.equal(M.stationFromHash("#station=999999", stations), null);
  assert.equal(M.stationFromHash("#station=", stations), null);
  assert.equal(M.stationFromHash("#map", stations), null);
  assert.equal(M.stationFromHash("", stations), null);
});

test("the list shows an alert badge only for a real alert, never 'No alerts' or 'Not reporting'", () => {
  for (const code of ["none", "offline"]) assert.equal(M.hasAlert({ status: { code } }), false);
  for (const code of ["warning", "watch", "planting", "delay"]) assert.equal(M.hasAlert({ status: { code } }), true);
  const badges = stations.filter(M.hasAlert).length;
  assert.equal(badges, stations.filter((s) => ["warning", "watch", "planting", "delay"].includes(s.status.code)).length);
  assert.ok(badges < stations.length, "badges are not repeated on every row");
});

test("map.js uses these rules, draws no clusters and builds no per-row 'No alerts' badge", () => {
  const source = fs.readFileSync(path.join(__dirname, "../../web/js/map.js"), "utf8");
  for (const call of ["M.stateOf", "M.freshest", "M.stationFromHash", "M.hasAlert", "M.presentStates"]) assert.ok(source.includes(call), call);
  assert.ok(!/clusterMarker|CLUSTER_PX|mk-count/.test(source), "no clustering");
  assert.ok(!/basemaps\.cartocdn/.test(source), "the CARTO style needs a key and is not used");
  assert.match(source, /hasAlert\(s\) \? I\.badge/);
  const page = fs.readFileSync(path.join(__dirname, "../../web/map.html"), "utf8");
  assert.ok(page.indexOf("js/map-logic.js") > 0 && page.indexOf("js/map-logic.js") < page.indexOf("js/map.js"));
});
