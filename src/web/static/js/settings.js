// ================= SETTINGS =================
// Every input carries data-setting="section.key". Loading, dirty tracking and saving are all generic,
// so adding a setting is one line of markup (the server schema in settings_service.py is the source of truth).
const SECRET_MASK = "********";
let settingsBaseline = {};
let settingsLoaded = false;
let releaseFeedUrl = "";

function settingInputs() {
  return Array.from(document.querySelectorAll("#settings-form [data-setting]"));
}

function readSettingInput(el) {
  if (el.type === "checkbox") return el.checked;
  if (el.type === "number" || el.type === "range") return el.value === "" ? null : Number(el.value);
  return el.value.trim();
}

function writeSettingInput(el, value) {
  if (el.type === "checkbox") el.checked = !!value;
  else el.value = value === undefined || value === null ? "" : value;
}

function collectSettings() {
  const out = {};
  settingInputs().forEach((el) => { out[el.dataset.setting] = readSettingInput(el); });
  return out;
}

function dirtySettingKeys() {
  const now = collectSettings();
  return Object.keys(now).filter((k) => now[k] !== settingsBaseline[k]);
}

function updateDirtyState() {
  const dirty = dirtySettingKeys();
  const bar = document.getElementById("settings-savebar");
  if (bar) bar.classList.toggle("is-clean", dirty.length === 0);
  const txt = document.getElementById("settings-dirty-text");
  if (txt) txt.textContent = dirty.length ? `You have ${dirty.length} unsaved change${dirty.length === 1 ? "" : "s"}` : "";
  return dirty;
}

function workerLabel(n) {
  return `${n} Worker${n > 1 ? "s" : " (Safe)"}`;
}

function refreshSettingsDerived() {
  const fmt = document.getElementById("cfg-format");
  const row = document.getElementById("row-mp3-bitrate");
  if (fmt && row) row.classList.toggle("hidden", fmt.value !== "mp3");

  const slider = document.getElementById("cfg-workers-slider");
  const label = document.getElementById("cfg-workers-label");
  if (slider && label) label.innerText = workerLabel(Number(slider.value) || 1);

  const tmpl = document.getElementById("cfg-template");
  const prev = document.getElementById("cfg-template-preview");
  if (tmpl && prev) {
    const sample = { artist: "Daft Punk", title: "One More Time", album: "Discovery", track_number: "01", year: "2001", playlist: "Favourites", ext: (fmt && fmt.value) || "mp3" };
    prev.textContent = tmpl.value.replace(/\{(\w+)\}/g, (m, k) => (k in sample ? sample[k] : m));
  }

  const days = document.getElementById("cfg-schedule-days");
  if (days) {
    const enabled = document.getElementById("cfg-schedule-enabled");
    const err = document.getElementById("cfg-days-error");
    if (err) err.classList.toggle("hidden", !(days.value.trim() === "" && enabled && enabled.checked));
    const fields = document.getElementById("schedule-fields");
    if (fields && enabled) fields.classList.toggle("opacity-50", !enabled.checked);
  }

  const min = document.getElementById("cfg-min-delay");
  const max = document.getElementById("cfg-max-delay");
  const err = document.getElementById("cfg-delay-error");
  if (min && max && err) {
    const bad = min.value !== "" && max.value !== "" && Number(min.value) > Number(max.value);
    err.classList.toggle("hidden", !bad);
    min.setAttribute("aria-invalid", String(bad));
    max.setAttribute("aria-invalid", String(bad));
  }
}

function updateDashboardBadges(s) {
  const audio = s.audio || {}, perf = s.performance || {};
  const w = perf.max_workers || 1;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.innerText = v; };
  set("dash-workers-pill", workerLabel(w));
  const fmt = (audio.output_format || "mp3");
  set("dash-format-badge", fmt === "mp3" ? `MP3 ${audio.mp3_bitrate || "320k"}` : fmt.toUpperCase());
  set("dash-jitter-badge", `${perf.rate_limit_min_delay ?? 3}s – ${perf.rate_limit_max_delay ?? 7}s`);
}

async function loadSettings(force = false) {
  try {
    const data = await UI.api("/api/settings", { quiet: true });
    const s = data.settings || {};
    updateDashboardBadges(s);

    const ver = document.getElementById("update-current-version");
    if (ver) ver.textContent = data.version || "--";
    releaseFeedUrl = data.release_feed_url || "";

    if (settingsLoaded && !force && dirtySettingKeys().length) return; // never clobber unsaved edits

    settingInputs().forEach((el) => {
      const [section, key] = el.dataset.setting.split(".");
      const v = (s[section] || {})[key];
      writeSettingInput(el, v);
    });
    syncScheduleDayBoxes();
    settingsBaseline = collectSettings();
    settingsLoaded = true;

    const sp = s.spotify || {};
    const badge = document.getElementById("spotify-api-status-badge");
    if (badge) {
      const set = !!(sp.client_id && sp.client_secret);
      badge.className = `px-2.5 py-1 rounded-full text-2xs font-mono shrink-0 ${set ? "bg-emerald-500/20 text-emerald-400" : "bg-s3 text-fg3"}`;
      badge.innerText = set ? "Keys saved" : "Not set";
    }
    refreshSettingsDerived();
    updateDirtyState();
  } catch (err) {
    console.error("Load settings error:", err);
  }
}

// The schedule's day boxes feed one hidden value ("mon,tue,...") so the generic save path handles it.
// The boxes are the source of truth while you click; they are only copied *from* the value when settings load.
function syncScheduleDayBoxes() {
  const hidden = document.getElementById("cfg-schedule-days");
  if (!hidden) return;
  const on = new Set(hidden.value.split(",").map((d) => d.trim()).filter(Boolean));
  document.querySelectorAll(".schedule-day").forEach((cb) => { cb.checked = on.has(cb.value); });
}

function scheduleDaysChanged() {
  const hidden = document.getElementById("cfg-schedule-days");
  if (!hidden) return;
  hidden.value = Array.from(document.querySelectorAll(".schedule-day:checked")).map((cb) => cb.value).join(",");
  refreshSettingsDerived();
  updateDirtyState();
}

// Fills the update-feed box with the address the release pipeline publishes to (still opt-in: nothing is contacted until you save and check).
function useReleaseFeed() {
  const input = document.getElementById("cfg-update-feed-url");
  if (!input || !releaseFeedUrl) return;
  input.value = releaseFeedUrl;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  UI.toast("Address filled in. Save your changes, then press Check now.", "info", 3500);
}

function discardSettings() {
  loadSettings(true);
  UI.toast("Changes discarded.", "info", 1500);
}

function toggleSecretVisibility() {
  const input = document.getElementById("cfg-spotify-client-secret");
  const text = document.getElementById("secret-toggle-text");
  const icon = document.getElementById("secret-toggle-icon");
  if (!input) return;
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  if (text) text.innerText = show ? "Hide" : "Show";
  if (icon) icon.setAttribute("data-lucide", show ? "eye-off" : "eye");
  UI.icons();
}

async function testSpotifyCredentials() {
  const cid = document.getElementById("cfg-spotify-client-id").value.trim();
  const csec = document.getElementById("cfg-spotify-client-secret").value.trim();
  const badge = document.getElementById("spotify-api-status-badge");
  if (!cid || (!csec)) {
    UI.toast("Enter both a Client ID and a Client Secret first.", "warning");
    return;
  }
  await UI.busy(document.getElementById("btn-test-spotify"), "Testing…", async () => {
    try {
      // A masked (unchanged) secret means "test the saved one": send nothing and let the server use it.
      const body = { client_id: cid };
      if (csec !== SECRET_MASK) body.client_secret = csec;
      const data = await UI.api("/api/spotify/test-credentials", { body, quiet: true });
      UI.toast(data.message || "Spotify API keys work.", "success");
      if (badge) { badge.className = "px-2.5 py-1 rounded-full text-2xs font-mono shrink-0 bg-emerald-500/20 text-emerald-400"; badge.innerText = "Verified"; }
    } catch (err) {
      UI.toast(err.message || "Spotify rejected those keys.", "error");
      if (badge) { badge.className = "px-2.5 py-1 rounded-full text-2xs font-mono shrink-0 bg-rose-500/20 text-rose-400"; badge.innerText = "Rejected"; }
    }
  });
}

async function saveAllSettings() {
  refreshSettingsDerived();
  const min = document.getElementById("cfg-min-delay"), max = document.getElementById("cfg-max-delay");
  if (min && max && Number(min.value) > Number(max.value)) {
    UI.toast("Minimum pause can't be longer than the maximum.", "error");
    max.focus();
    return;
  }

  const now = collectSettings();
  const payload = {};
  for (const key of dirtySettingKeys()) {
    const value = now[key];
    if (value === null || (typeof value === "number" && Number.isNaN(value))) {
      UI.toast(`"${key.split(".")[1].replace(/_/g, " ")}" needs a number.`, "error");
      return;
    }
    if (value === SECRET_MASK) continue; // untouched secret
    const [section, k] = key.split(".");
    (payload[section] = payload[section] || {})[k] = value;
  }
  if (!Object.keys(payload).length) { updateDirtyState(); return; }

  await UI.busy(document.getElementById("btn-settings-save"), "Saving…", async () => {
    try {
      await UI.api("/api/settings", { body: payload, prefix: "Settings not saved" });
      if (payload.performance && payload.performance.max_workers !== undefined) {
        await UI.api("/api/queue/concurrency", { body: { workers: payload.performance.max_workers }, quiet: true }).catch(() => {});
      }
      UI.toast("Settings saved.", "success", 2200);
      await loadSettings(true);
      fetchStatus();
    } catch (_) { /* reported */ }
  });
}

async function resetSettingsDefaults() {
  const ok = await UI.confirm({
    title: "Restore default settings?",
    message: "Every preference goes back to its factory value. Your Spotify sign-in and downloaded files are not touched.",
    confirmText: "Restore defaults", tone: "danger"
  });
  if (!ok) return;
  try {
    await UI.api("/api/settings/reset", { method: "POST", prefix: "Reset failed" });
    UI.toast("Settings restored to defaults.", "success");
    loadSettings(true);
  } catch (_) { /* reported */ }
}

// Live feedback + dirty tracking for the whole form (delegated: inputs are static markup).
document.addEventListener("input", (e) => { if (e.target.closest && e.target.closest("#settings-form")) { refreshSettingsDerived(); updateDirtyState(); } });
document.addEventListener("change", (e) => { if (e.target.closest && e.target.closest("#settings-form")) { refreshSettingsDerived(); updateDirtyState(); } });
window.addEventListener("beforeunload", (e) => { if (settingsLoaded && dirtySettingKeys().length) { e.preventDefault(); e.returnValue = ""; } });

// ================= STORAGE HEALTH (from the server, never assumed) =================
async function loadStorageHealth() {
  try {
    const r = await UI.api("/api/system/readiness", { quiet: true });
    const drive = (r.project_root || "").slice(0, 2);
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };

    set("storage-root", r.project_root || "--");
    set("storage-db", (r.project_root || "") + "\\data\\studio.db");
    set("storage-write", r.target_drive_writable ? "Writable ✓" : "Not writable ✗ (check folder permissions)");
    set("storage-ffmpeg", r.ffmpeg_installed ? (r.ffmpeg_version || "Installed") : "Missing: bin/ffmpeg.exe not found");

    const dl = document.getElementById("cfg-downloads-dir");
    if (dl && document.activeElement !== dl) dl.value = r.downloads_dir || "";

    const health = document.getElementById("storage-health");
    if (health) {
      const ok = r.ready;
      health.className = `flex items-start text-2xs font-medium ${ok ? "text-emerald-400" : "text-amber-400"}`;
      health.innerHTML = `<i data-lucide="${ok ? "shield-check" : "alert-triangle"}" aria-hidden="true" class="w-3.5 h-3.5 mr-1 mt-0.5 shrink-0"></i><span>${ok ? `Data stays in the app folder (${escapeHtml(drive)})` : "Storage needs attention. Open Settings → Storage."}</span>`;
      UI.icons(health);
    }
  } catch (_) { /* quiet: the banner covers outages */ }
}

async function browseForFolder(inputId) {
  const input = document.getElementById(inputId);
  try {
    const res = await UI.api("/api/system/pick-folder", { body: { start: input ? input.value : "" }, quiet: true });
    if (res.path && input) {
      input.value = res.path;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
  } catch (err) {
    UI.toast((err.data && err.data.unsupported) ? "The folder picker only works in the desktop window — type the path instead." : err.message, "info");
    if (input) input.focus();
  }
}

async function applyDownloadsDir() {
  const input = document.getElementById("cfg-downloads-dir");
  const path = input.value.trim();
  if (!path) { UI.toast("Enter a folder path.", "warning"); input.focus(); return; }
  try {
    const res = await UI.api("/api/settings/downloads-dir", { body: { path }, prefix: "Downloads folder not changed" });
    input.value = res.path;
    UI.toast(`Downloads will be saved to:\n${res.path}`, "success");
    loadStorageHealth();
  } catch (_) { /* reported */ }
}

// ================= UPDATES (opt-in) =================
let pendingUpdateFile = "";

function setUpdateStatus(text, isError) {
  const el = document.getElementById("update-status");
  if (!el) return;
  el.textContent = text;
  el.className = "text-2xs " + (isError ? "text-red-400" : "text-fg3");
}

async function checkForUpdates() {
  const dl = document.getElementById("btn-update-download");
  const inst = document.getElementById("btn-update-install");
  dl.classList.add("hidden");
  inst.classList.add("hidden");
  pendingUpdateFile = "";
  if (dirtySettingKeys().includes("maintenance.update_feed_url")) {
    setUpdateStatus("Save your settings first so the new feed address is used.", true);
    return;
  }
  setUpdateStatus("Checking…", false);
  try {
    const res = await UI.api("/api/update/check", { quiet: true });
    if (!res.enabled) setUpdateStatus("Update checks are off. Enter an HTTPS feed URL, save, then check.", false);
    else if (res.error) setUpdateStatus(res.error, true);
    else if (res.available) {
      setUpdateStatus(`Version ${res.latest} is available (you have ${res.current}). ${res.notes || ""}`.trim(), false);
      dl.classList.remove("hidden");
    } else setUpdateStatus(`You're up to date (v${res.current}).`, false);
  } catch (err) {
    setUpdateStatus("Could not check for updates: " + err.message, true);
  }
}

async function downloadUpdate() {
  setUpdateStatus("Downloading and verifying…", false);
  try {
    const res = await UI.api("/api/update/download", { method: "POST", quiet: true });
    pendingUpdateFile = res.file;
    setUpdateStatus(`Downloaded and verified ${res.file}. Click Install to continue.`, false);
    document.getElementById("btn-update-install").classList.remove("hidden");
  } catch (err) {
    setUpdateStatus(err.message || "Download failed.", true);
  }
}

async function installUpdate() {
  if (!pendingUpdateFile) return;
  const ok = await UI.confirm({ title: "Install the update?", message: "The installer starts and you'll need to close this app so it can finish.", confirmText: "Start installer" });
  if (!ok) return;
  try {
    await UI.api("/api/update/launch", { body: { file: pendingUpdateFile }, quiet: true });
    setUpdateStatus("Installer started. Close this app to let it finish.", false);
  } catch (err) {
    setUpdateStatus(err.message || "Could not start the installer.", true);
  }
}

// ================= LOG STREAM & DIAGNOSTICS =================
const MAX_LOG_LINES = 1000;
let logBuffer = [];
let currentLogSeverityFilter = "ALL";
let logLineQueue = [];
let logFlushScheduled = false;

function logMatches(entry) {
  const query = (document.getElementById("log-search-input")?.value || "").trim().toLowerCase();
  const sevOk = currentLogSeverityFilter === "ALL" || entry.level === currentLogSeverityFilter;
  return sevOk && (!query || entry.message.toLowerCase().includes(query) || String(entry.timestamp).toLowerCase().includes(query));
}

function logLineEl(data) {
  const line = document.createElement("div");
  line.className = `log-line log-${String(data.level).replace(/[^A-Za-z]/g, "")} flex space-x-2 text-xs select-text`;
  line.innerHTML = `
    <span class="text-fg4 font-mono shrink-0">[${escapeHtml(data.timestamp)}]</span>
    <span class="text-xs uppercase font-semibold shrink-0 w-16 ${getLevelColor(data.level)}">${escapeHtml(data.level)}</span>
    <span class="text-fg2 break-all">${escapeHtml(data.message)}</span>`;
  return line;
}

function scrollLogToEnd(terminal) {
  const chk = document.getElementById("autoscroll-chk");
  if (chk && chk.checked) terminal.scrollTop = terminal.scrollHeight;
}

function setLogSeverityFilter(lvl) {
  currentLogSeverityFilter = lvl;
  ["all", "error", "warning", "info"].forEach((k) => {
    const el = document.getElementById(`log-flt-${k}`);
    if (!el) return;
    const on = k.toUpperCase() === lvl;
    el.classList.toggle("active", on);
    el.setAttribute("aria-pressed", String(on));
  });
  applyLogFilters();
}

function applyLogFilters() {
  const terminal = document.getElementById("log-terminal");
  if (!terminal) return;
  const frag = document.createDocumentFragment();
  logBuffer.filter(logMatches).forEach((d) => frag.appendChild(logLineEl(d)));
  terminal.replaceChildren(frag);
  scrollLogToEnd(terminal);
}

// Incoming lines are batched per animation frame and the DOM is trimmed to the same cap as the buffer.
function flushLogLines() {
  logFlushScheduled = false;
  const terminal = document.getElementById("log-terminal");
  if (!terminal || !logLineQueue.length) { logLineQueue = []; return; }
  const frag = document.createDocumentFragment();
  logLineQueue.forEach((d) => frag.appendChild(logLineEl(d)));
  logLineQueue = [];
  terminal.appendChild(frag);
  while (terminal.childElementCount > MAX_LOG_LINES) terminal.firstElementChild.remove();
  scrollLogToEnd(terminal);
}

function startLogStream() {
  const terminal = document.getElementById("log-terminal");
  if (!terminal) return;
  const evtSource = new EventSource("/api/logs/stream");

  // The server sends named events ("event: log"), which onmessage never receives: listen for them by name.
  const onLogEvent = (e) => {
    try {
      const data = JSON.parse(e.data);
      logBuffer.push(data);
      if (logBuffer.length > MAX_LOG_LINES) logBuffer.shift();
      if (logMatches(data)) {
        logLineQueue.push(data);
        if (!logFlushScheduled) { logFlushScheduled = true; requestAnimationFrame(flushLogLines); }
      }
    } catch (err) {
      console.error("Log parsing error:", err);
    }
  };
  evtSource.addEventListener("log", onLogEvent);
  evtSource.onmessage = onLogEvent;      // unnamed events, if a future server sends them
  evtSource.onerror = () => { /* EventSource reconnects by itself; the connection banner covers real outages */ };
}

function getLevelColor(level) {
  if (level === "INFO") return "text-cyan-400";
  if (level === "WARNING") return "text-amber-400";
  if (level === "ERROR") return "text-red-400";
  return "text-fg3";
}

function clearLogs() {
  logBuffer = [];
  logLineQueue = [];
  const terminal = document.getElementById("log-terminal");
  if (terminal) terminal.replaceChildren();
}

async function exportDiagnostics() {
  try {
    const res = await UI.api("/api/diagnostics/export", { prefix: "Could not export diagnostics" });
    downloadBlob(`spotify_studio_diagnostics_${Date.now()}.json`, "application/json", JSON.stringify(res, null, 2));
    UI.toast("Diagnostics exported. Secrets are masked in the file.", "success");
  } catch (_) { /* reported */ }
}

async function copyDiagnosticsSummary() {
  try {
    const res = await fetch("/api/diagnostics/export?format=markdown");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const ok = await UI.copy(await res.text());
    UI.toast(ok ? "Summary copied. Paste it into your bug report." : "Couldn't copy to the clipboard.", ok ? "success" : "error", 2500);
  } catch (err) { UI.toast(`Couldn't build the summary: ${err.message}`, "error"); }
}

async function createSupportBundle() {
  try {
    const res = await UI.api("/api/diagnostics/bundle", { method: "POST", prefix: "Couldn't create the bundle" });
    UI.toast(`Support bundle saved (${UI.fmtBytes(res.size)}).\nAddresses, secrets and your Windows user name are removed. Read it before you share it.`, "success", 12000,
      { action: { label: "Show in folder", onClick: () => UI.api("/api/system/reveal", { body: { path: res.path }, prefix: "Could not open the folder" }) } });
  } catch (_) { /* reported */ }
}

// ================= ACCOUNT / SIGN-IN =================
function openLoginModal() {
  const a = window.__auth || {};
  const signedIn = !!a.authenticated;
  document.getElementById("login-signed-in").classList.toggle("hidden", !signedIn);
  document.getElementById("login-form").classList.toggle("hidden", signedIn);
  document.getElementById("login-subtitle").textContent = signedIn ? "You're signed in." : "Sign in to download Spotify tracks. A Premium account is required.";
  if (signedIn) {
    document.getElementById("login-signed-in-name").textContent = a.username || "Spotify account";
    const type = a.accountType === "premium" ? "Premium: downloads enabled" : a.accountType === "free" ? "Free account: Spotify blocks full-track downloads" : "Connected";
    document.getElementById("login-signed-in-type").textContent = type;
  }
  document.getElementById("login-modal").classList.remove("hidden");
}

function closeLoginModal() {
  document.getElementById("login-modal").classList.add("hidden");
  const err = document.getElementById("login-error-msg");
  err.classList.add("hidden");
  const pw = document.getElementById("login-password");
  pw.value = "";
  pw.type = "password";
  const reveal = document.getElementById("btn-login-reveal");
  if (reveal) { reveal.textContent = "Show"; reveal.setAttribute("aria-label", "Show password"); }
}

async function submitLogin(event) {
  if (event && event.preventDefault) event.preventDefault();
  const userEl = document.getElementById("login-username");
  const passEl = document.getElementById("login-password");
  const errEl = document.getElementById("login-error-msg");
  const u = userEl.value.trim(), p = passEl.value;
  const showError = (msg, el) => { errEl.innerText = msg; errEl.classList.remove("hidden"); if (el) el.focus(); };

  if (!u) return showError("Enter your Spotify username or email.", userEl);
  if (!p) return showError("Enter your password.", passEl);
  errEl.classList.add("hidden");

  await UI.busy(document.getElementById("btn-submit-login"), "Connecting to Spotify…", async () => {
    try {
      await UI.api("/api/login", { body: { username: u, password: p }, quiet: true });
      closeLoginModal();
      await fetchStatus();
      const a = window.__auth || {};
      if (a.accountType === "free") UI.toast("Signed in, but this is a free account. Spotify only allows full-track downloads on Premium.", "warning");
      else UI.toast("Signed in. Your session is cached on this computer.", "success");
    } catch (err) {
      showError(err.status === 401 ? "Spotify didn't accept that username and password." : err.message, passEl);
    }
  });
}

// ================= FIRST-RUN SETUP WIZARD =================
async function checkInitialReadiness() {
  try {
    const data = await UI.api("/api/system/readiness", { quiet: true });
    updateWizardUI(data);
    if (data.first_run_needed) openWizardModal();
  } catch (err) {
    console.error("Readiness check error:", err);
  }
}

const WIZARD_STEPS = [
  { title: "Set up Spotify Downloader Studio", sub: "First, a quick check that everything this app needs is in place." },
  { title: "How downloading works", sub: "A few things worth knowing before you start." },
  { title: "Choose a home for your music", sub: "Last step: pick where finished downloads are saved." }
];
let wizardStep = 1;

function wizardGo(n) {
  wizardStep = Math.min(3, Math.max(1, n));
  document.querySelectorAll("[data-wizard-step]").forEach((el) => el.classList.toggle("hidden", Number(el.dataset.wizardStep) !== wizardStep));
  document.querySelectorAll(".wiz-dot").forEach((d) => {
    const i = Number(d.dataset.dot);
    d.classList.toggle("is-done", i < wizardStep);
    if (i === wizardStep) d.setAttribute("aria-current", "step"); else d.removeAttribute("aria-current");
  });
  document.getElementById("wizard-step-label").textContent = `Step ${wizardStep} of 3`;
  document.getElementById("wizard-title").textContent = WIZARD_STEPS[wizardStep - 1].title;
  document.getElementById("wizard-subtitle").textContent = WIZARD_STEPS[wizardStep - 1].sub;
  document.getElementById("btn-wizard-back").classList.toggle("hidden", wizardStep === 1);
  document.getElementById("btn-wizard-next").classList.toggle("hidden", wizardStep === 3);
  const fin = document.getElementById("btn-wizard-finish");
  fin.classList.toggle("hidden", wizardStep !== 3);
  fin.classList.toggle("flex", wizardStep === 3);
  UI.icons(document.getElementById("wizard-modal"));
}

function wizardNext() { wizardGo(wizardStep + 1); }
function wizardBack() { wizardGo(wizardStep - 1); }

async function openWizardModal() {
  try { updateWizardUI(await UI.api("/api/system/readiness", { quiet: true })); } catch (_) { /* shown as-is */ }
  wizardGo(1);
  document.getElementById("wizard-modal").classList.remove("hidden");
  UI.icons(document.getElementById("wizard-modal"));
}

function closeWizardModal() {
  document.getElementById("wizard-modal").classList.add("hidden");
}

function updateWizardUI(data) {
  if (!data) return;
  const drive = (data.project_root || "").slice(0, 2);
  const diskEl = document.getElementById("wizard-disk-free");
  if (diskEl) diskEl.innerText = `${data.disk_free_gb} GB free on ${drive || "this drive"}${data.target_drive_writable ? " (write test passed)" : " (write test FAILED)"}`;

  const ffmpegEl = document.getElementById("wizard-ffmpeg-detail");
  const ffBadge = document.getElementById("wizard-ffmpeg-badge");
  if (ffmpegEl) {
    ffmpegEl.innerText = data.ffmpeg_installed
      ? `Found: ${data.ffmpeg_path || "bin/ffmpeg.exe"}`
      : "FFmpeg is missing. Put ffmpeg.exe and ffprobe.exe in the bin folder.";
  }
  if (ffBadge) {
    ffBadge.innerText = data.ffmpeg_installed ? "Ready" : "Missing";
    ffBadge.className = `px-2 py-0.5 rounded-full text-2xs font-mono ${data.ffmpeg_installed ? "bg-emerald-500/20 text-emerald-400" : "bg-red-500/20 text-red-400"}`;
  }
  const storageBadge = document.getElementById("wizard-storage-badge");
  if (storageBadge) {
    storageBadge.innerText = data.target_drive_writable ? "Writable" : "Not writable";
    storageBadge.className = `px-2 py-0.5 rounded-full text-2xs font-mono ${data.target_drive_writable ? "bg-emerald-500/20 text-emerald-400" : "bg-red-500/20 text-red-400"}`;
  }
  const pathInput = document.getElementById("wizard-downloads-path");
  if (pathInput && data.downloads_dir) pathInput.value = data.downloads_dir;
}

async function completeSetupWizard() {
  const dlPath = document.getElementById("wizard-downloads-path").value.trim() || "downloads";
  await UI.busy(document.getElementById("btn-wizard-finish"), "Finishing setup…", async () => {
    try {
      await UI.api("/api/system/complete-setup", { body: { downloads_path: dlPath }, prefix: "Setup not completed" });
      closeWizardModal();
      fetchStatus();
      loadSettings(true);
      loadStorageHealth();
      UI.toast(`Setup complete.\nDownloads folder: ${dlPath}`, "success");
    } catch (_) { /* reported */ }
  });
}
