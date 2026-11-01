// ================= TAB 9: LIBRARY INTELLIGENCE =================
async function loadIntelligence() {
  try {
    const res = await (await fetch("/api/intelligence/overview")).json();
    document.getElementById("intel-health-score").innerText = `${res.health_score}%`;
    document.getElementById("intel-total-tracks").innerText = res.total_tracks;
    document.getElementById("intel-total-artists").innerText = res.total_artists;
    document.getElementById("intel-total-size").innerText = `${res.total_size_mb} MB`;
    document.getElementById("intel-total-hours").innerText = `${res.total_duration_hours}h`;

    const ring = document.getElementById("intel-health-ring");
    if (ring) {
      ring.setAttribute("stroke-dasharray", `${res.health_score}, 100`);
      ring.className = res.health_score > 80 ? "text-spotify-green transition-colors duration-1000" : (res.health_score > 50 ? "text-amber-400 transition-colors duration-1000" : "text-red-400 transition-colors duration-1000");
    }

    // Last scan label
    const lastScanEl = document.getElementById("intel-last-scan-label");
    if (lastScanEl) {
      if (res.last_scan) {
        const d = new Date(res.last_scan.started_at * 1000);
        lastScanEl.innerText = `Last Scan: ${d.toLocaleDateString()} ${d.toLocaleTimeString()} (${res.last_scan.scanned_files} files, ${res.last_scan.status})`;
      } else {
        lastScanEl.innerText = "Last Scan: Never performed yet";
      }
    }

    // 6 Drill-Down Metric Cards
    const drillTags = document.getElementById("drill-missing-tags");
    if (drillTags) drillTags.innerText = res.missing_tags_count || 0;

    const drillArt = document.getElementById("drill-missing-art");
    if (drillArt) drillArt.innerText = res.missing_covers_count || 0;

    const drillUnreadable = document.getElementById("drill-unreadable");
    if (drillUnreadable) drillUnreadable.innerText = res.unreadable_tags_count || 0;

    const drillExact = document.getElementById("drill-exact-dups");
    if (drillExact) drillExact.innerText = res.exact_duplicates_count || 0;

    const drillProbable = document.getElementById("drill-probable-dups");
    if (drillProbable) drillProbable.innerText = res.probable_duplicates_count || 0;

    const drillAnomalies = document.getElementById("drill-anomalies");
    if (drillAnomalies) drillAnomalies.innerText = res.anomalous_duration_count || 0;

    // Formats Breakdown
    const fmtList = document.getElementById("intel-formats-list");
    if (fmtList) {
      fmtList.innerHTML = Object.entries(res.format_breakdown || {}).map(([fmt, count]) => `
        <span class="px-2.5 py-1 rounded-lg bg-s2 border border-line2 text-fg2 font-mono text-2xs">
          <b class="text-spotify-green uppercase">${escapeHtml(fmt)}</b>: ${count}
        </span>
      `).join("") || `<span class="text-fg4 text-2xs">No tracks indexed yet.</span>`;
    }

    // Bitrate Quality Tiers Breakdown
    const brList = document.getElementById("intel-bitrates-list");
    if (brList && res.bitrate_breakdown) {
      const b = res.bitrate_breakdown;
      brList.innerHTML = `
        <span class="px-2.5 py-1 rounded-lg bg-s2 border border-line2 text-fg2 font-mono text-2xs"><b class="text-purple-400">FLAC/Lossless</b>: ${b.lossless}</span>
        <span class="px-2.5 py-1 rounded-lg bg-s2 border border-line2 text-fg2 font-mono text-2xs"><b class="text-spotify-green">320k High</b>: ${b.high_320k}</span>
        <span class="px-2.5 py-1 rounded-lg bg-s2 border border-line2 text-fg2 font-mono text-2xs"><b class="text-blue-400">Standard</b>: ${b.standard}</span>
        <span class="px-2.5 py-1 rounded-lg bg-s2 border border-line2 text-fg2 font-mono text-2xs"><b class="text-amber-400">&lt;192k Low</b>: ${b.low}</span>
      `;
    }

    // Recommendations
    const recList = document.getElementById("intel-recommendations-list");
    if (recList) {
      recList.innerHTML = (res.recommendations || []).map(r => `
        <div class="p-3 rounded-xl bg-s2 border border-line2 flex items-center justify-between">
          <div>
            <div class="font-semibold text-fg">${escapeHtml(r.action)}</div>
            <div class="text-2xs text-fg3">${escapeHtml(r.description)}</div>
          </div>
          <span class="px-2 py-0.5 rounded-full text-2xs font-semibold ${r.severity === 'HIGH' ? 'bg-red-500/20 text-red-400' : 'bg-amber-500/20 text-amber-300'}">${escapeHtml(r.severity)}</span>
        </div>
      `).join("") || `<span class="text-emerald-400 font-medium text-xs">✓ All audio files healthy and organized!</span>`;
    }
  } catch (err) {
    console.error("Intelligence error:", err);
  }
}

// ================= TAB 10: QUALITY & FINGERPRINTS =================
async function loadQualityLab() {
  try {
    const res = await (await fetch("/api/quality/profiles")).json();
    const container = document.getElementById("quality-profiles-container");
    if (container && res.profiles) {
      container.innerHTML = Object.entries(res.profiles).map(([name, prof]) => `
        <div class="p-4 rounded-2xl bg-s2 border border-line space-y-2">
          <div class="font-semibold text-fg flex items-center justify-between">
            <span>${escapeHtml(name)}</span>
            <span class="px-2 py-0.5 rounded-full bg-spotify-green/20 text-spotify-green text-2xs font-mono uppercase">${escapeHtml(prof.format)}</span>
          </div>
          <p class="text-2xs text-fg3 leading-relaxed">${escapeHtml(prof.description)}</p>
          <div class="text-2xs text-fg4">${escapeHtml(prof.recommended_for)}</div>
        </div>
      `).join("");
    }
  } catch (err) {
    console.error("Quality Lab error:", err);
  }
}

async function runLibraryQualityAudit() {
  const btn = document.getElementById("btn-run-audit");
  btn.innerHTML = `<span class="">Inspecting Files (EBU R128)...</span>`;
  btn.disabled = true;

  try {
    const res = await (await fetch("/api/quality/audit")).json();
    const tbody = document.getElementById("quality-audit-tbody");
    if (tbody && res.audits) {
      tbody.innerHTML = res.audits.map(a => `
        <tr class="hover:bg-hv transition-colors">
          <td class="py-3 px-4 font-mono font-medium text-fg truncate max-w-xs" title="${escapeHtml(a.filename)}">${escapeHtml(a.filename)}</td>
          <td class="py-3 px-4 uppercase text-fg3 font-mono">${escapeHtml(a.stream?.codec || "MP3")}</td>
          <td class="py-3 px-4 font-mono">${a.stream?.bitrate_kbps ?? "n/a"}k</td>
          <td class="py-3 px-4 font-mono">${a.stream?.sample_rate ?? "n/a"} Hz</td>
          <td class="py-3 px-4 font-mono text-cyan-400">${a.loudness?.integrated_lufs != null ? a.loudness.integrated_lufs + " LUFS" : "n/a"}</td>
          <td class="py-3 px-4 font-mono ${a.loudness?.true_peak_dbtp >= 0.0 ? 'text-red-400 font-semibold' : 'text-fg2'}">${a.loudness?.true_peak_dbtp != null ? a.loudness.true_peak_dbtp + " dBTP" : "n/a"}</td>
          <td class="py-3 px-4">
            <span class="px-2 py-0.5 rounded-full text-2xs font-semibold ${a.status === 'EXCELLENT' ? 'bg-emerald-500/20 text-emerald-400' : (a.status === 'CLIPPING_RISK' ? 'bg-red-500/20 text-red-400' : 'bg-amber-500/20 text-amber-300')}">
              ${escapeHtml(a.status)}
            </span>
          </td>
        </tr>
      `).join("") || `<tr><td colspan="7" class="py-4 text-center text-fg4">No tracks audited.</td></tr>`;
    }
  } catch (err) {
    showToast("Audit error: " + err, "error");
  } finally {
    btn.innerHTML = `<i data-lucide="activity" class="w-3.5 h-3.5 mr-1.5"></i> Run Full Library Technical Audit`;
    btn.disabled = false;
    lucide.createIcons();
  }
}

async function scanAcousticDuplicates() {
  const btn = document.getElementById("btn-scan-fingerprints");
  btn.innerHTML = `<span class="">Computing Spectral Hashes...</span>`;
  btn.disabled = true;

  try {
    const res = await (await fetch("/api/fingerprint/duplicates?threshold=0.85")).json();
    const container = document.getElementById("acoustic-duplicates-container");
    if (container && res.duplicates) {
      container.innerHTML = res.duplicates.map(d => `
        <div class="p-3.5 rounded-xl bg-s2 border border-line flex items-center justify-between">
          <div class="space-y-1">
            <div class="font-semibold text-fg">Track A: ${escapeHtml(d.file_a.title || d.file_a.path)}</div>
            <div class="text-2xs text-fg3">Track B: ${escapeHtml(d.file_b.title || d.file_b.path)}</div>
          </div>
          <div class="text-right">
            <span class="px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 text-2xs font-mono font-semibold">${Math.round(d.similarity * 100)}% Match</span>
            <div class="text-2xs text-fg4 mt-1">${escapeHtml(d.type)}</div>
          </div>
        </div>
      `).join("") || `<div class="text-emerald-400 text-center py-4">✓ No duplicate acoustic recordings found!</div>`;
    }
  } catch (err) {
    showToast("Fingerprint error: " + err, "error");
  } finally {
    btn.innerHTML = `<i data-lucide="scan" class="w-3.5 h-3.5 mr-1.5"></i> Scan Acoustic Duplicates`;
    btn.disabled = false;
    lucide.createIcons();
  }
}

// ================= TAB 11: RELEASE COMPARISON =================
async function runReleaseComparison() {
  const a = document.getElementById("cmp-id-a").value.trim();
  const b = document.getElementById("cmp-id-b").value.trim();
  if (!a || !b) {
    showToast("Please enter both Album IDs", "warning");
    return;
  }

  const container = document.getElementById("cmp-results-container");
  container.innerHTML = `<div class="p-6 text-center text-spotify-green">Resolving releases and comparing tracks...</div>`;
  container.classList.remove("hidden");

  try {
    const res = await (await fetch("/api/release/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id_a: a, id_b: b, kind: "album" })
    })).json();

    container.innerHTML = `
      <div class="grid grid-cols-2 gap-4">
        <div class="p-4 rounded-2xl bg-s2 border border-line space-y-2">
          <h4 class="font-semibold text-sm text-fg">${escapeHtml(res.release_a.name)} (${res.release_a.track_count} tracks)</h4>
          <div class="text-xs text-fg3">${res.only_in_a.length} unique tracks in Release A</div>
        </div>
        <div class="p-4 rounded-2xl bg-s2 border border-line space-y-2">
          <h4 class="font-semibold text-sm text-fg">${escapeHtml(res.release_b.name)} (${res.release_b.track_count} tracks)</h4>
          <div class="text-xs text-fg3">${res.only_in_b.length} unique tracks in Release B</div>
        </div>
      </div>
      <div class="p-4 rounded-2xl bg-s2 border border-line space-y-2">
        <h4 class="font-semibold text-sm text-fg">Matched Tracks (${res.matched_tracks_count})</h4>
        <div class="divide-y divide-line max-h-60 overflow-y-auto">
          ${res.matched_tracks.map(t => `
            <div class="py-2 flex justify-between text-xs">
              <span class="text-fg">${escapeHtml(t.title)}</span>
              <span class="font-mono ${t.is_identical_duration ? 'text-emerald-400' : 'text-amber-300'}">Δ ${t.duration_diff_sec}s</span>
            </div>
          `).join("")}
        </div>
      </div>
    `;
  } catch (err) {
    container.innerHTML = `<div class="text-red-400 p-4">Comparison error: ${escapeHtml(err)}</div>`;
  }
}

async function fetchRepairQueue() {
  try {
    const res = await (await fetch("/api/release/repair-queue")).json();
    const tbody = document.getElementById("repair-queue-tbody");
    if (tbody) {
      tbody.innerHTML = (res.queue || []).map(q => `
        <tr class="hover:bg-hv transition-colors">
          <td class="py-3 px-4 font-mono font-medium text-fg truncate max-w-xs">${escapeHtml(q.filename)}</td>
          <td class="py-3 px-4 text-fg2">${escapeHtml(q.current_title || 'Unknown')}</td>
          <td class="py-3 px-4 text-fg2">${escapeHtml(q.current_artist || 'Unknown')}</td>
          <td class="py-3 px-4">
            ${q.issues.map(i => `<span class="px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 text-2xs mr-1">${escapeHtml(i)}</span>`).join("")}
          </td>
          <td class="py-3 px-4 text-right">
            <button data-on-click="openTagModal_38155e" data-args-click="${escapeHtml(JSON.stringify([encodeURIComponent(q.path), encodeURIComponent(q.current_title || ''), encodeURIComponent(q.current_artist || ''), encodeURIComponent(q.current_album || '')]))}" class="px-3 py-1 rounded-lg bg-spotify-green hover:bg-spotify-hover text-black font-semibold text-xs">
              Edit
            </button>
          </td>
        </tr>
      `).join("") || `<tr><td colspan="5" class="py-4 text-center text-emerald-400">✓ All local files have full metadata and cover artwork!</td></tr>`;
    }
  } catch (err) {
    console.error("Repair queue error:", err);
  }
}

// ================= TAB 12: ARTIST MONITOR =================
async function loadMonitors() {
  try {
    const aRes = await (await fetch("/api/monitors/artists")).json();
    const cRes = await (await fetch("/api/monitors/calendar")).json();

    const aList = document.getElementById("monitored-artists-list");
    if (aList && aRes.artists) {
      aList.innerHTML = aRes.artists.map(a => `
        <span class="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-xl bg-s2 border border-line2 text-fg">
          <span>${escapeHtml(a.name)}</span>
          <button data-on-click="deleteMonitoredArtist_59cdd4" data-args-click="${escapeHtml(JSON.stringify([encodeURIComponent(a.id)]))}" class="text-fg4 hover:text-red-400 ml-1.5"><i data-lucide="x" class="w-3.5 h-3.5"></i></button>
        </span>
      `).join("") || `<span class="text-fg4">No artists monitored yet.</span>`;
    }

    const cTbody = document.getElementById("release-calendar-tbody");
    if (cTbody && cRes.calendar) {
      cTbody.innerHTML = cRes.calendar.map(c => `
        <tr class="hover:bg-hv transition-colors">
          <td class="py-3 px-4 font-semibold text-fg">${escapeHtml(c.title)}</td>
          <td class="py-3 px-4 text-fg2">${escapeHtml(c.artist_name)}</td>
          <td class="py-3 px-4 uppercase text-fg3 font-mono text-2xs">${escapeHtml(c.release_type)}</td>
          <td class="py-3 px-4 font-mono text-fg2">${escapeHtml(c.release_date)}</td>
          <td class="py-3 px-4"><span class="px-2 py-0.5 rounded-full bg-spotify-green/20 text-spotify-green text-2xs font-semibold">${escapeHtml(c.status)}</span></td>
          <td class="py-3 px-4 text-right">
            <button data-on-click="fetchLinkDirectly_bde04b" data-args-click="${escapeHtml(JSON.stringify([encodeURIComponent(c.spotify_url)]))}" class="px-3 py-1 rounded-lg bg-spotify-green hover:bg-spotify-hover text-black font-semibold text-xs">
              Fetch & Download
            </button>
          </td>
        </tr>
      `).join("") || `<tr><td colspan="6" class="py-4 text-center text-fg4">No new releases in calendar.</td></tr>`;
    }
    lucide.createIcons();
  } catch (err) {
    console.error("Monitors error:", err);
  }
}

async function addMonitoredArtist() {
  const idEl = document.getElementById("monitor-artist-id");
  const nameEl = document.getElementById("monitor-artist-name");
  const id = idEl.value.trim(), name = nameEl.value.trim();
  if (!id || !name) { UI.toast("Enter both the Spotify artist ID (or link) and a name.", "warning"); (id ? nameEl : idEl).focus(); return; }
  try {
    await UI.api("/api/monitors/artists/add", { body: { id, name, auto_download: false }, prefix: "Could not add the artist" });
    idEl.value = ""; nameEl.value = "";
    UI.toast(`Now monitoring ${name}.`, "success", 2200);
    loadMonitors();
  } catch (_) { /* reported */ }
}

async function deleteMonitoredArtist(id) {
  let decodedId = id;
  try { decodedId = decodeURIComponent(id); } catch (_) { /* already decoded */ }
  const ok = await UI.confirm({ title: "Stop monitoring this artist?", message: "Their releases are no longer checked. Already-detected releases stay in the calendar.", confirmText: "Stop monitoring", tone: "danger" });
  if (!ok) return;
  try {
    await UI.api("/api/monitors/artists/delete", { body: { id: decodedId }, prefix: "Could not remove the artist" });
    loadMonitors();
  } catch (_) { /* reported */ }
}

async function checkAllMonitorsNow() {
  const btn = document.getElementById("btn-check-monitors");
  btn.innerHTML = `<span class="">Checking Spotify Discographies...</span>`;
  btn.disabled = true;
  try {
    const res = await (await fetch("/api/monitors/check", { method: "POST" })).json();
    showToast(`Checked ${res.artists_checked} artist${res.artists_checked === 1 ? "" : "s"}: ${res.new_releases_found} new release${res.new_releases_found === 1 ? "" : "s"} found.`, "success");
    loadMonitors();
  } catch (err) {
    showToast("Error checking monitors: " + err, "error");
  } finally {
    btn.innerHTML = `<i data-lucide="refresh-cw" class="w-3.5 h-3.5 mr-1.5"></i> Check For New Releases Now`;
    btn.disabled = false;
    lucide.createIcons();
  }
}

function fetchLinkDirectly(url) {
  let decodedUrl = url;
  try { decodedUrl = decodeURIComponent(url); } catch (_) {}
  switchTab("fetch");
  document.getElementById("multiline-input").value = decodedUrl;
  updateLinkHint();
  requestStagingPreview();
}

// ================= TAB 13: STORAGE OPTIMIZER =================
async function cleanTempBuffers() {
  try {
    const res = await (await fetch("/api/optimizer/clean-temp", { method: "POST" })).json();
    showToast(`Removed ${res.deleted_files} temporary file${res.deleted_files === 1 ? "" : "s"} (${res.reclaimed_mb} MB).` + (res.failed_files ? `\n${res.failed_files} couldn't be deleted because they are in use. Close any running download and try again.` : ""), res.failed_files ? "warning" : "success");
    fetchStatus();
  } catch (err) {
    showToast("Cleanup error: " + err, "error");
  }
}

// ================= TAB 13: STORAGE OPTIMIZER & SAFE DUPLICATE DETECTION =================
let currentDupTab = "exact";
let currentInspectedMeta = {};
let currentOrganizerPlan = null;

function switchDuplicateTab(tab) {
  currentDupTab = tab;
  const btnExact = document.getElementById("btn-dup-exact");
  const btnProb = document.getElementById("btn-dup-probable");

  if (tab === "exact") {
    btnExact.className = "px-3.5 py-1.5 rounded-xl bg-spotify-green/20 text-spotify-green font-semibold text-xs transition-colors";
    btnProb.className = "px-3.5 py-1.5 rounded-xl bg-s3 text-fg3 hover:text-fg font-medium text-xs transition-colors";
    loadExactDuplicates();
  } else {
    btnProb.className = "px-3.5 py-1.5 rounded-xl bg-purple-500/20 text-purple-300 font-semibold text-xs transition-colors";
    btnExact.className = "px-3.5 py-1.5 rounded-xl bg-s3 text-fg3 hover:text-fg font-medium text-xs transition-colors";
    loadProbableDuplicates();
  }
}

function drillDownToDuplicates(tab) {
  switchTab("optimizer");
  switchDuplicateTab(tab);
}

async function loadExactDuplicates() {
  const container = document.getElementById("optimizer-duplicates-list");
  container.innerHTML = `<div class="p-6 text-center text-spotify-green">Calculating SHA-256 cryptographic hashes...</div>`;

  try {
    const res = await (await fetch("/api/duplicates/exact")).json();
    const dups = res.duplicates || [];
    document.getElementById("dup-summary-label").innerText = `${dups.length} exact duplicate groups identified`;

    if (dups.length === 0) {
      container.innerHTML = `<div class="text-emerald-400 text-center py-8">✓ Zero bit-for-bit (SHA-256) duplicate files detected!</div>`;
      return;
    }

    container.innerHTML = dups.map(g => `
      <div class="p-4 rounded-2xl bg-s2 border border-line space-y-3">
        <div class="flex items-center justify-between">
          <div class="space-y-0.5">
            <span class="font-semibold text-fg text-xs">${g.count} Bit-for-Bit Identical Copies (${Math.round(g.size_bytes / 1024 / 1024 * 10) / 10} MB each)</span>
            <div class="text-2xs text-fg4 font-mono">SHA-256: ${escapeHtml(g.sha256.substring(0, 24))}...</div>
          </div>
          <span class="px-2 py-0.5 rounded-full bg-spotify-green/20 text-spotify-green text-2xs font-semibold">100% Cryptographic Match</span>
        </div>

        <div class="space-y-2">
          ${g.files.map(f => {
            const isKeep = f.path === g.recommended_keep_path;
            return `
              <div class="p-2.5 rounded-xl ${isKeep ? 'bg-[#18241b] border border-emerald-800/40' : 'bg-s1 border border-line'} flex items-center justify-between text-xs">
                <div class="truncate max-w-lg">
                  <div class="font-mono text-2xs text-fg truncate" title="${escapeHtml(f.path)}">${escapeHtml(f.path)}</div>
                  <div class="text-2xs text-fg3">${f.bitrate_kbps ? f.bitrate_kbps + ' kbps • ' : ''}${f.format.toUpperCase()}</div>
                </div>
                <div class="flex items-center space-x-2 shrink-0">
                  ${isKeep ? `
                    <span class="px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 text-2xs font-semibold">✓ Recommended to Keep</span>
                  ` : `
                    <button data-on-click="moveDuplicateToBackup_114185" data-args-click="${escapeHtml(JSON.stringify([encodeURIComponent(f.path)]))}" class="px-2.5 py-1 rounded-lg bg-s3 hover:bg-s4 text-amber-300 text-2xs font-semibold border border-amber-600/30">
                      Move to Backup
                    </button>
                    <button data-on-click="deleteDuplicateSafe_7cfde6" data-args-click="${escapeHtml(JSON.stringify([encodeURIComponent(f.path)]))}" class="px-2.5 py-1 rounded-lg bg-red-950/40 hover:bg-red-900/40 text-red-400 text-2xs font-semibold border border-red-800/40">
                      Delete permanently
                    </button>
                  `}
                </div>
              </div>
            `;
          }).join("")}
        </div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<div class="text-red-400 text-center py-4">Error loading duplicates: ${escapeHtml(err)}</div>`;
  }
}

async function loadProbableDuplicates() {
  const container = document.getElementById("optimizer-duplicates-list");
  container.innerHTML = `<div class="p-6 text-center text-purple-400">Clustering normalized recording titles & durations...</div>`;

  try {
    const res = await (await fetch("/api/duplicates/probable")).json();
    const dups = res.duplicates || [];
    document.getElementById("dup-summary-label").innerText = `${dups.length} probable recording clusters found`;

    if (dups.length === 0) {
      container.innerHTML = `<div class="text-emerald-400 text-center py-8">✓ Zero probable recording duplicates detected!</div>`;
      return;
    }

    container.innerHTML = dups.map(g => `
      <div class="p-4 rounded-2xl bg-s2 border border-line space-y-3">
        <div class="flex items-center justify-between">
          <div>
            <div class="font-semibold text-fg text-xs">${escapeHtml(g.normalized_artist.toUpperCase())} — ${escapeHtml(g.normalized_title.toUpperCase())}</div>
            <div class="text-2xs text-fg3">${g.count} variants • Reason: ${escapeHtml(g.retention_reason)}</div>
          </div>
          <span class="px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 text-2xs font-semibold">Probable Duplicate</span>
        </div>

        <div class="space-y-2">
          ${g.files.map(f => {
            const isKeep = f.path === g.recommended_keep_path;
            return `
              <div class="p-2.5 rounded-xl ${isKeep ? 'bg-[#211a28] border border-purple-800/40' : 'bg-s1 border border-line'} flex items-center justify-between text-xs">
                <div class="truncate max-w-lg">
                  <div class="font-semibold text-fg truncate">${escapeHtml(f.title)} (${escapeHtml(f.artist)})</div>
                  <div class="text-2xs text-fg3 font-mono truncate">${escapeHtml(f.path)}</div>
                  <div class="text-2xs text-fg3">${f.format.toUpperCase()} • ${f.bitrate_kbps || 320} kbps • ${f.duration_sec}s</div>
                </div>
                <div class="flex items-center space-x-2 shrink-0">
                  ${isKeep ? `
                    <span class="px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 text-2xs font-semibold">★ Highest Quality Variant</span>
                  ` : `
                    <button data-on-click="moveDuplicateToBackup_114185" data-args-click="${escapeHtml(JSON.stringify([encodeURIComponent(f.path)]))}" class="px-2.5 py-1 rounded-lg bg-s3 hover:bg-s4 text-amber-300 text-2xs font-semibold border border-amber-600/30">
                      Move to Backup
                    </button>
                  `}
                </div>
              </div>
            `;
          }).join("")}
        </div>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<div class="text-red-400 text-center py-4">Error loading recording duplicates: ${escapeHtml(err)}</div>`;
  }
}

async function moveDuplicateToBackup(encodedPath) {
  let p = encodedPath;
  try { p = decodeURIComponent(encodedPath); } catch (_) {}
  try {
    const res = await (await fetch("/api/duplicates/move-backup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: p })
    })).json();

    if (res.success) {
      exportedToast(`Moved the redundant copy to the backup folder:\n${res.backup_path}`, res.backup_path);
      if (currentDupTab === "exact") loadExactDuplicates();
      else loadProbableDuplicates();
      refreshLibrary();
      loadIntelligence();
    } else {
      showToast("Move failed: " + res.error, "error");
    }
  } catch (err) {
    showToast("Backup error: " + err, "error");
  }
}

async function deleteDuplicateSafe(encodedPath) {
  let p = encodedPath;
  try { p = decodeURIComponent(encodedPath); } catch (_) {}
  const ok = await UI.confirm({
    title: "Delete this copy permanently?",
    message: `This bypasses the Recycle Bin. A safety check confirms another identical copy remains.\n\n${p}`,
    confirmText: "Delete permanently", cancelText: "Keep it", tone: "danger"
  });
  if (!ok) return;

  try {
    const res = await (await fetch("/api/duplicates/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: p })
    })).json();

    if (res.success) {
      showToast("Duplicate copy deleted.", "success");
      loadExactDuplicates();
      refreshLibrary();
      loadIntelligence();
    } else {
      showToast("Delete rejected: " + res.error, "error");
    }
  } catch (err) {
    showToast("Delete error: " + err, "error");
  }
}

async function refreshHashDuplicates() {
  loadExactDuplicates();
}

async function deleteDuplicateFile(path) {
  deleteDuplicateSafe(encodeURIComponent(path));
}

// ================= TECHNICAL FILE INSPECTOR MODAL =================
async function openFileInspector(encodedPath) {
  const p = decodeURIComponent(encodedPath);
  currentInspectedPath = p;

  const modal = document.getElementById("file-inspector-modal");
  modal.classList.remove("hidden");

  try {
    const res = await (await fetch(`/api/library/inspect?path=${encodeURIComponent(p)}`)).json();
    document.getElementById("inspect-filename").innerText = res.filename;
    document.getElementById("inspect-path").innerText = res.path;

    const t = res.technical || {};
    document.getElementById("inspect-codec").innerText = (t.codec || "--").toUpperCase();
    document.getElementById("inspect-bitrate").innerText = t.bitrate_kbps ? `${t.bitrate_kbps} kbps` : "--";
    document.getElementById("inspect-sample-rate").innerText = t.sample_rate ? `${t.sample_rate} Hz` : "--";
    document.getElementById("inspect-channels").innerText = t.channels === 1 ? "Mono (1ch)" : (t.channels === 2 ? "Stereo (2ch)" : `${t.channels || 2}ch`);
    document.getElementById("inspect-bit-depth").innerText = t.bit_depth ? `${t.bit_depth}-bit` : "--";
    document.getElementById("inspect-duration").innerText = t.duration_sec ? UI.fmtDuration(t.duration_sec) : "--";

    const m = res.metadata || {};
    currentInspectedMeta = m;
    document.getElementById("inspect-has-art").innerText = m.has_artwork ? "Yes (Embedded/Folder)" : "No Artwork";
    document.getElementById("inspect-tag-status").innerText = res.tag_status || "VALID";

    document.getElementById("inspect-title").innerText = m.title || "--";
    document.getElementById("inspect-artist").innerText = m.artist || "--";
    document.getElementById("inspect-album").innerText = m.album || "--";
    document.getElementById("inspect-album-artist").innerText = m.album_artist || "--";
    document.getElementById("inspect-track-num").innerText = m.track_number ? `${m.track_number}${m.total_tracks ? '/' + m.total_tracks : ''}` : "--";
    document.getElementById("inspect-year").innerText = m.year || "--";

    const issuesContainer = document.getElementById("inspect-issues-container");
    if (res.issues && res.issues.length > 0) {
      issuesContainer.innerHTML = res.issues.map(iss => `
        <div class="p-2 rounded-lg bg-amber-950/40 border border-amber-800/40 text-amber-300 text-2xs flex items-center space-x-1.5">
          <i data-lucide="alert-triangle" class="w-3.5 h-3.5 shrink-0"></i>
          <span>${escapeHtml(iss)}</span>
        </div>
      `).join("");
    } else {
      issuesContainer.innerHTML = `<div class="text-emerald-400 text-2xs">✓ No defects or anomalies detected in audio tags or streams.</div>`;
    }
    lucide.createIcons();
  } catch (err) {
    showToast("Inspect error: " + err, "error");
  }
}

function closeInspectorModal() {
  document.getElementById("file-inspector-modal").classList.add("hidden");
}

function openTagModalFromInspector() {
  if (!currentInspectedPath) return;
  closeInspectorModal();
  const im = currentInspectedMeta || {};
  openTagModal(encodeURIComponent(currentInspectedPath), encodeURIComponent(im.title || ""), encodeURIComponent(im.artist || ""), encodeURIComponent(im.album || ""));
}

// ================= PROPOSED-VS-CURRENT METADATA REPAIR =================
async function openMetadataRepairModal(encodedPath) {
  const p = decodeURIComponent(encodedPath);
  document.getElementById("repair-file-path").value = p;
  document.getElementById("repair-target-filename").innerText = p.split(/[\/\\]/).pop();

  const modal = document.getElementById("metadata-repair-modal");
  modal.classList.remove("hidden");

  const tbody = document.getElementById("repair-fields-tbody");
  tbody.innerHTML = `<tr><td colspan="5" class="py-8 text-center text-spotify-green">Calculating proposed tags...</td></tr>`;

  try {
    const res = await (await fetch("/api/metadata/repair/propose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: p })
    })).json();

    if (!res.success) throw new Error(res.error || "Failed to generate proposals");

    const fields = res.proposal.fields || [];
    tbody.innerHTML = fields.map(f => `
      <tr class="hover:bg-hv transition-colors">
        <td class="py-2.5 px-3 text-center">
          <input type="checkbox" class="repair-field-chk accent-spotify-green cursor-pointer" data-field="${f.field}"
            data-value="${escapeHtml(f.proposed)}" ${f.is_different ? 'checked' : ''} />
        </td>
        <td class="py-2.5 px-3 font-semibold text-fg2">${escapeHtml(f.label)}</td>
        <td class="py-2.5 px-3 text-fg3 font-mono text-2xs truncate max-w-[150px]">${escapeHtml(f.current || "--")}</td>
        <td class="py-2.5 px-3 font-mono text-2xs ${f.is_different ? 'text-spotify-green font-semibold' : 'text-fg2'} truncate max-w-[150px]">
          ${escapeHtml(f.proposed || "--")}
        </td>
        <td class="py-2.5 px-3 text-2xs text-fg3">
          <div>${escapeHtml(f.source)}</div>
          <div class="text-spotify-green">${Math.round(f.confidence * 100)}% Conf</div>
        </td>
      </tr>
    `).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="py-6 text-center text-red-400">Error: ${escapeHtml(err.message)}</td></tr>`;
  }
}

function closeMetadataRepairModal() {
  document.getElementById("metadata-repair-modal").classList.add("hidden");
}

async function applyApprovedMetadataRepair() {
  const p = document.getElementById("repair-file-path").value;
  const checkboxes = document.querySelectorAll(".repair-field-chk:checked");
  if (checkboxes.length === 0) return showToast("Select at least one field to apply.", "warning");

  const approved = {};
  checkboxes.forEach(chk => {
    approved[chk.dataset.field] = chk.dataset.value;
  });

  const btn = document.getElementById("btn-apply-repair");
  btn.disabled = true;
  btn.innerText = "Applying & Verifying...";

  try {
    const res = await (await fetch("/api/metadata/repair/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: p, approved_fields: approved })
    })).json();

    if (res.success) {
      showToast("✓ Metadata successfully updated and verified!", "success");
      closeMetadataRepairModal();
      refreshLibrary();
      loadIntelligence();
    } else {
      showToast("Repair error: " + res.error, "error");
    }
  } catch (err) {
    showToast("Connection error: " + err, "error");
  } finally {
    btn.disabled = false;
    btn.innerText = "Apply Approved Fields";
  }
}

// ================= BULK MUSIC LIBRARY REORGANIZER =================
function openBulkOrganizerModal() {
  document.getElementById("bulk-organizer-modal").classList.remove("hidden");
}

function closeBulkOrganizerModal() {
  document.getElementById("bulk-organizer-modal").classList.add("hidden");
}

async function previewBulkOrganizer() {
  const tpl = document.getElementById("organizer-template-input").value.trim();
  const policy = document.getElementById("organizer-collision-policy").value;

  const tbody = document.getElementById("organizer-preview-tbody");
  tbody.innerHTML = `<tr><td colspan="3" class="py-8 text-center text-spotify-green">Calculating dry-run organization preview...</td></tr>`;

  try {
    const res = await (await fetch("/api/organizer/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template: tpl, collision_strategy: policy })
    })).json();

    if (!res.success) throw new Error(res.error || "Preview failed");

    currentOrganizerPlan = res.plan;
    document.getElementById("organizer-summary-ribbon").classList.remove("hidden");
    document.getElementById("org-total-files").innerText = res.plan.total_files;
    document.getElementById("org-will-move").innerText = res.plan.will_move;
    document.getElementById("org-unchanged").innerText = res.plan.unchanged;
    document.getElementById("org-collisions").innerText = res.plan.collisions;

    document.getElementById("btn-execute-organizer").disabled = res.plan.will_move === 0;

    tbody.innerHTML = res.plan.moves.map(m => {
      let statusBadge = `<span class="px-2 py-0.5 rounded text-2xs font-semibold bg-gray-800 text-fg3">Unchanged</span>`;
      if (m.status === "WILL_MOVE") {
        statusBadge = `<span class="px-2 py-0.5 rounded text-2xs font-semibold bg-emerald-950/60 text-emerald-400 border border-emerald-800/40">Will Move</span>`;
      } else if (m.status === "COLLISION_SKIP") {
        statusBadge = `<span class="px-2 py-0.5 rounded text-2xs font-semibold bg-amber-950/60 text-amber-300 border border-amber-800/40">Collision (Skip)</span>`;
      } else if (m.status === "COLLISION_OVERWRITE_OR_RENAME") {
        statusBadge = `<span class="px-2 py-0.5 rounded text-2xs font-semibold bg-red-950/60 text-red-400 border border-red-800/40">Collision (Rename/Ovw)</span>`;
      }

      return `
        <tr class="hover:bg-hv transition-colors">
          <td class="py-2 px-3 font-mono text-2xs text-fg3 truncate max-w-xs" title="${escapeHtml(m.current_path)}">
            ${escapeHtml(m.filename)}
          </td>
          <td class="py-2 px-3 font-mono text-2xs text-fg truncate max-w-sm" title="${escapeHtml(m.target_path)}">
            ${escapeHtml(m.target_path)}
          </td>
          <td class="py-2 px-3">${statusBadge}</td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="3" class="py-6 text-center text-red-400">Preview Error: ${escapeHtml(err.message)}</td></tr>`;
  }
}

async function executeBulkOrganizer() {
  if (!currentOrganizerPlan || !currentOrganizerPlan.moves) return;
  const moves = currentOrganizerPlan.moves.filter(m => m.status === "WILL_MOVE" || m.status.startsWith("COLLISION"));
  if (moves.length === 0) return showToast("No moves to execute.", "info");

  const okMove = await UI.confirm({
    title: `Move ${moves.length} file${moves.length === 1 ? "" : "s"}?`,
    message: "Files are renamed and moved on disk according to the preview. You can undo the whole batch afterwards.",
    confirmText: `Move ${moves.length} files`
  });
  if (!okMove) return;

  const btn = document.getElementById("btn-execute-organizer");
  btn.disabled = true;
  btn.innerText = "Moving Files...";

  const policy = document.getElementById("organizer-collision-policy").value;

  try {
    const res = await (await fetch("/api/organizer/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ moves: moves, collision_strategy: policy })
    })).json();

    if (res.success) {
      UI.store.set("organizer.lastBatch", { id: res.result.batch_id, moved: res.result.moved_count, at: Date.now() });
      refreshOrganizerUndo();
      UI.toast(`Reorganised ${res.result.moved_count} file${res.result.moved_count === 1 ? "" : "s"}${res.result.error_count ? ` (${res.result.error_count} errors)` : ""}.`,
        res.result.error_count ? "warning" : "success", undefined, { action: { label: "Undo", onClick: () => rollbackBulkOrganizer(true) } });
      closeBulkOrganizerModal();
      refreshLibrary();
    } else {
      showToast("Execution error: " + res.error, "error");
    }
  } catch (err) {
    showToast("Connection error: " + err, "error");
  } finally {
    btn.disabled = false;
    btn.innerText = "Execute Approved Moves";
  }
}

function refreshOrganizerUndo() {
  const btn = document.getElementById("btn-organizer-undo");
  if (!btn) return;
  const last = UI.store.get("organizer.lastBatch", null);
  btn.title = last ? `Undo the reorganise from ${new Date(last.at).toLocaleString()} (${last.moved} files)` : "Undo a reorganise by batch ID";
}

async function rollbackBulkOrganizer(skipConfirm = false) {
  const last = UI.store.get("organizer.lastBatch", null);
  let batchId = last && last.id;
  if (!batchId) {
    batchId = await UI.prompt({ title: "Undo a reorganise", message: "No recent reorganise was recorded on this computer. Enter a batch ID to undo (it looks like batch_org_…).", label: "Batch ID", placeholder: "batch_org_…", confirmText: "Undo batch" });
    if (!batchId) return;
  } else if (skipConfirm !== true) {
    const ok = await UI.confirm({ title: "Undo the last reorganise?", message: `${last.moved} file${last.moved === 1 ? "" : "s"} moved on ${new Date(last.at).toLocaleString()} go back to their original locations.`, confirmText: "Undo reorganise" });
    if (!ok) return;
  }
  try {
    const res = await UI.api("/api/organizer/rollback", { body: { batch_id: batchId }, prefix: "Undo failed" });
    UI.store.set("organizer.lastBatch", null);
    refreshOrganizerUndo();
    UI.toast(`Restored ${res.restored_count} file${res.restored_count === 1 ? "" : "s"} to their original locations.`, "success");
    refreshLibrary();
  } catch (_) { /* reported */ }
}

// ================= ONE-CLICK DUPLICATE CLEANUP =================
let dedupPlanData = null;

async function dedupPlan() {
  const btn = document.getElementById("btn-dedup-plan");
  const box = document.getElementById("dedup-plan");
  await UI.busy(btn, "Looking…", async () => {
    try {
      dedupPlanData = await UI.api("/api/duplicates/resolve/plan", { prefix: "Couldn't check for duplicates" });
    } catch (_) { return; }
    renderDedupPlan();
    box.classList.remove("hidden");
  });
}

function renderDedupPlan() {
  const box = document.getElementById("dedup-plan");
  const plan = dedupPlanData;
  if (!plan) return;
  if (!plan.group_count) {
    box.innerHTML = `<p class="text-xs text-emerald-400">✓ Nothing to clean up automatically.${plan.needs_review ? ` ${plan.needs_review} group${plan.needs_review === 1 ? "" : "s"} of similar songs need your own decision (use the lists below).` : ""}</p>`;
    return;
  }
  const rows = plan.groups.map((g, i) => `
    <li class="p-3 rounded-xl bg-s2 border border-line space-y-1.5">
      <label class="flex items-start gap-3 cursor-pointer">
        <input type="checkbox" class="dedup-group mt-1 accent-spotify-green" data-index="${i}" checked data-on-change="dedupToggle" />
        <span class="min-w-0 flex-1">
          <span class="block text-sm font-semibold text-fg truncate">${escapeHtml(g.title)}${g.artist ? ` <span class="font-normal text-fg3">· ${escapeHtml(g.artist)}</span>` : ""}${g.needs_review ? ` <span class="ml-1 px-1.5 py-0.5 rounded bg-amber-950/60 text-amber-300 border border-amber-800/40 text-2xs font-semibold">another copy needs your review</span>` : ""}</span>
          <span class="block text-xs text-emerald-300 truncate" title="${escapeHtml(g.keep.path)}">Keep: ${escapeHtml(g.keep.filename)} <span class="text-fg3">(${escapeHtml(g.keep.quality)})</span></span>
          ${g.remove.map((r) => `<span class="block text-xs text-fg3 truncate" title="${escapeHtml(r.path)}">Move aside: ${escapeHtml(r.filename)} <span class="text-fg4">(${escapeHtml(r.quality)}) · ${escapeHtml(r.reason)}</span></span>`).join("")}
        </span>
      </label>
    </li>`).join("");
  document.getElementById("dedup-plan").innerHTML = `
    <div class="flex flex-wrap items-center justify-between gap-3">
      <p id="dedup-summary" class="text-xs text-fg2" role="status"></p>
      <button type="button" id="btn-dedup-apply" data-on-click="dedupApply" class="px-4 py-2 rounded-xl bg-spotify-green hover:bg-spotify-hover text-black font-semibold text-xs disabled:opacity-40 disabled:cursor-not-allowed"></button>
    </div>
    <ul class="space-y-2 max-h-96 overflow-auto pr-1">${rows}</ul>`;
  dedupToggle();
}

function dedupSelectedPairs() {
  const pairs = [];
  document.querySelectorAll("#dedup-plan .dedup-group:checked").forEach((cb) => {
    const g = dedupPlanData.groups[Number(cb.dataset.index)];
    g.remove.forEach((r) => pairs.push({ keep: g.keep.path, remove: r.path, size: r.size_bytes }));
  });
  return pairs;
}

function dedupToggle() {
  const pairs = dedupSelectedPairs();
  const bytes = pairs.reduce((n, p) => n + (p.size || 0), 0);
  const summary = document.getElementById("dedup-summary");
  const apply = document.getElementById("btn-dedup-apply");
  if (!summary || !apply) return;
  const plan = dedupPlanData;
  summary.textContent = `${pairs.length} of ${plan.file_count} file${plan.file_count === 1 ? "" : "s"} selected · ${UI.fmtBytes(bytes)} can be freed` + (plan.needs_review ? ` · ${plan.needs_review} group${plan.needs_review === 1 ? " is" : "s are"} not included because the copies differ in quality` : "");
  apply.textContent = `Move ${pairs.length} file${pairs.length === 1 ? "" : "s"} aside…`;
  apply.disabled = pairs.length === 0;
}

async function dedupApply() {
  const pairs = dedupSelectedPairs();
  if (!pairs.length) return;
  const bytes = pairs.reduce((n, p) => n + (p.size || 0), 0);
  const ok = await UI.confirm({
    title: `Move ${pairs.length} duplicate${pairs.length === 1 ? "" : "s"} aside?`,
    message: `The best copy of each song stays where it is. The other ${pairs.length} file${pairs.length === 1 ? "" : "s"} (${UI.fmtBytes(bytes)}) go to Downloads → _duplicates_backup. Nothing is deleted and you can undo this.`,
    confirmText: "Move aside"
  });
  if (!ok) return;
  try {
    const res = await UI.api("/api/duplicates/resolve/apply", { body: { pairs: pairs.map(({ keep, remove }) => ({ keep, remove })) }, prefix: "Cleanup failed" });
    const left = res.refused.length ? `\n${res.refused.length} file${res.refused.length === 1 ? " was" : "s were"} left alone: ${res.refused[0].reason}.` : "";
    UI.toast(`Moved ${res.moved} duplicate${res.moved === 1 ? "" : "s"} aside (${UI.fmtBytes(res.reclaimed_bytes)}).${left}`, res.refused.length ? "warning" : "success", 12000,
      res.batch_id ? { action: { label: "Undo", onClick: () => dedupUndo(res.batch_id) } } : undefined);
  } catch (_) { return; }
  dedupPlanData = null;
  document.getElementById("dedup-plan").classList.add("hidden");
  refreshDedupUndo();
  refreshHashDuplicates();
  if (typeof refreshLibrary === "function") refreshLibrary();
}

async function refreshDedupUndo() {
  const btn = document.getElementById("btn-dedup-undo");
  if (!btn) return;
  try {
    const { last } = await UI.api("/api/duplicates/resolve/last", { quiet: true });
    btn.classList.toggle("hidden", !last);
    if (last) {
      btn.dataset.batch = last.batch_id;
      btn.textContent = `Undo last cleanup (${last.files} file${last.files === 1 ? "" : "s"})`;
    }
  } catch (_) { /* the button simply stays as it was */ }
}

async function dedupUndo(batchId) {
  const id = typeof batchId === "string" ? batchId : (document.getElementById("btn-dedup-undo") || {}).dataset?.batch;
  if (!id) return;
  try {
    const res = await UI.api("/api/duplicates/resolve/undo", { body: { batch_id: id }, prefix: "Undo failed" });
    UI.toast(`Restored ${res.restored} file${res.restored === 1 ? "" : "s"} to where they were.`, "success");
  } catch (_) { return; }
  refreshDedupUndo();
  refreshHashDuplicates();
  if (typeof refreshLibrary === "function") refreshLibrary();
}
