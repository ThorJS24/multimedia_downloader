# Spotify Downloader Studio — Known Limitations & System Boundaries

This document details external dependencies, platform boundaries, and architectural limitations of **Spotify Downloader Studio v2.0.0**.

---

## 1. Upstream Spotify Platform Policies
1. **Web API Developer Restrictions for Free Accounts**:
   - Spotify has implemented restrictions blocking developer apps from official Web API access (`/v1/tracks`, `/v1/playlists`) unless the account has an active Spotify Premium subscription.
   - **Application Behavior**: When official Spotify Web API credentials fail with HTTP 403 or auth errors, Spotify Downloader Studio automatically falls back to its in-process embed HTML parser. Metadata resolution and track discovery continue without Spotify API keys. Downloading audio from a Spotify link still needs a Spotify Premium session (see docs/SECURITY.md).

---

## 2. Audio Processing & Dependencies
1. **Spotify audio depends on `librespot` 0.0.10.**
   - It is the only maintained path to Spotify's audio servers, its newest release is the one pinned here, and it forces `protobuf` 3.20.x.
   - Compatibility with newer protobuf is *prepared but not switched on*: all 173 librespot message types round-trip identically on protobuf 5.29 and 6.33 when the pure-Python implementation is used (`tools/check_protobuf_compat.py` re-runs that check). A live Spotify login on a newer protobuf has not been tested, so the pin stays until it is.
   - If Spotify changes its protocol, downloads can stop working until librespot is updated upstream. Metadata lookup (titles, artwork, ISRC) does not use librespot and keeps working.
2. **GPU Hardware Acceleration**:
   - FFmpeg NVENC (NVIDIA) and Intel QSV hardware encoding require compatible local GPU hardware and drivers. When unsupported, FFmpeg automatically and gracefully falls back to CPU encoding using `libmp3lame`, `libopus`, and `flac`.

3. **Tag and artwork lookup is text-based.** *Fix tags & artwork automatically* matches by title, artist and length on MusicBrainz, not by audio fingerprint: the app's own fingerprints are not AcoustID/Chromaprint fingerprints, so files with no usable title or artist (or a wrong one) may find no match. It is limited to 200 tracks per run and one request per second, and it only embeds JPEG covers.
4. **Notifications and the tray depend on Windows.** The notification is a standard Windows balloon/toast through the tray icon; Focus Assist or "Do not disturb" can hide it. The tray only exists in the desktop window, not in `--headless` or `--browser` mode.
5. **The download schedule controls when downloads start, not how fast they go.** There is no bandwidth cap; the existing pause between tracks and the parallel-download setting are the throttles.
6. **Plugin packs are declarative.** They can send a web request or copy a file when something happens. They cannot run code, and they cannot copy outside the application folder.

---

## 3. Operating System & Filesystem Boundaries
1. **Storage containment**:
   - In accordance with the non-negotiable **zero writes to the system drive** rule, all application storage, databases, temp transcode buffers and logs live inside the application folder (the project folder when run from source, the install or portable folder for the packaged app).
   - The downloads folder, hook "copy" targets and bulk-organise destinations must also stay inside the application folder; the app rejects anything else.
   - OS-level operations (paging files, system driver caches) are managed by Windows and out of the app's control.
2. **Windows File Locks (WinError 32)**:
   - Aggressive third-party antivirus scanners or the Windows Search Indexer can occasionally lock newly created audio files for inspection. Spotify Downloader Studio incorporates an exponential 5-step retry loop with backoff to resolve file renaming conflicts safely.

---

## 4. Distribution
1. **Unsigned builds**: without a code-signing certificate the installer and exe are unsigned, so Windows SmartScreen shows "Windows protected your PC" on first run. Set `STUDIO_SIGN_PFX` (and `STUDIO_SIGN_PASSWORD`) before running `build_windows.py` to sign them.
2. **No automatic updates by default**: update checks stay off until an HTTPS update feed is configured in Settings, and installers are only launched if validly signed.
3. **Size**: the installer is about 93 MB and the unpacked app about 290 MB, most of it the bundled FFmpeg and FFprobe (about 200 MB, already the slim "essentials" build; the installer uses LZMA2 with solid compression). Going meaningfully lower means compiling an audio-only FFmpeg, which this project does not do; the app only needs MP3/AAC/Opus/Vorbis/FLAC/WAV, so that is the route if size ever matters more than convenience.
4. **Restoring a database backup needs a restart.** The restore is prepared while the app runs and applied the next time it opens, because replacing a live SQLite file under running threads is unsafe. Downloaded music files are never part of a database backup.
5. **CI has not been run on GitHub yet.** `.github/workflows/ci.yml` mirrors the local commands, but it was written without being able to execute a hosted runner; expect to adjust the FFmpeg or Python-version steps on the first run.
