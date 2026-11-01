// ================= INTEGRATED AUDIO PLAYER =================
// Repeat is tri-state (off / all / one); shuffle walks a shuffled "bag" so nothing repeats until every
// track has played. Volume, speed and the last track survive restarts; OS media keys work via MediaSession.
let isPlayerShuffle = false;
let repeatMode = "off";            // "off" | "all" | "one"
let shuffleBag = [];
let playHistory = [];
let isSeeking = false;
let seekTimer = null;
let consecutiveErrors = 0;
let advancedAutomatically = false;   // true only while the player moves on by itself (track ended / skipped after an error)
let currentTrackAuto = false;
let currentTrackMeta = null;       // { path, title, artist, album }
let lastPersist = 0;

function formatTimeSec(sec) { return UI.fmtDuration(sec); }
const audioEl = () => document.getElementById("audio-element");

function setPlayIcon(playing) {
  const btn = document.getElementById("btn-player-playpause");
  const icon = document.getElementById("icon-player-playpause");
  if (!btn || !icon) return;
  icon.setAttribute("data-lucide", playing ? "pause" : "play");
  btn.setAttribute("aria-label", playing ? "Pause" : "Play");
  UI.icons(btn);
}

function initAudioPlayer() {
  const audio = audioEl();
  if (!audio) return;
  const seek = document.getElementById("player-seek");
  const timeCur = document.getElementById("player-time-current");
  const timeTot = document.getElementById("player-time-total");

  const savedVol = UI.store.get("player.volume", 1);
  audio.volume = Math.min(1, Math.max(0, Number(savedVol) || 0));
  const vol = document.getElementById("player-volume");
  if (vol) vol.value = audio.volume;
  const savedRate = UI.store.get("player.rate", 1);
  audio.playbackRate = savedRate;
  const speed = document.getElementById("player-speed");
  if (speed) {
    const opt = Array.from(speed.options).find((o) => parseFloat(o.value) === Number(savedRate));
    if (opt) speed.value = opt.value;
  }
  repeatMode = UI.store.get("player.repeat", "off");
  isPlayerShuffle = !!UI.store.get("player.shuffle", false);
  paintRepeatShuffle();

  audio.addEventListener("timeupdate", () => {
    if (!audio.duration || isNaN(audio.duration)) return;
    if (timeCur) timeCur.innerText = formatTimeSec(audio.currentTime);
    if (seek && !isSeeking) {
      seek.value = (audio.currentTime / audio.duration) * 100;
      seek.setAttribute("aria-valuetext", `${formatTimeSec(audio.currentTime)} of ${formatTimeSec(audio.duration)}`);
    }
    const now = Date.now();
    if (currentTrackMeta && now - lastPersist > 5000) {
      lastPersist = now;
      UI.store.set("player.last", { ...currentTrackMeta, time: audio.currentTime });
    }
  });
  audio.addEventListener("loadedmetadata", () => { if (timeTot && audio.duration) timeTot.innerText = formatTimeSec(audio.duration); });
  audio.addEventListener("play", () => { document.body.classList.add("is-playing"); setPlayIcon(true); if (navigator.mediaSession) navigator.mediaSession.playbackState = "playing"; });
  audio.addEventListener("pause", () => { document.body.classList.remove("is-playing"); setPlayIcon(false); if (navigator.mediaSession) navigator.mediaSession.playbackState = "paused"; });
  audio.addEventListener("playing", () => { consecutiveErrors = 0; });
  audio.addEventListener("ended", () => playNextTrack(true));
  audio.addEventListener("error", () => {
    if (!audio.src || audio.src === window.location.href) return;
    setPlayIcon(false);
    const name = currentTrackMeta ? `"${currentTrackMeta.title}"` : "this file";
    consecutiveErrors += 1;
    UI.toast(`Can't play ${name}. The file may be missing, moved, or in an unsupported format.`, "warning");
    // Only keep going if we got here by ourselves; a track the user picked stays put so the error is clear.
    if (currentTrackAuto && consecutiveErrors <= 3 && playerQueue.length > 1) setTimeout(() => playNextTrack(true), 900);
  });

  initMediaSession();
  restoreLastTrack();
}

function initMediaSession() {
  if (!("mediaSession" in navigator)) return;
  const ms = navigator.mediaSession;
  const bind = (action, fn) => { try { ms.setActionHandler(action, fn); } catch (_) { /* unsupported action */ } };
  bind("play", () => toggleAudioPlayback());
  bind("pause", () => audioEl().pause());
  bind("previoustrack", () => playPrevTrack());
  bind("nexttrack", () => playNextTrack());
  bind("seekto", (d) => { if (d.seekTime != null) audioEl().currentTime = d.seekTime; });
}

function updateMediaSession(meta) {
  if (!("mediaSession" in navigator) || !window.MediaMetadata) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: meta.title, artist: meta.artist, album: meta.album || "",
    artwork: [{ src: `/api/library/cover?path=${encodeURIComponent(meta.path)}`, sizes: "512x512" }]
  });
}

function restoreLastTrack() {
  const last = UI.store.get("player.last", null);
  const audio = audioEl();
  if (!last || !last.path || !audio) return;
  currentTrackMeta = { path: last.path, title: last.title, artist: last.artist, album: last.album };
  paintTrackInfo(currentTrackMeta);
  audio.src = `/api/stream?path=${encodeURIComponent(last.path)}`;
  audio.addEventListener("loadedmetadata", function once() {
    audio.removeEventListener("loadedmetadata", once);
    if (last.time && last.time < audio.duration - 2) audio.currentTime = last.time;
  });
}

function toggleAudioPlayback() {
  const audio = audioEl();
  if (!audio) return;
  if (!audio.src || audio.src === window.location.href) {
    if (cachedLibraryFiles.length > 0) {
      const f = cachedLibraryFiles[0];
      playLocalTrack(encodeURIComponent(f.path), encodeURIComponent(f.title), encodeURIComponent(f.artist), true, f.album || "");
    } else {
      UI.toast("Nothing to play yet. Add or scan some music first.", "info");
    }
    return;
  }
  if (audio.paused) audio.play().catch((err) => UI.toast("Playback was blocked: " + err.message, "warning"));
  else audio.pause();
}

function seekAudio(percent) {
  const audio = audioEl();
  if (!audio || !audio.duration) return;
  isSeeking = true;
  clearTimeout(seekTimer);
  audio.currentTime = (parseFloat(percent) / 100) * audio.duration;
  seekTimer = setTimeout(() => { isSeeking = false; }, 350); // keep timeupdate from fighting the thumb
}

function volumeIcon(audio) {
  return audio.muted || audio.volume === 0 ? "volume-x" : audio.volume < 0.5 ? "volume-1" : "volume-2";
}

function setAudioVolume(vol) {
  const audio = audioEl();
  if (!audio) return;
  audio.volume = parseFloat(vol);
  audio.muted = false;
  UI.store.set("player.volume", audio.volume);
  const icon = document.getElementById("icon-player-vol");
  if (icon) { icon.setAttribute("data-lucide", volumeIcon(audio)); UI.icons(document.getElementById("btn-player-mute")); }
}

function toggleAudioMute() {
  const audio = audioEl();
  if (!audio) return;
  audio.muted = !audio.muted;
  const icon = document.getElementById("icon-player-vol");
  if (icon) { icon.setAttribute("data-lucide", volumeIcon(audio)); UI.icons(document.getElementById("btn-player-mute")); }
  document.getElementById("btn-player-mute")?.setAttribute("aria-pressed", String(audio.muted));
}

function setPlaybackSpeed(speed) {
  const audio = audioEl();
  if (!audio) return;
  audio.playbackRate = parseFloat(speed);
  UI.store.set("player.rate", audio.playbackRate);
}

// ---------- shuffle / repeat
function paintRepeatShuffle() {
  const sBtn = document.getElementById("btn-player-shuffle");
  if (sBtn) {
    sBtn.classList.toggle("text-spotify-green", isPlayerShuffle);
    sBtn.classList.toggle("text-fg3", !isPlayerShuffle);
    sBtn.setAttribute("aria-pressed", String(isPlayerShuffle));
  }
  const rBtn = document.getElementById("btn-player-repeat");
  if (rBtn) {
    rBtn.classList.toggle("text-spotify-green", repeatMode !== "off");
    rBtn.classList.toggle("text-fg3", repeatMode === "off");
    rBtn.setAttribute("aria-pressed", String(repeatMode !== "off"));
    const label = { off: "Repeat: off", all: "Repeat: whole queue", one: "Repeat: current track" }[repeatMode];
    rBtn.setAttribute("aria-label", label);
    rBtn.setAttribute("title", label);
    rBtn.innerHTML = `<i data-lucide="${repeatMode === "one" ? "repeat-1" : "repeat"}" aria-hidden="true" class="w-4 h-4"></i>`;
    UI.icons(rBtn);
  }
}

function toggleShuffle() {
  isPlayerShuffle = !isPlayerShuffle;
  shuffleBag = [];
  UI.store.set("player.shuffle", isPlayerShuffle);
  paintRepeatShuffle();
  UI.toast(isPlayerShuffle ? "Shuffle on" : "Shuffle off", "info", 1500);
}

function toggleRepeat() {
  repeatMode = { off: "all", all: "one", one: "off" }[repeatMode];
  UI.store.set("player.repeat", repeatMode);
  paintRepeatShuffle();
  UI.toast({ off: "Repeat off", all: "Repeat whole queue", one: "Repeat current track" }[repeatMode], "info", 1500);
}

function nextShuffleIndex() {
  if (!shuffleBag.length) {
    shuffleBag = playerQueue.map((_, i) => i).filter((i) => i !== playerCurrentIndex);
    for (let i = shuffleBag.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [shuffleBag[i], shuffleBag[j]] = [shuffleBag[j], shuffleBag[i]];
    }
  }
  return shuffleBag.shift();
}

function playQueueIndex(idx) {
  const t = playerQueue[idx];
  if (!t) return;
  playerCurrentIndex = idx;
  playLocalTrack(encodeURIComponent(t.path), encodeURIComponent(t.title || ""), encodeURIComponent(t.artist || ""), false, t.album || "");
}

function playPrevTrack() {
  const audio = audioEl();
  if (audio && audio.currentTime > 3) { audio.currentTime = 0; return; } // like every player: restart first
  if (playerQueue.length === 0) return;
  if (isPlayerShuffle && playHistory.length) { playQueueIndex(playHistory.pop()); return; }
  const idx = playerCurrentIndex > 0 ? playerCurrentIndex - 1 : (repeatMode === "all" ? playerQueue.length - 1 : 0);
  playQueueIndex(idx);
}

function playNextTrack(fromEnded = false) {
  const audio = audioEl();
  if (playerQueue.length === 0) return;
  if (fromEnded && repeatMode === "one" && playerCurrentIndex >= 0) {
    audio.currentTime = 0;
    audio.play().catch(() => {});
    return;
  }
  let next;
  if (isPlayerShuffle && playerQueue.length > 1) {
    next = nextShuffleIndex();
    if (playerCurrentIndex >= 0) playHistory.push(playerCurrentIndex);
    if (playHistory.length > 200) playHistory.shift();
  } else if (playerCurrentIndex + 1 < playerQueue.length) {
    next = playerCurrentIndex + 1;
  } else if (repeatMode === "all" || !fromEnded) {
    next = 0;
  } else {
    setPlayIcon(false); // end of the queue: stop
    return;
  }
  advancedAutomatically = fromEnded;
  playQueueIndex(next);
}

// ---------- slide-over drawers
const DRAWERS = { "drawer-now-playing": "btn-player-lyrics", "drawer-playback-queue": "btn-player-queue" };

function setDrawer(id, open) {
  const drawer = document.getElementById(id);
  if (!drawer) return;
  if (open) {
    Object.keys(DRAWERS).filter((other) => other !== id).forEach((other) => setDrawer(other, false));
    drawer.classList.remove("drawer-closed", "translate-x-full");
    drawer.classList.add("drawer-open");
    drawer.removeAttribute("inert");
    if (id === "drawer-now-playing") syncNowPlayingDrawerInfo(); else renderUpcomingPlayerQueue();
  } else {
    drawer.classList.remove("drawer-open");
    drawer.classList.add("drawer-closed", "translate-x-full");
    drawer.setAttribute("inert", "");
  }
  const btn = document.getElementById(DRAWERS[id]);
  if (btn) {
    btn.classList.toggle("text-spotify-green", open);
    btn.setAttribute("aria-expanded", String(open));
  }
}

function closeDrawer(id) { setDrawer(id, false); }
function toggleNowPlayingDrawer(forceOpen = null) {
  const d = document.getElementById("drawer-now-playing");
  setDrawer("drawer-now-playing", forceOpen !== null ? forceOpen : !d.classList.contains("drawer-open"));
}
function togglePlaybackQueueDrawer(forceOpen = null) {
  const d = document.getElementById("drawer-playback-queue");
  setDrawer("drawer-playback-queue", forceOpen !== null ? forceOpen : !d.classList.contains("drawer-open"));
}
function openNowPlayingDrawer() { toggleNowPlayingDrawer(true); }
function closeNowPlayingDrawer() { toggleNowPlayingDrawer(false); }
function openPlaybackQueueDrawer() { togglePlaybackQueueDrawer(true); }
function closePlaybackQueueDrawer() { togglePlaybackQueueDrawer(false); }

function syncNowPlayingDrawerInfo() {
  if (!currentTrackMeta) return;
  document.getElementById("drawer-track-title").innerText = currentTrackMeta.title;
  document.getElementById("drawer-track-artist").innerText = currentTrackMeta.artist || "Unknown artist";
  document.getElementById("drawer-track-album").innerText = currentTrackMeta.album || "Local library";
}

// ---------- queue
function removeTrackFromQueue(index) {
  if (index < 0 || index >= playerQueue.length) return;
  const removed = playerQueue.splice(index, 1);
  if (index < playerCurrentIndex) playerCurrentIndex--;
  shuffleBag = [];
  renderUpcomingPlayerQueue();
  UI.toast(`Removed: ${removed[0]?.title || "track"}`, "info", 1500);
}

function clearPlayerQueue() {
  playerQueue = [];
  playerCurrentIndex = -1;
  shuffleBag = [];
  renderUpcomingPlayerQueue();
  UI.toast("Playback queue cleared", "info", 1500);
}

function renderUpcomingPlayerQueue() {
  const list = document.getElementById("queue-upcoming-list");
  if (!list) return;
  if (playerQueue.length === 0 || playerCurrentIndex >= playerQueue.length - 1) {
    list.innerHTML = '<div class="text-fg4 text-xs italic py-2">No upcoming tracks</div>';
    return;
  }
  const upcoming = playerQueue.slice(playerCurrentIndex + 1, playerCurrentIndex + 51);
  list.innerHTML = upcoming.map((t, idx) => {
    const qi = playerCurrentIndex + 1 + idx;
    const args = jsonAttr([encodeURIComponent(t.path), encodeURIComponent(t.title || ""), encodeURIComponent(t.artist || ""), encodeURIComponent(t.album || "")]);
    return `
      <div class="flex items-center space-x-2.5 p-2 rounded-xl hover:bg-hv transition-colors group">
        <button type="button" data-on-click="playLocalTrack_9bca49" data-args-click="${args}" class="flex items-center space-x-2.5 flex-1 min-w-0 text-left" aria-label="Play ${escapeHtml(t.title || "track")}">
          <span class="text-2xs text-fg4 font-mono w-5 text-right">${qi + 1}</span>
          <span class="w-7 h-7 rounded bg-s3 flex items-center justify-center text-fg3 shrink-0"><i data-lucide="music" aria-hidden="true" class="w-3.5 h-3.5"></i></span>
          <span class="truncate flex-1">
            <span class="block text-xs font-semibold text-fg truncate">${escapeHtml(t.title || "Unknown title")}</span>
            <span class="block text-2xs text-fg3 truncate">${escapeHtml(t.artist || "Unknown artist")}</span>
          </span>
        </button>
        <button type="button" data-on-click="removeTrackFromQueue_41fcc9" data-args-click="${jsonAttr([qi])}" title="Remove from queue" aria-label="Remove ${escapeHtml(t.title || "track")} from queue" class="p-1 rounded hover:bg-s4 text-fg4 hover:text-fg opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity">
          <i data-lucide="x" aria-hidden="true" class="w-3.5 h-3.5"></i>
        </button>
      </div>`;
  }).join("");
  UI.icons(list);
}

async function loadTrackLyrics(path) {
  const statusEl = document.getElementById("drawer-lyrics-status");
  const contentEl = document.getElementById("drawer-lyrics-content");
  if (!contentEl) return;
  if (statusEl) statusEl.innerText = "Searching…";
  contentEl.innerHTML = '<span class="text-fg4 italic">Fetching lyrics…</span>';
  try {
    const data = await UI.api(`/api/library/lyrics?path=${encodeURIComponent(path)}`, { quiet: true });
    if (!currentTrackMeta || currentTrackMeta.path !== path) return; // user already moved on
    if (data.found && data.lyrics) {
      if (statusEl) statusEl.innerText = data.synced ? "Synced (LRC)" : "Plain text";
      contentEl.innerText = data.lyrics;
    } else {
      if (statusEl) statusEl.innerText = "Not found";
      contentEl.innerHTML = '<span class="text-fg4 italic">No embedded or .lrc lyrics for this track.</span>';
    }
  } catch (_) {
    if (statusEl) statusEl.innerText = "Unavailable";
    contentEl.innerHTML = '<span class="text-fg4 italic">Lyrics are unavailable.</span>';
  }
}

function paintTrackInfo(meta) {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.innerText = v; };
  set("player-title", meta.title);
  set("player-folder", meta.artist);
  set("drawer-track-title", meta.title);
  set("drawer-track-artist", meta.artist);
  set("drawer-track-album", meta.album || "Local library");
  set("queue-now-title", meta.title);
  set("queue-now-artist", meta.artist);

  const coverUrl = `/api/library/cover?path=${encodeURIComponent(meta.path)}`;
  FX.coverAccent(coverUrl);       // tint glows/spotlights with the album colour
  [["player-cover-art", "player-placeholder-icon"], ["drawer-cover-art", "drawer-placeholder-icon"]].forEach(([imgId, phId]) => {
    const img = document.getElementById(imgId), ph = document.getElementById(phId);
    if (img) { img.src = coverUrl; img.classList.remove("hidden"); }
    if (ph) ph.classList.add("hidden");
  });

  const badge = document.getElementById("player-format-badge");
  if (badge) {
    const m = meta.path.match(/\.([a-zA-Z0-9]+)$/);
    badge.innerText = m ? m[1].toUpperCase() : "";
    badge.classList.toggle("hidden", !m);
  }
  document.title = `${meta.title} · ${meta.artist}`;
}

// Upgrade a page-sized play queue to the whole library view (same filter/sort) in the background.
async function upgradeQueueToFullLibrary(currentPath) {
  try {
    const params = new URLSearchParams({ search: libSearchQuery, filter: libCurrentFilter, limit: 50000, offset: 0, sort_by: libSortBy, sort_dir: libSortDir });
    const data = await UI.api(`/api/library?${params}`, { quiet: true });
    const files = data.files || [];
    const idx = files.findIndex((f) => f.path === currentPath);
    if (idx !== -1 && currentTrackMeta && currentTrackMeta.path === currentPath) {
      playerQueue = files;
      playerCurrentIndex = idx;
      shuffleBag = [];
      if (document.getElementById("drawer-playback-queue").classList.contains("drawer-open")) renderUpcomingPlayerQueue();
    }
  } catch (_) { /* the page-sized queue keeps working */ }
}

function playLocalTrack(encodedPath, title, artist, updateQueue = true, album = "") {
  const audio = audioEl();
  if (!audio) return;
  const dec = (v, fb) => { try { return decodeURIComponent(v || "") || fb; } catch (_) { return v || fb; } };
  const path = dec(encodedPath, "");
  const meta = { path, title: dec(title, "Unknown track"), artist: dec(artist, "Unknown artist"), album: dec(album, "") };

  if (updateQueue && cachedLibraryFiles.length > 0) {
    const idx = cachedLibraryFiles.findIndex((f) => f.path === path);
    if (idx !== -1) {
      playerQueue = [...cachedLibraryFiles];
      playerCurrentIndex = idx;
      shuffleBag = []; playHistory = [];
      upgradeQueueToFullLibrary(path);
    }
  }

  currentTrackAuto = advancedAutomatically;
  advancedAutomatically = false;
  currentTrackMeta = meta;
  audio.src = `/api/stream?path=${encodeURIComponent(path)}`;
  audio.play().catch((err) => { if (err.name !== "AbortError") console.warn("Playback did not start:", err); });
  UI.store.set("player.last", { ...meta, time: 0 });

  UI.api("/api/player/played", { body: { path }, quiet: true }).catch(() => {});
  paintTrackInfo(meta);
  updateMediaSession(meta);
  renderUpcomingPlayerQueue();
  loadTrackLyrics(path);
}

// ================= TAG EDITOR =================
let tagOriginal = {};

function tagFieldIds() {
  return { title: "edit-tag-title", artist: "edit-tag-artist", album: "edit-tag-album", year: "edit-tag-year", genre: "edit-tag-genre" };
}

async function openTagModal(encodedPath, title, artist, album) {
  const dec = (v) => { try { return decodeURIComponent(v || ""); } catch (_) { return v || ""; } };
  const path = dec(encodedPath);
  const ids = tagFieldIds();
  document.getElementById("edit-tag-path").value = path;
  // Start from a clean slate every time: nothing from the previous file may leak into this one.
  const start = { title: dec(title), artist: dec(artist), album: dec(album), year: "", genre: "" };
  Object.entries(ids).forEach(([k, id]) => { document.getElementById(id).value = start[k]; });
  tagOriginal = { ...start };
  document.getElementById("tag-modal").classList.remove("hidden");

  // Fill everything else (and any arguments the caller didn't have) from the file itself.
  try {
    const res = await UI.api(`/api/library/inspect?path=${encodeURIComponent(path)}`, { quiet: true });
    if (document.getElementById("edit-tag-path").value !== path) return;
    const m = res.metadata || {};
    const fromFile = { title: m.title, artist: m.artist, album: m.album, year: m.year, genre: m.genre };
    Object.entries(ids).forEach(([k, id]) => {
      const el = document.getElementById(id);
      if (!el.value && fromFile[k]) el.value = String(fromFile[k]);
      if (!tagOriginal[k] && fromFile[k]) tagOriginal[k] = String(fromFile[k]);
    });
  } catch (_) { /* editing still works with what we have */ }
}

function closeTagModal() {
  document.getElementById("tag-modal").classList.add("hidden");
}

async function saveEditedTags() {
  const path = document.getElementById("edit-tag-path").value;
  const ids = tagFieldIds();
  const changed = {};
  for (const [k, id] of Object.entries(ids)) {
    const v = document.getElementById(id).value.trim();
    if (v !== (tagOriginal[k] || "")) changed[k] = v;
  }
  if (!Object.keys(changed).length) { UI.toast("No changes to save.", "info"); closeTagModal(); return; }
  if ("title" in changed && !changed.title) { UI.toast("The title can't be empty.", "warning"); document.getElementById(ids.title).focus(); return; }

  const meta = { ...changed };
  if ("artist" in meta) { meta.artists = [meta.artist]; delete meta.artist; }
  try {
    await UI.api("/api/tags/edit", { body: { path, metadata: meta }, prefix: "Tags not saved" });
    UI.toast(`Saved ${Object.keys(changed).join(", ")}.`, "success");
    closeTagModal();
    refreshLibrary();
  } catch (_) { /* reported */ }
}

async function openDownloadsFolder() {
  try {
    await UI.api("/api/open-folder", { method: "POST", prefix: "Could not open the folder" });
  } catch (_) { /* reported */ }
}
