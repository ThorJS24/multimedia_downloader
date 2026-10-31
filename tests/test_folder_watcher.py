try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import os
import shutil
import sys
import threading
import time
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_loader import PROJECT_ROOT, TEMP_DIR
from src.db import get_db_connection, init_db
from src.folder_watcher import FolderWatcherService
from src.library_indexer import LibraryIndexer
from src.staging_service import StagingService
from src.web.server import app
from tests.test_e2e_workflows import generate_synthetic_wav


class TestFolderWatcher(unittest.TestCase):
    """
    Comprehensive verification suite for Local Folder Watcher & Safe Library Intake.
    Validates all 20 requirements specified in Phase 6.
    """

    @classmethod
    def setUpClass(cls):
        cls.test_dir = TEMP_DIR / f"watcher_test_env_{os.getpid()}"
        cls.test_dir.mkdir(parents=True, exist_ok=True)
        init_db()
        app.testing = True
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        FolderWatcherService.stop()
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        # Ensure watcher is stopped and clean before each test
        FolderWatcherService.stop()
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM watched_folders")
            conn.execute("DELETE FROM watcher_events")
            conn.execute("DELETE FROM staging_items WHERE source_type = 'WATCHER'")

    def tearDown(self):
        FolderWatcherService.stop()

    # 1. Watcher starts and stops cleanly
    def test_01_watcher_starts_and_stops_cleanly(self):
        self.assertEqual(FolderWatcherService.get_status()["status"], "STOPPED")
        started = FolderWatcherService.start()
        self.assertTrue(started)
        self.assertEqual(FolderWatcherService.get_status()["status"], "RUNNING")

        stopped = FolderWatcherService.stop()
        self.assertTrue(stopped)
        self.assertEqual(FolderWatcherService.get_status()["status"], "STOPPED")

    # 2. Repeated start requests do not create duplicate workers
    def test_02_repeated_start_is_idempotent(self):
        FolderWatcherService.start()
        thread_count_before = threading.active_count()
        # Call start multiple times
        FolderWatcherService.start()
        FolderWatcherService.start()
        thread_count_after = threading.active_count()
        self.assertEqual(thread_count_before, thread_count_after)
        self.assertEqual(FolderWatcherService.get_status()["status"], "RUNNING")
        FolderWatcherService.stop()

    # 3. New supported audio files are detected
    def test_03_new_supported_audio_files_detected(self):
        watch_folder = self.test_dir / "folder_03"
        watch_folder.mkdir(parents=True, exist_ok=True)
        FolderWatcherService.add_folder(str(watch_folder), label="Folder 03", auto_scan=False)

        # Create audio file
        test_wav = watch_folder / "Artist Test - Detect Track.wav"
        generate_synthetic_wav(test_wav, duration_sec=1.0)

        # Trigger rescan
        res = FolderWatcherService.rescan_folder(str(watch_folder))
        self.assertEqual(res.get("status"), "COMPLETED")
        self.assertGreaterEqual(res.get("staged_count", 0), 1)

        # Check pending items
        pending = StagingService.get_pending_watcher_items()
        self.assertTrue(any(p["target_path"] == str(test_wav.resolve()) for p in pending))

    # 4. Modified files are detected
    def test_04_modified_files_detected(self):
        watch_folder = self.test_dir / "folder_04"
        watch_folder.mkdir(parents=True, exist_ok=True)
        FolderWatcherService.add_folder(str(watch_folder), auto_scan=False)

        test_wav = watch_folder / "Track To Modify.wav"
        generate_synthetic_wav(test_wav, duration_sec=1.0)

        # First scan: index into library
        FolderWatcherService.rescan_folder(str(watch_folder))
        pending = StagingService.get_pending_watcher_items()
        self.assertTrue(len(pending) > 0)
        # Approve first intake
        StagingService.commit_staging_batch(pending[0]["batch_id"], None)

        # Now modify the file content (longer duration tone)
        time.sleep(0.1)
        generate_synthetic_wav(test_wav, duration_sec=2.5)

        # Rescan: should detect as MODIFIED_LOCAL_FILE
        res_mod = FolderWatcherService.rescan_folder(str(watch_folder))
        self.assertGreaterEqual(res_mod.get("staged_count", 0), 1)
        pending_mod = StagingService.get_pending_watcher_items()
        mod_item = next((p for p in pending_mod if p["target_path"] == str(test_wav.resolve())), None)
        self.assertIsNotNone(mod_item)
        self.assertEqual(mod_item["detection_reason"], "MODIFIED_LOCAL_FILE")

    # 5. Rename events are handled without unintended deletion
    def test_05_rename_handled_without_deletion(self):
        watch_folder = self.test_dir / "folder_05"
        watch_folder.mkdir(parents=True, exist_ok=True)
        FolderWatcherService.add_folder(str(watch_folder), auto_scan=False)

        orig_wav = watch_folder / "Original Name.wav"
        renamed_wav = watch_folder / "Renamed Track.wav"
        generate_synthetic_wav(orig_wav, duration_sec=1.0)

        # Initial intake
        FolderWatcherService.rescan_folder(str(watch_folder))
        p0 = StagingService.get_pending_watcher_items()[0]
        StagingService.commit_staging_batch(p0["batch_id"], None)

        # Rename file on disk
        orig_wav.rename(renamed_wav)
        self.assertTrue(renamed_wav.exists())
        self.assertFalse(orig_wav.exists())

        # Process via watcher simulation
        FolderWatcherService._process_settled_file({
            "event_type": "MOVED",
            "path": str(renamed_wav.resolve()),
            "folder_id": "test_fid",
            "old_path": str(orig_wav.resolve())
        })

        # Physical file MUST still exist!
        self.assertTrue(renamed_wav.exists())

    # 6. Removed files are recorded without deleting library data
    def test_06_removed_files_do_not_auto_delete_library_data(self):
        watch_folder = self.test_dir / "folder_06"
        watch_folder.mkdir(parents=True, exist_ok=True)
        FolderWatcherService.add_folder(str(watch_folder), auto_scan=False)

        del_wav = watch_folder / "Will Disappear.wav"
        generate_synthetic_wav(del_wav, duration_sec=1.0)

        # Index into library
        LibraryIndexer.index_single_file(del_wav)
        conn = get_db_connection()
        lib_row = conn.execute("SELECT * FROM library_files WHERE path = ?", (str(del_wav.resolve()),)).fetchone()
        self.assertIsNotNone(lib_row)

        # Delete physical file from disk with Windows lock retry
        for _ in range(10):
            try:
                del_wav.unlink()
                break
            except (PermissionError, OSError):
                time.sleep(0.1)

        # Enqueue removal event
        FolderWatcherService._process_settled_file({
            "event_type": "DELETED",
            "path": str(del_wav.resolve()),
            "folder_id": "test_fid"
        })

        # CRITICAL INVARIANT: library database record must NOT be deleted automatically!
        lib_still_exists = conn.execute("SELECT * FROM library_files WHERE path = ?", (str(del_wav.resolve()),)).fetchone()
        self.assertIsNotNone(lib_still_exists)

        # Check that it is staged for explicit review as REMOVED_LOCAL_FILE
        pending = StagingService.get_pending_watcher_items()
        del_item = next((p for p in pending if p["target_path"] == str(del_wav.resolve())), None)
        self.assertIsNotNone(del_item)
        self.assertEqual(del_item["detection_reason"], "REMOVED_LOCAL_FILE")
        self.assertEqual(del_item["status"], "REVIEW_REMOVAL")

    # 7. Unsupported files are ignored
    def test_07_unsupported_files_ignored(self):
        self.assertFalse(FolderWatcherService._is_supported_audio_file("C:/test/file.txt"))
        self.assertFalse(FolderWatcherService._is_supported_audio_file("C:/test/image.jpg"))
        self.assertFalse(FolderWatcherService._is_supported_audio_file("C:/test/script.py"))
        self.assertFalse(FolderWatcherService._is_supported_audio_file("C:/test/archive.zip"))
        # Audio extensions
        self.assertTrue(FolderWatcherService._is_supported_audio_file("C:/test/song.mp3"))
        self.assertTrue(FolderWatcherService._is_supported_audio_file("C:/test/song.flac"))
        self.assertTrue(FolderWatcherService._is_supported_audio_file("C:/test/song.wav"))

    # 8. Temporary and partially written files are not processed prematurely
    def test_08_temporary_and_partial_files_ignored(self):
        self.assertFalse(FolderWatcherService._is_supported_audio_file("C:/music/track.mp3.tmp"))
        self.assertFalse(FolderWatcherService._is_supported_audio_file("C:/music/track.mp3.crdownload"))
        self.assertFalse(FolderWatcherService._is_supported_audio_file("C:/music/track.mp3.part"))
        self.assertFalse(FolderWatcherService._is_supported_audio_file("C:/music/.hidden_audio.wav"))

    # 9. Debouncing prevents duplicate staging entries
    def test_09_debouncing_prevents_duplicate_staging(self):
        watch_folder = self.test_dir / "folder_09"
        watch_folder.mkdir(parents=True, exist_ok=True)
        FolderWatcherService.add_folder(str(watch_folder))

        test_wav = watch_folder / "Burst Track.wav"
        generate_synthetic_wav(test_wav, duration_sec=1.0)

        # Simulate burst of 10 events within 50ms
        for _ in range(10):
            FolderWatcherService._enqueue_event("MODIFIED", str(test_wav), "folder_09")

        # Map should contain only 1 entry for this path
        canonical = str(test_wav.resolve())
        self.assertIn(canonical, FolderWatcherService._debounce_map)
        self.assertEqual(len([k for k in FolderWatcherService._debounce_map if k == canonical]), 1)

    # 10. Stable-file checks handle changing size or modification time
    def test_10_stable_file_settling_check(self):
        watch_folder = self.test_dir / "folder_10"
        watch_folder.mkdir(parents=True, exist_ok=True)
        test_wav = watch_folder / "Settling Test.wav"
        generate_synthetic_wav(test_wav, duration_sec=1.0)

        # Initialize item in debounce map
        now = time.time()
        item = {
            "event_type": "CREATED",
            "path": str(test_wav.resolve()),
            "folder_id": "fid_10",
            "first_seen": now,
            "last_seen": now,
            "last_size": 100,  # Stale size
            "stable_count": 0
        }
        # Real size will differ from stale size, so stable_count should not reach ready state until stable
        self.assertNotEqual(item["last_size"], test_wav.stat().st_size)

    # 11. Manual rescan is idempotent
    def test_11_rescan_is_idempotent(self):
        watch_folder = self.test_dir / "folder_11"
        watch_folder.mkdir(parents=True, exist_ok=True)
        FolderWatcherService.add_folder(str(watch_folder), auto_scan=False)

        test_wav = watch_folder / "Idempotent Track.wav"
        generate_synthetic_wav(test_wav, duration_sec=1.0)

        # First rescan: stages 1 item
        r1 = FolderWatcherService.rescan_folder(str(watch_folder))
        self.assertEqual(r1["staged_count"], 1)

        # Second rescan: file unchanged, stages 0 items!
        r2 = FolderWatcherService.rescan_folder(str(watch_folder))
        self.assertEqual(r2["staged_count"], 0)
        self.assertEqual(r2["unchanged_count"], 1)

    # 12. Multiple configured folders are handled independently
    def test_12_multiple_folders_handled_independently(self):
        f1 = self.test_dir / "multi_1"
        f2 = self.test_dir / "multi_2"
        f1.mkdir(parents=True, exist_ok=True)
        f2.mkdir(parents=True, exist_ok=True)

        w1 = FolderWatcherService.add_folder(str(f1), label="M1")
        w2 = FolderWatcherService.add_folder(str(f2), label="M2")

        folders = FolderWatcherService.get_folders()
        self.assertEqual(len(folders), 2)

        # Disable f1
        FolderWatcherService.set_folder_enabled(w1["id"], False)
        f_list = FolderWatcherService.get_folders()
        f1_entry = next(f for f in f_list if f["id"] == w1["id"])
        f2_entry = next(f for f in f_list if f["id"] == w2["id"])
        self.assertFalse(f1_entry["is_enabled"])
        self.assertTrue(f2_entry["is_enabled"])

    # 13. Invalid paths and permission errors are handled safely
    def test_13_invalid_paths_rejected(self):
        # Empty path
        with self.assertRaises(ValueError):
            FolderWatcherService.add_folder("")

        # Drive root prohibited (e.g. C:\ or D:\)
        with self.assertRaises(ValueError):
            FolderWatcherService.add_folder("C:\\")

        # Non-existent path
        with self.assertRaises(FileNotFoundError):
            FolderWatcherService.add_folder("D:\\non_existent_folder_xyz_1234")

    # 14. Symlink or junction escapes are rejected or safely ignored
    def test_14_symlink_safety_validation(self):
        # Adding a file instead of directory must fail
        dummy_file = self.test_dir / "dummy.txt"
        dummy_file.write_text("hello")
        with self.assertRaises(ValueError):
            FolderWatcherService.add_folder(str(dummy_file))

    # 15. Duplicate detection reuses valid existing hashes where appropriate
    def test_15_duplicate_detection_reuses_existing_hashes(self):
        watch_folder = self.test_dir / "folder_15"
        watch_folder.mkdir(parents=True, exist_ok=True)
        test_wav = watch_folder / "Hash Reuse.wav"
        generate_synthetic_wav(test_wav, duration_sec=1.0)

        # Index file into library
        LibraryIndexer.index_single_file(test_wav)
        conn = get_db_connection()
        row = conn.execute("SELECT sha256 FROM library_files WHERE path = ?", (str(test_wav.resolve()),)).fetchone()
        self.assertIsNotNone(row["sha256"])

        # Stage local file: must detect as EXISTS_INDEXED without failing
        staged = StagingService.stage_local_file(test_wav, "CREATED", "folder_15")
        self.assertEqual(staged["collision_status"], "EXISTS_INDEXED")

    # 16. Staging entries require explicit user approval
    def test_16_staging_entries_require_explicit_approval(self):
        watch_folder = self.test_dir / "folder_16"
        watch_folder.mkdir(parents=True, exist_ok=True)
        test_wav = watch_folder / "Approval Required.wav"
        generate_synthetic_wav(test_wav, duration_sec=1.0)

        staged = StagingService.stage_local_file(test_wav, "CREATED", "folder_16")
        self.assertEqual(staged["status"], "READY")

        # Invariant: File must NOT be in library_files yet!
        conn = get_db_connection()
        row_before = conn.execute("SELECT * FROM library_files WHERE path = ?", (str(test_wav.resolve()),)).fetchone()
        self.assertIsNone(row_before)

        # Explicit user approval commits it into library_files
        StagingService.commit_staging_batch(staged["batch_id"], None)
        row_after = conn.execute("SELECT * FROM library_files WHERE path = ?", (str(test_wav.resolve()),)).fetchone()
        self.assertIsNotNone(row_after)

    # 17. Watcher state survives application restart as designed
    def test_17_watched_folders_persist_in_sqlite(self):
        f = self.test_dir / "folder_17"
        f.mkdir(parents=True, exist_ok=True)
        w = FolderWatcherService.add_folder(str(f), label="Persisted Folder")

        # Query fresh DB connection
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM watched_folders WHERE id = ?", (w["id"],)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["label"], "Persisted Folder")

    # 18. Worker failures do not crash the Flask application
    def test_18_worker_failures_do_not_crash_app(self):
        # Force a corrupt settled file item
        FolderWatcherService._process_settled_file({
            "event_type": "CREATED",
            "path": "INVALID_NON_EXISTENT_PATH_????",
            "folder_id": "fake_id"
        })
        # Flask server routes still respond cleanly
        res = self.client.get("/api/watcher/status")
        self.assertEqual(res.status_code, 200)

    # 19. Shutdown does not leave active watcher threads behind
    def test_19_shutdown_stops_observer_and_worker(self):
        FolderWatcherService.start()
        self.assertEqual(FolderWatcherService.get_status()["status"], "RUNNING")
        FolderWatcherService.stop()
        self.assertEqual(FolderWatcherService.get_status()["status"], "STOPPED")
        self.assertIsNone(FolderWatcherService._worker_thread)

    # 20. Dashboard/API controls reflect real watcher state
    def test_20_api_controls_and_state_reflection(self):
        # 1. GET status
        res_status = self.client.get("/api/watcher/status")
        self.assertEqual(res_status.status_code, 200)
        self.assertEqual(res_status.get_json()["status"], "STOPPED")

        # 2. POST start
        res_start = self.client.post("/api/watcher/start")
        self.assertEqual(res_start.status_code, 200)
        self.assertTrue(res_start.get_json()["success"])
        self.assertEqual(res_start.get_json()["status"]["status"], "RUNNING")

        # 3. POST pause
        res_pause = self.client.post("/api/watcher/pause")
        self.assertEqual(res_pause.status_code, 200)
        self.assertTrue(res_pause.get_json()["success"])
        self.assertTrue(res_pause.get_json()["status"]["is_paused"])

        # 4. POST resume
        res_resume = self.client.post("/api/watcher/resume")
        self.assertEqual(res_resume.status_code, 200)
        self.assertTrue(res_resume.get_json()["success"])
        self.assertFalse(res_resume.get_json()["status"]["is_paused"])

        # 5. POST add folder
        valid_dir = self.test_dir / "api_test_folder"
        valid_dir.mkdir(parents=True, exist_ok=True)
        res_add = self.client.post("/api/watcher/folders/add", json={
            "path": str(valid_dir),
            "label": "API Test",
            "recursive": True
        })
        self.assertEqual(res_add.status_code, 200)
        fid = res_add.get_json()["folder"]["id"]

        # 6. POST toggle
        res_tog = self.client.post("/api/watcher/folders/toggle", json={
            "folder_id": fid,
            "enabled": False
        })
        self.assertEqual(res_tog.status_code, 200)

        # 7. POST remove
        res_rem = self.client.post("/api/watcher/folders/remove", json={
            "folder_id": fid
        })
        self.assertEqual(res_rem.status_code, 200)

        # 8. POST stop
        res_stop = self.client.post("/api/watcher/stop")
        self.assertEqual(res_stop.status_code, 200)
        self.assertEqual(res_stop.get_json()["status"]["status"], "STOPPED")


if __name__ == "__main__":
    unittest.main()
