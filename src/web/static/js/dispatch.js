// Delegated event dispatcher for the data-on-<event> markup (see handlers.js / actions.js).
//
// Inline on*= attributes are forbidden by the Content-Security-Policy, so markup names a handler:
//   <button data-on-click="switchTab_1a2b3c">  or  data-on-click="key" data-args-click='["x", 1]'
// Handlers run with `this` = the element that carries the attribute. Bubbling events walk up from
// the target exactly like inline handlers did, and event.stopPropagation() stops the walk (and, since
// the listener sits on <body>, still keeps the event away from document-level listeners).
"use strict";
(function () {
  var registry = (window.UI_HANDLERS = window.UI_HANDLERS || {});
  var hasOwn = Object.prototype.hasOwnProperty;

  function argsFor(el, type) {
    var raw = el.getAttribute("data-args-" + type);
    if (!raw) return [];
    try {
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (err) {
      return [];
    }
  }

  function run(type, el, event) {
    var key = el.getAttribute("data-on-" + type);
    if (!key || !hasOwn.call(registry, key) || typeof registry[key] !== "function") return;
    try {
      registry[key].call(el, event, argsFor(el, type));
    } catch (err) {
      // A broken handler must never take the page down or fail silently.
      console.error("Handler '" + key + "' failed:", err);
      if (window.UI && UI.toast) UI.toast("Something went wrong (" + (err && err.message ? err.message : err) + "). Details are in the diagnostics log.", "error");
    }
  }

  function bubbling(type) {
    document.body.addEventListener(type, function (event) {
      for (var el = event.target; el && el !== document.body.parentElement; el = el.parentElement) {
        if (el.hasAttribute && el.hasAttribute("data-on-" + type)) {
          run(type, el, event);
          if (event.cancelBubble) break;
        }
      }
    });
  }

  ["click", "change", "input", "submit", "keydown"].forEach(bubbling);

  // 'error' (failed <img>/<audio> loads) does not bubble, so listen during the capture phase.
  document.addEventListener("error", function (event) {
    var el = event.target;
    if (el && el.hasAttribute && el.hasAttribute("data-on-error")) run("error", el, event);
  }, true);
})();
