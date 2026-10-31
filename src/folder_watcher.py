"""
Folder Watcher Service & Safe Library Intake Engine.
Provides background monitoring of user-selected music folders for new, modified,
renamed, and removed audio files with debouncing, write settling validation,
and safe import staging without destructive operations.
"""

import atexit
import logging
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
    from watchdog.observers import Observer
    from watchdog.observers.polling import PollingObserver
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    # watchdog is optional: these placeholders keep the module importable without it
    FileSystemEventHandler = object      # type: ignore[misc,assignment]
    FileSystemEvent = object             # type: ignore[misc,assignment]
    Observer = None                      # type: ignore[misc,assignment]
    PollingObserver = None               # type: ignore[misc,assignment]

from src.db import get_db_connection, record_audit
from src.library_indexer import LibraryIndexer
from src.security import SecurityManager
from src.settings_service import SettingsService
from src.staging_service import StagingService

logger = logging.getLogger("FolderWatcher")


IGNORED_EXTENSIONS = {
    ".tmp", ".crdownload", ".part", ".partial", ".swp", ".bak",
    ".temp", ".download", ".ds_store", ".ini", ".log"
}


class _WatchdogEventHandler(FileSystemEventHandler):
    """Event handler bridging watchdog filesystem notifications into FolderWatcherService."""

    def __init__(self, service: "FolderWatcherService", folder_id: str):
        super().__init__()
        self.service = service
        self.folder_id = folder_id

    def on_created(self, event):
        if not event.is_directory:
            self.service._enqueue_event("CREATED", event.src_path, self.folder_id)

    def on_modified(self, event):
        if not event.is_directory:
            self.service._enqueue_event("MODIFIED", event.src_path, self.folder_id)

    def on_moved(self, event):
        if not event.is_directory:
            self.service._enqueue_event("MOVED", event.dest_path, self.folder_id, old_path=event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self.service._enqueue_event("DELETED", event.src_path, self.folder_id)


class FolderWatcherService:
    """
    Filesystem watcher service for local music libraries.
    Features:
    - Multiple monitored folders with independent enable/disable
    - Recursive monitoring
    - Debounce and write-settling validation (guards against incomplete writes)
    - Invariant safety: files are staged for review; zero automatic commits or deletions
    - Observer with polling fallback
    - Clean lifecycle (start/stop/pause/resume/rescan)
    """

    _lock = threading.RLock()
    _observer: Optional[Any] = None
    _scheduled_watches: Dict[str, Any] = {}  # folder_id -> watchdog watch object

    _status: str = "STOPPED"  # STOPPED, RUNNING, PAUSED, ERROR
    _is_paused: bool = False
    _last_error: Optional[str] = None
    _recent_errors: List[str] = []

    # Background worker and event processing
    _event_queue: queue.Queue = queue.Queue(maxsize=1000)
    _debounce_map: Dict[str, Dict[str, Any]] = {}  # canonical_path -> event_metadata
    _worker_thread: Optional[threading.Thread] = None
    _worker_stop_event = threading.Event()

    # Scanning concurrency control
    _scanning_folders: Set[str] = set()

    # Metrics
    _metrics = {
        "created": 0,
        "modified": 0,
        "moved": 0,
        "deleted": 0,
        "last_scan_at": 0.0
    }

    # Settling configuration (seconds)
    SETTLING_DELAY_SEC = 1.5
    MAX_SETTLING_WAIT_SEC = 10.0

    # ================= LIFECYCLE CONTROLS =================

    @classmethod
    def start(cls) -> bool:
        """Starts the watcher service and observer threads. Idempotent."""
        with cls._lock:
            if cls._status == "RUNNING":
                return True

            logger.info("Starting FolderWatcherService...")
            cls._worker_stop_event.clear()
            cls._is_paused = False
            cls._last_error = None

            # 1. Start worker thread for debouncing and stable-file intake
            if cls._worker_thread is None or not cls._worker_thread.is_alive():
                cls._worker_thread = threading.Thread(
                    target=cls._event_processor_loop,
                    name="FolderWatcherWorkerThread",
                    daemon=True
                )
                cls._worker_thread.start()

            # 2. Initialize and start watchdog observer
            try:
                if cls._observer is not None:
                    try:
                        cls._observer.stop()
                        cls._observer.join(timeout=1.0)
                    except Exception:
                        logger.debug("Suppressed exception", exc_info=True)
                    cls._observer = None

                cfg = SettingsService.get_all().get("watcher", {})
                use_polling = cfg.get("use_polling_fallback", False) or not WATCHDOG_AVAILABLE

                if use_polling and PollingObserver is not None:
                    cls._observer = PollingObserver(timeout=cfg.get("polling_interval_sec", 3.0))
                    logger.info("Using PollingObserver fallback for FolderWatcher.")
                elif WATCHDOG_AVAILABLE and Observer is not None:
                    cls._observer = Observer()
                    logger.info("Using native Watchdog Observer for FolderWatcher.")
                else:
                    cls._observer = None
                    logger.warning("Watchdog not available; relying exclusively on manual/polling scans.")

                # Schedule enabled folders
                cls._sync_scheduled_watches()

                if cls._observer:
                    cls._observer.start()

                cls._status = "RUNNING"
                record_audit("WATCHER", "FolderWatcherService started.")
                return True

            except Exception as e:
                cls._status = "ERROR"
                cls._last_error = str(e)
                cls._record_error(f"Failed to start watcher observer: {e}")
                logger.error(f"FolderWatcher start error: {e}", exc_info=True)
                return False

    @classmethod
    def stop(cls) -> bool:
        """Stops the watcher service and worker threads cleanly. Idempotent."""
        with cls._lock:
            if cls._status == "STOPPED":
                return True

            logger.info("Stopping FolderWatcherService...")
            cls._worker_stop_event.set()

            # Stop observer
            if cls._observer is not None:
                try:
                    cls._observer.stop()
                    cls._observer.join(timeout=3.0)
                except Exception as e:
                    logger.warning(f"Error stopping observer: {e}")
                cls._observer = None
                cls._scheduled_watches.clear()

            # Join worker thread
            if cls._worker_thread is not None and cls._worker_thread.is_alive():
                cls._worker_thread.join(timeout=2.0)
                cls._worker_thread = None

            cls._status = "STOPPED"
            cls._is_paused = False
            record_audit("WATCHER", "FolderWatcherService stopped cleanly.")
            return True

    @classmethod
    def pause(cls) -> bool:
        """Pauses processing of filesystem events without stopping the observer."""
        with cls._lock:
            if cls._status == "RUNNING":
                cls._is_paused = True
                cls._status = "PAUSED"
                logger.info("FolderWatcherService paused.")
                record_audit("WATCHER", "FolderWatcherService paused.")
                return True
            return False

    @classmethod
    def resume(cls) -> bool:
        """Resumes processing of filesystem events."""
        with cls._lock:
            if cls._status == "PAUSED":
                cls._is_paused = False
                cls._status = "RUNNING"
                logger.info("FolderWatcherService resumed.")
                record_audit("WATCHER", "FolderWatcherService resumed.")
                return True
            return False

    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        """Returns comprehensive diagnostic and operational status."""
        with cls._lock:
            folders = cls.get_folders()
            active_folders = [f for f in folders if f.get("is_enabled") and f.get("status") == "ACTIVE"]
            pending_items = StagingService.get_pending_watcher_items()

            return {
                "status": cls._status,
                "is_paused": cls._is_paused,
                "total_folders": len(folders),
                "active_folders": len(active_folders),
                "folders": folders,
                "metrics": dict(cls._metrics),
                "pending_intake_count": len(pending_items),
                "last_error": cls._last_error,
                "recent_errors": list(cls._recent_errors[-10:]),
                "watchdog_available": WATCHDOG_AVAILABLE
            }

    # ================= FOLDER MANAGEMENT & VALIDATION =================

    @classmethod
    def add_folder(cls, path: str, label: str = "", recursive: bool = True, auto_scan: bool = True) -> Dict[str, Any]:
        """
        Validates, canonicalizes, and adds a folder to the monitored list.
        Enforces strict path containment and security checks.
        """
        if not path or not isinstance(path, str):
            raise ValueError("Directory path must be a non-empty string.")

        try:
            target_path = Path(path).resolve()
        except Exception as e:
            raise ValueError(f"Invalid path syntax: {e}") from e

        # Security check: Never monitor root of drive
        if len(target_path.parts) <= 1 or target_path.parent == target_path:
            raise ValueError("Monitoring entire drive root (e.g. C:\\ or D:\\) is strictly prohibited.")

        # Security check: Never monitor UNC network shares
        target_str = str(target_path)
        if target_str.startswith("\\\\") or target_path.as_posix().startswith("//"):
            raise ValueError("UNC paths and network shares are strictly prohibited as watched folders.")

        # Security check: Never monitor Windows system directories
        system_roots = []
        for env_var in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            val = os.environ.get(env_var)
            if val:
                try:
                    system_roots.append(Path(val).resolve())
                except Exception:
                    logger.debug("Suppressed exception", exc_info=True)
        for fallback in (r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)"):
            try:
                p_fb = Path(fallback).resolve()
                if p_fb.exists() and p_fb not in system_roots:
                    system_roots.append(p_fb)
            except Exception:
                logger.debug("Suppressed exception", exc_info=True)

        for sroot in system_roots:
            is_sys = False
            try:
                if target_path == sroot or target_path.is_relative_to(sroot):
                    is_sys = True
            except Exception:
                logger.debug("Suppressed exception", exc_info=True)
            if is_sys:
                raise ValueError(f"Monitoring system directory '{target_path}' is strictly prohibited.")

        if not target_path.exists():
            raise FileNotFoundError(f"Directory does not exist: {target_path}")

        if not target_path.is_dir():
            raise ValueError(f"Target path is a file, not a directory: {target_path}")

        # Check symlink or junction escapes
        if target_path.is_symlink():
            resolved = target_path.resolve()
            if not resolved.exists():
                raise ValueError("Symlink target is invalid or inaccessible.")
            for sroot in system_roots:
                is_sym_sys = False
                try:
                    if resolved == sroot or resolved.is_relative_to(sroot):
                        is_sym_sys = True
                except Exception:
                    logger.debug("Suppressed exception", exc_info=True)
                if is_sym_sys:
                    raise ValueError("Symlink target resolves to a prohibited system directory.")

        # Test accessibility / read permission
        try:
            next(target_path.iterdir(), None)
        except PermissionError:
            raise PermissionError(f"Permission denied accessing directory: {target_path}") from None
        except Exception as e:
            raise OSError(f"Unable to read directory: {e}") from e

        folder_id = f"watch_{uuid.uuid4().hex[:8]}"
        label = label.strip() or target_path.name
        now = time.time()

        conn = get_db_connection()
        with conn:
            # Check for existing folder with same path
            existing = conn.execute("SELECT id FROM watched_folders WHERE path = ? COLLATE NOCASE", (str(target_path),)).fetchone()
            if existing:
                raise ValueError(f"Directory is already registered under ID '{existing['id']}'.")

            conn.execute(
                """
                INSERT INTO watched_folders (id, path, label, recursive, is_enabled, added_at, status)
                VALUES (?, ?, ?, ?, 1, ?, 'ACTIVE')
                """,
                (folder_id, str(target_path), label, 1 if recursive else 0, now)
            )

        logger.info(f"Added watched folder: '{target_path}' [{folder_id}]")
        record_audit("WATCHER", f"Added watched folder '{target_path}'")

        # Sync with running observer
        with cls._lock:
            cls._sync_scheduled_watches()

        # Trigger non-blocking initial scan in background if requested
        if auto_scan:
            cls._trigger_async_folder_scan(folder_id, target_path, recursive)

        return {
            "id": folder_id,
            "path": str(target_path),
            "label": label,
            "recursive": recursive,
            "is_enabled": True,
            "status": "ACTIVE"
        }

    @classmethod
    def remove_folder(cls, folder_id_or_path: str) -> bool:
        """Removes a folder from the watcher configuration."""
        norm_path = None
        try:
            p_cand = Path(folder_id_or_path)
            if p_cand.exists():
                norm_path = str(p_cand.resolve())
        except Exception:
            logger.debug("Suppressed exception", exc_info=True)

        conn = get_db_connection()
        with conn:
            cur = conn.execute(
                """
                SELECT id, path FROM watched_folders
                WHERE id = ?
                   OR path = ? COLLATE NOCASE
                   OR (path = ? COLLATE NOCASE AND ? IS NOT NULL)
                """,
                (folder_id_or_path, folder_id_or_path, norm_path, norm_path)
            )
            row = cur.fetchone()
            if not row:
                return False

            fid = row["id"]
            conn.execute("DELETE FROM watched_folders WHERE id = ?", (fid,))
            conn.execute("DELETE FROM watcher_events WHERE folder_id = ?", (fid,))

        logger.info(f"Removed watched folder [{fid}]: {row['path']}")
        record_audit("WATCHER", f"Removed watched folder '{row['path']}'")

        with cls._lock:
            cls._sync_scheduled_watches()
        return True

    @classmethod
    def set_folder_enabled(cls, folder_id_or_path: str, is_enabled: bool) -> bool:
        """Enables or disables monitoring for a specific folder."""
        norm_path = None
        try:
            p_cand = Path(folder_id_or_path)
            if p_cand.exists():
                norm_path = str(p_cand.resolve())
        except Exception:
            logger.debug("Suppressed exception", exc_info=True)

        conn = get_db_connection()
        with conn:
            cur = conn.execute(
                """
                UPDATE watched_folders SET is_enabled = ?
                WHERE id = ?
                   OR path = ? COLLATE NOCASE
                   OR (path = ? COLLATE NOCASE AND ? IS NOT NULL)
                """,
                (1 if is_enabled else 0, folder_id_or_path, folder_id_or_path, norm_path, norm_path)
            )
            if cur.rowcount == 0:
                return False

        with cls._lock:
            cls._sync_scheduled_watches()
        return True

    @classmethod
    def get_folders(cls) -> List[Dict[str, Any]]:
        """Retrieves all registered watched folders from SQLite."""
        conn = get_db_connection()
        cur = conn.execute(
            """
            SELECT id, path, label, recursive, is_enabled, added_at, last_scanned_at, status, error_message
            FROM watched_folders
            ORDER BY added_at ASC
            """
        )
        return [
            {
                "id": r["id"],
                "path": r["path"],
                "label": r["label"],
                "recursive": bool(r["recursive"]),
                "is_enabled": bool(r["is_enabled"]),
                "added_at": r["added_at"],
                "last_scanned_at": r["last_scanned_at"],
                "status": r["status"],
                "error_message": r["error_message"]
            }
            for r in cur.fetchall()
        ]

    # ================= WATCHDOG OBSERVER SYNCHRONIZATION =================

    @classmethod
    def _sync_scheduled_watches(cls):
        """Ensures the watchdog observer matches current enabled folders in SQLite."""
        if cls._observer is None:
            return

        folders = cls.get_folders()
        active_map = {f["id"]: f for f in folders if f["is_enabled"]}

        # Unschedule removed/disabled folders
        for fid in list(cls._scheduled_watches.keys()):
            if fid not in active_map:
                try:
                    cls._observer.unschedule(cls._scheduled_watches[fid])
                except Exception as e:
                    logger.debug(f"Unschedule error for folder {fid}: {e}")
                del cls._scheduled_watches[fid]

        # Schedule new/enabled folders
        for fid, f in active_map.items():
            if fid not in cls._scheduled_watches:
                p = Path(f["path"])
                if p.exists() and p.is_dir():
                    try:
                        handler = _WatchdogEventHandler(cls, fid)
                        watch = cls._observer.schedule(handler, str(p), recursive=f["recursive"])
                        cls._scheduled_watches[fid] = watch
                        logger.info(f"Observer scheduled watch on: {p} (recursive={f['recursive']})")
                    except Exception as e:
                        cls._record_error(f"Failed to schedule watch on {p}: {e}")
                        conn = get_db_connection()
                        with conn:
                            conn.execute(
                                "UPDATE watched_folders SET status = 'ERROR', error_message = ? WHERE id = ?",
                                (str(e), fid)
                            )

    # ================= EVENT FILTERING & DEBOUNCING =================

    @classmethod
    def _is_supported_audio_file(cls, path_str: str) -> bool:
        """Validates that path is a recognized non-temporary audio file."""
        p = Path(path_str)
        ext = p.suffix.lower()

        # Reject temporary or hidden files
        if p.name.startswith(".") or ext in IGNORED_EXTENSIONS or ".tmp" in p.name.lower():
            return False

        # Support audio extensions defined in LibraryIndexer
        return ext in LibraryIndexer.SUPPORTED_EXTS

    @classmethod
    def _enqueue_event(cls, event_type: str, path_str: str, folder_id: str, old_path: Optional[str] = None):
        """Validates and pushes a raw filesystem event to the debounce queue."""
        if cls._is_paused:
            return

        if not cls._is_supported_audio_file(path_str):
            return

        try:
            resolved_path = Path(path_str).resolve()
            canonical_path = str(resolved_path)
        except Exception:
            canonical_path = path_str
            resolved_path = Path(path_str)

        # Security containment: Verify that resolved path does not escape the watched folder boundary
        try:
            conn = get_db_connection()
            f_row = conn.execute("SELECT path FROM watched_folders WHERE id = ?", (folder_id,)).fetchone()
            if f_row:
                base_dir = Path(f_row["path"]).resolve()
                if not SecurityManager.validate_safe_path(resolved_path, base_dir):
                    logger.warning(f"Dropping event for file escaping watched boundary: {path_str} -> {canonical_path}")
                    return
        except Exception as e:
            logger.debug(f"Boundary verification error: {e}")

        now = time.time()
        with cls._lock:
            # Debounce burst events for the same path
            cls._debounce_map[canonical_path] = {
                "event_type": event_type,
                "path": canonical_path,
                "folder_id": folder_id,
                "old_path": old_path,
                "first_seen": cls._debounce_map.get(canonical_path, {}).get("first_seen", now),
                "last_seen": now,
                "last_size": -1,
                "stable_count": 0
            }

    @classmethod
    def _event_processor_loop(cls):
        """
        Background worker thread:
        Monitors _debounce_map for stable files whose size and modification time have settled,
        then dispatches them to staging.
        """
        logger.info("FolderWatcher event processor loop started.")
        while not cls._worker_stop_event.is_set():
            time.sleep(0.5)

            if cls._is_paused:
                continue

            now = time.time()
            stable_items = []

            with cls._lock:
                for path_str, item in list(cls._debounce_map.items()):
                    # Has file settled beyond SETTLING_DELAY_SEC since last event?
                    if now - item["last_seen"] < cls.SETTLING_DELAY_SEC:
                        continue

                    # For DELETED events, file is gone, dispatch immediately
                    if item["event_type"] == "DELETED":
                        stable_items.append(item)
                        del cls._debounce_map[path_str]
                        continue

                    # Check write settling: verify file accessibility and constant size
                    p = Path(path_str)
                    if not p.exists():
                        del cls._debounce_map[path_str]
                        continue

                    try:
                        cur_size = p.stat().st_size
                        # Verify we can open file for reading (ensures file is not locked by writer)
                        with open(p, "rb") as test_f:
                            test_f.read(128)

                        if item["last_size"] == cur_size:
                            item["stable_count"] += 1
                        else:
                            item["last_size"] = cur_size
                            item["stable_count"] = 0
                            continue

                        # Stable for at least 1 check cycle
                        if item["stable_count"] >= 1:
                            stable_items.append(item)
                            del cls._debounce_map[path_str]

                    except (PermissionError, OSError):
                        # File still being written to / locked by writer
                        if now - item["first_seen"] > cls.MAX_SETTLING_WAIT_SEC:
                            logger.warning(f"File settling timed out for {path_str}; dropping event.")
                            del cls._debounce_map[path_str]
                        continue

            # Process settled items outside lock
            for item in stable_items:
                cls._process_settled_file(item)

        logger.info("FolderWatcher event processor loop stopped.")

    @classmethod
    def _process_settled_file(cls, item: Dict[str, Any]):
        """Creates/updates a staging item for a settled audio file."""
        path_obj = Path(item["path"])
        event_type = item["event_type"]
        folder_id = item["folder_id"]

        try:
            # 1. Update metrics
            if event_type == "CREATED":
                cls._metrics["created"] += 1
            elif event_type == "MODIFIED":
                cls._metrics["modified"] += 1
            elif event_type == "MOVED":
                cls._metrics["moved"] += 1
            elif event_type == "DELETED":
                cls._metrics["deleted"] += 1

            # 2. Stage candidate safely via StagingService
            batch_id = f"batch_watch_{time.strftime('%Y%m%d')}"
            staged = StagingService.stage_local_file(
                file_path=path_obj,
                event_type=event_type,
                folder_id=folder_id,
                batch_id=batch_id
            )

            # 3. Record in watcher_events table
            conn = get_db_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO watcher_events (id, folder_id, file_path, event_type, detected_at, status, staging_item_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"wevt_{uuid.uuid4().hex[:10]}",
                        folder_id,
                        str(path_obj),
                        event_type,
                        time.time(),
                        "STAGED" if staged else "IGNORED",
                        staged["id"] if staged else None
                    )
                )

        except Exception as e:
            cls._record_error(f"Error processing settled file {path_obj}: {e}")
            logger.error(f"Error processing settled file {path_obj}: {e}", exc_info=True)

    # ================= INITIAL SCAN & MANUAL RESCAN =================

    @classmethod
    def _trigger_async_folder_scan(cls, folder_id: str, dir_path: Path, recursive: bool):
        """Launches a bounded background thread for directory scanning."""
        t = threading.Thread(
            target=cls.rescan_folder,
            args=(folder_id, dir_path, recursive),
            name=f"FolderScanThread-{folder_id}",
            daemon=True
        )
        t.start()

    @classmethod
    def rescan_folder(cls, folder_id_or_path: str, dir_path: Optional[Path] = None, recursive: Optional[bool] = None) -> Dict[str, Any]:
        """
        Controlled, idempotent initial scan / manual rescan of a monitored folder.
        Extracts files, compares against existing library, and stages new/modified items.
        """
        norm_path = None
        try:
            p_cand = Path(folder_id_or_path)
            if p_cand.exists():
                norm_path = str(p_cand.resolve())
        except Exception:
            logger.debug("Suppressed exception", exc_info=True)

        conn = get_db_connection()
        cur = conn.execute(
            """
            SELECT id, path, recursive FROM watched_folders
            WHERE id = ?
               OR path = ? COLLATE NOCASE
               OR (path = ? COLLATE NOCASE AND ? IS NOT NULL)
            """,
            (folder_id_or_path, folder_id_or_path, norm_path, norm_path)
        )
        row = cur.fetchone()
        if not row:
            return {"error": "Watched folder not found."}

        fid = row["id"]
        target_path = dir_path or Path(row["path"]).resolve()
        is_recursive = recursive if recursive is not None else bool(row["recursive"])

        # Prevent concurrent scans of the same folder, or wait briefly if an initial scan is finishing
        wait_start = time.time()
        while True:
            with cls._lock:
                if fid not in cls._scanning_folders:
                    cls._scanning_folders.add(fid)
                    break
            if time.time() - wait_start > 5.0:
                return {"status": "SKIPPED", "message": "Scan already in progress for this folder."}
            time.sleep(0.05)

        logger.info(f"Starting rescan on folder [{fid}]: {target_path} (recursive={is_recursive})")
        staged_count = 0
        unchanged_count = 0
        error_count = 0
        batch_id = f"batch_scan_{fid[:8]}_{int(time.time())}"

        try:
            if not target_path.exists() or not target_path.is_dir():
                raise FileNotFoundError(f"Directory missing: {target_path}")

            walker = target_path.rglob("*") if is_recursive else target_path.glob("*")
            for item in walker:
                if not item.is_file():
                    continue

                if not cls._is_supported_audio_file(str(item)):
                    continue

                # Security containment: Ensure symlinks do not escape target_path
                try:
                    resolved_item = item.resolve()
                    if not SecurityManager.validate_safe_path(resolved_item, target_path):
                        logger.warning(f"Skipping symlink escaping watched folder: {item} -> {resolved_item}")
                        continue
                except Exception:
                    continue

                try:
                    res = StagingService.stage_local_file(
                        file_path=item,
                        event_type="CREATED",
                        folder_id=fid,
                        batch_id=batch_id
                    )
                    if res:
                        if res.get("status") == "READY":
                            staged_count += 1
                        else:
                            unchanged_count += 1
                    else:
                        unchanged_count += 1
                except Exception as ex:
                    error_count += 1
                    logger.debug(f"Rescan file error for {item}: {ex}")

            now = time.time()
            with conn:
                conn.execute(
                    "UPDATE watched_folders SET last_scanned_at = ?, status = 'ACTIVE', error_message = NULL WHERE id = ?",
                    (now, fid)
                )

            cls._metrics["last_scan_at"] = now
            logger.info(f"Rescan complete for [{fid}]: {staged_count} staged, {unchanged_count} unchanged, {error_count} errors.")

            return {
                "folder_id": fid,
                "batch_id": batch_id,
                "staged_count": staged_count,
                "unchanged_count": unchanged_count,
                "error_count": error_count,
                "status": "COMPLETED"
            }

        except Exception as e:
            err_msg = str(e)
            cls._record_error(f"Rescan failed on {target_path}: {err_msg}")
            with conn:
                conn.execute(
                    "UPDATE watched_folders SET status = 'ERROR', error_message = ? WHERE id = ?",
                    (err_msg, fid)
                )
            return {"error": err_msg, "status": "FAILED"}

        finally:
            with cls._lock:
                cls._scanning_folders.discard(fid)

    @classmethod
    def _record_error(cls, msg: str):
        """Records an internal error into memory buffer and audit log."""
        cls._last_error = msg
        cls._recent_errors.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(cls._recent_errors) > 50:
            cls._recent_errors.pop(0)
        record_audit("WATCHER", f"Watcher Error: {msg}")


# Register clean process exit hook
atexit.register(FolderWatcherService.stop)
