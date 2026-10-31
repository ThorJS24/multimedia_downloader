try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import os
import shutil
import sys
import threading
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_loader import PROJECT_ROOT, TEMP_DIR
from src.db import get_db_connection, init_db, DB_PATH
from src.folder_watcher import FolderWatcherService
from src.library_indexer import LibraryIndexer
from src.staging_service import StagingService
from src.backup_service import BackupService
from src.web.server import app
from tests.test_e2e_workflows import generate_synthetic_wav


class TestAdversarialPhase7(unittest.TestCase):
    """
    Phase 7 Adversarial, Recovery, Integrity & Security Verification Suite.
    Exhaustively tests failure modes, edge cases, boundaries, and recovery behaviors.
    """

    @classmethod
    def setUpClass(cls):
        cls.test_dir = TEMP_DIR / f"phase7_adv_test_{os.getpid()}"
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
        FolderWatcherService.stop()
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM watched_folders")
            conn.execute("DELETE FROM watcher_events")
            conn.execute("DELETE FROM staging_items")
            conn.execute("DELETE FROM library_files WHERE path LIKE '%phase7_adv%'")

    def tearDown(self):
        FolderWatcherService.stop()

    # ================= 1. FILESYSTEM & PATH SECURITY =================

    def test_sec_01_drive_root_and_unc_rejection(self):
        r"""Rejects drive roots (C:\, D:\) and UNC paths."""
        for root in ["C:\\", "D:\\", "C:/", "D:/", "\\\\localhost\\c$", "\\\\127.0.0.1\\share"]:
            with self.assertRaises(ValueError):
                FolderWatcherService.add_folder(root, auto_scan=False)

    def test_sec_02_system_directory_rejection(self):
        r"""Rejects Windows system directories like C:\Windows or C:\Program Files."""
        for sys_dir in [r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)"]:
            if Path(sys_dir).exists():
                with self.assertRaises(ValueError):
                    FolderWatcherService.add_folder(sys_dir, auto_scan=False)

    def test_sec_03_symlink_escaping_watched_folder_is_blocked(self):
        """Ensures symlinks inside watched directory pointing outside are NOT traversed or staged."""
        wf_dir = self.test_dir / "wf_symlink_test"
        outside_dir = self.test_dir / "outside_secret_dir"
        wf_dir.mkdir(parents=True, exist_ok=True)
        outside_dir.mkdir(parents=True, exist_ok=True)

        # Create audio file outside monitored root
        secret_audio = outside_dir / "confidential_meeting.wav"
        generate_synthetic_wav(secret_audio, duration_sec=1.0)

        # Create symlink or Windows directory junction inside monitored folder pointing outside
        symlink_created = False
        symlink_target = wf_dir / "symlink_to_secret.wav"
        try:
            os.symlink(str(secret_audio), str(symlink_target))
            symlink_created = True
        except (OSError, NotImplementedError):
            pass

        if not symlink_created:
            try:
                import _winapi
                junction_link = wf_dir / "junction_escape"
                _winapi.CreateJunction(str(outside_dir), str(junction_link))
                symlink_created = True
            except Exception as e:
                self.skipTest(f"Reparse point creation not permitted: {e}")

        wf = FolderWatcherService.add_folder(str(wf_dir), auto_scan=False)
        res = FolderWatcherService.rescan_folder(wf["id"])
        self.assertEqual(res.get("status"), "COMPLETED")
        self.assertEqual(res.get("staged_count"), 0)

        # Verify no items staged
        pending = StagingService.get_pending_watcher_items()
        self.assertEqual(len(pending), 0)

    def test_sec_04_special_characters_in_filenames(self):
        """Handles spaces, unicode, apostrophes, and punctuation in filenames without crashes."""
        wf_dir = self.test_dir / "wf_special_chars"
        wf_dir.mkdir(parents=True, exist_ok=True)

        complex_names = [
            "Artist with Spaces - Track 1.wav",
            "O'Connor - Nothing's Real.wav",
            "宇多田ヒカル - First Love (Remix).wav",
            "Artist & Friends, Vol. 1 [2024]! (feat. DJ).wav",
        ]
        for name in complex_names:
            p = wf_dir / name
            generate_synthetic_wav(p, duration_sec=1.0)

        wf = FolderWatcherService.add_folder(str(wf_dir), auto_scan=False)
        res = FolderWatcherService.rescan_folder(wf["id"])
        self.assertEqual(res.get("status"), "COMPLETED")
        self.assertEqual(res.get("staged_count"), len(complex_names))

        pending = StagingService.get_pending_watcher_items()
        self.assertEqual(len(pending), len(complex_names))

    def test_sec_05_api_arbitrary_path_traversal_guards(self):
        """Ensures all filesystem endpoints reject path traversal (..), drive roots, or escapes."""
        traversal_attempts = [
            "../../../../Windows/System32/cmd.exe",
            "C:\\Windows\\System32\\calc.exe",
            "D:\\mini-projects\\Spotify_playlist_downloader\\..\\..\\secret.txt",
        ]
        # Test /api/tags/edit
        for bad_path in traversal_attempts:
            res = self.client.post("/api/tags/edit", json={"path": bad_path, "metadata": {"title": "hack"}})
            self.assertIn(res.status_code, [400, 403, 404])

        # Test /api/stream
        for bad_path in traversal_attempts:
            res = self.client.get(f"/api/stream?path={bad_path}")
            self.assertIn(res.status_code, [400, 403, 404])

        # Test /api/duplicates/move-backup
        for bad_path in traversal_attempts:
            res = self.client.post("/api/duplicates/move-backup", json={"path": bad_path})
            self.assertIn(res.status_code, [400, 403, 404])

    # ================= 2. WATCHER LIFECYCLE & RECOVERY =================

    def test_life_01_repeated_lifecycle_transitions(self):
        """Verifies start -> pause -> resume -> stop -> restart with no leaked threads or errors."""
        self.assertEqual(FolderWatcherService.get_status()["status"], "STOPPED")

        self.assertTrue(FolderWatcherService.start())
        self.assertEqual(FolderWatcherService.get_status()["status"], "RUNNING")

        # Repeated start is idempotent
        self.assertTrue(FolderWatcherService.start())
        self.assertEqual(FolderWatcherService.get_status()["status"], "RUNNING")

        # Pause and resume
        self.assertTrue(FolderWatcherService.pause())
        self.assertEqual(FolderWatcherService.get_status()["status"], "PAUSED")
        self.assertTrue(FolderWatcherService.resume())
        self.assertEqual(FolderWatcherService.get_status()["status"], "RUNNING")

        # Stop and restart
        self.assertTrue(FolderWatcherService.stop())
        self.assertEqual(FolderWatcherService.get_status()["status"], "STOPPED")
        self.assertTrue(FolderWatcherService.stop())  # Idempotent stop

        self.assertTrue(FolderWatcherService.start())
        self.assertEqual(FolderWatcherService.get_status()["status"], "RUNNING")
        FolderWatcherService.stop()

    def test_life_02_restart_preserves_pending_items_without_duplicates(self):
        """Restarting watcher or rescanning does NOT duplicate existing pending staging items."""
        wf_dir = self.test_dir / "wf_restart_dedup"
        wf_dir.mkdir(parents=True, exist_ok=True)
        test_wav = wf_dir / "Deduplication Track.wav"
        generate_synthetic_wav(test_wav, duration_sec=1.5)

        wf = FolderWatcherService.add_folder(str(wf_dir), auto_scan=False)
        r1 = FolderWatcherService.rescan_folder(wf["id"])
        self.assertEqual(r1.get("staged_count"), 1)

        pending_1 = StagingService.get_pending_watcher_items()
        self.assertEqual(len(pending_1), 1)

        # Simulate service restart and second rescan
        FolderWatcherService.stop()
        FolderWatcherService.start()
        r2 = FolderWatcherService.rescan_folder(wf["id"])
        self.assertEqual(r2.get("status"), "COMPLETED")

        pending_2 = StagingService.get_pending_watcher_items()
        # Must still be exactly 1 item, zero duplicates created!
        self.assertEqual(len(pending_2), 1)
        self.assertEqual(pending_1[0]["id"], pending_2[0]["id"])
        FolderWatcherService.stop()

    def test_life_03_burst_events_coalescing(self):
        """Simulates 30 rapid modification events on the same file; verifies they coalesce."""
        wf_dir = self.test_dir / "wf_burst"
        wf_dir.mkdir(parents=True, exist_ok=True)
        test_wav = wf_dir / "Rapid Write.wav"
        generate_synthetic_wav(test_wav, duration_sec=1.0)

        wf = FolderWatcherService.add_folder(str(wf_dir), auto_scan=False)
        # Directly enqueue 30 events into debounce queue
        for _ in range(30):
            FolderWatcherService._enqueue_event("MODIFIED", str(test_wav), wf["id"])

        # Check debounce map size
        canonical = str(test_wav.resolve())
        with FolderWatcherService._lock:
            self.assertIn(canonical, FolderWatcherService._debounce_map)
            self.assertEqual(len(FolderWatcherService._debounce_map), 1)

    # ================= 3. DATABASE & TRANSACTION INTEGRITY =================

    def test_db_01_safety_invariant_failed_commit_is_not_marked_committed(self):
        """Safety Invariant: If file is missing when approved, status becomes FAILED, never COMMITTED."""
        wf_dir = self.test_dir / "wf_toctou_missing"
        wf_dir.mkdir(parents=True, exist_ok=True)
        test_wav = wf_dir / "Ghost Track.wav"
        generate_synthetic_wav(test_wav, duration_sec=1.0)

        wf = FolderWatcherService.add_folder(str(wf_dir), auto_scan=False)
        FolderWatcherService.rescan_folder(wf["id"])

        pending = StagingService.get_pending_watcher_items()
        self.assertEqual(len(pending), 1)
        staged_id = pending[0]["id"]

        # TOCTOU: Delete file from disk before user approves
        test_wav.unlink()

        # Attempt to approve missing file via API
        res = self.client.post("/api/watcher/items/approve", json={"item_id": staged_id})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("approved_count"), 0)
        self.assertEqual(data.get("failed_count"), 1)

        # Invariant check: SQLite must record status='FAILED', never 'COMMITTED'
        conn = get_db_connection()
        row = conn.execute("SELECT status FROM staging_items WHERE id = ?", (staged_id,)).fetchone()
        self.assertEqual(row["status"], "FAILED")

    def test_db_02_database_integrity_and_backup(self):
        """Verifies PRAGMA integrity_check and non-blocking backup creation."""
        ok, msg = BackupService.verify_database_integrity()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")

        backup_file = BackupService.create_database_backup(reason="phase7_test")
        self.assertTrue(backup_file.exists())
        self.assertTrue(backup_file.stat().st_size > 0)
        backup_file.unlink()

    def test_db_03_repeated_init_db_migrations_are_idempotent(self):
        """Running database initialization/migrations multiple times is completely idempotent."""
        for _ in range(3):
            init_db()
        ok, _ = BackupService.verify_database_integrity()
        self.assertTrue(ok)

    # ================= 4. MALFORMED AND UNTRUSTED MEDIA =================

    def test_media_01_empty_zero_byte_file(self):
        """0-byte audio file does not crash the indexer and is flagged as UNREADABLE or CORRUPT."""
        p = self.test_dir / "zero_bytes.mp3"
        p.write_bytes(b"")
        meta, tech, tag_status, issues = LibraryIndexer.inspect_file_metadata(p)
        self.assertIn(tag_status, ["UNREADABLE", "CORRUPT"])
        self.assertTrue(len(issues) > 0)

    def test_media_02_corrupted_header_garbage_bytes(self):
        """Garbage/corrupted byte headers do not crash the indexer and are flagged as CORRUPT/UNREADABLE."""
        p = self.test_dir / "garbage_header.flac"
        p.write_bytes(b"\x00\xFF\xAA\x55RandomGarbageDataThatIsNotAValidAudioHeader" * 10)
        meta, tech, tag_status, issues = LibraryIndexer.inspect_file_metadata(p)
        self.assertIn(tag_status, ["UNREADABLE", "CORRUPT"])
        self.assertTrue(len(issues) > 0)

    def test_media_03_xss_injection_in_metadata_tags(self):
        """Ensures HTML/JavaScript tags in title/artist are safely captured as text strings."""
        p = self.test_dir / "xss_track.wav"
        generate_synthetic_wav(p, duration_sec=1.0)

        # Stage directly with XSS strings
        conn = get_db_connection()
        xss_title = "<script>alert('XSS')</script>"
        xss_artist = "<img src=x onerror=alert(1)>"
        with conn:
            conn.execute(
                """
                INSERT INTO staging_items (id, batch_id, track_id, title, artists, album, duration_ms, target_path, status, source_type)
                VALUES ('stg_xss_01', 'b_xss', 'trk_xss', ?, ?, 'Album', 1000, ?, 'READY', 'WATCHER')
                """,
                (xss_title, xss_artist, str(p))
            )

        # Check API response
        res = self.client.get("/api/watcher/pending")
        self.assertEqual(res.status_code, 200)
        items = res.get_json().get("items", [])
        xss_item = next((i for i in items if i["id"] == "stg_xss_01"), None)
        self.assertIsNotNone(xss_item)
        # Content remains raw string in JSON; frontend app.js escapes via escapeHtml
        self.assertEqual(xss_item["title"], xss_title)
        self.assertEqual(xss_item["artists"], xss_artist)

    def test_media_04_batch_scan_resilience_to_corrupt_files(self):
        """A batch containing corrupted files continues scanning and stages the valid files."""
        wf_dir = self.test_dir / "wf_mixed_batch"
        wf_dir.mkdir(parents=True, exist_ok=True)

        good_1 = wf_dir / "Good Song 1.wav"
        generate_synthetic_wav(good_1, duration_sec=1.0)

        bad_corrupt = wf_dir / "Corrupt Audio.mp3"
        bad_corrupt.write_bytes(b"\x00\x01\x02\x03CorruptHeader")

        good_2 = wf_dir / "Good Song 2.wav"
        generate_synthetic_wav(good_2, duration_sec=1.0)

        wf = FolderWatcherService.add_folder(str(wf_dir), auto_scan=False)
        res = FolderWatcherService.rescan_folder(wf["id"])
        self.assertEqual(res.get("status"), "COMPLETED")
        # Staged count must include the good files
        self.assertGreaterEqual(res.get("staged_count"), 2)

    # ================= 5. STAGING, APPROVAL & NON-DESTRUCTIVE INVARIANTS =================

    def test_staging_01_full_lifecycle_detect_approve_index(self):
        """Full lifecycle: detect -> stage -> review -> approve -> indexed in library."""
        wf_dir = self.test_dir / "wf_full_lifecycle"
        wf_dir.mkdir(parents=True, exist_ok=True)
        track = wf_dir / "Lifecycle Track.wav"
        generate_synthetic_wav(track, duration_sec=1.2)

        wf = FolderWatcherService.add_folder(str(wf_dir), auto_scan=False)
        FolderWatcherService.rescan_folder(wf["id"])

        pending = StagingService.get_pending_watcher_items()
        self.assertEqual(len(pending), 1)
        item_id = pending[0]["id"]

        # Approve item
        res = self.client.post("/api/watcher/items/approve", json={"item_id": item_id})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json().get("approved_count"), 1)

        # Verify indexed into library_files
        conn = get_db_connection()
        lib_row = conn.execute("SELECT * FROM library_files WHERE path = ?", (str(track.resolve()),)).fetchone()
        self.assertIsNotNone(lib_row)
        self.assertEqual(lib_row["filename"], track.name)

        # Staging item status must be COMMITTED
        stg_row = conn.execute("SELECT status FROM staging_items WHERE id = ?", (item_id,)).fetchone()
        self.assertEqual(stg_row["status"], "COMMITTED")

    def test_staging_02_dismiss_item_never_deletes_physical_file(self):
        """Dismissing an intake item marks it DISMISSED but NEVER deletes the file from disk."""
        wf_dir = self.test_dir / "wf_dismiss_safety"
        wf_dir.mkdir(parents=True, exist_ok=True)
        track = wf_dir / "Keep Me On Disk.wav"
        generate_synthetic_wav(track, duration_sec=1.0)

        wf = FolderWatcherService.add_folder(str(wf_dir), auto_scan=False)
        FolderWatcherService.rescan_folder(wf["id"])

        pending = StagingService.get_pending_watcher_items()
        self.assertEqual(len(pending), 1)
        item_id = pending[0]["id"]

        # Dismiss item
        res = self.client.post("/api/watcher/items/dismiss", json={"item_id": item_id})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json().get("success"))

        # Physical file MUST still exist!
        self.assertTrue(track.exists())

        # No pending items left
        self.assertEqual(len(StagingService.get_pending_watcher_items()), 0)

    # ================= 6. API VALIDATION & BOUNDARY GUARDS =================

    def test_api_01_watcher_add_missing_params(self):
        """Rejects requests with missing or empty path with 400 Bad Request."""
        res = self.client.post("/api/watcher/folders/add", json={})
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/watcher/folders/add", json={"path": ""})
        self.assertEqual(res.status_code, 400)

    def test_api_02_watcher_remove_missing_folder_id(self):
        """Rejects folder removal without folder_id with 400 Bad Request."""
        res = self.client.post("/api/watcher/folders/remove", json={})
        self.assertEqual(res.status_code, 400)

    def test_api_03_watcher_approve_missing_item_id(self):
        """Rejects approval requests without item_id with 400 Bad Request."""
        res = self.client.post("/api/watcher/items/approve", json={})
        self.assertEqual(res.status_code, 400)

    # ================= 7. CONCURRENCY & ZERO C: FOOTPRINT =================

    def test_perf_01_concurrent_rescan_requests(self):
        """Concurrent rescan calls on the same folder do not corrupt state or crash."""
        wf_dir = self.test_dir / "wf_concurrent"
        wf_dir.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            generate_synthetic_wav(wf_dir / f"Track_{i}.wav", duration_sec=0.5)

        wf = FolderWatcherService.add_folder(str(wf_dir), auto_scan=False)

        results = []
        def worker():
            res = FolderWatcherService.rescan_folder(wf["id"])
            results.append(res)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(len(results), 3)
        # At least one rescan completes cleanly; others complete or skip gracefully
        completed = [r for r in results if r.get("status") == "COMPLETED"]
        self.assertGreaterEqual(len(completed), 1)

    def test_containment_01_zero_c_drive_writes(self):
        """Verifies that project TEMP_DIR, DB_PATH, and test artifacts strictly reside on D:."""
        from src.config_loader import APP_DIR
        app_root = str(APP_DIR).lower()
        self.assertTrue(str(TEMP_DIR).lower().startswith(app_root))
        self.assertTrue(str(DB_PATH).lower().startswith(app_root))
        self.assertTrue(str(self.test_dir).lower().startswith(app_root))


if __name__ == "__main__":
    unittest.main()
