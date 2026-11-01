// ================= PHASE 29 MULTI-SOURCE & ALTERNATIVES FRONTEND ENGINE =================
let unifiedCurrentScope = "all";
let unifiedSearchDebounceTimer = null;
let currentAlternativeTarget = null;

function setUnifiedScope(scope) {
  unifiedCurrentScope = scope;
  document.querySelectorAll(".scope-btn").forEach(btn => {
    const on = btn.id === `scope-btn-${scope}`;
    btn.classList.toggle("bg-inv", on);
    btn.classList.toggle("text-inv-fg", on);
    btn.classList.toggle("bg-s3", !on);
    btn.classList.toggle("text-fg2", !on);
    btn.setAttribute("aria-pressed", String(on));
  });

  const inp = document.getElementById("omni-search-input");
  if (inp && inp.value.trim()) {
    triggerUnifiedSearch(inp.value.trim());
  }
}

function handleUnifiedSearchInput(val) {
  clearTimeout(unifiedSearchDebounceTimer);
  const clearBtn = document.getElementById("btn-clear-omni-search");
  if (clearBtn) clearBtn.classList.toggle("hidden", !val);

  if (!val.trim()) {
    document.getElementById("omni-empty-state")?.classList.remove("hidden");
    document.getElementById("omni-no-results-state")?.classList.add("hidden");
    document.getElementById("omni-results-container")?.classList.add("hidden");
    return;
  }

  unifiedSearchDebounceTimer = setTimeout(() => {
    triggerUnifiedSearch(val.trim());
  }, 280);
}

function clearUnifiedSearch() {
  const inp = document.getElementById("omni-search-input");
  if (inp) {
    inp.value = "";
    handleUnifiedSearchInput("");
    inp.focus();
  }
}

let unifiedSearchSeq = 0;

async function triggerUnifiedSearch(query) {
  const seq = ++unifiedSearchSeq;
  const status = document.getElementById("omni-empty-state");
  try {
    const data = await UI.api(`/api/search/unified?q=${encodeURIComponent(query)}&scope=${unifiedCurrentScope}&entity_type=${omniActiveCategory}&limit=12`, { prefix: "Search failed" });
    if (seq !== unifiedSearchSeq) return; // an older, slower answer must not overwrite a newer one
    renderUnifiedSearchResults(data);
  } catch (err) {
    console.error("Unified search error:", err);
  }
}

function renderUnifiedSearchResults(data) {
  const results = data.results || [];
  const emptyState = document.getElementById("omni-empty-state");
  const noResState = document.getElementById("omni-no-results-state");
  const resContainer = document.getElementById("omni-results-container");

  if (emptyState) emptyState.classList.add("hidden");

  if (results.length === 0) {
    const echoEl = document.getElementById("omni-query-echo");
    if (echoEl) echoEl.innerText = data.query || "";
    if (noResState) noResState.classList.remove("hidden");
    if (resContainer) resContainer.classList.add("hidden");
    return;
  }

  if (noResState) noResState.classList.add("hidden");
  if (resContainer) resContainer.classList.remove("hidden");

  // Render Top Result Card
  const topCard = document.getElementById("omni-top-result-card");
  if (topCard && results.length > 0) {
    const top = results[0];
    const provBadge = getProviderBadgeHtml(top.provider);
    topCard.innerHTML = `
      <div class="space-y-4">
        <div class="relative w-28 h-28 rounded-2xl overflow-hidden bg-s3 shadow-2xl border border-white/10">
          <img src="${escapeHtml(top.thumbnail_url || '')}" class="w-full h-full object-cover" data-on-error="this_49d186" />
          <div class="w-full h-full hidden items-center justify-center text-spotify-green bg-[#16202c]"><i data-lucide="music" class="w-8 h-8"></i></div>
        </div>
        <div>
          <div class="flex items-center space-x-2 mb-1.5">${provBadge}</div>
          <h2 class="text-xl font-semibold text-fg group-hover:underline truncate">${escapeHtml(top.title)}</h2>
          <p class="text-xs text-fg3 truncate">${escapeHtml(top.primary_creator || 'Unknown')}</p>
        </div>
      </div>
      <div class="pt-4 flex items-center justify-between border-t border-line">
        <span class="text-2xs text-fg3 capitalize">${escapeHtml(top.entity_type)}</span>
        <div class="flex items-center space-x-2">
          ${renderItemActionButtons(top)}
        </div>
      </div>
    `;
  }

  // Render Tracks / Results List
  const tracksList = document.getElementById("omni-tracks-list");
  if (tracksList) {
    tracksList.innerHTML = results.map((item, idx) => {
      const provBadge = getProviderBadgeHtml(item.provider);
      const durStr = item.duration_ms ? formatDurationMs(item.duration_ms) : "--:--";
      return `
        <div class="group flex items-center justify-between p-2.5 rounded-xl hover:bg-hv transition-colors border border-transparent hover:border-line">
          <div class="flex items-center space-x-3 truncate">
            <span class="w-6 text-center text-xs text-fg4 font-mono">${idx + 1}</span>
            <div class="w-10 h-10 rounded-lg overflow-hidden bg-s3 shrink-0 border border-white/5 relative">
              <img src="${escapeHtml(item.thumbnail_url || '')}" class="w-full h-full object-cover" data-on-error="this_f2cb27" />
            </div>
            <div class="truncate">
              <div class="text-xs font-semibold text-fg group-hover:text-spotify-green transition-colors truncate flex items-center space-x-1.5">
                <span>${escapeHtml(item.title)}</span>
                ${item.is_alternative ? '<span class="px-1.5 py-0.2 rounded text-2xs bg-purple-900/30 text-purple-300 border border-purple-800/40">ALT</span>' : ''}
              </div>
              <div class="text-2xs text-fg3 truncate flex items-center space-x-2">
                <span>${escapeHtml(item.primary_creator || '')}</span>
                ${item.album ? `<span>•</span><span class="text-fg4 truncate">${escapeHtml(item.album)}</span>` : ''}
              </div>
            </div>
          </div>
          <div class="flex items-center space-x-3 shrink-0 ml-3">
            ${provBadge}
            <span class="text-2xs text-fg4 font-mono max-sm:hidden sm:inline">${durStr}</span>
            <div class="flex items-center space-x-1.5">
              ${renderItemActionButtons(item)}
            </div>
          </div>
        </div>
      `;
    }).join("");
  }

  if (window.lucide) {
    try { lucide.createIcons({ root: resContainer }); } catch (_) {}
  }
}

function getProviderBadgeHtml(provider) {
  const p = (provider || "spotify").toLowerCase();
  if (p === "spotify") {
    return `<span class="badge-provider-spotify px-2 py-0.5 rounded-md text-2xs font-semibold flex items-center"><span class="w-1.5 h-1.5 rounded-full bg-spotify-green mr-1"></span>Spotify</span>`;
  } else if (p === "ytmusic") {
    return `<span class="badge-provider-ytmusic px-2 py-0.5 rounded-md text-2xs font-semibold flex items-center"><span class="w-1.5 h-1.5 rounded-full bg-red-500 mr-1"></span>YT Music</span>`;
  } else if (p === "youtube") {
    return `<span class="badge-provider-youtube px-2 py-0.5 rounded-md text-2xs font-semibold flex items-center"><span class="w-1.5 h-1.5 rounded-full bg-red-600 mr-1"></span>YouTube</span>`;
  }
  return `<span class="badge-provider-local px-2 py-0.5 rounded-md text-2xs font-semibold flex items-center"><span class="w-1.5 h-1.5 rounded-full bg-purple-400 mr-1"></span>Local</span>`;
}

function renderItemActionButtons(item) {
  let btns = "";
  const itemJson = item; // passed to handlers via data-args (escaped once, when the attribute is written)

  if (item.capabilities && item.capabilities.can_embed) {
    btns += `
      <button data-on-click="openYouTubePlayerModal_bfbb03" data-args-click="${escapeHtml(JSON.stringify([itemJson]))}" title="Watch / Listen In-Page" class="p-1.5 rounded-lg bg-s3 hover:bg-red-900/40 text-red-400 hover:text-white transition-colors">
        <i data-lucide="play" class="w-3.5 h-3.5 fill-current"></i>
      </button>
    `;
  }

  if (item.capabilities && item.capabilities.can_download) {
    btns += `
      <button data-on-click="enqueueUnifiedItem_19995d" data-args-click="${escapeHtml(JSON.stringify([itemJson]))}" title="Add to Download Queue" class="p-1.5 rounded-lg bg-s3 hover:bg-spotify-green hover:text-black text-fg2 transition-colors">
        <i data-lucide="download" class="w-3.5 h-3.5"></i>
      </button>
    `;
  }

  // Cross-source alternative action (Rule B / D)
  btns += `
    <button data-on-click="openAlternativesModal_8254e2" data-args-click="${escapeHtml(JSON.stringify([itemJson]))}" title="Find cross-source alternatives" class="p-1.5 rounded-lg bg-s3 hover:bg-purple-900/40 text-purple-300 hover:text-white transition-colors">
      <i data-lucide="shuffle" class="w-3.5 h-3.5"></i>
    </button>
  `;

  return btns;
}

function formatDurationMs(ms) {
  if (!ms || ms <= 0) return "--:--";
  const sec = Math.floor(ms / 1000);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s < 10 ? '0' : ''}${s}`;
}

async function enqueueUnifiedItem(item) {
  try {
    const trackPayload = {
      id: item.provider_item_id || item.id,
      title: item.title,
      artists: item.contributors && item.contributors.length > 0 ? item.contributors : [item.primary_creator || "Unknown"],
      album: item.album || "",
      duration_ms: item.duration_ms || 0,
      cover_url: item.thumbnail_url || "",
      isrc: item.isrc || "",
      provider: item.provider || "spotify",
      provider_item_id: item.provider_item_id || item.id,
      canonical_url: item.canonical_url || "",
      is_alternative: Boolean(item.is_alternative),
      original_spotify_id: item.original_spotify_id || "",
      capabilities: item.capabilities || {}
    };

    const res = await (await fetch("/api/queue/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tracks: [trackPayload], collection_name: `${item.provider.toUpperCase()} Search` })
    })).json();

    if (res.success) {
      showToast(`Enqueued from ${item.provider.toUpperCase()}: ${item.title}`, "success");
      fetchQueueSnapshot();
    } else {
      showToast(res.error || "Failed to enqueue", "error");
    }
  } catch (err) {
    showToast("Enqueue error: " + err.message, "error");
  }
}

// Alternatives Modal Management
async function openAlternativesModal(item) {
  currentAlternativeTarget = item;
  const modal = document.getElementById("modal-alternatives");
  if (!modal) return;

  document.getElementById("alt-target-title").innerText = item.title || "Track";
  document.getElementById("alt-target-artist").innerText = item.primary_creator || (item.artists ? item.artists.join(", ") : "");
  
  modal.classList.remove("hidden");
  modal.classList.add("flex");

  document.getElementById("alt-loading").classList.remove("hidden");
  document.getElementById("alt-empty").classList.add("hidden");
  document.getElementById("alt-candidates-list").innerHTML = "";

  // Check current alternative in DB
  try {
    const checkRes = await (await fetch(`/api/alternatives/current?provider=${item.provider}&item_id=${item.provider_item_id || item.id}`)).json();
    const currCard = document.getElementById("alt-current-card");
    if (checkRes.has_alternative && checkRes.match) {
      document.getElementById("alt-current-text").innerText = `${checkRes.match.alternative_provider.toUpperCase()}: ${checkRes.match.alternative_item_id} (${Math.round((checkRes.match.confidence_score || 0)*100)}% Match)`;
      currCard.classList.remove("hidden");
    } else {
      currCard.classList.add("hidden");
    }
  } catch (_) {}

  // Request candidates
  try {
    const res = await (await fetch("/api/alternatives/find", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item })
    })).json();

    document.getElementById("alt-loading").classList.add("hidden");

    if (res.success && res.report && res.report.candidates && res.report.candidates.length > 0) {
      renderCandidateCards(res.report.candidates);
    } else {
      document.getElementById("alt-empty").classList.remove("hidden");
    }
  } catch (err) {
    document.getElementById("alt-loading").classList.add("hidden");
    document.getElementById("alt-empty").classList.remove("hidden");
    showToast("Error searching candidates: " + err.message, "error");
  }

  if (window.lucide) {
    try { lucide.createIcons({ root: modal }); } catch (_) {}
  }
}

function closeAlternativesModal() {
  const modal = document.getElementById("modal-alternatives");
  if (modal) {
    modal.classList.add("hidden");
    modal.classList.remove("flex");
  }
  currentAlternativeTarget = null;
}

function renderCandidateCards(candidates) {
  const list = document.getElementById("alt-candidates-list");
  if (!list) return;

  list.innerHTML = candidates.map(cand => {
    const item = cand.item;
    const scorePct = Math.round(cand.confidence_score * 100);
    let pillClass = "score-pill-high";
    if (scorePct < 70) pillClass = "score-pill-low";
    else if (scorePct < 85) pillClass = "score-pill-medium";

    const candJson = cand; // passed to handlers via data-args (escaped once, when the attribute is written)
    return `
      <div class="alt-candidate-card p-4 rounded-2xl bg-s2 border border-line flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div class="flex items-center space-x-3 truncate flex-1">
          <div class="w-12 h-12 rounded-xl overflow-hidden bg-s3 shrink-0 border border-white/5 relative">
            <img src="${escapeHtml(item.thumbnail_url || '')}" class="w-full h-full object-cover" data-on-error="this_f2cb27" />
          </div>
          <div class="truncate">
            <div class="flex items-center space-x-2">
              <span class="text-sm font-semibold text-fg truncate">${escapeHtml(item.title)}</span>
              ${getProviderBadgeHtml(item.provider)}
            </div>
            <div class="text-xs text-fg3 truncate flex items-center space-x-2 pt-0.5">
              <span>${escapeHtml(item.primary_creator || '')}</span>
              ${item.duration_ms ? `<span>•</span><span class="font-mono text-fg4">${formatDurationMs(item.duration_ms)}</span>` : ''}
              <span>•</span>
              <span class="text-fg3 capitalize">${cand.outcome.replace(/_/g, ' ')}</span>
            </div>
            <div class="text-2xs text-fg3 italic pt-1 truncate">
              ${escapeHtml(cand.explanation || '')}
            </div>
          </div>
        </div>
        <div class="flex items-center space-x-3 shrink-0 self-end md:self-center">
          <div class="px-2.5 py-1 rounded-full text-xs font-mono font-semibold ${pillClass}">
            ${scorePct}% Match
          </div>
          <button data-on-click="selectAlternativeCandidate_09f876" data-args-click="${escapeHtml(JSON.stringify([candJson]))}" class="px-4 py-2 rounded-xl bg-purple-600 hover:bg-purple-500 text-white font-semibold text-xs transition-colors flex items-center">
            <i data-lucide="check" class="w-3.5 h-3.5 mr-1.5 stroke-[3]"></i> Choose
          </button>
          <button data-on-click="downloadAlternativeCandidate_a1" data-args-click="${escapeHtml(JSON.stringify([candJson]))}" title="Download this match instead of the Spotify audio" class="px-4 py-2 rounded-xl bg-spotify-green hover:bg-spotify-hover text-black font-semibold text-xs transition-colors flex items-center">
            <i data-lucide="download" class="w-3.5 h-3.5 mr-1.5 stroke-[3]"></i> Download this
          </button>
        </div>
      </div>
    `;
  }).join("");

  if (window.lucide) {
    try { lucide.createIcons({ root: list }); } catch (_) {}
  }
}

async function selectAlternativeCandidate(cand) {
  if (!currentAlternativeTarget) return;
  try {
    const res = await (await fetch("/api/alternatives/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        original_item: currentAlternativeTarget,
        alternative_item: cand.item
      })
    })).json();

    if (res.success) {
      showToast(`Associated ${cand.item.provider.toUpperCase()} candidate with original item!`, "success");
      closeAlternativesModal();
    } else {
      showToast(res.error || "Failed to associate alternative", "error");
    }
  } catch (err) {
    showToast("Association error: " + err.message, "error");
  }
}

async function clearCurrentAlternative() {
  const target = currentAlternativeTarget;
  if (!target) return;
  try {
    const res = await (await fetch("/api/alternatives/clear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: target.provider, item_id: target.provider_item_id || target.id })
    })).json();
    if (res.success) {
      document.getElementById("alt-current-card").classList.add("hidden");
      showToast("Alternative association removed.", "success");
    } else {
      showToast(res.error || "Could not remove the association", "error");
    }
  } catch (err) {
    showToast("Error removing association: " + err.message, "error");
  }
}

// Explicit user action: download this candidate in place of the Spotify track, tagged as that track.
async function downloadAlternativeCandidate(cand) {
  const original = currentAlternativeTarget;
  if (!original) return;
  const chosen = cand.item;
  const item = {
    ...chosen,
    title: original.title || chosen.title,
    primary_creator: original.primary_creator || chosen.primary_creator,
    contributors: original.contributors && original.contributors.length ? original.contributors : chosen.contributors,
    album: original.album || chosen.album || "",
    thumbnail_url: original.thumbnail_url || chosen.thumbnail_url || "",
    isrc: original.isrc || chosen.isrc || "",
    duration_ms: original.duration_ms || chosen.duration_ms || 0,
    is_alternative: true,
    original_spotify_id: original.provider_item_id || original.id
  };
  try {
    // record the choice first (it is what makes this an explicit, auditable substitution) ...
    const assoc = await (await fetch("/api/alternatives/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ original_item: original, alternative_item: chosen })
    })).json();
    if (!assoc.success) {
      showToast(assoc.error || "Could not record your choice", "error");
      return;
    }
    // ... then queue the download
    await enqueueUnifiedItem(item);
    closeAlternativesModal();
  } catch (err) {
    showToast("Download error: " + err.message, "error");
  }
}

function openYouTubePlayerModal(item) {
  const modal = document.getElementById("modal-youtube-embed");
  const iframe = document.getElementById("yt-player-iframe");
  const title = document.getElementById("yt-player-title");
  const link = document.getElementById("yt-player-canonical-link");

  if (!modal || !iframe) return;

  const embedUrl = item.capabilities?.embed_url || `https://www.youtube-nocookie.com/embed/${item.provider_item_id || item.id}?autoplay=1`;
  iframe.src = embedUrl;
  if (title) title.innerText = `${item.title} — ${item.primary_creator || 'YouTube'}`;
  if (link) link.href = item.canonical_url || `https://www.youtube.com/watch?v=${item.provider_item_id || item.id}`;

  modal.classList.remove("hidden");
  modal.classList.add("flex");

  if (window.lucide) {
    try { lucide.createIcons({ root: modal }); } catch (_) {}
  }
}

function closeYouTubePlayerModal() {
  const modal = document.getElementById("modal-youtube-embed");
  const iframe = document.getElementById("yt-player-iframe");
  if (iframe) iframe.src = "";
  if (modal) {
    modal.classList.add("hidden");
    modal.classList.remove("flex");
  }
}


