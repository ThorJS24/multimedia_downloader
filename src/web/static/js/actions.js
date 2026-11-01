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

// Preferences search: a section stays when its title or any field label matches; inside a section only the matching
// fields stay (the whole section when its title matches). Uses [data-search-hidden] so it never fights other show/hide logic.
function filterSettings(event) {
  const q = (event.target.value || "").trim().toLowerCase();
  const tokens = q.split(/\s+/).filter(Boolean);
  const hit = (el) => { const text = (el.textContent || "").toLowerCase(); return tokens.every((tk) => text.includes(tk)); };
  const sections = document.querySelectorAll("#settings-form > section");
  let shown = 0;
  sections.forEach((sec) => {
    const rows = sec.querySelectorAll(".space-y-1\\.5, details, label.flex");
    rows.forEach((r) => r.removeAttribute("data-search-hidden"));
    if (!tokens.length) { sec.removeAttribute("data-search-hidden"); shown++; return; }
    const title = sec.querySelector("h3");
    const titleHit = !!title && hit(title);
    const anyRow = Array.from(rows).some(hit);
    const visible = titleHit || anyRow;
    sec.toggleAttribute("data-search-hidden", !visible);
    if (visible) {
      shown++;
      if (!titleHit) rows.forEach((r) => { if (!hit(r) && !r.querySelector(".space-y-1\\.5, details, label.flex")) r.setAttribute("data-search-hidden", ""); });
      sec.querySelectorAll("details").forEach((d) => { if (!d.hasAttribute("data-search-hidden")) d.open = true; });
    }
    const link = document.querySelector(`#settings-index a[href="#${sec.id}"]`);
    if (link) link.toggleAttribute("data-search-hidden", !visible);
  });
  const empty = document.getElementById("settings-search-empty");
  if (empty) {
    empty.classList.toggle("hidden", shown > 0);
    empty.textContent = shown > 0 ? "" : `Nothing in Preferences matches “${event.target.value.trim()}”.`;
  }
}

async function createBackupNow() {
  try {
    const res = await UI.api("/api/backups/create", { method: "POST", prefix: "Backup failed" });
    UI.toast("Database backup created" + (res.path ? ":\n" + res.path : "."), "success");
    loadBackups();
  } catch (_) { /* reported */ }
}

// ---------- database backups: list + restore (a restore is staged and applied the next time the app starts)
let backupFolder = "";

function backupKind(name) {
  if (name.includes("_prerestore_")) return "Saved before a restore";
  if (name.includes("_auto_")) return "Automatic";
  return "Manual";
}

function renderRestoreBanner(res) {
  const el = document.getElementById("restore-banner");
  if (!el) return;
  const pending = res.pending, last = res.last_restore;
  const tone = (cls) => { el.className = `p-3 rounded-xl border text-xs ${cls}`; };
  if (pending) {
    tone("bg-amber-500/10 border-amber-500/40 text-amber-200");
    el.innerHTML = `<div class="font-semibold">Restore ready: ${escapeHtml(pending.source || "a backup")}</div>
      <div class="mt-1 text-fg2">Close Spotify Downloader Studio and open it again to finish. Until then nothing has changed.</div>
      <button type="button" data-on-click="cancelRestore" class="mt-2 px-3 py-1.5 rounded-lg bg-s3 hover:bg-s4 border border-line2 text-2xs font-semibold text-fg">Cancel restore</button>`;
  } else if (last) {
    const when = new Date(last.at * 1000).toLocaleString();
    tone(last.ok ? "bg-emerald-500/10 border-emerald-500/40 text-emerald-200" : "bg-red-500/10 border-red-500/40 text-red-200");
    el.innerHTML = last.ok
      ? `<div class="font-semibold">Restored from ${escapeHtml(last.source || "a backup")} (${escapeHtml(when)})</div>
         <div class="mt-1 text-fg2">${last.saved_previous_as ? `Your previous data was saved as ${escapeHtml(last.saved_previous_as)} in case you want to go back.` : ""}</div>
         <button type="button" data-on-click="dismissRestoreResult" class="mt-2 px-3 py-1.5 rounded-lg bg-s3 hover:bg-s4 border border-line2 text-2xs font-semibold text-fg">Dismiss</button>`
      : `<div class="font-semibold">The last restore was not applied (${escapeHtml(when)})</div>
         <div class="mt-1 text-fg2">${escapeHtml(last.error || "Unknown error")}. Your data was left as it was.</div>
         <button type="button" data-on-click="dismissRestoreResult" class="mt-2 px-3 py-1.5 rounded-lg bg-s3 hover:bg-s4 border border-line2 text-2xs font-semibold text-fg">Dismiss</button>`;
  } else {
    el.className = "hidden";
    el.innerHTML = "";
  }
}

async function loadBackups() {
  const list = document.getElementById("backup-list");
  if (!list) return;
  try {
    const res = await UI.api("/api/backups/list", { quiet: true });
    backupFolder = res.folder || "";
    renderRestoreBanner(res);
    const items = res.backups || [];
    list.innerHTML = items.length ? items.map((b) => `
      <li class="flex items-center justify-between gap-3 px-3 py-2 rounded-xl bg-s2 border border-line">
        <div class="min-w-0">
          <div class="text-fg truncate">${escapeHtml(b.created_str)} <span class="text-fg3">· ${escapeHtml(backupKind(b.filename))}</span></div>
          <div class="text-2xs text-fg4 font-mono truncate" title="${escapeHtml(b.filename)}">${escapeHtml(b.filename)} · ${escapeHtml(UI.fmtBytes(b.size_kb * 1024))}</div>
        </div>
        <button type="button" data-on-click="restoreBackup" data-args-click="${escapeHtml(JSON.stringify([b.filename]))}" class="shrink-0 px-3 py-1.5 rounded-lg bg-s3 hover:bg-s4 border border-line2 text-2xs font-semibold text-fg">Restore…</button>
      </li>`).join("") : `<li class="text-fg3 py-2">No backups yet. Use “Back up now”, or leave automatic backups on.</li>`;
  } catch (_) {
    list.innerHTML = `<li class="text-red-400 py-2">Couldn't load the backup list. <button type="button" data-on-click="loadBackups" class="underline">Try again</button></li>`;
  }
}

async function restoreBackup(filename) {
  const ok = await UI.confirm({
    title: "Restore this backup?",
    message: `Your history, queue and library index will be replaced by ${filename} the next time you open the app. Your current data is saved first, so you can go back. Downloaded music files are not touched.`,
    confirmText: "Prepare restore", tone: "danger"
  });
  if (!ok) return;
  try {
    await UI.api("/api/backups/restore", { body: { filename }, prefix: "Could not prepare the restore" });
    UI.toast("Restore is ready. Close the app and open it again to finish.", "success");
    loadBackups();
  } catch (_) { /* reported */ }
}

async function cancelRestore() {
  try {
    await UI.api("/api/backups/restore/cancel", { method: "POST", prefix: "Could not cancel" });
    UI.toast("Restore cancelled. Nothing was changed.", "success");
    loadBackups();
  } catch (_) { /* reported */ }
}

async function dismissRestoreResult() {
  try {
    await UI.api("/api/backups/restore/dismiss", { method: "POST", quiet: true });
  } catch (_) { /* purely cosmetic */ }
  loadBackups();
}

function openBackupsFolder() {
  if (!backupFolder) { UI.toast("There are no backups yet.", "info"); return; }
  UI.api("/api/system/reveal", { body: { path: backupFolder }, prefix: "Could not open the folder" }).catch(() => {});
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
