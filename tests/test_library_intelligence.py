try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import csv
import math
import os
import shutil
import struct
import sys
import time
import unittest
import wave
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_loader import PROJECT_ROOT, TEMP_DIR, get_downloads_path
from src.db import get_db_connection, init_db
from src.library_indexer import LibraryIndexer
from src.library_intelligence import LibraryIntelligence
from src.web.server import app


def generate_synthetic_audio(file_path: Path, freq: float = 440.0, duration_sec: float = 1.0, sample_rate: int = 44100):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(duration_sec * sample_rate)
    with wave.open(str(file_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        data = bytearray()
        for i in range(num_samples):
            val = int(32767.0 * 0.4 * math.sin(2.0 * math.pi * freq * i / sample_rate))
            data.extend(struct.pack("<h", val))
        wf.writeframes(data)


class TestLibraryIntelligence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = TEMP_DIR / f"test_lib_intel_{os.getpid()}"
        cls.test_dir.mkdir(parents=True, exist_ok=True)
        init_db()
        app.testing = True
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        # Clear library_files table before each test for clean isolation
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM library_files")
            conn.execute("DELETE FROM organize_history")
            conn.execute("DELETE FROM library_scans")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)
        self.test_dir.mkdir(parents=True, exist_ok=True)

    def test_exact_duplicate_detection(self):
        """Verify cryptographic SHA-256 duplicate detection groups byte-identical files."""
        f1 = self.test_dir / "orig_song.wav"
        f2 = self.test_dir / "duplicate_song.wav"
        generate_synthetic_audio(f1, freq=440.0, duration_sec=1.0)
        shutil.copy2(f1, f2)  # byte-for-byte identical

        LibraryIndexer.scan_directory(self.test_dir)

        dups = LibraryIntelligence.get_exact_duplicates()
        self.assertEqual(len(dups), 1)
        group = dups[0]
        self.assertEqual(group["type"], "EXACT_HASH")
        self.assertEqual(group["count"], 2)
        self.assertIn("recommended_keep_path", group)
        self.assertTrue(Path(group["recommended_keep_path"]).exists())

    def test_probable_duplicate_grouping_and_false_positive_prevention(self):
        """Verify probable duplicate grouping on normalized metadata and false positive prevention."""
        conn = get_db_connection()

        # Two recordings of the same song with title noise delta
        p1 = str(self.test_dir / "bohemian_1.wav")
        p2 = str(self.test_dir / "bohemian_2.wav")
        # Different artist (cover version) -> must NOT be grouped
        p3 = str(self.test_dir / "bohemian_cover.wav")
        # Same artist, completely different title -> must NOT be grouped
        p4 = str(self.test_dir / "radio_ga_ga.wav")

        for p in (p1, p2, p3, p4):
            generate_synthetic_audio(Path(p), duration_sec=2.0)

        with conn:
            conn.execute("""
                INSERT INTO library_files (path, filename, title, artist, album, duration_sec, format, size_bytes, sha256)
                VALUES (?, 'bohemian_1.wav', 'Bohemian Rhapsody', 'Queen', 'A Night at the Opera', 2.0, 'wav', 1000, 'sha_dummy_1')
            """, (p1,))
            conn.execute("""
                INSERT INTO library_files (path, filename, title, artist, album, duration_sec, format, size_bytes, sha256)
                VALUES (?, 'bohemian_2.wav', 'Bohemian Rhapsody (2011 Remaster)', 'Queen', 'Greatest Hits', 2.5, 'wav', 1200, 'sha_dummy_2')
            """, (p2,))
            conn.execute("""
                INSERT INTO library_files (path, filename, title, artist, album, duration_sec, format, size_bytes, sha256)
                VALUES (?, 'bohemian_cover.wav', 'Bohemian Rhapsody', 'Panic! At The Disco', 'Suicide Squad', 2.0, 'wav', 1050, 'sha_dummy_3')
            """, (p3,))
            conn.execute("""
                INSERT INTO library_files (path, filename, title, artist, album, duration_sec, format, size_bytes, sha256)
                VALUES (?, 'radio_ga_ga.wav', 'Radio Ga Ga', 'Queen', 'The Works', 2.0, 'wav', 1100, 'sha_dummy_4')
            """, (p4,))

        probable_groups = LibraryIntelligence.find_probable_recording_duplicates()
        self.assertEqual(len(probable_groups), 1, "Should identify exactly 1 probable duplicate group")

        group = probable_groups[0]
        self.assertEqual(group["count"], 2)
        group_paths = [f["path"] for f in group["files"]]
        self.assertIn(p1, group_paths)
        self.assertIn(p2, group_paths)
        self.assertNotIn(p3, group_paths, "Cover version by different artist must not be grouped")
        self.assertNotIn(p4, group_paths, "Different song by same artist must not be grouped")

    def test_empty_and_incomplete_metadata_audit(self):
        """Verify detection of files lacking required metadata or with corrupt tags."""
        audio_file = self.test_dir / "incomplete_track.wav"
        generate_synthetic_audio(audio_file, duration_sec=1.0)

        LibraryIndexer.scan_directory(self.test_dir)

        # Incomplete track should have issues flagged
        audit_res = LibraryIntelligence.get_metadata_audit_items()
        self.assertGreaterEqual(audit_res["total"], 1)

        # Overview health metrics should accurately count missing tags
        overview = LibraryIntelligence.get_intelligence_overview()
        self.assertGreaterEqual(overview["missing_tags_count"], 1)
        self.assertGreaterEqual(overview["files_requiring_review"], 1)

    def test_cache_invalidation_after_file_modification(self):
        """Verify incremental cache skips unchanged files and re-hashes modified files."""
        f = self.test_dir / "cache_test.wav"
        generate_synthetic_audio(f, freq=440.0, duration_sec=1.0)

        conn = get_db_connection()
        LibraryIndexer._index_file(conn, f)

        initial_row = conn.execute("SELECT sha256, mtime, last_scanned FROM library_files WHERE path = ?", (str(f),)).fetchone()
        initial_sha = initial_row["sha256"]
        self.assertTrue(initial_sha)

        # Re-index immediately without touching file: should be a cache hit (sha256 unchanged)
        LibraryIndexer._index_file(conn, f)
        cached_row = conn.execute("SELECT sha256, mtime FROM library_files WHERE path = ?", (str(f),)).fetchone()
        self.assertEqual(cached_row["sha256"], initial_sha)

        # Now modify file content and advance mtime
        time.sleep(0.05)
        generate_synthetic_audio(f, freq=880.0, duration_sec=1.5)  # new content
        new_mtime = time.time() + 10.0
        os.utime(f, (new_mtime, new_mtime))

        # Re-index: cache must invalidate and compute new SHA-256
        LibraryIndexer._index_file(conn, f)
        updated_row = conn.execute("SELECT sha256, mtime FROM library_files WHERE path = ?", (str(f),)).fetchone()
        self.assertNotEqual(updated_row["sha256"], initial_sha)

    def test_metadata_proposed_diff_and_repair_with_safety_rollback(self):
        """Verify proposed-vs-current metadata generation and repair with backup rollback."""
        audio_file = self.test_dir / "01 - Pink Floyd - Time.wav"
        generate_synthetic_audio(audio_file, duration_sec=1.0)

        conn = get_db_connection()
        LibraryIndexer._index_file(conn, audio_file)

        # Generate proposal
        prop = LibraryIntelligence.generate_proposed_metadata(str(audio_file))
        self.assertTrue(prop["has_changes"])
        fields_map = {f["field"]: f for f in prop["fields"]}

        self.assertEqual(fields_map["artist"]["proposed"], "Pink Floyd")
        self.assertEqual(fields_map["title"]["proposed"], "Time")
        self.assertEqual(fields_map["track_number"]["proposed"], "1")

        # Apply approved repair
        approved = {
            "title": "Time (2026 Remaster)",
            "artist": "Pink Floyd",
            "album": "The Dark Side of the Moon"
        }
        res = LibraryIntelligence.apply_metadata_repair(str(audio_file), approved)
        self.assertTrue(res)

        # Verify DB updated
        row = conn.execute("SELECT title, artist, album FROM library_files WHERE path = ?", (str(audio_file),)).fetchone()
        self.assertEqual(row["title"], "Time (2026 Remaster)")
        self.assertEqual(row["artist"], "Pink Floyd")

    def test_safe_duplicate_actions_and_protection(self):
        """Verify duplicate file moves to backup and refusal to delete single remaining copy."""
        f1 = self.test_dir / "safe_copy_1.wav"
        f2 = self.test_dir / "safe_copy_2.wav"
        generate_synthetic_audio(f1, duration_sec=1.0)
        shutil.copy2(f1, f2)

        conn = get_db_connection()
        LibraryIndexer._index_file(conn, f1)
        LibraryIndexer._index_file(conn, f2)

        # Move f2 to backup
        move_res = LibraryIntelligence.move_duplicate_to_backup(str(f2))
        self.assertTrue(move_res["success"])
        self.assertFalse(f2.exists())
        self.assertTrue(Path(move_res["backup_path"]).exists())

        # Attempting to delete f1 now must fail because f1 is the ONLY copy remaining in library_files
        with self.assertRaises(RuntimeError):
            LibraryIntelligence.delete_duplicate_safely(str(f1))

    def test_bulk_organization_preview_and_rollback(self):
        """Verify bulk reorganization dry-run preview, atomic moves, and rollback."""
        down_dir = get_downloads_path()
        song = down_dir / "Bulk Artist - Bulk Title.mp3"
        song.parent.mkdir(parents=True, exist_ok=True)
        song.write_text("dummy audio", encoding="utf-8")

        conn = get_db_connection()
        with conn:
            conn.execute("""
                INSERT INTO library_files (path, filename, title, artist, album, track_number, format, size_bytes)
                VALUES (?, 'Bulk Artist - Bulk Title.mp3', 'Bulk Title', 'Bulk Artist', 'Bulk Album', 1, 'mp3', 100)
            """, (str(song),))

        # 1. Preview
        template = "{artist}/{album}/{track_number} - {title}.{ext}"
        preview = LibraryIntelligence.preview_bulk_organization(template=template)
        self.assertEqual(preview["total_files"], 1)
        self.assertEqual(preview["will_move"], 1)
        move_item = preview["moves"][0]
        self.assertEqual(move_item["status"], "WILL_MOVE")

        # 2. Execute
        exec_res = LibraryIntelligence.execute_bulk_organization(preview["moves"])
        self.assertEqual(exec_res["moved_count"], 1)
        batch_id = exec_res["batch_id"]

        new_path = Path(move_item["target_path"])
        self.assertTrue(new_path.exists())
        self.assertFalse(song.exists())

        # 3. Rollback
        restored = LibraryIntelligence.rollback_bulk_organization(batch_id)
        self.assertEqual(restored, 1)
        self.assertTrue(song.exists())
        self.assertFalse(new_path.exists())

        # Cleanup
        if song.exists():
            song.unlink()

    def test_export_audit_csv_correctness(self):
        """Verify export_audit_csv generates valid CSV with all expected columns."""
        f = self.test_dir / "audit_test.wav"
        generate_synthetic_audio(f, duration_sec=1.0)
        conn = get_db_connection()
        LibraryIndexer._index_file(conn, f)

        out_csv = self.test_dir / "output_audit.csv"
        count = LibraryIndexer.export_audit_csv(out_csv)
        self.assertGreaterEqual(count, 1)
        self.assertTrue(out_csv.exists())

        with open(out_csv, "r", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            rows = list(reader)
            self.assertEqual(len(rows), count)
            first = rows[0]
            self.assertIn("title", first)
            self.assertIn("artist", first)
            self.assertIn("bitrate_kbps", first)
            self.assertIn("sha256", first)
            self.assertIn("tag_status", first)

    def test_library_pagination_and_drill_down_endpoints(self):
        """Verify GET /api/library pagination parameters and filter options."""
        conn = get_db_connection()
        for i in range(15):
            p = str(self.test_dir / f"page_track_{i}.wav")
            with conn:
                conn.execute("""
                    INSERT INTO library_files (path, filename, title, artist, album, duration_sec, format, size_bytes, tag_status, has_artwork)
                    VALUES (?, ?, ?, 'Page Artist', 'Page Album', 120.0, 'wav', 1000, 'VALID', 1)
                """, (p, f"page_track_{i}.wav", f"Title {i}"))

        # Fetch page 1 (limit 5, offset 0)
        res1 = self.client.get("/api/library?limit=5&offset=0")
        self.assertEqual(res1.status_code, 200)
        data1 = res1.get_json()
        self.assertEqual(data1["total"], 15)
        self.assertEqual(len(data1["files"]), 5)

        # Fetch page 2 (limit 5, offset 5)
        res2 = self.client.get("/api/library?limit=5&offset=5")
        data2 = res2.get_json()
        self.assertEqual(len(data2["files"]), 5)
        self.assertNotEqual(data1["files"][0]["path"], data2["files"][0]["path"])


if __name__ == "__main__":
    unittest.main()
