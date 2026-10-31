# Spotify Downloader Studio — Known Limitations & System Boundaries

This document details external dependencies, platform boundaries, and architectural limitations of **Spotify Downloader Studio v2.0.0**.

---

## 1. Upstream Spotify Platform Policies
1. **Web API Developer Restrictions for Free Accounts**:
   - Spotify has implemented restrictions blocking developer apps from official Web API access (`/v1/tracks`, `/v1/playlists`) unless the account has an active Spotify Premium subscription.
   - **Application Behavior**: When official Spotify Web API credentials fail with HTTP 403 or auth errors, Spotify Downloader Studio automatically falls back to its in-process embed HTML parser. Metadata resolution and track discovery continue without Spotify API keys. Downloading audio from a Spotify link still needs a Spotify Premium session (see docs/SECURITY.md).

---

## 2. Audio Processing & Model Dependencies
1. **AI Stem Separation (Demucs / Spleeter)**:
   - Deep-learning audio stem separation requires PyTorch and pre-trained neural network weights (~2.5 GB to ~4.0 GB).
   - These model weights are intentionally omitted from the standard Windows installer (`SpotifyDownloader-v2.0.0-Setup.exe`, ~249 MB) to maintain lightweight portable distribution. Users wishing to run local stem separation can attach their Python PyTorch environment via the Plugin Engine (`src/plugin_engine.py`).
2. **GPU Hardware Acceleration**:
   - FFmpeg NVENC (NVIDIA) and Intel QSV hardware encoding require compatible local GPU hardware and drivers. When unsupported, FFmpeg automatically and gracefully falls back to CPU encoding using `libmp3lame`, `libopus`, and `flac`.

---

## 3. Operating System & Filesystem Boundaries
1. **Windows Volume Isolation**:
   - In accordance with the non-negotiable **Zero C: Drive Writes** rule, all application storage, databases, temp transcode buffers, and logs are contained strictly within `D:\mini-projects\Spotify_playlist_downloader`.
   - OS-level kernel operations (paging files, system driver caches) are managed exclusively by Windows.
2. **Windows File Locks (WinError 32)**:
   - Aggressive third-party antivirus scanners or the Windows Search Indexer can occasionally lock newly created audio files for inspection. Spotify Downloader Studio incorporates an exponential 5-step retry loop with backoff to resolve file renaming conflicts safely.
