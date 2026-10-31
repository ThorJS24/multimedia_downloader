// ================= TAB 14: LOCAL FOLDER WATCHER =================
let isWatcherPaused = false;

async function loadWatcherDashboard() {
  await fetchWatcherStatus();
  await loadWatchedFolders();
  await loadWatcherPendingIntake();
}

async function fetchWatcherStatus() {
  try {
    const res = await fetch("/api/watcher/status");
    const data = await res.json();

    const pill = document.getElementById("watcher-status-pill");
    if (pill) {
      pill.innerText = data.status || "STOPPED";
      if (data.status === "RUNNING") {
        pill.className = "px-2.5 py-0.5 rounded-full text-2xs font-mono font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30";
      } else if (data.status === "PAUSED") {
        pill.className = "px-2.5 py-0.5 rounded-full text-2xs font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30";
      } else if (data.status === "ERROR") {
        pill.className = "px-2.5 py-0.5 rounded-full text-2xs font-mono font-bold bg-red-500/20 text-red-400 border border-red-500/30";
      } else {
        pill.className = "px-2.5 py-0.5 rounded-full text-2xs font-mono font-bold bg-s3 text-fg3 border border-line2";
      }
    }

    isWatcherPaused = !!data.is_paused;
    const pauseBtn = document.getElementById("btn-watcher-pause");
    if (pauseBtn) {
      pauseBtn.innerHTML = isWatcherPaused
        ? `<i data-lucide="play" class="w-3.5 h-3.5 mr-1.5 fill-amber-300"></i> Resume`
        : `<i data-lucide="pause" class="w-3.5 h-3.5 mr-1.5"></i> Pause`;
    }

    // Update Metrics
    const metrics = data.metrics || {};
    const mFolders = document.getElementById("metric-watcher-folders");
    if (mFolders) mFolders.innerText = data.total_folders || 0;
    const mCreated = document.getElementById("metric-watcher-created");
    if (mCreated) mCreated.innerText = metrics.created || 0;
    const mModified = document.getElementById("metric-watcher-modified");
    if (mModified) mModified.innerText = metrics.modified || 0;
    const mDeleted = document.getElementById("metric-watcher-deleted");
    if (mDeleted) mDeleted.innerText = metrics.deleted || 0;
    const mPending = document.getElementById("metric-watcher-pending");
    if (mPending) mPending.innerText = data.pending_intake_count || 0;

    // Update Activity Log
    const logBox = document.getElementById("watcher-activity-log");
    if (logBox && data.recent_errors) {
      if (data.recent_errors.length === 0) {
        logBox.innerHTML = `<div class="text-emerald-400">✓ Watcher operating normally with zero recent errors.</div>`;
      } else {
        logBox.innerHTML = data.recent_errors.map(e => `
          <div class="text-red-400 flex items-center space-x-1.5">
            <span class="text-fg4">•</span>
            <span>${escapeHtml(e)}</span>
          </div>
        `).join("");
      }
    }

    lucide.createIcons();
  } catch (err) {
    console.error("Error loading watcher status:", err);
  }
}

async function startWatcher() {
  try {
    const res = await (await fetch("/api/watcher/start", { method: "POST" })).json();
    if (res.success) {
      await fetchWatcherStatus();
    } else {
      showToast("Failed to start watcher: " + (res.status?.last_error || "Unknown error"), "error");
    }
  } catch (err) {
    showToast("Error starting watcher: " + err, "error");
  }
}

async function stopWatcher() {
  try {
    const res = await (await fetch("/api/watcher/stop", { method: "POST" })).json();
    if (res.success) {
      await fetchWatcherStatus();
    }
  } catch (err) {
    showToast("Error stopping watcher: " + err, "error");
  }
}

async function togglePauseWatcher() {
  try {
    const endpoint = isWatcherPaused ? "/api/watcher/resume" : "/api/watcher/pause";
    const res = await (await fetch(endpoint, { method: "POST" })).json();
    if (res.success) {
      await fetchWatcherStatus();
    }
  } catch (err) {
    showToast("Error toggling watcher pause: " + err, "error");
  }
}

async function loadWatchedFolders() {
  try {
    const res = await (await fetch("/api/watcher/folders")).json();
    const tbody = document.getElementById("watched-folders-tbody");
    if (!tbody) return;

    const folders = res.folders || [];
    if (folders.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" class="py-6 text-center text-fg4">No monitored folders configured yet. Add your music directories above.</td></tr>`;
      return;
    }

    tbody.innerHTML = folders.map(f => {
      const isEnabled = f.is_enabled;
      const scanDate = f.last_scanned_at ? new Date(f.last_scanned_at * 1000).toLocaleTimeString() : "Never";
      const statusBadge = f.status === "ACTIVE"
        ? `<span class="px-2 py-0.5 rounded-full text-2xs font-bold bg-emerald-500/20 text-emerald-400">ACTIVE</span>`
        : `<span class="px-2 py-0.5 rounded-full text-2xs font-bold bg-red-500/20 text-red-400" title="${escapeHtml(f.error_message || '')}">ERROR</span>`;

      return `
        <tr class="hover:bg-hv transition-colors">
          <td class="py-3 px-4 font-mono font-medium text-fg truncate max-w-xs" title="${escapeHtml(f.path)}">
            ${escapeHtml(f.path)}
          </td>
          <td class="py-3 px-4 text-fg2 font-semibold">${escapeHtml(f.label || "--")}</td>
          <td class="py-3 px-3 text-fg3">${f.recursive ? "Recursive" : "Flat"}</td>
          <td class="py-3 px-3">${statusBadge}</td>
          <td class="py-3 px-4 font-mono text-fg3 text-2xs">${scanDate}</td>
          <td class="py-3 px-4 text-right">
            <div class="flex items-center justify-end space-x-1.5">
              <button data-on-click="rescanWatchedFolder_009c0a" data-args-click="${escapeHtml(JSON.stringify([encodeURIComponent(f.id)]))}" title="Rescan this folder" class="p-1.5 rounded-lg hover:bg-s4 text-fg3 hover:text-fg transition-colors">
                <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>
              </button>
              <button data-on-click="toggleWatchedFolderEnabled_221f1b" data-args-click="${escapeHtml(JSON.stringify([encodeURIComponent(f.id), !isEnabled]))}" title="${isEnabled ? 'Disable' : 'Enable'}" class="p-1.5 rounded-lg hover:bg-s4 ${isEnabled ? 'text-spotify-green' : 'text-fg4'} transition-colors">
                <i data-lucide="${isEnabled ? 'check-circle' : 'circle'}" class="w-3.5 h-3.5"></i>
              </button>
              <button data-on-click="removeWatchedFolderPrompt_f4854f" data-args-click="${escapeHtml(JSON.stringify([encodeURIComponent(f.id), encodeURIComponent(f.path)]))}" title="Remove from monitoring" class="p-1.5 rounded-lg hover:bg-s4 text-fg4 hover:text-red-400 transition-colors">
                <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
              </button>
            </div>
          </td>
        </tr>
      `;
    }).join("");

    lucide.createIcons();
  } catch (err) {
    console.error("Error loading watched folders:", err);
  }
}

async function addNewWatchedFolder() {
  const pathInput = document.getElementById("new-watch-path");
  const labelInput = document.getElementById("new-watch-label");
  const recInput = document.getElementById("new-watch-recursive");

  const path = pathInput.value.trim();
  const label = labelInput.value.trim();
  const recursive = recInput.checked;

  if (!path) {
    return showToast("Please specify a directory path to monitor.", "warning");
  }

  try {
    const res = await (await fetch("/api/watcher/folders/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, label, recursive })
    })).json();

    if (res.success) {
      pathInput.value = "";
      labelInput.value = "";
      UI.toast("Folder added. It will be scanned shortly.", "success", 2200);
      await loadWatcherDashboard();
    } else {
      showToast("Error adding folder: " + (res.error || "Unknown error"), "error");
    }
  } catch (err) {
    showToast("Error adding folder: " + err, "error");
  }
}

async function removeWatchedFolderPrompt(encodedId, encodedPath) {
  let fid = encodedId;
  let fpath = encodedPath;
  try { fid = decodeURIComponent(encodedId); } catch (_) {}
  try { fpath = decodeURIComponent(encodedPath); } catch (_) {}

  const ok = await UI.confirm({ title: "Stop watching this folder?", message: `${fpath}\n\nYour files are not touched; the folder is just no longer scanned.`, confirmText: "Stop watching", tone: "danger" });
  if (!ok) return;

  try {
    const res = await (await fetch("/api/watcher/folders/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_id: fid })
    })).json();

    if (res.success) {
      await loadWatcherDashboard();
    } else {
      showToast("Failed to remove folder: " + res.error, "error");
    }
  } catch (err) {
    showToast("Error removing folder: " + err, "error");
  }
}

async function toggleWatchedFolderEnabled(encodedId, shouldEnable) {
  let fid = encodedId;
  try { fid = decodeURIComponent(encodedId); } catch (_) {}

  try {
    await fetch("/api/watcher/folders/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_id: fid, enabled: shouldEnable })
    });
    await loadWatcherDashboard();
  } catch (err) {
    showToast("Error toggling folder: " + err, "error");
  }
}

async function rescanWatchedFolder(encodedId) {
  let fid = encodedId;
  try { fid = decodeURIComponent(encodedId); } catch (_) {}

  try {
    const res = await (await fetch("/api/watcher/folders/rescan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_id: fid })
    })).json();

    if (res.success) {
      await loadWatcherDashboard();
    }
  } catch (err) {
    showToast("Rescan error: " + err, "error");
  }
}

async function rescanAllWatchedFolders() {
  try {
    await fetch("/api/watcher/folders/rescan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    await loadWatcherDashboard();
  } catch (err) {
    showToast("Rescan all error: " + err, "error");
  }
}

async function loadWatcherPendingIntake() {
  try {
    const res = await (await fetch("/api/watcher/pending")).json();
    const tbody = document.getElementById("watcher-pending-tbody");
    const badge = document.getElementById("watcher-pending-badge");
    if (!tbody) return;

    const items = res.items || [];
    if (badge) badge.innerText = `${items.length} items`;

    const navBadge = document.getElementById("badge-watcher-pending");
    if (navBadge) { navBadge.innerText = items.length; navBadge.classList.toggle("hidden", items.length === 0); }
    if (items.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" class="py-6 text-center text-fg4">No pending changes awaiting intake approval. All monitored folders are in sync.</td></tr>`;
      return;
    }

    tbody.innerHTML = items.map(it => {
      let reasonBadge = `<span class="px-2 py-0.5 rounded text-2xs font-bold bg-emerald-500/20 text-emerald-400">NEW FILE</span>`;
      if (it.detection_reason === "MODIFIED_LOCAL_FILE") {
        reasonBadge = `<span class="px-2 py-0.5 rounded text-2xs font-bold bg-cyan-500/20 text-cyan-300">MODIFIED</span>`;
      } else if (it.detection_reason === "REMOVED_LOCAL_FILE") {
        reasonBadge = `<span class="px-2 py-0.5 rounded text-2xs font-bold bg-amber-500/20 text-amber-300">REMOVED</span>`;
      }

      let collisionBadge = `<span class="px-2 py-0.5 rounded text-2xs font-mono bg-s3 text-fg2">${escapeHtml(it.collision_status)}</span>`;
      if (it.collision_status === "DUPLICATE_CONTENT_EXISTS") {
        collisionBadge = `<span class="px-2 py-0.5 rounded text-2xs font-bold bg-purple-500/20 text-purple-300">DUPLICATE HASH</span>`;
      } else if (it.collision_status === "MODIFIED_IN_PLACE") {
        collisionBadge = `<span class="px-2 py-0.5 rounded text-2xs font-bold bg-cyan-500/20 text-cyan-300">UPDATED FILE</span>`;
      }

      return `
        <tr class="hover:bg-hv transition-colors">
          <td class="py-3 px-4">
            <div class="font-bold text-fg truncate max-w-xs">${escapeHtml(it.title)}</div>
            <div class="text-2xs text-fg4 font-mono truncate max-w-xs" title="${escapeHtml(it.target_path)}">${escapeHtml(it.target_path.split(/[\\/]/).pop())}</div>
          </td>
          <td class="py-3 px-4 text-fg2 truncate max-w-[140px]">${escapeHtml(it.artists)}</td>
          <td class="py-3 px-4 text-fg3 truncate max-w-[140px]">${escapeHtml(it.album || "--")}</td>
          <td class="py-3 px-4">${reasonBadge}</td>
          <td class="py-3 px-4">${collisionBadge}</td>
          <td class="py-3 px-4 text-right">
            <div class="flex items-center justify-end space-x-1.5">
              <button data-on-click="approveWatcherItem_bb7542" data-args-click="${escapeHtml(JSON.stringify([encodeURIComponent(it.id)]))}" class="px-2.5 py-1 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-black font-bold text-2xs transition-colors">
                Approve
              </button>
              <button data-on-click="dismissWatcherItem_2f3b9a" data-args-click="${escapeHtml(JSON.stringify([encodeURIComponent(it.id)]))}" class="px-2.5 py-1 rounded-lg bg-s3 hover:bg-s4 text-fg3 hover:text-red-400 text-2xs border border-line2 transition-colors">
                Dismiss
              </button>
            </div>
          </td>
        </tr>
      `;
    }).join("");

    lucide.createIcons();
  } catch (err) {
    console.error("Error loading pending watcher items:", err);
  }
}

async function approveWatcherItem(encodedId) {
  let iid = encodedId;
  try { iid = decodeURIComponent(encodedId); } catch (_) {}

  try {
    const res = await (await fetch("/api/watcher/items/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: iid })
    })).json();

    if (res.success) {
      UI.toast("Added to your library.", "success", 1800);
      await loadWatcherDashboard();
      if (typeof refreshLibrary === "function") refreshLibrary();
    } else {
      UI.toast(res.error || "That item couldn't be approved.", "error");
    }
  } catch (err) {
    showToast("Error approving item: " + err, "error");
  }
}

async function dismissWatcherItem(encodedId) {
  let iid = encodedId;
  try { iid = decodeURIComponent(encodedId); } catch (_) {}

  try {
    const res = await (await fetch("/api/watcher/items/dismiss", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: iid })
    })).json();

    if (res.success) {
      await loadWatcherDashboard();
    }
  } catch (err) {
    showToast("Error dismissing item: " + err, "error");
  }
}

async function approveAllWatcherPending() {
  try {
    const res = await (await fetch("/api/watcher/pending")).json();
    const items = res.items || [];
    if (items.length === 0) return showToast("No pending items to approve.", "warning");

    const okAll = await UI.confirm({ title: `Add ${items.length} item${items.length === 1 ? "" : "s"} to your library?`, message: "Every pending change listed below is indexed. Nothing is deleted from disk.", confirmText: `Approve ${items.length}` });
    if (!okAll) return;
    const itemIds = items.map(it => it.id);
    const appRes = await (await fetch("/api/watcher/items/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_ids: itemIds })
    })).json();

    if (appRes.success) {
      showToast(`Approved and indexed ${appRes.approved_count} items into your library!`, "success");
      await loadWatcherDashboard();
      if (typeof refreshLibrary === "function") refreshLibrary();
    }
  } catch (err) {
    showToast("Error approving items: " + err, "error");
  }
}


