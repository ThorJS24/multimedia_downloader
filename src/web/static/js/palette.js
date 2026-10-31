// Command palette (Ctrl+K): jump to any section, run common actions, or find and play a track.
// Built on the shared modal manager (focus trap, inert background, Esc, focus restore) and the ARIA
// combobox/listbox pattern so it is fully usable with a keyboard or screen reader.
"use strict";

const Palette = (function () {
  let el = null, input = null, list = null, empty = null;
  let shown = [];          // items currently rendered (flat, in order)
  let active = 0;
  let trackTimer = null, trackSeq = 0, trackItems = [];

  const hasFn = (name) => typeof window[name] === "function";

  function sectionItems() {
    const out = [];
    Object.values(HUBS).forEach((hub) => {
      hub.tabs.forEach(([id, label]) => {
        out.push({ group: "Go to", label: hub.tabs.length > 1 ? `${hub.label}: ${label}` : label, icon: "corner-down-right", keys: `${hub.label} ${label} ${id}`, run: () => switchTab(id, true, true) });
      });
    });
    return out;
  }

  function actionItems() {
    const a = window.__auth || {};
    const items = [
      { group: "Actions", label: "Add music…", icon: "plus", keys: "add paste link download new", run: () => { switchTab("fetch"); setTimeout(() => document.getElementById("multiline-input")?.focus(), 60); } },
      { group: "Actions", label: "Paste link from clipboard and review", icon: "clipboard-paste", keys: "paste clipboard link", run: async () => { switchTab("fetch"); await pasteClipboard(); requestStagingPreview(); } },
      { group: "Actions", label: "Scan library for new files", icon: "refresh-cw", keys: "scan index library refresh", run: () => { switchTab("library"); startLibraryBgScan(); } },
      { group: "Actions", label: isQueuePaused ? "Resume the download queue" : "Pause the download queue", icon: isQueuePaused ? "play" : "pause", keys: "pause resume queue stop", run: () => togglePauseQueue() },
      { group: "Actions", label: "Retry all failed downloads", icon: "rotate-cw", keys: "retry failed errors", run: () => retryAllFailedJobs() },
      { group: "Actions", label: "Open downloads folder", icon: "folder-open", keys: "open folder explorer downloads", run: () => openDownloadsFolder() },
      { group: "Actions", label: "Play / pause", icon: "play-circle", keys: "play pause music audio", run: () => toggleAudioPlayback() },
      { group: "Settings", label: "Switch theme (dark / light / system)", icon: "sun-moon", keys: "theme dark light appearance", run: () => cycleTheme() },
      { group: "Settings", label: "Visual effects: Full", icon: "sparkles", keys: "effects animation full", run: () => setFxChoice("full") },
      { group: "Settings", label: "Visual effects: Subtle", icon: "sparkle", keys: "effects animation subtle", run: () => setFxChoice("subtle") },
      { group: "Settings", label: "Visual effects: Off", icon: "ban", keys: "effects animation off reduce motion", run: () => setFxChoice("off") },
      { group: "Settings", label: a.authenticated ? "Spotify account / sign out" : "Sign in to Spotify", icon: "key", keys: "sign in login logout account spotify", run: () => openLoginModal() },
      { group: "Help", label: "Keyboard shortcuts", icon: "keyboard", keys: "help shortcuts keys", run: () => UI.showShortcuts() }
    ];
    return items.filter((i) => !(i.label.startsWith("Play /") && !hasFn("toggleAudioPlayback")));
  }

  // Every typed word must appear (prefix matches rank first); empty query shows a short curated list.
  function score(query, item) {
    const hay = `${item.label} ${item.keys || ""} ${item.sub || ""}`.toLowerCase();
    let s = 0;
    for (const word of query.toLowerCase().split(/\s+/).filter(Boolean)) {
      const at = hay.indexOf(word);
      if (at < 0) return -1;
      s += at === 0 || hay[at - 1] === " " ? 3 : 1;
    }
    return s;
  }

  function build() {
    if (el) return;
    el = document.createElement("div");
    el.id = "cmdk";
    el.setAttribute("data-modal", "");
    el.setAttribute("aria-label", "Command palette");
    el.className = "fixed inset-0 bg-black/60 backdrop-blur-sm z-[65] hidden flex items-start justify-center p-4 pt-[14vh]";
    el.innerHTML = `
      <div id="cmdk-panel" class="w-full max-w-xl bg-s1 border border-line2 rounded-2xl overflow-hidden">
        <div class="flex items-center px-4 border-b border-line">
          <i data-lucide="search" aria-hidden="true" class="w-4 h-4 text-fg3 shrink-0"></i>
          <input id="cmdk-input" type="text" role="combobox" aria-expanded="true" aria-controls="cmdk-list" aria-autocomplete="list" aria-label="Search commands, sections and tracks"
            placeholder="Type a command, section or song…" autocomplete="off" spellcheck="false"
            class="flex-1 bg-transparent px-3 py-4 text-sm text-fg placeholder-fg4 focus:outline-none" />
          <button type="button" data-modal-close aria-label="Close" class="text-2xs font-mono text-fg3 border border-line2 rounded px-1.5 py-0.5 hover:text-fg">Esc</button>
        </div>
        <ul id="cmdk-list" role="listbox" aria-label="Results" class="max-h-[50vh] overflow-y-auto py-2"></ul>
        <div id="cmdk-empty" class="hidden px-4 py-8 text-center text-xs text-fg3">Nothing matches that.</div>
        <div class="flex items-center justify-between px-4 py-2 border-t border-line text-2xs text-fg4">
          <span><kbd class="font-mono">↑↓</kbd> navigate · <kbd class="font-mono">Enter</kbd> run</span><span>Ctrl+K</span>
        </div>
      </div>`;
    document.body.appendChild(el);
    UI.registerModal(el);
    UI.icons(el);
    input = el.querySelector("#cmdk-input");
    list = el.querySelector("#cmdk-list");
    empty = el.querySelector("#cmdk-empty");

    el.querySelector("[data-modal-close]").addEventListener("click", close);
    input.addEventListener("input", () => { refresh(input.value); searchTracks(input.value); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Home") { e.preventDefault(); setActive(0); }
      else if (e.key === "End") { e.preventDefault(); setActive(shown.length - 1); }
      else if (e.key === "Enter") { e.preventDefault(); run(active); }
    });
    list.addEventListener("click", (e) => { const li = e.target.closest("[role=option]"); if (li) run(Number(li.dataset.idx)); });
    list.addEventListener("pointermove", (e) => { const li = e.target.closest("[role=option]"); if (li && Number(li.dataset.idx) !== active) setActive(Number(li.dataset.idx), false); });
  }

  function refresh(query) {
    const q = query.trim();
    const all = [...sectionItems(), ...actionItems()];
    let pool;
    if (!q) {
      pool = [...all.filter((i) => i.group === "Actions").slice(0, 5), ...all.filter((i) => i.group === "Go to").slice(0, 6)];
    } else {
      pool = all.map((i) => ({ i, s: score(q, i) })).filter((x) => x.s >= 0).sort((a, b) => b.s - a.s).map((x) => x.i).slice(0, 12);
    }
    shown = [...pool, ...(q.length >= 2 ? trackItems : [])];
    render();
  }

  function render() {
    let html = "", lastGroup = null;
    shown.forEach((it, idx) => {
      if (it.group !== lastGroup) { html += `<li role="presentation" class="px-4 pt-2 pb-1 text-2xs font-bold uppercase tracking-wider text-fg4">${escapeHtml(it.group)}</li>`; lastGroup = it.group; }
      html += `
        <li id="cmdk-opt-${idx}" role="option" data-idx="${idx}" aria-selected="${idx === active}" class="flex items-center gap-3 mx-2 px-3 py-2 rounded-lg cursor-pointer text-xs text-fg2">
          <i data-lucide="${it.icon || "music"}" aria-hidden="true" class="w-4 h-4 text-fg3 shrink-0"></i>
          <span class="flex-1 min-w-0 truncate"><span class="text-fg font-semibold">${escapeHtml(it.label)}</span>${it.sub ? `<span class="text-fg3"> · ${escapeHtml(it.sub)}</span>` : ""}</span>
          <span class="cmdk-kbd text-2xs font-mono text-fg3 shrink-0">↵</span>
        </li>`;
    });
    list.innerHTML = html;
    empty.classList.toggle("hidden", shown.length > 0);
    UI.icons(list);
    input.setAttribute("aria-activedescendant", shown.length ? `cmdk-opt-${active}` : "");
  }

  function setActive(i, scroll = true) {
    if (!shown.length) return;
    active = (i + shown.length) % shown.length;
    list.querySelectorAll("[role=option]").forEach((li) => li.setAttribute("aria-selected", String(Number(li.dataset.idx) === active)));
    input.setAttribute("aria-activedescendant", `cmdk-opt-${active}`);
    if (scroll) document.getElementById(`cmdk-opt-${active}`)?.scrollIntoView({ block: "nearest" });
  }
  const move = (d) => setActive(active + d);

  function searchTracks(query) {
    clearTimeout(trackTimer);
    const q = query.trim();
    if (q.length < 2) { trackItems = []; return; }
    const seq = ++trackSeq;
    trackTimer = setTimeout(async () => {
      try {
        const data = await UI.api(`/api/library?search=${encodeURIComponent(q)}&limit=6`, { quiet: true });
        if (seq !== trackSeq || el.classList.contains("hidden")) return;
        trackItems = (data.files || []).map((f) => ({
          group: "Tracks", label: f.title, sub: f.artist, icon: "music",
          run: () => playLocalTrack(encodeURIComponent(f.path), encodeURIComponent(f.title), encodeURIComponent(f.artist), true, f.album || "")
        }));
        refresh(input.value);
      } catch (_) { /* palette keeps working without track results */ }
    }, 160);
  }

  function run(idx) {
    const it = shown[idx];
    if (!it) return;
    close();
    setTimeout(() => { try { const r = it.run(); if (r && r.catch) r.catch(() => {}); } catch (err) { UI.toast(err.message || String(err), "error"); } }, 30);
  }

  function open() {
    build();
    trackItems = []; active = 0;
    input.value = "";
    refresh("");
    el.classList.remove("hidden");
    input.focus();
  }

  function close() { if (el) el.classList.add("hidden"); }
  function isOpen() { return !!el && !el.classList.contains("hidden"); }

  return { open, close, isOpen };
})();
