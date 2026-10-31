// ================= QUEUE MANAGER =================
let queueEtag = "";
let cachedQueueJobs = [];
let lastQueueCounts = { total: 0, queued: 0, active: 0, completed: 0, failed: 0 };
let selectedQueueJobIds = new Set();
let queueSearchQuery = "";
let queueStatusFilter = "ALL";
let queueCollectionFilter = "ALL";
let queueSortColumn = "default";
let queueSortDirection = "asc";
let currentQueuePage = 1;
let queuePageSize = 50;
let totalQueuePages = 1;
let activeInspectingJobId = null;
let lastSelectedQueueJobId = null;

const ACTIVE_STAGES = new Set(["ACTIVE", "DOWNLOADING", "CONVERTING", "TAGGING", "FETCHING_METADATA", "MATCHING_SOURCE", "VERIFYING"]);
const isActiveJob = (j) => ACTIVE_STAGES.has(j.stage) || j.status === "ACTIVE";
const artistText = (j) => (Array.isArray(j.artists) ? j.artists.join(", ") : (j.artists || "")) || "Unknown";
const collectionOf = (j) => j.collection_name || j.album || "Singles";
const jsonAttr = (v) => escapeHtml(JSON.stringify(v));

// Poll quickly while the queue is on screen or work is running; slowly otherwise; barely at all when hidden.
function startQueuePolling() {
  UI.poll(fetchQueue, {
    visibleMs: () => (currentTab === "queue" || lastQueueCounts.active > 0 ? 1200 : 4000),
    hiddenMs: 15000
  });
}

async function fetchQueue() {
  let data;
  try {
    const res = await fetch("/api/queue/snapshot", { headers: queueEtag ? { "If-None-Match": queueEtag } : {} });
    UI.noteNetwork(true);
    if (res.status === 304) return;            // nothing changed: skip parsing and rendering
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    queueEtag = res.headers.get("ETag") || "";
    data = await res.json();
  } catch (err) {
    UI.noteNetwork(false);
    console.error("Queue poll error:", err);
    return;
  }

  const c = data.counts;
  lastQueueCounts = c;
  const setText = (id, v) => { const el = document.getElementById(id); if (el && el.innerText !== String(v)) el.innerText = v; };
  setText("q-total", c.total);
  setText("q-completed", c.completed);
  setText("q-queued", c.queued);
  setText("q-active", c.active || 0);
  setText("q-failed", c.failed);
  setText("btn-q-failed-count", c.failed);
  refreshQueueBadges();

  isQueuePaused = data.is_paused;
  const pBtn = document.getElementById("text-q-pause");
  const pIco = document.getElementById("icon-q-pause");
  if (pBtn && pIco) {
    const label = data.is_paused ? "Resume queue" : "Pause queue";
    if (pBtn.innerText !== label) {
      pBtn.innerText = label;
      const fresh = document.createElement("i");
      fresh.id = "icon-q-pause";
      fresh.className = "w-3.5 h-3.5 mr-1.5";
      fresh.setAttribute("aria-hidden", "true");
      fresh.setAttribute("data-lucide", data.is_paused ? "play" : "pause");
      pIco.replaceWith(fresh);
      UI.icons(fresh.parentElement);
    }
  }

  const qStateEl = document.getElementById("dash-queue-state");
  if (qStateEl) {
    let text = "Idle", cls = "text-fg3 font-bold";
    if (data.is_paused) { text = "Paused"; cls = "text-amber-400 font-bold"; }
    else if (c.active > 0) { text = "Downloading"; cls = "text-spotify-green font-bold"; }
    else if (c.queued > 0) { text = "Waiting"; cls = "text-blue-400 font-bold"; }
    qStateEl.innerText = text;
    qStateEl.className = cls;
  }

  cachedQueueJobs = data.jobs || [];
  updateQueueCollectionsDropdown(cachedQueueJobs);

  for (const sid of Array.from(selectedQueueJobIds)) {
    if (!cachedQueueJobs.some((j) => j.id === sid)) selectedQueueJobIds.delete(sid);
  }

  if (currentTab === "queue") applyQueueFiltersAndRender();
  if (currentTab === "dashboard") renderDashboardQueue(cachedQueueJobs);
}

function refreshQueueBadges() {
  const c = lastQueueCounts;
  const waiting = c.queued + (c.active || 0);
  const badge = document.getElementById("badge-queue-count");
  if (badge) { badge.innerText = waiting; badge.classList.toggle("hidden", waiting === 0); }
  const sub = document.getElementById("subnav-queue-count");
  if (sub) { sub.innerText = waiting; sub.classList.toggle("hidden", waiting === 0); }
  FX.setNumber("dash-queued", waiting);
  const retryDash = document.getElementById("btn-q-failed-count");
  if (retryDash) retryDash.innerText = c.failed;
}

// Home: what is running right now, and what needs a human.
function renderDashboardQueue(jobs) {
  const activeList = document.getElementById("dash-active-list");
  const failedList = document.getElementById("dash-failed-list");
  if (!activeList || !failedList) return;

  const active = jobs.filter(isActiveJob).slice(0, 5);
  activeList.innerHTML = active.length ? active.map((j) => `
    <li class="space-y-1">
      <div class="flex items-center justify-between gap-3 text-xs">
        <span class="font-semibold text-fg truncate">${escapeHtml(j.title)} <span class="text-fg3 font-normal">· ${escapeHtml(artistText(j))}</span></span>
        <span class="font-mono text-fg3 shrink-0">${Math.round(j.progress || 0)}%</span>
      </div>
      <div class="h-1.5 rounded-full bg-s4 overflow-hidden" role="progressbar" aria-label="Progress for ${escapeHtml(j.title)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(j.progress || 0)}"><div class="h-full bg-spotify-green rounded-full transition-all duration-300" style="width:${j.progress || 0}%"></div></div>
    </li>`).join("") : (lastQueueCounts.queued > 0
      ? `<li class="text-xs text-fg3 py-3">${lastQueueCounts.queued} waiting${isQueuePaused ? " — the queue is paused" : ""}.</li>`
      : `<li class="text-xs text-fg3 py-3">Nothing is downloading.</li>`);

  const failed = jobs.filter((j) => j.status === "FAILED");
  document.getElementById("dash-retry-all")?.classList.toggle("hidden", failed.length === 0);
  failedList.innerHTML = failed.length ? failed.slice(0, 5).map((j) => `
    <li class="flex items-center justify-between gap-3 text-xs">
      <div class="min-w-0">
        <div class="font-semibold text-fg truncate">${escapeHtml(j.title)} <span class="text-fg3 font-normal">· ${escapeHtml(artistText(j))}</span></div>
        <div class="text-red-400 truncate" title="${escapeHtml(j.error || "")}">${escapeHtml(j.error || "Failed")}</div>
      </div>
      <div class="flex items-center gap-1.5 shrink-0">
        <button type="button" data-on-click="openQueueJobDetailsModal_507735" data-args-click="${jsonAttr([j.id])}" class="px-2.5 py-1 rounded-lg bg-s3 hover:bg-s4 text-fg2 border border-line2">Details</button>
        <button type="button" data-on-click="retryJob_06628e" data-args-click="${jsonAttr([j.id])}" class="px-2.5 py-1 rounded-lg bg-amber-500/15 text-amber-300 border border-amber-500/30 hover:bg-amber-500/25 font-semibold">Retry</button>
      </div>
    </li>`).join("") + (failed.length > 5 ? `<li class="text-xs text-fg3">+${failed.length - 5} more in the queue.</li>` : "")
    : `<li class="text-xs text-fg3 py-3">No failed downloads.</li>`;
}

function updateQueueCollectionsDropdown(jobs) {
  const sel = document.getElementById("q-select-collection");
  if (!sel) return;

  const counts = new Map();
  jobs.forEach((j) => counts.set(collectionOf(j), (counts.get(collectionOf(j)) || 0) + 1));
  const names = Array.from(counts.keys()).sort();
  const signature = names.map((n) => `${n}\u0000${counts.get(n)}`).join("\u0001") + `|${jobs.length}`;
  if (sel.dataset.lastSignature === signature) return;

  const currentVal = sel.value;
  let html = `<option value="ALL">All collections (${jobs.length})</option>`;
  names.forEach((col) => {
    html += `<option value="${escapeHtml(encodeURIComponent(col))}">${escapeHtml(col)} (${counts.get(col)})</option>`;
  });
  sel.innerHTML = html;
  sel.dataset.lastSignature = signature;
  if (currentVal && Array.from(sel.options).some((o) => o.value === currentVal)) sel.value = currentVal;
}

function getFilteredAndSortedQueue() {
  let list = cachedQueueJobs.slice();

  if (queueStatusFilter !== "ALL") {
    list = queueStatusFilter === "ACTIVE" ? list.filter(isActiveJob) : list.filter((j) => j.status === queueStatusFilter);
  }
  if (queueCollectionFilter !== "ALL") {
    const decodedCol = decodeURIComponent(queueCollectionFilter);
    list = list.filter((j) => collectionOf(j) === decodedCol);
  }
  if (queueSearchQuery) {
    const q = queueSearchQuery.toLowerCase().trim();
    list = list.filter((j) => [j.title, artistText(j), j.album, j.collection_name, j.error, j.id].some((v) => (v || "").toLowerCase().includes(q)));
  }

  list.sort((a, b) => {
    let cmp = 0;
    if (queueSortColumn === "title") cmp = (a.title || "").localeCompare(b.title || "");
    else if (queueSortColumn === "artist") cmp = artistText(a).localeCompare(artistText(b));
    else if (queueSortColumn === "album") cmp = (a.collection_name || a.album || "").localeCompare(b.collection_name || b.album || "");
    else if (queueSortColumn === "status") cmp = (a.stage || a.status || "").localeCompare(b.stage || b.status || "");
    else if (queueSortColumn === "progress") cmp = (a.progress || 0) - (b.progress || 0);
    else return 0;
    return queueSortDirection === "desc" ? -cmp : cmp;
  });
  return list;
}

function currentQueuePageItems(filtered) {
  const pageSize = queuePageSize === "ALL" ? filtered.length : (parseInt(queuePageSize, 10) || 50);
  const startIdx = filtered.length === 0 ? 0 : (currentQueuePage - 1) * pageSize;
  const endIdx = queuePageSize === "ALL" ? filtered.length : Math.min(startIdx + pageSize, filtered.length);
  return { items: filtered.slice(startIdx, endIdx), startIdx, endIdx, pageSize };
}

function applyQueueFiltersAndRender() {
  const filtered = getFilteredAndSortedQueue();
  const totalCount = filtered.length;
  let pageSize = queuePageSize === "ALL" ? Math.max(totalCount, 1) : (parseInt(queuePageSize, 10) || 50);

  totalQueuePages = Math.max(1, Math.ceil(totalCount / pageSize));
  currentQueuePage = Math.min(Math.max(currentQueuePage, 1), totalQueuePages);
  const { items, startIdx, endIdx } = currentQueuePageItems(filtered);

  const setText = (id, v) => { const el = document.getElementById(id); if (el) el.innerText = v; };
  setText("q-page-info", totalCount === 0 ? "Showing 0 of 0 items" : `Showing ${startIdx + 1}–${endIdx} of ${totalCount}`);
  setText("q-filter-info", totalCount !== cachedQueueJobs.length ? `(filtered from ${cachedQueueJobs.length} total)` : "");
  setText("q-current-page", currentQueuePage);
  setText("q-total-pages", totalQueuePages);

  const dis = (id, v) => { const el = document.getElementById(id); if (el) el.disabled = v; };
  dis("btn-q-first", currentQueuePage <= 1);
  dis("btn-q-prev", currentQueuePage <= 1);
  dis("btn-q-next", currentQueuePage >= totalQueuePages);
  dis("btn-q-last", currentQueuePage >= totalQueuePages);

  updateBatchBar(items);
  renderMasterQueueTable(items, startIdx);
  updateSortIndicators();
}

function updateSortIndicators() {
  document.querySelectorAll("[data-q-sort]").forEach((th) => {
    const on = th.dataset.qSort === queueSortColumn;
    th.setAttribute("aria-sort", on ? (queueSortDirection === "asc" ? "ascending" : "descending") : "none");
  });
}

function updateBatchBar(visibleItems) {
  const bar = document.getElementById("q-batch-bar");
  const masterChk = document.getElementById("chk-queue-master");
  if (!bar) return;

  const selCount = selectedQueueJobIds.size;
  const setText = (id, v) => { const el = document.getElementById(id); if (el) el.innerText = v; };
  setText("q-selected-count", selCount);
  setText("q-visible-count", visibleItems.length);

  const breakdownEl = document.getElementById("q-selection-breakdown");
  if (breakdownEl) {
    let failed = 0, queued = 0, done = 0;
    for (const sid of selectedQueueJobIds) {
      const j = cachedQueueJobs.find((item) => item.id === sid);
      if (!j) continue;
      if (j.status === "FAILED") failed++; else if (j.status === "QUEUED") queued++; else if (j.status === "COMPLETED") done++;
    }
    const parts = [];
    if (failed) parts.push(`${failed} Failed`);
    if (queued) parts.push(`${queued} Queued`);
    if (done) parts.push(`${done} Done`);
    breakdownEl.innerText = parts.length ? `(${parts.join(", ")})` : "";
  }

  bar.classList.toggle("hidden", selCount === 0);
  if (masterChk) {
    const all = visibleItems.length > 0 && visibleItems.every((j) => selectedQueueJobIds.has(j.id));
    masterChk.checked = all;
    masterChk.indeterminate = !all && visibleItems.some((j) => selectedQueueJobIds.has(j.id));
  }
}

const STAGE_BADGES = {
  QUEUED: ["Queued", "bg-blue-950/60 text-blue-300 border-blue-800/40"],
  FETCHING_METADATA: ["Metadata", "bg-amber-950/60 text-amber-300 border-amber-800/40 animate-pulse"],
  MATCHING_SOURCE: ["Matching", "bg-purple-950/60 text-purple-300 border-purple-800/40 animate-pulse"],
  DOWNLOADING: ["Downloading", "bg-indigo-950/60 text-indigo-300 border-indigo-800/40 animate-pulse"],
  CONVERTING: ["Transcoding", "bg-cyan-950/60 text-cyan-300 border-cyan-800/40 animate-pulse"],
  TAGGING: ["Tagging", "bg-pink-950/60 text-pink-300 border-pink-800/40"],
  VERIFYING: ["Verifying", "bg-teal-950/60 text-teal-300 border-teal-800/40"],
  COMPLETED: ["Completed", "bg-emerald-950/60 text-emerald-300 border-emerald-800/40"],
  SKIPPED: ["Skipped", "bg-gray-800 text-fg2 border-transparent"],
  FAILED: ["Failed", "bg-red-950/60 text-red-300 border-red-800/40"],
  CANCELLED: ["Cancelled", "bg-zinc-800 text-fg3 border-transparent"]
};

function stageBadge(stage) {
  const [label, cls] = STAGE_BADGES[stage] || [stage || "—", "bg-s3 text-fg2 border-transparent"];
  return `<span class="px-2 py-0.5 rounded-full border font-mono text-2xs ${cls}">${escapeHtml(label)}</span>`;
}

// Dragging only makes sense when what you see IS the queue order: default sort, no filters.
function queueDragEnabled() {
  return queueSortColumn === "default" && !queueSearchQuery && queueStatusFilter === "ALL" && queueCollectionFilter === "ALL";
}

function queueRowSignature(job, idx, isSelected) {
  return [idx, isSelected, queueDragEnabled() && job.status === "QUEUED", job.title, artistText(job), collectionOf(job), job.stage, job.status, job.progress, job.speed, job.eta, job.error, job.cover_url].join("\u0001");
}

function queueRowHtml(job, idx, isSelected) {
  const id = jsonAttr([job.id]);
  const artists = artistText(job);
  const cover = job.cover_url
    ? `<img src="${escapeHtml(job.cover_url)}" loading="lazy" decoding="async" alt="" width="32" height="32" class="w-8 h-8 rounded-lg object-cover shrink-0 border border-line2" data-on-error="this_4b0b04" />`
    : `<div class="w-8 h-8 rounded-lg bg-s3 flex items-center justify-center shrink-0 text-fg4"><i data-lucide="music" aria-hidden="true" class="w-4 h-4"></i></div>`;
  const err = job.error
    ? `<div role="button" tabindex="0" data-on-click="openQueueJobDetailsModal_507735" data-args-click="${id}" class="text-2xs text-red-400 mt-1 truncate max-w-[220px] hover:underline cursor-pointer flex items-center" title="${escapeHtml(job.error)}"><i data-lucide="alert-circle" aria-hidden="true" class="w-3 h-3 mr-1 shrink-0"></i>${escapeHtml(job.error)}</div>`
    : "";
  const btn = (key, icon, label, cls = "text-fg3 hover:text-fg") =>
    `<button type="button" data-on-click="${key}" data-args-click="${id}" title="${label}" aria-label="${label}: ${escapeHtml(job.title)}" class="p-1.5 rounded-md ${cls}"><i data-lucide="${icon}" aria-hidden="true" class="w-3.5 h-3.5"></i></button>`;
  const progress = Math.round(job.progress || 0);

  return `
    <td class="py-3 px-3 text-center" data-on-click="event_d74500">
      <input type="checkbox" aria-label="Select ${escapeHtml(job.title)}" data-on-click="handleQueueCheckboxClick_e6f254" data-args-click="${id}" ${isSelected ? "checked" : ""} class="accent-spotify-green cursor-pointer" />
    </td>
    <td class="py-3 px-3 font-mono text-fg4">${idx}</td>
    <td class="py-3 px-4">
      <div class="flex items-center space-x-3">
        ${cover}
        <div class="truncate max-w-[240px]">
          <div class="font-bold text-fg truncate" title="${escapeHtml(job.title)}">${escapeHtml(job.title)}</div>
          <div class="text-2xs text-fg3 truncate" title="${escapeHtml(artists)}">${escapeHtml(artists)}</div>
        </div>
      </div>
    </td>
    <td class="py-3 px-4 text-fg3 truncate max-w-[160px]" title="${escapeHtml(collectionOf(job))}">${escapeHtml(collectionOf(job))}</td>
    <td class="py-3 px-4">${stageBadge(job.stage)}${err}</td>
    <td class="py-3 px-4">
      <div class="flex items-center space-x-2">
        <div class="flex-1 bg-s4 h-1.5 rounded-full overflow-hidden" role="progressbar" aria-label="Progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><div class="bg-spotify-green h-full rounded-full transition-all duration-300" style="width:${progress}%"></div></div>
        <span class="font-mono text-2xs text-fg3 w-9 text-right">${progress}%</span>
      </div>
    </td>
    <td class="py-3 px-4 font-mono text-fg3 text-2xs"><div>${escapeHtml(job.speed || "--")}</div><div class="text-fg4">${escapeHtml(job.eta || "--")}</div></td>
    <td class="py-3 px-4 text-center">
      <div class="flex items-center justify-center space-x-0.5">
        ${job.status === "QUEUED" ? btn("reorderJob_f1c63a", "arrow-up", "Move up") + btn("reorderJob_0de448", "arrow-down", "Move down") : ""}
        ${job.status === "FAILED" || job.status === "CANCELLED" ? btn("retryJob_06628e", "rotate-cw", "Retry", "text-amber-400 hover:text-amber-300") : ""}
        ${btn("openEditQueueJobModal_6f708d", "edit-3", "Edit metadata", "text-fg3 hover:text-blue-400")}
        ${btn("openQueueJobDetailsModal_507735", "info", "Details")}
        ${btn("removeJob_0cb4f8", "trash-2", "Remove", "text-fg4 hover:text-red-400")}
      </div>
    </td>`;
}

// Keyed diff: only rows whose visible data changed are rebuilt, so hover, focus and scroll survive polling.
function renderMasterQueueTable(jobs, startIdx = 0) {
  const tbody = document.getElementById("master-queue-body");
  if (!tbody) return;

  if (!jobs || jobs.length === 0) {
    const isFiltered = queueSearchQuery || queueStatusFilter !== "ALL" || queueCollectionFilter !== "ALL";
    tbody.innerHTML = `
      <tr>
        <td colspan="8" class="py-12 text-center text-fg3">
          <i data-lucide="${isFiltered ? "search-x" : "inbox"}" aria-hidden="true" class="w-8 h-8 mx-auto mb-2 opacity-40"></i>
          <div>${isFiltered ? "No queue jobs match your search or filters." : "Your download queue is empty."}</div>
          ${isFiltered
            ? '<button type="button" data-on-click="resetAllQueueFilters_33fc4d" class="mt-2 text-xs text-spotify-green hover:underline cursor-pointer">Reset filters</button>'
            : '<button type="button" data-on-click="goTab" data-args-click=\'["fetch"]\' class="mt-2 text-xs text-spotify-green hover:underline cursor-pointer">Add music</button>'}
        </td>
      </tr>`;
    UI.icons(tbody);
    return;
  }

  const existing = new Map();
  Array.from(tbody.children).forEach((tr) => {
    if (tr.dataset.jobId) existing.set(tr.dataset.jobId, tr); else tr.remove();
  });

  let cursor = tbody.firstElementChild;
  jobs.forEach((job, i) => {
    const idx = startIdx + i + 1;
    const selected = selectedQueueJobIds.has(job.id);
    const sig = queueRowSignature(job, idx, selected);
    let tr = existing.get(job.id);
    if (tr && tr.dataset.sig === sig) {
      existing.delete(job.id);
    } else {
      const fresh = document.createElement("tr");
      fresh.dataset.jobId = job.id;
      fresh.dataset.sig = sig;
      const draggable = queueDragEnabled() && job.status === "QUEUED";
      fresh.className = `hover:bg-hv transition-colors ${selected ? "bg-emerald-950/20" : ""} ${draggable ? "cursor-grab" : ""}`;
      if (draggable) { fresh.draggable = true; fresh.title = "Drag to change the download order"; }
      fresh.innerHTML = queueRowHtml(job, idx, selected);
      if (tr) { tr.replaceWith(fresh); existing.delete(job.id); if (cursor === tr) cursor = fresh; }
      tr = fresh;
      UI.icons(tr);
    }
    if (cursor === tr) cursor = cursor.nextElementSibling;
    else tbody.insertBefore(tr, cursor);
  });
  existing.forEach((tr) => tr.remove());
}

// ================= Filtering, sorting, paging =================
function handleQueueFilterChange() {
  const searchInput = document.getElementById("q-search-input");
  const clearBtn = document.getElementById("q-search-clear");
  queueSearchQuery = searchInput ? searchInput.value : "";
  if (clearBtn) clearBtn.classList.toggle("hidden", !queueSearchQuery);

  const statusSel = document.getElementById("q-select-status");
  if (statusSel) queueStatusFilter = statusSel.value;
  const colSel = document.getElementById("q-select-collection");
  if (colSel) queueCollectionFilter = colSel.value;

  const sortSel = document.getElementById("q-select-sort");
  if (sortSel) {
    if (sortSel.value === "default") { queueSortColumn = "default"; queueSortDirection = "asc"; }
    else { const parts = sortSel.value.split("_"); queueSortColumn = parts[0]; queueSortDirection = parts[1] || "asc"; }
  }
  updateStatusFilterPills();
  currentQueuePage = 1;
  applyQueueFiltersAndRender();
}

function clearQueueSearch() {
  const input = document.getElementById("q-search-input");
  if (input) input.value = "";
  handleQueueFilterChange();
}

function setQueueStatusFilter(status) {
  queueStatusFilter = status;
  const sel = document.getElementById("q-select-status");
  if (sel) sel.value = status;
  updateStatusFilterPills();
  currentQueuePage = 1;
  applyQueueFiltersAndRender();
}

function updateStatusFilterPills() {
  const pills = { ALL: "q-pill-all", QUEUED: "q-pill-queued", ACTIVE: "q-pill-active", COMPLETED: "q-pill-completed", FAILED: "q-pill-failed" };
  Object.entries(pills).forEach(([k, id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    const on = k === queueStatusFilter;
    el.classList.toggle("ring-2", on);
    el.classList.toggle("ring-spotify-green", on);
    el.classList.toggle("font-bold", on);
    el.setAttribute("aria-pressed", String(on));
  });
}

function resetAllQueueFilters() {
  const searchInput = document.getElementById("q-search-input");
  if (searchInput) searchInput.value = "";
  const global = document.getElementById("global-search-input");
  if (global) global.value = "";
  queueSearchQuery = ""; queueStatusFilter = "ALL"; queueCollectionFilter = "ALL";
  queueSortColumn = "default"; queueSortDirection = "asc";
  ["q-select-status", "q-select-collection"].forEach((id) => { const el = document.getElementById(id); if (el) el.value = "ALL"; });
  const sortSel = document.getElementById("q-select-sort");
  if (sortSel) sortSel.value = "default";
  updateStatusFilterPills();
  currentQueuePage = 1;
  applyQueueFiltersAndRender();
}

function handleQueuePageSizeChange() {
  const sel = document.getElementById("q-select-pagesize");
  if (sel) queuePageSize = sel.value;
  currentQueuePage = 1;
  applyQueueFiltersAndRender();
}

function changeQueuePage(page) {
  if (page < 1 || page > totalQueuePages) return;
  currentQueuePage = page;
  applyQueueFiltersAndRender();
}

function toggleSortColumn(col) {
  if (queueSortColumn === col) queueSortDirection = queueSortDirection === "asc" ? "desc" : "asc";
  else { queueSortColumn = col; queueSortDirection = "asc"; }
  const sortSel = document.getElementById("q-select-sort");
  if (sortSel) sortSel.value = col === "default" ? "default" : `${col}_${queueSortDirection}`;
  applyQueueFiltersAndRender();
}

// ================= Selection =================
function handleQueueCheckboxClick(jobId, event) {
  event.stopPropagation();
  const checked = event.target.checked;
  const filtered = getFilteredAndSortedQueue();

  if (event.shiftKey && lastSelectedQueueJobId) {
    const fromIdx = filtered.findIndex((j) => j.id === lastSelectedQueueJobId);
    const toIdx = filtered.findIndex((j) => j.id === jobId);
    if (fromIdx !== -1 && toIdx !== -1) {
      for (let i = Math.min(fromIdx, toIdx); i <= Math.max(fromIdx, toIdx); i++) {
        if (checked) selectedQueueJobIds.add(filtered[i].id); else selectedQueueJobIds.delete(filtered[i].id);
      }
      lastSelectedQueueJobId = jobId;
      applyQueueFiltersAndRender();
      return;
    }
  }
  if (checked) selectedQueueJobIds.add(jobId); else selectedQueueJobIds.delete(jobId);
  lastSelectedQueueJobId = jobId;
  applyQueueFiltersAndRender();
}

function toggleMasterSelectMenu(event) {
  if (event) event.stopPropagation();
  const menu = document.getElementById("q-select-preset-menu");
  if (menu) menu.classList.toggle("hidden");
}

function closeMasterSelectMenu() {
  const menu = document.getElementById("q-select-preset-menu");
  if (menu) menu.classList.add("hidden");
}

document.addEventListener("click", () => { closeMasterSelectMenu(); closeQueueExportMenu(); });

function selectQueuePreset(preset) {
  closeMasterSelectMenu();
  const filtered = getFilteredAndSortedQueue();
  const { items } = currentQueuePageItems(filtered);
  const add = (list) => list.forEach((j) => selectedQueueJobIds.add(j.id));

  if (preset === "page") add(items);
  else if (preset === "all_filtered") add(filtered);
  else if (preset === "failed") add(filtered.filter((j) => j.status === "FAILED"));
  else if (preset === "queued") add(filtered.filter((j) => j.status === "QUEUED"));
  else if (preset === "invert") items.forEach((j) => { if (selectedQueueJobIds.has(j.id)) selectedQueueJobIds.delete(j.id); else selectedQueueJobIds.add(j.id); });
  applyQueueFiltersAndRender();
}

function toggleQueueItemSelection(jobId, checked) {
  if (checked) selectedQueueJobIds.add(jobId); else selectedQueueJobIds.delete(jobId);
  applyQueueFiltersAndRender();
}

function toggleSelectAllCurrentPage(checked) {
  const { items } = currentQueuePageItems(getFilteredAndSortedQueue());
  items.forEach((j) => { if (checked) selectedQueueJobIds.add(j.id); else selectedQueueJobIds.delete(j.id); });
  applyQueueFiltersAndRender();
}

function toggleSelectAllQueueFiltered(checked) {
  getFilteredAndSortedQueue().forEach((j) => { if (checked !== false) selectedQueueJobIds.add(j.id); else selectedQueueJobIds.delete(j.id); });
  applyQueueFiltersAndRender();
}

function deselectAllQueueItems() {
  selectedQueueJobIds.clear();
  applyQueueFiltersAndRender();
}

// ================= Batch actions =================
function openBatchEditModal() {
  if (selectedQueueJobIds.size === 0) { UI.toast("Select some items to edit first.", "info"); return; }
  document.getElementById("batch-edit-count").innerText = selectedQueueJobIds.size;
  document.getElementById("batch-q-collection").value = "";
  document.getElementById("batch-q-album").value = "";
  document.getElementById("batch-q-requeue").checked = true;
  document.getElementById("modal-batch-edit-queue").classList.remove("hidden");
}

function closeBatchEditModal() {
  document.getElementById("modal-batch-edit-queue").classList.add("hidden");
}

async function applyBatchEditModal() {
  const col = document.getElementById("batch-q-collection").value.trim();
  const alb = document.getElementById("batch-q-album").value.trim();
  const req = document.getElementById("batch-q-requeue").checked;
  const updates = {};
  if (col) updates.collection_name = col;
  if (alb) updates.album = alb;
  if (req) updates.requeue = true;
  if (!col && !alb && !req) { UI.toast("No changes specified.", "info"); return; }

  try {
    const data = await UI.api("/api/queue/batch-action", { body: { action: "update", job_ids: Array.from(selectedQueueJobIds), updates }, prefix: "Batch update failed" });
    UI.toast(`Updated ${data.count} item${data.count === 1 ? "" : "s"}.`, "success");
    closeBatchEditModal();
    fetchQueue();
  } catch (_) { /* reported */ }
}

// CSV cells that start with = + - @ would run as formulas when opened in Excel.
function csvCell(v) {
  let s = String(v == null ? "" : v);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return `"${s.replace(/"/g, '""')}"`;
}

function downloadBlob(filename, mime, text) {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function exportSelectedQueue(format = "csv") {
  if (selectedQueueJobIds.size === 0) { UI.toast("Select items to export first.", "info"); return; }
  const list = cachedQueueJobs.filter((j) => selectedQueueJobIds.has(j.id));
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  if (format === "json") {
    downloadBlob(`queue_selected_${stamp}.json`, "application/json", JSON.stringify(list, null, 2));
  } else {
    const rows = [["#", "ID", "Title", "Artists", "Album", "Collection", "Status", "Stage", "Progress", "Error"].map(csvCell).join(",")];
    list.forEach((j, i) => rows.push([i + 1, j.id, j.title, artistText(j), j.album, j.collection_name, j.status, j.stage, `${j.progress}%`, j.error].map(csvCell).join(",")));
    downloadBlob(`queue_selected_${stamp}.csv`, "text/csv;charset=utf-8", "﻿" + rows.join("\r\n"));
  }
  UI.toast(`Exported ${list.length} item${list.length === 1 ? "" : "s"}.`, "success");
}

function trackFromJob(j) {
  return {
    id: j.track_id, title: j.title, artists: Array.isArray(j.artists) ? j.artists : [j.artists].filter(Boolean),
    album: j.album, duration_ms: j.duration_ms, cover_url: j.cover_url, isrc: j.isrc, year: j.year,
    provider: j.provider, provider_item_id: j.provider_item_id, canonical_url: j.canonical_url,
    is_alternative: j.is_alternative, original_spotify_id: j.original_spotify_id, capabilities: j.capabilities,
    collection_id: j.collection_id, collection_name: j.collection_name
  };
}

// Removing from the queue is undoable: the tracks are re-queued (at the end) from the snapshot we kept.
function offerQueueUndo(jobs, verb) {
  const tracks = jobs.map(trackFromJob);
  UI.toast(`${verb} ${jobs.length} item${jobs.length === 1 ? "" : "s"}.`, "info", undefined, {
    action: {
      label: "Undo",
      onClick: async () => {
        const res = await UI.api("/api/queue/add", { body: { tracks }, prefix: "Undo failed" });
        UI.toast(`Restored ${res.added} item${res.added === 1 ? "" : "s"} to the queue.`, "success");
        fetchQueue();
      }
    }
  });
}

async function batchQueueAction(action) {
  const ids = Array.from(selectedQueueJobIds);
  if (ids.length === 0) return;
  const snapshot = cachedQueueJobs.filter((j) => selectedQueueJobIds.has(j.id));

  if (action === "remove" || action === "cancel") {
    const ok = await UI.confirm({
      title: action === "remove" ? `Remove ${ids.length} item${ids.length === 1 ? "" : "s"}?` : `Cancel ${ids.length} item${ids.length === 1 ? "" : "s"}?`,
      message: action === "remove" ? "They leave the queue. Downloaded files are not deleted. You can undo this right after." : "Queued items stop; running downloads finish their current step.",
      confirmText: action === "remove" ? "Remove" : "Cancel items", cancelText: "Keep", tone: "danger"
    });
    if (!ok) return;
  }

  try {
    const data = await UI.api("/api/queue/batch-action", { body: { action, job_ids: ids }, prefix: "Batch action failed" });
    deselectAllQueueItems();
    if (action === "remove") offerQueueUndo(snapshot, "Removed");
    else UI.toast(`${action.replace("_", " ")}: ${data.count} item${data.count === 1 ? "" : "s"}.`, "success");
    fetchQueue();
  } catch (_) { /* reported */ }
}

async function retryAllFailedJobs() {
  const failedCount = cachedQueueJobs.filter((j) => j.status === "FAILED").length;
  if (failedCount === 0) { UI.toast("There are no failed downloads to retry.", "info"); return; }
  const ok = await UI.confirm({ title: `Retry ${failedCount} failed download${failedCount === 1 ? "" : "s"}?`, message: "They go back to the end of the queue.", confirmText: "Retry all" });
  if (!ok) return;
  try {
    const data = await UI.api("/api/queue/retry-all-failed", { method: "POST", prefix: "Retry failed" });
    UI.toast(`Re-queued ${data.count} failed download${data.count === 1 ? "" : "s"}.`, "success");
    fetchQueue();
  } catch (_) { /* reported */ }
}

async function clearFailedQueue() {
  const failed = cachedQueueJobs.filter((j) => j.status === "FAILED");
  if (failed.length === 0) { UI.toast("There are no failed items in the queue.", "info"); return; }
  const ok = await UI.confirm({ title: `Clear ${failed.length} failed item${failed.length === 1 ? "" : "s"}?`, message: "They are removed from the queue list. You can undo this right after.", confirmText: "Clear failed", tone: "danger" });
  if (!ok) return;
  try {
    const data = await UI.api("/api/queue/clear-failed", { method: "POST", prefix: "Could not clear failed items" });
    offerQueueUndo(failed, "Cleared");
    fetchQueue();
    return data;
  } catch (_) { /* reported */ }
}

async function clearFinishedQueue() {
  const done = cachedQueueJobs.filter((j) => ["COMPLETED", "SKIPPED"].includes(j.status));
  if (done.length === 0) { UI.toast("Nothing finished to clear yet.", "info"); return; }
  try {
    await UI.api("/api/queue/clear-finished", { method: "POST", prefix: "Could not clear finished items" });
    UI.toast(`Cleared ${done.length} finished item${done.length === 1 ? "" : "s"} from the list. Your files are untouched.`, "success");
    fetchQueue();
  } catch (_) { /* reported */ }
}

function toggleQueueExportMenu() {
  const menu = document.getElementById("q-export-menu");
  if (menu) menu.classList.toggle("hidden");
}

function closeQueueExportMenu() {
  const menu = document.getElementById("q-export-menu");
  if (menu) menu.classList.add("hidden");
}

// ================= Single item: edit =================
function openEditQueueJobModal(jobId) {
  const job = cachedQueueJobs.find((j) => j.id === jobId);
  if (!job) return;
  document.getElementById("edit-q-job-id").value = job.id;
  document.getElementById("edit-q-title").value = job.title || "";
  document.getElementById("edit-q-artists").value = Array.isArray(job.artists) ? job.artists.join(", ") : (job.artists || "");
  document.getElementById("edit-q-album").value = job.album || "";
  document.getElementById("edit-q-collection").value = job.collection_name || "";
  document.getElementById("edit-q-requeue").checked = (job.status === "FAILED" || job.status === "CANCELLED");
  document.getElementById("modal-edit-queue-job").classList.remove("hidden");
}

function closeEditQueueJobModal() {
  document.getElementById("modal-edit-queue-job").classList.add("hidden");
}

async function saveEditQueueJob() {
  const artistsStr = document.getElementById("edit-q-artists").value.trim();
  try {
    await UI.api("/api/queue/update-job", {
      body: {
        job_id: document.getElementById("edit-q-job-id").value,
        title: document.getElementById("edit-q-title").value.trim(),
        artists: artistsStr ? artistsStr.split(",").map((a) => a.trim()).filter(Boolean) : [],
        album: document.getElementById("edit-q-album").value.trim(),
        collection_name: document.getElementById("edit-q-collection").value.trim(),
        retry: document.getElementById("edit-q-requeue").checked
      },
      prefix: "Could not update the item"
    });
    UI.toast("Item updated.", "success");
    closeEditQueueJobModal();
    fetchQueue();
  } catch (_) { /* reported */ }
}

// ================= Single item: details =================
function openQueueJobDetailsModal(jobId) {
  const job = cachedQueueJobs.find((j) => j.id === jobId);
  if (!job) return;
  activeInspectingJobId = jobId;

  const set = (id, v) => { document.getElementById(id).innerText = v; };
  set("detail-job-title", job.title || "Untitled");
  set("detail-job-artists", artistText(job));
  set("detail-job-id", job.id);
  set("detail-job-track-id", job.track_id);
  set("detail-job-collection", job.collection_name || "Downloads");
  set("detail-job-album", job.album || "None");
  set("detail-job-status", `${job.stage} (${job.status})`);
  set("detail-job-source", job.match_source || "None");

  const avatar = document.getElementById("detail-job-avatar");
  avatar.innerHTML = job.cover_url
    ? `<img src="${escapeHtml(job.cover_url)}" alt="" class="w-full h-full object-cover" data-on-error="this_c25d64" />`
    : `<i data-lucide="music" aria-hidden="true" class="w-6 h-6 text-fg4"></i>`;

  const errBox = document.getElementById("detail-job-error-box");
  document.getElementById("detail-job-error-text").innerText = job.error || "";
  errBox.classList.toggle("hidden", !job.error);
  document.getElementById("btn-detail-retry").classList.toggle("hidden", !(job.status === "FAILED" || job.status === "CANCELLED"));

  document.getElementById("modal-queue-job-details").classList.remove("hidden");
  UI.icons(avatar);
}

function closeQueueJobDetailsModal() {
  document.getElementById("modal-queue-job-details").classList.add("hidden");
  activeInspectingJobId = null;
}

async function copyQueueJobError() {
  const text = document.getElementById("detail-job-error-text").innerText;
  if (text) UI.toast((await UI.copy(text)) ? "Error details copied." : "Could not copy to the clipboard.", "info", 1800);
}

function openEditModalFromDetails() {
  if (!activeInspectingJobId) return;
  const id = activeInspectingJobId;
  closeQueueJobDetailsModal();
  openEditQueueJobModal(id);
}

async function retryJobFromDetails() {
  if (!activeInspectingJobId) return;
  await retryJob(activeInspectingJobId);
  closeQueueJobDetailsModal();
}

// ================= Queue-wide controls =================
async function togglePauseQueue() {
  const action = isQueuePaused ? "resume" : "pause";
  try {
    await UI.api(`/api/queue/${action}`, { method: "POST", prefix: `Could not ${action} the queue` });
    UI.toast(action === "pause" ? "Queue paused. Running tracks finish first." : "Queue resumed.", "info", 2200);
    fetchQueue();
  } catch (_) { /* reported */ }
}

async function cancelQueue() {
  const n = lastQueueCounts.queued;
  if (n === 0) { UI.toast("No waiting items to cancel.", "info"); return; }
  const ok = await UI.confirm({ title: `Cancel ${n} waiting item${n === 1 ? "" : "s"}?`, message: "Running downloads finish their current step. Cancelled items can be retried.", confirmText: "Cancel waiting items", cancelText: "Keep", tone: "danger" });
  if (!ok) return;
  try {
    await UI.api("/api/queue/cancel-all", { method: "POST", prefix: "Could not cancel the queue" });
    UI.toast(`Cancelled ${n} waiting item${n === 1 ? "" : "s"}.`, "success");
    fetchQueue();
  } catch (_) { /* reported */ }
}

async function retryJob(jobId) {
  try {
    await UI.api("/api/queue/retry", { body: { job_id: jobId }, prefix: "Retry failed" });
    UI.toast("Re-queued.", "success", 1800);
    fetchQueue();
  } catch (_) { /* reported */ }
}

async function removeJob(jobId) {
  const job = cachedQueueJobs.find((j) => j.id === jobId);
  try {
    await UI.api("/api/queue/remove", { body: { job_id: jobId }, prefix: "Could not remove the item" });
    if (job) offerQueueUndo([job], "Removed");
    fetchQueue();
  } catch (_) { /* reported */ }
}

async function reorderJob(jobId, direction) {
  try {
    await UI.api("/api/queue/reorder", { body: { job_id: jobId, direction }, prefix: "Could not move the item" });
    fetchQueue();
  } catch (_) { /* reported */ }
}

// ================= Drag & drop reordering =================
let draggedJobId = null;

function clearDropMarkers() {
  document.querySelectorAll("#master-queue-body .drop-before, #master-queue-body .drop-after").forEach((tr) => tr.classList.remove("drop-before", "drop-after"));
}

function initQueueDragAndDrop() {
  const tbody = document.getElementById("master-queue-body");
  if (!tbody || tbody.dataset.dnd) return;
  tbody.dataset.dnd = "1";

  tbody.addEventListener("dragstart", (e) => {
    const tr = e.target.closest && e.target.closest("tr[data-job-id]");
    if (!tr || !tr.draggable) return;
    draggedJobId = tr.dataset.jobId;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", draggedJobId);
    tr.classList.add("opacity-50");
  });
  tbody.addEventListener("dragover", (e) => {
    if (!draggedJobId) return;
    const tr = e.target.closest("tr[data-job-id]");
    if (!tr || tr.dataset.jobId === draggedJobId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    clearDropMarkers();
    const r = tr.getBoundingClientRect();
    tr.classList.add(e.clientY > r.top + r.height / 2 ? "drop-after" : "drop-before");
  });
  tbody.addEventListener("dragleave", (e) => { if (!tbody.contains(e.relatedTarget)) clearDropMarkers(); });
  tbody.addEventListener("dragend", () => { draggedJobId = null; clearDropMarkers(); tbody.querySelectorAll(".opacity-50").forEach((t) => t.classList.remove("opacity-50")); });
  tbody.addEventListener("drop", async (e) => {
    const tr = e.target.closest("tr[data-job-id]");
    if (!draggedJobId || !tr) return;
    e.preventDefault();
    const after = tr.classList.contains("drop-after");
    const before = after ? (tr.nextElementSibling && tr.nextElementSibling.dataset.jobId) || null : tr.dataset.jobId;
    const id = draggedJobId;
    draggedJobId = null;
    clearDropMarkers();
    try {
      const res = await UI.api("/api/queue/reorder", { body: { job_id: id, before_id: before }, prefix: "Could not move the item" });
      if (res.moved === false) UI.toast("Only waiting items can be reordered.", "info", 2000);
      queueEtag = "";
      fetchQueue();
    } catch (_) { /* reported */ }
  });
}

document.addEventListener("DOMContentLoaded", initQueueDragAndDrop);
