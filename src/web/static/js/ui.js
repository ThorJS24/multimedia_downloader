// Shared UI services: toasts, dialogs, modal focus management, keyboard/screen-reader enhancement,
// fetch helpers. Loaded before every feature script; feature code should use these instead of
// alert()/confirm()/prompt() or hand-rolled fetch error handling.
"use strict";

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

const UI = (function () {
  // ---------------------------------------------------------------- storage (never throws)
  const store = {
    get(key, fallback = null) {
      try { const v = localStorage.getItem("studio." + key); return v === null ? fallback : JSON.parse(v); } catch (_) { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem("studio." + key, JSON.stringify(value)); } catch (_) { /* private mode */ }
    }
  };

  // ---------------------------------------------------------------- icons
  function icons(root) {
    if (!window.lucide) return;
    try { lucide.createIcons(root ? { root } : undefined); } catch (_) { /* icon failure must never break a handler */ }
  }

  // ---------------------------------------------------------------- formatting
  function fmtDuration(sec) {
    sec = Number(sec);
    if (!isFinite(sec) || sec <= 0) return "0:00";
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
  }
  function fmtBytes(n) {
    n = Number(n) || 0;
    const u = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
  }

  // Placeholder rows shown while a table loads for the first time (shimmer = "something is coming").
  function skeletonRows(cols, rows = 6) {
    const cell = (w) => `<td class="py-3.5 px-4"><div class="skeleton h-3.5" style="width:${w}%"></div></td>`;
    return Array.from({ length: rows }, (_, r) => `<tr aria-hidden="true">${Array.from({ length: cols }, (_, c) => cell(c === 0 ? 8 : 35 + ((r * 17 + c * 23) % 55))).join("")}</tr>`).join("");
  }

  // ---------------------------------------------------------------- clipboard
  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      const ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
      ta.remove();
      return ok;
    }
  }

  // ---------------------------------------------------------------- toasts
  const MAX_TOASTS = 5;
  const toastIcons = { success: "check-circle-2", error: "alert-circle", warning: "alert-triangle", info: "info" };

  function toast(message, type = "info", duration, opts = {}) {
    const container = document.getElementById("toast-container");
    if (!container) return null;
    const text = String(message == null ? "" : message).trim();
    if (!text) return null;
    if (!toastIcons[type]) type = "info";

    // Identical message already visible -> bump a counter instead of stacking duplicates.
    for (const el of container.children) {
      if (el.dataset.msg === text && el.dataset.type === type) {
        const n = (Number(el.dataset.count) || 1) + 1;
        el.dataset.count = n;
        const badge = el.querySelector(".toast-count");
        if (badge) { badge.textContent = `×${n}`; badge.classList.remove("hidden"); }
        el._restart && el._restart();
        return el;
      }
    }
    while (container.children.length >= MAX_TOASTS) container.firstElementChild.remove();

    // Errors stay until dismissed (people need time to read/copy them); the rest fade out.
    if (duration === undefined) duration = type === "error" ? 0 : opts.action ? 10000 : type === "warning" ? 9000 : 4500;

    const el = document.createElement("div");
    el.className = `toast-item toast-${type} flex items-start p-3.5 rounded-xl shadow-2xl backdrop-blur-md mb-2 text-xs font-semibold`;
    el.setAttribute("role", type === "error" ? "alert" : "status");
    el.dataset.msg = text; el.dataset.type = type; el.dataset.count = 1;
    el.innerHTML = `
      <i data-lucide="${toastIcons[type]}" aria-hidden="true" class="w-4 h-4 mr-2.5 mt-0.5 shrink-0 stroke-[2.5]"></i>
      <span class="flex-1 break-words whitespace-pre-line select-text">${escapeHtml(text)}</span>
      <span class="toast-count hidden ml-2 px-1.5 rounded-full bg-black/25 text-2xs font-mono shrink-0"></span>
      ${opts.action ? '<button type="button" class="toast-action ml-3 px-2.5 py-1 rounded-lg bg-white/15 hover:bg-white/25 text-2xs font-semibold shrink-0 cursor-pointer"></button>' : ""}
      <span class="flex items-center shrink-0 ml-2 -mr-1 -mt-0.5">
        ${text.length > 40 || type === "error" ? '<button type="button" aria-label="Copy message" title="Copy" class="toast-copy p-1 opacity-70 hover:opacity-100 cursor-pointer"><i data-lucide="copy" aria-hidden="true" class="w-3.5 h-3.5"></i></button>' : ""}
        <button type="button" aria-label="Dismiss notification" class="toast-close p-1 opacity-70 hover:opacity-100 cursor-pointer"><i data-lucide="x" aria-hidden="true" class="w-3.5 h-3.5"></i></button>
      </span>`;
    container.appendChild(el);
    icons(el);

    let timer = null, hovering = false;
    const dismiss = () => {
      clearTimeout(timer);
      el.classList.add("toast-hiding");
      setTimeout(() => el.remove(), 260);
    };
    const paintBar = () => {
      const old = el.querySelector(".toast-bar");
      if (old) old.remove();
      if (duration > 0) { const bar = document.createElement("span"); bar.className = "toast-bar"; bar.style.animationDuration = `${duration}ms`; bar.setAttribute("aria-hidden", "true"); el.appendChild(bar); }
    };
    el._restart = () => { clearTimeout(timer); paintBar(); if (duration > 0 && !hovering) timer = setTimeout(dismiss, duration); };
    el.addEventListener("mouseenter", () => { hovering = true; clearTimeout(timer); });
    el.addEventListener("mouseleave", () => { hovering = false; el._restart(); });
    el.querySelector(".toast-close").addEventListener("click", dismiss);
    const cp = el.querySelector(".toast-copy");
    if (cp) cp.addEventListener("click", async () => { toast((await copy(text)) ? "Copied to clipboard" : "Copy failed", "info", 1500); });
    const act = el.querySelector(".toast-action");
    if (act) {
      act.textContent = opts.action.label;
      act.addEventListener("click", async () => { dismiss(); try { await opts.action.onClick(); } catch (e) { toast(e.message || String(e), "error"); } });
    }
    el._restart();
    return el;
  }

  // ---------------------------------------------------------------- fetch helpers
  class ApiError extends Error {
    constructor(message, status, data) { super(message); this.name = "ApiError"; this.status = status; this.data = data; }
  }

  let offlineStrikes = 0;
  function noteNetwork(ok) {
    const banner = document.getElementById("conn-banner");
    offlineStrikes = ok ? 0 : offlineStrikes + 1;
    if (banner) banner.classList.toggle("hidden", offlineStrikes < 2);
  }

  // JSON request. Resolves with the parsed body; rejects with ApiError (message is user-presentable).
  // opts: { method, body, quiet (no error toast), prefix (toast prefix), signal }
  async function api(url, opts = {}) {
    const init = { method: opts.method || (opts.body !== undefined ? "POST" : "GET"), signal: opts.signal, headers: {} };
    if (opts.body instanceof FormData) init.body = opts.body;
    else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
    let res, data = null;
    try {
      res = await fetch(url, init);
    } catch (err) {
      if (err && err.name === "AbortError") throw err;
      noteNetwork(false);
      const e = new ApiError("Cannot reach the application server. It may have stopped.", 0, null);
      if (!opts.quiet) toast(e.message, "error");
      throw e;
    }
    noteNetwork(true);
    if (res.status === 304) return { __notModified: true };
    try { data = await res.json(); } catch (_) { data = null; }
    const failed = !res.ok || (data && data.success === false);
    if (failed) {
      const msg = (data && (data.error || data.message)) || `Request failed (${res.status})`;
      const e = new ApiError(msg, res.status, data);
      if (!opts.quiet) toast(opts.prefix ? `${opts.prefix}: ${msg}` : msg, "error");
      throw e;
    }
    return data == null ? {} : data;
  }

  // Run an async action on a button: disables it, swaps the label, restores afterwards.
  async function busy(btn, label, fn) {
    if (!btn) return fn();
    if (btn.dataset.busy === "1") return undefined;
    const html = btn.innerHTML;
    btn.dataset.busy = "1"; btn.disabled = true; btn.setAttribute("aria-busy", "true");
    btn.innerHTML = `<span class="inline-flex items-center"><span class="w-3.5 h-3.5 mr-2 border-2 border-current border-t-transparent rounded-full animate-spin" aria-hidden="true"></span>${escapeHtml(label)}</span>`;
    try {
      return await fn();
    } finally {
      btn.innerHTML = html; btn.disabled = false; btn.removeAttribute("aria-busy"); delete btn.dataset.busy;
      icons(btn);
    }
  }

  // Polling that backs off while the window is hidden. Returns {stop, now}.
  function poll(fn, { visibleMs = 1500, hiddenMs = 10000 } = {}) {
    let timer = null, stopped = false;
    const tick = async () => {
      if (stopped) return;
      try { await fn(); } catch (_) { /* fn reports its own errors */ }
      const next = document.hidden ? hiddenMs : visibleMs;
      if (!stopped) timer = setTimeout(tick, typeof next === "function" ? next() : next);
    };
    timer = setTimeout(tick, 0);
    document.addEventListener("visibilitychange", () => { if (!document.hidden && !stopped) { clearTimeout(timer); tick(); } });
    return { stop() { stopped = true; clearTimeout(timer); }, now() { clearTimeout(timer); return tick(); } };
  }

  // ---------------------------------------------------------------- modal manager
  const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
  const BACKGROUND_IDS = ["app-sidebar", "main-content", "player-bar"];
  let lastTrigger = null;
  let titleSeq = 0;

  function openModals() {
    return Array.from(document.querySelectorAll("[data-modal]")).filter((m) => !m.classList.contains("hidden"));
  }

  function setBackgroundInert(on) {
    BACKGROUND_IDS.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (on) el.setAttribute("inert", ""); else el.removeAttribute("inert");
    });
  }

  function prepareModal(m) {
    if (m.dataset.modalReady) return;
    m.dataset.modalReady = "1";
    m.setAttribute("role", m.getAttribute("role") || "dialog");
    m.setAttribute("aria-modal", "true");
    const heading = m.querySelector("h1,h2,h3");
    if (heading && !m.getAttribute("aria-labelledby")) {
      if (!heading.id) heading.id = `modal-title-${++titleSeq}`;
      m.setAttribute("aria-labelledby", heading.id);
      m.removeAttribute("aria-label");
    }
    // Clicking the dimmed backdrop (not the dialog) dismisses it.
    m.addEventListener("mousedown", (e) => {
      if (e.target === m && !m.hasAttribute("data-modal-static")) closeModal(m);
    });
  }

  function closeModal(m) {
    const btn = m.querySelector("[data-modal-close]") || m.querySelector('[aria-label="Close"]');
    if (btn) btn.click(); else m.classList.add("hidden");
  }

  let wasOpen = false;
  function syncModalState() {
    const open = openModals();
    open.forEach(prepareModal);
    if (open.length && !wasOpen) {
      lastTrigger = document.activeElement;
      setBackgroundInert(true);
    }
    if (open.length) {
      const top = open[open.length - 1];
      if (!top.contains(document.activeElement)) {
        const target = top.querySelector("[autofocus]") || top.querySelector('input:not([type="hidden"]):not([type="checkbox"]),select,textarea') || top.querySelector(FOCUSABLE);
        if (target) target.focus({ preventScroll: true });
        else { top.setAttribute("tabindex", "-1"); top.focus({ preventScroll: true }); }
      }
    } else if (wasOpen) {
      setBackgroundInert(false);
      if (lastTrigger && document.contains(lastTrigger) && typeof lastTrigger.focus === "function") lastTrigger.focus({ preventScroll: true });
      lastTrigger = null;
    }
    wasOpen = open.length > 0;
  }

  let modalObserver = null;

  // Dialogs built at runtime must call this right after creation: if their `hidden` class is removed in the
  // same tick, an observer attached later would miss the change (and the page behind would never go inert).
  function registerModal(m) {
    if (!modalObserver || m.dataset.modalWatched) return;
    m.dataset.modalWatched = "1";
    prepareModal(m);
    modalObserver.observe(m, { attributes: true, attributeFilter: ["class"] });
  }

  function watchModals() {
    modalObserver = new MutationObserver(syncModalState);
    document.querySelectorAll("[data-modal]").forEach(registerModal);
    new MutationObserver((records) => {
      records.forEach((r) => r.addedNodes.forEach((n) => { if (n.nodeType === 1 && n.matches && n.matches("[data-modal]")) registerModal(n); }));
    }).observe(document.body, { childList: true });
  }

  // ---------------------------------------------------------------- confirm / prompt dialogs
  let dialogEl = null;
  function ensureDialog() {
    if (dialogEl) return dialogEl;
    dialogEl = document.createElement("div");
    dialogEl.id = "ui-dialog";
    dialogEl.setAttribute("data-modal", "");
    dialogEl.setAttribute("data-modal-static", "");
    dialogEl.className = "fixed inset-0 bg-black/80 backdrop-blur-sm z-[60] hidden flex items-center justify-center p-4";
    dialogEl.innerHTML = `
      <form class="w-full max-w-md bg-s1 border border-line2 rounded-2xl p-6 space-y-4 shadow-2xl" novalidate>
        <div class="flex items-start space-x-3">
          <div id="ui-dialog-icon" class="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"></div>
          <div class="min-w-0">
            <h3 id="ui-dialog-title" class="text-base font-semibold text-fg"></h3>
            <p id="ui-dialog-message" class="text-xs text-fg3 mt-1 whitespace-pre-line break-words"></p>
          </div>
        </div>
        <div id="ui-dialog-field" class="hidden space-y-1">
          <label id="ui-dialog-label" for="ui-dialog-input" class="text-2xs font-semibold text-fg2"></label>
          <input id="ui-dialog-input" type="text" autocomplete="off" class="w-full bg-s2 border border-line2 rounded-xl px-3 py-2 text-xs text-fg focus:border-spotify-green focus:outline-none" />
        </div>
        <div class="flex justify-end space-x-2 pt-1">
          <button type="button" id="ui-dialog-cancel" class="px-4 py-2 rounded-xl bg-s3 hover:bg-s4 text-xs font-semibold text-fg2">Cancel</button>
          <button type="submit" id="ui-dialog-ok" class="px-5 py-2 rounded-xl text-xs font-semibold"></button>
        </div>
      </form>`;
    document.body.appendChild(dialogEl);
    registerModal(dialogEl);
    return dialogEl;
  }

  function dialog({ title, message = "", confirmText = "OK", cancelText = "Cancel", tone = "primary", input = null }) {
    const el = ensureDialog();
    return new Promise((resolve) => {
      const form = el.querySelector("form");
      const ok = el.querySelector("#ui-dialog-ok");
      const cancel = el.querySelector("#ui-dialog-cancel");
      const field = el.querySelector("#ui-dialog-field");
      const inputEl = el.querySelector("#ui-dialog-input");
      const iconBox = el.querySelector("#ui-dialog-icon");
      const danger = tone === "danger";

      el.querySelector("#ui-dialog-title").textContent = title;
      el.querySelector("#ui-dialog-message").textContent = message;
      ok.textContent = confirmText;
      cancel.textContent = cancelText;
      ok.className = "px-5 py-2 rounded-xl text-xs font-semibold " + (danger ? "bg-red-600 hover:bg-red-500 text-white" : "bg-spotify-green hover:bg-spotify-hover text-black");
      iconBox.className = "w-10 h-10 rounded-xl flex items-center justify-center shrink-0 " + (danger ? "bg-red-500/15 text-red-400" : "bg-spotify-green/15 text-spotify-green");
      iconBox.innerHTML = `<i data-lucide="${danger ? "alert-triangle" : input ? "pencil" : "help-circle"}" aria-hidden="true" class="w-5 h-5"></i>`;
      icons(iconBox);
      field.classList.toggle("hidden", !input);
      if (input) {
        el.querySelector("#ui-dialog-label").textContent = input.label || "";
        inputEl.placeholder = input.placeholder || "";
        inputEl.value = input.value || "";
      }

      const finish = (value) => {
        form.removeEventListener("submit", handleSubmit);
        cancel.removeEventListener("click", handleCancel);
        el.removeEventListener("keydown", handleKey, true);
        el.classList.add("hidden");
        resolve(value);
      };
      const handleSubmit = (e) => { e.preventDefault(); finish(input ? inputEl.value.trim() : true); };
      const handleCancel = () => finish(input ? null : false);
      const handleKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); handleCancel(); } };
      form.addEventListener("submit", handleSubmit);
      cancel.addEventListener("click", handleCancel);
      el.addEventListener("keydown", handleKey, true);
      // Make the generic modal manager's "Close" fallback resolve this dialog too.
      cancel.setAttribute("data-modal-close", "");

      el.classList.remove("hidden");
      // Destructive prompts focus Cancel so a stray Enter can't delete anything.
      (input ? inputEl : danger ? cancel : ok).focus();
    });
  }

  const confirmDialog = (opts) => dialog(typeof opts === "string" ? { title: "Are you sure?", message: opts } : opts);
  const promptDialog = (opts) => dialog({ ...opts, input: { label: opts.label, placeholder: opts.placeholder, value: opts.value } });

  // ---------------------------------------------------------------- accessibility enhancer
  const NATIVE_INTERACTIVE = new Set(["BUTTON", "A", "INPUT", "SELECT", "TEXTAREA", "SUMMARY", "LABEL", "OPTION"]);

  function humanize(name) { return (name || "").replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()); }

  function enhance(root) {
    if (!root || root.nodeType !== 1) return;
    const scan = (el) => {
      if (el.hasAttribute && el.hasAttribute("data-on-click")) {
        if (el.tagName === "TH") {
          if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "0");
        } else if (!NATIVE_INTERACTIVE.has(el.tagName)) {
          if (!el.hasAttribute("role")) el.setAttribute("role", "button");
          if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "0");
        }
      }
      if ((el.tagName === "BUTTON" || el.getAttribute("role") === "button") && !el.hasAttribute("aria-label") && !el.textContent.trim()) {
        const label = el.getAttribute("title") || humanize((el.querySelector("[data-lucide]") || {}).getAttribute?.("data-lucide"));
        if (label) el.setAttribute("aria-label", label);
      }
      if (el.tagName === "IMG") {
        if (!el.hasAttribute("alt")) el.setAttribute("alt", "");
        if (!el.hasAttribute("loading")) el.setAttribute("loading", "lazy");
        if (!el.hasAttribute("decoding")) el.setAttribute("decoding", "async");
      }
    };
    scan(root);
    root.querySelectorAll('[data-on-click],button,[role="button"],img').forEach(scan);
  }

  function watchAccessibility() {
    enhance(document.body);
    const mo = new MutationObserver((records) => {
      for (const r of records) r.addedNodes.forEach((n) => enhance(n));
    });
    mo.observe(document.body, { childList: true, subtree: true });

    // Enter/Space activate role="button" elements exactly like native buttons.
    document.addEventListener("keydown", (e) => {
      if (e.defaultPrevented || (e.key !== "Enter" && e.key !== " ")) return;
      const t = e.target;
      if (!t || t.nodeType !== 1 || NATIVE_INTERACTIVE.has(t.tagName)) return;
      if (t.getAttribute("role") === "button" || (t.tagName === "TH" && t.hasAttribute("data-on-click"))) {
        e.preventDefault();
        t.click();
      }
    });
  }

  // ---------------------------------------------------------------- global keyboard shortcuts
  const INTERACTIVE_SELECTOR = 'button,a[href],input,select,textarea,summary,[role="button"],[role="slider"],[role="tab"],[contenteditable="true"]';

  function isTyping(t) {
    return !!(t && t.closest && t.closest('input:not([type="checkbox"]):not([type="range"]):not([type="button"]),textarea,select,[contenteditable="true"]'));
  }

  function watchKeyboard() {
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        const open = openModals();
        if (open.length) { e.preventDefault(); closeModal(open[open.length - 1]); return; }
        ["drawer-now-playing", "drawer-playback-queue"].forEach((id) => {
          const d = document.getElementById(id);
          if (d && d.classList.contains("drawer-open") && typeof window.closeDrawer === "function") window.closeDrawer(id);
        });
        return;
      }
      if (openModals().length) return;
      const t = e.target;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (typeof Palette !== "undefined") Palette.open();
      } else if (e.key === "/" && !isTyping(t) && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        const s = document.getElementById("global-search-input");
        if (s) { s.focus(); s.select(); }
      } else if (e.altKey && /^[1-5]$/.test(e.key) && typeof window.openHub === "function") {
        e.preventDefault();
        window.openHub(["home", "downloads", "library", "automation", "settings"][Number(e.key) - 1]);
      } else if (e.key === "?" && !isTyping(t)) {
        e.preventDefault();
        showShortcuts();
      } else if (e.code === "Space" && !e.repeat && !isTyping(t) && !(t.closest && t.closest(INTERACTIVE_SELECTOR))) {
        // Space only controls playback when nothing focusable owns it (never hijacks buttons/links).
        e.preventDefault();
        if (typeof window.toggleAudioPlayback === "function") window.toggleAudioPlayback();
      }
    });
  }

  function showShortcuts() {
    confirmDialog({
      title: "Keyboard shortcuts",
      message: "Ctrl+K   Command palette (go anywhere, run anything, find a song)\n/   Focus the search box\nAlt+1…5   Home, Downloads, Library, Automation, Settings\nSpace   Play / pause\nEsc   Close dialog or panel\n?   This help",
      confirmText: "Got it", cancelText: "Close"
    });
  }

  // ---------------------------------------------------------------- init
  function init() {
    watchModals();
    watchAccessibility();
    watchKeyboard();
  }

  return { store, icons, registerModal, skeletonRows, noteNetwork, fmtDuration, fmtBytes, copy, toast, api, busy, poll, ApiError, confirm: confirmDialog, prompt: promptDialog, enhance, init, showShortcuts };
})();

// Back-compat globals used across feature scripts.
function showToast(message, type = "info", duration, opts) { return UI.toast(message, type, duration, opts); }
// Anything still calling alert() gets a toast instead of a blocking native dialog (no keyword guessing).
window.alert = function (msg) { UI.toast(msg, "info"); };

document.addEventListener("DOMContentLoaded", () => UI.init());
