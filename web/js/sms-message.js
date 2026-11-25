/* SMS builder shared by the SMS demo page (window.ImarikaSmsMessage) and the send function (require).
   The page previews buildMessage(...) and the function sends buildMessage(...) with the same data
   files, so the preview and the sent text cannot differ.

   Messages (all built from the deployed data, never from text typed in the browser):
   - latest:     the station's last reading (date and time in Kenya time, EAT) and a one-line outlook
                 from the first forecast day; the outlook is dropped if the message would pass 306
                 characters, the date and time never are.
   - crop_guide: "Imarika <area>:" then crop guide sentences 1, 3 and 2, each only if it fits.
   - alert:      the engine's advice for the station, only when there is one (English wording).
   Every message: at most 306 characters (two SMS), whole sentences only, plain GSM characters. */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory(require("./units.js"));
  else root.ImarikaSmsMessage = factory(root.ImarikaUnits);
})(typeof self !== "undefined" ? self : this, function (U) {
  "use strict";

  var MAX_CHARS = 306;
  var CROPS = ["maize", "beans", "sorghum", "cassava", "cowpeas", "sweet potatoes"];
  var LANGS = ["en", "sw"];
  var SCENARIOS = ["latest", "crop_guide", "alert"];
  var ORDER = [0, 2, 1];
  var EAT_OFFSET_MS = 3 * 3600 * 1000;  // Africa/Nairobi is UTC+3 all year, no daylight saving
  var WEEKDAYS = {
    en: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
    sw: ["Jumapili", "Jumatatu", "Jumanne", "Jumatano", "Alhamisi", "Ijumaa", "Jumamosi"]
  };
  var MONTHS = {
    en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    sw: ["Januari", "Februari", "Machi", "Aprili", "Mei", "Juni", "Julai", "Agosti", "Septemba", "Oktoba",
         "Novemba", "Desemba"]
  };
  // Characters outside the GSM 03.38 basic set (or in its two-character extension table).
  var PLAIN = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "…": "...", " ": " ", " ": " ", "°": "",
    "{": "(", "}": ")", "[": "(", "]": ")", "~": "-", "^": "", "|": "/", "\\": "/", "€": "EUR"
  };
  var GSM_BASIC = "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛ" +
    "ΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡" +
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà";

  function own(object, key) {
    return object !== null && typeof object === "object" && Object.prototype.hasOwnProperty.call(object, key);
  }
  function isNumber(value) { return typeof value === "number" && isFinite(value); }

  /* Plain GSM text: known replacements, then accents removed, then anything else dropped. */
  function gsm(text) {
    var out = "";
    var source = String(text == null ? "" : text);
    for (var i = 0; i < source.length; i++) {
      var ch = own(PLAIN, source[i]) ? PLAIN[source[i]] : source[i];
      for (var j = 0; j < ch.length; j++) {
        var c = ch[j];
        if (GSM_BASIC.indexOf(c) >= 0) { out += c; continue; }
        var plain = c.normalize ? c.normalize("NFKD").replace(/[^\x00-\x7f]/g, "") : "";
        for (var k = 0; k < plain.length; k++) if (GSM_BASIC.indexOf(plain[k]) >= 0) out += plain[k];
      }
    }
    return out.replace(/ {2,}/g, " ").trim();
  }

  function smsParts(length) { return length <= 160 ? 1 : Math.ceil(length / 153); }
  function result(text) { return { text: text, chars: text.length, parts: smsParts(text.length) }; }
  function pad(n) { return (n < 10 ? "0" : "") + n; }

  /* "Tue 24 Nov, 20:26" (en) or "Jumanne 24 Novemba, 20:26" (sw), in EAT, from an ISO UTC time. */
  function readingTime(isoUtc, lang) {
    var ms = Date.parse(isoUtc);
    if (!isFinite(ms)) return null;
    var d = new Date(ms + EAT_OFFSET_MS);
    return WEEKDAYS[lang][d.getUTCDay()] + " " + d.getUTCDate() + " " + MONTHS[lang][d.getUTCMonth()] + ", " +
      pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes());
  }

  /* "Wed 25 Nov" or "Jumatano 25 Novemba" for a forecast date "YYYY-MM-DD" (a calendar day in Kenya). */
  function dayLabel(isoDate, lang) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(isoDate || ""));
    if (!m) return null;
    var d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    return WEEKDAYS[lang][d.getUTCDay()] + " " + d.getUTCDate() + " " + MONTHS[lang][d.getUTCMonth()];
  }

  function rainAmount(mm) { return U.rainText(mm); }   // the shared rule in js/units.js

  /* A station can be chosen only while it is sending data (active in the forecast export). */
  function selectable(station) { return !!station && station.active === true; }

  function findStation(stations, id) {
    if (!Array.isArray(stations) || typeof id !== "string") return null;
    for (var i = 0; i < stations.length; i++) if (stations[i] && stations[i].id === id) return stations[i];
    return null;
  }

  function buildLatestMessage(station, lang) {
    if (!selectable(station) || LANGS.indexOf(lang) < 0) return null;
    var latest = station.latest || {};
    var when = readingTime(latest.time_utc || station.last_reading_utc, lang);
    if (!when) return null;
    var sw = lang === "sw";
    var values = [];
    if (isNumber(latest.temperature_c)) values.push(sw ? "joto nyuzi joto " + U.temp(latest.temperature_c)
                                                       : "temp " + U.temp(latest.temperature_c) + "C");
    if (isNumber(latest.humidity_pct)) values.push((sw ? "unyevu wa hewa " : "humidity ") + Math.round(latest.humidity_pct) + "%");
    if (isNumber(latest.rain_since_morning_mm)) values.push(sw ? "mvua tangu 08:00: milimita " + rainAmount(latest.rain_since_morning_mm)
                                                               : "rain since 08:00: " + rainAmount(latest.rain_since_morning_mm) + " mm");
    var head = "Imarika " + (station.area || "Busia") + (sw ? ", kituo " : ", station ") + station.id + ".";
    var reading = (sw ? "Kipimo cha mwisho " : "Last reading ") + when + (values.length ? ": " + values.join(", ") : "") + ".";
    var message = gsm(head + " " + reading);

    var day = (station.forecast || [])[0];
    if (day && isNumber(day.rain_mm) && isNumber(day.temp_max)) {
      var rain = rainAmount(day.rain_mm), temp = U.temp(day.temp_max);
      var flat = isNumber(station.forecast_variation_mm) && station.forecast_variation_mm < 1.0;
      var label = dayLabel(day.date, lang);
      var outlook;
      if (sw) {
        outlook = flat
          ? "Mwelekeo: mvua karibu milimita " + rain + " na joto la juu nyuzi joto " + temp + " kila siku, kama siku za hivi karibuni."
          : "Mwelekeo" + (label ? " " + label : "") + ": mvua karibu milimita " + rain + " na joto la juu nyuzi joto " + temp + ".";
      } else {
        outlook = flat
          ? "Outlook: about " + rain + " mm rain and up to " + temp + "C each day, similar to recent days."
          : "Outlook" + (label ? " " + label : "") + ": about " + rain + " mm rain and up to " + temp + "C.";
      }
      var candidate = message + " " + gsm(outlook);
      if (candidate.length <= MAX_CHARS) message = candidate;  // drop the outlook, never the reading time
    }
    return message.length <= MAX_CHARS ? result(message) : null;
  }

  /* The engine's advice for the station (planting, spraying or flood), only if there is one. */
  function buildAlertMessage(station) {
    if (!selectable(station)) return null;
    var insight = (station.insights || [])[0];
    if (!insight || !insight.sms_text) return null;
    var text = gsm(insight.sms_text);
    return text && text.length <= MAX_CHARS ? result(text) : null;
  }

  function cropEntry(guide, station, crop, lang) {
    if (CROPS.indexOf(crop) < 0 || LANGS.indexOf(lang) < 0) return null;
    var stations = guide && guide.stations;
    if (!own(stations, station)) return null;
    var crops = stations[station].crops;
    if (!own(crops, crop) || !own(crops[crop], lang)) return null;
    return { area: stations[station].area, entry: crops[crop][lang] };
  }

  /* {text, chars, parts} for one station, crop and language, or null if the guide has no such entry. */
  function buildCropMessage(guide, station, crop, lang) {
    var found = cropEntry(guide, station, crop, lang);
    if (!found || !found.entry || !Array.isArray(found.entry.sentences) || found.entry.sentences.length !== 3) return null;
    var message = gsm("Imarika " + (found.area || "Busia") + ":");
    var used = 0;
    for (var i = 0; i < ORDER.length; i++) {
      var sentence = found.entry.sentences[ORDER[i]];
      var text = gsm(sentence && sentence.text);
      if (!text || /\{(RAIN|TEMP)\}|\((RAIN|TEMP)\)/.test(text)) return null;  // never send unfilled placeholders
      var candidate = message + " " + text;
      if (candidate.length <= MAX_CHARS) { message = candidate; used += 1; }
    }
    return used ? result(message) : null;
  }

  /* Station ids that have a crop guide entry. */
  function guideStations(guide) {
    return guide && guide.stations && typeof guide.stations === "object" ? Object.keys(guide.stations) : [];
  }

  /* Which messages a station offers: {latest, crop_guide, alert} as true or false. */
  function available(data, stationId) {
    var station = findStation(data && data.stations, stationId);
    return {
      latest: !!buildLatestMessage(station, "en"),
      crop_guide: selectable(station) && !!buildCropMessage(data && data.guide, stationId, CROPS[0], "en"),
      alert: !!buildAlertMessage(station)
    };
  }

  /* The one entry point: data = {stations: [...], guide: {...}}; request = {scenario, station, crop, lang}. */
  function buildMessage(data, request) {
    request = request || {};
    if (SCENARIOS.indexOf(request.scenario) < 0 || LANGS.indexOf(request.lang) < 0) return null;
    var station = findStation(data && data.stations, request.station);
    if (!selectable(station)) return null;
    if (request.scenario === "latest") return buildLatestMessage(station, request.lang);
    if (request.scenario === "alert") return buildAlertMessage(station);
    return buildCropMessage(data && data.guide, station.id, request.crop, request.lang);
  }

  return { MAX_CHARS: MAX_CHARS, CROPS: CROPS, LANGS: LANGS, SCENARIOS: SCENARIOS, gsm: gsm, smsParts: smsParts,
           readingTime: readingTime, dayLabel: dayLabel, selectable: selectable, findStation: findStation,
           buildLatestMessage: buildLatestMessage, buildAlertMessage: buildAlertMessage,
           buildCropMessage: buildCropMessage, stations: guideStations, available: available,
           buildMessage: buildMessage };
});
