# AGENTS.md — Permanent Engineering Guidelines & Operational Constraints

## 1. Core Mission & Architectural Context
Spotify Downloader Studio is a release-ready, enterprise-grade music library, audio retrieval, tagging, and playlist management application built for Windows x64.
Its architecture strictly decouples:
- **Spotify Metadata Resolution & Direct Stream Discovery** (`src/metadata_service.py`, `src/spotify_direct.py`)
- **Compliant Audio Retrieval & Fingerprinted Matching** (`src/compliant_audio.py`, `src/match_engine.py`, `src/fingerprint_service.py`)
- **Queue Dispatching & Concurrency Balancing** (`src/queue_manager.py`)
- **Transcoding, Tagging & Loudness Normalization** (`src/media_processor.py`, `src/tag_writer.py`, `src/quality_service.py`)
- **Library Intelligence & Release Automation** (`src/library_intelligence.py`, `src/scheduler_service.py`, `src/plugin_engine.py`)
- **Storage, Database & Desktop Experience** (`src/config_loader.py`, `src/db.py`, `src/desktop_shell.py`, `src/web/server.py`)

---

## 2. Mandatory Storage & Containment Constraints (NON-NEGOTIABLE)
1. **Zero C: Drive Writes**:
   - The primary drive (`C:`) has constrained capacity.
   - Every file, cache, virtual environment, temporary buffer, database, log, intermediate transcode artifact, and PyInstaller build output **must be stored strictly within the project folder**:
     `d:\mini-projects\Spotify_playlist_downloader`.
2. **Environment Overrides**:
   - `TEMP` and `TMP` environment variables are redirected by `src/config_loader.py` to `PROJECT_ROOT / "temp"`.
   - SQLite databases are persisted in `PROJECT_ROOT / "data"` with Write-Ahead Logging (`WAL`) mode enabled.
   - Python caches and credentials reside in `PROJECT_ROOT / ".cache"`.
   - Never write to `%USERPROFILE%`, `%APPDATA%`, or `%TEMP%` on `C:`.

---

## 3. Engineering & Modification Rules
1. **Inspect Before Implementing**:
   - Always verify existing implementations before adding new files or functions.
   - Do not create parallel, duplicate modules. Reuse the enterprise engines (`QueueManager`, `MetadataService`, `MediaProcessor`, `FileOrganizer`, `TagWriter`).
2. **In-Process APIs Over Fragile Subprocesses**:
   - Do not call external CLI executables like `yt-dlp` via `subprocess.run(["yt-dlp", ...])` when the Python package (`yt_dlp`) is installed in `.venv`. Always use in-process library APIs (`yt_dlp.YoutubeDL`) to eliminate dependencies on system `PATH`.
   - Local binaries (`bin/ffmpeg.exe` and `bin/ffprobe.exe`) must be resolved via `get_ffmpeg_bin()` and `get_ffprobe_bin()`.
3. **Data Safety & Atomic Operations**:
   - Never perform destructive file operations without validation.
   - Media tagging and transcoding must write to temporary buffers in `PROJECT_ROOT / "temp"` and atomically move to the destination.
   - SQLite queries must use parameterized statements (`?`) to prevent SQL injection and schema corruption.
4. **Credential Security**:
   - Secrets, session tokens, passwords, and API keys must never be logged to console output, log files, or persisted in git.
   - Session data in `.cache/credentials.json` must be masked in any diagnostics reports.
5. **Platform Compliance & Rate Limiting**:
   - Honor HTTP `429 Too Many Requests` responses with exponential backoff and adhere to `Retry-After` headers.
   - Separate metadata lookup from audio acquisition with transparent fallback.

---

## 4. Verification & Testing Standards
1. **Automated Test Suite**:
   - Every modification must be validated against `tests/test_suite.py`.
   - Tests must be idempotent, cleanup temporary artifacts, and mock network services to guarantee zero network flakes.
2. **Packaging Verification**:
   - Windows executable builds must be tested via `build_windows.py` ensuring `--version` and `--diagnostics` succeed cleanly.
