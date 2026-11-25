/* Weather map page: one status line, area chips, a Map | List switch and one selected-station panel.
   Reads only data/*.json. Three marker states, measured against the newest reading in the data:
   sent data on that day ("today"), sent data in the last 2 days but not that day, not reporting.
   Words such as "today" follow the visitor's own clock; otherwise the date is shown. */
(function () {
  "use strict";
  var I = window.Imarika, t = I.t, esc = I.esc, icon = I.icon, M = window.ImarikaMapLogic, U = window.ImarikaUnits;
  // OpenStreetMap, greyed and lightened in CSS, with our own area labels on top. (CARTO's label-free style was
  // tried: it now draws "API key required" over the tiles, so it is not used.) Offline: boundaries only.
  var BASEMAPS = [
    { url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", subdomains: "", cls: "tiles-osm",
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }
  ];
  var AREA_STYLE = { color: "#1f5f3f", weight: 1, opacity: 0.75, fillColor: "#1f7a4d", fillOpacity: 0.04 };
  var AREA_CHOSEN = { color: "#0f3d2e", weight: 2, opacity: 0.9, fillColor: "#1f7a4d", fillOpacity: 0.1 };
  var COUNTY_STYLE = { color: "#0f3d2e", weight: 1.25, opacity: 0.6, fill: false };
  var STATES = M.STATES;
  var state = { meta: null, stations: [], areas: [], area: "all", selected: null, map: null, layer: null,
                areaLayers: {}, view: "map", dataDay: null, sheetOpen: false, fitted: false };

  function $(id) { return document.getElementById(id); }
  function byId(id) { return state.stations.filter(function (s) { return s.id === id; })[0]; }
  function reduceMotion() { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
  function phone() { return window.matchMedia("(max-width: 760px)").matches; }
  function sheet() { return window.matchMedia("(max-width: 1000px)").matches; }

  /* ------------------------------------------------------------ days and states */
  function today() { return I.localDay(new Date().toISOString()); }
  function shiftDay(iso, days) {
    var p = iso.split("-").map(Number), d = new Date(Date.UTC(p[0], p[1] - 1, p[2] + days));
    return d.toISOString().slice(0, 10);
  }
  /* "today", "yesterday" or "Wed 25 Nov", by the visitor's clock. */
  function dayWord(isoDate) {
    if (isoDate === today()) return t("time.today");
    if (isoDate === shiftDay(today(), -1)) return t("time.yesterday");
    return I.longDate(isoDate);
  }
  function capital(text) { return text.charAt(0).toUpperCase() + text.slice(1); }

  function stateOf(s) { return M.stateOf(s, state.dataDay); }
  function stateLabel(code) {
    return code === "today" ? t("map.state_today", { day: dayWord(state.dataDay) }) : t("map.state_" + code);
  }
  function shortState(code) {
    return code === "today" ? capital(dayWord(state.dataDay)) : code === "recent" ? t("map.short_recent") : t("map.state_off");
  }
  function hasAlert(s) { return M.hasAlert(s); }
  function stationName(s) { return t("map.station_name", { area: s.area || "Busia", id: s.id }); }
  function dot(code) { return '<span class="dot dot-' + code + '" aria-hidden="true"></span>'; }

  function freshest(list) { return M.freshest(list); }
  function visible() {
    return state.stations.filter(function (s) { return state.area === "all" || s.area === state.area; });
  }

  /* ------------------------------------------------------------ panel */
  /* Standard UV index categories (WHO): 0-2 Low, 3-5 Moderate, 6-7 High, 8-10 Very high, 11+ Extreme. */
  function uvWord(uv) {
    var r = Math.round(uv);
    return t("map." + (r <= 2 ? "uv_low" : r <= 5 ? "uv_moderate" : r <= 7 ? "uv_high" : r <= 10 ? "uv_very_high" : "uv_extreme"));
  }

  function tile(name, label, value, opts) {
    opts = opts || {};
    return '<li class="tile">' + icon(name) + '<span class="tile-label">' + esc(label) + '</span><strong class="tile-value' +
      (opts.text ? " tile-value-text" : "") + '">' + esc(value) + (opts.sub ? ' <span class="tile-sub">' + esc(opts.sub) + "</span>" : "") + "</strong>" +
      (opts.note ? '<span class="tile-note">' + esc(opts.note) + "</span>" : "") + "</li>";
  }

  /* The sun sensor reads 0 at night: say so, so a 0 is not mistaken for a fault. */
  function afterDark(latest) {
    if (I.round(latest.uv_index) !== 0) return false;
    var hm = I.clock(latest.time_utc).split(":").map(Number), minutes = hm[0] * 60 + hm[1];
    return minutes < 6 * 60 + 30 || minutes > 18 * 60 + 30;
  }

  /* Four tiles: temperature, humidity, rain since 08:00, sun strength. Rain since 08:00 belongs to today only:
     a reading from an earlier day shows "No reading today" instead of that day's total. */
  function readings(latest) {
    if (!latest || !latest.time_utc) return "";
    var tiles = [], readToday = I.localDay(latest.time_utc) === today();
    if (latest.temperature_c != null) tiles.push(tile("thermometer", t("map.temperature"), U.temp(latest.temperature_c) + "°C"));
    if (latest.humidity_pct != null) tiles.push(tile("humidity", t("map.humidity"), I.round(latest.humidity_pct) + "%"));
    if (!readToday) tiles.push(tile("drop", t("map.rain_morning"), t("map.no_reading_today"), { text: true }));
    else if (latest.rain_since_morning_mm != null) {
      tiles.push(tile("drop", t("map.rain_morning"), U.rainText(latest.rain_since_morning_mm) + " " + t("rain.unit")));
    }
    if (latest.uv_index != null) {
      tiles.push(tile("sun", t("map.sun"), I.round(latest.uv_index),
                      { sub: uvWord(latest.uv_index), note: afterDark(latest) ? t("map.after_dark") : "" }));
    }
    if (!tiles.length) return "";
    var day = I.localDay(latest.time_utc), time = I.clock(latest.time_utc);
    var hours = (Date.now() - new Date(latest.time_utc).getTime()) / 3600000;
    var caption = hours > 6
      ? '<p class="caption caption-stale">' + esc(t("map.stale", { date: I.longDate(day), time: time })) + "</p>"
      : '<p class="caption">' + esc(day === today() ? t("map.updated_today", { time: time })
                                                       : t("map.updated_on", { date: I.longDate(day), time: time })) + "</p>";
    return '<h3 class="panel-label">' + esc(t("map.latest")) + '</h3><ul class="tiles">' + tiles.join("") + "</ul>" + caption;
  }

  function outlook(s) {
    if (!s.active) return '<p class="panel-note">' + esc(t("map.stopped")) + "</p>";
    var day = (s.forecast || [])[0];
    if (!day) return '<p class="panel-note">' + esc(t("map.no_forecast")) + "</p>";
    var line = t("map.outlook_line", { day: I.longDate(day.date),
      mm: day.rain_mm != null ? U.rainText(day.rain_mm) : t("map.no_value"), max: day.temp_max != null ? U.temp(day.temp_max) : "-" });
    return '<h3 class="panel-label">' + esc(t("map.outlook")) + '</h3><p class="outlook-line">' + icon("drop") + "<span>" +
      esc(line) + '</span></p><p class="caption">' + esc(t("map.rain_window")) + (s.flat ? ". " + esc(t("map.outlook_note")) : ".") + "</p>";
  }

  function renderPanel(s) {
    var code = stateOf(s), last = s.last_reading_utc;
    var status = code === "off"
      ? (last ? t("map.status_line_off", { date: I.longDate(I.localDay(last)), time: I.clock(last) }) : t("map.state_off"))
      : t("map.status_line_on", { day: dayWord(I.localDay(last)), time: I.clock(last) });
    var html = '<div class="panel-head">' + dot(code) + '<div class="panel-title"><h2 id="panel-title">' + esc(stationName(s)) +
      '</h2><p class="panel-status">' + esc(status) + "</p></div>" +
      '<button type="button" class="sheet-toggle" aria-controls="panel-body" aria-expanded="' + state.sheetOpen + '">' +
      '<span class="visually-hidden">' + esc(t(state.sheetOpen ? "map.sheet_close" : "map.sheet_open")) + "</span>" + icon("arrow") + "</button></div>" +
      '<div class="panel-body" id="panel-body">' +
      (hasAlert(s) ? '<div class="panel-alert">' + I.badge(s.status.code) + "<p>" + esc(s.what_to_do) + "</p></div>" : "") +
      (s.active ? readings(s.latest) : "") + outlook(s) +
      '<a class="btn btn-primary details-btn" href="station.html?id=' + encodeURIComponent(s.id) + '">' + icon("arrow") +
      "<span>" + esc(t("map.details")) + "</span></a></div>";
    var panel = $("panel");
    panel.innerHTML = html;
    panel.classList.toggle("is-open", state.sheetOpen);
  }

  /* ------------------------------------------------------------ list */
  function lastCell(s) {
    if (!s.last_reading_utc) return t("map.no_value");
    var day = I.localDay(s.last_reading_utc);
    return day === today() ? I.clock(s.last_reading_utc) : shortDate(day);
  }
  /* "23 Nov" for the compact list. */
  function shortDate(isoDate) {
    var parts = I.longDate(isoDate).split(" ");
    return parts[1] + " " + parts[2].slice(0, 3);
  }
  function rainCell(s) {
    var l = s.latest;
    if (!l || l.rain_since_morning_mm == null || !l.time_utc || I.localDay(l.time_utc) !== state.dataDay) return "–";
    return U.rainText(l.rain_since_morning_mm) + " " + t("rain.unit");
  }

  /* A data cell carries its column name: hidden on wide screens (the table head shows it), shown small on phones. */
  function cell(head, value) {
    return '<td class="cell-data"><span class="cell-label">' + esc(head) + '<span class="visually-hidden">: </span></span>' +
      "<span>" + esc(value) + "</span></td>";
  }

  function renderList() {
    var rows = visible().slice().sort(function (a, b) {
      return (a.area || "").localeCompare(b.area || "") || a.id.localeCompare(b.id, "en", { numeric: true });
    });
    var rainHead = state.dataDay === today() ? t("map.col_rain") : t("map.col_rain_on", { day: I.longDate(state.dataDay) });
    var heads = [t("map.col_station"), t("map.col_area"), t("map.col_status"), t("map.col_last"), t("map.col_temp"), rainHead];
    $("station-list").innerHTML = '<table class="station-table"><thead><tr>' + heads.map(function (h) {
      return '<th scope="col">' + esc(h) + "</th>";
    }).join("") + "</tr></thead><tbody>" + rows.map(function (s) {
      var code = stateOf(s), l = s.latest;
      var temp = l && l.temperature_c != null && s.active ? U.temp(l.temperature_c) + "°C" : "–";
      return '<tr data-station="' + esc(s.id) + '"' + (s.id === state.selected ? ' class="is-selected"' : "") + ">" +
        '<th scope="row"><button type="button" class="row-btn" data-station="' + esc(s.id) + '" aria-pressed="' +
        (s.id === state.selected) + '">' + esc(s.label) + '<span class="row-area">' + esc(s.area || "") + "</span></button>" +
        (hasAlert(s) ? I.badge(s.status.code) : "") + "</th>" +
        '<td class="cell-area">' + esc(s.area || "") + "</td>" +
        '<td class="cell-status">' + dot(code) + "<span>" + esc(shortState(code)) + "</span></td>" +
        cell(heads[3], lastCell(s)) + cell(heads[4], temp) + cell(heads[5], rainCell(s)) + "</tr>";
    }).join("") + "</tbody></table>";
  }

  /* ------------------------------------------------------------ chips, legend, status line */
  function renderChips() {
    $("area-chips").innerHTML = ["all"].concat(state.areas).map(function (area) {
      return '<button type="button" class="chip" data-area="' + esc(area) + '" aria-pressed="' + (area === state.area) + '">' +
        esc(area === "all" ? t("map.all") : area) + "</button>";
    }).join("");
  }

  function renderLegend() {
    $("legend").innerHTML = M.presentStates(state.stations, state.dataDay).map(function (c) {
      return "<li>" + dot(c) + "<span>" + esc(stateLabel(c)) + "</span></li>";
    }).join("");
  }

  function renderStatus() {
    var c = state.meta.counts || {}, when = state.meta.latest_reading_utc;
    $("map-status").textContent = t("map.status_line", { n: c.stations_reporting, total: c.stations_total,
      when: when ? t("time.date_time", { date: dayWord(I.localDay(when)), time: I.clock(when) }) : t("map.no_value") });
  }

  /* ------------------------------------------------------------ selection, area, view */
  function select(id, opts) {
    var s = byId(id);
    if (!s) return;
    opts = opts || {};
    state.selected = id;
    if (opts.open) state.sheetOpen = true;
    renderPanel(s);
    renderList();
    drawMarkers();
    if (opts.pan && state.map && s.lat != null && state.view === "map") {
      state.map.panTo([s.lat, s.lon], { animate: !reduceMotion() });
    }
    if (opts.open && !sheet()) $("panel").scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "nearest" });
    if (history.replaceState) history.replaceState(null, "", "#station=" + id);
  }

  function chooseArea(area) {
    state.area = area;
    renderChips();
    renderList();
    Object.keys(state.areaLayers).forEach(function (name) {
      state.areaLayers[name].setStyle(name === area ? AREA_CHOSEN : AREA_STYLE);
    });
    var current = byId(state.selected);
    if (area !== "all" && (!current || current.area !== area)) {
      var first = freshest(visible());
      if (first) select(first.id);
    }
    fitView(true);
  }

  function setView(view) {
    state.view = view;
    $("map-view").hidden = view !== "map";
    $("list-view").hidden = view !== "list";
    Array.prototype.forEach.call(document.querySelectorAll(".view-btn"), function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-view") === view));
    });
    if (view === "map" && state.map) {
      state.map.invalidateSize();
      if (!state.fitted) fitView(false);
    }
  }

  /* ------------------------------------------------------------ map */
  function fitView(animate) {
    var map = state.map;
    if (!map || state.view !== "map") return;
    var layer = state.area !== "all" && state.areaLayers[state.area];
    var placed = visible().filter(function (s) { return s.lat != null && s.lon != null; });
    var bounds = layer ? layer.getBounds() : placed.length ? L.latLngBounds(placed.map(function (s) { return [s.lat, s.lon]; })).pad(0.12) : null;
    if (!bounds) { map.setView([0.45, 34.2], 10); return; }
    if (animate && !reduceMotion()) map.flyToBounds(bounds, { padding: [24, 24], maxZoom: 13 });
    else map.fitBounds(bounds, { padding: [24, 24], maxZoom: 13 });
    state.fitted = true;
  }

  function addTiles(map, index) {
    var spec = BASEMAPS[index];
    var box = map.getContainer();
    if (!spec || !navigator.onLine) {
      box.classList.remove("tiles-osm");
      $("map-note").textContent = t("map.tiles_offline");
      $("map-note").hidden = false;
      return;
    }
    var loaded = 0, failed = 0, done = false;
    var tiles = L.tileLayer(spec.url, { maxZoom: 18, subdomains: spec.subdomains || "abc", attribution: spec.attribution });
    function next() {
      if (done) return;
      done = true;
      map.removeLayer(tiles);
      addTiles(map, index + 1);
    }
    tiles.on("tileload", function () { loaded += 1; });
    tiles.on("tileerror", function () { failed += 1; if (failed >= 4 && loaded === 0) next(); });
    box.classList.add(spec.cls);
    tiles.addTo(map);
    setTimeout(function () { if (loaded === 0) next(); }, 9000);
  }

  function initMap(geo) {
    var map = L.map("map", { scrollWheelZoom: false, zoomSnap: 0.5, minZoom: 8, maxZoom: 18 });
    state.map = map;
    map.attributionControl.setPrefix(false);
    map.attributionControl.addAttribution("Boundaries: geoBoundaries");
    map.on("focus", function () { map.scrollWheelZoom.enable(); });
    map.on("blur", function () { map.scrollWheelZoom.disable(); });
    (geo && geo.features || []).forEach(function (f) {
      var level = f.properties.level;
      if (level !== "county" && level !== "sub-county") return;
      var layer = L.geoJSON(f, { interactive: false, style: level === "county" ? COUNTY_STYLE : AREA_STYLE }).addTo(map);
      if (level === "sub-county") {
        state.areaLayers[f.properties.name] = layer;
        L.tooltip({ permanent: true, direction: "center", className: "area-label", interactive: false })
          .setLatLng(layer.getBounds().getCenter()).setContent(esc(f.properties.name)).addTo(map);
      }
    });
    addTiles(map, 0);
    state.layer = L.layerGroup().addTo(map);
    fitView(false);
  }

  /* One uniform circle per station, three states, no clustering; the selected one gets a larger ring. */
  function drawMarkers() {
    if (!state.map) return;
    state.layer.clearLayers();
    state.stations.filter(function (s) { return s.lat != null && s.lon != null; }).forEach(function (s) {
      var code = stateOf(s), chosen = s.id === state.selected;
      var label = stationName(s) + ": " + stateLabel(code);
      var marker = L.marker([s.lat, s.lon], {
        icon: L.divIcon({ className: "", iconSize: [44, 44], iconAnchor: [22, 22],
          html: '<span class="mk mk-' + code + (chosen ? " is-selected" : "") + '"></span>' }),
        title: label, alt: label, keyboard: true, riseOnHover: true,
        zIndexOffset: chosen ? 1000 : (2 - STATES.indexOf(code)) * 10
      });
      marker.on("click", function () { select(s.id, { open: true }); });
      state.layer.addLayer(marker);
    });
  }

  /* ------------------------------------------------------------ start */
  function fromHash() { return M.stationFromHash(location.hash, state.stations); }

  I.ready.then(function (meta) {
    state.meta = meta;
    return Promise.all([I.getJSON("data/stations.json"), I.getJSON(meta.boundary_file).catch(function () { return null; })]);
  }).then(function (res) {
    state.stations = res[0].stations;
    state.dataDay = M.dataDay(state.meta, state.stations) || today();
    var seen = {};
    state.stations.forEach(function (s) { if (s.area) seen[s.area] = true; });
    state.areas = Object.keys(seen).sort();
    renderStatus();
    renderChips();
    renderLegend();
    if (window.L) initMap(res[1]);
    setView(phone() ? "list" : "map");
    var start = fromHash() || (freshest(state.stations) || {}).id;
    if (start) select(start);

    document.addEventListener("click", function (event) {
      var chip = event.target.closest(".chip");
      if (chip) { chooseArea(chip.getAttribute("data-area")); return; }
      var view = event.target.closest("[data-view]");
      if (view) { setView(view.getAttribute("data-view")); return; }
      var toggle = event.target.closest(".sheet-toggle");
      if (toggle) {
        state.sheetOpen = !state.sheetOpen;
        var s = byId(state.selected);
        if (s) renderPanel(s);
        return;
      }
      var row = event.target.closest("tr[data-station], .row-btn");
      if (row) select(row.getAttribute("data-station"), { pan: true, open: true });
    });
    window.addEventListener("hashchange", function () { var id = fromHash(); if (id && id !== state.selected) select(id, { pan: true }); });
  }).catch(function (err) {
    if (window.console) console.error(err);
  });
})();
