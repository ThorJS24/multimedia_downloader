# 🎵 Authentic Spotify Playlist & Track Downloader

A dedicated Spotify audio stream extractor and playlist downloader that pulls **genuine audio streams directly from Spotify's servers (CDN & Access Point)**.

It resolves Spotify metadata, then retrieves audio. **Spotify links are downloaded only via the direct Spotify Access Point session (Premium required) and never fall back to another source automatically.** YouTube / YouTube Music are used only when you explicitly pick an alternative match for a track (via in-process `yt-dlp`). Tracks are transcoded, tagged with Spotify metadata and cover art, and indexed in a local SQLite library.

---

## 🔒 Storage Isolation (Zero C: Drive Usage)

This project has been configured to **strictly reside within this directory (`D:\mini-projects\Spotify_playlist_downloader`)**:
- **Virtual Environment (`.venv/`)**: Python binaries and packages installed locally.
- **Pip Cache (`.cache/pip/`)**: Package wheels and caches saved strictly in the project folder.
- **Temporary Files (`temp/`)**: Decryption buffers, FFmpeg chunks, and audio transcoding temp files strictly mapped to `D:\...\temp`.
- **Downloads (`downloads/`)**: All saved tracks and playlists are placed in subfolders under `D:\...\downloads`.
- **Zero data written to the `C:` drive**.

---

## ⚠️ Important: Spotify Accounts, Encryption & Safety

### 1. Free Account vs. Spotify Premium Account
* **Spotify Web API (Metadata)**: Both Free and Premium accounts can query playlist info, track titles, album names, release dates, and high-resolution cover arts.
* **Audio Decryption Key Server**: When downloading the actual full song audio, Spotify's key server (`Mercury / AP`) checks the account type.
  * **Free Accounts**: Spotify server returns `403 Forbidden` / `Premium required` when requesting decryption keys for full tracks.
  * **Premium Accounts**: Spotify server grants the 16-byte AES decryption keys, allowing full high-quality (160kbps - 320kbps) audio streams to be decrypted and saved.

### 2. Account Ban Risk & Protective Safeguards
* Spotify's Terms of Service prohibit stream ripping.
* If automated scripts download hundreds of songs simultaneously without delays, Spotify's telemetry can flag the account.
* **Built-in Safety Protections**:
  * **Rate Limiting with Random Jitter**: The tool automatically inserts a random delay (3–7 seconds, configurable in `config.json`) between track downloads to mimic natural listening behavior.
  * **Session Caching**: Credentials and session tokens are securely cached in `.cache/credentials.json` so you do not repeatedly send login requests.
  * **Recommendation**: **Use a secondary / burner Spotify account or family plan slot** rather than your primary personal account for bulk downloading.

---

## 🚀 Quick Start Guide

### Open the app
Double-click **`SpotifyDownloader.exe`** (or run `.\run.ps1` during development). The app opens straight into its desktop window: there is no console and no command-line menu. Everything (sign-in, downloads, library, settings) is done inside the app.

Optional switches, from a terminal: `--browser` (open in your web browser instead), `--headless` / `--no-browser` (server only), `--version`, `--diagnostics`, and `mcp` (Model Context Protocol server for AI assistants; see `docs/mcp_integration_guide.md`).
* **Security**: the app serves itself on a free local port protected by a per-run session token; the desktop window handles that for you.
* **How it is organised** (five sidebar destinations; the sections inside each appear as tabs):
  * 🏠 **Home**: paste a link to start, see what is downloading and what failed (one-click Retry), jump back into your music.
  * 📥 **Downloads**: *Add music* (review & add, or add without review), *Queue* (live progress, filters, bulk actions, undo), *Match lab* (see how the matcher scores real candidates).
  * 🎧 **Library**: *Tracks* (sortable, with the built-in player), *Discover* (multi-source search), *Health*, *Quality*, *Releases*, *Cleanup* (duplicates and temp files).
  * ⚙️ **Automation**: *Playlist sync*, *Artist monitor*, *Folder watcher*.
  * 🔧 **Settings**: *Preferences* (every option, one save bar) and *Diagnostics* (live log stream).
* **Look & feel**: dark, light or follow-Windows theme and adjustable visual effects (Full / Subtle / Off; Windows "reduce motion" is respected), album-art-tinted accents, collapsible sidebar.
* **Command palette**: `Ctrl+K` jumps to any section, runs an action or finds and plays a song. Other shortcuts: `/` search, `Alt+1…5` destinations, `Space` play/pause, `?` for help.
* **Safety nets**: confirmation dialogs for anything destructive, undo for queue removals and bulk reorganise, "Show in folder" after exports.

---

---

## ⚙️ Configuration (`config/config.json`)

Settings are normally changed in the **Settings** tab. The file is validated on every change: unknown
keys are rejected and numbers are clamped to their allowed range. You can export/import settings
(without credentials) from the same tab or via `/api/settings/export` and `/api/settings/import`.

| Section | Keys |
|---|---|
| `general` | `first_run_completed`, `restore_queue_on_startup` |
| `audio` | `output_format` (mp3/ogg/flac/opus/m4a/aac/wav), `mp3_bitrate`, `audio_quality`, `normalize_loudness`, `save_cover_art`, `embed_lyrics` |
| `naming` | `template`, `folder_organization` (playlist/artist_album/flat), `collision_strategy` (skip/overwrite/rename) |
| `performance` | `max_workers` (1-4), `rate_limit_min_delay`, `rate_limit_max_delay`, `retry_limit` (0-10) |
| `spotify` | `username`, `password`\*, `client_id`, `client_secret`\*, `credentials_file` |
| `watcher` | `enabled`, `polling_interval_sec`, `settling_delay_sec`, `recursive`, `use_polling_fallback` |
| `maintenance` | `monitor_enabled`, `monitor_interval_minutes`, `backup_enabled`, `backup_interval_hours`, `log_retention_days`, `job_retention_days`, `update_feed_url`, `update_require_signature` |

\* Encrypted with Windows DPAPI in the file and masked (`********`) in the UI/API. A `paths` section
(`download_dir`, ...) is kept as-is.

**Updates are opt-in.** Nothing is contacted until you set `maintenance.update_feed_url` to an HTTPS
URL serving `{"version", "url", "sha256", "notes"}` (see `src/update_service.py`). The Settings tab can then check,
download (SHA-256 verified) and start the installer, which is only launched if it is validly code-signed.

Logs are written to `logs/studio.log` (rotating, secrets redacted). Backups of the database go to
`data/backups/`.

---

## 📦 Standalone Windows Executable (`SpotifyDownloader.exe`)

A self-contained, release-ready Windows distribution with zero prerequisites:
* **No Python installation required**.
* **Bundled FFmpeg & FFprobe**: Encodes 320kbps MP3, M4A/AAC, FLAC, WAV, and native OGG Vorbis without system dependencies.
* **One download path**: Spotify links are fetched through your Spotify Premium session; YouTube / YouTube Music are used only when you explicitly pick an alternative for a track. See `docs/ARCHITECTURE.md` and `docs/SECURITY.md`.
* **First-Run Setup Wizard**: Interactive GUI setup that verifies storage containment on `D:`, detects FFmpeg, and sets your downloads folder.
* **Strict D: Drive Containment**: The executable and bundled libraries guarantee 0 bytes modified on the `C:` drive. All temp transcode files, databases, caches, and downloads reside locally.

### Running the Standalone Executable
```powershell
# Run the Desktop Application / Web Studio
.\dist\SpotifyDownloader\SpotifyDownloader.exe

# Run Diagnostics
.\dist\SpotifyDownloader\SpotifyDownloader.exe --diagnostics

# Show Version & Build Info
.\dist\SpotifyDownloader\SpotifyDownloader.exe --version

# Run Headless Server (without opening desktop window)
.\dist\SpotifyDownloader\SpotifyDownloader.exe web --no-browser
```

### Building the Release Package
To rebuild the standalone executable and release ZIP archive:
```powershell
.\.venv\Scripts\python.exe build_windows.py
```
Output artifacts are generated in `dist/`:
- `dist/SpotifyDownloader/SpotifyDownloader.exe` (Portable distribution folder)
- `dist/SpotifyDownloader-v<version>-Windows-x64.zip` and `...-Setup.exe` (Release archive and installer)
- `dist/checksums.txt` (SHA-256 integrity manifest)

The version comes from `src/version.py`. To Authenticode-sign the exe and installer, set
`STUDIO_SIGN_PFX` (and `STUDIO_SIGN_PASSWORD`, optionally `STUDIO_SIGNTOOL`) before building; without
them the build proceeds unsigned. After editing UI classes run `python tools/build_frontend.py`
(needs Node.js; the shipped app needs nothing).

---

## 📄 License

MIT (see `LICENSE`). Third-party components keep their own licenses; downloading content may be restricted by the terms of the services involved.

---

## 🧪 Comprehensive Test Suite

The project is developed and tested on **Python 3.14** (the full suite also passes on 3.10). Create the
environment with `py -3.14 -m venv .venv` and install `requirements-dev.txt`.

Run the automated test suite verifying storage isolation, SQLite WAL mode, multi-factor matching, DPAPI encryption, and REST endpoints:
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tests/run_tests.py          # everything
.\.venv\Scripts\python.exe tests/run_tests.py --fast   # no browser / spawned servers (~25 s)
```
Static checks (both are clean): `python -m ruff check .` and `python -m mypy src`.

Tests run against a throwaway data directory under `temp/` (never your real database, downloads or config) and clean it up on exit. The browser tests also need `playwright install chromium`.
```powershell
# single module:
.\.venv\Scripts\python.exe -m unittest tests.test_suite
```

---

## 📁 Project Structure

```
Spotify_playlist_downloader/
│
├── .venv/                      # Isolated virtual environment (on D: drive)
├── .cache/                     # Local project cache and credentials (on D: drive)
├── bin/                        # Self-contained FFmpeg and FFprobe binaries
│   ├── ffmpeg.exe
│   └── ffprobe.exe
├── build/                      # Build workspace for PyInstaller
├── config/
│   └── config.json             # Configuration & preference settings
├── data/
│   ├── studio.db               # SQLite database in WAL mode
│   └── backups/                # Automated online database backups
├── dist/                       # Compiled release distribution
│   ├── SpotifyDownloader/      # Standalone portable application
│   │   ├── SpotifyDownloader.exe
│   │   ├── PORTABLE_QUICKSTART.txt
│   │   └── _internal/
│   ├── SpotifyDownloader-v2.0.0-Windows-x64.zip
│   └── checksums.txt           # SHA-256 release checksums
├── downloads/                  # Output directory for downloaded playlists and tracks
├── src/
│   ├── web/                    # Flask server, templates & modern Spotify UI
│   │   ├── static/             # js/*.js (ui.js = shared dialogs/toasts/a11y; handlers.js = event registry), style.css (design tokens)
│   │   ├── templates/          # index.html (5 destinations, sub-navigation, dialogs)
│   │   ├── server.py           # Flask app, session-token auth, blueprint registration
│   │   ├── state.py            # Shared singletons (queue, metadata, sync)
│   │   ├── routes/             # Blueprints: system, downloads, library, pages, automation, audio_lab
│   │   └── log_stream.py       # Thread-safe ring buffer log broadcaster
│   ├── compliant_audio.py      # Compliant audio retrieval engine (yt-dlp + backoff)
│   ├── config_loader.py        # Strict D: drive path enforcement & frozen app loader
│   ├── db.py                   # SQLite session, schema, audit logs & migrations
│   ├── desktop_shell.py        # pywebview native desktop window wrapper
│   ├── file_organizer.py       # Template naming & collision engine
│   ├── instance_lock.py        # Windows Named Mutex single-instance lock
│   ├── library_indexer.py      # Local file scanner, deduplication & M3U exporter
│   ├── link_parser.py          # Multi-format parser (URLs, URIs, search, CSV)
│   ├── match_engine.py         # Multi-factor fuzzy & ISRC matching engine
│   ├── media_processor.py      # FFmpeg transcoder & FFprobe integrity validator
│   ├── metadata_service.py     # Spotify Web API & AP metadata resolution with caching
│   ├── queue_manager.py        # Job state machine & thread pool balancer
│   ├── security.py             # Windows DPAPI encryption & secret redaction
│   ├── settings_service.py     # Unified settings manager with defaults & reset
│   ├── spotify_direct.py       # Direct Spotify Access Point client
│   └── sync_engine.py          # Playlist differential sync engine
│
├── tests/                      # unittest suite (hermetic temp data dir via _hermetic.py)
├── main.py                     # Launcher: opens the desktop app (plus --version / --diagnostics / mcp)
├── build_windows.py            # Windows release build automation script
├── package.spec                # PyInstaller packaging specification
├── version_info.txt            # Windows PE version resource definition
├── run.ps1                     # Development launcher (opens the app without a console)
├── requirements.txt            # Runtime dependencies
├── requirements-dev.txt        # + PyInstaller, Playwright
└── README.md                   # Complete documentation
```
