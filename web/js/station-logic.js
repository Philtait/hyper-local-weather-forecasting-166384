/* Station page rules without the page: tabs, when a chart is honest, and the summary numbers.
   Used by js/station.js and js/charts.js in the browser (window.ImarikaStationLogic) and by the Node tests. */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ImarikaStationLogic = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var TABS = ["latest", "days", "working"];
  var MIN_DAYS = 3;          // never draw a chart of one or two points
  var WINDOW = 7;            // charts look at the last 7 days

  function tabFromHash(hash) {
    var name = String(hash || "").replace(/^#/, "");
    return TABS.indexOf(name) >= 0 ? name : TABS[0];
  }

  function lastDays(station, n) { return ((station && station.history) || []).slice(-(n || WINDOW)); }
  function known(value) { return typeof value === "number" && isFinite(value); }

  /* Days (of the last 7) that have a value for this chart. */
  function daysWith(station, kind) {
    return lastDays(station).filter(function (d) {
      if (kind === "rain") return known(d.rain_mm);
      if (kind === "temperature") return known(d.temp_min) && known(d.temp_max);
      if (kind === "humidity") return known(d.humidity_mean);
      return (d.readings_received || 0) > 0;
    });
  }

  /* A chart only when at least 3 of the last 7 days have data; otherwise one calm sentence and a table. */
  function chartable(station, kind) { return daysWith(station, kind).length >= MIN_DAYS; }

  function hasForecast(station) { return !!station && station.active === true && (station.forecast || []).length > 0; }

  /* "Rain was measured on 2 of the last 7 days (total 0 mm)": the total covers only the measured days. */
  function rainSummary(station) {
    var measured = daysWith(station, "rain");
    var total = measured.reduce(function (sum, d) { return sum + d.rain_mm; }, 0);
    var next = hasForecast(station) ? station.forecast.filter(function (d) { return known(d.rain_mm); }) : [];
    return {
      measuredDays: measured.length, window: lastDays(station).length, totalMm: Math.round(total),
      forecastDays: next.length, forecastMm: Math.round(next.reduce(function (sum, d) { return sum + d.rain_mm; }, 0))
    };
  }

  /* "Sent data on 4 of the last 14 days". */
  function workingSummary(station) {
    var days = lastDays(station, 14);
    return { sent: days.filter(function (d) { return (d.readings_received || 0) > 0; }).length, total: days.length };
  }

  return { TABS: TABS, MIN_DAYS: MIN_DAYS, WINDOW: WINDOW, tabFromHash: tabFromHash, lastDays: lastDays, daysWith: daysWith,
           chartable: chartable, hasForecast: hasForecast, rainSummary: rainSummary, workingSummary: workingSummary };
});
