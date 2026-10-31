try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import shutil
import unittest
from pathlib import Path

from src.config_loader import PROJECT_ROOT, get_downloads_path
from src.db import get_db_connection, init_db
from src.rules_engine import RulesEngine
from src.web.server import app


class TestRoadmapFeatures(unittest.TestCase):
    """
    Automated Test Suite for Roadmap Features:
    - Declarative Rules Engine (conditions, preview, execute, rollback)
    - Batch Library Operations (batch-tags, batch-reindex, selective export-m3u)
    - Unified Operations Center API
    - Audio Streaming and Security Boundaries
    """

    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = app.test_client()
        cls.test_dir = PROJECT_ROOT / "temp" / "test_roadmap_env"
        cls.test_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            try:
                shutil.rmtree(cls.test_dir)
            except Exception:
                pass

    def setUp(self):
        self.conn = get_db_connection()
        # Clean test tables
        with self.conn:
            self.conn.execute("DELETE FROM organization_rules")
            self.conn.execute("DELETE FROM rules_execution_log")

    # ================= RULES ENGINE TESTS =================

    def test_rules_engine_crud(self):
        """Test rule creation, retrieval, and deletion."""
        rule_data = {
            "name": "Classical Organizer",
            "priority": 5,
            "condition_type": "genre_contains",
            "condition_value": "Classical",
            "action_type": "move_to_template",
            "action_value": "Classical/{artist}/{album}/{title}.{ext}",
            "is_enabled": 1
        }
        saved = RulesEngine.save_rule(rule_data)
        self.assertIn("id", saved)
        self.assertEqual(saved["name"], "Classical Organizer")

        rules = RulesEngine.get_rules()
        self.assertTrue(any(r["name"] == "Classical Organizer" for r in rules))

        deleted = RulesEngine.delete_rule(saved["id"])
        self.assertTrue(deleted)

        rules_after = RulesEngine.get_rules()
        self.assertFalse(any(r["id"] == saved["id"] for r in rules_after))

    def test_rules_engine_condition_evaluation(self):
        """Test rule condition matching logic."""
        file_meta = {
            "title": "Symphony No. 5",
            "artist": "Beethoven",
            "album": "Masterpieces",
            "genre": "Classical Orchestral",
            "bitrate_kbps": 320,
            "format": "flac",
            "has_artwork": 1,
            "year": "2020"
        }

        # genre_contains
        self.assertTrue(RulesEngine.evaluate_condition("genre_contains", "classical", file_meta))
        self.assertFalse(RulesEngine.evaluate_condition("genre_contains", "hiphop", file_meta))

        # artist_contains
        self.assertTrue(RulesEngine.evaluate_condition("artist_contains", "beethoven", file_meta))
        self.assertFalse(RulesEngine.evaluate_condition("artist_contains", "mozart", file_meta))

        # bitrate_below
        self.assertTrue(RulesEngine.evaluate_condition("bitrate_below", "400", file_meta))
        self.assertFalse(RulesEngine.evaluate_condition("bitrate_below", "256", file_meta))

        # format_equals
        self.assertTrue(RulesEngine.evaluate_condition("format_equals", "flac", file_meta))
        self.assertFalse(RulesEngine.evaluate_condition("format_equals", "mp3", file_meta))

        # missing_tag
        self.assertFalse(RulesEngine.evaluate_condition("missing_tag", "year", file_meta))
        file_meta_missing = dict(file_meta)
        file_meta_missing["year"] = ""
        self.assertTrue(RulesEngine.evaluate_condition("missing_tag", "year", file_meta_missing))

        # all
        self.assertTrue(RulesEngine.evaluate_condition("all", "", file_meta))

    def test_rules_preview_and_execute_and_rollback(self):
        """Test dry-run preview, atomic execution, and rollback."""
        # Create a dummy audio file in test_dir
        source_file = self.test_dir / "raw_track.mp3"
        source_file.write_bytes(b"\x00" * 2048)

        # Index in DB
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO library_files (path, filename, folder, title, artist, album, genre, format, size_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET title = excluded.title
                """,
                (str(source_file), source_file.name, str(source_file.parent), "Raw Track", "Test Artist", "Test Album", "Electronic", "mp3", 2048)
            )

        # 1. Preview
        rule = {
            "id": "rule_test",
            "name": "Electronic Sorter",
            "priority": 1,
            "condition_type": "genre_contains",
            "condition_value": "Electronic",
            "action_type": "rename_template",
            "action_value": "{artist} - {title}.{ext}",
            "is_enabled": 1
        }
        preview_res = RulesEngine.preview(rules=[rule], file_paths=[str(source_file)])
        self.assertEqual(preview_res["total_evaluated"], 1)
        self.assertEqual(preview_res["will_move"], 1)
        self.assertEqual(len(preview_res["planned_actions"]), 1)

        planned = preview_res["planned_actions"][0]
        self.assertEqual(planned["filename"], "raw_track.mp3")
        self.assertEqual(planned["target_filename"], "Test Artist - Raw Track.mp3")

        # Verify preview did NOT touch the file
        self.assertTrue(source_file.exists())

        # 2. Execute
        exec_res = RulesEngine.execute(preview_res["planned_actions"], collision_policy="skip")
        self.assertTrue(exec_res["success"])
        self.assertEqual(exec_res["executed_count"], 1)
        target_path = Path(planned["target_path"])
        self.assertTrue(target_path.exists())
        self.assertFalse(source_file.exists())

        # 3. Rollback
        rb_res = RulesEngine.rollback(exec_res["batch_id"])
        self.assertTrue(rb_res["success"])
        self.assertEqual(rb_res["reverted_count"], 1)
        self.assertTrue(source_file.exists())
        self.assertFalse(target_path.exists())

    def test_safe_overwrite_preservation_and_rollback(self):
        """Test that overwrite policy preserves existing destination in recovery buffer and restores it on rollback."""
        src_file = self.test_dir / "new_version.mp3"
        src_file.write_text("NEW_AUDIO_CONTENT_VERSION_2")

        dest_file = self.test_dir / "Final Track.mp3"
        dest_file.write_text("OLD_ORIGINAL_AUDIO_CONTENT_VERSION_1")

        action = [{
            "rule_id": "rule_overwrite",
            "rule_name": "Overwrite Test",
            "source_path": str(src_file),
            "target_path": str(dest_file),
            "filename": src_file.name,
            "target_filename": dest_file.name,
            "will_move": True,
            "collision": True,
            "action": "rename_template"
        }]

        # Execute with overwrite
        res = RulesEngine.execute(action, collision_policy="overwrite")
        self.assertTrue(res["success"])
        self.assertEqual(res["executed_count"], 1)

        # Destination should now contain the new content
        self.assertEqual(dest_file.read_text(), "NEW_AUDIO_CONTENT_VERSION_2")
        self.assertFalse(src_file.exists())

        # Rollback
        rb = RulesEngine.rollback(res["batch_id"])
        self.assertTrue(rb["success"])
        self.assertEqual(rb["reverted_count"], 1)

        # Source file should be restored with new content
        self.assertTrue(src_file.exists())
        self.assertEqual(src_file.read_text(), "NEW_AUDIO_CONTENT_VERSION_2")

        # Destination file should be fully restored with its original content!
        self.assertTrue(dest_file.exists())
        self.assertEqual(dest_file.read_text(), "OLD_ORIGINAL_AUDIO_CONTENT_VERSION_1")

    def test_repeated_rollback_idempotency(self):
        """Test that calling rollback multiple times on the same batch is handled safely and returns informative status."""
        test_file = self.test_dir / "idempotent_src.mp3"
        test_file.write_text("IDEMPOTENT_TEST")

        target_file = self.test_dir / "idempotent_target.mp3"
        if target_file.exists():
            target_file.unlink()

        action = [{
            "rule_id": "rule_idem",
            "rule_name": "Idempotent Rule",
            "source_path": str(test_file),
            "target_path": str(target_file),
            "filename": test_file.name,
            "target_filename": target_file.name,
            "will_move": True,
            "collision": False,
            "action": "rename_template"
        }]

        res = RulesEngine.execute(action, collision_policy="skip")
        batch_id = res["batch_id"]

        # First rollback succeeds
        rb1 = RulesEngine.rollback(batch_id)
        self.assertTrue(rb1["success"])
        self.assertEqual(rb1["reverted_count"], 1)

        # Second rollback on same batch_id should gracefully return false without crashing
        rb2 = RulesEngine.rollback(batch_id)
        self.assertFalse(rb2["success"])
        self.assertIn("already rolled back", rb2["message"])

    # ================= BATCH LIBRARY OPERATIONS TESTS =================

    def test_batch_reindex_endpoint(self):
        """Test POST /api/library/batch-reindex endpoint."""
        test_file = self.test_dir / "reindex_sample.mp3"
        test_file.write_bytes(b"\x00" * 1024)

        res = self.client.post("/api/library/batch-reindex", json={"paths": [str(test_file)]})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["reindexed_count"], 1)

    def test_batch_tag_partial_failure(self):
        """Test POST /api/library/batch-tags with valid and missing files handles partial failure transparently."""
        valid_track = self.test_dir / "partial_valid.mp3"
        valid_track.write_bytes(b"\x00" * 1024)
        missing_track = self.test_dir / "partial_missing_nonexistent.mp3"

        res = self.client.post("/api/library/batch-tags", json={
            "paths": [str(valid_track), str(missing_track)],
            "metadata": {"genre": "Synthwave"}
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        # Not completely successful because 1 failed, but updated valid file
        self.assertFalse(data["success"])
        self.assertEqual(data["updated_count"], 1)
        self.assertEqual(data["error_count"], 1)
        self.assertEqual(len(data["errors"]), 1)
        self.assertEqual(data["errors"][0]["path"], str(missing_track))

    def test_selective_export_m3u(self):
        """Test POST /api/library/export-m3u with specific paths."""
        track_path = self.test_dir / "playlist_song.mp3"
        track_path.write_bytes(b"\x00" * 1024)

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO library_files (path, filename, folder, title, artist, album, format, duration_sec, size_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, 180.0, 1024)
                ON CONFLICT(path) DO UPDATE SET title = excluded.title
                """,
                (str(track_path), track_path.name, str(track_path.parent), "Playlist Song", "Band", "Album", "mp3")
            )

        res = self.client.post("/api/library/export-m3u", json={
            "paths": [str(track_path)],
            "playlist_name": "Test_Custom_Mix"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["track_count"], 1)
        m3u_file = Path(data["path"])
        self.assertTrue(m3u_file.exists())
        content = m3u_file.read_text(encoding="utf-8")
        self.assertIn("#EXTM3U", content)
        self.assertIn("Playlist Song", content)

    # ================= OPERATIONS CENTER & AUDIO STREAM TESTS =================

    def test_operations_center_endpoint(self):
        """Test GET /api/operations/center returns structured health telemetry."""
        res = self.client.get("/api/operations/center")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIn("health", data)
        self.assertIn("queue", data)
        self.assertIn("watcher", data)
        self.assertIn("indexer", data)
        self.assertEqual(data["health"]["status"], "HEALTHY")

    def test_audio_stream_supported_and_missing_files(self):
        """Test GET /api/stream returns 200 for local file, 404 for missing file, and 403 for escape."""
        audio_file = get_downloads_path() / "test_stream_audio.mp3"
        audio_file.write_bytes(b"\x49\x44\x33" + b"\x00" * 2048)  # ID3 header mock

        res_ok = None
        try:
            # 1. Valid stream
            res_ok = self.client.get(f"/api/stream?path={audio_file}")
            self.assertEqual(res_ok.status_code, 200)
            self.assertEqual(len(res_ok.data), 2051)

            # 2. Missing file inside downloads
            missing_in_down = get_downloads_path() / "ghost_file.mp3"
            res_404 = self.client.get(f"/api/stream?path={missing_in_down}")
            self.assertEqual(res_404.status_code, 404)

            # 3. Path outside project root
            res_403 = self.client.get("/api/stream?path=C:\\Windows\\System32\\cmd.exe")
            self.assertEqual(res_403.status_code, 403)
            data_403 = res_403.get_json()
            self.assertFalse(data_403.get("success", True))
        finally:
            if res_ok is not None:
                res_ok.close()
            if audio_file.exists():
                audio_file.unlink()

    def test_audio_stream_security_boundary(self):
        """Test GET /api/stream rejects paths outside the project root."""
        res = self.client.get("/api/stream?path=C:\\Windows\\System32\\cmd.exe")
        self.assertEqual(res.status_code, 403)
        data = res.get_json()
        self.assertFalse(data.get("success", True))


if __name__ == "__main__":
    unittest.main()
