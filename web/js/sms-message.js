/* Crop guide SMS builder, shared by the SMS demo page (window.ImarikaSmsMessage) and the send function
   (require). One builder means the preview and the sent text cannot differ.
   Rules (same as scripts/sms_insight_test.py): "Imarika <area>:" then sentences 1, 3 and 2 of the
   crop guide entry, each added only if the whole message stays within 306 characters (two SMS parts);
   a sentence is never cut; plain GSM characters only. */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ImarikaSmsMessage = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var MAX_CHARS = 306;
  var CROPS = ["maize", "beans", "sorghum", "cassava", "cowpeas", "sweet potatoes"];
  var LANGS = ["en", "sw"];
  var ORDER = [0, 2, 1];
  // Characters outside the GSM 03.38 basic set (or in its two-character extension table).
  var PLAIN = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "…": "...", " ": " ", " ": " ", "°": " deg",
    "{": "(", "}": ")", "[": "(", "]": ")", "~": "-", "^": "", "|": "/", "\\": "/", "€": "EUR"
  };
  var GSM_BASIC = "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛ" +
    "ΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡" +
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà";

  function own(object, key) {
    return object !== null && typeof object === "object" && Object.prototype.hasOwnProperty.call(object, key);
  }

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

  function entryFor(guide, station, crop, lang) {
    if (CROPS.indexOf(crop) < 0 || LANGS.indexOf(lang) < 0) return null;
    var stations = guide && guide.stations;
    if (!own(stations, station)) return null;
    var crops = stations[station].crops;
    if (!own(crops, crop) || !own(crops[crop], lang)) return null;
    return { area: stations[station].area, entry: crops[crop][lang] };
  }

  /* {text, chars, parts} for one station, crop and language, or null if the guide has no such entry. */
  function buildCropMessage(guide, station, crop, lang) {
    var found = entryFor(guide, station, crop, lang);
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
    if (!used) return null;
    return { text: message, chars: message.length, parts: smsParts(message.length) };
  }

  /* Station ids that have a crop guide entry. */
  function stations(guide) {
    return guide && guide.stations && typeof guide.stations === "object" ? Object.keys(guide.stations) : [];
  }

  return { MAX_CHARS: MAX_CHARS, CROPS: CROPS, LANGS: LANGS, gsm: gsm, smsParts: smsParts,
           buildCropMessage: buildCropMessage, stations: stations };
});
