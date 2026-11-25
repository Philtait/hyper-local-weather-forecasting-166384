/* Weather map rules without the map: marker state, default station, hash and alerts.
   Used by js/map.js in the browser (window.ImarikaMapLogic) and by the Node tests (require). */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ImarikaMapLogic = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var STATES = ["today", "recent", "off"];          // no more than three marker states
  var ALERTS = ["warning", "watch", "planting", "delay"];
  var EAT_OFFSET_MS = 3 * 3600 * 1000;              // Africa/Nairobi is UTC+3 all year

  /* Calendar day in Kenya time (YYYY-MM-DD) of an ISO UTC time. */
  function eatDay(isoUtc) {
    var ms = Date.parse(isoUtc);
    return isFinite(ms) ? new Date(ms + EAT_OFFSET_MS).toISOString().slice(0, 10) : null;
  }

  /* The day of the newest reading in the data: "today" for the markers means this day. */
  function dataDay(meta, stations) {
    var newest = (meta && meta.latest_reading_utc) ||
      (stations || []).map(function (s) { return s.last_reading_utc || ""; }).sort().pop();
    return newest ? eatDay(newest) : null;
  }

  /* today: sent data on the data's newest day; recent: sending data (last 2 days) but not that day; off: not reporting. */
  function stateOf(station, day) {
    if (!station || !station.active || !station.last_reading_utc) return "off";
    return eatDay(station.last_reading_utc) === day ? "today" : "recent";
  }

  function presentStates(stations, day) {
    return STATES.filter(function (code) {
      return (stations || []).some(function (s) { return stateOf(s, day) === code; });
    });
  }

  /* Default selection: the freshest reading, stations that are sending data first. */
  function freshest(stations) {
    return (stations || []).slice().sort(function (a, b) {
      return (Number(!!b.active) - Number(!!a.active)) ||
        String(b.last_reading_utc || "").localeCompare(String(a.last_reading_utc || ""));
    })[0] || null;
  }

  /* map.html#station=113 (and the older #station-113) select a station that exists. */
  function stationFromHash(hash, stations) {
    var m = /^#station[=-](\w+)$/.exec(String(hash || ""));
    if (!m) return null;
    return (stations || []).some(function (s) { return s.id === m[1]; }) ? m[1] : null;
  }

  /* Only a real alert earns a badge; "No alerts this week" and "Not reporting" never do. */
  function hasAlert(station) { return !!station && !!station.status && ALERTS.indexOf(station.status.code) >= 0; }

  return { STATES: STATES, ALERTS: ALERTS, eatDay: eatDay, dataDay: dataDay, stateOf: stateOf, presentStates: presentStates,
           freshest: freshest, stationFromHash: stationFromHash, hasAlert: hasAlert };
});
