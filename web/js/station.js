/* Station page (station.html?id=113): latest readings, last 14 days, is it working. Tabs are keyboard
   accessible and kept in the URL hash (#latest, #days, #working). Reads only data/*.json. */
(function () {
  "use strict";
  var I = window.Imarika, t = I.t, esc = I.esc, icon = I.icon, M = window.ImarikaMapLogic, S = window.ImarikaStationLogic;
  var CHARTS = ["rain", "temperature", "humidity"];
  var state = { meta: null, stations: [], station: null, dataDay: null, tab: "latest", chart: "rain", drawn: {} };

  function $(id) { return document.getElementById(id); }
  function today() { return I.localDay(new Date().toISOString()); }
  function shiftDay(iso, days) {
    var p = iso.split("-").map(Number);
    return new Date(Date.UTC(p[0], p[1] - 1, p[2] + days)).toISOString().slice(0, 10);
  }
  function dayWord(iso) {
    if (iso === today()) return t("time.today");
    if (iso === shiftDay(today(), -1)) return t("time.yesterday");
    return I.longDate(iso);
  }
  function oneDecimal(n) { return Math.round(n * 10) / 10; }

  /* ------------------------------------------------------------ header */
  function header(s) {
    $("station-title").textContent = t("map.station_name", { area: s.area || "Busia", id: s.id });
    var code = M.stateOf(s, state.dataDay), last = s.last_reading_utc;
    $("station-status").innerHTML = '<span class="dot dot-' + code + '" aria-hidden="true"></span><span>' + esc(code === "off"
      ? (last ? t("map.status_line_off", { date: I.longDate(I.localDay(last)), time: I.clock(last) }) : t("map.state_off"))
      : t("map.status_line_on", { day: dayWord(I.localDay(last)), time: I.clock(last) })) + "</span>";
    document.title = $("station-title").textContent + ": Imarika";
  }

  /* ------------------------------------------------------------ latest readings tab */
  function uvWord(uv) {
    var r = Math.round(uv);
    return t("map." + (r <= 2 ? "uv_low" : r <= 5 ? "uv_moderate" : r <= 7 ? "uv_high" : r <= 10 ? "uv_very_high" : "uv_extreme"));
  }
  function afterDark(l) {
    if (Math.round(l.uv_index) !== 0) return false;
    var hm = I.clock(l.time_utc).split(":").map(Number), minutes = hm[0] * 60 + hm[1];
    return minutes < 390 || minutes > 1110;
  }
  function tile(name, labelText, value, opts) {
    opts = opts || {};
    return '<li class="tile">' + icon(name) + '<span class="tile-label">' + esc(labelText) + '</span><strong class="tile-value' +
      (opts.text ? " tile-value-text" : "") + '">' + esc(value) + "</strong>" +
      (opts.note ? '<span class="tile-note">' + esc(opts.note) + "</span>" : "") + "</li>";
  }

  /* Temperature, humidity, rain since 08:00, sun strength and air pressure, with units and the reading time. */
  function readings(s) {
    var l = s.latest;
    if (!s.active || !l || !l.time_utc) return "";
    var tiles = [], readToday = I.localDay(l.time_utc) === today();
    if (l.temperature_c != null) tiles.push(tile("thermometer", t("map.temperature"), Math.round(l.temperature_c) + "°C"));
    if (l.humidity_pct != null) tiles.push(tile("humidity", t("map.humidity"), Math.round(l.humidity_pct) + "%"));
    if (!readToday) tiles.push(tile("drop", t("map.rain_morning"), t("map.no_reading_today"), { text: true }));
    else if (l.rain_since_morning_mm != null) tiles.push(tile("drop", t("map.rain_morning"), oneDecimal(l.rain_since_morning_mm) + " " + t("rain.unit")));
    if (l.uv_index != null) tiles.push(tile("sun", t("map.sun"), Math.round(l.uv_index) + " (" + uvWord(l.uv_index) + ")", { note: afterDark(l) ? t("map.after_dark") : "" }));
    if (l.pressure_hpa != null) tiles.push(tile("info", t("map.pressure"), Math.round(l.pressure_hpa) + " " + t("map.hpa")));
    if (!tiles.length) return "";
    var day = I.localDay(l.time_utc), time = I.clock(l.time_utc);
    var hours = (Date.now() - new Date(l.time_utc).getTime()) / 3600000;
    var caption = hours > 6 ? '<p class="caption caption-stale">' + esc(t("map.stale", { date: I.longDate(day), time: time })) + "</p>"
      : '<p class="caption">' + esc(readToday ? t("map.updated_today", { time: time }) : t("map.updated_on", { date: I.longDate(day), time: time })) + "</p>";
    return '<ul class="tiles tiles-wide">' + tiles.join("") + "</ul>" + caption;
  }

  /* The outlook as one strip of compact day tiles that scrolls sideways on phones. */
  function outlook(s) {
    if (!s.active) return '<p class="panel-note">' + esc(t("map.stopped")) + "</p>";
    if (!S.hasForecast(s)) return '<p class="panel-note">' + esc(t("map.no_forecast")) + "</p>";
    var strip = s.forecast.map(function (d) {
      var name = I.weekday(d.date).slice(0, 3), num = Number(d.date.slice(8, 10));
      return '<li class="day-tile"><span class="day-tile-name">' + esc(name + " " + num) + '</span><span class="day-tile-rain">' +
        icon("drop") + "<span>" + esc(d.rain_mm != null ? (d.rain_mm >= 1 ? Math.round(d.rain_mm) : oneDecimal(d.rain_mm)) + " " +
        t("rain.unit") : t("map.no_value")) + "</span></span>" +
        '<span class="day-tile-temp">' + esc(d.temp_max != null ? t("station.up_to", { max: Math.round(d.temp_max) }) : t("map.no_value")) + "</span></li>";
    }).join("");
    return '<h2 class="section-label">' + esc(t("station.outlook")) + '</h2><ul class="day-strip" tabindex="0" aria-label="' +
      esc(t("station.outlook")) + '">' + strip + '</ul><p class="caption">' + esc(t("map.rain_window")) + "." +
      (s.flat ? " " + esc(t("map.outlook_note")) : "") + "</p>";
  }

  function others(s) {
    var list = state.stations.filter(function (o) { return o.area === s.area && o.id !== s.id; })
      .sort(function (a, b) { return a.id.localeCompare(b.id, "en", { numeric: true }); });
    if (!list.length) return "";
    return '<div class="others"><h2 class="section-label">' + esc(t("map.other_stations")) + '</h2><ul class="others-list">' +
      list.map(function (o) {
        var code = M.stateOf(o, state.dataDay);
        return '<li><a class="other-link" href="station.html?id=' + encodeURIComponent(o.id) + '"><span class="dot dot-' + code +
          '" aria-hidden="true"></span>' + esc(o.label) + "</a></li>";
      }).join("") + "</ul></div>";
  }

  /* ------------------------------------------------------------ tabs */
  function renderLatest(s) { $("panel-latest").innerHTML = readings(s) + outlook(s) + others(s); }

  function renderDays() {
    $("panel-days").innerHTML = '<div class="chart-switch" role="group" aria-label="' + esc(t("station.charts_label")) + '">' +
      CHARTS.map(function (c) {
        return '<button type="button" class="chart-btn" data-chart="' + c + '" aria-pressed="' + (c === state.chart) + '">' +
          esc(t("station.chart_" + c)) + "</button>";
      }).join("") + '</div><section class="chart" id="chart-slot" aria-live="polite"></section>';
    I.stationCharts[state.chart]($("chart-slot"), state.station);
  }

  function renderWorking() {
    $("panel-working").innerHTML = '<section class="chart" id="working-slot"></section>';
    I.stationCharts.working($("working-slot"), state.station, state.meta);
  }

  function show(tab, focus) {
    state.tab = S.TABS.indexOf(tab) >= 0 ? tab : "latest";
    S.TABS.forEach(function (name) {
      var button = $("tab-" + name), panel = $("panel-" + name), on = name === state.tab;
      button.setAttribute("aria-selected", String(on));
      button.tabIndex = on ? 0 : -1;
      panel.hidden = !on;
    });
    // Charts need a visible box to measure, so they are drawn when their tab is shown.
    if (state.tab === "days") renderDays();
    if (state.tab === "working") renderWorking();
    if (focus) $("tab-" + state.tab).focus();
    if (history.replaceState && location.hash !== "#" + state.tab) history.replaceState(null, "", "#" + state.tab);
  }

  function bindTabs() {
    $("station-tabs").addEventListener("click", function (e) {
      var b = e.target.closest("[role=tab]");
      if (b) show(b.getAttribute("data-tab"));
    });
    $("station-tabs").addEventListener("keydown", function (e) {
      var i = S.TABS.indexOf(state.tab), next = null;
      if (e.key === "ArrowRight") next = S.TABS[(i + 1) % S.TABS.length];
      else if (e.key === "ArrowLeft") next = S.TABS[(i + S.TABS.length - 1) % S.TABS.length];
      else if (e.key === "Home") next = S.TABS[0];
      else if (e.key === "End") next = S.TABS[S.TABS.length - 1];
      if (next) { e.preventDefault(); show(next, true); }
    });
    $("panel-days").addEventListener("click", function (e) {
      var b = e.target.closest(".chart-btn");
      if (b) { state.chart = b.getAttribute("data-chart"); renderDays(); }
    });
    window.addEventListener("hashchange", function () { show(S.tabFromHash(location.hash)); });
    var width = window.innerWidth;
    window.addEventListener("resize", function () {
      if (Math.abs(window.innerWidth - width) < 24) return;
      width = window.innerWidth;
      if (state.tab !== "latest") show(state.tab);
    });
  }

  /* ------------------------------------------------------------ start */
  I.ready.then(function (meta) {
    state.meta = meta;
    return I.getJSON("data/stations.json");
  }).then(function (res) {
    state.stations = res.stations;
    state.dataDay = M.dataDay(state.meta, state.stations);
    var id = new URLSearchParams(location.search).get("id");
    var s = state.stations.filter(function (o) { return o.id === id; })[0];
    if (!s) {
      $("station-title").textContent = t("station.not_found_title");
      $("station-status").textContent = t("station.not_found");
      $("station-tabs").hidden = true;
      return;
    }
    state.station = s;
    header(s);
    renderLatest(s);
    bindTabs();
    show(S.tabFromHash(location.hash));
  }).catch(function (err) { if (window.console) console.error(err); });
})();
