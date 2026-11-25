/* Imarika showcase site. Reads only data/*.json; all rules run in Python. */
(function () {
  "use strict";

  var TZ = "Africa/Nairobi";
  var ORDER = { warning: 0, watch: 1, planting: 2, delay: 3, none: 4, offline: 5 };
  var ALERT_CODES = { warning: 1, watch: 1, planting: 1, delay: 1 };
  var PHONE = /^\+254[17]\d{8}$/;
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var state = { meta: null, stations: [], scenarios: [], models: null, quality: null, selected: null,
                map: null, markers: {}, sms: null };

  /* ---------------------------------------------------------------- helpers */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function dateOnly(iso, opts) {
    var p = String(iso).split("-").map(Number);
    return new Intl.DateTimeFormat("en-GB", Object.assign({ timeZone: "UTC" }, opts))
      .format(new Date(Date.UTC(p[0], p[1] - 1, p[2])));
  }
  function longDate(iso) { return dateOnly(iso, { day: "numeric", month: "long", year: "numeric" }); }
  function shortDay(iso) { return dateOnly(iso, { weekday: "short", day: "numeric", month: "short" }); }
  function kenyaTime(isoUtc, withTime) {
    var opts = withTime === false ? { day: "numeric", month: "long", year: "numeric" }
      : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false };
    return new Intl.DateTimeFormat("en-GB", Object.assign({ timeZone: TZ }, opts)).format(new Date(isoUtc));
  }
  function fmtNumber(n) { return typeof n === "number" ? n.toLocaleString("en-GB") : esc(n); }
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }
  function getJSON(path) {
    return fetch(path, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(path + " " + r.status);
      return r.json();
    });
  }

  var ICON_PATHS = {
    warning: '<path d="M12 6v7"/><path d="M12 17.5v.5"/>',
    watch: '<path d="M8 6c-2 3-3 5-3 6.5a3 3 0 0 0 6 0C11 11 10 9 8 6z"/><path d="M16 9c-1.5 2.2-2.2 3.6-2.2 4.7a2.2 2.2 0 0 0 4.4 0c0-1.1-.7-2.5-2.2-4.7z"/>',
    planting: '<path d="M12 20v-8"/><path d="M12 12c0-3 2-5 6-5 0 3-2 5-6 5z"/><path d="M12 14c0-2.5-1.7-4-5-4 0 2.5 1.7 4 5 4z"/>',
    delay: '<circle cx="12" cy="12" r="7"/><path d="M12 8.5V12l2.5 1.5"/>',
    none: '<path d="m6.5 12.5 3.5 3.5 7.5-8"/>',
    offline: '<path d="M7 12h10"/>'
  };
  function icon(code, cls) {
    return '<svg class="' + (cls || "") + '" viewBox="0 0 24 24" aria-hidden="true">' + (ICON_PATHS[code] || "") + "</svg>";
  }
  function statusInfo(code) {
    var found = (state.meta.statuses || []).filter(function (s) { return s.code === code; })[0];
    return found || { code: code, label: code };
  }
  function byId(id) { return state.stations.filter(function (s) { return s.id === id; })[0]; }
  function severitySort(a, b) {
    return (ORDER[a.status.code] - ORDER[b.status.code]) || a.label.localeCompare(b.label, "en", { numeric: true });
  }

  /* ------------------------------------------------------------------- hero */
  function renderHero() {
    var m = state.meta, c = m.counts;
    $("#stat-reporting").textContent = c.stations_reporting + " of " + c.stations_total;
    $("#stat-alerts").textContent = c.stations_with_alerts + " of " + c.stations_total;
    $("#stat-issued").textContent = longDate(m.as_of);
    $("#issued-line").textContent = "Imarika forecast, issued " + longDate(m.as_of) +
      ". Choose a station on the map or in the list.";
    if (m.synthetic) $("#synthetic-banner").hidden = false;
  }

  /* -------------------------------------------------------------------- map */
  var TILE_URL = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
  var TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors ' +
    '&copy; <a href="https://carto.com/attributions">CARTO</a>';

  function initMap(geo) {
    var map = L.map("map", { scrollWheelZoom: false, zoomSnap: 0.25, attributionControl: true, minZoom: 6 });
    state.map = map;
    map.attributionControl.setPrefix(false);
    map.attributionControl.addAttribution("Boundaries: geoBoundaries");
    map.on("focus click", function () { map.scrollWheelZoom.enable(); });
    map.on("blur mouseout", function () { map.scrollWheelZoom.disable(); });

    var county = null;
    if (geo) {
      var features = geo.features || [];
      var style = {
        country: { color: "#5B6B80", weight: 1.5, fill: false },
        county: { color: "#1F4D36", weight: 3, fillColor: "#2E7D32", fillOpacity: 0.04 },
        "sub-county": { color: "#6F7D74", weight: 1.2, dashArray: "5 5", fill: false }
      };
      ["country", "sub-county", "county"].forEach(function (level) {
        features.filter(function (f) { return f.properties.level === level; }).forEach(function (f) {
          var layer = L.geoJSON(f, { style: style[level], interactive: false }).addTo(map);
          if (level === "county") county = layer;
          if (level === "sub-county") {
            var centre = layer.getBounds().getCenter();
            L.tooltip({ permanent: true, direction: "center", className: "area-label", interactive: false })
              .setLatLng(centre).setContent(esc(f.properties.name)).addTo(map);
          }
        });
      });
      addLocator(map, features);
    }
    if (county) map.fitBounds(county.getBounds(), { padding: [16, 16] });
    else map.setView([0.45, 34.25], 10);

    addTiles(map);
    addMarkers(map);
  }

  function addTiles(map) {
    var notice = $("#map-notice");
    function offline(reason) {
      if (state.tilesOff) return;
      state.tilesOff = true;
      if (tiles) map.removeLayer(tiles);
      $("#map").classList.add("offline-map");
      notice.textContent = reason + " Showing an offline view of the boundaries and stations.";
      notice.hidden = false;
    }
    if (!navigator.onLine) { offline("You are offline, so the background map is not available."); return; }
    // A tile service that refuses access may still answer every tile with the same placeholder
    // image. Two different tiles (Busia and an ocean tile) can never be identical, so compare them.
    var probe = function (z, x, y) {
      return fetch(TILE_URL.replace("{s}", "a").replace("{z}", z).replace("{x}", x).replace("{y}", y).replace("{r}", ""), { mode: "cors" })
        .then(function (r) { if (!r.ok) throw new Error("tile"); return r.arrayBuffer(); });
    };
    Promise.all([probe(10, 609, 510), probe(3, 0, 0)]).then(function (pair) {
      var a = new Uint8Array(pair[0]), b = new Uint8Array(pair[1]);
      var same = a.length === b.length && a.every(function (v, i) { return v === b[i]; });
      if (same) offline("The background map service is not available.");
    }).catch(function () { /* tileerror and the timeout below handle failures */ });
    var loaded = 0, failed = 0;
    var tiles = L.tileLayer(TILE_URL, { subdomains: "abcd", maxZoom: 19, attribution: TILE_ATTRIBUTION, crossOrigin: true });
    tiles.on("tileload", function () { loaded += 1; });
    tiles.on("tileerror", function () {
      failed += 1;
      if (failed >= 4 && loaded === 0) offline("The background map could not be loaded.");
    });
    tiles.addTo(map);
    setTimeout(function () { if (loaded === 0) offline("The background map is taking too long to load."); }, 9000);
  }

  function markerHtml(s, selected) {
    var pulse = (s.status.code === "warning" || s.status.code === "watch") ? " pulse" : "";
    return '<div class="marker s-' + s.status.code + pulse + (selected ? " selected" : "") + '">' +
      '<div class="marker-core">' + icon(s.status.code) + "</div></div>";
  }

  function addMarkers(map) {
    state.stations.forEach(function (s) {
      if (s.lat == null || s.lon == null) return;
      var label = s.name + ": " + s.status.label;
      var marker = L.marker([s.lat, s.lon], {
        icon: L.divIcon({ className: "", html: markerHtml(s, false), iconSize: [36, 36], iconAnchor: [18, 18] }),
        keyboard: true, title: label, alt: label, riseOnHover: true,
        zIndexOffset: (5 - ORDER[s.status.code]) * 100
      }).addTo(map);
      marker.on("click", function () { select(s.id, { from: "map" }); });
      var el = marker.getElement();
      if (el) { el.setAttribute("role", "button"); el.setAttribute("aria-label", label); }
      state.markers[s.id] = marker;
    });
  }

  function addLocator(map, features) {
    var kenya = features.filter(function (f) { return f.properties.level === "country"; })[0];
    var busia = features.filter(function (f) { return f.properties.level === "county"; })[0];
    if (!kenya || !busia) return;
    var rings = function (g) {
      var polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
      return polys.map(function (p) { return p[0]; });
    };
    var all = [].concat.apply([], rings(kenya.geometry));
    var xs = all.map(function (p) { return p[0]; }), ys = all.map(function (p) { return p[1]; });
    var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
    var minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
    var W = 104, H = 120, k = Math.min((W - 8) / (maxX - minX), (H - 8) / (maxY - minY));
    var path = function (g) {
      return rings(g).map(function (ring) {
        return ring.map(function (p, i) {
          return (i ? "L" : "M") + (4 + (p[0] - minX) * k).toFixed(1) + " " + (4 + (maxY - p[1]) * k).toFixed(1);
        }).join("") + "Z";
      }).join("");
    };
    var Locator = L.Control.extend({
      options: { position: "topright" },
      onAdd: function () {
        var div = L.DomUtil.create("div", "locator secondary");
        div.innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Busia County is in western Kenya, on the border with Uganda">' +
          '<path d="' + path(kenya.geometry) + '" fill="#EEF1EC" stroke="#5B6B80" stroke-width="1"/>' +
          '<path d="' + path(busia.geometry) + '" fill="#C62828" stroke="#C62828" stroke-width="1.2"/></svg>' +
          "<span>Busia in Kenya</span>";
        return div;
      }
    });
    new Locator().addTo(map);
  }

  function renderLegend() {
    $("#legend").innerHTML = state.meta.statuses.map(function (s) {
      return '<li><span class="dot s-' + s.code + '">' + icon(s.code) + "</span>" + esc(s.label) + "</li>";
    }).join("");
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
      return '<li><button type="button" data-id="' + esc(s.id) + '"' + (state.selected === s.id ? ' aria-current="true"' : "") + ">" +
        '<span class="dot s-' + s.status.code + '">' + icon(s.status.code) + "</span>" +
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
    if (previous && state.markers[previous]) state.markers[previous].setIcon(L.divIcon({ className: "", html: markerHtml(byId(previous), false), iconSize: [36, 36], iconAnchor: [18, 18] }));
    var marker = state.markers[id];
    if (marker) {
      marker.setIcon(L.divIcon({ className: "", html: markerHtml(s, true), iconSize: [36, 36], iconAnchor: [18, 18] }));
      var el = marker.getElement();
      if (el) { el.setAttribute("role", "button"); el.setAttribute("aria-label", s.name + ": " + s.status.label); }
      if (opts && opts.from !== "map" && state.map) state.map.panInside(marker.getLatLng(), { padding: [60, 60], animate: !reduceMotion });
    }
    renderList();
    renderPanel(s);
  }

  function dayTiles(s) {
    return '<div class="days">' + s.forecast.map(function (d) {
      var cls = d.alert ? " alert s-" + d.alert : "";
      var flag = !!d.alert;
      var rain = d.rain_mm == null ? "–" : Math.round(d.rain_mm) + "<small>mm</small>";
      var temp = d.temp_min == null || d.temp_max == null ? "" : Math.round(d.temp_min) + "–" + Math.round(d.temp_max) + "°";
      var full = dateOnly(d.date, { weekday: "long", day: "numeric", month: "long" }) + ": " +
        (d.rain_mm == null ? "no rain data" : Math.round(d.rain_mm) + " mm of rain") +
        (temp ? ", " + Math.round(d.temp_min) + " to " + Math.round(d.temp_max) + " degrees" : "") +
        (d.alert === "warning" ? ", very heavy rain" : d.alert === "watch" ? ", heavy rain" : d.alert === "delay" ? ", rain: delay spraying" : "");
      return '<div class="day' + cls + '" role="group" aria-label="' + esc(full) + '"><b aria-hidden="true">' + dateOnly(d.date, { weekday: "short" }) + " " + dateOnly(d.date, { day: "numeric" }) + "</b>" +
        '<span class="rain" aria-hidden="true">' + rain + "</span>" + (temp ? '<span class="temp" aria-hidden="true">' + temp + "</span>" : "") +
        (flag ? '<span class="flag" aria-hidden="true">' + icon(d.alert) + "</span>" : "") + "</div>";
    }).join("") + "</div>" + dayKey(s.forecast);
  }

  function dayKey(days) {
    var names = { warning: "Very heavy rain", watch: "Heavy rain", delay: "Rain: delay spraying" };
    var seen = Object.keys(names).filter(function (k) { return days.some(function (d) { return d.alert === k; }); });
    if (!seen.length) return "";
    return '<p class="day-key">' + seen.map(function (k) {
      return '<span class="s-' + k + '" style="color:var(--c)">' + icon(k) + '<span style="color:var(--ink)">' + names[k] + "</span></span>";
    }).join("") + "</p>";
  }

  var BAR_COLOURS = { warning: "#C62828", watch: "#E59A12", delay: "#1565C0" };
  function rainChart(days) {
    var heavy = state.meta.thresholds.heavy_rain_mm;
    var W = 380, H = 200, l = 34, r = 8, t = 20, b = 40;
    var max = Math.max.apply(null, days.map(function (d) { return d.rain_mm || 0; }));
    var top = Math.max(heavy + 5, Math.ceil(max * 1.15 / 10) * 10);
    var y = function (v) { return t + (H - t - b) * (1 - v / top); };
    var step = (W - l - r) / days.length, bw = step * 0.62;
    var out = ['<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Daily rain forecast: ' +
      esc(days.map(function (d) { return dateOnly(d.date, { weekday: "long" }) + " " + (d.rain_mm == null ? "no data" : Math.round(d.rain_mm) + " millimetres"); }).join(", ")) + '">'];
    [0, top / 2, top].forEach(function (v) {
      out.push('<line x1="' + l + '" x2="' + (W - r) + '" y1="' + y(v) + '" y2="' + y(v) + '" stroke="#ECE7DA"/>' +
        '<text x="' + (l - 6) + '" y="' + (y(v) + 4) + '" text-anchor="end" font-size="11" fill="#4F5C54">' + Math.round(v) + "</text>");
    });
    days.forEach(function (d, i) {
      var v = d.rain_mm || 0, x = l + i * step + (step - bw) / 2;
      var fill = BAR_COLOURS[d.alert] || "#8FB3DA";
      out.push('<rect class="bar" x="' + x.toFixed(1) + '" y="' + y(v).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(0, y(0) - y(v)).toFixed(1) + '" rx="3" fill="' + fill + '"/>');
      if (d.rain_mm != null) out.push('<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (y(v) - 5).toFixed(1) + '" text-anchor="middle" font-size="12" font-weight="700" fill="#1B2820">' + Math.round(v) + "</text>");
      out.push('<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - b + 16) + '" text-anchor="middle" font-size="12" fill="#1B2820">' + dateOnly(d.date, { weekday: "short" }) + "</text>");
      if (d.planting) out.push('<rect x="' + (l + i * step + 1).toFixed(1) + '" y="' + (H - b + 24) + '" width="' + (step - 2).toFixed(1) + '" height="5" rx="2" fill="#2E7D32"/>');
    });
    out.push('<line x1="' + l + '" x2="' + (W - r) + '" y1="' + y(heavy) + '" y2="' + y(heavy) + '" stroke="#B76E00" stroke-width="2" stroke-dasharray="6 4"/>' +
      '<text x="' + (W - r) + '" y="' + (y(heavy) - 5) + '" text-anchor="end" font-size="11" font-weight="700" fill="#8A5300">Heavy rain: ' + heavy + " mm</text>");
    out.push('<text x="4" y="12" font-size="11" fill="#4F5C54">mm</text></svg>');
    var planting = days.some(function (d) { return d.planting; });
    return out.join("") + (planting ? '<p class="small muted" style="margin:.3rem 0 0"><span class="dot s-planting" style="width:12px;height:12px;vertical-align:-1px"></span> Green strip: days in the planting window.</p>' : "");
  }

  function rightNow(s) {
    var r = s.latest;
    if (!r) return '<p class="muted" style="margin:0">No recent sensor reading is available.</p>';
    var cell = function (name, v) { return "<div>" + name + "<b>" + v + "</b></div>"; };
    return '<div class="now">' +
      cell("Temperature", r.temperature_c == null ? "no data" : Math.round(r.temperature_c) + "°C") +
      cell("Humidity", r.humidity_pct == null ? "no data" : Math.round(r.humidity_pct) + "%") +
      cell("Wind", r.wind_ms == null ? "no data" : r.wind_ms.toFixed(1) + " m/s") +
      cell("Rain today", r.rain_today_mm == null ? "no data" : r.rain_today_mm.toFixed(1) + " mm") +
      '</div><p class="small muted" style="margin:.4rem 0 0">Reading from ' + esc(kenyaTime(r.time_utc)) + " (Kenya time)</p>";
  }

  function whyBlock(s) {
    var heavy = state.meta.thresholds.heavy_rain_mm;
    var body = s.insights.length
      ? s.insights.map(function (i) { return "<p><strong>" + esc(i.title) + ".</strong> " + esc(i.basis) + "</p>"; }).join("")
      : "<p>No day in the next 7 days reaches the heavy-rain level of " + heavy + " mm, there is not enough rain for planting, and no rain is expected in the next 3 days.</p>";
    return "<details><summary>Why this alert?</summary><div class=\"details-body\">" + body + "</div></details>";
  }

  function reliabilityBlock(s) {
    var lines = state.meta.reliability.slice();
    var caveat = s.insights.length ? s.insights[0].caveat : null;
    if (caveat && lines.indexOf(caveat) < 0) lines.push(caveat);
    return "<details><summary>How reliable is this?</summary><div class=\"details-body\"><ul>" +
      lines.map(function (l) { return "<li>" + esc(l) + "</li>"; }).join("") +
      '</ul><p class="small muted" style="margin:.6rem 0 0"><a href="#trust">Read more about reliability</a></p></div></details>';
  }

  function renderPanel(s) {
    var panel = $("#panel");
    panel.className = "card panel s-" + s.status.code;
    var head = '<div class="panel-head"><div class="status-label"><span class="dot s-' + s.status.code + '">' + icon(s.status.code) + "</span>" +
      esc(s.status.label) + '</div><h3 id="panel-name">' + esc(s.label) + '</h3><p class="where">' +
      esc((s.area ? s.area + ", " : "") + "Busia County") + "</p></div>";
    var body;
    if (!s.active) {
      var since = s.last_reading_utc ? " on " + esc(kenyaTime(s.last_reading_utc, false)) : "";
      body = '<div><p class="label">What to do</p><p class="todo">' + esc(s.what_to_do) + "</p></div>" +
        '<div><p class="label">Last reading</p><p class="stopped">This station stopped reporting' + since + ". There is no forecast for it.</p></div>";
    } else {
      body = '<div><p class="label">What to do</p><p class="todo">' + esc(s.what_to_do) + "</p></div>" +
        '<div><p class="label">Next 7 days</p>' + dayTiles(s) + "</div>" +
        '<div><p class="label">Right now</p>' + rightNow(s) + "</div>" +
        '<div class="chart"><p class="label">Rain each day (mm)</p>' + rainChart(s.forecast) + "</div>" +
        whyBlock(s) + reliabilityBlock(s) +
        (s.insights.length ? '<div class="panel-actions"><button class="btn btn-primary btn-small" type="button" data-sms-station="' + esc(s.id) + '" data-sms-key="' + esc(s.insights[0].key) + '">' +
          '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="2"/><path d="M10 18h4"/></svg>Preview SMS</button></div>' : "") +
        '<p class="small muted" style="margin:0">' + esc(state.meta.issued_line) + "</p>";
    }
    panel.innerHTML = head + '<div class="panel-body">' + body + "</div>";
  }

  /* ------------------------------------------------------------ alert cards */
  function renderAlerts() {
    var rows = [];
    state.stations.forEach(function (s) {
      if (!s.active) return;
      s.insights.forEach(function (i) { rows.push({ s: s, i: i }); });
    });
    rows.sort(function (a, b) {
      return (ORDER[a.i.status] - ORDER[b.i.status]) || a.i.start_date.localeCompare(b.i.start_date) ||
        a.s.id.localeCompare(b.s.id, "en", { numeric: true });
    });
    var box = $("#alert-cards");
    if (!rows.length) { box.innerHTML = '<div class="card no-alerts"><p class="todo">No alerts across Busia County this week.</p></div>'; return; }
    box.innerHTML = rows.map(function (r) {
      var when = r.i.type === "planting_window" || r.i.type === "rain_delay" ? "From " + shortDay(r.i.start_date) : "On " + shortDay(r.i.start_date);
      return '<article class="card alert-card reveal s-' + r.i.status + '">' +
        '<span class="tag">' + icon(r.i.status) + esc(r.i.title) + "</span>" +
        "<h3>" + esc(r.s.label) + (r.s.area ? ", " + esc(r.s.area) : "") + "</h3>" +
        '<p class="when">' + esc(when) + "</p>" +
        '<p class="action">' + esc(r.i.action) + "</p>" +
        '<div class="panel-actions"><button class="btn btn-primary btn-small" type="button" data-sms-station="' + esc(r.s.id) + '" data-sms-key="' + esc(r.i.key) + '">Preview SMS</button>' +
        '<button class="btn btn-ghost btn-small" type="button" data-show-station="' + esc(r.s.id) + '">Show on map</button></div></article>';
    }).join("");
  }

  /* --------------------------------------------------------------- SMS demo */
  function smsSources() {
    return state.stations.filter(function (s) { return s.active && s.insights.length; }).sort(severitySort);
  }

  function renderSmsDemo() {
    var stations = smsSources();
    var first = stations[0];
    state.sms = { mode: first ? "station" : "scenario", station: first ? first.id : null,
                  key: first ? first.insights[0].key : null, scenario: state.scenarios.length ? state.scenarios[state.scenarios.length - 1].key : null };
    $("#sms-demo").innerHTML =
      '<form class="card sms-controls" id="sms-form" novalidate>' +
      '<p class="sandbox-note"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6l-8-3z"/></svg>' +
      "Sandbox simulator: no real phone is contacted.</p>" +
      '<fieldset class="field"><legend>Message from</legend><div class="segmented" role="radiogroup">' +
      '<label><input type="radio" name="sms-mode" value="station"' + (first ? " checked" : " disabled") + '><span>A station\'s advice</span></label>' +
      '<label><input type="radio" name="sms-mode" value="scenario"' + (first ? "" : " checked") + '><span>An example week</span></label></div></fieldset>' +
      '<div id="sms-station-fields"><div class="field"><label for="sms-station">Station</label><select id="sms-station">' +
      stations.map(function (s) { return '<option value="' + esc(s.id) + '">' + esc(s.label + (s.area ? ", " + s.area : "") + " – " + s.status.label) + "</option>"; }).join("") +
      '</select></div><div class="field" style="margin-top:1rem"><label for="sms-advisory">Advice</label><select id="sms-advisory"></select></div></div>' +
      '<div id="sms-scenario-fields" hidden><div class="field"><label for="sms-scenario">Example week</label><select id="sms-scenario">' +
      state.scenarios.map(function (sc) { return '<option value="' + esc(sc.key) + '">' + esc(sc.title) + "</option>"; }).join("") +
      '</select><div id="scenario-preview" class="hint"></div></div></div>' +
      '<div class="field"><label for="sms-phone">Phone number</label><input id="sms-phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="+254712345678" aria-describedby="sms-phone-hint sms-phone-error">' +
      '<span id="sms-phone-hint" class="hint">Kenyan mobile number in +254 format.</span><span id="sms-phone-error" class="error" role="alert"></span></div>' +
      '<div><button id="sms-send" class="btn btn-primary" type="submit">' +
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12 20 4l-6 16-3-7-7-1z"/></svg>Send to sandbox</button></div>' +
      '<p id="sms-result" class="result" role="status" hidden></p></form>' +
      '<div class="phone" aria-label="Phone showing the text message"><div class="phone-notch"></div><div class="phone-screen">' +
      '<div class="phone-status"><span id="phone-clock"></span><span>4G</span></div>' +
      '<div class="phone-top"><div class="phone-avatar"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="9" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6z"/></svg></div><span>Text message</span></div>' +
      '<div class="phone-thread" id="phone-thread" aria-live="polite"></div>' +
      '<div class="phone-foot"><span id="phone-note"></span><span id="sms-counter" class="counter"></span></div></div></div>';

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
    $("#phone-clock").textContent = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date());
    updateSms();
  }

  function currentMessage() {
    var sms = state.sms;
    if (sms.mode === "station") {
      var s = byId(sms.station);
      var i = s && s.insights.filter(function (x) { return x.key === sms.key; })[0];
      return i ? { text: i.sms_text, note: state.meta.issued_line, body: { station: s.id, advisory: i.key } } : null;
    }
    var sc = state.scenarios.filter(function (x) { return x.key === sms.scenario; })[0];
    if (!sc) return null;
    return { text: sc.sms_text, note: "Example week, not a forecast", body: { scenario: sc.key }, scenario: sc };
  }

  function updateSms() {
    var sms = state.sms;
    $("#sms-station-fields").hidden = sms.mode !== "station";
    $("#sms-scenario-fields").hidden = sms.mode !== "scenario";
    var radio = document.querySelector('input[name="sms-mode"][value="' + sms.mode + '"]');
    if (radio) radio.checked = true;
    if (sms.mode === "station" && sms.station) {
      $("#sms-station").value = sms.station;
      var s = byId(sms.station);
      $("#sms-advisory").innerHTML = s.insights.map(function (i) {
        return '<option value="' + esc(i.key) + '"' + (i.key === sms.key ? " selected" : "") + ">" + esc(i.title + ", " + shortDay(i.start_date)) + "</option>";
      }).join("");
    }
    if (sms.scenario) $("#sms-scenario").value = sms.scenario;
    var msg = currentMessage();
    if (msg && msg.scenario) {
      var sc = msg.scenario, top = Math.max.apply(null, sc.rain_mm.concat([25]));
      $("#scenario-preview").innerHTML = '<div class="scenario-bars" aria-hidden="true">' + sc.rain_mm.map(function (v) {
        return '<span style="height:' + Math.max(4, v / top * 100) + '%"></span>'; }).join("") + "</div>" +
        "Rain each day: " + sc.rain_mm.map(function (v) { return v + " mm"; }).join(", ") + ". Result: " + esc(sc.status.label) + ".";
    }
    showOnPhone(msg);
    $("#sms-result").hidden = true;
  }

  var typingTimer = null;
  function showOnPhone(msg) {
    var thread = $("#phone-thread");
    clearTimeout(typingTimer);
    var limit = 160;
    var text = msg && msg.text;
    $("#phone-note").textContent = msg ? msg.note : "";
    var counter = $("#sms-counter");
    counter.textContent = text ? text.length + "/" + limit : "";
    counter.classList.toggle("over", !!text && text.length > limit);
    $("#sms-send").disabled = !text;
    var stamp = '<p class="phone-time">Today ' + new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date()) + "</p>";
    if (!text) {
      thread.innerHTML = stamp + '<p class="phone-time">No message: Imarika sends nothing when there is no alert.</p>';
      return;
    }
    var bubble = '<div class="bubble arrive">' + esc(text) + "</div>";
    if (reduceMotion) { thread.innerHTML = stamp + bubble; return; }
    thread.innerHTML = stamp + '<div class="typing" aria-hidden="true"><i></i><i></i><i></i></div>';
    typingTimer = setTimeout(function () { thread.innerHTML = stamp + bubble; }, 750);
  }

  function result(kind, text) {
    var box = $("#sms-result");
    box.className = "result " + kind;
    box.textContent = text;
    box.hidden = false;
  }

  function sendSms() {
    var raw = $("#sms-phone").value.replace(/[\s-]/g, "");
    if (!PHONE.test(raw)) {
      $("#sms-phone-error").textContent = "Enter a Kenyan mobile number in +254 format, for example +254712345678.";
      $("#sms-phone").focus();
      return;
    }
    var msg = currentMessage();
    if (!msg || !msg.text) return;
    var body = Object.assign({ phone: raw }, msg.body);
    var button = $("#sms-send");
    button.disabled = true;
    result("demo", "Sending to the sandbox...");
    var ctrl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, 15000);
    fetch("api/send-sms", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: ctrl ? ctrl.signal : undefined })
      .then(function (r) {
        var type = r.headers.get("content-type") || "";
        if (type.indexOf("application/json") < 0) throw new Error("unreachable");
        return r.json().then(function (j) { return { status: r.status, json: j }; });
      })
      .then(function (res) {
        if (res.json && res.json.ok) result("ok", "Sent to the sandbox: " + (res.json.detail || "accepted") + ". No real phone was contacted.");
        else result("fail", "Not sent: " + ((res.json && res.json.error) || "the sandbox refused the message") + ".");
      })
      .catch(function () { result("demo", "Demo mode: message shown on the phone only."); })
      .then(function () { clearTimeout(timer); button.disabled = false; });
  }

  function loadSms(stationId, key) {
    var s = byId(stationId);
    if (!s) return;
    state.sms.mode = "station"; state.sms.station = stationId; state.sms.key = key || s.insights[0].key;
    updateSms();
    $("#sms").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    setTimeout(function () { $("#sms-phone").focus({ preventScroll: true }); }, reduceMotion ? 0 : 600);
  }

  /* ----------------------------------------------------------- how it works */
  var STEPS = [
    ["Sensors", '<rect x="9" y="3" width="6" height="10" rx="3"/><path d="M12 13v8M8 21h8M5 8a7 7 0 0 0 0 0M4 6c-1 2-1 4 0 6M20 6c1 2 1 4 0 6"/>',
      "Weather stations in fields across Busia County measure temperature, humidity, wind, air pressure and rain through the day."],
    ["Checking", '<path d="M4 5h16M7 12h10M10 19h4"/>',
      "Faulty readings are removed, short gaps are filled, and the station records are lined up with satellite and weather-model data."],
    ["Forecasting", '<path d="M3 17l5-5 4 3 8-8"/><path d="M15 7h5v5"/>',
      "A forecasting model trained on past readings predicts rain and temperature for each of the next 7 days at every station."],
    ["Rules", '<path d="M9 4h6M12 4v4M5 8h14l-2 12H7L5 8z"/><path d="m9 14 2 2 4-4"/>',
      "Simple rules based on Kenya Meteorological Department rain levels and published planting guidance turn the forecast into advice."],
    ["Text message", '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M9 7h6M9 10h4M10 18h4"/>',
      "Each piece of advice becomes one short text message with one clear action, so any phone can receive it."],
    ["Web and map", '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14"/>',
      "This website shows the same forecast and advice for every station, and works offline once it has been opened."]
  ];
  function renderPipeline() {
    $("#pipeline").innerHTML = STEPS.map(function (s, i) {
      return '<li class="reveal"><div class="icon"><svg viewBox="0 0 24 24" aria-hidden="true">' + s[1] + "</svg></div>" +
        "<h3><span class=\"sr-only\">Step " + (i + 1) + ": </span>" + s[0] + "</h3><p>" + s[2] + "</p></li>";
    }).join("");
  }

  /* ------------------------------------------------------------------ trust */
  var CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7"/></svg>';
  var CAUTION = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.5"/></svg>';
  function sourceLine(src) { return src ? '<span class="source">Source: ' + esc(src) + "</span>" : ""; }
  function fmtValue(v, unit) {
    if (v == null) return "–";
    var digits = v < 1 ? 2 : 1;
    return "about " + v.toFixed(digits) + " " + unit;
  }

  function renderTrust() {
    var m = state.models || {}, q = state.quality || {};
    var sar = m.sarima || {}, lstm = m.lstm || {};
    var vars = sar.variables || [];
    var html = [];
    var station = sar.station ? " (" + esc(sar.station) + ")" : "";
    var table = vars.length ? '<table class="acc-table"><thead><tr><th scope="col">Measurement</th><th scope="col">Typical error one hour ahead</th><th scope="col">Better than "no change"</th></tr></thead><tbody>' +
      vars.map(function (v) {
        var pct = v.skill_vs_persistence == null ? null : Math.round(v.skill_vs_persistence * 100);
        return "<tr><th scope=\"row\">" + esc(v.label) + "</th><td>" + esc(fmtValue(v.typical_error, v.unit)) + "</td><td>" +
          (pct == null ? "–" : '<span class="better">' + pct + '% better</span><div class="bar-track"><div class="bar-fill" style="width:' + Math.max(0, Math.min(100, pct)) + '%"></div></div>') + "</td></tr>";
      }).join("") + "</tbody></table>" + sourceLine(vars[0].source)
      : '<p class="muted">No measured accuracy results are available.</p>';
    html.push('<div class="card wide reveal"><h3>What has been measured</h3><p>The forecasting approach, ' + esc(sar.plain_name || "a statistical model") +
      ", was tested one hour ahead at one station" + station + " on recent data that was kept aside for testing. Each result is compared with simply assuming the weather stays the same as an hour earlier.</p>" + table + "</div>");

    var limits = [
      "Accuracy was measured only one hour ahead, at one station.",
      "The accuracy of the 7-day forecasts has not been measured yet.",
      "Forecast settings are shared across all stations; they are not tuned for each station.",
      "Rain is harder to predict than temperature."
    ];
    if (lstm.plain_name) limits.push("A second approach, " + lstm.plain_name + ", was tested: " + (lstm.status || "") );
    html.push('<div class="card reveal"><h3>What has not been measured</h3><ul class="check-list caution">' +
      limits.map(function (l) { return "<li>" + CAUTION + "<span>" + esc(l) + "</span></li>"; }).join("") + "</ul></div>");

    var audit = (q.audit || []).map(function (a) {
      var value = fmtNumber(a.value) + (a.of ? " of " + fmtNumber(a.of) : "");
      return '<li><span class="num">' + value + "</span><span>" + esc(a.label) + "</span>" + sourceLine(a.source) + "</li>";
    });
    if (q.temperature_agreement) {
      var t = q.temperature_agreement;
      audit.push('<li><span class="num">' + t.min.toFixed(2) + "–" + t.max.toFixed(2) + "</span><span>" + esc(t.label) + "</span>" + sourceLine(t.source) + "</li>");
    }
    var fixes = (q.fixes || []).map(function (f) {
      return '<li><span class="num">' + fmtNumber(f.value) + "</span><span>" + esc(f.label) + (f.unit ? " (" + esc(f.unit) + ")" : "") + "</span>" + sourceLine(f.source) + "</li>";
    });
    html.push('<div class="card reveal"><h3>Data checks and fixes</h3><ul class="fix-list">' + audit.concat(fixes).join("") + "</ul>" +
      ((q.other_fixes || []).length ? '<ul class="check-list" style="margin-top:1rem">' + q.other_fixes.map(function (f) { return "<li>" + CHECK + "<span>" + esc(f) + "</span></li>"; }).join("") + "</ul>" + sourceLine(q.other_fixes_source) : "") + "</div>");

    html.push('<div class="card wide reveal"><h3>Use it wisely</h3><ul class="check-list">' +
      state.meta.reliability.map(function (l) { return "<li>" + CHECK + "<span>" + esc(l) + "</span></li>"; }).join("") + "</ul></div>");
    $("#trust-body").innerHTML = html.join("");
  }

  /* ------------------------------------------------------------------ about */
  function renderAbout() {
    var c = state.meta.credits || {}, b = c.boundaries || {};
    var sources = (c.data_sources || []).map(function (s) { return "<li><strong>" + esc(s.name) + "</strong>: " + esc(s.detail) + "</li>"; }).join("");
    var layers = (b.layers || []).map(function (l) {
      return "<li>" + esc(l.name) + " (" + esc(l.level) + "): " + esc(l.source) + ". " + esc(l.licence) + ".</li>";
    }).join("");
    $("#about-body").innerHTML =
      '<div class="card reveal"><h3>Data sources</h3><ul>' + sources + "</ul></div>" +
      '<div class="card reveal"><h3>Maps</h3><p>' + esc(b.headline) + "</p><ul>" + layers + '</ul><p class="small muted" style="margin-top:.8rem">' + esc(b.citation) + "</p>" +
      '<p class="small muted" style="margin:0">Map tiles &copy; CARTO. Map data &copy; OpenStreetMap contributors.</p></div>' +
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
      if (show) {
        select(show.getAttribute("data-show-station"), { from: "list" });
        $("#map-section").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth" });
      }
    });
    var toggle = $("#present-toggle");
    var apply = function (on) {
      document.body.classList.toggle("present", on);
      toggle.setAttribute("aria-pressed", on ? "true" : "false");
      if (state.map) setTimeout(function () { state.map.invalidateSize(); }, 50);
    };
    toggle.addEventListener("click", function () {
      var on = !document.body.classList.contains("present");
      apply(on);
      try { localStorage.setItem("imarika-present", on ? "1" : "0"); } catch (err) { /* private mode */ }
    });
    try { if (localStorage.getItem("imarika-present") === "1") apply(true); } catch (err) { /* ignore */ }
    window.addEventListener("online", offlineBanner);
    window.addEventListener("offline", offlineBanner);
  }

  function offlineBanner(force) {
    var banner = $("#offline-banner");
    var off = force === true || !navigator.onLine;
    if (off && state.meta) {
      banner.textContent = "Showing saved forecast from " + longDate(state.meta.as_of) + ". You appear to be offline.";
      banner.hidden = false;
    } else banner.hidden = true;
  }

  function reveal() {
    var items = document.querySelectorAll(".reveal");
    if (reduceMotion || !("IntersectionObserver" in window)) {
      items.forEach(function (el) { el.classList.add("shown"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add("shown"); io.unobserve(en.target); } });
    }, { rootMargin: "0px 0px -40px 0px" });
    items.forEach(function (el) { io.observe(el); });
  }

  function fail(err) {
    $("#panel").innerHTML = '<p class="muted">The forecast data could not be loaded. Please try again later.</p>';
    if (window.console) console.error(err);
  }

  function start() {
    Promise.all([
      getJSON("data/meta.json"), getJSON("data/stations.json"), getJSON("data/scenarios.json"),
      getJSON("data/model_results.json").catch(function () { return null; }),
      getJSON("data/data_quality.json").catch(function () { return null; })
    ]).then(function (res) {
      state.meta = res[0]; state.stations = res[1].stations || []; state.scenarios = res[2].scenarios || [];
      state.models = res[3]; state.quality = res[4];
      renderHero(); renderLegend(); renderList(); wireEvents(); offlineBanner();
      var geoPath = state.meta.boundary_file || "data/kenya_boundaries.geojson";
      getJSON(geoPath).catch(function () { return null; }).then(function (geo) {
        if (typeof L !== "undefined") initMap(geo);
        else { $("#map-notice").textContent = "The map could not be loaded."; $("#map-notice").hidden = false; }
        var first = state.stations.slice().sort(severitySort)[0];
        if (first) select(first.id, { from: "init" });
      });
      renderAlerts(); renderSmsDemo(); renderPipeline(); renderTrust(); renderAbout(); reveal();
    }).catch(fail);

    if ("serviceWorker" in navigator && location.protocol !== "file:") {
      navigator.serviceWorker.register("sw.js").catch(function () { /* optional */ });
      navigator.serviceWorker.addEventListener("message", function (e) {
        if (e.data && e.data.type === "offline") offlineBanner(true);
      });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
