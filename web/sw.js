/* Imarika service worker: app shell and data, stale-while-revalidate.
   Bump VERSION when shell files change so old caches are removed. */
"use strict";

var VERSION = "imarika-v5";
var SHELL = [
  "./", "index.html", "how-it-works.html", "map.html", "sms.html", "contact.html",
  "css/styles.css", "js/common.js", "js/home.js", "js/map.js", "js/charts.js", "js/sms.js", "js/contact.js", "i18n/en.json", "manifest.webmanifest",
  "vendor/leaflet/leaflet.js", "vendor/leaflet/leaflet.css",
  "fonts/lexend-latin-400-normal.woff2", "fonts/lexend-latin-500-normal.woff2", "fonts/lexend-latin-600-normal.woff2",
  "fonts/lexend-latin-700-normal.woff2",
  "img/icons.svg", "img/about-field.webp", "img/station.webp", "img/farmer.webp",
  "icons/icon-192.png", "icons/icon-512.png"
];
var DATA = ["data/meta.json", "data/stations.json", "data/scenarios.json", "data/model_results.json",
            "data/data_quality.json", "data/kenya_boundaries.geojson", "contact.json"];

self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(VERSION).then(function (cache) {
    return cache.addAll(SHELL).then(function () {
      // Data files are cached one by one, so a missing optional file does not block install.
      return Promise.all(DATA.map(function (url) { return cache.add(url).catch(function () { return null; }); }));
    });
  }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (key) { return key !== VERSION; }).map(function (key) { return caches.delete(key); }));
  }).then(function () { return self.clients.claim(); }));
});

function tellOffline() {
  self.clients.matchAll({ type: "window" }).then(function (clients) {
    clients.forEach(function (client) { client.postMessage({ type: "offline" }); });
  });
}

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;               // the SMS function is never cached
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;    // map tiles are never cached
  if (url.pathname.indexOf("/api/") >= 0) return;

  event.respondWith(caches.open(VERSION).then(function (cache) {
    // Pages are cached under their own path; "/" is the Home page.
    var key = request.mode === "navigate" && /\/$/.test(url.pathname) ? "index.html" : request;
    return cache.match(key, { ignoreSearch: true }).then(function (cached) {
      var network = fetch(request).then(function (response) {
        if (response && response.ok) cache.put(key, response.clone());
        return response;
      }).catch(function () {
        if (cached) tellOffline();
        return cached || Response.error();
      });
      if (cached) { event.waitUntil(network.catch(function () { return null; })); return cached; }
      return network;
    });
  }));
});
