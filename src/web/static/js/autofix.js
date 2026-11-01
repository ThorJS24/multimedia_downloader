// Fix tags & artwork automatically (Library health): look tracks up online, review the suggestions, apply the ones you tick.
"use strict";

let autofixPoll = null;
let autofixItems = [];

function autofixSetRunning(running) {
  document.getElementById("btn-autofix-start").classList.toggle("hidden", running);
  document.getElementById("btn-autofix-cancel").classList.toggle("hidden", !running);
  document.getElementById("autofix-filter").disabled = running;
  document.getElementById("autofix-limit").disabled = running;
}

async function autofixStart() {
  try {
    await UI.api("/api/autofix/start", {
      body: { filter: document.getElementById("autofix-filter").value, limit: Number(document.getElementById("autofix-limit").value) },
      prefix: "Couldn't start the lookup"
    });
  } catch (_) { return; }
  autofixItems = [];
  document.getElementById("autofix-results").classList.add("hidden");
  document.getElementById("autofix-status").classList.remove("hidden");
  autofixSetRunning(true);
  if (autofixPoll) autofixPoll.stop();
  autofixPoll = UI.poll(autofixRefresh, { visibleMs: 1200, hiddenMs: 5000 });
}

async function autofixCancel() {
  try { await UI.api("/api/autofix/cancel", { method: "POST", quiet: true }); } catch (_) { /* the next poll shows the real state */ }
}

function autofixSummaryText(st) {
  const c = st.counts || {};
  const bits = [];
  if (c.no_match) bits.push(`${c.no_match} with no confident match`);
  if (c.already_ok) bits.push(`${c.already_ok} already correct`);
  if (c.no_title) bits.push(`${c.no_title} with nothing to search for`);
  if (c.unreadable) bits.push(`${c.unreadable} unreadable`);
  return bits.join(" · ");
}

async function autofixRefresh() {
  let st;
  try { st = await UI.api("/api/autofix/status", { quiet: true }); } catch (_) { return; }
  const status = document.getElementById("autofix-status");
  const pct = st.total ? Math.round((st.done / st.total) * 100) : 0;
  status.innerHTML = `
    <div class="flex items-center justify-between text-xs text-fg2"><span role="status">${st.running ? "Looking up tracks…" : (st.cancelled ? "Stopped." : "Finished.")} ${st.done} of ${st.total} checked · ${st.items.length} with suggestions</span><span class="font-mono">${pct}%</span></div>
    <div class="h-1.5 rounded-full bg-s3 overflow-hidden" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-label="Lookup progress"><div class="h-full bg-spotify-green transition-all" style="width:${pct}%"></div></div>
    ${st.error ? `<p role="alert" class="text-xs text-red-400">${escapeHtml(st.error)}</p>` : ""}
    ${!st.running ? `<p class="text-2xs text-fg3">${escapeHtml(autofixSummaryText(st))}</p>` : ""}`;
  if (!st.running) {
    if (autofixPoll) { autofixPoll.stop(); autofixPoll = null; }
    autofixSetRunning(false);
    autofixItems = st.items;
    renderAutofixResults();
  }
}

function autofixFieldRow(item, f) {
  return `<label class="flex items-start gap-2 text-xs cursor-pointer">
    <input type="checkbox" class="autofix-opt mt-0.5 accent-spotify-green" data-path="${escapeHtml(item.path)}" data-field="${escapeHtml(f.field)}" ${f.selected ? "checked" : ""} data-on-change="autofixCount" />
    <span class="min-w-0"><span class="text-fg3">${escapeHtml(f.label)}:</span>
      ${f.current ? `<span class="text-fg4 line-through">${escapeHtml(f.current)}</span> → ` : ""}<span class="text-fg">${escapeHtml(f.proposed)}</span>
      <span class="ml-1 px-1.5 py-0.5 rounded text-2xs font-semibold ${f.fills_gap ? "bg-emerald-950/60 text-emerald-300" : "bg-amber-950/60 text-amber-300"}">${f.fills_gap ? "fills a gap" : "replaces what you have"}</span></span>
  </label>`;
}

function renderAutofixResults() {
  const box = document.getElementById("autofix-results");
  if (!autofixItems.length) {
    box.innerHTML = `<p class="text-xs text-fg3">No suggestions to review.</p>`;
    box.classList.remove("hidden");
    return;
  }
  box.innerHTML = `
    <div class="flex flex-wrap items-center justify-between gap-3">
      <p id="autofix-count" class="text-xs text-fg2" role="status"></p>
      <div class="flex items-center gap-2">
        <button type="button" data-on-click="autofixSelect" data-args-click='["gaps"]' class="px-3 py-1.5 rounded-lg bg-s3 hover:bg-s4 border border-line2 text-2xs font-semibold text-fg2">Only fill gaps</button>
        <button type="button" data-on-click="autofixSelect" data-args-click='["none"]' class="px-3 py-1.5 rounded-lg bg-s3 hover:bg-s4 border border-line2 text-2xs font-semibold text-fg2">Select none</button>
        <button type="button" id="btn-autofix-apply" data-on-click="autofixApply" class="px-4 py-2 rounded-xl bg-spotify-green hover:bg-spotify-hover text-black font-bold text-xs disabled:opacity-40 disabled:cursor-not-allowed">Apply…</button>
      </div>
    </div>
    <ul class="space-y-2 max-h-[28rem] overflow-auto pr-1">${autofixItems.map((it) => `
      <li class="p-3 rounded-xl bg-s2 border border-line space-y-1.5">
        <div class="flex flex-wrap items-baseline justify-between gap-2"><span class="text-sm font-semibold text-fg truncate" title="${escapeHtml(it.path)}">${escapeHtml(it.filename)}</span>
          <span class="text-2xs text-fg4">Matched “${escapeHtml(it.matched)}” · ${Math.round(it.confidence * 100)}% sure</span></div>
        ${it.fields.map((f) => autofixFieldRow(it, f)).join("")}
        ${it.art ? `<label class="flex items-start gap-2 text-xs cursor-pointer"><input type="checkbox" class="autofix-art mt-0.5 accent-spotify-green" data-path="${escapeHtml(it.path)}" ${it.art.selected ? "checked" : ""} data-on-change="autofixCount" /><span><span class="text-fg3">Cover art:</span> <span class="text-fg">add the front cover</span> <span class="ml-1 px-1.5 py-0.5 rounded text-2xs font-semibold bg-emerald-950/60 text-emerald-300">fills a gap</span></span></label>` : ""}
      </li>`).join("")}</ul>`;
  box.classList.remove("hidden");
  autofixCount();
}

function autofixSelections() {
  const byPath = new Map();
  const entry = (path) => { if (!byPath.has(path)) byPath.set(path, { path, fields: [], art: false }); return byPath.get(path); };
  document.querySelectorAll("#autofix-results .autofix-opt:checked").forEach((cb) => entry(cb.dataset.path).fields.push(cb.dataset.field));
  document.querySelectorAll("#autofix-results .autofix-art:checked").forEach((cb) => { entry(cb.dataset.path).art = true; });
  return Array.from(byPath.values());
}

function autofixCount() {
  const sel = autofixSelections();
  const changes = sel.reduce((n, s) => n + s.fields.length + (s.art ? 1 : 0), 0);
  const label = document.getElementById("autofix-count");
  const btn = document.getElementById("btn-autofix-apply");
  if (!label || !btn) return;
  label.textContent = `${changes} change${changes === 1 ? "" : "s"} in ${sel.length} file${sel.length === 1 ? "" : "s"} selected`;
  btn.disabled = changes === 0;
}

function autofixSelect(mode) {
  document.querySelectorAll("#autofix-results .autofix-opt").forEach((cb) => {
    const item = autofixItems.find((i) => i.path === cb.dataset.path);
    const field = item && item.fields.find((f) => f.field === cb.dataset.field);
    cb.checked = mode === "gaps" && !!field && field.fills_gap;
  });
  document.querySelectorAll("#autofix-results .autofix-art").forEach((cb) => { cb.checked = mode === "gaps"; });
  autofixCount();
}

async function autofixApply() {
  const selections = autofixSelections();
  const replacing = selections.some((s) => s.fields.some((name) => {
    const item = autofixItems.find((i) => i.path === s.path);
    const f = item && item.fields.find((x) => x.field === name);
    return f && !f.fills_gap;
  }));
  const ok = await UI.confirm({
    title: `Write these changes to ${selections.length} file${selections.length === 1 ? "" : "s"}?`,
    message: `The tags (and cover art, where ticked) are written into the audio files.${replacing ? " Some ticked changes replace values you already have." : " Only empty fields are filled."} If writing fails for a file, it is put back exactly as it was.`,
    confirmText: "Write changes", tone: replacing ? "danger" : undefined
  });
  if (!ok) return;
  let res;
  try {
    res = await UI.api("/api/autofix/apply", { body: { selections }, prefix: "Couldn't apply the changes" });
  } catch (_) { return; }
  const problems = res.failed.length ? `\n${res.failed.length} file${res.failed.length === 1 ? "" : "s"} could not be changed: ${res.failed[0].error}` : "";
  const art = res.art_unavailable ? `\nCover art could not be downloaded for ${res.art_unavailable} file${res.art_unavailable === 1 ? "" : "s"}.` : "";
  UI.toast(`Updated ${res.applied} file${res.applied === 1 ? "" : "s"}.${art}${problems}`, res.failed.length || res.art_unavailable ? "warning" : "success");
  try {
    const st = await UI.api("/api/autofix/status", { quiet: true });
    autofixItems = st.items;
  } catch (_) { autofixItems = []; }
  renderAutofixResults();
  if (typeof loadIntelligence === "function") loadIntelligence();
  if (typeof refreshLibrary === "function") refreshLibrary();
}
