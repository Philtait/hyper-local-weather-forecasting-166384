/* Home page: numbers, the week's headline and a small station map. All numbers come from data/. */
(function () {
  "use strict";
  var I = window.Imarika;

  function rings(geometry) {
    return (geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates).map(function (p) { return p[0]; });
  }

  function miniMap(geo, stations) {
    var features = geo.features || [];
    var county = features.filter(function (f) { return f.properties.level === "county"; })[0];
    var areas = features.filter(function (f) { return f.properties.level === "sub-county"; });
    if (!county) return "";
    var pts = [].concat.apply([], rings(county.geometry));
    var lons = pts.map(function (p) { return p[0]; }), lats = pts.map(function (p) { return p[1]; });
    var minX = Math.min.apply(null, lons), maxX = Math.max.apply(null, lons);
    var minY = Math.min.apply(null, lats), maxY = Math.max.apply(null, lats);
    var W = 600, pad = 24, cosLat = Math.cos(((minY + maxY) / 2) * Math.PI / 180);
    var k = (W - 2 * pad) / ((maxX - minX) * cosLat);
    var H = Math.round((maxY - minY) * k + 2 * pad);
    function x(lon) { return pad + (lon - minX) * cosLat * k; }
    function y(lat) { return pad + (maxY - lat) * k; }
    function path(g) {
      return rings(g).map(function (ring) {
        return ring.map(function (p, i) { return (i ? "L" : "M") + x(p[0]).toFixed(1) + " " + y(p[1]).toFixed(1); }).join("") + "Z";
      }).join("");
    }
    var out = ['<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + I.esc(I.t("home.stations_map_label")) + '">'];
    areas.forEach(function (f) {
      out.push('<path d="' + path(f.geometry) + '" fill="#ffffff" stroke="#b9cdc1" stroke-width="1.2"/>');
    });
    out.push('<path d="' + path(county.geometry) + '" fill="none" stroke="#0f3d2e" stroke-width="2.5"/>');
    areas.forEach(function (f) {
      var r = rings(f.geometry)[0], cx = 0, cy = 0;
      r.forEach(function (p) { cx += p[0]; cy += p[1]; });
      cx /= r.length; cy /= r.length;
      out.push('<text x="' + x(cx).toFixed(1) + '" y="' + y(cy).toFixed(1) + '" text-anchor="middle" font-size="15" font-weight="600" fill="#4a5a52">' +
        I.esc(f.properties.name) + "</text>");
    });
    var fg = { warning: "#b3261e", watch: "#b45309", planting: "#166534", delay: "#1d4ed8", none: "#475569", offline: "#6b7280" };
    stations.slice().sort(function (a, b) { return I.ORDER[b.status.code] - I.ORDER[a.status.code]; }).forEach(function (s) {
      if (s.lat == null) return;
      var off = s.status.code === "offline";
      out.push('<circle cx="' + x(s.lon).toFixed(1) + '" cy="' + y(s.lat).toFixed(1) + '" r="9" fill="' + (off ? "#ffffff" : fg[s.status.code]) +
        '" stroke="' + (off ? fg.offline : "#ffffff") + '" stroke-width="2.5"' + (off ? ' stroke-dasharray="4 3"' : "") + "/>");
    });
    out.push("</svg>");
    return out.join("");
  }

  I.ready.then(function (meta) {
    return Promise.all([I.getJSON("data/stations.json"), I.getJSON(meta.boundary_file).catch(function () { return null; })])
      .then(function (res) {
        var stations = res[0].stations, geo = res[1];
        var areas = {}, days = 0, counts = {};
        stations.forEach(function (s) {
          if (s.area) areas[s.area] = true;
          days = Math.max(days, s.forecast.length);
          counts[s.status.code] = (counts[s.status.code] || 0) + 1;
        });
        var values = { stations: stations.length, areas: Object.keys(areas).length,
                       reporting: stations.filter(function (s) { return s.active; }).length, days: days };
        Array.prototype.forEach.call(document.querySelectorAll("[data-count]"), function (el) {
          el.textContent = values[el.getAttribute("data-count")];
        });
        document.getElementById("updated").textContent =
          I.t("home.updated", { time: I.clock(meta.generated_at) + ", " + I.longDate(I.localDay(meta.generated_at)) });
        if (meta.headline_week) {
          // The label already says "This week:", so drop the sentence's own closing "this week".
          document.getElementById("hero-week-text").textContent = meta.headline_week.replace(/\s+this week(\.?)\s*$/i, "$1");
          document.getElementById("hero-week").hidden = false;
        }
        document.getElementById("status-counts").innerHTML = Object.keys(I.ORDER).filter(function (code) { return counts[code]; })
          .map(function (code) {
            var n = counts[code];
            return "<li>" + I.badge(code) + ' <span class="muted">' + n + " " + I.esc(I.t(n === 1 ? "home.station_word" : "home.stations_word")) + "</span></li>";
          }).join("");
        if (geo) document.getElementById("mini-map").innerHTML = miniMap(geo, stations);
      });
  }).catch(function (err) { if (window.console) console.error(err); });
})();
