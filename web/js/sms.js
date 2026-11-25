/* SMS demo: choose an area and a message, preview the exact text, send it to the sandbox simulator.
   The server looks the text up again from data/; the browser only sends which message was chosen.
   Crop guide messages are built by js/sms-message.js, the same builder the send function uses. */
(function () {
  "use strict";
  var I = window.Imarika, t = I.t, esc = I.esc, B = window.ImarikaSmsMessage;
  var PHONE = /^\+254[17]\d{8}$/;
  var LIMIT = 160;
  var CROP = "crop:";
  var state = { stations: [], scenarios: [], guide: null, area: null, choice: null, lang: "en" };

  function $(id) { return document.getElementById(id); }
  function stationFor(area) {
    return state.stations.filter(function (s) { return s.area === area; }).sort(I.bySeverity)[0];
  }

  /* Each choice: {id, label, text, body} where body is what the server needs to find the text itself. */
  function choices() {
    var list = [], s = stationFor(state.area);
    if (s) {
      var advice = (s.insights || [])[0];
      list.push(advice
        ? { id: "current", label: t("sms.current", { station: s.name }), text: advice.sms_text, body: { station: s.id, advisory: advice.key } }
        : { id: "current", label: t("sms.no_current", { station: s.name }), text: null, body: null });
    }
    state.scenarios.forEach(function (sc) {
      list.push({ id: "scenario-" + sc.key, label: t("sms.scenario_" + sc.key), text: sc.sms_text,
                  body: sc.sms_text ? { scenario: sc.key } : null, dry: !sc.sms_text });
    });
    return list;
  }

  function renderAreas() {
    var areas = {};
    state.stations.forEach(function (s) { if (s.area) areas[s.area] = true; });
    $("sms-areas").innerHTML = Object.keys(areas).sort().map(function (a) {
      return '<button type="button" class="area-btn" aria-pressed="' + (a === state.area) + '" data-area="' + esc(a) + '">' + esc(a) + "</button>";
    }).join("");
  }

  /* The crop guide uses the same station as "this week's advice"; only a station with a forecast has one. */
  function cropStation() {
    var s = stationFor(state.area);
    if (!s || !state.guide || B.stations(state.guide).indexOf(s.id) < 0 || !(s.forecast || []).length) return { station: s, ok: false };
    return { station: s, ok: true };
  }

  function cropChoice(crop) {
    var cs = cropStation();
    if (!cs.ok) return null;
    var built = B.buildCropMessage(state.guide, cs.station.id, crop, state.lang);
    return { id: CROP + crop, label: t("sms.crops." + state.lang + "." + crop), text: built && built.text,
             parts: built && built.parts,
             body: built ? { scenario: "crop_guide", station: cs.station.id, crop: crop, lang: state.lang } : null };
  }

  function renderChoices() {
    var list = choices();
    var cropActive = state.choice && state.choice.indexOf(CROP) === 0 && cropStation().ok;
    if (!cropActive && !list.some(function (c) { return c.id === state.choice; })) {
      state.choice = (list.filter(function (c) { return c.text; })[0] || list[0]).id;
    }
    $("sms-messages").innerHTML = list.map(function (c, i) {
      var heading = i === 1 ? '<p class="choice-group">' + esc(t("sms.examples")) + "</p>" : "";
      return heading + '<label class="choice"><input type="radio" name="message" value="' + esc(c.id) + '"' +
        (c.id === state.choice ? " checked" : "") + "><span>" + esc(c.label) + "</span></label>";
    }).join("");
    renderCrops();
    preview();
  }

  function renderCrops() {
    var cs = cropStation();
    var html = '<p class="choice-group" id="crop-group-title">' + esc(t("sms.crop_group")) + "</p>";
    if (!cs.ok) {
      html += '<p class="crop-note">' + esc(cs.station && state.guide
        ? t("sms.crop_none", { station: cs.station.name }) : t("sms.crop_unavailable")) + "</p>";
      $("sms-crops").innerHTML = html;
      return;
    }
    html += '<div class="lang-switch" role="group" aria-label="' + esc(t("sms.lang_label")) + '">' +
      B.LANGS.map(function (lang) {
        return '<button type="button" class="lang-btn" data-lang="' + lang + '" aria-pressed="' + (lang === state.lang) + '">' +
          esc(t("sms.lang_" + lang)) + "</button>";
      }).join("") + "</div>";
    html += '<div class="crop-grid" role="group" aria-labelledby="crop-group-title">' + B.CROPS.map(function (crop) {
      return '<button type="button" class="crop-btn" data-crop="' + esc(crop) + '" aria-pressed="' +
        (state.choice === CROP + crop) + '">' + esc(t("sms.crops." + state.lang + "." + crop)) + "</button>";
    }).join("") + "</div>";
    $("sms-crops").innerHTML = html;
  }

  function current() {
    if (state.choice && state.choice.indexOf(CROP) === 0) return cropChoice(state.choice.slice(CROP.length));
    return choices().filter(function (c) { return c.id === state.choice; })[0];
  }

  function preview() {
    var c = current(), box = $("sms-text");
    if (c && c.text) {
      box.textContent = c.text;
      box.classList.remove("empty");
      // Short messages keep "x of 160 characters"; longer ones show the real count and number of messages.
      $("sms-count").textContent = c.text.length <= LIMIT ? t("sms.counter", { n: c.text.length })
        : t("sms.counter_parts", { n: c.text.length, parts: B.smsParts(c.text.length) });
    } else {
      box.textContent = c && c.dry ? t("sms.dry_none") : t("sms.nothing");
      box.classList.add("empty");
      $("sms-count").textContent = "";
    }
    $("sms-send").disabled = !(c && c.body);
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
    var c = current();
    if (!c || !c.body) { result("fail", t("sms.nothing")); return; }
    var button = $("sms-send");
    button.disabled = true;
    result("", t("sms.sending"));
    var ctrl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, 15000);
    fetch("api/send-sms", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ phone: raw }, c.body)), signal: ctrl ? ctrl.signal : undefined })
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
    return Promise.all([I.getJSON("data/stations.json"), I.getJSON("data/scenarios.json"),
                        I.getJSON("data/crop_guide.json").catch(function () { return null; })]);
  }).then(function (res) {
    state.stations = res[0].stations;
    state.scenarios = res[1].scenarios;
    state.guide = res[2];
    var first = state.stations.filter(function (s) { return s.active && s.insights.length; }).sort(I.bySeverity)[0] || state.stations[0];
    state.area = first ? first.area : null;
    renderAreas();
    renderChoices();
    $("sms-areas").addEventListener("click", function (e) {
      var b = e.target.closest(".area-btn");
      if (!b) return;
      state.area = b.getAttribute("data-area");
      state.choice = "current";
      renderAreas();
      renderChoices();
      result("", "");
    });
    $("sms-messages").addEventListener("change", function (e) {
      // A test scenario replaces any selected crop: only one message is active at a time.
      if (e.target.name === "message") { state.choice = e.target.value; renderCrops(); preview(); result("", ""); }
    });
    $("sms-crops").addEventListener("click", function (e) {
      var lang = e.target.closest(".lang-btn"), crop = e.target.closest(".crop-btn");
      if (lang) state.lang = lang.getAttribute("data-lang");
      else if (crop) state.choice = CROP + crop.getAttribute("data-crop");
      else return;
      renderChoices();
      result("", "");
    });
    $("sms-phone").addEventListener("input", function () { $("sms-phone-error").textContent = ""; });
    $("sms-form").addEventListener("submit", send);
  }).catch(function (err) { if (window.console) console.error(err); });
})();
