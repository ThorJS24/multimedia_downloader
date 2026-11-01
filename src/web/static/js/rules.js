// Rules & hooks screen (Automation -> Rules & hooks): organising rules with preview/apply/undo, download hooks,
// and the download archive. Talks to /api/rules/*, /api/plugins/hooks/* and /api/archive/list.
"use strict";

const RULE_CONDITIONS = {
  all: { label: "is in the library (every file)", needsValue: false, hint: "Matches every file." },
  genre_contains: { label: "has a genre containing…", needsValue: true, hint: "Not case-sensitive, e.g. rock." },
  artist_contains: { label: "has an artist containing…", needsValue: true, hint: "Not case-sensitive, e.g. daft." },
  album_contains: { label: "has an album containing…", needsValue: true, hint: "Not case-sensitive, e.g. live." },
  format_equals: { label: "is in the format…", needsValue: true, hint: "e.g. mp3, flac, ogg." },
  bitrate_below: { label: "has a bitrate below (kbps)…", needsValue: true, hint: "A number, e.g. 192." },
  missing_tag: { label: "is missing the tag…", needsValue: true, hint: "One of: title, artist, album, genre, year." }
};

const RULE_ACTIONS = {
  move_to_template: { label: "move it to the folder pattern", placeholder: "{artist}/{album}/{title}.{ext}", hint: "Relative to your downloads folder. Available: {artist} {album} {title} {track_number} {year} {genre} {ext}." },
  rename_template: { label: "rename it using the pattern", placeholder: "{artist} - {title}.{ext}", hint: "A file name only; the file stays in its folder." },
  flag_issue: { label: "flag it for review", placeholder: "Needs attention", hint: "Doesn't move anything; marks the file as an issue." }
};

let rulesCache = [];
let lastRulesPreview = null;
let archiveCache = [];

const ruleCondLabel = (r) => `${(RULE_CONDITIONS[r.condition_type] || {}).label || r.condition_type}${RULE_CONDITIONS[r.condition_type]?.needsValue ? " “" + r.condition_value + "”" : ""}`;
const ruleActionLabel = (r) => `${(RULE_ACTIONS[r.action_type] || {}).label || r.action_type}`;

function fillRuleSelects() {
  const c = document.getElementById("rule-cond-type");
  const a = document.getElementById("rule-action-type");
  if (c && !c.options.length) c.innerHTML = Object.entries(RULE_CONDITIONS).map(([k, v]) => `<option value="${k}">${escapeHtml(v.label)}</option>`).join("");
  if (a && !a.options.length) a.innerHTML = Object.entries(RULE_ACTIONS).map(([k, v]) => `<option value="${k}">${escapeHtml(v.label)}</option>`).join("");
}

function rulesConditionChanged() {
  const spec = RULE_CONDITIONS[document.getElementById("rule-cond-type").value] || {};
  const v = document.getElementById("rule-cond-value");
  v.classList.toggle("hidden", !spec.needsValue);
  document.getElementById("rule-cond-hint").textContent = spec.hint || "";
  if (!spec.needsValue) v.value = "";
}

function rulesActionChanged() {
  const spec = RULE_ACTIONS[document.getElementById("rule-action-type").value] || {};
  const v = document.getElementById("rule-action-value");
  v.placeholder = spec.placeholder || "";
  document.getElementById("rule-action-hint").textContent = spec.hint || "";
}

function showRuleForm(rule) {
  fillRuleSelects();
  const form = document.getElementById("rule-form");
  document.getElementById("rule-id").value = rule ? rule.id : "";
  document.getElementById("rule-name").value = rule ? rule.name : "";
  document.getElementById("rule-priority").value = rule ? rule.priority : (rulesCache.length ? Math.max(...rulesCache.map((r) => r.priority)) + 10 : 10);
  document.getElementById("rule-cond-type").value = rule ? rule.condition_type : "genre_contains";
  document.getElementById("rule-cond-value").value = rule ? rule.condition_value : "";
  document.getElementById("rule-action-type").value = rule ? rule.action_type : "move_to_template";
  document.getElementById("rule-action-value").value = rule ? rule.action_value : "";
  document.getElementById("rule-form-error").classList.add("hidden");
  rulesConditionChanged();
  rulesActionChanged();
  form.classList.remove("hidden");
  document.getElementById("rule-name").focus();
}

function rulesNew() { showRuleForm(null); }
function rulesCancel() { document.getElementById("rule-form").classList.add("hidden"); }
function rulesEdit(id) { const r = rulesCache.find((x) => x.id === id); if (r) showRuleForm(r); }

async function rulesSave(event) {
  event.preventDefault();
  const err = document.getElementById("rule-form-error");
  const body = {
    id: document.getElementById("rule-id").value || undefined,
    name: document.getElementById("rule-name").value.trim(),
    priority: document.getElementById("rule-priority").value,
    condition_type: document.getElementById("rule-cond-type").value,
    condition_value: document.getElementById("rule-cond-value").value.trim(),
    action_type: document.getElementById("rule-action-type").value,
    action_value: document.getElementById("rule-action-value").value.trim() || (RULE_ACTIONS[document.getElementById("rule-action-type").value] || {}).placeholder || ""
  };
  const existing = rulesCache.find((r) => r.id === body.id);
  if (existing) body.is_enabled = !!existing.is_enabled;
  try {
    await UI.api("/api/rules/save", { body, quiet: true });
    err.classList.add("hidden");
    rulesCancel();
    UI.toast(`Rule “${body.name}” saved.`, "success", 2000);
    lastRulesPreview = null;
    await loadRules();
  } catch (e) {
    err.textContent = e.message;
    err.classList.remove("hidden");
  }
}

async function rulesToggle(id, enabled) {
  const r = rulesCache.find((x) => x.id === id);
  if (!r) return;
  try {
    await UI.api("/api/rules/save", { body: { ...r, is_enabled: enabled }, prefix: "Could not update the rule" });
    lastRulesPreview = null;
    await loadRules();
  } catch (_) { await loadRules(); }
}

async function rulesDelete(id) {
  const r = rulesCache.find((x) => x.id === id);
  const ok = await UI.confirm({ title: `Delete the rule “${r ? r.name : id}”?`, message: "Files that were already organised stay where they are.", confirmText: "Delete rule", tone: "danger" });
  if (!ok) return;
  try {
    await UI.api("/api/rules/delete", { body: { id }, prefix: "Could not delete the rule" });
    lastRulesPreview = null;
    await loadRules();
  } catch (_) { /* reported */ }
}

function renderRules() {
  const list = document.getElementById("rules-list");
  if (!list) return;
  if (!rulesCache.length) {
    list.innerHTML = `<li class="text-xs text-fg3 py-3">No rules yet. Without any, files are sorted into Artist / Album folders. Add a rule to do something different.</li>`;
    return;
  }
  list.innerHTML = rulesCache.map((r) => `
    <li class="flex flex-wrap items-center gap-3 p-3 rounded-xl bg-s2 border border-line ${r.is_enabled ? "" : "opacity-60"}">
      <label class="flex items-center shrink-0 cursor-pointer" title="${r.is_enabled ? "Enabled" : "Disabled"}">
        <span class="sr-only">Enable ${escapeHtml(r.name)}</span>
        <input type="checkbox" class="accent-spotify-green" ${r.is_enabled ? "checked" : ""} data-on-change="rulesToggleFromInput" data-args-change="${jsonAttr([r.id])}" />
      </label>
      <span class="w-10 text-center text-2xs font-mono text-fg4" title="Priority">#${r.priority}</span>
      <div class="flex-1 min-w-[12rem]">
        <div class="font-semibold text-fg text-xs">${escapeHtml(r.name)}</div>
        <div class="text-2xs text-fg3">When a file ${escapeHtml(ruleCondLabel(r))}, ${escapeHtml(ruleActionLabel(r))} <span class="font-mono text-fg2">${escapeHtml(r.action_value)}</span></div>
      </div>
      <button type="button" data-on-click="rulesEditFromButton" data-args-click="${jsonAttr([r.id])}" class="px-2.5 py-1.5 rounded-lg bg-s3 hover:bg-s4 border border-line2 text-2xs font-semibold text-fg2" aria-label="Edit ${escapeHtml(r.name)}">Edit</button>
      <button type="button" data-on-click="rulesDeleteFromButton" data-args-click="${jsonAttr([r.id])}" class="px-2.5 py-1.5 rounded-lg bg-s3 hover:bg-s4 border border-line2 text-2xs font-semibold text-red-400" aria-label="Delete ${escapeHtml(r.name)}">Delete</button>
    </li>`).join("");
}

async function loadRules() {
  try {
    rulesCache = (await UI.api("/api/rules", { quiet: true })).rules || [];
    renderRules();
  } catch (err) {
    document.getElementById("rules-list").innerHTML = `<li class="text-xs text-red-400 py-3">Couldn't load rules: ${escapeHtml(err.message)}</li>`;
  }
}

// ---------- preview / apply / undo
async function rulesPreview() {
  await UI.busy(document.getElementById("btn-rules-preview"), "Checking…", async () => {
    try {
      const res = await UI.api("/api/rules/preview", { body: {}, prefix: "Preview failed" });
      lastRulesPreview = res.preview;
      renderRulesPreview();
    } catch (_) { /* reported */ }
  });
}

function renderRulesPreview() {
  const p = lastRulesPreview;
  const summary = document.getElementById("rules-summary");
  const body = document.getElementById("rules-preview-body");
  const apply = document.getElementById("btn-rules-apply");
  if (!p) { summary.classList.add("hidden"); apply.disabled = true; return; }
  const cell = (label, n, cls) => `<div class="p-3 rounded-xl bg-s2 border border-line ${cls || ""}"><div class="text-2xs text-fg3">${label}</div><div class="text-lg font-semibold font-mono">${n}</div></div>`;
  summary.classList.remove("hidden");
  summary.innerHTML = cell("Files checked", p.total_evaluated) + cell("Would move", p.will_move, "text-spotify-green") + cell("Already in place", p.unchanged) + cell("Name clashes", p.collisions, p.collisions ? "text-amber-400" : "");
  const moves = (p.planned_actions || []).filter((a) => a.will_move);
  body.innerHTML = moves.length ? moves.slice(0, 300).map((a) => `
    <tr class="hover:bg-hv">
      <td class="py-2 px-3 font-mono text-2xs text-fg2 truncate max-w-[18rem]" title="${escapeHtml(a.source_path)}">${escapeHtml(a.filename)}</td>
      <td class="py-2 px-3 font-mono text-2xs text-fg truncate max-w-[24rem]" title="${escapeHtml(a.target_path)}">${escapeHtml(a.target_path)}${a.collision ? ' <span class="px-1.5 py-0.5 rounded bg-amber-950/60 text-amber-300 text-2xs">exists</span>' : ""}</td>
      <td class="py-2 px-3 text-2xs text-fg3">${escapeHtml(a.rule_name || "")}</td>
    </tr>`).join("") + (moves.length > 300 ? `<tr><td colspan="3" class="py-2 px-3 text-2xs text-fg3">…and ${moves.length - 300} more (all will be applied).</td></tr>` : "")
    : `<tr><td colspan="3" class="py-6 text-center text-fg3">Nothing needs to move: everything already matches your rules.</td></tr>`;
  apply.disabled = moves.length === 0;
  apply.textContent = moves.length ? `Apply ${moves.length} move${moves.length === 1 ? "" : "s"}…` : "Apply…";
}

async function rulesApply() {
  const p = lastRulesPreview;
  const moves = p ? (p.planned_actions || []).filter((a) => a.will_move) : [];
  if (!moves.length) return;
  const policy = document.getElementById("rules-collision").value;
  const ok = await UI.confirm({
    title: `Move ${moves.length} file${moves.length === 1 ? "" : "s"}?`,
    message: `${p.collisions ? `${p.collisions} destination name${p.collisions === 1 ? " already exists" : "s already exist"} (${policy === "skip" ? "those are skipped" : policy === "rename" ? "both are kept" : "replaced, with the old copy kept for undo"}). ` : ""}You can undo the whole batch afterwards.`,
    confirmText: `Move ${moves.length}`
  });
  if (!ok) return;
  try {
    const res = await UI.api("/api/rules/execute", { body: { actions: moves, collision_policy: policy }, quiet: true });
    UI.toast(`Moved ${res.executed_count}, skipped ${res.skipped_count}${res.error_count ? `, ${res.error_count} errors` : ""}.`, res.error_count ? "warning" : "success", undefined,
      res.executed_count ? { action: { label: "Undo", onClick: () => rulesUndoBatch(res.batch_id, true) } } : undefined);
    lastRulesPreview = null;
    renderRulesPreview();
    document.getElementById("rules-preview-body").innerHTML = `<tr><td colspan="3" class="py-6 text-center text-fg3">Applied. Preview again to see what is left.</td></tr>`;
    await loadRuleBatches();
    if (typeof refreshLibrary === "function") refreshLibrary();
  } catch (e) {
    UI.toast(e.message, "error");
  }
}

async function rulesUndoBatch(batchId, skipConfirm = false) {
  if (!skipConfirm) {
    const ok = await UI.confirm({ title: "Undo this batch?", message: "Every file it moved goes back to where it was.", confirmText: "Undo batch" });
    if (!ok) return;
  }
  try {
    const res = await UI.api("/api/rules/rollback", { body: { batch_id: batchId }, quiet: true });
    UI.toast(`Restored ${res.reverted_count} file${res.reverted_count === 1 ? "" : "s"}.`, res.errors && res.errors.length ? "warning" : "success");
    await loadRuleBatches();
    if (typeof refreshLibrary === "function") refreshLibrary();
  } catch (e) {
    UI.toast(`Undo failed: ${e.message}`, "error");
  }
}

async function loadRuleBatches() {
  const ul = document.getElementById("rules-batches");
  try {
    const batches = (await UI.api("/api/rules/batches", { quiet: true })).batches || [];
    ul.innerHTML = batches.length ? batches.map((b) => {
      const undone = b.moved === 0 && b.rolled_back > 0;
      return `<li class="flex flex-wrap items-center justify-between gap-3 p-2.5 rounded-lg bg-s2 border border-line text-xs">
        <span class="text-fg2">${new Date(b.started_at * 1000).toLocaleString()} · <b class="text-fg">${b.moved}</b> moved${b.rolled_back ? `, ${b.rolled_back} undone` : ""}</span>
        ${undone ? '<span class="text-2xs text-fg4">Undone</span>' : `<button type="button" data-on-click="rulesUndoFromButton" data-args-click="${jsonAttr([b.batch_id])}" class="px-2.5 py-1 rounded-lg bg-s3 hover:bg-s4 border border-line2 text-2xs font-semibold text-fg2">Undo</button>`}
      </li>`;
    }).join("") : `<li class="text-xs text-fg3">Nothing has been applied yet.</li>`;
  } catch (_) { /* leave as is */ }
}

// ---------- hooks
function hooksActionChanged() {
  const act = document.getElementById("hook-action").value;
  document.getElementById("hook-target-label").textContent = act === "webhook" ? "Web address (https://…)" : "Folder (inside the app)";
  document.getElementById("hook-target").placeholder = act === "webhook" ? "https://example.com/hook" : "downloads/sorted";
}

async function loadHooks() {
  const ul = document.getElementById("hooks-list");
  try {
    const hooks = (await UI.api("/api/plugins/hooks", { quiet: true })).hooks || [];
    const evLabel = { on_download_complete: "a download finishes", on_download_failed: "a download fails", on_queue_finished: "the queue finishes a batch" };
    ul.innerHTML = hooks.length ? hooks.map((h) => `
      <li class="flex flex-wrap items-center gap-3 p-3 rounded-xl bg-s2 border border-line">
        <div class="flex-1 min-w-[12rem]">
          <div class="font-semibold text-fg text-xs">${escapeHtml(h.name)}</div>
          <div class="text-2xs text-fg3">When ${escapeHtml(evLabel[h.event_type] || h.event_type)}: ${h.action_type === "webhook" ? "call" : "copy the file to"} <span class="font-mono text-fg2">${escapeHtml(h.target)}</span></div>
        </div>
        <button type="button" data-on-click="hooksDeleteFromButton" data-args-click="${jsonAttr([h.id])}" class="px-2.5 py-1.5 rounded-lg bg-s3 hover:bg-s4 border border-line2 text-2xs font-semibold text-red-400" aria-label="Delete ${escapeHtml(h.name)}">Delete</button>
      </li>`).join("") : `<li class="text-xs text-fg3 py-2">No hooks yet.</li>`;
  } catch (err) {
    ul.innerHTML = `<li class="text-xs text-red-400 py-2">Couldn't load hooks: ${escapeHtml(err.message)}</li>`;
  }
}

async function hooksSave(event) {
  event.preventDefault();
  const err = document.getElementById("hook-form-error");
  const body = {
    name: document.getElementById("hook-name").value.trim(),
    event_type: document.getElementById("hook-event").value,
    action_type: document.getElementById("hook-action").value,
    target: document.getElementById("hook-target").value.trim()
  };
  try {
    await UI.api("/api/plugins/hooks/register", { body, quiet: true });
    err.classList.add("hidden");
    document.getElementById("hook-name").value = "";
    document.getElementById("hook-target").value = "";
    UI.toast(`Hook “${body.name}” added.`, "success", 2000);
    loadHooks();
  } catch (e) {
    err.textContent = e.message;
    err.classList.remove("hidden");
  }
}

async function hooksDelete(id) {
  const ok = await UI.confirm({ title: "Delete this hook?", message: "It stops running after future downloads.", confirmText: "Delete hook", tone: "danger" });
  if (!ok) return;
  try {
    await UI.api("/api/plugins/hooks/delete", { body: { id }, prefix: "Could not delete the hook" });
    loadHooks();
  } catch (_) { /* reported */ }
}

// ---------- archive
function renderArchive() {
  const ul = document.getElementById("archive-list");
  const q = (document.getElementById("archive-filter").value || "").trim().toLowerCase();
  const rows = archiveCache.filter((a) => !q || `${a.title} ${a.artist}`.toLowerCase().includes(q));
  ul.innerHTML = rows.length ? rows.map((a) => `
    <li class="flex items-center justify-between gap-3 px-3 py-2 text-xs">
      <span class="truncate"><b class="text-fg">${escapeHtml(a.title || a.resource_id)}</b> <span class="text-fg3">· ${escapeHtml(a.artist || "")}</span></span>
      <span class="text-2xs text-fg4 shrink-0">${a.downloaded_at ? new Date(a.downloaded_at * 1000).toLocaleDateString() : ""}</span>
    </li>`).join("") : `<li class="text-xs text-fg3 p-4">${archiveCache.length ? "Nothing matches that filter." : "No downloads have been archived yet."}</li>`;
}

async function loadArchive() {
  try {
    archiveCache = (await UI.api("/api/archive/list?limit=500", { quiet: true })).archive || [];
    renderArchive();
  } catch (_) { /* keep placeholder */ }
}

function loadRulesTab() {
  fillRuleSelects();
  loadRules();
  loadRuleBatches();
  loadHooks();
  loadArchive();
  hooksActionChanged();
}
