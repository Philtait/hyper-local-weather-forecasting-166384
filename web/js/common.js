/* Shared helpers for every Imarika page: strings, dates, icons, data and offline support.
   Pages wait for Imarika.ready, which resolves once the strings in i18n/en.json are loaded. */
(function () {
  "use strict";

  var TZ = "Africa/Nairobi";
  var ORDER = { warning: 0, watch: 1, planting: 2, delay: 3, none: 4, offline: 5 };
  var STATUS_ICON = { warning: "warning", watch: "drop", planting: "sprout", delay: "spray", none: "check", offline: "offline" };
  var strings = {};

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  /* t("map.title", {count: 3}) returns the string with {count} filled in. */
  function t(key, values) {
    var node = key.split(".").reduce(function (n, part) { return n && n[part]; }, strings);
    var text = typeof node === "string" ? node : key;
    return text.replace(/\{(\w+)\}/g, function (m, name) { return values && name in values ? values[name] : m; });
  }
  function icon(name, cls) {
    return '<svg class="icon' + (cls ? " " + cls : "") + '" aria-hidden="true" focusable="false"><use href="img/icons.svg#' + name + '"></use></svg>';
  }
  function badge(code) {
    return '<span class="badge st-' + esc(code) + '">' + icon(STATUS_ICON[code] || "info") + "<span>" + esc(t("status." + code)) + "</span></span>";
  }

  /* Dates: forecast dates are calendar days; reading times are UTC and shown in Kenya time. */
  function parts(isoUtc) {
    var f = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, year: "numeric", month: "numeric", day: "numeric",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(isoUtc));
    var out = {};
    f.forEach(function (p) { out[p.type] = p.value; });
    return out;
  }
  function dayOfDate(iso) {
    var p = String(iso).split("-").map(Number);
    return new Date(Date.UTC(p[0], p[1] - 1, p[2]));
  }
  function weekday(iso) { return strings.days[dayOfDate(iso).getUTCDay()]; }
  function longDate(iso) {
    var d = dayOfDate(iso);
    return weekday(iso) + " " + d.getUTCDate() + " " + strings.months[d.getUTCMonth()];
  }
  function localDay(isoUtc) { var p = parts(isoUtc); return p.year + "-" + p.month.padStart(2, "0") + "-" + p.day.padStart(2, "0"); }
  function clock(isoUtc) { var p = parts(isoUtc); return p.hour + ":" + p.minute; }
  /* "06:40 today", "06:40 yesterday" or "06:40, Monday 23 November" relative to the forecast issue day. */
  function whenSeen(isoUtc, today) {
    var day = localDay(isoUtc), time = clock(isoUtc);
    if (day === today) return time + " " + t("time.today");
    var prev = new Date(dayOfDate(today).getTime() - 86400000).toISOString().slice(0, 10);
    if (day === prev) return time + " " + t("time.yesterday");
    return time + ", " + longDate(day);
  }
  function rainClass(mm) {
    if (mm >= 50) return "very_heavy";
    if (mm >= 20) return "heavy";
    if (mm >= 1) return "some";
    return "dry";
  }
  function round(n) { return Math.round(n); }

  function getJSON(path) {
    return fetch(path).then(function (r) { if (!r.ok) throw new Error(path + " " + r.status); return r.json(); });
  }

  /* Offline support: the service worker says when it served a saved copy. */
  function showOffline(meta) {
    var note = document.getElementById("offline-note");
    if (!note || !meta) return;
    note.textContent = t("offline.saved", { date: longDate(meta.as_of) });
    note.hidden = false;
  }
  var metaForOffline = null;
  function register() {
    if (!("serviceWorker" in navigator) || location.protocol === "file:") return;
    navigator.serviceWorker.register("sw.js").catch(function () { return null; });
    navigator.serviceWorker.addEventListener("message", function (event) {
      if (event.data && event.data.type === "offline") showOffline(metaForOffline);
    });
  }
  window.addEventListener("offline", function () { showOffline(metaForOffline); });

  var ready = getJSON("i18n/en.json").then(function (data) {
    strings = data;
    return getJSON("data/meta.json");
  }).then(function (meta) {
    metaForOffline = meta;
    if (!navigator.onLine) showOffline(meta);
    return meta;
  });

  register();

  window.Imarika = {
    ready: ready, t: t, esc: esc, icon: icon, badge: badge, ORDER: ORDER, STATUS_ICON: STATUS_ICON,
    weekday: weekday, longDate: longDate, clock: clock, localDay: localDay, whenSeen: whenSeen,
    rainClass: rainClass, round: round, getJSON: getJSON,
    bySeverity: function (a, b) {
      return (ORDER[a.status.code] - ORDER[b.status.code]) || a.id.localeCompare(b.id, "en", { numeric: true });
    }
  };
})();
