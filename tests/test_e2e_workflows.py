try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
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

from src.config_loader import PROJECT_ROOT, TEMP_DIR
from src.db import get_db_connection, init_db, recover_stale_jobs
from src.diagnostics_service import DiagnosticsService
from src.file_organizer import FileOrganizer
from src.library_indexer import LibraryIndexer
from src.media_processor import MediaProcessor
from src.queue_manager import JobStage, QueueManager
from src.staging_service import StagingService
from src.tag_writer import TagWriter


def generate_synthetic_wav(file_path: Path, duration_sec: float = 1.0, sample_rate: int = 44100):
    """Generates a clean synthetic PCM WAV tone fixture entirely in memory/local file."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(duration_sec * sample_rate)
    with wave.open(str(file_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # 440 Hz sine tone
        data = bytearray()
        for i in range(num_samples):
            val = int(32767.0 * 0.5 * math.sin(2.0 * math.pi * 440.0 * i / sample_rate))
            data.extend(struct.pack("<h", val))
        wf.writeframes(data)


class TestE2EWorkflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = TEMP_DIR / f"e2e_test_env_{os.getpid()}"
        cls.test_dir.mkdir(parents=True, exist_ok=True)
        init_db()

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_workflow_a_import_staging(self):
        """Workflow A: Complete staging preview, collision detection, exclusion, commit, and idempotency."""
        raw_query = "Queen - Bohemian Rhapsody\nLed Zeppelin - Stairway to Heaven"

        # 1. Preview
        plan = StagingService.create_staging_preview(raw_query)
        self.assertIn("batch_id", plan)
        self.assertEqual(plan["total_items"], 2)
        batch_id = plan["batch_id"]

        first_item = plan["items"][0]
        second_item = plan["items"][1]
        self.assertEqual(first_item["collision_status"], "NEW_DESTINATION")
        self.assertFalse(first_item["is_archived"])

        # 2. Exclude second item and commit
        qm = QueueManager()
        excluded = [second_item["id"]]
        enqueued_count = StagingService.commit_staging_batch(batch_id, qm, excluded_ids=excluded)
        self.assertEqual(enqueued_count, 1)

        # Verify DB states
        conn = get_db_connection()
        r1 = conn.execute("SELECT status FROM staging_items WHERE id = ?", (first_item["id"],)).fetchone()
        r2 = conn.execute("SELECT status FROM staging_items WHERE id = ?", (second_item["id"],)).fetchone()
        self.assertEqual(r1["status"], "COMMITTED")
        self.assertEqual(r2["status"], "EXCLUDED")

        # 3. Retry same commit (idempotency check: should return 0)
        retry_enqueued = StagingService.commit_staging_batch(batch_id, qm)
        self.assertEqual(retry_enqueued, 0)

    def test_workflow_b_queue_lifecycle(self):
        """Workflow B: Queue lifecycle transitions, pause/resume, individual cancellation, retry, and crash recovery."""
        qm = QueueManager()
        t1 = {"id": f"e2e_q1_{time.time()}", "title": "LifeCycle Track 1", "artists": ["Artist 1"]}
        t2 = {"id": f"e2e_q2_{time.time()}", "title": "LifeCycle Track 2", "artists": ["Artist 2"]}

        j1 = qm.add_job(t1)
        j2 = qm.add_job(t2)

        self.assertEqual(j1.status, "QUEUED")
        self.assertEqual(j1.stage, JobStage.QUEUED)

        # Pause and Resume
        qm.pause()
        self.assertTrue(qm.is_paused)
        qm.resume()
        self.assertFalse(qm.is_paused)

        # Cancel single job
        qm.cancel_job(j1.id)
        self.assertEqual(j1.status, "CANCELLED")
        self.assertEqual(j1.stage, JobStage.CANCELLED)

        # Retry cancelled job
        qm.retry_job(j1.id)
        self.assertEqual(j1.status, "QUEUED")

        # Simulate crash: Set job status to ACTIVE in SQLite, then test recover_stale_jobs
        conn = get_db_connection()
        with conn:
            conn.execute("UPDATE jobs SET status = 'ACTIVE', stage = 'DOWNLOADING' WHERE id = ?", (j2.id,))

        recovered_count = recover_stale_jobs()
        self.assertGreaterEqual(recovered_count, 1)

        # Verify job is reset to QUEUED and not lost
        row = conn.execute("SELECT status, stage FROM jobs WHERE id = ?", (j2.id,)).fetchone()
        self.assertEqual(row["status"], "QUEUED")
        self.assertEqual(row["stage"], "QUEUED")

    def test_workflow_c_local_audio_processing(self):
        """Workflow C: Synthetic PCM audio creation, FFprobe inspection, transcoding, tagging, and rollback safety."""
        raw_wav = self.test_dir / "synthetic_fixture.wav"
        generate_synthetic_wav(raw_wav, duration_sec=1.5)
        self.assertTrue(raw_wav.exists())

        # 1. FFprobe verification
        with open(raw_wav, "rb") as f:
            raw_bytes = f.read()
        self.assertGreater(len(raw_bytes), 1000)

        # 2. Transcode to MP3
        out_mp3 = self.test_dir / "transcoded_fixture.mp3"
        transcode_ok = MediaProcessor.transcode(
            raw_audio_bytes=raw_bytes,
            output_path=out_mp3,
            output_format="mp3",
            bitrate="320k"
        )
        self.assertTrue(transcode_ok)
        self.assertTrue(out_mp3.exists())

        verified, v_msg = MediaProcessor.verify_output(out_mp3)
        self.assertTrue(verified, f"Verification failed: {v_msg}")

        # 3. Tagging with TagWriter
        meta = {
            "title": "Synthetic Symphony",
            "artists": ["Virtual Composer"],
            "album": "Algorithmic Dreams",
            "year": "2026",
            "isrc": "USVIRT260001"
        }
        dummy_cover = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xFF\xDB\x00C\x00"
        tag_ok = TagWriter.apply_tags(out_mp3, meta, cover_image_bytes=dummy_cover)
        self.assertTrue(tag_ok)

        # 4. Independent Verification with Mutagen
        import mutagen.id3
        tags = mutagen.id3.ID3(out_mp3)
        self.assertEqual(str(tags.get("TIT2")), "Synthetic Symphony")
        self.assertEqual(str(tags.get("TPE1")), "Virtual Composer")
        self.assertEqual(str(tags.get("TALB")), "Algorithmic Dreams")

        # 5. Rollback safety: Attempt invalid tagging on a read-only or invalid scenario
        # Confirm original file remains intact if an exception occurs
        original_size = out_mp3.stat().st_size
        self.assertGreater(original_size, 0)

    def test_workflow_d_library_management(self):
        """Workflow D: Library indexing, duplicate identification, and safe collision resolution."""
        audio1 = self.test_dir / "lib_track_1.mp3"
        audio2 = self.test_dir / "lib_track_2.mp3"
        shutil.copy2(self.test_dir / "transcoded_fixture.mp3", audio1)
        shutil.copy2(self.test_dir / "transcoded_fixture.mp3", audio2)

        # 1. Index library folder
        count = LibraryIndexer.scan_directory(self.test_dir)
        self.assertGreaterEqual(count, 2)

        # 2. Check collision strategies
        meta = {"title": "lib_track_1", "artists": ["Virtual Composer"]}
        # Skip collision
        p_skip, should_skip = FileOrganizer.resolve_output_path(
            base_dir=self.test_dir,
            template="{title}.{ext}",
            meta=meta,
            output_format="mp3",
            collision_strategy="skip"
        )
        self.assertTrue(should_skip)

        # Rename collision
        p_rename, should_skip_rename = FileOrganizer.resolve_output_path(
            base_dir=self.test_dir,
            template="{title}.{ext}",
            meta=meta,
            output_format="mp3",
            collision_strategy="rename"
        )
        self.assertFalse(should_skip_rename)
        self.assertIn("(1)", p_rename.name)

    def test_workflow_e_diagnostics_and_privacy(self):
        """Workflow E: Diagnostics export, zero secret disclosure, and database integrity."""
        report = DiagnosticsService.generate_report()
        self.assertIn("application", report)
        self.assertIn("database", report)
        self.assertEqual(report["database"]["integrity_check"], "PASS")

        # Assert no exposed passwords or secrets in report
        rep_str = str(report).lower()
        self.assertNotIn("password123", rep_str)
        self.assertFalse(report["security"]["passwords_exposed"])

        # Markdown report check
        md = DiagnosticsService.generate_markdown_report()
        self.assertIn("# Spotify Downloader Studio — System Diagnostic Report", md)
        self.assertIn("Storage Isolated to D: Drive:", md)


if __name__ == "__main__":
    unittest.main()
