// ================= MATCH LAB =================
function spotifyTrackToNormalized(track) {
  const artists = Array.isArray(track.artists) ? track.artists : [track.artists].filter(Boolean);
  return {
    provider: "spotify", provider_item_id: track.id, entity_type: "track", title: track.title,
    primary_creator: artists[0] || "", contributors: artists, album: track.album || "",
    duration_ms: track.duration_ms || 0, thumbnail_url: track.cover_url || "", isrc: track.isrc || "",
    canonical_url: `https://open.spotify.com/track/${track.id}`
  };
}

function scorePillClass(pct) {
  return pct >= 85 ? "score-pill-high" : pct >= 70 ? "score-pill-medium" : "score-pill-low";
}

function renderMatchResult(track, rows) {
  const card = document.getElementById("match-result-card");
  card.classList.remove("hidden");
  const best = rows[0];
  const pct = best ? Math.round(best.confidence * 100) : 0;
  const badge = document.getElementById("match-confidence-badge");
  badge.innerText = best ? `${pct}% match` : "No candidates";
  badge.className = `px-3 py-1 rounded-full text-xs font-semibold font-mono ${best ? scorePillClass(pct) : "bg-s3 text-fg3"}`;

  document.getElementById("match-exp-title").innerText = track.title || "--";
  document.getElementById("match-exp-artist").innerText = (track.artists || []).join(", ") || "--";
  document.getElementById("match-cand-title").innerText = best ? best.title : "Nothing found";
  document.getElementById("match-cand-source").innerText = best ? `${best.source}${best.creator ? " · " + best.creator : ""}` : "--";
  document.getElementById("match-reasons-list").innerHTML = best ? (best.reasons || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("") || "<li>No explanation was provided.</li>" : "";

  const wrap = document.getElementById("match-candidates");
  const list = document.getElementById("match-candidates-list");
  wrap.classList.toggle("hidden", rows.length < 2);
  list.innerHTML = rows.map((r) => {
    const p = Math.round(r.confidence * 100);
    return `
      <li class="p-3 rounded-xl bg-s2 border border-line flex flex-wrap items-center justify-between gap-3 text-xs">
        <div class="min-w-0">
          <div class="font-semibold text-fg truncate">${escapeHtml(r.title)} <span class="text-fg3 font-normal">· ${escapeHtml(r.creator || "")}</span></div>
          <div class="text-fg3 truncate">${escapeHtml(r.source)}${r.duration ? " · " + UI.fmtDuration(r.duration) : ""}${r.outcome ? " · " + escapeHtml(r.outcome.replace(/_/g, " ").toLowerCase()) : ""}</div>
          ${r.explanation ? `<div class="text-fg4 italic truncate" title="${escapeHtml(r.explanation)}">${escapeHtml(r.explanation)}</div>` : ""}
        </div>
        <span class="px-2.5 py-1 rounded-full font-mono font-semibold ${scorePillClass(p)}">${p}%</span>
      </li>`;
  }).join("");
  UI.icons(card);
}

async function testMatchCandidate() {
  const url = document.getElementById("match-test-url").value.trim();
  if (!url) return UI.toast("Paste a Spotify track link first.", "info");

  await UI.busy(document.getElementById("btn-match-run"), "Scoring…", async () => {
    try {
      const parsed = await UI.api("/api/links/parse", { body: { text: url }, prefix: "Link not understood" });
      const link = (parsed.links || []).find((l) => l.is_valid);
      if (!link) return UI.toast("That doesn't look like a Spotify link.", "warning");
      if (link.kind !== "track") return UI.toast("Match lab scores single tracks. Paste a track link.", "warning");

      const track = (await UI.api("/api/resolve", { body: { kind: link.kind, id: link.id }, prefix: "Could not look up the track" })).data;
      const mTitle = document.getElementById("match-manual-title").value.trim();

      if (mTitle) {
        // One recording supplied by the user.
        const mArtist = document.getElementById("match-manual-artist").value.trim();
        const mDur = Number(document.getElementById("match-manual-duration").value) || 0;
        const res = await UI.api("/api/match/evaluate", {
          body: { expected: track, candidate: { id: "manual", title: mTitle, artists: mArtist ? [mArtist] : [], duration_ms: mDur * 1000, source: "Manual entry" } },
          prefix: "Could not score the recording"
        });
        const c = res.candidate || {};
        renderMatchResult(track, [{ title: mTitle, creator: mArtist, source: "Manual entry", confidence: res.confidence || 0, reasons: c.match_reasons || [], duration: mDur, outcome: res.is_acceptable ? "acceptable" : "rejected" }]);
        return;
      }

      // Otherwise: real candidates from YouTube Music / YouTube, scored by the engine.
      const found = await UI.api("/api/alternatives/find", { body: { item: spotifyTrackToNormalized(track) }, prefix: "Candidate search failed" });
      const candidates = (found.report && found.report.candidates) || [];
      const rows = candidates.map((c) => ({
        title: c.item.title, creator: c.item.primary_creator, source: (c.item.provider || "").toUpperCase(),
        confidence: c.confidence_score || 0, duration: (c.item.duration_ms || 0) / 1000, outcome: c.outcome,
        explanation: c.explanation, reasons: c.explanation ? [c.explanation] : []
      })).sort((a, b) => b.confidence - a.confidence);
      renderMatchResult(track, rows);
      if (!rows.length) UI.toast("No candidates were found on YouTube Music or YouTube for that track.", "info");
    } catch (_) { /* reported */ }
  });
}

// ================= PLAYLIST SYNCHRONIZATION =================
async function syncPlaylistId() {
  const url = document.getElementById("sync-playlist-url").value.trim();
  if (!url) { UI.toast("Paste a playlist link first.", "info"); return null; }
  const parsed = await UI.api("/api/links/parse", { body: { text: url }, prefix: "Link not understood" });
  const link = (parsed.links || []).find((l) => l.is_valid);
  if (!link || link.kind !== "playlist") { UI.toast("That isn't a Spotify playlist link.", "warning"); return null; }
  return link.id;
}

async function runSyncCompare(dryRun = true) {
  try {
    const id = await syncPlaylistId();
    if (!id) return;
    const res = await UI.api("/api/sync/compare", { body: { playlist_id: id, dry_run: dryRun }, prefix: "Comparison failed" });
    const r = res.report;
    document.getElementById("sync-report-card").classList.remove("hidden");
    document.getElementById("sync-report-title").innerText = `Sync report: ${r.playlist_name || "Playlist"}`;
    document.getElementById("sync-total").innerText = r.total_tracks || 0;
    document.getElementById("sync-added").innerText = r.added_count || 0;
    document.getElementById("sync-removed").innerText = r.removed_count || 0;
    document.getElementById("sync-local").innerText = r.already_local_count || 0;
  } catch (_) { /* reported */ }
}

async function runSyncApply() {
  try {
    const id = await syncPlaylistId();
    if (!id) return;
    const res = await UI.api("/api/sync/apply", { body: { playlist_id: id }, prefix: "Sync failed" });
    UI.toast(res.enqueued ? `Queued ${res.enqueued} missing track${res.enqueued === 1 ? "" : "s"}.` : "Nothing to add: your local copy is up to date.", "success");
    if (res.enqueued) switchTab("queue");
  } catch (_) { /* reported */ }
}

// ================= LOCAL LIBRARY =================
let libCurrentPage = 0;
let libPageLimit = 50;
let libCurrentFilter = "all";
let libSearchQuery = "";
let libSortBy = "last_scanned";
let libSortDir = "desc";
let libScanPollTimer = null;
let currentInspectedPath = "";
let libRequestSeq = 0;

function debounceLibSearch() {
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(() => {
    libSearchQuery = document.getElementById("lib-search-input").value.trim();
    libCurrentPage = 0;
    markViewDirty();
    refreshLibrary();
  }, 300);
}

function paintLibraryFilter() {
  ["all", "missing_tags", "missing_art", "exact_duplicates", "anomalies", "review"].forEach((f) => {
    const el = document.getElementById(`btn-flt-${f}`);
    if (!el) return;
    const on = f === libCurrentFilter;
    el.className = on
      ? "px-3 py-1 rounded-lg bg-spotify-green/20 text-spotify-green font-semibold text-xs transition-colors"
      : "px-3 py-1 rounded-lg bg-s3 text-fg3 hover:text-fg text-xs transition-colors";
    el.setAttribute("aria-pressed", String(on));
  });
}

function setLibraryFilter(filter) {
  libCurrentFilter = filter;
  libCurrentPage = 0;
  paintLibraryFilter();
  markViewDirty();
  refreshLibrary();
}

function setLibrarySort(key) {
  if (libSortBy === key) libSortDir = libSortDir === "asc" ? "desc" : "asc";
  else { libSortBy = key; libSortDir = "asc"; }
  libCurrentPage = 0;
  paintLibrarySort();
  markViewDirty();
  refreshLibrary();
}

function drillDownToLibrary(filter) {
  switchTab("library");
  setLibraryFilter(filter);
}

function changeLibraryPage(delta) {
  libCurrentPage = Math.max(0, libCurrentPage + delta);
  refreshLibrary();
  const sc = document.getElementById("content-scroll");
  if (sc) sc.scrollTo({ top: 0 });
}

function changeLibraryLimit(newLimit) {
  libPageLimit = parseInt(newLimit, 10);
  libCurrentPage = 0;
  refreshLibrary();
}

// Each optional column knows its header, server-side sort key and how to render its cell.
const LIB_COLUMNS = [
  { id: "title", label: "Title & filename", sort: "title", required: true, th: "py-3 px-4",
    cell: (f) => `<td class="py-2.5 px-4"><div class="font-semibold text-fg truncate max-w-xs" title="${escapeHtml(f.title)}">${escapeHtml(f.title)}</div><div class="text-2xs text-fg4 font-mono truncate max-w-xs" title="${escapeHtml(f.filename)}">${escapeHtml(f.filename)}</div></td>` },
  { id: "artist", label: "Artist", sort: "artist", on: true, th: "py-3 px-4",
    cell: (f, h) => `<td class="py-2.5 px-4 text-fg3 truncate max-w-[150px]" title="${escapeHtml(f.artist)}"><button type="button" class="hover:text-fg hover:underline truncate max-w-full text-left" data-on-click="openArtistView_53ee7d" data-args-click="${jsonAttr([h.enc(f.artist)])}">${escapeHtml(f.artist)}</button></td>` },
  { id: "album", label: "Album", sort: "album", on: true, hide: "md", th: "py-3 px-4",
    cell: (f, h) => `<td class="py-2.5 px-4 text-fg3 truncate max-w-[150px]" title="${escapeHtml(f.album)}"><button type="button" class="hover:text-fg hover:underline truncate max-w-full text-left" data-on-click="openAlbumView_810177" data-args-click="${jsonAttr([h.enc(f.album), h.enc(f.artist)])}">${escapeHtml(f.album)}</button></td>` },
  { id: "year", label: "Year", sort: "year", hide: "md", th: "py-3 px-3 w-16",
    cell: (f) => `<td class="py-2.5 px-3 font-mono text-xs text-fg3">${escapeHtml(f.year || "—")}</td>` },
  { id: "genre", label: "Genre", sort: "genre", hide: "md", th: "py-3 px-3",
    cell: (f) => `<td class="py-2.5 px-3 text-xs text-fg3 truncate max-w-[110px]" title="${escapeHtml(f.genre || "")}">${escapeHtml(f.genre || "—")}</td>` },
  { id: "duration", label: "Time", sort: "duration", hide: "md", th: "py-3 px-3 w-16",
    cell: (f) => `<td class="py-2.5 px-3 font-mono text-xs text-fg3">${UI.fmtDuration(f.duration_sec)}</td>` },
  { id: "format", label: "Format", sort: "format", on: true, hide: "sm", th: "py-3 px-3 w-20",
    cell: (f) => `<td class="py-2.5 px-3 font-mono text-fg3 text-2xs uppercase font-medium">${escapeHtml(f.format)}</td>` },
  { id: "quality", label: "Quality", sort: "bitrate", on: true, hide: "sm", th: "py-3 px-3 w-28",
    cell: (f) => `<td class="py-2.5 px-3 font-mono text-xs text-fg2"><div>${f.bitrate_kbps ? f.bitrate_kbps + "k" : "--"} <span class="text-2xs text-fg4">${(f.size_bytes / (1024 * 1024)).toFixed(1)} MB</span></div></td>` },
  { id: "issues", label: "Identified issues", on: true, hide: "lg", th: "py-3 px-4",
    cell: (f) => {
      const issues = f.issues || [];
      const badges = issues.length
        ? issues.slice(0, 2).map((iss) => `<span class="px-1.5 py-0.5 rounded text-2xs font-semibold bg-amber-950/60 text-amber-300 border border-amber-800/40 truncate max-w-[130px] inline-block align-middle" title="${escapeHtml(iss)}">${escapeHtml(iss)}</span>`).join(" ")
          + (issues.length > 2 ? ` <span class="text-2xs text-fg4 font-mono" title="${escapeHtml(issues.slice(2).join(", "))}">+${issues.length - 2}</span>` : "")
        : `<span class="px-1.5 py-0.5 rounded text-2xs font-semibold bg-emerald-950/60 text-emerald-400 border border-emerald-800/40">✓ Healthy</span>`;
      return `<td class="py-2.5 px-4">${badges}</td>`;
    } }
];

// Narrow windows drop the lower-priority columns (see .lib-hide-* in style.css); the Columns menu still lists them.
function hideClass(c) { return c.hide ? `lib-hide-${c.hide}` : ""; }
function renderLibraryCell(c, f, h) { return c.cell(f, h).replace(/^<td class="/, `<td class="${hideClass(c)} `); }

function visibleLibraryColumns() {
  const saved = UI.store.get("lib.columns", null);
  return LIB_COLUMNS.filter((c) => c.required || (Array.isArray(saved) ? saved.includes(c.id) : c.on));
}

function libraryColspan() { return visibleLibraryColumns().length + 3; }   // + select, play, actions

function renderLibraryHeader() {
  const row = document.getElementById("lib-head-row");
  if (!row) return;
  row.innerHTML = `
    <th class="py-3 px-3 w-10 text-center"><input aria-label="Select / Deselect all" type="checkbox" id="chk-master-lib" data-on-change="toggleSelectAllLibrary_3fb3ae" class="accent-spotify-green cursor-pointer" title="Select / Deselect all" /></th>
    <th scope="col" class="py-3 px-3 w-10 text-center"><span class="sr-only">Play</span></th>
    ${visibleLibraryColumns().map((c) => c.sort
      ? `<th scope="col" aria-sort="none" data-lib-sort="${c.sort}" data-on-click="sortLibraryColumn" data-args-click='["${c.sort}"]' class="${c.th} ${hideClass(c)} cursor-pointer hover:text-fg select-none">${escapeHtml(c.label)} <span class="sort-ind" aria-hidden="true"></span></th>`
      : `<th scope="col" class="${c.th} ${hideClass(c)}">${escapeHtml(c.label)}</th>`).join("")}
    <th scope="col" class="py-3 px-3 w-24 text-right"><span class="sr-only">Actions</span></th>`;
  paintLibrarySort();
  updateBatchActionBar();
}

function paintLibrarySort() {
  document.querySelectorAll("[data-lib-sort]").forEach((th) => {
    const on = th.dataset.libSort === libSortBy;
    th.setAttribute("aria-sort", on ? (libSortDir === "asc" ? "ascending" : "descending") : "none");
    const ind = th.querySelector(".sort-ind");
    if (ind) ind.textContent = on ? (libSortDir === "asc" ? "▲" : "▼") : "";
  });
}

function libraryRowHtml(f) {
  const isChecked = selectedLibraryPaths.has(f.path);
  const enc = (v) => encodeURIComponent(v || "");
  const helpers = { enc };
  const playArgs = jsonAttr([enc(f.path), enc(f.title), enc(f.artist), enc(f.album)]);
  const one = jsonAttr([enc(f.path)]);

  return `
    <tr class="hover:bg-hv transition-colors ${isChecked ? "bg-spotify-green/5" : ""}">
      <td class="py-2.5 px-3 text-center">
        <input type="checkbox" aria-label="Select ${escapeHtml(f.title)}" class="lib-row-chk accent-spotify-green cursor-pointer" data-path="${escapeHtml(f.path)}" ${isChecked ? "checked" : ""} data-on-change="toggleLibraryRowSelection_ff200f" />
      </td>
      <td class="py-2.5 px-3 text-center">
        <button type="button" data-on-click="playLocalTrack_15214c" data-args-click="${playArgs}" aria-label="Play ${escapeHtml(f.title)}" class="p-2 rounded-xl bg-s3 hover:bg-s4 text-fg cursor-pointer">
          <i data-lucide="play" aria-hidden="true" class="w-3.5 h-3.5 fill-black"></i>
        </button>
      </td>
      ${visibleLibraryColumns().map((c) => renderLibraryCell(c, f, helpers)).join("")}
      <td class="py-2.5 px-3 text-right">
        <div class="flex items-center justify-end space-x-0.5">
          <button type="button" data-on-click="openFileInspector_45a42b" data-args-click="${one}" title="Technical details" aria-label="Technical details: ${escapeHtml(f.title)}" class="p-1.5 rounded-lg hover:bg-s4 text-cyan-400 hover:text-fg transition-colors"><i data-lucide="info" aria-hidden="true" class="w-3.5 h-3.5"></i></button>
          <button type="button" data-on-click="openMetadataRepairModal_6e1598" data-args-click="${one}" title="Repair tags" aria-label="Repair tags: ${escapeHtml(f.title)}" class="p-1.5 rounded-lg hover:bg-s4 text-amber-400 hover:text-fg transition-colors"><i data-lucide="wrench" aria-hidden="true" class="w-3.5 h-3.5"></i></button>
          <button type="button" data-on-click="openTagModal_38155e" data-args-click="${playArgs}" title="Edit tags" aria-label="Edit tags: ${escapeHtml(f.title)}" class="p-1.5 rounded-lg hover:bg-s4 text-fg3 hover:text-fg transition-colors"><i data-lucide="edit-3" aria-hidden="true" class="w-3.5 h-3.5"></i></button>
        </div>
      </td>
    </tr>`;
}

// ---------- column chooser
function renderColumnsMenu() {
  const menu = document.getElementById("lib-columns-menu");
  if (!menu) return;
  const on = new Set(visibleLibraryColumns().map((c) => c.id));
  menu.innerHTML = `
    <div class="px-3 py-1.5 text-2xs font-semibold text-fg4">Show columns</div>
    ${LIB_COLUMNS.map((c) => `
      <label class="flex items-center space-x-2 px-3 py-1.5 text-xs ${c.required ? "text-fg4" : "text-fg2 hover:bg-hv cursor-pointer"}">
        <input type="checkbox" class="accent-spotify-green" ${on.has(c.id) ? "checked" : ""} ${c.required ? "disabled" : ""} data-on-change="toggleLibraryColumn" data-args-change='["${c.id}"]' />
        <span>${escapeHtml(c.label)}${c.required ? " (always)" : ""}</span>
      </label>`).join("")}
    <div class="border-t border-line mt-1 pt-1"><button type="button" data-on-click="resetLibraryColumns" class="w-full text-left px-3 py-1.5 text-xs text-fg3 hover:text-fg hover:bg-hv">Reset to default</button></div>`;
}

function toggleColumnMenu() {
  const menu = document.getElementById("lib-columns-menu");
  const btn = document.getElementById("btn-lib-columns");
  const open = menu.classList.toggle("hidden") === false;
  btn.setAttribute("aria-expanded", String(open));
  if (open) renderColumnsMenu();
}

function closeColumnMenu() {
  document.getElementById("lib-columns-menu")?.classList.add("hidden");
  document.getElementById("btn-lib-columns")?.setAttribute("aria-expanded", "false");
}

function setLibraryColumns(ids) {
  UI.store.set("lib.columns", ids);
  renderLibraryHeader();
  renderColumnsMenu();
  markViewDirty();
  refreshLibrary();
}

function toggleLibraryColumn(id, checked) {
  const ids = new Set(visibleLibraryColumns().map((c) => c.id));
  if (checked) ids.add(id); else ids.delete(id);
  setLibraryColumns(LIB_COLUMNS.filter((c) => ids.has(c.id)).map((c) => c.id));
}

function resetLibraryColumns() {
  setLibraryColumns(LIB_COLUMNS.filter((c) => c.required || c.on).map((c) => c.id));
}

document.addEventListener("click", (e) => { if (!e.target.closest("#lib-columns-wrap")) closeColumnMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeColumnMenu(); });

// ---------- saved views (filter + search + sort + columns), stored on this computer
const libraryViews = () => UI.store.get("lib.views", []);

function renderViewsSelect(selected = "") {
  const sel = document.getElementById("lib-views-select");
  if (!sel) return;
  const views = libraryViews();
  sel.innerHTML = `<option value="">Saved views…</option>` + views.map((v) => `<option value="${escapeHtml(v.name)}">${escapeHtml(v.name)}</option>`).join("");
  sel.value = views.some((v) => v.name === selected) ? selected : "";
  document.getElementById("btn-lib-delete-view")?.classList.toggle("hidden", !sel.value);
}

function markViewDirty() {
  const sel = document.getElementById("lib-views-select");
  if (sel && sel.value) { sel.value = ""; document.getElementById("btn-lib-delete-view")?.classList.add("hidden"); }
}

async function saveLibraryView() {
  const name = await UI.prompt({ title: "Save this view", message: "Remembers the current filter, search, sort order and columns.", label: "View name", placeholder: "e.g. Needs artwork", confirmText: "Save view" });
  if (!name) return;
  const views = libraryViews().filter((v) => v.name !== name);
  views.push({ name, filter: libCurrentFilter, search: libSearchQuery, sortBy: libSortBy, sortDir: libSortDir, columns: visibleLibraryColumns().map((c) => c.id) });
  UI.store.set("lib.views", views);
  renderViewsSelect(name);
  UI.toast(`Saved view "${name}".`, "success", 2000);
}

function applyLibraryView(name) {
  const v = libraryViews().find((x) => x.name === name);
  document.getElementById("btn-lib-delete-view")?.classList.toggle("hidden", !v);
  if (!v) return;
  libSearchQuery = v.search || "";
  const si = document.getElementById("lib-search-input");
  if (si) si.value = libSearchQuery;
  libSortBy = v.sortBy || "last_scanned";
  libSortDir = v.sortDir || "desc";
  UI.store.set("lib.columns", v.columns);
  libCurrentFilter = v.filter || "all";
  libCurrentPage = 0;
  paintLibraryFilter();
  renderLibraryHeader();
  refreshLibrary();
}

async function deleteLibraryView() {
  const sel = document.getElementById("lib-views-select");
  const name = sel && sel.value;
  if (!name) return;
  const ok = await UI.confirm({ title: `Delete the view "${name}"?`, message: "Only the saved shortcut is removed; your tracks are untouched.", confirmText: "Delete view", tone: "danger" });
  if (!ok) return;
  UI.store.set("lib.views", libraryViews().filter((v) => v.name !== name));
  renderViewsSelect();
}

async function refreshLibrary() {
  const seq = ++libRequestSeq;
  const tbody = document.getElementById("lib-table-body");
  if (!tbody.children.length) tbody.innerHTML = UI.skeletonRows(libraryColspan());   // first load only
  try {
    const offset = libCurrentPage * libPageLimit;
    const params = new URLSearchParams({ search: libSearchQuery, filter: libCurrentFilter, limit: libPageLimit, offset, sort_by: libSortBy, sort_dir: libSortDir });
    const data = await UI.api(`/api/library?${params}`, { quiet: true });
    if (seq !== libRequestSeq) return; // a newer request superseded this one
    const total = data.total || 0;
    cachedLibraryFiles = data.files || [];

    const setText = (id, v) => { const el = document.getElementById(id); if (el) el.innerText = v; };
    setText("lib-showing-count", total);
    const maxPages = Math.ceil(total / libPageLimit) || 1;
    setText("lib-pagination-info", total > 0
      ? `Showing ${offset + 1}–${Math.min(offset + cachedLibraryFiles.length, total)} of ${total} tracks (page ${libCurrentPage + 1} of ${maxPages})`
      : "0 tracks found");
    document.getElementById("btn-lib-prev").disabled = libCurrentPage === 0;
    document.getElementById("btn-lib-next").disabled = libCurrentPage + 1 >= maxPages;

    if (!cachedLibraryFiles.length) {
      const filtered = libSearchQuery || libCurrentFilter !== "all";
      tbody.innerHTML = `
        <tr><td colspan="${libraryColspan()}" class="py-12 text-center text-fg3">
          <i data-lucide="music" aria-hidden="true" class="w-8 h-8 mx-auto mb-2 opacity-40"></i>
          <div>${filtered ? "No tracks match your search or filter." : "Your library is empty."}</div>
          ${filtered
            ? '<button type="button" data-on-click="clearLibraryFilters" class="mt-2 text-xs text-spotify-green hover:underline">Clear search and filters</button>'
            : '<button type="button" data-on-click="startLibraryBgScan_f08b35" class="mt-2 text-xs text-spotify-green hover:underline">Scan the downloads folder</button>'}
        </td></tr>`;
    } else {
      tbody.innerHTML = cachedLibraryFiles.map(libraryRowHtml).join("");
    }
    UI.icons(tbody);
    updateBatchActionBar();
  } catch (err) {
    if (seq !== libRequestSeq) return;
    tbody.innerHTML = `<tr><td colspan="${libraryColspan()}" class="py-10 text-center text-red-400">Couldn't load the library: ${escapeHtml(err.message)}<br><button type="button" data-on-click="refreshLibrary" class="mt-2 text-xs text-spotify-green hover:underline">Try again</button></td></tr>`;
  }
}

function clearLibraryFilters() {
  libSearchQuery = "";
  const s = document.getElementById("lib-search-input");
  if (s) s.value = "";
  setLibraryFilter("all");
}

// ================= BACKGROUND SCANNING =================
async function startLibraryBgScan() {
  const btn = document.getElementById("btn-lib-start-scan");
  if (btn) btn.disabled = true;
  try {
    await UI.api("/api/library/scan/start", { method: "POST", prefix: "Could not start the scan" });
    document.getElementById("btn-lib-pause-scan").classList.remove("hidden");
    document.getElementById("btn-lib-cancel-scan").classList.remove("hidden");
    document.getElementById("lib-scan-banner").classList.remove("hidden");
    if (libScanPollTimer) clearInterval(libScanPollTimer);
    libScanPollTimer = setInterval(pollLibraryScanStatus, 1000);
    pollLibraryScanStatus();
  } catch (_) {
    if (btn) btn.disabled = false;
  }
}

async function pollLibraryScanStatus() {
  try {
    const res = await UI.api("/api/library/scan/status", { quiet: true });
    const banner = document.getElementById("lib-scan-banner");
    const pauseBtn = document.getElementById("btn-lib-pause-scan");
    if (res.status === "RUNNING") {
      banner.classList.remove("hidden");
      document.getElementById("lib-scan-status-text").innerText = `Scanning ${res.current} / ${res.total} files (${res.elapsed_sec}s)…`;
      document.getElementById("lib-scan-file-text").innerText = res.current_file || "";
      if (pauseBtn) pauseBtn.innerText = "Pause";
    } else if (res.status === "PAUSED") {
      document.getElementById("lib-scan-status-text").innerText = `Scan paused at ${res.current} / ${res.total} files.`;
      if (pauseBtn) pauseBtn.innerText = "Resume";
    } else {
      clearInterval(libScanPollTimer);
      libScanPollTimer = null;
      banner.classList.add("hidden");
      document.getElementById("btn-lib-start-scan").disabled = false;
      document.getElementById("btn-lib-pause-scan").classList.add("hidden");
      document.getElementById("btn-lib-cancel-scan").classList.add("hidden");
      if (res.status === "COMPLETED") UI.toast(`Scan finished: ${res.total ?? res.current ?? 0} files checked.`, "success", 3000);
      else if (res.status === "FAILED") UI.toast("The library scan failed. See Diagnostics for details.", "error");
      refreshLibrary();
      loadIntelligence();
      fetchStatus();
      loadHomePageData();
    }
  } catch (err) {
    console.error("Poll scan status error:", err);
  }
}

async function pauseResumeLibraryScan() {
  const btn = document.getElementById("btn-lib-pause-scan");
  const pausing = btn.innerText === "Pause";
  try {
    await UI.api(`/api/library/scan/${pausing ? "pause" : "resume"}`, { method: "POST", prefix: "Scan control failed" });
    btn.innerText = pausing ? "Resume" : "Pause";
    pollLibraryScanStatus();
  } catch (_) { /* reported */ }
}

async function cancelLibraryScan() {
  const ok = await UI.confirm({ title: "Cancel the library scan?", message: "Files already scanned stay indexed.", confirmText: "Cancel scan", cancelText: "Keep scanning", tone: "danger" });
  if (!ok) return;
  try {
    await UI.api("/api/library/scan/cancel", { method: "POST", prefix: "Could not cancel the scan" });
    pollLibraryScanStatus();
  } catch (_) { /* reported */ }
}

// A toast with a one-click way to see the file that was just written.
function exportedToast(message, path) {
  UI.toast(message, "success", 9000, path ? {
    action: { label: "Show in folder", onClick: () => UI.api("/api/system/reveal", { body: { path }, prefix: "Could not open the folder" }) }
  } : undefined);
}

async function exportAuditCSV() {
  try {
    const res = await UI.api("/api/library/export-audit-csv", { method: "POST", prefix: "Audit export failed" });
    exportedToast(`Quality & metadata audit exported (${res.exported_count} rows).\n${res.path}`, res.path);
  } catch (_) { /* reported */ }
}

async function scanLocalLibrary() { startLibraryBgScan(); }

async function exportM3U() {
  try {
    const res = await UI.api("/api/library/export-m3u", { method: "POST", prefix: "Playlist export failed" });
    exportedToast(`Exported ${res.track_count} tracks to a .m3u playlist.\n${res.path}`, res.path);
  } catch (_) { /* reported */ }
}

async function exportCSV() {
  try {
    const res = await UI.api("/api/library/export-csv", { method: "POST", prefix: "CSV export failed" });
    exportedToast(`Exported the library index.\n${res.path}`, res.path);
  } catch (_) { /* reported */ }
}

// ================= MULTI-SELECT & BATCH ACTIONS =================
function toggleLibraryRowSelection(chk) {
  const path = chk.dataset.path;
  if (chk.checked) selectedLibraryPaths.add(path); else selectedLibraryPaths.delete(path);
  const tr = chk.closest("tr");
  if (tr) tr.classList.toggle("bg-spotify-green/5", chk.checked);
  updateBatchActionBar();
}

function toggleSelectAllLibrary(checked) {
  document.querySelectorAll(".lib-row-chk").forEach((c) => {
    c.checked = checked;
    if (checked) selectedLibraryPaths.add(c.dataset.path); else selectedLibraryPaths.delete(c.dataset.path);
    const tr = c.closest("tr");
    if (tr) tr.classList.toggle("bg-spotify-green/5", checked);
  });
  updateBatchActionBar();
}

function clearLibrarySelection() {
  selectedLibraryPaths.clear();
  document.querySelectorAll(".lib-row-chk").forEach((c) => {
    c.checked = false;
    const tr = c.closest("tr");
    if (tr) tr.classList.remove("bg-spotify-green/5");
  });
  updateBatchActionBar();
}

function updateBatchActionBar() {
  const bar = document.getElementById("lib-batch-bar");
  const count = selectedLibraryPaths.size;
  const countEl = document.getElementById("lib-batch-count");
  if (countEl) countEl.innerText = count;
  if (bar) bar.classList.toggle("visible", count > 0);

  const master = document.getElementById("chk-master-lib");
  const rows = Array.from(document.querySelectorAll(".lib-row-chk"));
  if (master) {
    const checked = rows.filter((c) => c.checked).length;
    master.checked = rows.length > 0 && checked === rows.length;
    master.indeterminate = checked > 0 && checked < rows.length;
  }
}

function openBatchTagModal() {
  if (selectedLibraryPaths.size === 0) return UI.toast("Select at least one track first.", "warning");
  document.getElementById("batch-tag-modal-count").innerText = selectedLibraryPaths.size;
  document.getElementById("batch-tag-modal").classList.remove("hidden");
}

function closeBatchTagModal() {
  document.getElementById("batch-tag-modal").classList.add("hidden");
}

async function applyBatchTags() {
  const fields = { artist: "artists", album: "album", genre: "genre", year: "year" };
  const meta = {};
  let any = false;
  for (const [id, key] of Object.entries(fields)) {
    if (!document.getElementById(`batch-chk-${id}`).checked) continue;
    any = true;
    const v = document.getElementById(`batch-tag-${id}`).value.trim();
    if (!v) return UI.toast(`"${id}" is ticked but empty. Untick it or fill it in.`, "warning");
    meta[key] = id === "artist" ? [v] : v;
  }
  if (!any) return UI.toast("Tick at least one field to change.", "warning");

  const n = selectedLibraryPaths.size;
  const ok = await UI.confirm({ title: `Change tags on ${n} track${n === 1 ? "" : "s"}?`, message: "The selected fields are rewritten inside each audio file (a backup is kept until the write is verified).", confirmText: "Apply" });
  if (!ok) return;

  await UI.busy(document.getElementById("btn-apply-batch-tags"), "Applying tags…", async () => {
    try {
      const res = await UI.api("/api/library/batch-tags", { body: { paths: Array.from(selectedLibraryPaths), metadata: meta }, quiet: true });
      UI.toast(`Updated tags on ${res.updated_count} track${res.updated_count === 1 ? "" : "s"}.`, "success");
      closeBatchTagModal();
      clearLibrarySelection();
      refreshLibrary();
    } catch (err) {
      const first = err.data && err.data.errors && err.data.errors[0];
      UI.toast(`Some tracks weren't updated${first ? `: ${first.error}` : ` (${err.message})`}.`, "error");
      refreshLibrary();
    }
  });
}

async function batchExportM3U() {
  if (selectedLibraryPaths.size === 0) return UI.toast("Select at least one track first.", "warning");
  try {
    const res = await UI.api("/api/library/export-m3u", { body: { paths: Array.from(selectedLibraryPaths), playlist_name: "Selected_Batch_Playlist" }, prefix: "Export failed" });
    exportedToast(`Exported ${res.track_count} tracks.\n${res.path}`, res.path);
  } catch (_) { /* reported */ }
}

async function batchReindex() {
  if (selectedLibraryPaths.size === 0) return UI.toast("Select at least one track first.", "warning");
  try {
    const res = await UI.api("/api/library/batch-reindex", { body: { paths: Array.from(selectedLibraryPaths) }, prefix: "Re-index failed" });
    UI.toast(`Re-indexed ${res.reindexed_count} track${res.reindexed_count === 1 ? "" : "s"}.`, "success");
    clearLibrarySelection();
    refreshLibrary();
  } catch (_) { /* reported */ }
}

document.addEventListener("DOMContentLoaded", () => {
  renderLibraryHeader();
  renderColumnsMenu();
  renderViewsSelect();
});
