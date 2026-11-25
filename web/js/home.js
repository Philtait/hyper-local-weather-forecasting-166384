/* Home page: the stations summary line and a small station map. All numbers come from data/.
   The circles use the same three states as the Weather map (js/map-logic.js). */
(function () {
  "use strict";
  var I = window.Imarika, M = window.ImarikaMapLogic;
  var STYLE = {
    today: { fill: "#1f7a4d", stroke: "#ffffff", width: 2.5 },
    recent: { fill: "#b5e2c6", stroke: "#1f7a4d", width: 3.5 },
    off: { fill: "#ffffff", stroke: "#6b7280", width: 2.5 }
  };

  function rings(geometry) {
    return (geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates).map(function (p) { return p[0]; });
  }

  function miniMap(geo, stations, day) {
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
    // Stations not reporting first, so the ones sending data sit on top.
    stations.slice().sort(function (a, b) { return M.STATES.indexOf(M.stateOf(b, day)) - M.STATES.indexOf(M.stateOf(a, day)); })
      .forEach(function (s) {
        if (s.lat == null) return;
        var st = STYLE[M.stateOf(s, day)];
        out.push('<circle cx="' + x(s.lon).toFixed(1) + '" cy="' + y(s.lat).toFixed(1) + '" r="9" fill="' + st.fill +
          '" stroke="' + st.stroke + '" stroke-width="' + st.width + '"/>');
      });
    out.push("</svg>");
    return out.join("");
  }

  function dayWord(day) {
    return day === I.localDay(new Date().toISOString()) ? I.t("time.today") : I.longDate(day);
  }

  I.ready.then(function (meta) {
    return Promise.all([I.getJSON("data/stations.json"), I.getJSON(meta.boundary_file).catch(function () { return null; })])
      .then(function (res) {
        var stations = res[0].stations, geo = res[1], day = M.dataDay(meta, stations), counts = {};
        stations.forEach(function (s) { var code = M.stateOf(s, day); counts[code] = (counts[code] || 0) + 1; });
        if (meta.latest_reading_utc) {
          // Plain counts from the data: stations that sent data recently and the newest reading, in Kenya time.
          document.getElementById("hero-week-text").textContent = I.t("home.hero_data", {
            n: stations.filter(function (s) { return s.active; }).length, total: stations.length,
            date: I.longDate(I.localDay(meta.latest_reading_utc)), time: I.clock(meta.latest_reading_utc) });
          document.getElementById("hero-week").hidden = false;
        }
        document.getElementById("status-counts").innerHTML = M.STATES.filter(function (code) { return counts[code]; })
          .map(function (code) {
            return '<li><span class="dot dot-' + code + '" aria-hidden="true"></span><span>' +
              I.esc(I.t("map.state_" + code, { day: dayWord(day) })) + '</span><strong>' + counts[code] + "</strong></li>";
          }).join("");
        if (geo) document.getElementById("mini-map").innerHTML = miniMap(geo, stations, day);
      });
  }).catch(function (err) { if (window.console) console.error(err); });
})();
