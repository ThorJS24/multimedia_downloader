// ============================================================================
// Home shelves + dedicated Album / Artist / Playlist pages.
// One card builder and one track-row builder serve every grid and table.
// ============================================================================
let currentAlbumData = null;
let currentArtistData = null;
let currentPlaylistData = null;
let omniActiveCategory = "all";

const enc = (v) => encodeURIComponent(v == null ? "" : v);
const coverUrl = (path) => `/api/library/cover?path=${encodeURIComponent(path)}`;

function formatDuration(sec) { return UI.fmtDuration(sec); }

// ---------- builders
function coverImg(path, fallbackIcon, fallbackCls, rounded = "rounded-xl") {
  return `
    <div class="relative w-full aspect-square ${rounded} overflow-hidden bg-s3 mb-3 shadow-md">
      <img src="${coverUrl(path)}" alt="" loading="lazy" decoding="async" class="w-full h-full object-cover" data-on-error="this_49d186" />
      <div class="w-full h-full hidden items-center justify-center ${fallbackCls}"><i data-lucide="${fallbackIcon}" aria-hidden="true" class="w-8 h-8"></i></div>
    </div>`;
}

const hoverPlay = `
  <span aria-hidden="true" class="absolute right-2 bottom-2 w-10 h-10 rounded-full bg-spotify-green text-black shadow-xl flex items-center justify-center opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 translate-y-2 group-hover:translate-y-0 transition-all duration-200">
    <i data-lucide="play" class="w-5 h-5 fill-black ml-0.5"></i>
  </span>`;

function mediaCard({ handler, args, label, cover, title, subtitle, footLeft, footRight, round = false }) {
  const art = cover || "";
  return `
    <div class="fx-card group p-3.5 rounded-2xl bg-s1 hover:bg-hv border border-line hover:border-line2 transition-all flex flex-col justify-between cursor-pointer ${round ? "items-center text-center" : ""}"
         data-on-click="${handler}" data-args-click="${jsonAttr(args)}" aria-label="${escapeHtml(label)}">
      <div class="${round ? "w-full flex flex-col items-center" : ""}">
        ${art}
        <div class="font-bold text-fg text-xs truncate ${round ? "w-full" : ""}" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
        <div class="text-2xs text-fg3 truncate mt-0.5 ${round ? "w-full" : ""}" title="${escapeHtml(subtitle || "")}">${escapeHtml(subtitle || "")}</div>
      </div>
      ${footLeft !== undefined ? `<div class="mt-2 pt-2 border-t border-line w-full flex items-center justify-between text-2xs text-fg4 font-mono"><span class="truncate max-w-[60%]">${footLeft}</span><span>${footRight || ""}</span></div>` : ""}
    </div>`;
}

const trackCard = (t, footLeft) => mediaCard({
  handler: "playLocalTrack_15214c", args: [enc(t.path), enc(t.title), enc(t.artist), enc(t.album)],
  label: `Play ${t.title} by ${t.artist}`,
  cover: `<div class="relative w-full aspect-square rounded-xl overflow-hidden bg-s3 mb-3 shadow-md">
      <img src="${coverUrl(t.path)}" alt="" loading="lazy" decoding="async" class="w-full h-full object-cover" data-on-error="this_49d186" />
      <div class="w-full h-full hidden items-center justify-center text-fg4 bg-s3"><i data-lucide="music" aria-hidden="true" class="w-8 h-8"></i></div>${hoverPlay}</div>`,
  title: t.title, subtitle: t.artist, footLeft, footRight: formatDuration(t.duration_sec)
});

function trackRowHtml(t, { index, artist = false, album = false, showCover = false }) {
  const playArgs = jsonAttr([enc(t.path), enc(t.title), enc(t.artist), enc(t.album)]);
  return `
    <tr class="hover:bg-hv transition-colors group cursor-pointer" data-on-click="playLocalTrack_15214c" data-args-click="${playArgs}">
      <td class="py-3 px-4 w-12 text-center text-fg4 font-mono text-xs group-hover:text-spotify-green">
        <span class="group-hover:hidden">${index}</span>
        <i data-lucide="play" aria-hidden="true" class="w-3.5 h-3.5 mx-auto hidden group-hover:inline fill-spotify-green text-spotify-green"></i>
      </td>
      <td class="py-3 px-4">
        <div class="flex items-center space-x-3">
          ${showCover ? `<div class="w-9 h-9 rounded-lg overflow-hidden bg-s3 shrink-0"><img src="${coverUrl(t.path)}" alt="" loading="lazy" decoding="async" class="w-full h-full object-cover" data-on-error="this_49d186" /><div class="w-full h-full hidden items-center justify-center text-fg4"><i data-lucide="music" aria-hidden="true" class="w-4 h-4"></i></div></div>` : ""}
          <div class="font-bold text-fg text-xs truncate max-w-md">${escapeHtml(t.title)}</div>
        </div>
      </td>
      ${artist ? `<td class="py-3 px-4 text-fg3 text-xs truncate max-w-xs"><button type="button" class="hover:underline hover:text-fg" data-on-click="openArtistView_302f1a" data-args-click="${jsonAttr([enc(t.artist)])}">${escapeHtml(t.artist)}</button></td>` : ""}
      ${album ? `<td class="py-3 px-4 text-fg3 text-xs truncate max-w-xs"><button type="button" class="hover:underline hover:text-fg" data-on-click="openAlbumView_7a0637" data-args-click="${jsonAttr([enc(t.album), enc(t.artist)])}">${escapeHtml(t.album)}</button></td>` : ""}
      <td class="py-3 px-4 w-28 text-right font-mono text-fg3 text-xs">${formatDuration(t.duration_sec)}</td>
      <td class="py-3 px-4 w-24 text-right">
        <button type="button" data-on-click="queueLocalTrack_8df91f" data-args-click="${jsonAttr([enc(t.path), enc(t.title), enc(t.artist)])}" title="Add to playback queue" aria-label="Add ${escapeHtml(t.title)} to playback queue" class="p-1.5 rounded-lg hover:bg-s4 text-fg3 hover:text-fg"><i data-lucide="list-plus" aria-hidden="true" class="w-3.5 h-3.5"></i></button>
      </td>
    </tr>`;
}

// ---------- A. Home shelves
const SHELVES = [
  { key: "recently_played", id: "played", build: (t) => trackCard(t, `${t.play_count || 1} ${t.play_count === 1 ? "play" : "plays"}`) },
  { key: "recently_added", id: "added", build: (t) => trackCard(t, escapeHtml(t.album)) },
  { key: "top_artists", id: "artists", build: (a) => mediaCard({
      handler: "openArtistView_53ee7d", args: [enc(a.artist)], label: `Open artist ${a.artist}`, round: true,
      cover: `<div class="relative w-28 h-28 rounded-full overflow-hidden bg-s3 mb-3 shadow-xl border border-line"><img src="${coverUrl(a.sample_path)}" alt="" loading="lazy" decoding="async" class="w-full h-full object-cover" data-on-error="this_49d186" /><div class="w-full h-full hidden items-center justify-center text-blue-400 bg-s3"><i data-lucide="mic" aria-hidden="true" class="w-10 h-10"></i></div></div>`,
      title: a.artist, subtitle: `Artist • ${a.track_count} ${a.track_count === 1 ? "song" : "songs"}` }) },
  { key: "top_albums", id: "albums", build: (al) => mediaCard({
      handler: "openAlbumView_810177", args: [enc(al.album), enc(al.artist)], label: `Open album ${al.album} by ${al.artist}`,
      cover: coverImg(al.sample_path, "disc", "text-purple-400 bg-s3"), title: al.album, subtitle: al.artist,
      footLeft: al.year ? al.year : "Album", footRight: `${al.track_count} tracks` }) },
  { key: "playlists", id: "playlists", build: (p) => mediaCard({
      handler: "openPlaylistView_0c783b", args: [enc(p.name)], label: `Open playlist ${p.name}`,
      cover: p.sample_path ? coverImg(p.sample_path, "list-music", "text-emerald-400 bg-s3")
        : `<div class="w-full aspect-square rounded-xl overflow-hidden mb-3 flex items-center justify-center text-emerald-400 bg-s3"><i data-lucide="list-music" aria-hidden="true" class="w-8 h-8"></i></div>`,
      title: p.name, subtitle: "Playlist", footLeft: `${p.track_count} tracks` }) }
];

async function loadHomePageData() {
  try {
    const data = await UI.api("/api/home", { quiet: true });
    let anything = false;
    SHELVES.forEach((shelf) => {
      const container = document.getElementById(`home-shelf-${shelf.id}-container`);
      const grid = document.getElementById(`home-shelf-${shelf.id}-grid`);
      const items = data[shelf.key] || [];
      if (!grid || !container) return;
      container.classList.toggle("hidden", items.length === 0); // no empty shelves
      if (items.length) {
        anything = true;
        grid.innerHTML = items.map(shelf.build).join("");
      }
    });
    document.getElementById("home-empty")?.classList.toggle("hidden", anything);
    UI.icons(document.getElementById("tab-dashboard"));
  } catch (err) {
    console.error("Error loading home page shelves:", err);
  }
}

// ---------- B. Discover filters (the search itself lives in sources.js)
function setOmniCategory(cat) {
  omniActiveCategory = cat;
  document.querySelectorAll(".omni-cat-btn").forEach((btn) => {
    const on = btn.id === `omni-cat-${cat}`;
    btn.classList.toggle("bg-inv", on);
    btn.classList.toggle("text-inv-fg", on);
    btn.classList.toggle("bg-s3", !on);
    btn.classList.toggle("text-fg2", !on);
    btn.setAttribute("aria-pressed", String(on));
  });
  // Show only the sections that belong to the chosen category.
  const tracksSection = document.getElementById("omni-tracks-list")?.closest(".grid") || document.getElementById("omni-tracks-list");
  const show = { tracks: ["all", "tracks"], artists: ["all", "artists"], albums: ["all", "albums"] };
  tracksSection?.classList.toggle("hidden", !show.tracks.includes(cat) && cat !== "playlists" && cat !== "videos");
  document.getElementById("omni-artists-section")?.classList.toggle("hidden", !show.artists.includes(cat));
  document.getElementById("omni-albums-section")?.classList.toggle("hidden", !show.albums.includes(cat));
  const input = document.getElementById("omni-search-input");
  if (input && input.value.trim() && typeof triggerUnifiedSearch === "function") triggerUnifiedSearch(input.value.trim());
}

// ---------- C. Album
function fillHeader({ titleId, title, coverId, placeholderId, samplePath }) {
  const t = document.getElementById(titleId);
  if (t) t.innerText = title;
  const img = document.getElementById(coverId), ph = document.getElementById(placeholderId);
  if (img && samplePath) {
    img.src = coverUrl(samplePath);
    img.classList.remove("hidden");
    if (ph) ph.classList.add("hidden");
  } else if (img) {
    img.classList.add("hidden");
    if (ph) ph.classList.remove("hidden");
  }
}

const minutesText = (sec) => `${Math.floor(sec / 60)} min ${Math.floor(sec % 60)} sec`;

async function openAlbumView(albumName, artistName) {
  const dec = (v) => { try { return decodeURIComponent(v || ""); } catch (_) { return v || ""; } };
  try {
    const data = await UI.api(`/api/library/album?name=${enc(dec(albumName))}&artist=${enc(dec(artistName))}`, { prefix: "Album not found" });
    currentAlbumData = data;
    switchTab("album-detail");
    fillHeader({ titleId: "album-view-title", title: data.album, coverId: "album-view-cover", placeholderId: "album-view-placeholder", samplePath: data.sample_path });
    document.getElementById("album-view-artist-link").innerText = data.artist || "Unknown artist";
    document.getElementById("album-view-year").innerText = data.year || "--";
    document.getElementById("album-view-track-count").innerText = `${data.track_count} songs`;
    document.getElementById("album-view-duration").innerText = minutesText(data.total_duration_sec);
    document.getElementById("album-view-tracks-body").innerHTML = data.tracks.map((t, i) => trackRowHtml(t, { index: t.track_number || i + 1, artist: true })).join("");
    UI.icons(document.getElementById("tab-album-detail"));
  } catch (_) { /* reported */ }
}

function openArtistFromCurrentAlbum() {
  if (currentAlbumData && currentAlbumData.artist) openArtistView(currentAlbumData.artist);
}

function playTracks(tracks, label) {
  if (!tracks || tracks.length === 0) return UI.toast("There are no tracks to play.", "warning");
  playerQueue = [...tracks];
  playerCurrentIndex = 0;
  shuffleBag = []; playHistory = [];
  playLocalTrack(enc(tracks[0].path), enc(tracks[0].title), enc(tracks[0].artist), false, tracks[0].album);
  UI.toast(`Playing ${label}`, "success", 2000);
}

function queueTracks(tracks) {
  if (!tracks || tracks.length === 0) return UI.toast("There are no tracks to queue.", "warning");
  playerQueue = playerQueue.concat(tracks);
  UI.toast(`Added ${tracks.length} track${tracks.length === 1 ? "" : "s"} to the playback queue.`, "success", 2000);
}

async function exportTracksM3U(tracks, name) {
  if (!tracks || tracks.length === 0) return UI.toast("There are no tracks to export.", "warning");
  try {
    const res = await UI.api("/api/library/export-m3u", { body: { paths: tracks.map((t) => t.path), playlist_name: name }, prefix: "Export failed" });
    exportedToast(`Exported ${res.track_count} tracks to ${name}.m3u`, res.path);
  } catch (_) { /* reported */ }
}

function playCurrentAlbum() { if (currentAlbumData) playTracks(currentAlbumData.tracks, `album: ${currentAlbumData.album}`); }
function queueCurrentAlbum() { if (currentAlbumData) queueTracks(currentAlbumData.tracks); }
function exportCurrentAlbumM3U() { if (currentAlbumData) exportTracksM3U(currentAlbumData.tracks, currentAlbumData.album); }

// ---------- D. Artist
async function openArtistView(artistName) {
  let name = artistName;
  try { name = decodeURIComponent(artistName); } catch (_) { /* already decoded */ }
  try {
    const data = await UI.api(`/api/library/artist?name=${enc(name)}`, { prefix: "Artist not found" });
    currentArtistData = data;
    switchTab("artist-detail");
    fillHeader({ titleId: "artist-view-name", title: data.artist, coverId: "artist-view-avatar", placeholderId: "artist-view-placeholder", samplePath: data.sample_path });
    document.getElementById("artist-view-tracks-count").innerText = `${data.total_tracks} local tracks`;
    document.getElementById("artist-view-albums-count").innerText = `${data.album_count} releases`;
    document.getElementById("artist-view-popular-body").innerHTML = (data.top_tracks || []).slice(0, 10)
      .map((t, i) => trackRowHtml(t, { index: i + 1, album: true, showCover: true })).join("");
    document.getElementById("artist-view-albums-grid").innerHTML = (data.albums || []).map((al) => mediaCard({
      handler: "openAlbumView_810177", args: [enc(al.album), enc(data.artist)], label: `Open album ${al.album}`,
      cover: coverImg(al.sample_path, "disc", "text-purple-400 bg-s3"), title: al.album, subtitle: al.year ? String(al.year) : "Release",
      footLeft: `${al.track_count} tracks` })).join("");
    UI.icons(document.getElementById("tab-artist-detail"));
  } catch (_) { /* reported */ }
}

function playCurrentArtist() { if (currentArtistData) playTracks(currentArtistData.top_tracks, currentArtistData.artist); }
function queueCurrentArtist() { if (currentArtistData) queueTracks(currentArtistData.top_tracks); }

// ---------- E. Playlist
async function openPlaylistView(playlistName) {
  let name = playlistName;
  try { name = decodeURIComponent(playlistName); } catch (_) { /* already decoded */ }
  try {
    const data = await UI.api(`/api/library/playlist?name=${enc(name)}`, { prefix: "Playlist not found" });
    currentPlaylistData = data;
    switchTab("playlist-detail");
    fillHeader({ titleId: "playlist-view-title", title: data.name, coverId: "playlist-view-cover", placeholderId: "playlist-view-placeholder", samplePath: data.sample_path });
    document.getElementById("playlist-view-track-count").innerText = `${data.track_count} tracks`;
    document.getElementById("playlist-view-duration").innerText = minutesText(data.total_duration_sec);
    document.getElementById("playlist-view-tracks-body").innerHTML = data.tracks.map((t, i) => trackRowHtml(t, { index: i + 1, artist: true, album: true })).join("");
    UI.icons(document.getElementById("tab-playlist-detail"));
  } catch (_) { /* reported */ }
}

function playCurrentPlaylist() { if (currentPlaylistData) playTracks(currentPlaylistData.tracks, `playlist: ${currentPlaylistData.name}`); }
function queueCurrentPlaylist() { if (currentPlaylistData) queueTracks(currentPlaylistData.tracks); }
function exportCurrentPlaylistM3U() { if (currentPlaylistData) exportTracksM3U(currentPlaylistData.tracks, currentPlaylistData.name); }

function queueLocalTrack(encodedPath, title, artist) {
  const dec = (v, fb) => { try { return decodeURIComponent(v || "") || fb; } catch (_) { return v || fb; } };
  const t = { path: dec(encodedPath, ""), title: dec(title, "Unknown track"), artist: dec(artist, "Unknown artist"), album: "", duration_sec: 0 };
  playerQueue.push(t);
  UI.toast(`Queued: ${t.title}`, "success", 1800);
}
