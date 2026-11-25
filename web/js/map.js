/* Weather map page: choose an area, read its forecast, see every station on the map.
   Reads only data/*.json; every rule (status, advice, planting, spraying) was applied in Python. */
(function () {
  "use strict";
  var I = window.Imarika, t = I.t, esc = I.esc, icon = I.icon;
  var TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  var TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
  var CLUSTER_PX = 34;
  var state = { meta: null, stations: [], areas: [], selected: null, map: null, layer: null, tilesOff: false, areaLayers: {} };
  var AREA_STYLE = { color: "#1f7a4d", weight: 1.2, dashArray: "4 4", fillColor: "#e8f5ee", fillOpacity: 0.35 };
  var AREA_CHOSEN = { color: "#0f3d2e", weight: 4, dashArray: null, fillColor: "#e8f5ee", fillOpacity: 0.55 };

  function byId(id) { return state.stations.filter(function (s) { return s.id === id; })[0]; }
  function inArea(area) { return state.stations.filter(function (s) { return s.area === area; }).sort(I.bySeverity); }
  function reduceMotion() { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }

  /* ------------------------------------------------------------ result card */
  /* A day's status follows the engine's order: flood warning, flood watch, planting window, rain delay, quiet. */
  function dayStatus(day) {
    if (day.alert === "warning" || day.alert === "watch") return day.alert;
    if (day.planting) return "planting";
    if (day.alert === "delay") return "delay";
    return "none";
  }

  function dayRow(day) {
    var code = dayStatus(day);
    var word = code === "none" ? t("map.day_quiet") : t("status." + code);
    var mm = day.rain_mm != null ? I.round(day.rain_mm) + " " + t("rain.unit") : t("map.no_value");
    var temp = day.temp_min != null && day.temp_max != null
      ? t("time.temp_range", { min: I.round(day.temp_min), max: I.round(day.temp_max) }) : t("map.no_value");
    return '<li class="day day-' + code + '"><span class="day-name">' + esc(I.weekday(day.date)) + "</span>" +
      '<span class="day-status">' + icon(I.STATUS_ICON[code]) + "<span>" + esc(word) + "</span></span>" +
      '<span class="day-mm">' + icon("drop") + "<span>" + esc(mm) + "</span></span>" +
      '<span class="day-temp">' + icon("thermometer") + "<span>" + esc(temp) + "</span></span></li>";
  }

  function nowList(latest) {
    if (!latest) return "";
    var rows = [];
    if (latest.temperature_c != null) rows.push(["thermometer", t("map.temperature"), I.round(latest.temperature_c) + "°C"]);
    if (latest.humidity_pct != null) rows.push(["humidity", t("map.humidity"), I.round(latest.humidity_pct) + "%"]);
    if (latest.wind_ms != null) rows.push(["wind", t("map.wind"), I.round(latest.wind_ms * 3.6) + " " + t("map.kmh")]);
    if (latest.rain_today_mm != null) {
      var mm = latest.rain_today_mm;
      rows.push(["drop", t("map.rain_today"), t("rain." + I.rainClass(mm)) + " (" + (Math.round(mm * 10) / 10) + " " + t("rain.unit") + ")"]);
    }
    if (!rows.length) return "";
    return "<h3>" + esc(t("map.right_now")) + '</h3><ul class="now-list">' + rows.map(function (r) {
      return "<li>" + icon(r[0]) + "<span>" + esc(r[1]) + "</span><strong>" + esc(r[2]) + "</strong></li>";
    }).join("") + "</ul>";
  }

  function opsList(operations) {
    var wanted = [["planting", "sprout"], ["spraying", "spray"]];
    var rows = wanted.map(function (w) {
      var op = (operations || []).filter(function (o) { return o.key === w[0]; })[0];
      if (!op) return "";
      return '<li><span class="icon-wrap">' + icon(w[1]) + "</span><strong>" + esc(t("map." + w[0])) + "</strong><p>" + esc(op.text) + "</p></li>";
    }).join("");
    return rows ? '<ul class="ops-list">' + rows + "</ul>" : "";
  }

  function renderCard(s) {
    var others = inArea(s.area).filter(function (o) { return o.id !== s.id; });
    var html = "<h2>" + esc(s.area || s.label) + "</h2>";
    if (!s.active) {
      var stopped = I.longDate(I.localDay(s.last_reading_utc));
      html += '<p class="station-line">' + esc(s.label) + "</p>" + I.badge("offline") +
        '<p class="action">' + esc(t("map.not_reporting", { date: stopped })) + "</p>" +
        '<div class="chart-slot" id="chart-slot"></div>';
    } else {
      html += '<p class="station-line">' + esc(t("map.station_updated", { station: s.label, time: I.whenSeen(s.last_reading_utc, state.meta.as_of) })) + "</p>" +
        I.badge(s.status.code) + '<p class="action">' + esc(s.what_to_do) + "</p>" +
        "<h3>" + esc(t("map.next_days")) + '</h3><ul class="day-rows" role="list">' + s.forecast.slice(0, 3).map(dayRow).join("") + "</ul>" +
        (s.forecast.length > 3 ? "<h3>" + esc(t("map.later")) + '</h3><ul class="day-rows day-rows-later" role="list">' + s.forecast.slice(3).map(dayRow).join("") + "</ul>" : "") +
        nowList(s.latest) +
        '<div class="chart-slot" id="chart-slot"></div>' +
        opsList(s.operations);
    }
    if (others.length) {
      html += '<p class="other-stations"><span>' + esc(t("map.other_stations")) + ":</span>" + others.map(function (o) {
        return '<button type="button" class="link-btn" data-station="' + esc(o.id) + '">' + esc(o.label) + "</button>";
      }).join("") + "</p>";
    }
    document.getElementById("result").innerHTML = html;
    if (I.charts) I.charts.render(document.getElementById("chart-slot"), s, state.meta);
  }

  /* ------------------------------------------------------------ selection */
  function select(id, opts) {
    var s = byId(id);
    if (!s) return;
    opts = opts || {};
    state.selected = id;
    renderCard(s);
    Array.prototype.forEach.call(document.querySelectorAll(".area-btn"), function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-area") === s.area));
    });
    Array.prototype.forEach.call(document.querySelectorAll(".station-row"), function (b) {
      if (b.getAttribute("data-station") === id) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current");
    });
    highlightArea(s.area);
    drawMarkers();
    var areaLayer = state.areaLayers[s.area];
    if (opts.zoomArea && state.map && areaLayer) {
      state.map.flyToBounds(areaLayer.getBounds(), { padding: [24, 24], maxZoom: 12, animate: !reduceMotion() });
    } else if (opts.pan && state.map && s.lat != null) {
      state.map.panTo([s.lat, s.lon], { animate: !reduceMotion() });
    }
    if (opts.scroll) document.getElementById("result").scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "start" });
    if (history.replaceState) history.replaceState(null, "", "#station-" + id);
  }

  function renderAreas() {
    document.getElementById("area-buttons").innerHTML = state.areas.map(function (area) {
      return '<button type="button" class="area-btn" aria-pressed="false" data-area="' + esc(area) + '">' + esc(area) + "</button>";
    }).join("");
  }

  function renderGroups() {
    document.getElementById("station-groups").innerHTML = state.areas.map(function (area) {
      return "<section><h3>" + esc(area) + "</h3><ul>" + inArea(area).map(function (s) {
        return '<li><button type="button" class="station-row" data-station="' + esc(s.id) + '"><span>' + esc(s.label) + "</span>" + I.badge(s.status.code) + "</button></li>";
      }).join("") + "</ul></section>";
    }).join("");
  }

  function renderLegend() {
    var present = {};
    state.stations.forEach(function (s) { present[s.status.code] = true; });
    document.getElementById("legend-items").innerHTML = Object.keys(I.ORDER).filter(function (c) { return present[c]; })
      .map(function (c) { return "<li>" + I.badge(c) + "</li>"; }).join("");
    if (window.matchMedia("(max-width: 760px)").matches) document.getElementById("legend").open = false;
  }

  /* ------------------------------------------------------------ map */
  function highlightArea(area) {
    Object.keys(state.areaLayers).forEach(function (name) {
      var layer = state.areaLayers[name];
      layer.setStyle(name === area ? AREA_CHOSEN : AREA_STYLE);
      if (name === area) layer.bringToFront();
    });
  }

  function addTiles(map) {
    var tiles = null, loaded = 0, failed = 0;
    function off() {
      if (state.tilesOff) return;
      state.tilesOff = true;
      if (tiles) map.removeLayer(tiles);
      var el = document.getElementById("map-note");
      el.textContent = t("map.tiles_offline");
      el.hidden = false;
    }
    if (!navigator.onLine) { off(); return; }
    tiles = L.tileLayer(TILE_URL, { maxZoom: 18, attribution: TILE_ATTRIBUTION });
    tiles.on("tileload", function () { loaded += 1; });
    tiles.on("tileerror", function () { failed += 1; if (failed >= 4 && loaded === 0) off(); });
    tiles.addTo(map);
    setTimeout(function () { if (loaded === 0) off(); }, 9000);
  }

  function initMap(geo) {
    var map = L.map("map", { scrollWheelZoom: false, zoomSnap: 0.5, minZoom: 8, maxZoom: 18 });
    state.map = map;
    map.attributionControl.setPrefix(false);
    map.attributionControl.addAttribution("Boundaries: geoBoundaries");
    map.on("focus", function () { map.scrollWheelZoom.enable(); });
    map.on("blur", function () { map.scrollWheelZoom.disable(); });
    var bounds = null;
    (geo && geo.features || []).forEach(function (f) {
      var level = f.properties.level;
      if (level !== "county" && level !== "sub-county") return;
      var layer = L.geoJSON(f, { interactive: false, style: level === "county"
        ? { color: "#0f3d2e", weight: 3, fill: false } : AREA_STYLE }).addTo(map);
      if (level === "county") bounds = layer.getBounds();
      if (level === "sub-county") {
        state.areaLayers[f.properties.name] = layer;
        L.tooltip({ permanent: true, direction: "center", className: "area-label", interactive: false })
          .setLatLng(layer.getBounds().getCenter()).setContent(esc(f.properties.name)).addTo(map);
      }
    });
    if (bounds) map.fitBounds(bounds, { padding: [12, 12] }); else map.setView([0.45, 34.2], 10);
    addTiles(map);
    state.layer = L.layerGroup().addTo(map);
    map.on("zoomend", drawMarkers);
    drawMarkers();
  }

  function selectedArea() { var s = byId(state.selected); return s ? s.area : null; }

  function stationMarker(s) {
    var code = s.status.code, label = s.label + ", " + (s.area || "") + ": " + t("status." + code);
    var cls = "mk mk-" + code + (s.area && s.area === selectedArea() ? " in-area" : "") + (s.id === state.selected ? " is-selected" : "");
    var marker = L.marker([s.lat, s.lon], {
      icon: L.divIcon({ className: "", iconSize: [28, 28], iconAnchor: [14, 14],
        html: '<span class="' + cls + '">' + icon(I.STATUS_ICON[code]) + "</span>" }),
      title: label, alt: label, keyboard: true, zIndexOffset: s.id === state.selected ? 1000 : (5 - I.ORDER[code]) * 10
    });
    marker.on("click", function () { select(s.id, { scroll: window.matchMedia("(max-width: 1000px)").matches }); });
    return marker;
  }

  /* The cluster takes the colour of the most serious status inside it (same order as everywhere else). */
  function clusterMarker(group) {
    var lat = 0, lon = 0, area = selectedArea();
    group.forEach(function (s) { lat += s.lat; lon += s.lon; });
    var code = group.slice().sort(I.bySeverity)[0].status.code;
    var chosen = group.some(function (s) { return s.area && s.area === area; });
    var label = t("map.cluster_status", { count: group.length, status: t("status." + code) });
    var marker = L.marker([lat / group.length, lon / group.length], {
      icon: L.divIcon({ className: "", iconSize: [34, 34], iconAnchor: [17, 17],
        html: '<span class="mk-count mk-' + code + (chosen ? " in-area" : "") + '">' + group.length + "</span>" }),
      title: label, alt: label, keyboard: true, zIndexOffset: 2000
    });
    marker.on("click", function () {
      var map = state.map, b = L.latLngBounds(group.map(function (s) { return [s.lat, s.lon]; }));
      var zoom = Math.min(map.getBoundsZoom(b.pad(0.5)), map.getMaxZoom());
      if (zoom <= map.getZoom()) zoom = Math.min(map.getZoom() + 2, map.getMaxZoom());
      map.setView(b.getCenter(), zoom, { animate: !reduceMotion() });
    });
    return marker;
  }

  /* Stations closer than CLUSTER_PX on screen are shown as one count marker. */
  function drawMarkers() {
    var map = state.map;
    if (!map) return;
    state.layer.clearLayers();
    var groups = [], atMax = map.getZoom() >= map.getMaxZoom();
    state.stations.filter(function (s) { return s.lat != null && s.lon != null; }).sort(I.bySeverity).forEach(function (s) {
      var p = map.latLngToLayerPoint([s.lat, s.lon]);
      var hit = atMax ? null : groups.filter(function (g) { return g.point.distanceTo(p) < CLUSTER_PX; })[0];
      if (hit) hit.items.push(s); else groups.push({ point: p, items: [s] });
    });
    groups.forEach(function (g) {
      state.layer.addLayer(g.items.length === 1 ? stationMarker(g.items[0]) : clusterMarker(g.items));
    });
  }

  /* ------------------------------------------------------------ start */
  I.ready.then(function (meta) {
    state.meta = meta;
    document.getElementById("map-headline").textContent = meta.headline || "";
    return Promise.all([I.getJSON("data/stations.json"), I.getJSON(meta.boundary_file).catch(function () { return null; })]);
  }).then(function (res) {
    state.stations = res[0].stations;
    var seen = {};
    state.stations.forEach(function (s) { if (s.area) seen[s.area] = true; });
    state.areas = Object.keys(seen).sort();
    renderAreas();
    renderGroups();
    renderLegend();
    document.addEventListener("click", function (event) {
      var area = event.target.closest(".area-btn");
      if (area) {
        var first = inArea(area.getAttribute("data-area"))[0];
        if (first) select(first.id, { pan: true, zoomArea: true });
        return;
      }
      var row = event.target.closest("[data-station]");
      if (row) select(row.getAttribute("data-station"), { pan: true, scroll: row.classList.contains("station-row") });
    });
    if (window.L) initMap(res[1]);
    var wanted = (location.hash.match(/^#station-(\w+)$/) || [])[1];
    var start = byId(wanted) || state.stations.slice().sort(I.bySeverity)[0];
    if (start) select(start.id);
  }).catch(function (err) {
    if (window.console) console.error(err);
  });
})();
