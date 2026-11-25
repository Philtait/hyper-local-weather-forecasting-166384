/* SMS demo: choose an area and a message, preview the exact text, send it to the sandbox simulator.
   The server looks the text up again from data/; the browser only sends which message was chosen. */
(function () {
  "use strict";
  var I = window.Imarika, t = I.t, esc = I.esc;
  var PHONE = /^\+254[17]\d{8}$/;
  var LIMIT = 160;
  var state = { stations: [], scenarios: [], area: null, choice: null };

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

  function renderChoices() {
    var list = choices();
    if (!list.some(function (c) { return c.id === state.choice; })) state.choice = (list.filter(function (c) { return c.text; })[0] || list[0]).id;
    $("sms-messages").innerHTML = list.map(function (c, i) {
      var heading = i === 1 ? '<p class="choice-group">' + esc(t("sms.examples")) + "</p>" : "";
      return heading + '<label class="choice"><input type="radio" name="message" value="' + esc(c.id) + '"' +
        (c.id === state.choice ? " checked" : "") + "><span>" + esc(c.label) + "</span></label>";
    }).join("");
    preview();
  }

  function current() { return choices().filter(function (c) { return c.id === state.choice; })[0]; }

  function preview() {
    var c = current(), box = $("sms-text");
    if (c && c.text) {
      box.textContent = c.text;
      box.classList.remove("empty");
      $("sms-count").textContent = t("sms.counter", { n: c.text.length });
    } else {
      box.textContent = c && c.dry ? t("sms.dry_none") : t("sms.nothing");
      box.classList.add("empty");
      $("sms-count").textContent = "";
    }
    $("sms-send").disabled = !(c && c.body);
  }

  function result(kind, text) {
    var el = $("sms-result");
    el.className = "sms-result " + kind;
    el.textContent = text;
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
        if (j && j.ok) result("ok", t("sms.sent"));
        else result("fail", t("sms.failed", { reason: (j && j.error) || t("sms.no_reason") }));
      })
      .catch(function () { result("", t("sms.demo")); })
      .then(function () { clearTimeout(timer); button.disabled = false; });
  }

  I.ready.then(function () {
    return Promise.all([I.getJSON("data/stations.json"), I.getJSON("data/scenarios.json")]);
  }).then(function (res) {
    state.stations = res[0].stations;
    state.scenarios = res[1].scenarios;
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
      if (e.target.name === "message") { state.choice = e.target.value; preview(); result("", ""); }
    });
    $("sms-phone").addEventListener("input", function () { $("sms-phone-error").textContent = ""; });
    $("sms-form").addEventListener("submit", send);
  }).catch(function (err) { if (window.console) console.error(err); });
})();
