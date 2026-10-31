# Upgrade, Recovery & Uninstall Safety Guide

## Overview
Spotify Downloader Studio is engineered with strict invariants safeguarding user data across versions, re-installations, and uninstallation.

---

## 1. Upgrade Safety Policy

### Binary Replacement vs User Data
The application strictly decouples binaries from user data:
- Application binaries reside in the installation directory (`%LOCALAPPDATA%\Programs\Spotify Downloader Studio`).
- User databases, historical download records, monitored artists, folder watcher queues, and configuration reside in the separate user data directory (`%LOCALAPPDATA%\SpotifyDownloaderStudio`).

### In-Place Upgrade Procedure
1. Close any running instances of Spotify Downloader Studio.
2. Run the new installer (`SpotifyDownloader-v2.0.0-Setup.exe`).
3. The installer overwrites existing application binaries in place.
4. **Guarantees:**
   - No database reset or recreation.
   - User settings in `config.json` are preserved.
   - Downloaded music files are never altered or deleted.
   - SQLite migrations execute automatically and idempotently on next startup.

---

## 2. Backup and Disaster Recovery

### Automatic & Manual Backups
Spotify Downloader Studio features integrated database backup mechanisms via `src/backup_service.py`:
- Backups are stored in `data/backups/`.
- Safe SQLite backup using SQLite's online backup API (`VACUUM INTO` or atomic copy while WAL checkpointed).

### Triggering a Manual Backup
Through the REST API:
```http
POST /api/backups/create
Content-Type: application/json
X-Session-Token: <SESSION_TOKEN>

{
  "reason": "pre_migration_backup"
}
```

Or from a terminal with the diagnostics switch:
```powershell
.\SpotifyDownloader.exe --diagnostics
```

### Verifying Database Integrity
The application routinely runs `PRAGMA integrity_check` on startup and during diagnostics.
If the database detects corruption:
1. Stop the application.
2. Check `data/backups/` for the latest uncorrupted backup.
3. Replace `data/studio.db` with the backup copy.
4. Remove `studio.db-wal` and `studio.db-shm` if present.
5. Re-launch the application.

---

## 3. Safe Uninstallation Behavior

### Standard Uninstaller
Spotify Downloader Studio can be uninstalled normally via:
- **Windows Settings** → **Apps** → **Installed apps** → **Spotify Downloader Studio** → **Uninstall**
- Or running the uninstaller directly:
  `%LOCALAPPDATA%\Programs\Spotify Downloader Studio\unins000.exe`

### Silent Uninstallation
```powershell
& "$env:LOCALAPPDATA\Programs\Spotify Downloader Studio\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

### What Gets Removed vs What is Preserved
- **Cleanly Removed:**
  - Application executable (`SpotifyDownloader.exe`)
  - Internal dependencies and runtimes (`_internal/`)
  - Start Menu shortcuts
  - Desktop shortcuts
  - Windows registry uninstall keys in `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall`
- **Strictly Preserved (Non-Destructive Invariant):**
  - User SQLite database (`%LOCALAPPDATA%\SpotifyDownloaderStudio\data\studio.db`)
  - User configuration (`%LOCALAPPDATA%\SpotifyDownloaderStudio\config\config.json`)
  - Monitored folder database records
  - All downloaded and indexed audio files in user music directories

### Complete Data Purge (Optional)
If a user explicitly wishes to remove all personal data and download records, they can manually delete the data directory:
```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\SpotifyDownloaderStudio"
```
