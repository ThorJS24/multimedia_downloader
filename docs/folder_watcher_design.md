# 📂 Spotify Downloader Studio — Folder Watcher & Safe Library Intake Architecture

## 1. Executive Summary & Design Principles

The **Folder Watcher & Safe Library Intake** subsystem (`src/folder_watcher.py`) extends Spotify Downloader Studio with automated, non-destructive filesystem monitoring for local music directories. It enables users to designate watched folders that continuously detect new audio files, modifications, renames, and deletions.

### Core Architectural Principles
1. **Zero-Destructive Intake**: Filesystem events **never** mutate audio tags, download replacement tracks, or delete database/library items automatically.
2. **Mandatory Human-in-the-Loop Review**: All newly detected audio files are staged into `staging_items` as `source_type='WATCHER'`. They remain in review until explicitly approved by the user through the dashboard or API.
3. **Write-Settling & Lock Verification**: External media players, torrent clients, or file copiers often write audio files in increments. The watcher enforces a settling window, verifying that file size and modification time have stabilized and that no write handles remain open before staging.
4. **Resilient Filesystem Monitoring**: Uses native Windows API event notifications via `watchdog` (`ReadDirectoryChangesW`) with an automatic, seamless fallback to polling (`PollingObserver`) if native hooks fail or are unsupported.
5. **Rigorous Storage & Directory Containment**: Prohibits monitoring root drives (e.g., `C:\`, `D:\`), system directories (`Windows`, `Program Files`), or paths outside permitted workspace roots.

---

## 2. Subsystem Architecture

```
+-----------------------------------------------------------------------------------+
|                            Operating System Filesystem                            |
+-----------------------------------------------------------------------------------+
                                         │
                 Native Win32 ReadDirectoryChangesW / Polling
                                         │
                                         ▼
+-----------------------------------------------------------------------------------+
|                             _WatchdogEventHandler                                 |
|  - on_created() / on_modified() / on_moved() / on_deleted()                       |
|  - Extension filtering (.mp3, .flac, .m4a, .ogg, .wav, .opus, .aac, .wma)         |
+-----------------------------------------------------------------------------------+
                                         │
                               Queues event
                                         │
                                         ▼
+-----------------------------------------------------------------------------------+
|                        FolderWatcherService Debouncer                             |
|  - Thread-safe pending event registry with RLock                                  |
|  - Debounce timer (default: 3.0s)                                                 |
|  - Write-Settling Engine (monitors size & mtime stability over settle window)     |
|  - File-lock test (exclusive read attempt via os.open / O_RDONLY)                 |
+-----------------------------------------------------------------------------------+
                                         │
                               Once stabilized
                                         │
                                         ▼
+-----------------------------------------------------------------------------------+
|                        StagingService (Safe Intake)                               |
|  - Extracts tags via Mutagen (safe, read-only)                                    |
|  - Queries Duplicate Detector / Fingerprint Service                               |
|  - Inserts into SQLite `staging_items` with source_type='WATCHER'                 |
|  - Records diagnostic event in `watcher_events`                                   |
+-----------------------------------------------------------------------------------+
                                         │
                                         ▼
+-----------------------------------------------------------------------------------+
|                             Flask Web Dashboard (UI)                              |
|  - Tab 14: #tab-watcher                                                           |
|  - Watched Folders Table (status, file counts, last scanned, enable/disable)      |
|  - Pending Intake Queue (Approve -> Single-file Indexing / Dismiss -> Ignore)     |
+-----------------------------------------------------------------------------------+
```

---

## 3. Database Schema & Persistence

Folder watcher state and audit logs are governed by **Database Migration 5** in `src/db.py`.

### 3.1 `watched_folders` Table
Persists user-configured directories and their scanning settings:
```sql
CREATE TABLE IF NOT EXISTS watched_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_path TEXT UNIQUE NOT NULL,
    label TEXT,
    recursive INTEGER DEFAULT 1,
    enabled INTEGER DEFAULT 1,
    last_scanned_at TIMESTAMP,
    file_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 `watcher_events` Table
Maintains an immutable historical audit log of detected filesystem activities:
```sql
CREATE TABLE IF NOT EXISTS watcher_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id INTEGER REFERENCES watched_folders(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,       -- CREATED, MODIFIED, MOVED, DELETED, STAGED, ERROR
    file_path TEXT NOT NULL,
    details TEXT,                  -- JSON encoded metadata, file size, previous path
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.3 `staging_items` Table Extensions
The existing staging pipeline was enhanced to record watcher-origin items seamlessly alongside download batches:
- `source_type TEXT DEFAULT 'DOWNLOAD'`: Distinguishes `DOWNLOAD` vs `WATCHER` origins.
- `source_path TEXT`: Records the absolute original path on disk for local files.
- `detection_reason TEXT`: Captures intake rationale (e.g., `NEW_LOCAL_FILE`, `LOCAL_FILE_MODIFIED`).

---

## 4. Event Processing & Write-Settling Mechanics

### 4.1 Supported Audio Extensions
The watcher filters events strictly against compliant audio formats:
`.mp3`, `.flac`, `.m4a`, `.ogg`, `.opus`, `.wav`, `.aac`, `.wma`.

### 4.2 Write-Settling State Machine
When files are actively being downloaded or copied by third-party utilities, processing them immediately risks reading corrupted partial frames or encountering file-lock collisions.
To eliminate this race condition, `FolderWatcherService` implements a three-tier settling validation:
1. **Debounce Queueing**: When an event occurs, it is recorded in `_pending_events[file_path]` with timestamp `time.time()`. If subsequent write bursts occur, the timestamp resets.
2. **Settling Window Verification**: Background thread checks if `(now - last_event_time) >= settling_window_seconds` (default: 3.0s).
3. **Lock & Growth Invariance**:
   ```python
   def _is_file_ready(self, path: Path) -> bool:
       try:
           size_1 = path.stat().st_size
           mtime_1 = path.stat().st_mtime
           time.sleep(0.5)
           size_2 = path.stat().st_size
           mtime_2 = path.stat().st_mtime
           if size_1 != size_2 or mtime_1 != mtime_2:
               return False  # Still actively growing
           # Exclusive read lock check
           with open(path, "rb") as f:
               f.read(512)
           return True
       except (OSError, PermissionError):
           return False  # Locked by another process
   ```

### 4.3 Safe Deletion & Non-Destructive Behavior
If a file in a watched directory is removed:
1. An event of type `DELETED` is logged into `watcher_events`.
2. Existing indexed records in the music database are **never** automatically purged.
3. If the file was in `staging_items` with `status='PENDING'`, its status is transitioned to `DISMISSED` with the note `File deleted from watched folder prior to approval`.
4. No network requests, downloads, or destructive operations occur.

---

## 5. Security & Boundary Defense

The folder watcher integrates with `SecurityManager` (`src/security.py`) and Windows path validation:
1. **Root Directory Rejection**: Folders evaluating to `C:\`, `D:\`, `/`, or bare drive roots are strictly rejected to prevent recursive enumeration of entire volumes.
2. **System Directory Block**: Prohibits watching `C:\Windows`, `C:\Program Files`, `%TEMP%`, or sensitive system folders.
3. **Path Canonicalization**: Resolves symlinks, `..` traversal tokens, and Windows drive letter cases before persistence in SQLite.
4. **Containment Guarantee**: All internal logs, database records, and temporary files remain strictly inside `D:\mini-projects\Spotify_playlist_downloader`. Zero writes to `C:`.

---

## 6. REST API Specification

| Endpoint | Method | Payload / Params | Description |
| :--- | :---: | :--- | :--- |
| `/api/watcher/status` | GET | None | Returns active state, paused state, folder count, pending items count, and observer type. |
| `/api/watcher/start` | POST | None | Starts background monitoring on all enabled watched directories. |
| `/api/watcher/stop` | POST | None | Cleanly stops all active watchers and background debouncer thread. |
| `/api/watcher/pause` | POST | None | Temporarily suppresses event intake without unmounting observers. |
| `/api/watcher/resume` | POST | None | Resumes event intake. |
| `/api/watcher/folders` | GET | None | Returns list of configured watched folders with recursive status, file counts, and timestamps. |
| `/api/watcher/folders/add` | POST | `{"folder_path": str, "label": str, "recursive": bool}` | Adds a new directory, runs initial validation, and schedules watcher. |
| `/api/watcher/folders/remove`| POST | `{"folder_id": int}` | Unwatches directory and deletes record from `watched_folders`. |
| `/api/watcher/folders/toggle`| POST | `{"folder_id": int, "enabled": bool}` | Dynamically pauses/resumes watching a specific directory. |
| `/api/watcher/folders/rescan`| POST | `{"folder_id": int}` | Triggers immediate manual crawl of directory for missed or updated files. |
| `/api/watcher/pending` | GET | None | Returns pending intake items staged from watcher with metadata, duplicate flag, and paths. |
| `/api/watcher/items/approve` | POST | `{"item_id": int}` or `{"item_ids": [...]}` | Approves staged item(s), commits them, and indexes them into the music library. |
| `/api/watcher/items/dismiss` | POST | `{"item_id": int}` or `{"item_ids": [...]}` | Dismisses staged item(s) without library indexing. |
