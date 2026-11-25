/* Imarika showcase site. Reads only data/*.json; every rule runs in Python. */
(function () {
  "use strict";

  var TZ = "Africa/Nairobi";
  var ORDER = { warning: 0, watch: 1, planting: 2, delay: 3, none: 4, offline: 5 };
  var PHONE = /^\+254[17]\d{8}$/;
  var DESKTOP = window.matchMedia("(min-width: 1181px)");
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var state = { meta: null, stations: [], scenarios: [], models: null, quality: null, selected: null,
                map: null, markers: {}, sms: null, tilesOff: false };

  /* ---------------------------------------------------------------- helpers */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function dateOnly(iso, opts) {
    var p = String(iso).split("-").map(Number);
    return new Intl.DateTimeFormat("en-GB", Object.assign({ timeZone: "UTC" }, opts)).format(new Date(Date.UTC(p[0], p[1] - 1, p[2])));
  }
  function longDate(iso) { return dateOnly(iso, { day: "numeric", month: "long", year: "numeric" }); }
  function shortDay(iso) { return dateOnly(iso, { weekday: "short", day: "numeric", month: "short" }); }
  function eat(isoUtc, opts) { return new Intl.DateTimeFormat("en-GB", Object.assign({ timeZone: TZ }, opts)).format(new Date(isoUtc)); }
  function eatClock(isoUtc) { return eat(isoUtc, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }); }
  function eatDate(isoUtc) { return eat(isoUtc, { day: "numeric", month: "long", year: "numeric" }); }
  function fmtNumber(n) { return typeof n === "number" ? n.toLocaleString("en-GB") : esc(n); }
  function getJSON(path) {
    return fetch(path, { cache: "no-cache" }).then(function (r) { if (!r.ok) throw new Error(path + " " + r.status); return r.json(); });
  }
  function svg(paths, cls) { return '<svg class="' + (cls || "ic") + '" viewBox="0 0 24 24" aria-hidden="true">' + paths + "</svg>"; }

  var P = {
    warning: '<path d="M12 6v7"/><path d="M12 17.5v.5"/>',
    watch: '<path d="M8 6c-2 3-3 5-3 6.5a3 3 0 0 0 6 0C11 11 10 9 8 6z"/><path d="M16 9c-1.5 2.2-2.2 3.6-2.2 4.7a2.2 2.2 0 0 0 4.4 0c0-1.1-.7-2.5-2.2-4.7z"/>',
    planting: '<path d="M12 20v-8"/><path d="M12 12c0-3 2-5 6-5 0 3-2 5-6 5z"/><path d="M12 14c0-2.5-1.7-4-5-4 0 2.5 1.7 4 5 4z"/>',
    delay: '<circle cx="12" cy="12" r="7"/><path d="M12 8.5V12l2.5 1.5"/>',
    none: '<path d="m6.5 12.5 3.5 3.5 7.5-8"/>',
    offline: '<path d="M7 12h10"/>',
    drop: '<path d="M12 3.5c-3.3 4.4-5.5 7.4-5.5 10a5.5 5.5 0 0 0 11 0c0-2.6-2.2-5.6-5.5-10z"/>',
    pin: '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    sync: '<path d="M4 12a8 8 0 0 1 13.7-5.7L20 8.5"/><path d="M20 4v4.5h-4.5"/><path d="M20 12a8 8 0 0 1-13.7 5.7L4 15.5"/><path d="M4 20v-4.5h4.5"/>',
    humidity: '<path d="M12 3.5c-3.3 4.4-5.5 7.4-5.5 10a5.5 5.5 0 0 0 11 0c0-2.6-2.2-5.6-5.5-10z"/><path d="M6.6 14h10.8"/>',
    wind: '<path d="M3 9h11a3 3 0 1 0-3-3"/><path d="M3 14h15a3 3 0 1 1-3 3"/><path d="M3 19h7"/>',
    alert: '<path d="M12 4 2.5 20h19L12 4z"/><path d="M12 10v4.5M12 17.5v.01"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    check: '<circle cx="12" cy="12" r="8.5"/><path d="m8.3 12.3 2.5 2.5 5-5.3"/>',
    hold: '<circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6M15 9l-6 6"/>',
    wait: '<circle cx="12" cy="12" r="8.5"/><path d="M8 12h8"/>',
    phone: '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M10 18h4"/>',
    send: '<path d="M4 12 20 4l-6 16-3-7-7-1z"/>',
    shield: '<path d="M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6l-8-3z"/>'
  };
  function statusIcon(code, cls) { return svg(P[code] || "", cls || ""); }
  function dot(code) { return '<span class="dot st-' + code + '">' + statusIcon(code) + "</span>"; }
  function byId(id) { return state.stations.filter(function (s) { return s.id === id; })[0]; }
  function severitySort(a, b) {
    return (ORDER[a.status.code] - ORDER[b.status.code]) || a.id.localeCompare(b.id, "en", { numeric: true });
  }
  function areaLine(s) { return (s.area ? s.area + " area, " : "") + "Busia County"; }

  /* --------------------------------------------------------------- overview */
  function renderOverview() {
    var m = state.meta, c = m.counts;
    $("#stat-reporting").textContent = c.stations_reporting + " of " + c.stations_total;
    $("#stat-alerts").textContent = c.stations_with_alerts + " of " + c.stations_total;
    $("#issued-line").textContent = "Imarika forecast, issued " + longDate(m.as_of);
    if (m.synthetic) { $("#synthetic-banner").hidden = false; document.body.classList.add("has-banner"); }
  }

  /* -------------------------------------------------------------------- map */
  var TILE_URL = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
  var TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors ' +
    '&copy; <a href="https://carto.com/attributions">CARTO</a>';

  function fitPadding() {
    if (!DESKTOP.matches) return { paddingTopLeft: [16, 16], paddingBottomRight: [16, 16] };
    var right = document.body.classList.contains("present") ? 540 : 460;
    return { paddingTopLeft: [340, 70], paddingBottomRight: [right, 100] };
  }

  function initMap(geo) {
    var map = L.map("map", { scrollWheelZoom: false, zoomSnap: 0.25, minZoom: 6 });
    state.map = map;
    map.attributionControl.setPrefix(false);
    map.attributionControl.addAttribution("Boundaries: geoBoundaries");
    map.on("focus click", function () { map.scrollWheelZoom.enable(); });
    map.on("blur mouseout", function () { map.scrollWheelZoom.disable(); });
    var county = null;
    if (geo) {
      var features = geo.features || [];
      var style = {
        country: { color: "#44506e", weight: 1.5, fill: false },
        county: { color: "#0b2e6b", weight: 3, fillColor: "#0b2e6b", fillOpacity: 0.05 },
        "sub-county": { color: "#5d6b8a", weight: 1.2, dashArray: "5 5", fill: false }
      };
      ["country", "sub-county", "county"].forEach(function (level) {
        features.filter(function (f) { return f.properties.level === level; }).forEach(function (f) {
          var layer = L.geoJSON(f, { style: style[level], interactive: false }).addTo(map);
          if (level === "county") county = layer;
          if (level === "sub-county") {
            L.tooltip({ permanent: true, direction: "center", className: "area-label", interactive: false })
              .setLatLng(layer.getBounds().getCenter()).setContent(esc(f.properties.name)).addTo(map);
          }
        });
      });
      addLocator(map, features);
    }
    state.countyBounds = county ? county.getBounds() : null;
    refit();
    addTiles(map);
    addMarkers(map);
    DESKTOP.addEventListener("change", function () { setTimeout(function () { map.invalidateSize(); refit(); }, 60); });
  }

  function refit() {
    if (!state.map) return;
    if (state.countyBounds) state.map.fitBounds(state.countyBounds, fitPadding());
    else state.map.setView([0.45, 34.25], 10);
  }

  function addTiles(map) {
    var notice = $("#map-notice"), tiles = null;
    function offline(reason) {
      if (state.tilesOff) return;
      state.tilesOff = true;
      if (tiles) map.removeLayer(tiles);
      $("#map").classList.add("offline-map");
      notice.textContent = reason + " Showing an offline view of the boundaries and stations.";
      notice.hidden = false;
    }
    if (!navigator.onLine) { offline("You are offline, so the background map is not available."); return; }
    // A tile service that refuses access may answer every tile with the same placeholder image.
    // A Busia tile and an ocean tile can never be identical, so compare them.
    var probe = function (z, x, y) {
      return fetch(TILE_URL.replace("{s}", "a").replace("{z}", z).replace("{x}", x).replace("{y}", y).replace("{r}", ""), { mode: "cors" })
        .then(function (r) { if (!r.ok) throw new Error("tile"); return r.arrayBuffer(); });
    };
    Promise.all([probe(10, 609, 510), probe(3, 0, 0)]).then(function (pair) {
      var a = new Uint8Array(pair[0]), b = new Uint8Array(pair[1]);
      if (a.length === b.length && a.every(function (v, i) { return v === b[i]; })) offline("The background map service is not available.");
    }).catch(function () { /* tileerror and the timeout below handle failures */ });
    var loaded = 0, failed = 0;
    tiles = L.tileLayer(TILE_URL, { subdomains: "abcd", maxZoom: 19, attribution: TILE_ATTRIBUTION, crossOrigin: true });
    tiles.on("tileload", function () { loaded += 1; });
    tiles.on("tileerror", function () { failed += 1; if (failed >= 4 && loaded === 0) offline("The background map could not be loaded."); });
    tiles.addTo(map);
    setTimeout(function () { if (loaded === 0) offline("The background map is taking too long to load."); }, 9000);
  }

  function markerIcon(s, selected) {
    var pulse = s.status.code === "warning" || s.status.code === "watch" ? " pulse" : "";
    return L.divIcon({ className: "", iconSize: [36, 36], iconAnchor: [18, 18],
      html: '<div class="marker st-' + s.status.code + pulse + (selected ? " selected" : "") + '"><div class="marker-core">' + statusIcon(s.status.code) + "</div></div>" });
  }
  function labelMarker(s) {
    var el = state.markers[s.id] && state.markers[s.id].getElement();
    if (el) { el.setAttribute("role", "button"); el.setAttribute("aria-label", s.name + ": " + s.status.label); }
  }
  function addMarkers(map) {
    state.stations.forEach(function (s) {
      if (s.lat == null || s.lon == null) return;
      var label = s.name + ": " + s.status.label;
      var marker = L.marker([s.lat, s.lon], { icon: markerIcon(s, s.id === state.selected), keyboard: true, title: label,
        alt: label, riseOnHover: true, zIndexOffset: (5 - ORDER[s.status.code]) * 100 }).addTo(map);
      marker.on("click", function () { select(s.id, { from: "map" }); });
      state.markers[s.id] = marker;
      labelMarker(s);
    });
  }

  function addLocator(map, features) {
    var kenya = features.filter(function (f) { return f.properties.level === "country"; })[0];
    var busia = features.filter(function (f) { return f.properties.level === "county"; })[0];
    if (!kenya || !busia) return;
    var rings = function (g) { return (g.type === "Polygon" ? [g.coordinates] : g.coordinates).map(function (p) { return p[0]; }); };
    var all = [].concat.apply([], rings(kenya.geometry));
    var xs = all.map(function (p) { return p[0]; }), ys = all.map(function (p) { return p[1]; });
    var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs), minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
    var W = 96, H = 110, k = Math.min((W - 8) / (maxX - minX), (H - 8) / (maxY - minY));
    var path = function (g) {
      return rings(g).map(function (ring) {
        return ring.map(function (p, i) { return (i ? "L" : "M") + (4 + (p[0] - minX) * k).toFixed(1) + " " + (4 + (maxY - p[1]) * k).toFixed(1); }).join("") + "Z";
      }).join("");
    };
    var Locator = L.Control.extend({
      options: { position: "topright" },
      onAdd: function () {
        var div = L.DomUtil.create("div", "locator secondary");
        div.innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Busia County is in western Kenya, on the border with Uganda">' +
          '<path d="' + path(kenya.geometry) + '" fill="#f1f3ff" stroke="#44506e" stroke-width="1"/>' +
          '<path d="' + path(busia.geometry) + '" fill="#ba1a1a" stroke="#ba1a1a" stroke-width="1.2"/></svg><span>Busia in Kenya</span>';
        return div;
      }
    });
    new Locator().addTo(map);
  }

  function renderLegend() {
    $("#legend").innerHTML = state.meta.statuses.map(function (s) { return "<li>" + dot(s.code) + esc(s.label) + "</li>"; }).join("");
  }

  /* ----------------------------------------------------------- station list */
  function renderList() {
    var q = $("#station-search").value.trim().toLowerCase();
    var sort = $("#station-sort").value;
    var items = state.stations.filter(function (s) {
      return !q || (s.name + " " + (s.area || "") + " " + s.id + " " + s.status.label).toLowerCase().indexOf(q) >= 0;
    });
    items.sort(sort === "alerts" ? severitySort : sort === "area"
      ? function (a, b) { return (a.area || "~").localeCompare(b.area || "~") || a.id.localeCompare(b.id); }
      : function (a, b) { return a.id.localeCompare(b.id, "en", { numeric: true }); });
    var list = $("#station-items");
    if (!items.length) { list.innerHTML = '<li class="empty">No station matches that search.</li>'; return; }
    list.innerHTML = items.map(function (s) {
      return '<li><button type="button" data-id="' + esc(s.id) + '"' + (state.selected === s.id ? ' aria-current="true"' : "") + ">" + dot(s.status.code) +
        '<span class="item-text"><span class="item-name">' + esc(s.label) + (s.area ? ", " + esc(s.area) : "") + "</span>" +
        '<span class="item-status">' + esc(s.status.label) + "</span></span></button></li>";
    }).join("");
  }

  /* ------------------------------------------------------------------ panel */
  function select(id, opts) {
    var s = byId(id);
    if (!s) return;
    var previous = state.selected;
    state.selected = id;
    if (previous && state.markers[previous]) { state.markers[previous].setIcon(markerIcon(byId(previous), false)); labelMarker(byId(previous)); }
    if (state.markers[id]) {
      state.markers[id].setIcon(markerIcon(s, true));
      labelMarker(s);
      if (opts && opts.from !== "map" && opts.from !== "init") state.map.panInside(state.markers[id].getLatLng(), { padding: [80, 80], animate: !reduceMotion });
    }
    renderList();
    renderPanel(s);
    if (opts && opts.from === "map" && !DESKTOP.matches) $("#panel").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth" });
  }

  function statusBar(s) {
    var left = s.active ? '<span><i class="live" aria-hidden="true"></i>Station reporting</span>'
      : '<span><i class="live off" aria-hidden="true"></i>Not reporting</span>';
    var right = "";
    if (s.active && s.latest && s.latest.time_utc) right = "<span>" + svg(P.sync) + "Synced " + esc(eatClock(s.latest.time_utc)) + " EAT</span>";
    return '<div class="ph-status">' + left + right + "</div>";
  }

  function header(s) {
    return '<header class="ph-head">' + svg(P.pin) + '<div><h2 id="panel-name">' + esc(s.label) + "</h2><p>" + esc(areaLine(s)) + "</p>" +
      '<span class="status-chip">' + dot(s.status.code) + esc(s.status.label) + "</span></div></header>";
  }

  function metricsCard(s) {
    var r = s.latest;
    if (!r) return '<div class="card ph-card"><p class="label">Right now</p><p style="margin:.4rem 0 0">No recent sensor reading is available.</p></div>';
    var tiles = [];
    if (r.humidity_pct != null) tiles.push(["humidity", "Humidity", Math.round(r.humidity_pct) + "%"]);
    if (r.rain_today_mm != null) tiles.push(["drop", "Rain today", r.rain_today_mm.toFixed(1) + " mm"]);
    if (r.wind_ms != null) tiles.push(["wind", "Wind speed", r.wind_ms.toFixed(1) + " m/s"]);
    return '<div class="card ph-card"><div class="card-head"><p class="label">Right now at the station</p></div>' +
      (r.temperature_c != null ? '<p class="temp-big">' + Math.round(r.temperature_c) + "°C</p>" : "") +
      (tiles.length ? '<div class="metric-tiles">' + tiles.map(function (t) {
        return '<div class="metric">' + svg(P[t[0]]) + "<span>" + t[1] + "</span><b>" + t[2] + "</b></div>";
      }).join("") + "</div>" : "") +
      '<p class="reading-time">Reading from ' + esc(eat(r.time_utc, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })) + " EAT</p></div>";
  }

  function dayTiles(s) {
    var heavy = state.meta.thresholds.heavy_rain_mm;
    var tiles = s.forecast.map(function (d) {
      var isHeavy = d.rain_mm != null && d.rain_mm >= heavy;
      var rain = d.rain_mm == null ? "no data" : Math.round(d.rain_mm) + " mm";
      var label = dateOnly(d.date, { weekday: "long", day: "numeric", month: "long" }) + ": " +
        (d.temp_max != null ? "high " + Math.round(d.temp_max) + " degrees, " : "") + (d.rain_mm == null ? "no rain data" : Math.round(d.rain_mm) + " millimetres of rain") +
        (isHeavy ? ", heavy rain" : "");
      return '<div class="day' + (isHeavy ? " heavy" : "") + '" role="listitem" aria-label="' + esc(label) + '">' +
        '<span class="dname" aria-hidden="true">' + dateOnly(d.date, { weekday: "short" }) + "</span>" +
        '<span class="ddate" aria-hidden="true">' + dateOnly(d.date, { day: "numeric", month: "short" }) + "</span>" +
        svg(P.drop, "ic drop") +
        (d.temp_max != null ? '<span class="tmax" aria-hidden="true">' + Math.round(d.temp_max) + "°C</span>" : "") +
        '<span class="rain' + (isHeavy ? " heavy" : "") + '" aria-hidden="true">' + rain + "</span></div>";
    }).join("");
    return '<div><div class="section-row"><h3>7-day forecast</h3><span class="chip">Daily rain and high</span></div>' +
      '<div class="days" role="list">' + tiles + '</div><p class="day-note">Red rain figure: ' + heavy + " mm or more in a day (heavy rain).</p></div>";
  }

  function alertBanners(s) {
    return s.insights.filter(function (i) { return i.type !== "planting_window"; }).map(function (i) {
      return '<div class="alert-banner" role="note">' + svg(P.alert) + '<span class="alabel">' + esc(i.title) + "</span>" +
        "<p>" + esc(i.action) + "</p>" + '<span class="awhen">' + svg(P.clock) + "From " + esc(shortDay(i.start_date)) + "</span></div>";
    }).join("");
  }

  function plantingCard(s) {
    var p = s.planting;
    if (!p) return "";
    return '<div class="card planting-card' + (p.status ? "" : " off") + '"><div class="card-head"><h3>' + svg(P.planting) + "Planting window</h3>" +
      (p.badge ? '<span class="badge-green">' + esc(p.badge) + "</span>" : "") + "</div><p>" + esc(p.text) + "</p>" +
      (p.detail ? '<p class="detail">' + esc(p.detail) + "</p>" : "") + "</div>";
  }

  function opsTiles(s) {
    if (!s.operations || !s.operations.length) return "";
    var icons = { go: P.check, hold: P.hold, wait: P.wait, unknown: P.wait };
    return '<div><p class="label" style="margin-bottom:.5rem">Field work this week</p><div class="ops">' + s.operations.map(function (o) {
      return '<div class="op ' + esc(o.state) + '"><h4>' + svg(icons[o.state] || P.wait) + esc(o.title) + "</h4><p>" + esc(o.text) + "</p></div>";
    }).join("") + "</div></div>";
  }

  var BAR = { warning: "#ba1a1a", watch: "#e47f15" };
  function rainChart(days) {
    var heavy = state.meta.thresholds.heavy_rain_mm;
    var W = 380, H = 200, l = 34, r = 8, t = 20, b = 40;
    var max = Math.max.apply(null, days.map(function (d) { return d.rain_mm || 0; }));
    var top = Math.max(heavy + 5, Math.ceil(max * 1.15 / 10) * 10);
    var y = function (v) { return t + (H - t - b) * (1 - v / top); };
    var step = (W - l - r) / days.length, bw = step * 0.6;
    var out = ['<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Rain each day: ' +
      esc(days.map(function (d) { return dateOnly(d.date, { weekday: "long" }) + " " + (d.rain_mm == null ? "no data" : Math.round(d.rain_mm) + " millimetres"); }).join(", ")) + '">'];
    [0, top / 2, top].forEach(function (v) {
      out.push('<line x1="' + l + '" x2="' + (W - r) + '" y1="' + y(v) + '" y2="' + y(v) + '" stroke="#e1e4ef"/>' +
        '<text x="' + (l - 6) + '" y="' + (y(v) + 4) + '" text-anchor="end" font-size="12" fill="#444650">' + Math.round(v) + "</text>");
    });
    days.forEach(function (d, i) {
      var v = d.rain_mm || 0, x = l + i * step + (step - bw) / 2;
      out.push('<rect x="' + x.toFixed(1) + '" y="' + y(v).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(0, y(0) - y(v)).toFixed(1) + '" rx="2" fill="' + (BAR[d.alert] || "#0b2e6b") + '"/>');
      if (d.rain_mm != null) out.push('<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (y(v) - 5).toFixed(1) + '" text-anchor="middle" font-size="12" font-weight="700" fill="#151c29">' + Math.round(v) + "</text>");
      out.push('<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - b + 16) + '" text-anchor="middle" font-size="12" fill="#151c29">' + dateOnly(d.date, { weekday: "short" }) + "</text>");
      if (d.planting) out.push('<rect x="' + (l + i * step + 1).toFixed(1) + '" y="' + (H - b + 24) + '" width="' + (step - 2).toFixed(1) + '" height="5" rx="1" fill="#006e26"/>');
    });
    out.push('<line x1="' + l + '" x2="' + (W - r) + '" y1="' + y(heavy) + '" y2="' + y(heavy) + '" stroke="#ba1a1a" stroke-width="2" stroke-dasharray="6 4"/>' +
      '<text x="' + (W - r) + '" y="' + (y(heavy) - 5) + '" text-anchor="end" font-size="12" font-weight="700" fill="#93000a">Heavy rain: ' + heavy + " mm</text>" +
      '<text x="4" y="12" font-size="12" fill="#444650">mm</text></svg>');
    var planting = days.some(function (d) { return d.planting; });
    return '<div class="card ph-card chart"><div class="section-row"><h3>Rain each day</h3></div>' + out.join("") +
      (planting ? '<p class="day-note">Green strip: days in the planting window.</p>' : "") + "</div>";
  }

  function whyBlock(s) {
    var heavy = state.meta.thresholds.heavy_rain_mm;
    var body = s.insights.length
      ? s.insights.map(function (i) { return "<p><strong>" + esc(i.title) + ".</strong> " + esc(i.basis) + "</p>"; }).join("")
      : "<p>No day in the next 7 days reaches the heavy-rain level of " + heavy + " mm, there is not enough rain for planting, and no rain is expected in the next 3 days.</p>";
    return '<details class="more"><summary>Why this alert?</summary><div class="details-body">' + body + "</div></details>";
  }

  function reliabilityBlock(s) {
    var lines = state.meta.reliability.slice();
    var caveat = s.insights.length ? s.insights[0].caveat : null;
    if (caveat && lines.indexOf(caveat) < 0) lines.push(caveat);
    return '<details class="more"><summary>How reliable is this?</summary><div class="details-body"><ul>' +
      lines.map(function (l) { return "<li>" + esc(l) + "</li>"; }).join("") +
      '</ul><p class="small" style="margin:.6rem 0 0"><a href="#trust">More about reliability</a></p></div></details>';
  }

  function renderPanel(s) {
    var body;
    if (!s.active) {
      var when = s.last_reading_utc ? " on " + esc(eatDate(s.last_reading_utc)) : "";
      body = '<div class="card stopped"><p class="label" style="margin-bottom:.4rem">No forecast</p><p>This station stopped reporting' + when +
        ".</p><p class=\"small muted\" style=\"margin-top:.5rem\">" + esc(s.what_to_do) + "</p></div>";
    } else {
      body = metricsCard(s) + dayTiles(s) + alertBanners(s) + plantingCard(s) + opsTiles(s) + rainChart(s.forecast) +
        whyBlock(s) + reliabilityBlock(s) +
        (s.insights.length ? '<button class="btn btn-primary" type="button" data-sms-station="' + esc(s.id) + '" data-sms-key="' + esc(s.insights[0].key) + '">' +
          svg(P.phone) + "Preview the SMS</button>" : "") +
        '<p class="issued">' + esc(state.meta.issued_line) + "</p>";
    }
    var panel = $("#panel");
    panel.innerHTML = statusBar(s) + header(s) + '<div class="ph-body">' + body + "</div>";
    panel.scrollTop = 0;
  }

  /* ------------------------------------------------------------ alert cards */
  function renderAlerts() {
    var rows = [];
    state.stations.forEach(function (s) { if (s.active) s.insights.forEach(function (i) { rows.push({ s: s, i: i }); }); });
    rows.sort(function (a, b) {
      return (ORDER[a.i.status] - ORDER[b.i.status]) || a.i.start_date.localeCompare(b.i.start_date) || a.s.id.localeCompare(b.s.id, "en", { numeric: true });
    });
    var box = $("#alert-cards");
    if (!rows.length) { box.innerHTML = '<div class="card ph-card"><p style="margin:0">No alerts across Busia County this week.</p></div>'; return; }
    box.innerHTML = rows.map(function (r) {
      var when = (r.i.type === "flood_alert" ? "On " : "From ") + shortDay(r.i.start_date);
      return '<article class="alert-card reveal st-' + r.i.status + '"><span class="tag">' + statusIcon(r.i.status) + esc(r.i.title) + "</span>" +
        "<h3>" + esc(r.s.label) + (r.s.area ? ", " + esc(r.s.area) : "") + '</h3><p class="when">' + esc(when) + "</p>" +
        '<p class="action">' + esc(r.i.action) + "</p>" +
        '<div class="btn-row"><button class="btn btn-primary btn-small" type="button" data-sms-station="' + esc(r.s.id) + '" data-sms-key="' + esc(r.i.key) + '">Preview SMS</button>' +
        '<button class="btn btn-outline btn-small" type="button" data-show-station="' + esc(r.s.id) + '">Show on map</button></div></article>';
    }).join("");
  }

  /* --------------------------------------------------------------- SMS demo */
  function smsStations() { return state.stations.filter(function (s) { return s.active && s.insights.length; }).sort(severitySort); }

  function renderSmsDemo() {
    var stations = smsStations(), first = stations[0];
    state.sms = { mode: first ? "station" : "scenario", station: first ? first.id : null, key: first ? first.insights[0].key : null,
                  scenario: state.scenarios.length ? state.scenarios[state.scenarios.length - 1].key : null };
    $("#sms-demo").innerHTML =
      '<form class="card sms-controls" id="sms-form" novalidate>' +
      '<p class="sandbox-note">' + svg(P.shield) + "Sandbox simulator: no real phone is contacted.</p>" +
      '<fieldset class="field"><legend>Message from</legend><div class="segmented">' +
      '<label><input type="radio" name="sms-mode" value="station"' + (first ? " checked" : " disabled") + "><span>A station's advice</span></label>" +
      '<label><input type="radio" name="sms-mode" value="scenario"' + (first ? "" : " checked") + "><span>An example week</span></label></div></fieldset>" +
      '<div id="sms-station-fields"><div class="field"><label for="sms-station">Station</label><select id="sms-station">' +
      stations.map(function (s) { return '<option value="' + esc(s.id) + '">' + esc(s.label + (s.area ? ", " + s.area : "") + " – " + s.status.label) + "</option>"; }).join("") +
      '</select></div><div class="field" style="margin-top:1rem"><label for="sms-advisory">Advice</label><select id="sms-advisory"></select></div></div>' +
      '<div id="sms-scenario-fields" hidden><div class="field"><label for="sms-scenario">Example week</label><select id="sms-scenario">' +
      state.scenarios.map(function (sc) { return '<option value="' + esc(sc.key) + '">' + esc(sc.title) + "</option>"; }).join("") +
      '</select><div id="scenario-preview" class="hint"></div></div></div>' +
      '<div class="field"><label for="sms-phone">Phone number</label><input id="sms-phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="+254712345678" aria-describedby="sms-phone-hint sms-phone-error">' +
      '<span id="sms-phone-hint" class="hint">Kenyan mobile number in +254 format.</span><span id="sms-phone-error" class="error" role="alert"></span></div>' +
      '<div><button id="sms-send" class="btn btn-primary" type="submit">' + svg(P.send) + "Send to sandbox</button></div>" +
      '<p id="sms-result" class="result" role="status" hidden></p></form>' +
      '<div class="phone" aria-label="Phone screen showing the text message"><div class="phone-screen">' +
      '<div class="ph-status"><span id="phone-clock"></span><span>Sandbox</span></div>' +
      '<header class="ph-head">' + svg(P.phone) + '<div><h2 id="phone-title">SMS demo</h2><p id="phone-sub"></p></div></header>' +
      '<div class="card sms-card" id="sms-card"><h3 id="sms-card-title"></h3><span id="sms-card-when" class="small muted"></span>' +
      '<div class="sms-box"><p class="label">Simulated text message</p><p id="sms-text" class="sms-text" aria-live="polite"></p></div>' +
      '<div class="sms-meta"><span id="sms-counter"></span><span id="sms-note" class="chip"></span></div></div></div></div>';

    var form = $("#sms-form");
    form.addEventListener("change", function (e) {
      if (e.target.name === "sms-mode") state.sms.mode = e.target.value;
      if (e.target.id === "sms-station") { state.sms.station = e.target.value; state.sms.key = byId(e.target.value).insights[0].key; }
      if (e.target.id === "sms-advisory") state.sms.key = e.target.value;
      if (e.target.id === "sms-scenario") state.sms.scenario = e.target.value;
      if (e.target.id !== "sms-phone") updateSms();
    });
    $("#sms-phone").addEventListener("input", function () { $("#sms-phone-error").textContent = ""; });
    form.addEventListener("submit", function (e) { e.preventDefault(); sendSms(); });
    $("#phone-clock").textContent = eatClock(new Date().toISOString()) + " EAT";
    updateSms();
  }

  function currentMessage() {
    var sms = state.sms;
    if (sms.mode === "station") {
      var s = byId(sms.station);
      var i = s && s.insights.filter(function (x) { return x.key === sms.key; })[0];
      return i ? { text: i.sms_text, title: i.title, status: i.status, when: "From " + shortDay(i.start_date), sub: s.label + (s.area ? ", " + s.area : ""),
                   note: "Issued " + dateOnly(state.meta.as_of, { day: "numeric", month: "short" }), body: { station: s.id, advisory: i.key } } : null;
    }
    var sc = state.scenarios.filter(function (x) { return x.key === sms.scenario; })[0];
    if (!sc) return null;
    var top = sc.insights[0];
    return { text: sc.sms_text, title: top ? top.title : sc.status.label, status: top ? top.status : sc.status.code, when: "Example week: " + sc.title,
             sub: "Example week, not a forecast", note: "Example", body: { scenario: sc.key }, scenario: sc };
  }

  function updateSms() {
    var sms = state.sms;
    $("#sms-station-fields").hidden = sms.mode !== "station";
    $("#sms-scenario-fields").hidden = sms.mode !== "scenario";
    var radio = document.querySelector('input[name="sms-mode"][value="' + sms.mode + '"]');
    if (radio) radio.checked = true;
    if (sms.mode === "station" && sms.station) {
      $("#sms-station").value = sms.station;
      $("#sms-advisory").innerHTML = byId(sms.station).insights.map(function (i) {
        return '<option value="' + esc(i.key) + '"' + (i.key === sms.key ? " selected" : "") + ">" + esc(i.title + ", " + shortDay(i.start_date)) + "</option>";
      }).join("");
    }
    if (sms.scenario) $("#sms-scenario").value = sms.scenario;
    var msg = currentMessage();
    if (msg && msg.scenario) {
      var sc = msg.scenario, top = Math.max.apply(null, sc.rain_mm.concat([25]));
      $("#scenario-preview").innerHTML = '<div class="scenario-bars" aria-hidden="true">' + sc.rain_mm.map(function (v) {
        return '<span style="height:' + Math.max(4, v / top * 100) + '%"></span>'; }).join("") + "</div>Rain each day: " +
        sc.rain_mm.map(function (v) { return v + " mm"; }).join(", ") + ". Result: " + esc(sc.status.label) + ".";
    }
    showOnPhone(msg);
    $("#sms-result").hidden = true;
  }

  var typingTimer = null;
  function showOnPhone(msg) {
    clearTimeout(typingTimer);
    var card = $("#sms-card"), textBox = $("#sms-text"), text = msg && msg.text;
    card.className = "card sms-card st-" + (msg ? msg.status : "none");
    $("#sms-card-title").innerHTML = msg ? statusIcon(msg.status) + esc(msg.title) : "";
    $("#sms-card-when").textContent = msg ? msg.when : "";
    $("#phone-sub").textContent = msg ? msg.sub : "";
    $("#sms-note").textContent = msg ? msg.note : "";
    $("#sms-counter").textContent = text ? text.length + "/160 characters" : "";
    $("#sms-counter").className = text && text.length > 160 ? "over" : "";
    $("#sms-send").disabled = !text;
    if (!text) { textBox.className = "sms-text"; textBox.textContent = "No message: Imarika sends nothing when there is no alert."; return; }
    if (reduceMotion) { textBox.className = "sms-text"; textBox.textContent = text; return; }
    textBox.className = "sms-text";
    textBox.innerHTML = '<span class="typing" aria-hidden="true"><i></i><i></i><i></i></span>';
    typingTimer = setTimeout(function () { textBox.className = "sms-text arrive"; textBox.textContent = text; }, 700);
  }

  function result(kind, text) { var box = $("#sms-result"); box.className = "result " + kind; box.textContent = text; box.hidden = false; }

  function sendSms() {
    var raw = $("#sms-phone").value.replace(/[\s-]/g, "");
    if (!PHONE.test(raw)) {
      $("#sms-phone-error").textContent = "Enter a Kenyan mobile number in +254 format, for example +254712345678.";
      $("#sms-phone").focus();
      return;
    }
    var msg = currentMessage();
    if (!msg || !msg.text) return;
    var button = $("#sms-send");
    button.disabled = true;
    result("demo", "Sending to the sandbox...");
    var ctrl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, 15000);
    fetch("api/send-sms", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ phone: raw }, msg.body)), signal: ctrl ? ctrl.signal : undefined })
      .then(function (r) {
        if ((r.headers.get("content-type") || "").indexOf("application/json") < 0) throw new Error("unreachable");
        return r.json();
      })
      .then(function (j) {
        if (j && j.ok) result("ok", "Sent to the sandbox: " + (j.detail || "accepted") + ". No real phone was contacted.");
        else result("fail", "Not sent: " + ((j && j.error) || "the sandbox refused the message") + ".");
      })
      .catch(function () { result("demo", "Demo mode: message shown on the phone only."); })
      .then(function () { clearTimeout(timer); button.disabled = false; });
  }

  function loadSms(stationId, key) {
    var s = byId(stationId);
    if (!s || !s.insights.length) return;
    state.sms.mode = "station"; state.sms.station = stationId; state.sms.key = key || s.insights[0].key;
    updateSms();
    $("#sms").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    setTimeout(function () { $("#sms-phone").focus({ preventScroll: true }); }, reduceMotion ? 0 : 600);
  }

  /* ----------------------------------------------------------- how it works */
  var STEPS = [
    ["Sensors", '<rect x="9" y="3" width="6" height="10" rx="3"/><path d="M12 13v8M8 21h8M4 6c-1 2-1 4 0 6M20 6c1 2 1 4 0 6"/>',
      "Weather stations in fields across Busia County measure temperature, humidity, wind, air pressure and rain through the day."],
    ["Checking", '<path d="M4 5h16M7 12h10M10 19h4"/>',
      "Faulty readings are removed, short gaps are filled, and station records are lined up with satellite and weather-model data."],
    ["Forecasting", '<path d="M3 17l5-5 4 3 8-8"/><path d="M15 7h5v5"/>',
      "A forecasting model trained on past readings predicts rain and temperature for each of the next 7 days at every station."],
    ["Rules", '<path d="M9 4h6M12 4v4M5 8h14l-2 12H7L5 8z"/><path d="m9 14 2 2 4-4"/>',
      "Simple rules based on Kenya Meteorological Department rain levels and published planting guidance turn the forecast into advice."],
    ["Text message", '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M9 7h6M9 10h4M10 18h4"/>',
      "Each piece of advice becomes one short text message with one clear action, so any phone can receive it."],
    ["Web and map", '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14"/>',
      "This website shows the same forecast and advice for every station, and keeps working offline once it has been opened."]
  ];
  function renderPipeline() {
    $("#pipeline").innerHTML = STEPS.map(function (s, i) {
      return '<li class="reveal"><span class="num">STEP ' + (i + 1) + '</span><div class="icon"><svg viewBox="0 0 24 24" aria-hidden="true">' + s[1] + "</svg></div><h3>" + s[0] + "</h3><p>" + s[2] + "</p></li>";
    }).join("");
  }

  /* ------------------------------------------------------------------ trust */
  var CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7"/></svg>';
  var CAUTION = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.5"/></svg>';
  function sourceLine(src) { return src ? '<span class="source">Source: ' + esc(src) + "</span>" : ""; }
  function fmtValue(v, unit) { return v == null ? "–" : "about " + v.toFixed(v < 1 ? 2 : 1) + " " + unit; }

  function renderTrust() {
    var m = state.models || {}, q = state.quality || {}, sar = m.sarima || {}, lstm = m.lstm || {}, vars = sar.variables || [];
    var html = [];
    var table = vars.length ? '<table class="acc-table"><thead><tr><th scope="col">Measurement</th><th scope="col">Typical error one hour ahead</th><th scope="col">Better than "no change"</th></tr></thead><tbody>' +
      vars.map(function (v) {
        var pct = v.skill_vs_persistence == null ? null : Math.round(v.skill_vs_persistence * 100);
        return '<tr><th scope="row">' + esc(v.label) + "</th><td>" + esc(fmtValue(v.typical_error, v.unit)) + "</td><td>" +
          (pct == null ? "–" : '<span class="better">' + pct + '% better</span><div class="bar-track"><div class="bar-fill" style="width:' + Math.max(0, Math.min(100, pct)) + '%"></div></div>') + "</td></tr>";
      }).join("") + "</tbody></table>" + sourceLine(vars[0].source) : '<p class="muted">No measured accuracy results are available.</p>';
    html.push('<div class="card wide reveal"><h3>What has been measured</h3><p>The forecasting approach, ' + esc(sar.plain_name || "a statistical model") +
      ", was tested one hour ahead at one station" + (sar.station ? " (" + esc(sar.station) + ")" : "") +
      " on recent data kept aside for testing. Each result is compared with simply assuming the weather stays the same as an hour earlier.</p>" + table + "</div>");
    var limits = ["Accuracy was measured only one hour ahead, at one station.", "The accuracy of the 7-day forecasts has not been measured yet.",
      "Forecast settings are shared across all stations; they are not tuned for each station.", "Rain is harder to predict than temperature."];
    if (lstm.plain_name) limits.push("A second approach, " + lstm.plain_name + ", was also tried. " + (lstm.status || ""));
    html.push('<div class="card reveal"><h3>What has not been measured</h3><ul class="check-list caution">' +
      limits.map(function (l) { return "<li>" + CAUTION + "<span>" + esc(l) + "</span></li>"; }).join("") + "</ul></div>");
    var items = (q.audit || []).map(function (a) {
      return '<li><span class="num">' + fmtNumber(a.value) + (a.of ? '<span class="small muted" style="display:block;font-weight:500">of ' + fmtNumber(a.of) + "</span>" : "") +
        "</span><span>" + esc(a.label) + "</span>" + sourceLine(a.source) + "</li>";
    });
    if (q.temperature_agreement) {
      var t = q.temperature_agreement;
      items.push('<li><span class="num">' + t.min.toFixed(2) + "–" + t.max.toFixed(2) + "</span><span>" + esc(t.label) + "</span>" + sourceLine(t.source) + "</li>");
    }
    (q.fixes || []).forEach(function (f) {
      items.push('<li><span class="num">' + fmtNumber(f.value) + "</span><span>" + esc(f.label) + (f.unit ? " (" + esc(f.unit) + ")" : "") + "</span>" + sourceLine(f.source) + "</li>");
    });
    html.push('<div class="card reveal"><h3>Data checks and fixes</h3><ul class="fix-list">' + items.join("") + "</ul>" +
      ((q.other_fixes || []).length ? '<ul class="check-list" style="margin-top:1rem">' + q.other_fixes.map(function (f) { return "<li>" + CHECK + "<span>" + esc(f) + "</span></li>"; }).join("") + "</ul>" + sourceLine(q.other_fixes_source) : "") + "</div>");
    html.push('<div class="card wide reveal"><h3>Use it wisely</h3><ul class="check-list">' +
      state.meta.reliability.map(function (l) { return "<li>" + CHECK + "<span>" + esc(l) + "</span></li>"; }).join("") + "</ul></div>");
    $("#trust-body").innerHTML = html.join("");
  }

  /* ------------------------------------------------------------------ about */
  function renderAbout() {
    var c = state.meta.credits || {}, b = c.boundaries || {};
    $("#about-body").innerHTML =
      '<div class="card reveal"><h3>Data sources</h3><ul>' + (c.data_sources || []).map(function (s) { return "<li><strong>" + esc(s.name) + "</strong>: " + esc(s.detail) + "</li>"; }).join("") + "</ul></div>" +
      '<div class="card reveal"><h3>Maps</h3><p>' + esc(b.headline) + "</p><ul>" + (b.layers || []).map(function (l) {
        return "<li>" + esc(l.name) + " (" + esc(l.level) + "): " + esc(l.source) + ". " + esc(l.licence) + ".</li>"; }).join("") +
      '</ul><p class="small muted" style="margin-top:.8rem">' + esc(b.citation) + '</p><p class="small muted" style="margin:0">Map tiles &copy; CARTO. Map data &copy; OpenStreetMap contributors.</p></div>' +
      '<div class="card reveal"><h3>About this project</h3><p>Imarika is a student project built for smallholder farmers in Busia County, Kenya.</p>' +
      "<p>Forecasts on this site are labelled &ldquo;Imarika forecast&rdquo; with the date they were issued. They do not replace advice from local experts.</p></div>";
  }

  /* -------------------------------------------------------------- behaviour */
  function wireEvents() {
    $("#station-search").addEventListener("input", renderList);
    $("#station-sort").addEventListener("change", renderList);
    $("#station-items").addEventListener("click", function (e) {
      var b = e.target.closest("button[data-id]");
      if (b) select(b.getAttribute("data-id"), { from: "list" });
    });
    document.addEventListener("click", function (e) {
      var sms = e.target.closest("[data-sms-station]");
      if (sms) { loadSms(sms.getAttribute("data-sms-station"), sms.getAttribute("data-sms-key")); return; }
      var show = e.target.closest("[data-show-station]");
      if (show) { select(show.getAttribute("data-show-station"), { from: "list" }); $("#map-section").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth" }); }
    });
    var toggle = $("#present-toggle");
    var apply = function (on) {
      document.body.classList.toggle("present", on);
      toggle.setAttribute("aria-pressed", on ? "true" : "false");
      if (state.map) setTimeout(function () { state.map.invalidateSize(); refit(); }, 80);
    };
    toggle.addEventListener("click", function () {
      var on = !document.body.classList.contains("present");
      apply(on);
      try { localStorage.setItem("imarika-present", on ? "1" : "0"); } catch (err) { /* private mode */ }
    });
    try { if (localStorage.getItem("imarika-present") === "1") apply(true); } catch (err) { /* ignore */ }
    window.addEventListener("online", offlineBanner);
    window.addEventListener("offline", offlineBanner);
    // Bottom navigation: highlight the section in view
    if ("IntersectionObserver" in window) {
      var links = document.querySelectorAll(".bottom-nav a");
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          links.forEach(function (a) { a.setAttribute("aria-current", a.getAttribute("data-nav") === en.target.id ? "true" : "false"); });
        });
      }, { rootMargin: "-45% 0px -50% 0px" });
      ["map-section", "panel", "week", "sms", "about"].forEach(function (id) { var el = document.getElementById(id); if (el) io.observe(el); });
    }
  }

  function offlineBanner(force) {
    var banner = $("#offline-banner");
    if ((force === true || !navigator.onLine) && state.meta) {
      banner.textContent = "Showing saved forecast from " + longDate(state.meta.as_of) + ". You appear to be offline.";
      banner.hidden = false;
    } else banner.hidden = true;
  }

  function reveal() {
    var items = document.querySelectorAll(".reveal");
    if (reduceMotion || !("IntersectionObserver" in window)) { items.forEach(function (el) { el.classList.add("shown"); }); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add("shown"); io.unobserve(en.target); } });
    }, { rootMargin: "0px 0px -40px 0px" });
    items.forEach(function (el) { io.observe(el); });
  }

  function start() {
    Promise.all([getJSON("data/meta.json"), getJSON("data/stations.json"), getJSON("data/scenarios.json"),
      getJSON("data/model_results.json").catch(function () { return null; }), getJSON("data/data_quality.json").catch(function () { return null; })
    ]).then(function (res) {
      state.meta = res[0]; state.stations = res[1].stations || []; state.scenarios = res[2].scenarios || [];
      state.models = res[3]; state.quality = res[4];
      renderOverview(); renderLegend(); wireEvents(); offlineBanner();
      var first = state.stations.slice().sort(severitySort)[0];
      if (first) { state.selected = first.id; renderList(); renderPanel(first); } else renderList();
      getJSON(state.meta.boundary_file || "data/kenya_boundaries.geojson").catch(function () { return null; }).then(function (geo) {
        if (typeof L !== "undefined") initMap(geo);
        else { $("#map-notice").textContent = "The map could not be loaded."; $("#map-notice").hidden = false; }
      });
      renderAlerts(); renderSmsDemo(); renderPipeline(); renderTrust(); renderAbout(); reveal();
    }).catch(function (err) {
      $("#panel").innerHTML = '<p class="stopped">The forecast data could not be loaded. Please try again later.</p>';
      if (window.console) console.error(err);
    });
    if ("serviceWorker" in navigator && location.protocol !== "file:") {
      navigator.serviceWorker.register("sw.js").catch(function () { /* optional */ });
      navigator.serviceWorker.addEventListener("message", function (e) { if (e.data && e.data.type === "offline") offlineBanner(true); });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
