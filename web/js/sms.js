/* SMS demo: choose a station and a message, preview the exact text, send it to the sandbox simulator.
   Messages come from the station's own data: latest readings, the crop guide, or the weather alert.
   js/sms-message.js builds the text here and in the send function, so the preview is what is sent.
   The browser sends only which message was chosen: {scenario, station, crop, lang, phone}. */
(function () {
  "use strict";
  var I = window.Imarika, t = I.t, esc = I.esc, B = window.ImarikaSmsMessage;
  var PHONE = /^\+254[17]\d{8}$/;
  var state = { stations: [], guide: null, station: null, scenario: "latest", crop: B.CROPS[0], lang: "en", area: null, showOff: false };

  function $(id) { return document.getElementById(id); }
  function data() { return { stations: state.stations, guide: state.guide }; }
  function areaOf(s) { return s.area || "Busia"; }
  function areas() {
    var seen = {};
    state.stations.forEach(function (s) { seen[areaOf(s)] = true; });
    return Object.keys(seen).sort();
  }

  function stationButton(s) {
    var ok = B.selectable(s);
    var meta = ok ? t("sms.last_reading", { time: B.readingTime(s.last_reading_utc, "en") }) : t("sms.not_sending");
    return '<button type="button" class="station-btn" data-station="' + esc(s.id) + '" aria-pressed="' +
      (s.id === state.station) + '"' + (ok ? "" : " disabled") + ' aria-label="' +
      esc(t("sms.station_label", { area: areaOf(s), id: s.id }) + ". " + meta) + '"><span class="station-name">' +
      esc(t("sms.station_short", { id: s.id })) + '</span><span class="station-meta">' + esc(meta) + "</span></button>";
  }

  /* Step 1 in two small choices (Hick's law): an area, then only the stations there that sent data.
     Stations not sending data wait behind a toggle, collapsed by default. */
  function renderStations() {
    $("sms-areas").innerHTML = areas().map(function (area) {
      return '<button type="button" class="chip" data-area="' + esc(area) + '" aria-pressed="' + (area === state.area) + '">' +
        esc(area) + "</button>";
    }).join("");
    var list = state.stations.filter(function (s) { return areaOf(s) === state.area; })
      .sort(function (a, b) { return a.id < b.id ? -1 : 1; });
    var on = list.filter(B.selectable), off = list.filter(function (s) { return !B.selectable(s); });
    $("sms-stations").innerHTML = on.length ? '<div class="station-buttons">' + on.map(stationButton).join("") + "</div>"
      : '<p class="muted">' + esc(t("sms.none_in_area")) + "</p>";
    var toggle = $("sms-off-toggle");
    toggle.hidden = !off.length;
    toggle.setAttribute("aria-expanded", String(state.showOff));
    toggle.textContent = state.showOff ? t("sms.hide_off") : t("sms.show_off", { n: off.length });
    $("sms-off").innerHTML = '<div class="station-buttons">' + off.map(stationButton).join("") + "</div>";
    $("sms-off").hidden = !state.showOff || !off.length;
  }

  function option(id, label, enabled, note, extra) {
    return '<div class="message-option"><label class="choice' + (enabled ? "" : " disabled") + '"><input type="radio" name="message" value="' +
      id + '"' + (state.scenario === id && enabled ? " checked" : "") + (enabled ? "" : " disabled") + "><span>" + esc(label) +
      (note ? '<span class="choice-note">' + esc(note) + "</span>" : "") + "</span></label>" + (extra || "") + "</div>";
  }

  function renderMessages() {
    var can = B.available(data(), state.station);
    if (!can[state.scenario]) state.scenario = "latest";
    var langs = '<div class="lang-switch" role="group" aria-label="' + esc(t("sms.lang_label")) + '">' +
      B.LANGS.map(function (lang) {
        return '<button type="button" class="lang-btn" data-lang="' + lang + '" lang="' + lang + '" aria-pressed="' + (lang === state.lang) + '">' +
          esc(t("sms.lang_" + lang)) + "</button>";
      }).join("") + "</div>";
    var crops = can.crop_guide ? '<div class="crop-grid" role="group" aria-label="' + esc(t("sms.msg_crop")) + '">' +
      B.CROPS.map(function (crop) {
        return '<button type="button" class="crop-btn" data-crop="' + esc(crop) + '" lang="' + state.lang + '" aria-pressed="' +
          (state.scenario === "crop_guide" && state.crop === crop) + '">' + esc(t("sms.crops." + state.lang + "." + crop)) + "</button>";
      }).join("") + "</div>" : "";
    $("sms-messages").innerHTML = langs +
      option("latest", t("sms.msg_latest"), can.latest, can.latest ? "" : t("sms.not_sending")) +
      option("crop_guide", t("sms.msg_crop"), can.crop_guide, can.crop_guide ? "" : t("sms.crop_none_station"), crops) +
      option("alert", t("sms.msg_alert"), can.alert,
             can.alert ? (state.lang === "sw" ? t("sms.alert_english") : "") : t("sms.alert_none"));
    preview();
  }

  function request() {
    var r = { scenario: state.scenario, station: state.station, lang: state.lang };
    if (state.scenario === "crop_guide") r.crop = state.crop;
    return r;
  }

  function preview() {
    var built = state.station ? B.buildMessage(data(), request()) : null, box = $("sms-text");
    // Kiswahili text is marked lang="sw" so screen readers read it right; the alert is always English.
    box.lang = built && state.lang === "sw" && state.scenario !== "alert" ? "sw" : "en";
    if (built) {
      box.textContent = built.text;
      box.classList.remove("empty");
      $("sms-count").textContent = built.parts === 1 ? t("sms.counter_one", { n: built.chars })
        : t("sms.counter_parts", { n: built.chars, parts: built.parts });
    } else {
      box.textContent = t("sms.nothing");
      box.classList.add("empty");
      $("sms-count").textContent = "";
    }
    $("sms-send").disabled = !built;
  }

  function result(kind, text, extra) {
    var el = $("sms-result");
    el.className = "sms-result " + kind;
    el.textContent = text;
    if (extra) {
      var line = document.createElement("span");
      line.className = "sms-result-line";
      line.textContent = extra;
      el.appendChild(line);
    }
  }

  function send(event) {
    event.preventDefault();
    var raw = $("sms-phone").value.replace(/[\s-]/g, "");
    if (!PHONE.test(raw)) {
      $("sms-phone-error").textContent = t("sms.phone_error");
      $("sms-phone").setAttribute("aria-invalid", "true");
      $("sms-phone").focus();
      return;
    }
    $("sms-phone-error").textContent = "";
    $("sms-phone").removeAttribute("aria-invalid");
    if (!state.station || !B.buildMessage(data(), request())) { result("fail", t("sms.nothing")); return; }
    var button = $("sms-send");
    button.disabled = true;
    result("", t("sms.sending"));
    var ctrl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, 15000);
    fetch("api/send-sms", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ phone: raw }, request())), signal: ctrl ? ctrl.signal : undefined })
      .then(function (r) {
        if ((r.headers.get("content-type") || "").indexOf("application/json") < 0) throw new Error("no service");
        return r.json();
      })
      .then(function (j) {
        if (j && j.ok) result("ok", t("sms.sent"), t("sms.check_simulator"));
        else result("fail", t("sms.failed", { reason: (j && j.error) || t("sms.no_reason") }));
      })
      .catch(function () { result("", t("sms.demo")); })
      .then(function () { clearTimeout(timer); button.disabled = false; });
  }

  I.ready.then(function () {
    return Promise.all([I.getJSON("data/stations.json"), I.getJSON("data/crop_guide.json").catch(function () { return null; })]);
  }).then(function (res) {
    state.stations = res[0].stations;
    state.guide = res[1];
    var first = state.stations.filter(B.selectable).sort(function (a, b) {
      return (a.area || "") < (b.area || "") ? -1 : (a.area || "") > (b.area || "") ? 1 : a.id < b.id ? -1 : 1;
    })[0];
    state.station = first ? first.id : null;
    state.area = first ? areaOf(first) : areas()[0];
    renderStations();
    renderMessages();
    function pickStation(e) {
      var b = e.target.closest(".station-btn");
      if (!b || b.disabled) return;
      state.station = b.getAttribute("data-station");
      state.scenario = "latest";
      renderStations();
      renderMessages();
      result("", "");
    }
    $("sms-stations").addEventListener("click", pickStation);
    $("sms-off").addEventListener("click", pickStation);
    $("sms-areas").addEventListener("click", function (e) {
      var b = e.target.closest("[data-area]");
      if (!b || b.getAttribute("data-area") === state.area) return;
      state.area = b.getAttribute("data-area");
      state.showOff = false;
      // The preview follows the area: its first station that sent data, or nothing to send.
      var next = state.stations.filter(function (s) { return areaOf(s) === state.area && B.selectable(s); })
        .sort(function (a, c) { return a.id < c.id ? -1 : 1; })[0];
      state.station = next ? next.id : null;
      state.scenario = "latest";
      renderStations();
      renderMessages();
      result("", "");
    });
    $("sms-off-toggle").addEventListener("click", function () {
      state.showOff = !state.showOff;
      renderStations();
    });
    $("sms-messages").addEventListener("change", function (e) {
      if (e.target.name === "message") { state.scenario = e.target.value; renderMessages(); result("", ""); }
    });
    $("sms-messages").addEventListener("click", function (e) {
      var lang = e.target.closest(".lang-btn"), crop = e.target.closest(".crop-btn");
      if (lang) state.lang = lang.getAttribute("data-lang");
      else if (crop) { state.scenario = "crop_guide"; state.crop = crop.getAttribute("data-crop"); }
      else return;
      renderMessages();
      result("", "");
    });
    $("sms-phone").addEventListener("input", function () { $("sms-phone-error").textContent = ""; });
    $("sms-form").addEventListener("submit", send);
  }).catch(function (err) { if (window.console) console.error(err); });
})();
