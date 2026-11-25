/* One rounding rule for rain and temperature, shared by every page and the SMS builder, so the same
   value never shows as "5.6 mm" in one place and "6 mm" in another.
   Rain: whole mm from 1 mm up, one decimal below 1 mm (0.96 rounds up to 1). Temperature: whole degrees.
   Totals: the sum of the daily values as shown, then rounded by the same rule.
   Used in the browser (window.ImarikaUnits) and in Node (require), including api/send-sms.js. */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ImarikaUnits = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function known(n) { return typeof n === "number" && isFinite(n); }

  /* Rain as a number: 0.43 -> 0.4, 0.96 -> 1, 5.6 -> 6. */
  function rain(mm) {
    if (!known(mm)) return null;
    var tenth = Math.round(mm * 10) / 10;
    return tenth >= 1 ? Math.round(mm) : tenth;
  }
  /* Rain as text without the unit: "0", "0.4", "6". */
  function rainText(mm) { var v = rain(mm); return v == null ? "" : String(v); }
  /* Whole degrees. */
  function temp(c) { return known(c) ? Math.round(c) : null; }
  /* A total that matches the daily numbers people see: add the shown values, then round the same way. */
  function rainTotal(values) {
    var sum = 0;
    (values || []).forEach(function (mm) { var v = rain(mm); if (v != null) sum += v; });
    return rain(Math.round(sum * 10) / 10);
  }

  return { rain: rain, rainText: rainText, temp: temp, rainTotal: rainTotal };
});
