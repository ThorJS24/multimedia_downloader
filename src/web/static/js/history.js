// Download history: every finished download (even after the queue was cleared), searchable, with re-download.
"use strict";

const HISTORY_PAGE = 50;
let historyState = { q: "", status: "", offset: 0, total: 0, loading: false, timer: 0, abort: null };

const HISTORY_STATUS = {
  COMPLETED: { label: "Done", cls: "bg-emerald-950/60 text-emerald-300 border-emerald-800/40" },
  FAILED: { label: "Failed", cls: "bg-red-950/60 text-red-300 border-red-800/40" },
  CANCELLED: { label: "Cancelled", cls: "bg-s3 text-fg3 border-line2" },
  SKIPPED: { label: "Skipped", cls: "bg-s3 text-fg3 border-line2" }
};

function historyRowHtml(item) {
  const st = HISTORY_STATUS[item.status] || { label: item.status, cls: "bg-s3 text-fg3 border-line2" };
  const cover = item.cover_url
    ? `<img src="${escapeHtml(item.cover_url)}" loading="lazy" decoding="async" alt="" width="40" height="40" class="w-10 h-10 rounded-lg object-cover shrink-0 border border-line2" data-on-error="this_4b0b04" />`
    : `<div class="w-10 h-10 rounded-lg bg-s3 flex items-center justify-center shrink-0 text-fg4"><i data-lucide="music" aria-hidden="true" class="w-4 h-4"></i></div>`;
  const when = item.finished_at ? new Date(item.finished_at * 1000).toLocaleString() : "";
  const hint = item.error ? explainError(item.error) : null;
  const detail = [item.artists, item.album].filter(Boolean).join(" · ");
  const failure = item.error
    ? `<div class="text-2xs text-red-400 mt-0.5 truncate" title="${escapeHtml(item.error)}">${escapeHtml(hint ? hint.short : item.error)}</div>`
    : (item.status === "COMPLETED" && !item.file_exists ? `<div class="text-2xs text-amber-400 mt-0.5">File not found. It may have been moved or deleted.</div>` : "");
  const show = item.file_exists
    ? `<button type="button" data-on-click="historyShow" data-args-click="${jsonAttr([item.output_path])}" class="px-2.5 py-1.5 rounded-lg bg-s3 hover:bg-s4 border border-line2 text-2xs font-semibold text-fg2">Show in folder</button>`
    : "";
  return `<li class="flex items-center gap-3 px-3 py-2.5 rounded-xl bg-s1 border border-line">
    ${cover}
    <div class="min-w-0 flex-1">
      <div class="text-sm font-semibold text-fg truncate" title="${escapeHtml(item.title)}">${escapeHtml(item.title || "Untitled")}</div>
      <div class="text-xs text-fg3 truncate" title="${escapeHtml(detail)}">${escapeHtml(detail)}</div>
      ${failure}
    </div>
    <div class="max-md:hidden text-2xs text-fg4 font-mono shrink-0 text-right">${escapeHtml(when)}${item.collection_name ? `<div class="text-fg3 truncate max-w-[160px]" title="${escapeHtml(item.collection_name)}">${escapeHtml(item.collection_name)}</div>` : ""}</div>
    <span class="px-2 py-0.5 rounded-full border text-2xs font-semibold shrink-0 ${st.cls}">${escapeHtml(st.label)}</span>
    <div class="flex items-center gap-1.5 shrink-0">
      ${show}
      <button type="button" data-on-click="historyRedownload" data-args-click="${jsonAttr([item.id])}" class="px-2.5 py-1.5 rounded-lg bg-s3 hover:bg-s4 border border-line2 text-2xs font-semibold text-fg">Download again</button>
    </div>
  </li>`;
}

async function loadHistory(reset = true) {
  const list = document.getElementById("history-list");
  if (!list) return;
  const s = historyState;
  if (reset) { s.offset = 0; if (s.abort) s.abort.abort(); }
  else if (s.loading) return;
  s.loading = true;
  s.abort = new AbortController();
  const params = new URLSearchParams({ limit: HISTORY_PAGE, offset: s.offset });
  if (s.q) params.set("q", s.q);
  if (s.status) params.set("status", s.status);
  if (reset && !list.children.length) list.innerHTML = `<li class="text-xs text-fg3 py-6 text-center">Loading history…</li>`;
  try {
    const res = await UI.api(`/api/history?${params}`, { quiet: true, signal: s.abort.signal });
    s.total = res.total;
    const html = res.items.map(historyRowHtml).join("");
    if (reset) {
      list.innerHTML = html || `<li class="text-xs text-fg3 py-8 text-center">${s.q || s.status ? "Nothing in the history matches that." : "Nothing has been downloaded yet. Finished downloads will appear here."}</li>`;
    } else {
      list.insertAdjacentHTML("beforeend", html);
    }
    s.offset += res.items.length;
    const count = document.getElementById("history-count");
    if (count) count.textContent = `${res.total} download${res.total === 1 ? "" : "s"}${s.q || s.status ? " match" : ""}`;
    const more = document.getElementById("btn-history-more");
    if (more) more.classList.toggle("hidden", s.offset >= res.total);
    UI.icons();
  } catch (err) {
    if (err && err.name === "AbortError") return;
    list.innerHTML = `<li class="text-xs text-red-400 py-6 text-center">Couldn't load the history. <button type="button" data-on-click="historyReload" class="underline">Try again</button></li>`;
  } finally {
    s.loading = false;
  }
}

function historySearchInput() {
  clearTimeout(historyState.timer);
  historyState.timer = setTimeout(() => {
    historyState.q = document.getElementById("history-search").value.trim();
    loadHistory(true);
  }, 250);
}

function historyStatusChanged() {
  historyState.status = document.getElementById("history-status").value;
  loadHistory(true);
}

async function historyRedownload(jobId) {
  try {
    await UI.api("/api/history/redownload", { body: { job_id: jobId }, prefix: "Couldn't queue it again" });
    UI.toast("Added to the queue.", "success", 6000, { action: { label: "Open queue", onClick: () => switchTab("queue") } });
    if (typeof fetchQueue === "function") fetchQueue();
  } catch (_) { /* reported */ }
}

function historyShow(path) {
  UI.api("/api/system/reveal", { body: { path }, prefix: "Couldn't open the folder" }).catch(() => {});
}
