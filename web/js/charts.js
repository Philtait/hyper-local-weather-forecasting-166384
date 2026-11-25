/* Station page charts: hand-written SVG, no chart library, one chart at a time.
   A chart is drawn only when at least 3 of the last 7 days have data (js/station-logic.js); otherwise one calm
   sentence and a small table. Colours: rain blue, temperature brown, humidity teal. Labels sit on the chart. */
(function () {
  "use strict";
  var I = window.Imarika, t = I.t, esc = I.esc, S = window.ImarikaStationLogic;
  var NS = "http://www.w3.org/2000/svg";
  var C = { rain: "#2f6fb0", rainLight: "#cfe0f3", temp: "#8a5a3c", humid: "#0e7490", grid: "#d5e2da", text: "#33433b",
            missing: "#9aa3ad", full: "#1f7a4d", part: "#a7d7b9", none: "#ffffff", noneLine: "#6b7280", forest: "#0f3d2e" };
  var FONT = 16;

  function el(name, attrs, text) {
    var node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text != null) node.textContent = text;
    return node;
  }
  function label(svg, x, y, text, extra) {
    svg.appendChild(el("text", Object.assign({ x: x, y: y, "font-size": FONT, fill: C.text, "text-anchor": "middle",
      "font-family": "Lexend, sans-serif" }, extra || {}), text));
  }
  function mm(v) { return v >= 1 || v === 0 ? String(Math.round(v)) : "<1"; }
  function dayNum(iso) { return String(Number(iso.slice(8, 10))); }
  function known(v) { return typeof v === "number" && isFinite(v); }

  function frame(box, height, n) {
    var W = Math.max(280, Math.floor(box.clientWidth || 320));
    var svg = el("svg", { width: W, height: height, viewBox: "0 0 " + W + " " + height, class: "chart-svg", role: "img" });
    var left = 4, right = 4, slot = (W - left - right) / Math.max(1, n);
    return { svg: svg, W: W, H: height, slot: slot, x: function (i) { return left + slot * i + slot / 2; } };
  }
  /* Day numbers under the columns; on narrow screens every other day (first and last always) so they never touch. */
  function axisDays(f, days) {
    var every = f.slot < 30 ? 2 : 1;
    days.forEach(function (d, i) {
      if (i % every === 0 || i === days.length - 1) label(f.svg, f.x(i), f.H - 6, dayNum(d.date));
    });
  }

  function numbers(headers, rows, open) {
    var body = "<table><thead><tr>" + headers.map(function (h) { return '<th scope="col">' + esc(h) + "</th>"; }).join("") +
      "</tr></thead><tbody>" + rows.map(function (r) {
        return "<tr>" + r.map(function (c, i) { return i ? "<td>" + esc(c) + "</td>" : '<th scope="row">' + esc(c) + "</th>"; }).join("") + "</tr>";
      }).join("") + "</tbody></table>";
    return open ? '<div class="small-table">' + body + "</div>"
      : '<details class="chart-numbers"><summary>' + esc(t("station.show_numbers")) + "</summary>" + body + "</details>";
  }
  function host(slot, summary) {
    slot.innerHTML = '<p class="chart-summary">' + summary + '</p><div class="chart-box"></div>';
    return slot.querySelector(".chart-box");
  }
  function sparse(slot, summary, n, headers, rows) {
    slot.innerHTML = (summary ? '<p class="chart-summary">' + summary + "</p>" : "") +
      '<p class="calm">' + esc(t("station.sparse", { n: n })) + "</p>" + numbers(headers, rows, true);
  }
  function day(d) { return I.longDate(d.date); }

  /* ------------------------------------------------------------ rain: last 7 days and the forecast */
  function rain(slot, s) {
    var r = S.rainSummary(s);
    var summary = esc(t("station.rain_measured", { n: r.measuredDays, total: r.window, mm: r.totalMm })) +
      (r.forecastDays ? " " + esc(t("station.rain_forecast", { mm: r.forecastMm })) : "") +
      ' <span class="muted">' + esc(t("map.rain_window")) + ".</span>";
    var past = S.lastDays(s), next = S.hasForecast(s) ? s.forecast : [];
    var rowFor = function (d, kind) {
      return [day(d), t(kind === "past" ? "station.measured" : "station.forecast"), known(d.rain_mm) ? mm(d.rain_mm) + " " + t("rain.unit") : t("station.no_reading")];
    };
    var rows = past.map(function (d) { return rowFor(d, "past"); }).concat(next.map(function (d) { return rowFor(d, "next"); }));
    var headers = [t("station.day"), t("station.kind"), t("station.rain_col")];
    if (!S.chartable(s, "rain")) {   // the small table covers only the last 7 days; the forecast is on the first tab
      sparse(slot, summary, r.measuredDays, [t("station.day"), t("station.rain_col")], past.map(function (d) {
        return [day(d), known(d.rain_mm) ? mm(d.rain_mm) + " " + t("rain.unit") : t("station.no_reading")];
      }));
      return;
    }
    var days = past.map(function (d) { return { date: d.date, v: d.rain_mm, kind: "past" }; })
      .concat(next.map(function (d) { return { date: d.date, v: d.rain_mm, kind: "next" }; }));
    var box = host(slot, summary), f = frame(box, 250, days.length);
    var top = 50, bottom = f.H - 34;
    var max = Math.max(5, Math.max.apply(null, days.filter(function (d) { return known(d.v); }).map(function (d) { return d.v; }))) * 1.15;
    var y = function (v) { return bottom - (bottom - top) * v / max; };
    var bw = Math.min(30, f.slot * 0.62);
    f.svg.appendChild(el("line", { x1: 0, x2: f.W, y1: bottom, y2: bottom, stroke: C.grid }));
    days.forEach(function (d, i) {
      var x = f.x(i);
      if (!known(d.v)) {  // no reading: a short grey dash on the baseline, never a tall bar
        f.svg.appendChild(el("line", { x1: x - bw / 3, x2: x + bw / 3, y1: bottom - 3, y2: bottom - 3, stroke: C.missing,
          "stroke-width": 3, "stroke-linecap": "round" }));
        return;
      }
      var h = Math.max(d.v > 0 ? 2 : 0, bottom - y(d.v));
      f.svg.appendChild(el("rect", d.kind === "past"
        ? { x: x - bw / 2, y: bottom - h, width: bw, height: h, fill: C.rain, rx: 3 }
        : { x: x - bw / 2 + 1, y: bottom - h, width: bw - 2, height: h, fill: C.rainLight, stroke: C.rain, "stroke-width": 1.5, rx: 3 }));
      label(f.svg, x, bottom - h - 6, mm(d.v), { "font-weight": d.kind === "past" ? 600 : 400 });
    });
    // Direct labels for the two parts instead of a legend.
    if (past.length) label(f.svg, f.x(0) - f.slot / 2 + 2, 20, t("station.measured"), { "text-anchor": "start", "font-weight": 600, fill: C.forest });
    if (next.length) {
      var split = f.x(past.length) - f.slot / 2;
      f.svg.appendChild(el("line", { x1: split, x2: split, y1: 28, y2: bottom, stroke: C.forest, "stroke-dasharray": "4 4" }));
      label(f.svg, split + 6, 20, t("station.forecast"), { "text-anchor": "start", "font-weight": 600, fill: C.forest });
    }
    axisDays(f, days);
    box.appendChild(f.svg);
    slot.insertAdjacentHTML("beforeend", numbers(headers, rows, false));
  }

  /* ------------------------------------------------------------ temperature: daily range */
  function temperature(slot, s) {
    var past = S.lastDays(s), measured = S.daysWith(s, "temperature");
    var rows = past.map(function (d) {
      return [day(d), known(d.temp_min) && known(d.temp_max) ? t("station.range", { min: Math.round(d.temp_min), max: Math.round(d.temp_max) }) : t("station.no_reading")];
    });
    var headers = [t("station.day"), t("station.temp_col")];
    if (measured.length < S.MIN_DAYS) { sparse(slot, "", measured.length, headers, rows); return; }
    var lo = Math.min.apply(null, measured.map(function (d) { return d.temp_min; }));
    var hi = Math.max.apply(null, measured.map(function (d) { return d.temp_max; }));
    var box = host(slot, esc(t("station.temp_summary", { min: Math.round(lo), max: Math.round(hi) })));
    var f = frame(box, 250, past.length), top = 30, bottom = f.H - 56;
    var y = function (v) { return bottom - (bottom - top) * (v - (lo - 1)) / Math.max(2, hi - lo + 2); };
    var bw = Math.min(18, f.slot * 0.4);
    past.forEach(function (d, i) {
      var x = f.x(i);
      if (!(known(d.temp_min) && known(d.temp_max))) {
        f.svg.appendChild(el("line", { x1: x - bw / 2, x2: x + bw / 2, y1: bottom + 20, y2: bottom + 20, stroke: C.missing, "stroke-width": 3, "stroke-linecap": "round" }));
        return;
      }
      var y1 = y(d.temp_max), y2 = y(d.temp_min);
      f.svg.appendChild(el("rect", { x: x - bw / 2, y: y1, width: bw, height: Math.max(3, y2 - y1), rx: 4, fill: C.temp }));
      label(f.svg, x, y1 - 7, Math.round(d.temp_max), { fill: C.temp, "font-weight": 600 });
      label(f.svg, x, y2 + 19, Math.round(d.temp_min));
    });
    axisDays(f, past);
    box.appendChild(f.svg);
    slot.insertAdjacentHTML("beforeend", numbers(headers, rows, false));
  }

  /* ------------------------------------------------------------ humidity: daily average */
  function humidity(slot, s) {
    var past = S.lastDays(s), measured = S.daysWith(s, "humidity");
    var rows = past.map(function (d) { return [day(d), known(d.humidity_mean) ? Math.round(d.humidity_mean) + "%" : t("station.no_reading")]; });
    var headers = [t("station.day"), t("station.humid_col")];
    if (measured.length < S.MIN_DAYS) { sparse(slot, "", measured.length, headers, rows); return; }
    var vals = measured.map(function (d) { return d.humidity_mean; });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var box = host(slot, esc(t("station.humid_summary", { min: Math.round(lo), max: Math.round(hi) })));
    var f = frame(box, 210, past.length), top = 30, bottom = f.H - 36, a = Math.max(0, lo - 8), b = Math.min(100, hi + 8);
    var y = function (v) { return bottom - (bottom - top) * (v - a) / Math.max(1, b - a); };
    var path = "", pen = false;
    past.forEach(function (d, i) {   // the line only joins neighbouring days that both have a value
      if (!known(d.humidity_mean)) { pen = false; return; }
      path += (pen ? "L" : "M") + f.x(i).toFixed(1) + " " + y(d.humidity_mean).toFixed(1);
      pen = true;
    });
    f.svg.appendChild(el("path", { d: path, fill: "none", stroke: C.humid, "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    past.forEach(function (d, i) {
      if (!known(d.humidity_mean)) {
        f.svg.appendChild(el("line", { x1: f.x(i) - 6, x2: f.x(i) + 6, y1: bottom, y2: bottom, stroke: C.missing, "stroke-width": 3, "stroke-linecap": "round" }));
        return;
      }
      f.svg.appendChild(el("circle", { cx: f.x(i), cy: y(d.humidity_mean), r: 4.5, fill: C.humid }));
      label(f.svg, f.x(i), y(d.humidity_mean) - 10, Math.round(d.humidity_mean), { fill: C.humid, "font-weight": 600 });
    });
    axisDays(f, past);
    box.appendChild(f.svg);
    slot.insertAdjacentHTML("beforeend", numbers(headers, rows, false));
  }

  /* ------------------------------------------------------------ is it working: 14 squares */
  function working(slot, s, meta) {
    var days = S.lastDays(s, 14), w = S.workingSummary(s), full = (meta && meta.history_hours_per_day) || 17;
    var parts = [esc(t("station.working_summary", { n: w.sent, total: w.total }))];
    if (s.last_reading_utc) {
      parts.push(esc(t("station.working_last", { date: I.longDate(I.localDay(s.last_reading_utc)), time: I.clock(s.last_reading_utc) })));
    }
    var box = host(slot, parts.join(" "));
    var f = frame(box, 70, days.length), size = Math.min(26, f.slot - 4);
    days.forEach(function (d, i) {
      var n = d.readings_received || 0;
      f.svg.appendChild(el("rect", { x: f.x(i) - size / 2 + 1, y: 6, width: size - 2, height: size - 2, rx: 4,
        // "Most of the day": at least two thirds of the hours (the stations are off overnight).
        fill: n >= Math.round(full * 2 / 3) ? C.full : n > 0 ? C.part : C.none, stroke: n > 0 ? "none" : C.noneLine, "stroke-width": 1.5 }));
    });
    axisDays(f, days);
    box.appendChild(f.svg);
    slot.insertAdjacentHTML("beforeend", '<p class="caption">' + esc(t("station.working_key")) + "</p>" +
      numbers([t("station.day"), t("station.hours_col")], days.map(function (d) { return [day(d), String(d.readings_received || 0)]; }), false));
  }

  I.stationCharts = { rain: rain, temperature: temperature, humidity: humidity, working: working };
})();
