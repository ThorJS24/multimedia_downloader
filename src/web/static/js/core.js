// Master frontend logic: navigation (hubs + sections), status, link intake.
let currentTab = "dashboard";
let isQueuePaused = false;
let currentCollectionInfo = { id: "", name: "" };
let searchDebounceTimer = null;
let selectedLibraryPaths = new Set();
let cachedLibraryFiles = [];
let playerQueue = [];
let playerCurrentIndex = -1;

// ================= NAVIGATION MODEL =================
// Five destinations in the sidebar; the sections inside each live in a sub-navigation strip.
const HUBS = {
  home: { label: "Home", tabs: [["dashboard", "Overview"]] },
  downloads: { label: "Downloads", tabs: [["fetch", "Add music"], ["queue", "Queue"], ["history", "History"], ["matching", "Match lab"]] },
  library: {
    label: "Library",
    tabs: [["library", "Tracks"], ["search", "Discover"], ["intelligence", "Health"], ["quality_lab", "Quality"], ["comparator", "Releases"], ["optimizer", "Cleanup"]],
    detail: ["album-detail", "artist-detail", "playlist-detail"]
  },
  automation: { label: "Automation", tabs: [["sync", "Playlist sync"], ["monitor", "Artist monitor"], ["watcher", "Folder watcher"], ["rules", "Rules & hooks"], ["plugins", "Plugins"]] },
  settings: { label: "Settings", tabs: [["settings", "Preferences"], ["logs", "Diagnostics"]] }
};

// Plain count: the number is set, not animated.
function setCount(id, value) {
  const el = document.getElementById(id);
  if (el && el.textContent !== String(value)) el.textContent = String(value);
}

const TAB_TITLES = {
  dashboard: "Overview",
  fetch: "Add music",
  queue: "Download queue",
  history: "Download history",
  plugins: "Plugins & extensions",
  matching: "Match lab",
  sync: "Playlist sync",
  library: "Library",
  intelligence: "Library health",
  quality_lab: "Audio quality",
  comparator: "Release comparison",
  monitor: "Artist monitor",
  watcher: "Folder watcher",
  rules: "Rules & hooks",
  optimizer: "Cleanup & duplicates",
  logs: "Diagnostics",
  settings: "Preferences",
  search: "Discover",
  "album-detail": "Album",
  "artist-detail": "Artist",
  "playlist-detail": "Playlist"
};

const TAB_HUB = {};
Object.entries(HUBS).forEach(([hub, def]) => {
  def.tabs.forEach(([id]) => { TAB_HUB[id] = hub; });
  (def.detail || []).forEach((id) => { TAB_HUB[id] = hub; });
});

const SEARCH_PLACEHOLDERS = { library: "Filter library…", queue: "Filter queue…" };

// Tab switching history (back/forward buttons) + URL hash so a reload keeps your place.
let navHistory = ["dashboard"];
let navHistoryIndex = 0;

function updateNavHistoryButtons() {
  const btnBack = document.getElementById("btn-nav-back");
  const btnFwd = document.getElementById("btn-nav-forward");
  if (btnBack) btnBack.disabled = navHistoryIndex <= 0;
  if (btnFwd) btnFwd.disabled = navHistoryIndex >= navHistory.length - 1;
}

function navHistoryBack() {
  if (navHistoryIndex > 0) {
    navHistoryIndex--;
    switchTab(navHistory[navHistoryIndex], false);
  }
}

function navHistoryForward() {
  if (navHistoryIndex < navHistory.length - 1) {
    navHistoryIndex++;
    switchTab(navHistory[navHistoryIndex], false);
  }
}

function renderSubnav(hubId, activeTab) {
  const wrap = document.getElementById("subnav-wrap");
  const nav = document.getElementById("subnav");
  if (!wrap || !nav) return;
  const hub = HUBS[hubId];
  if (!hub || hub.tabs.length < 2) {
    wrap.classList.add("hidden");
    nav.innerHTML = "";
    delete nav.dataset.hub;
    return;
  }
  wrap.classList.remove("hidden");
  if (nav.dataset.hub === hubId) {            // same destination: just move the selection, keep focus where it is
    nav.querySelectorAll('[role="tab"]').forEach((btn) => {
      const on = btn.id === `tab-btn-${activeTab}`;
      btn.setAttribute("aria-selected", String(on));
      btn.setAttribute("tabindex", on ? "0" : "-1");
    });
    return;
  }
  nav.dataset.hub = hubId;
  nav.innerHTML = hub.tabs.map(([id, label]) => `
    <button type="button" role="tab" id="tab-btn-${id}" aria-selected="${id === activeTab}" aria-controls="tab-${id}"
      tabindex="${id === activeTab ? 0 : -1}" data-on-click="goTab" data-args-click='["${id}"]' class="subnav-btn">
      ${escapeHtml(label)}${id === "queue" ? '<span id="subnav-queue-count" class="ml-2 px-1.5 rounded-full bg-spotify-green/20 text-spotify-green text-2xs font-mono hidden"></span>' : ""}
    </button>`).join("");
  if (typeof refreshQueueBadges === "function") refreshQueueBadges();
}

// Arrow keys move between sections, like a native tab list.
document.addEventListener("keydown", (e) => {
  const tab = e.target && e.target.closest && e.target.closest('#subnav [role="tab"]');
  if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
  const tabs = Array.from(document.querySelectorAll('#subnav [role="tab"]'));
  let i = tabs.indexOf(tab);
  if (e.key === "ArrowRight") i = (i + 1) % tabs.length;
  else if (e.key === "ArrowLeft") i = (i - 1 + tabs.length) % tabs.length;
  else if (e.key === "Home") i = 0;
  else i = tabs.length - 1;
  e.preventDefault();
  tabs[i].focus();
  tabs[i].click();
});

document.addEventListener("keydown", (e) => {
  if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); navHistoryBack(); }
  else if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); navHistoryForward(); }
});

function openHub(hubId, focusHeading = false) {
  const hub = HUBS[hubId];
  if (!hub) return;
  const remembered = UI.store.get("lastTab." + hubId);
  const target = hub.tabs.some(([id]) => id === remembered) ? remembered : hub.tabs[0][0];
  switchTab(target, true, focusHeading);
}

function switchTab(tabId, pushHistory = true, focusHeading = false) {
  if (!TAB_HUB[tabId]) tabId = "dashboard";
  const hubId = TAB_HUB[tabId];

  if (pushHistory && (navHistory.length === 0 || navHistory[navHistoryIndex] !== tabId)) {
    navHistory = navHistory.slice(0, navHistoryIndex + 1);
    navHistory.push(tabId);
    navHistoryIndex = navHistory.length - 1;
  }
  updateNavHistoryButtons();

  const previousTab = currentTab;
  currentTab = tabId;
  document.querySelectorAll(".tab-content").forEach((el) => el.classList.add("hidden"));
  const targetTab = document.getElementById(`tab-${tabId}`);
  if (targetTab) targetTab.classList.remove("hidden");

  document.querySelectorAll(".nav-btn[data-hub]").forEach((el) => {
    const on = (el.dataset.hub === hubId);
    el.classList.toggle("active", on);
    if (on) el.setAttribute("aria-current", "page"); else el.removeAttribute("aria-current");
  });
  renderSubnav(hubId, tabId);
  if (HUBS[hubId].tabs.some(([id]) => id === tabId)) UI.store.set("lastTab." + hubId, tabId);

  const title = TAB_TITLES[tabId] || "Spotify Studio";
  const titleEl = document.getElementById("current-tab-title");
  if (titleEl) {
    titleEl.innerText = title;
    titleEl.setAttribute("tabindex", "-1");
    if (focusHeading) titleEl.focus({ preventScroll: true });
  }
  document.title = `${title} · Spotify Downloader Studio`;
  try {
    if (location.hash !== `#/${tabId}`) history.replaceState(null, "", `#/${tabId}`);
  } catch (_) { /* file:// or sandboxed */ }

  const scroller = document.getElementById("content-scroll");
  if (scroller && previousTab !== tabId) scroller.scrollTop = 0;

  syncSearchContext(tabId, previousTab);

  if (tabId === "library") refreshLibrary();
  if (tabId === "dashboard") { fetchStatus(); loadHomePageData(); renderDashboardQueue(cachedQueueJobs); }
  if (tabId === "search") { const sInp = document.getElementById("omni-search-input"); if (sInp) sInp.focus(); }
  if (tabId === "intelligence") loadIntelligence();
  if (tabId === "quality_lab") loadQualityLab();
  if (tabId === "comparator") fetchRepairQueue();
  if (tabId === "monitor") loadMonitors();
  if (tabId === "watcher") loadWatcherDashboard();
  if (tabId === "rules") loadRulesTab();
  if (tabId === "history") loadHistory(true);
  if (tabId === "plugins") loadPlugins();
  if (tabId === "optimizer") { refreshHashDuplicates(); refreshDedupUndo(); }
  if (tabId === "settings") { loadSettings(); loadStorageHealth(); loadBackups(); }
  if (tabId === "queue") { applyQueueFiltersAndRender(); renderQueueAlerts(); fetchQueue(); }
  UI.icons();
}

window.addEventListener("hashchange", () => {
  const wanted = location.hash.replace(/^#\/?/, "");
  if (wanted && wanted !== currentTab && TAB_HUB[wanted]) switchTab(wanted);
});

// ================= HEADER SEARCH (context aware) =================
let globalSearchDebounceTimer = null;

function syncSearchContext(tabId, previousTab) {
  const input = document.getElementById("global-search-input");
  if (!input) return;
  // Leaving a view must not leave an invisible filter behind.
  if (previousTab && previousTab !== tabId && input.value) {
    input.value = "";
    const clearBtn = document.getElementById("btn-clear-global-search");
    if (clearBtn) clearBtn.classList.add("hidden");
    applyGlobalFilter("");
  }
  input.placeholder = SEARCH_PLACEHOLDERS[tabId] || "Search music (Enter)";
  input.setAttribute("aria-label", SEARCH_PLACEHOLDERS[tabId] ? SEARCH_PLACEHOLDERS[tabId].replace("…", "") : "Search music");
}

function applyGlobalFilter(q) {
  const libInput = document.getElementById("lib-search-input");
  if (libInput && libInput.value !== q && (currentTab === "library" || q === "")) {
    libInput.value = q;
    libSearchQuery = q;
    libCurrentPage = 0;
    if (currentTab === "library") refreshLibrary();
  }
  const queueInput = document.getElementById("q-search-input");
  if (queueInput && queueInput.value !== q && (currentTab === "queue" || q === "")) {
    queueInput.value = q;
    queueSearchQuery = q;
    if (currentTab === "queue") applyQueueFiltersAndRender();
  }
}

function handleGlobalSearch(query) {
  const q = (query || "").trim();
  const clearBtn = document.getElementById("btn-clear-global-search");
  if (clearBtn) clearBtn.classList.toggle("hidden", q.length === 0);
  if (currentTab !== "library" && currentTab !== "queue") return; // elsewhere: Enter opens Discover
  clearTimeout(globalSearchDebounceTimer);
  globalSearchDebounceTimer = setTimeout(() => applyGlobalFilter(q), 250);
}

function submitGlobalSearch() {
  const input = document.getElementById("global-search-input");
  const q = input ? input.value.trim() : "";
  if (!q || currentTab === "library" || currentTab === "queue") return;
  switchTab("search");
  const omni = document.getElementById("omni-search-input");
  if (omni) {
    omni.value = q;
    if (typeof handleUnifiedSearchInput === "function") handleUnifiedSearchInput(q);
  }
  input.value = "";
  const clearBtn = document.getElementById("btn-clear-global-search");
  if (clearBtn) clearBtn.classList.add("hidden");
}

function clearGlobalSearch() {
  const input = document.getElementById("global-search-input");
  if (input) { input.value = ""; input.focus(); }
  handleGlobalSearch("");
}

// ================= SIDEBAR / THEME =================
function applySidebarState(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  const btn = document.getElementById("btn-sidebar-toggle");
  if (btn) {
    const label = collapsed ? "Expand sidebar" : "Collapse sidebar";
    btn.setAttribute("aria-expanded", String(!collapsed));
    btn.setAttribute("aria-label", label);
    btn.setAttribute("title", label);
    btn.innerHTML = `<i aria-hidden="true" data-lucide="${collapsed ? "panel-left-open" : "panel-left-close"}" class="w-4 h-4 mr-3 shrink-0"></i><span class="nav-label">Collapse</span>`;
    UI.icons(btn);
  }
}

function toggleSidebar() {
  const collapsed = !document.body.classList.contains("sidebar-collapsed");
  UI.store.set("sidebarCollapsed", collapsed);
  applySidebarState(collapsed);
}

function initSidebar() {
  const saved = UI.store.get("sidebarCollapsed", null);
  const narrow = window.matchMedia("(max-width: 1100px)");
  applySidebarState(saved === null ? narrow.matches : saved);
  narrow.addEventListener("change", () => { if (UI.store.get("sidebarCollapsed", null) === null) applySidebarState(narrow.matches); });
}

const THEME_ICONS = { dark: "moon", light: "sun", system: "monitor" };
const THEME_LABELS = { dark: "Dark", light: "Light", system: "Match Windows" };

function refreshThemeUI() {
  const pref = window.StudioTheme ? StudioTheme.get() : "dark";
  const btn = document.getElementById("btn-theme-toggle");
  if (btn) {
    btn.innerHTML = `<i aria-hidden="true" data-lucide="${THEME_ICONS[pref]}" class="w-4 h-4"></i>`;
    btn.setAttribute("title", `Theme: ${THEME_LABELS[pref]} (click to change)`);
    btn.setAttribute("aria-label", `Theme: ${THEME_LABELS[pref]}. Switch theme`);
  }
  document.querySelectorAll('input[name="theme"]').forEach((r) => { r.checked = (r.value === pref); });
  UI.icons(btn || undefined);
}

function cycleTheme() {
  const order = ["dark", "light", "system"];
  const next = order[(order.indexOf(StudioTheme.get()) + 1) % order.length];
  StudioTheme.set(next);
  refreshThemeUI();
  UI.toast(`Theme: ${THEME_LABELS[next]}`, "info", 1500);
}

// ================= INIT =================
document.addEventListener("DOMContentLoaded", () => {
  initSidebar();
  refreshThemeUI();
  const fromHash = location.hash.replace(/^#\/?/, "");
  if (fromHash && TAB_HUB[fromHash] && fromHash !== "dashboard") {
    navHistory = ["dashboard", fromHash];
    navHistoryIndex = 1;
    switchTab(fromHash, false);
  } else {
    switchTab("dashboard", false);
  }
  fetchStatus();
  startQueuePolling();
  startLogStream();
  refreshLibrary();
  loadSettings();
  loadStorageHealth();
  checkInitialReadiness();
  initAudioPlayer();
  loadHomePageData();
  updateNavHistoryButtons();
});

// ================= SYSTEM STATUS =================
async function fetchStatus() {
  try {
    const data = await UI.api("/api/status", { quiet: true });
    const drive = data.drive ? `${data.drive}` : "Disk";

    document.getElementById("disk-free-label").innerText = `${data.disk_free_gb} GB free`;
    document.getElementById("disk-drive-label").innerText = `${drive} drive`;
    document.getElementById("dash-disk").innerText = `${data.disk_free_gb} GB`;
    const dl = document.getElementById("dash-disk-label");
    if (dl) dl.innerText = `${drive} free`;
    const percent = Math.min(100, Math.round((data.disk_used_gb / data.disk_total_gb) * 100)) || 0;
    document.getElementById("disk-bar").style.width = `${percent}%`;
    const meter = document.getElementById("disk-meter");
    if (meter) meter.setAttribute("aria-valuenow", String(percent));

    setCount("dash-completed", data.completed_count || 0);
    setCount("dash-library", data.library_count || 0);
    window.__libraryCount = data.library_count || 0;

    window.__auth = { authenticated: !!data.authenticated, accountType: data.account_type || "unknown", username: data.username || "", credentialsExist: !!data.credentials_exist };
    renderAuthPill();
  } catch (err) {
    console.error("Status error:", err);
  }
}

function renderAuthPill() {
  const a = window.__auth || {};
  const ind = document.getElementById("auth-indicator");
  const txt = document.getElementById("auth-text");
  if (!ind || !txt) return;
  let dot = "bg-red-400", label = "Sign in", cls = "text-fg2";
  if (a.authenticated) {
    if (a.accountType === "free") { dot = "bg-amber-400"; label = "Free account (can't download)"; cls = "text-amber-300"; }
    else if (a.accountType === "premium") { dot = "bg-spotify-green shadow-[0_0_8px_#1DB954]"; label = a.username ? `Premium · ${a.username}` : "Premium"; cls = "text-emerald-400 font-semibold"; }
    else { dot = "bg-spotify-green shadow-[0_0_8px_#1DB954]"; label = a.username ? `Signed in · ${a.username}` : "Signed in"; cls = "text-emerald-400 font-semibold"; }
  } else if (a.credentialsExist) {
    dot = "bg-amber-400"; label = "Session expired — sign in"; cls = "text-amber-300";
  }
  ind.className = `w-2 h-2 rounded-full ${dot}`;
  txt.className = cls;
  txt.innerText = label;

  if (typeof renderQueueAlerts === "function") renderQueueAlerts();
  const settingsStatus = document.getElementById("settings-account-status");
  if (settingsStatus) settingsStatus.textContent = a.authenticated ? (a.accountType === "premium" ? "Premium" : a.accountType === "free" ? "Free" : "Connected") : "Signed out";
  const inBtn = document.getElementById("btn-settings-signin");
  const outBtn = document.getElementById("btn-settings-signout");
  if (inBtn) inBtn.classList.toggle("hidden", !!a.authenticated);
  if (outBtn) outBtn.classList.toggle("hidden", !a.authenticated && !a.credentialsExist);
}

// ================= LINK INTAKE (Add music) =================
async function pasteClipboard() {
  try {
    const text = await navigator.clipboard.readText();
    const input = document.getElementById("multiline-input");
    input.value = (input.value ? input.value + "\n" : "") + text;
    updateLinkHint();
    input.focus();
  } catch (err) {
    UI.toast("Clipboard access was blocked. Click the box and press Ctrl+V instead.", "warning");
  }
}

function clearFetchInput() {
  document.getElementById("multiline-input").value = "";
  updateLinkHint();
}

function updateLinkHint() {
  const el = document.getElementById("fetch-link-hint");
  const input = document.getElementById("multiline-input");
  if (!el || !input) return;
  const lines = input.value.split(/\s*\n\s*/).map((l) => l.trim()).filter(Boolean);
  const spotify = lines.filter((l) => /spotify\.com\/|spotify:/i.test(l)).length;
  if (!lines.length) { el.textContent = ""; return; }
  el.textContent = spotify === lines.length
    ? `${spotify} Spotify link${spotify === 1 ? "" : "s"} detected.`
    : `${spotify} Spotify link${spotify === 1 ? "" : "s"} detected, ${lines.length - spotify} other line${lines.length - spotify === 1 ? "" : "s"} (search text or unknown).`;
}

function triggerFileImport() {
  document.getElementById("file-import-input").click();
}

async function handleFileImport(e) {
  const file = e.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  try {
    const data = await UI.api("/api/links/import-file", { body: formData, prefix: "Import failed" });
    if (data.links && data.links.length > 0) {
      const urls = data.links.map((l) => l.clean_url || l.raw);
      const input = document.getElementById("multiline-input");
      input.value = urls.join("\n");
      updateLinkHint();
      UI.toast(`Imported ${data.total} link${data.total === 1 ? "" : "s"} from ${file.name}`, "success");
    } else {
      UI.toast("No Spotify links were found in that file.", "warning");
    }
  } catch (_) { /* reported */ } finally {
    e.target.value = "";
  }
}

// "Add all without review": resolve every link and enqueue straight away.
async function resolveSubmittedLinks() {
  const text = document.getElementById("multiline-input").value.trim();
  if (!text) return UI.toast("Paste at least one Spotify link first.", "warning");

  await UI.busy(document.getElementById("btn-fetch-links"), "Resolving…", async () => {
    let parsed;
    try {
      parsed = await UI.api("/api/links/parse", { body: { text, deduplicate: document.getElementById("chk-dedup").checked } });
    } catch (_) { return; }

    const valid = (parsed.links || []).filter((l) => l.is_valid);
    const invalidCount = (parsed.links || []).length - valid.length;
    if (valid.length === 0) {
      UI.toast("No valid Spotify links could be identified.", "warning");
      return;
    }

    const groups = [];
    const failures = [];
    for (const link of valid) {
      try {
        const res = await UI.api("/api/resolve", { body: { kind: link.kind, id: link.id }, quiet: true });
        const item = res.data;
        if (item.tracks && Array.isArray(item.tracks)) {
          item.tracks.forEach((t) => { t.collection_id = item.id; t.collection_name = item.name; t.playlist_name = item.name; });
          groups.push({ id: item.id, name: item.name, tracks: item.tracks });
        } else {
          groups.push({ id: "", name: "", tracks: [item] });
        }
      } catch (err) {
        failures.push(`${link.kind || "link"} ${link.id}: ${err.message}`);
      }
    }

    const total = groups.reduce((n, g) => n + g.tracks.length, 0);
    if (total === 0) {
      UI.toast(`Nothing could be added.\n${failures.slice(0, 3).join("\n")}`, "error");
      return;
    }
    if (total > 50) {
      const ok = await UI.confirm({ title: `Add ${total} tracks?`, message: "They will all be queued without a review step.", confirmText: `Add ${total} tracks` });
      if (!ok) return;
    }

    let added = 0;
    for (const g of groups) {
      try {
        const res = await UI.api("/api/queue/add", { body: { tracks: g.tracks, collection_id: g.id, collection_name: g.name } });
        added += res.added || 0;
      } catch (err) {
        failures.push(`${g.name || "tracks"}: ${err.message}`);
      }
    }

    const notes = [];
    if (invalidCount) notes.push(`${invalidCount} invalid link${invalidCount === 1 ? "" : "s"} skipped`);
    if (failures.length) notes.push(`${failures.length} failed:\n${failures.slice(0, 3).join("\n")}`);
    UI.toast(`Added ${added} track${added === 1 ? "" : "s"} to the queue.${notes.length ? "\n" + notes.join("\n") : ""}`, failures.length ? "warning" : "success");
    if (added > 0) {
      clearFetchInput();
      fetchQueue();
      switchTab("queue");
    }
  });
}
