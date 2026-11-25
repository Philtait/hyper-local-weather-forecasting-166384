/* Contact page: details come from contact.json (filled in by the project owner, never invented).
   The form only opens the visitor's email app with the text filled in; nothing is sent to a server. */
(function () {
  "use strict";
  var I = window.Imarika, t = I.t, esc = I.esc;

  function mailto(address, subject, body) {
    return "mailto:" + encodeURIComponent(address).replace(/%40/g, "@") + "?subject=" + encodeURIComponent(subject) +
      (body ? "&body=" + encodeURIComponent(body) : "");
  }
  function $(id) { return document.getElementById(id); }

  I.ready.then(function () {
    return I.getJSON("contact.json").catch(function () { return {}; });
  }).then(function (info) {
    var email = String(info.email || "").trim();
    var valid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
    if (valid) {
      $("email-button").href = mailto(email, t("contact.subject"));
      $("report-button").href = mailto(email, t("contact.report_subject"), t("contact.report_body"));
      $("email-button").hidden = false;
      $("report-button").hidden = false;
    } else {
      $("no-email").hidden = false;
    }
    var rows = [["phone", info.phone], ["pin", [info.organisation, info.address].filter(Boolean).join(", ")]]
      .filter(function (r) { return r[1] && String(r[1]).trim(); });
    if (valid) rows.unshift(["mail", email]);
    $("contact-details").innerHTML = rows.map(function (r) {
      return "<li>" + I.icon(r[0]) + "<span>" + esc(r[1]) + "</span></li>";
    }).join("");

    $("contact-form").addEventListener("submit", function (event) {
      event.preventDefault();
      var name = $("c-name").value.trim(), message = $("c-message").value.trim();
      if (!valid) { $("c-error").textContent = t("contact.no_email"); return; }
      if (!message) { $("c-error").textContent = t("contact.form_error"); $("c-message").focus(); return; }
      $("c-error").textContent = "";
      window.location.href = mailto(email, t("contact.subject"), message + (name ? "\n\n" + name : ""));
    });
  });
})();
