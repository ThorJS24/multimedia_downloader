// Plugins & extensions: built-in feature switches and plugin packs (declarative add-ons; nothing from a pack ever runs as code).
"use strict";

let pluginPacks = [];

async function loadPlugins() {
  try {
    const data = await UI.api("/api/plugins/packs", { quiet: true });
    pluginPacks = data.packs;
    renderBuiltinFeatures(data.builtin);
    renderPluginPacks();
  } catch (_) {
    document.getElementById("plugins-packs").innerHTML = `<p class="text-xs text-red-400">Couldn't load the plugin list. <button type="button" data-on-click="loadPluginsAgain" class="underline">Try again</button></p>`;
  }
}

function renderBuiltinFeatures(list) {
  document.getElementById("plugins-builtin").innerHTML = list.map((f) => `
    <li class="p-4 rounded-2xl bg-s1 border border-line flex items-start justify-between gap-3">
      <div class="min-w-0">
        <div class="text-sm font-semibold text-fg">${escapeHtml(f.name)}</div>
        <p class="text-xs text-fg3 mt-0.5">${escapeHtml(f.description)}</p>
        <button type="button" data-on-click="pluginsOpenSetting" data-args-click="${jsonAttr([f.anchor])}" class="mt-1.5 text-2xs text-fg3 hover:text-fg underline">More settings</button>
      </div>
      <label class="shrink-0 mt-0.5 cursor-pointer"><span class="sr-only">${escapeHtml(f.name)}</span>
        <input type="checkbox" class="accent-spotify-green" ${f.enabled ? "checked" : ""} data-on-change="pluginsToggleBuiltin" data-args-change="${jsonAttr([f.setting])}" /></label>
    </li>`).join("");
}

async function pluginsToggleBuiltin(setting, event) {
  const box = event.target;
  const [section, key] = setting.split(".");
  try {
    await UI.api("/api/settings", { body: { [section]: { [key]: box.checked } }, prefix: "Couldn't change that" });
  } catch (_) { box.checked = !box.checked; return; }
  if (typeof loadSettings === "function") loadSettings(true);
}

function pluginsOpenSetting(anchor) {
  switchTab("settings");
  setTimeout(() => { const el = document.getElementById(anchor); if (el) el.scrollIntoView({ block: "start" }); }, 150);
}

function packFieldHtml(pack, s) {
  const id = `pack-${pack.id}-${s.key}`;
  const type = s.secret ? "password" : (s.type === "url" ? "url" : "text");
  const value = pack.values[s.key] || "";
  return `<div class="space-y-1">
    <label for="${escapeHtml(id)}" class="block text-xs font-semibold text-fg2">${escapeHtml(s.label)}${s.required ? "" : ` <span class="font-normal text-fg4">(optional)</span>`}</label>
    <div class="flex items-center gap-2">
      <input id="${escapeHtml(id)}" type="${type}" data-pack-field="${escapeHtml(s.key)}" value="${escapeHtml(value)}" autocomplete="off" spellcheck="false"
        class="flex-1 min-w-0 bg-s2 border border-line2 focus:border-spotify-green rounded-xl px-3 py-2 text-xs ${s.secret ? "" : "font-mono "}text-fg focus:outline-none" />
      ${s.type === "folder" ? `<button type="button" data-on-click="pluginsBrowse" data-args-click="${jsonAttr([id])}" class="px-3 py-2 rounded-xl bg-s3 hover:bg-s4 border border-line2 text-xs font-semibold text-fg2">Browse…</button>` : ""}
    </div>
    ${s.help ? `<p class="text-2xs text-fg4">${escapeHtml(s.help)}</p>` : ""}
    ${s.secret ? `<p class="text-2xs text-fg4">Stored encrypted on this computer and never shown again.</p>` : ""}
  </div>`;
}

function packCardHtml(p) {
  const when = p.last_status ? new Date(p.last_status.at * 1000).toLocaleString() : "";
  const status = p.last_status
    ? `<p class="text-2xs ${p.last_status.ok ? "text-emerald-400" : "text-red-400"}">Last attempt (${escapeHtml(when)}): ${escapeHtml(p.last_status.detail)}</p>` : "";
  const buttons = p.enabled
    ? `<button type="button" data-on-click="pluginsSave" data-args-click="${jsonAttr([p.id, true])}" class="px-4 py-2 rounded-xl bg-spotify-green hover:bg-spotify-hover text-black font-semibold text-xs">Save changes</button>
       <button type="button" data-on-click="pluginsTest" data-args-click="${jsonAttr([p.id])}" class="px-4 py-2 rounded-xl bg-s3 hover:bg-s4 border border-line2 text-xs font-semibold text-fg">Send a test</button>
       <button type="button" data-on-click="pluginsSave" data-args-click="${jsonAttr([p.id, false])}" class="px-4 py-2 rounded-xl bg-s3 hover:bg-s4 border border-line2 text-xs font-semibold text-fg2">Turn off</button>`
    : `<button type="button" data-on-click="pluginsSave" data-args-click="${jsonAttr([p.id, true])}" class="px-4 py-2 rounded-xl bg-spotify-green hover:bg-spotify-hover text-black font-semibold text-xs">Turn on</button>`;
  const removal = p.source === "imported"
    ? `<button type="button" data-on-click="pluginsRemove" data-args-click="${jsonAttr([p.id, p.name])}" class="px-4 py-2 rounded-xl text-red-300 hover:bg-red-950/40 border border-line text-xs font-semibold">Remove</button>`
    : "";
  return `<article id="pack-${escapeHtml(p.id)}" data-pack="${escapeHtml(p.id)}" class="p-5 rounded-2xl bg-s1 border border-line space-y-3" aria-labelledby="pack-h-${escapeHtml(p.id)}">
    <div class="flex flex-wrap items-start justify-between gap-2">
      <div class="min-w-0">
        <h4 id="pack-h-${escapeHtml(p.id)}" class="text-sm font-semibold text-fg">${escapeHtml(p.name)} <span class="ml-1 text-2xs font-mono font-normal text-fg4">v${escapeHtml(p.version)}${p.author ? ` · ${escapeHtml(p.author)}` : ""}</span></h4>
        <p class="text-xs text-fg3 mt-0.5 max-w-3xl">${escapeHtml(p.description)}</p>
      </div>
      <div class="flex items-center gap-1.5 shrink-0">
        ${p.source === "imported" ? `<span class="px-2 py-0.5 rounded-full border border-amber-800/40 bg-amber-950/60 text-amber-300 text-2xs font-semibold">Added by you</span>` : ""}
        <span class="px-2 py-0.5 rounded-full border text-2xs font-semibold ${p.enabled ? "border-emerald-800/40 bg-emerald-950/60 text-emerald-300" : "border-line2 bg-s3 text-fg3"}">${p.enabled ? "On" : "Off"}</span>
      </div>
    </div>
    <div class="rounded-xl bg-s2 border border-line p-3">
      <div class="text-2xs font-semibold text-fg3 mb-1">What it will do</div>
      <ul class="space-y-1 text-xs text-fg2 list-disc pl-4">${p.permissions.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
    </div>
    ${p.settings.length ? `<div class="grid grid-cols-1 md:grid-cols-2 gap-3">${p.settings.map((s) => packFieldHtml(p, s)).join("")}</div>` : ""}
    <p class="pack-error hidden text-xs text-red-400" role="alert"></p>
    <ul class="pack-test hidden space-y-1 text-xs" aria-live="polite"></ul>
    ${status}
    <div class="flex flex-wrap items-center gap-2">${buttons}${removal}</div>
  </article>`;
}

function renderPluginPacks() {
  document.getElementById("plugins-packs").innerHTML = pluginPacks.map(packCardHtml).join("");
  UI.icons();
}

function packCard(id) { return document.querySelector(`[data-pack="${CSS.escape(id)}"]`); }

function showPackError(id, message) {
  const el = packCard(id) && packCard(id).querySelector(".pack-error");
  if (!el) return;
  el.textContent = message || "";
  el.classList.toggle("hidden", !message);
}

async function pluginsSave(id, enable) {
  const card = packCard(id);
  const values = {};
  card.querySelectorAll("[data-pack-field]").forEach((el) => { values[el.dataset.packField] = el.value.trim(); });
  showPackError(id, "");
  try {
    await UI.api("/api/plugins/packs/configure", { body: { id, values, enabled: enable }, quiet: true });
  } catch (err) { showPackError(id, err.message); return; }
  const name = (pluginPacks.find((p) => p.id === id) || {}).name || "Plugin";
  UI.toast(enable ? `${name} is on.` : `${name} is off.`, "success", 2500);
  await loadPlugins();
}

async function pluginsTest(id) {
  const list = packCard(id).querySelector(".pack-test");
  showPackError(id, "");
  let res;
  try {
    res = await UI.api("/api/plugins/packs/test", { body: { id }, quiet: true });
  } catch (err) { showPackError(id, err.message); return; }
  list.innerHTML = res.results.map((r) => `<li class="${r.ok ? "text-emerald-400" : "text-red-400"}">${r.ok ? "Worked" : "Failed"} (${escapeHtml(r.label)}): ${escapeHtml(r.detail)}</li>`).join("");
  list.classList.remove("hidden");
  const allOk = res.results.every((r) => r.ok);
  UI.toast(allOk ? "The test worked." : "The test failed. See the details on the plugin.", allOk ? "success" : "warning");
}

async function pluginsRemove(id, name) {
  const ok = await UI.confirm({ title: `Remove “${name}”?`, message: "Its settings are deleted too. You can add the pack file again later.", confirmText: "Remove", tone: "danger" });
  if (!ok) return;
  try { await UI.api("/api/plugins/packs/remove", { body: { id }, prefix: "Couldn't remove it" }); } catch (_) { return; }
  loadPlugins();
}

function pluginsPickFile() { document.getElementById("plugin-file-input").click(); }

async function pluginsImportFile(event) {
  const input = event.target;
  const file = input.files && input.files[0];
  input.value = "";
  if (!file) return;
  if (file.size > 64 * 1024) { UI.toast("That file is too large to be a plugin pack.", "error"); return; }
  let manifest;
  try { manifest = JSON.parse(await file.text()); } catch (_) { UI.toast("That file isn't valid JSON.", "error"); return; }
  const ok = await UI.confirm({
    title: "Add this plugin pack?",
    message: "Only add packs from people you trust. It stays switched off until you read what it will do and turn it on.",
    confirmText: "Add it"
  });
  if (!ok) return;
  let res;
  try { res = await UI.api("/api/plugins/packs/import", { body: { manifest }, prefix: "That pack can't be added" }); } catch (_) { return; }
  UI.toast(`Added “${res.pack.name}”. Review what it does, then turn it on.`, "success");
  await loadPlugins();
  const card = packCard(res.pack.id);
  if (card) card.scrollIntoView({ block: "center" });
}
