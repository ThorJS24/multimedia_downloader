// ================= INTERACTIVE IMPORT STAGING UI =================
let currentStagingPlan = null;
let stagingSelectedIds = new Set();
let isCommittingStaging = false;
let stagingCommitted = false;
let currentStagingFilter = "all";

async function requestStagingPreview() {
  const text = document.getElementById("multiline-input").value.trim();
  if (!text) {
    return showToast("Please enter at least one Spotify link, URI, or search query to stage.", "warning");
  }

  const btn = document.getElementById("btn-staging-preview");
  const origHtml = btn.innerHTML;
  btn.innerHTML = `<span class="animate-pulse">Inspecting & Staging...</span>`;
  btn.disabled = true;

  const template = document.getElementById("staging-opt-template")?.value || "{artist} - {title}.{ext}";
  const format = document.getElementById("staging-opt-format")?.value || "mp3";
  const collision = document.getElementById("staging-opt-collision")?.value || "skip";

  try {
    const res = await fetch("/api/staging/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input: text,
        template: template,
        output_format: format,
        collision_strategy: collision
      })
    });

    const data = await res.json();
    if (!data.success || !data.plan) {
      throw new Error(data.error || "Failed to generate staging preview.");
    }

    currentStagingPlan = data.plan;
    stagingCommitted = false;
    stagingSelectedIds.clear();

    // Select all eligible items by default
    currentStagingPlan.items.forEach(it => {
      if (it.status === "READY") {
        stagingSelectedIds.add(it.id);
      }
    });

    currentStagingFilter = "all";
    renderStagingModal("all");

    const modal = document.getElementById("staging-modal");
    modal.classList.remove("hidden");
    lucide.createIcons();
  } catch (err) {
    showToast("Staging Error: " + err.message, "error");
  } finally {
    btn.innerHTML = origHtml;
    btn.disabled = false;
    lucide.createIcons();
  }
}

function closeStagingModal() {
  document.getElementById("staging-modal").classList.add("hidden");
  const banner = document.getElementById("staging-result-banner");
  if (banner) banner.classList.add("hidden");
}

function renderStagingModal(filter = "all") {
  if (!currentStagingPlan) return;

  currentStagingFilter = filter;
  const items = currentStagingPlan.items;

  // Update Badges & Counters
  document.getElementById("staging-batch-badge").innerText = currentStagingPlan.batch_id;
  document.getElementById("metric-staging-total").innerText = currentStagingPlan.total_items;
  const eligibleCount = items.filter(it => it.status === "READY").length;
  document.getElementById("metric-staging-eligible").innerText = eligibleCount;
  document.getElementById("metric-staging-collisions").innerText = currentStagingPlan.summary.collisions;
  document.getElementById("metric-staging-archived").innerText = currentStagingPlan.summary.archived;

  // Filter items
  let filtered = items;
  if (filter === "ready") {
    filtered = items.filter(it => it.status === "READY");
  } else if (filter === "collision") {
    filtered = items.filter(it => it.collision_status !== "NEW_DESTINATION");
  } else if (filter === "archived") {
    filtered = items.filter(it => it.is_archived);
  }

  // Update filter pill UI
  ["all", "ready", "collision", "archived"].forEach(f => {
    const el = document.getElementById(`flt-stg-${f}`);
    if (el) {
      if (f === filter) {
        el.className = "px-2.5 py-1 rounded-md bg-spotify-green/20 text-spotify-green text-2xs font-semibold";
      } else {
        el.className = "px-2.5 py-1 rounded-md bg-s3 text-fg3 text-2xs hover:text-fg";
      }
    }
  });

  const tbody = document.getElementById("staging-table-body");
  if (!filtered || filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="py-8 text-center text-fg4">No items match the current filter.</td></tr>`;
  } else {
    tbody.innerHTML = filtered.map((it, idx) => {
      const isChecked = stagingSelectedIds.has(it.id);

      // Collision Pill
      let collisionPill = `<span class="px-2 py-0.5 rounded text-2xs font-semibold bg-emerald-950/60 text-emerald-400 border border-emerald-800/40">✓ New Destination</span>`;
      if (it.collision_status === "COLLISION_SKIP") {
        collisionPill = `<span class="px-2 py-0.5 rounded text-2xs font-semibold bg-amber-950/60 text-amber-300 border border-amber-800/40">⚠ Exists (Skip)</span>`;
      } else if (it.collision_status === "COLLISION_OVERWRITE_OR_RENAME") {
        collisionPill = `<span class="px-2 py-0.5 rounded text-2xs font-semibold bg-red-950/60 text-red-400 border border-red-800/40">⚡ Overwrite/Rename</span>`;
      }

      // Archive Pill
      let archivePill = it.is_archived
        ? `<span class="px-2 py-0.5 rounded text-2xs font-semibold bg-purple-950/60 text-purple-300 border border-purple-800/40">📁 In Archive</span>`
        : `<span class="px-2 py-0.5 rounded text-2xs font-semibold bg-s2 text-fg3">Fresh</span>`;

      return `
        <tr class="hover:bg-hv transition-colors ${isChecked ? 'bg-s2/60' : 'opacity-65'}">
          <td class="py-2.5 px-3 text-center">
            <input type="checkbox" class="stg-item-chk accent-spotify-green cursor-pointer" data-item-id="${it.id}"
              ${isChecked ? 'checked' : ''} data-on-change="toggleStagingItem_76a8bb" data-args-change="${escapeHtml(JSON.stringify([it.id]))}" />
          </td>
          <td class="py-2.5 px-3 font-mono text-fg4 text-center">${idx + 1}</td>
          <td class="py-2.5 px-3">
            <div class="font-bold text-fg truncate max-w-xs">${escapeHtml(it.title)}</div>
            <div class="text-2xs text-fg3 truncate max-w-xs">${escapeHtml(it.artists)}</div>
          </td>
          <td class="py-2.5 px-3 text-fg3 truncate max-w-[140px]">${escapeHtml(it.album || "--")}</td>
          <td class="py-2.5 px-3 font-mono text-2xs text-fg3 truncate max-w-[200px]" title="${escapeHtml(it.target_path)}">
            ${escapeHtml(it.target_path.split(/[\\/]/).pop() || it.target_path)}
          </td>
          <td class="py-2.5 px-3">${collisionPill}</td>
          <td class="py-2.5 px-3">${archivePill}</td>
        </tr>
      `;
    }).join("");
  }

  updateStagingCounts();
}

function toggleStagingItem(itemId, isChecked) {
  if (isChecked) {
    stagingSelectedIds.add(itemId);
  } else {
    stagingSelectedIds.delete(itemId);
  }
  updateStagingCounts();
}

function stagingSelectAll(selectAll) {
  if (!currentStagingPlan) return;
  if (selectAll) {
    currentStagingPlan.items.forEach(it => { if (it.status === "READY") stagingSelectedIds.add(it.id); });
  } else {
    stagingSelectedIds.clear();
  }
  renderStagingModal(currentStagingFilter);
}

function stagingToggleMaster(isChecked) {
  stagingSelectAll(isChecked);
}

function filterStagingTable(filter) {
  renderStagingModal(filter);
}

function updateStagingCounts() {
  if (!currentStagingPlan) return;

  const total = currentStagingPlan.items.length;
  const selected = stagingSelectedIds.size;
  const excluded = total - selected;

  document.getElementById("metric-staging-selected").innerText = selected;
  document.getElementById("metric-staging-excluded").innerText = excluded;
  document.getElementById("confirm-enq-count").innerText = selected;
  document.getElementById("confirm-excl-count").innerText = excluded;

  const btnCommit = document.getElementById("btn-commit-staging");
  if (btnCommit) {
    btnCommit.disabled = selected === 0 || isCommittingStaging || stagingCommitted;
  }

  const masterChk = document.getElementById("chk-staging-master");
  if (masterChk) {
    masterChk.checked = selected > 0 && selected === total;
  }

  // Check for overwrite collisions among selected items
  const collisionMode = document.getElementById("staging-opt-collision")?.value || "skip";
  const hasOverwriteCollision = currentStagingPlan.items.some(
    it => stagingSelectedIds.has(it.id) && it.collision_status === "COLLISION_OVERWRITE_OR_RENAME" && collisionMode === "overwrite"
  );
  const warnEl = document.getElementById("staging-overwrite-warning");
  if (warnEl) {
    if (hasOverwriteCollision) {
      warnEl.classList.remove("hidden");
    } else {
      warnEl.classList.add("hidden");
    }
  }
}

async function commitStagingBatch() {
  if (!currentStagingPlan) return;
  if (stagingSelectedIds.size === 0) {
    return showToast("No items selected. Please select at least one item to commit.", "warning");
  }
  if (isCommittingStaging || stagingCommitted) return;

  const btn = document.getElementById("btn-commit-staging");
  const origHtml = btn.innerHTML;
  btn.innerHTML = `<span class="animate-pulse">Enqueuing ${stagingSelectedIds.size} items...</span>`;
  btn.disabled = true;
  isCommittingStaging = true;

  // Calculate excluded IDs: items in plan that are NOT selected
  const excludedIds = currentStagingPlan.items
    .filter(it => !stagingSelectedIds.has(it.id))
    .map(it => it.id);

  const banner = document.getElementById("staging-result-banner");
  banner.classList.add("hidden");

  try {
    const res = await fetch("/api/staging/commit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        batch_id: currentStagingPlan.batch_id,
        excluded_ids: excludedIds
      })
    });

    const data = await res.json();
    if (!data.success) {
      throw new Error(data.error || "Failed to commit staging batch.");
    }

    banner.className = "p-3 rounded-xl text-xs font-semibold flex items-center justify-between bg-emerald-950/70 border border-emerald-700/60 text-emerald-300";
    banner.innerHTML = `
      <div class="flex items-center space-x-2">
        <i data-lucide="check-circle" class="w-4 h-4 text-emerald-400"></i>
        <span>Successfully enqueued <strong>${data.enqueued}</strong> tracks into the Download Queue!</span>
      </div>
      <button data-on-click="switchTab_42a907" class="px-3 py-1 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-black font-bold text-2xs">
        View Queue
      </button>
    `;
    banner.classList.remove("hidden");
    lucide.createIcons();

    // Refresh queue counts
    stagingCommitted = true;           // a second click would enqueue the same batch again
    clearFetchInput();
    UI.toast(`Added ${data.enqueued} track${data.enqueued === 1 ? "" : "s"} to the queue.`, "success");
    fetchQueue();
  } catch (err) {
    banner.className = "p-3 rounded-xl text-xs font-semibold flex items-center space-x-2 bg-red-950/70 border border-red-700/60 text-red-300";
    banner.innerHTML = `
      <i data-lucide="alert-circle" class="w-4 h-4 text-red-400"></i>
      <span>Commit Error: ${escapeHtml(err.message)}</span>
    `;
    banner.classList.remove("hidden");
    lucide.createIcons();
  } finally {
    isCommittingStaging = false;
    btn.innerHTML = origHtml;
    btn.disabled = stagingCommitted || stagingSelectedIds.size === 0;
    lucide.createIcons();
  }
}

