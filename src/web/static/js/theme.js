// Runs in <head> (blocking) so the right theme is applied before first paint. Preference: dark | light | system.
"use strict";
(function () {
  var KEY = "studio.theme";
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;

  function read() {
    try { return localStorage.getItem(KEY) || "dark"; } catch (_) { return "dark"; }
  }

  function apply(pref) {
    var resolved = pref === "system" ? (media && media.matches ? "light" : "dark") : pref;
    if (resolved !== "light") resolved = "dark";
    document.documentElement.setAttribute("data-theme", resolved);
    document.documentElement.classList.toggle("dark", resolved === "dark");
  }

  window.StudioTheme = {
    get: read,
    set: function (pref) {
      if (["dark", "light", "system"].indexOf(pref) < 0) pref = "dark";
      try { localStorage.setItem(KEY, pref); } catch (_) {}
      apply(pref);
    },
    resolved: function () { return document.documentElement.getAttribute("data-theme") || "dark"; }
  };

  apply(read());
  if (media && media.addEventListener) {
    media.addEventListener("change", function () { if (read() === "system") apply("system"); });
  }
})();
