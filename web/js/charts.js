/* Station charts for the Weather map result card: hand-written SVG, no chart library.
   Drawn at the card's real pixel width so text never shrinks below 14 px. Tap or focus a day to read it. */
(function () {
  "use strict";
  var I = window.Imarika, t = I.t, esc = I.esc;
  var NS = "http://www.w3.org/2000/svg";
  var C = { measured: "#2f6fb0", forecast: "#a9c6e8", heavy: "#b45309", heavyLight: "#f5cf9f", vheavy: "#b3261e",
            vheavyLight: "#f4b3ad", temp: "#8a5a3c", humid: "#0e7490", grid: "#d5e2da", text: "#4a5a52",
            full: "#1f7a4d", part: "#a7d7b9", none: "#f3f4f6", noneLine: "#6b7280" };
  var FONT = 14;

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
  function fullDay(iso) { return I.longDate(iso); }

  /* ------------------------------------------------------------ the 14-day series */
  function series(station) {
    var past = (station.history || []).slice(-7).map(function (d) {
      return { date: d.date, kind: "measured", rain: d.rain_mm, tmin: d.temp_min, tmax: d.temp_max };
    });
    var next = (station.active ? station.forecast : []).map(function (d) {
      return { date: d.date, kind: "forecast", rain: d.rain_mm, tmin: d.temp_min, tmax: d.temp_max };
    });
    return { days: past.concat(next), split: past.length && next.length ? past.length : -1 };
  }

  /* ------------------------------------------------------------ shared frame */
  function frame(box, height, n) {
    var W = Math.max(280, Math.floor(box.clientWidth || 320));
    var svg = el("svg", { width: W, height: height, viewBox: "0 0 " + W + " " + height, class: "chart-svg" });
    var left = 6, right = 6, slot = (W - left - right) / n;
    return { svg: svg, W: W, H: height, slot: slot, x: function (i) { return left + slot * i + slot / 2; } };
  }
  /* "Today" goes under the visitor's own date in Kenya time, and is left out when that date is not on the chart. */
  function todayIndex(days) {
    var today = I.localDay(new Date().toISOString());
    return days.map(function (d) { return d.date; }).indexOf(today);
  }
  function divider(f, split, top, bottom, days) {
    if (split >= 0) {
      var x = f.x(split) - f.slot / 2;
      f.svg.appendChild(el("line", { x1: x, x2: x, y1: top - 4, y2: bottom + 4, stroke: "#0f3d2e", "stroke-width": 1.5, "stroke-dasharray": "4 3" }));
      label(f.svg, x - 6, top - 8, t("charts.measured"), { "text-anchor": "end", "font-weight": 600 });
      label(f.svg, x + 6, top - 8, t("charts.forecast"), { "text-anchor": "start", "font-weight": 600 });
    }
    var i = days ? todayIndex(days) : -1;
    if (i >= 0) label(f.svg, f.x(i), f.H - 4, t("charts.today"), { "font-weight": 700, fill: "#0f3d2e" });
  }
  /* Day-of-month numbers under each column; charts with a Today marker leave a row below for it. */
  function axisDays(f, days, withToday) {
    days.forEach(function (d, i) { label(f.svg, f.x(i), f.H - (withToday ? 24 : 6), dayNum(d.date)); });
  }
  /* A transparent column per day: tap, click or focus shows that day's values in the readout. */
  function hits(f, days, readout, describe) {
    days.forEach(function (d, i) {
      var r = el("rect", { x: f.x(i) - f.slot / 2, y: 0, width: f.slot, height: f.H, fill: "transparent", tabindex: 0,
        role: "button", "aria-label": describe(d), class: "chart-hit" });
      function show() { readout.textContent = describe(d); }
      r.addEventListener("click", show);
      r.addEventListener("focus", show);
      f.svg.appendChild(r);
    });
  }
  function table(headers, rows) {
    return '<details class="chart-numbers"><summary>' + esc(t("charts.show_numbers")) + '</summary><table><thead><tr>' +
      headers.map(function (h) { return '<th scope="col">' + esc(h) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      rows.map(function (r) { return "<tr>" + r.map(function (c, i) { return i ? "<td>" + esc(c) + "</td>" : '<th scope="row">' + esc(c) + "</th>"; }).join("") + "</tr>"; }).join("") +
      "</tbody></table></details>";
  }
  function block(title, summary) {
    var wrap = document.createElement("section");
    wrap.className = "chart";
    wrap.innerHTML = "<h4>" + esc(title) + '</h4><p class="chart-summary">' + esc(summary) + '</p><div class="chart-box"></div>' +
      '<p class="chart-readout" aria-live="polite"></p>';
    return wrap;
  }
  function kindWord(d) { return t(d.kind === "measured" ? "charts.measured" : "charts.forecast"); }

  /* ------------------------------------------------------------ 1. rain */
  function rainChart(host, s, heavy, veryHeavy) {
    var data = series(s), days = data.days;
    if (!days.length) return;
    var known = days.filter(function (d) { return d.rain != null; });
    if (!known.length && data.split < 0 && s.active === false) return;
    var past = days.filter(function (d) { return d.kind === "measured"; }), next = days.filter(function (d) { return d.kind === "forecast"; });
    var sum = function (list) { return Math.round(list.reduce(function (a, d) { return a + (d.rain || 0); }, 0)); };
    var missing = past.filter(function (d) { return d.rain == null; }).length;
    var parts = [];
    if (past.length) parts.push(t("charts.rain_past", { mm: sum(past) }));
    if (next.length) parts.push(t("charts.rain_next", { mm: sum(next) }));
    if (missing) parts.push(t(missing === 1 ? "charts.no_reading_one" : "charts.no_reading_many", { n: missing }));
    parts.push(t("charts.rain_window"));
    var wrap = block(t("charts.rain_title"), parts.join(" "));
    host.appendChild(wrap);
    var f = frame(wrap.querySelector(".chart-box"), 250, days.length);
    var top = 46, bottom = f.H - 46;
    var max = Math.max(heavy * 1.4, Math.max.apply(null, known.map(function (d) { return d.rain; }).concat([0])) * 1.12);
    var y = function (v) { return bottom - (bottom - top) * v / max; };
    var defs = el("defs");
    var pattern = el("pattern", { id: "hatch-" + s.id, width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
    pattern.appendChild(el("rect", { width: 6, height: 6, fill: "#eef1f4" }));
    pattern.appendChild(el("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: "#9aa3ad", "stroke-width": 2 }));
    defs.appendChild(pattern);
    f.svg.appendChild(defs);
    f.svg.appendChild(el("line", { x1: 0, x2: f.W, y1: bottom, y2: bottom, stroke: C.grid }));
    var bw = Math.min(28, f.slot * 0.66);
    days.forEach(function (d, i) {
      var x = f.x(i) - bw / 2;
      if (d.rain == null) {
        var h = (bottom - top) * 0.62;
        f.svg.appendChild(el("rect", { x: x, y: bottom - h, width: bw, height: h, fill: "url(#hatch-" + s.id + ")", stroke: "#9aa3ad", rx: 2 }));
        label(f.svg, 0, 0, t("charts.no_reading"), { transform: "translate(" + (f.x(i) + 5) + "," + (bottom - 6) + ") rotate(-90)", "text-anchor": "start", fill: "#475569" });
        return;
      }
      var level = d.rain >= veryHeavy ? "v" : d.rain >= heavy ? "h" : "n";
      var solid = level === "v" ? C.vheavy : level === "h" ? C.heavy : C.measured;
      var light = level === "v" ? C.vheavyLight : level === "h" ? C.heavyLight : C.forecast;
      var h2 = Math.max(d.rain > 0 ? 2 : 0, bottom - y(d.rain));
      f.svg.appendChild(el("rect", d.kind === "measured"
        ? { x: x, y: bottom - h2, width: bw, height: h2, fill: solid, rx: 2 }
        : { x: x + 0.75, y: bottom - h2, width: bw - 1.5, height: h2, fill: light, stroke: solid, "stroke-width": 1.5, rx: 2 }));
      label(f.svg, f.x(i), bottom - h2 - 5, mm(d.rain), { fill: level === "n" ? C.text : solid, "font-weight": level === "n" ? 400 : 700 });
    });
    var yh = y(heavy);
    f.svg.appendChild(el("line", { x1: 0, x2: f.W, y1: yh, y2: yh, stroke: C.heavy, "stroke-width": 1.5, "stroke-dasharray": "6 4" }));
    // Put the line's label at an end where no bar or value label reaches up into it.
    var text = t("charts.heavy_line"), width = text.length * 8 + 8;
    function clear(x0, x1, lowest) {
      return days.every(function (d, i) {
        var cx = f.x(i);
        if (cx + bw / 2 < x0 || cx - bw / 2 > x1) return true;
        var tall = d.rain == null ? (bottom - top) * 0.62 : bottom - y(d.rain) + 22;
        return bottom - tall > lowest;
      });
    }
    var spot = null;
    [[yh - 5, yh - 2], [yh + 17, yh + 20]].some(function (row) {
      for (var x0 = f.W - width; x0 >= 0 && !spot; x0 -= 4) if (clear(x0, x0 + width, row[1])) spot = [x0 + 4, row[0]];
      return spot;
    });
    spot = spot || [f.W - width + 4, yh - 5];
    label(f.svg, spot[0], spot[1], text, { "text-anchor": "start", fill: C.heavy, "font-weight": 600,
      stroke: "#ffffff", "stroke-width": 4, "paint-order": "stroke" });
    divider(f, data.split, top - 10, bottom, days);
    axisDays(f, days, todayIndex(days) >= 0);
    var readout = wrap.querySelector(".chart-readout");
    hits(f, days, readout, function (d) {
      return fullDay(d.date) + ": " + (d.rain == null ? t("charts.no_reading") : kindWord(d) + ", " + t("rain." + I.rainClass(d.rain)) + ", " + mm(d.rain) + " " + t("rain.unit"));
    });
    wrap.querySelector(".chart-box").appendChild(f.svg);
    wrap.insertAdjacentHTML("beforeend", table([t("charts.day"), t("charts.type"), t("charts.rain_col")], days.map(function (d) {
      return [fullDay(d.date), kindWord(d), d.rain == null ? t("charts.no_reading") : mm(d.rain) + " " + t("rain.unit")];
    })));
  }

  /* ------------------------------------------------------------ 2. temperature */
  /* Measured days have a range (lowest and highest while the station was on); forecast days only a maximum. */
  function tempValue(d) {
    if (d.kind === "forecast") return d.tmax == null ? null : t("time.up_to", { max: Math.round(d.tmax) });
    return d.tmin == null || d.tmax == null ? null : t("charts.range", { min: Math.round(d.tmin), max: Math.round(d.tmax) });
  }
  function tempChart(host, s) {
    var data = series(s), days = data.days;
    var measured = days.filter(function (d) { return d.kind === "measured" && d.tmin != null && d.tmax != null; });
    var forecast = days.filter(function (d) { return d.kind === "forecast" && d.tmax != null; });
    if (!measured.length && !forecast.length) return;
    var values = [];
    measured.forEach(function (d) { values.push(d.tmin, d.tmax); });
    forecast.forEach(function (d) { values.push(d.tmax); });
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    var parts = [];
    if (measured.length) {
      parts.push(t("charts.temp_measured", { min: Math.round(Math.min.apply(null, measured.map(function (d) { return d.tmin; }))),
                                             max: Math.round(Math.max.apply(null, measured.map(function (d) { return d.tmax; }))) }));
    }
    if (forecast.length) parts.push(t("charts.temp_forecast", { max: Math.round(Math.max.apply(null, forecast.map(function (d) { return d.tmax; }))) }));
    var wrap = block(t("charts.temp_title"), parts.join(" "));
    host.appendChild(wrap);
    var f = frame(wrap.querySelector(".chart-box"), 260, days.length);
    var top = 50, bottom = f.H - 68;
    var y = function (v) { return bottom - (bottom - top) * (v - (lo - 1)) / Math.max(2, (hi + 1) - (lo - 1)); };
    var bw = Math.min(16, f.slot * 0.4);
    days.forEach(function (d, i) {
      if (d.kind === "forecast") {
        if (d.tmax == null) { label(f.svg, f.x(i), (top + bottom) / 2, "–"); return; }
        var ym = y(d.tmax);
        f.svg.appendChild(el("line", { x1: f.x(i) - bw / 2 - 2, x2: f.x(i) + bw / 2 + 2, y1: ym, y2: ym, stroke: C.temp, "stroke-width": 4, "stroke-linecap": "round" }));
        label(f.svg, f.x(i), ym - 9, Math.round(d.tmax));
        return;
      }
      if (d.tmin == null || d.tmax == null) {
        label(f.svg, f.x(i), (top + bottom) / 2, "–");
        return;
      }
      var y1 = y(d.tmax), y2 = y(d.tmin);
      f.svg.appendChild(el("rect", { x: f.x(i) - bw / 2, y: y1, width: bw, height: Math.max(2, y2 - y1), rx: 3, fill: C.temp }));
      label(f.svg, f.x(i), y1 - 6, Math.round(d.tmax));
      label(f.svg, f.x(i), y2 + 17, Math.round(d.tmin));
    });
    divider(f, data.split, top - 18, bottom + 18, days);
    axisDays(f, days, todayIndex(days) >= 0);
    hits(f, days, wrap.querySelector(".chart-readout"), function (d) {
      var v = tempValue(d);
      return fullDay(d.date) + ": " + (v == null ? t("charts.no_reading") : kindWord(d) + ", " + v);
    });
    wrap.querySelector(".chart-box").appendChild(f.svg);
    wrap.insertAdjacentHTML("beforeend", table([t("charts.day"), t("charts.type"), t("charts.temp_col")], days.map(function (d) {
      var v = tempValue(d);
      return [fullDay(d.date), kindWord(d), v == null ? t("charts.no_reading") : v];
    })));
  }

  /* ------------------------------------------------------------ 3. humidity */
  function humidityChart(host, s) {
    var days = (s.history || []).slice(-7);
    var known = days.filter(function (d) { return d.humidity_mean != null; });
    if (!known.length) return;
    var vals = known.map(function (d) { return d.humidity_mean; });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var wrap = block(t("charts.humid_title"), t("charts.humid_summary", { min: Math.round(lo), max: Math.round(hi) }));
    host.appendChild(wrap);
    var f = frame(wrap.querySelector(".chart-box"), 170, days.length);
    var top = 24, bottom = f.H - 30, a = Math.max(0, lo - 8), b = Math.min(100, hi + 8);
    var y = function (v) { return bottom - (bottom - top) * (v - a) / Math.max(1, b - a); };
    var path = "", pen = false, last = -1;
    days.forEach(function (d, i) {
      if (d.humidity_mean == null) { pen = false; return; }
      path += (pen ? "L" : "M") + f.x(i).toFixed(1) + " " + y(d.humidity_mean).toFixed(1);
      pen = true; last = i;
    });
    f.svg.appendChild(el("path", { d: path, fill: "none", stroke: C.humid, "stroke-width": 2.5, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    days.forEach(function (d, i) {
      if (d.humidity_mean != null) f.svg.appendChild(el("circle", { cx: f.x(i), cy: y(d.humidity_mean), r: i === last ? 5 : 3.5, fill: C.humid }));
    });
    if (last >= 0) {
      // Put the label on the side away from the incoming line: below it if the line comes down, above if it goes up.
      var prev = days.slice(0, last).filter(function (d) { return d.humidity_mean != null; }).pop();
      var down = prev && prev.humidity_mean > days[last].humidity_mean;
      label(f.svg, f.x(last) - 8, y(days[last].humidity_mean) + (down ? 22 : -12), Math.round(days[last].humidity_mean) + "%",
            { "text-anchor": "end", fill: C.humid, "font-weight": 700 });
    }
    axisDays(f, days, false);
    hits(f, days, wrap.querySelector(".chart-readout"), function (d) {
      return fullDay(d.date) + ": " + (d.humidity_mean == null ? t("charts.no_reading") : t("map.humidity") + " " + Math.round(d.humidity_mean) + "%");
    });
    wrap.querySelector(".chart-box").appendChild(f.svg);
    wrap.insertAdjacentHTML("beforeend", table([t("charts.day"), t("map.humidity")], days.map(function (d) {
      return [fullDay(d.date), d.humidity_mean == null ? t("charts.no_reading") : Math.round(d.humidity_mean) + "%"];
    })));
  }

  /* ------------------------------------------------------------ 4. is this station working? */
  function workingChart(host, s, fullHours) {
    var days = s.history || [];
    if (!days.length) return;
    var parts = [];
    if (s.last_reading_utc) {
      parts.push(t("charts.last_reading", { date: I.longDate(I.localDay(s.last_reading_utc)), time: I.clock(s.last_reading_utc), ago: I.ago(s.last_reading_utc) }));
    }
    if (s.days_with_data_14d != null) parts.push(t("charts.coverage", { n: s.days_with_data_14d, total: days.length }));
    var wrap = block(t("charts.working_title"), parts.join(" "));
    host.appendChild(wrap);
    var f = frame(wrap.querySelector(".chart-box"), 64, days.length);
    var size = Math.min(22, f.slot - 4);
    days.forEach(function (d, i) {
      var n = d.readings_received || 0;
      f.svg.appendChild(el("rect", { x: f.x(i) - size / 2 + 0.75, y: 8, width: size - 1.5, height: size - 1.5, rx: 3,
        fill: n >= fullHours ? C.full : n > 0 ? C.part : C.none, stroke: n > 0 ? "none" : C.noneLine,
        "stroke-dasharray": n > 0 ? "" : "3 2", "stroke-width": 1.2 }));
    });
    axisDays(f, days, false);
    var key = '<p class="chart-key">' + [["full", "charts.key_full"], ["part", "charts.key_part"], ["none", "charts.key_none"]].map(function (k) {
      return '<span class="key-item"><span class="key-sq key-' + k[0] + '"></span>' + esc(t(k[1])) + "</span>";
    }).join("") + "</p>";
    hits(f, days, wrap.querySelector(".chart-readout"), function (d) {
      var n = d.readings_received || 0;
      return fullDay(d.date) + ": " + t(n >= fullHours ? "charts.key_full" : n > 0 ? "charts.key_part" : "charts.key_none");
    });
    wrap.querySelector(".chart-box").appendChild(f.svg);
    wrap.querySelector(".chart-box").insertAdjacentHTML("afterend", key);
    wrap.insertAdjacentHTML("beforeend", table([t("charts.day"), t("charts.hours_col")], days.map(function (d) {
      return [fullDay(d.date), String(d.readings_received || 0)];
    })));
  }

  /* ------------------------------------------------------------ public */
  function render(slot, station, meta) {
    slot.innerHTML = "";
    var host = document.createElement("div");
    host.className = "charts";
    host.innerHTML = "<h3>" + esc(t("charts.title")) + "</h3>";
    slot.appendChild(host);
    var heavy = meta.thresholds.heavy_rain_mm, veryHeavy = meta.thresholds.very_heavy_rain_mm;
    var fullHours = meta.history_hours_per_day || 18;
    if (station.active) {
      rainChart(host, station, heavy, veryHeavy);
      tempChart(host, station);
      humidityChart(host, station);
      workingChart(host, station, fullHours);
    } else {
      workingChart(host, station, fullHours);
      var past = (station.history || []).some(function (d) { return d.readings_received > 0; });
      if (past) { rainChart(host, station, heavy, veryHeavy); tempChart(host, station); humidityChart(host, station); }
    }
    if (host.children.length === 1) slot.innerHTML = "";
  }

  var last = { slot: null, station: null, meta: null, width: 0 };
  window.addEventListener("resize", function () {
    if (!last.slot || !last.slot.isConnected) return;
    var w = last.slot.clientWidth;
    if (Math.abs(w - last.width) < 8) return;
    last.width = w;
    render(last.slot, last.station, last.meta);
  });

  I.charts = {
    render: function (slot, station, meta) {
      last = { slot: slot, station: station, meta: meta, width: slot.clientWidth };
      render(slot, station, meta);
    }
  };
})();
