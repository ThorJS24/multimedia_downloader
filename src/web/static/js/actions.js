// Behaviour behind the redesigned shell's controls. handlers.js maps markup keys to these functions
// (the CSP forbids inline handlers, so every data-on-* key must be registered there).
"use strict";

// ---------- appearance / navigation
function setThemeChoice(value) {
  StudioTheme.set(value);
  refreshThemeUI();
}

function globalSearchKey(event, input) {
  if (event.key === "Enter") { event.preventDefault(); submitGlobalSearch(); }
  else if (event.key === "Escape" && input.value) { event.stopPropagation(); clearGlobalSearch(); }
}

// ---------- home / add music
function homeQuickAdd(event) {
  event.preventDefault();
  const box = document.getElementById("home-quick-input");
  const text = box.value.trim();
  if (!text) { UI.toast("Paste at least one Spotify link first.", "warning"); box.focus(); return; }
  document.getElementById("multiline-input").value = text;
  updateLinkHint();
  box.value = "";
  switchTab("fetch");
  requestStagingPreview();
}

// ---------- account
async function signOutAccount() {
  const ok = await UI.confirm({
    title: "Sign out of Spotify?",
    message: "The cached session is deleted from this computer. You'll need to sign in again to download Spotify tracks.",
    confirmText: "Sign out", tone: "danger"
  });
  if (!ok) return;
  try {
    await UI.api("/api/logout", { method: "POST", prefix: "Sign out failed" });
    UI.toast("Signed out.", "success");
    closeLoginModal();
    fetchStatus();
  } catch (_) { /* reported */ }
}

function toggleLoginPassword(btn) {
  const input = document.getElementById("login-password");
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  btn.textContent = show ? "Hide" : "Show";
  btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
}

// ---------- settings
function jumpToSetting(event, link) {
  event.preventDefault();
  const el = document.getElementById(link.getAttribute("href").slice(1));
  if (!el) return;
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  const h = el.querySelector("h3");
  if (h) { h.setAttribute("tabindex", "-1"); h.focus({ preventScroll: true }); }
}

async function createBackupNow() {
  try {
    const res = await UI.api("/api/backups/create", { method: "POST", prefix: "Backup failed" });
    UI.toast("Database backup created" + (res.path ? ":\n" + res.path : "."), "success");
  } catch (_) { /* reported */ }
}

function pickSettingsImport() {
  document.getElementById("settings-import-input").click();
}

async function importSettingsFile(event) {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  let payload;
  try { payload = JSON.parse(await file.text()); } catch (_) { UI.toast("That file isn't valid JSON.", "error"); return; }
  const ok = await UI.confirm({ title: "Import settings?", message: `Settings from ${file.name} replace your current values. Credentials are never imported.`, confirmText: "Import" });
  if (!ok) return;
  try {
    await UI.api("/api/settings/import", { body: payload, prefix: "Import failed" });
    UI.toast("Settings imported.", "success");
    loadSettings(true);
  } catch (_) { /* reported */ }
}

// ---------- match lab form
function submitMatchForm(event) {
  event.preventDefault();
  testMatchCandidate();
}
