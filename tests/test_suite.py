try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import os
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_loader import PROJECT_ROOT, TEMP_DIR, get_downloads_path
from src.db import get_db_connection, init_db
from src.link_parser import LinkParser
from src.match_engine import MatchCandidate, MatchEngine
from src.media_processor import get_ffmpeg_bin, get_ffprobe_bin
from src.queue_manager import QueueManager
from src.security import SecurityManager
from src.backup_service import BackupService
from src.web.server import app


class TestSpotifyDownloaderSuite(unittest.TestCase):
    def test_01_storage_containment(self):
        """Verify strict storage containment on D: drive"""
        self.assertTrue(str(PROJECT_ROOT).lower().startswith("d:\\mini-projects\\spotify_playlist_downloader"))
        self.assertTrue(str(TEMP_DIR).lower().startswith("d:\\mini-projects\\spotify_playlist_downloader"))
        self.assertTrue(str(get_downloads_path()).lower().startswith("d:\\mini-projects\\spotify_playlist_downloader"))
        self.assertEqual(os.environ.get("TEMP"), str(TEMP_DIR))
        self.assertEqual(os.environ.get("TMP"), str(TEMP_DIR))

    def test_02_database_integrity(self):
        """Verify SQLite DB initialization in WAL mode"""
        init_db()
        conn = get_db_connection()
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")
        ok, msg = BackupService.verify_database_integrity()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")

    def test_03_link_parser(self):
        """Verify LinkParser multi-format parsing"""
        urls = """
        https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT
        spotify:album:1DFixLWuPkv3KT3TnV35m3
        https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
        Queen - Bohemian Rhapsody
        """
        items = LinkParser.parse_multiline(urls, deduplicate=True)
        self.assertEqual(len(items), 4)
        types = [item.kind for item in items]
        self.assertIn("track", types)
        self.assertIn("album", types)
        self.assertIn("playlist", types)
        self.assertIn("search", types)

    def test_04_match_engine(self):
        """Verify multi-factor candidate matching scoring"""
        cand = MatchCandidate(
            candidate_id="cand_1",
            title="Bohemian Rhapsody",
            artists=["Queen"],
            duration_ms=354000,
            isrc="GBUM71029604"
        )
        expected = {
            "title": "Bohemian Rhapsody",
            "artists": ["Queen"],
            "duration_ms": 355000,
            "isrc": "GBUM71029604"
        }
        score = MatchEngine.evaluate_match(expected, cand)
        self.assertEqual(score, 1.0)
        self.assertEqual(cand.confidence_score, 1.0)

    def test_05_ffmpeg_bin_discovery(self):
        """Verify FFmpeg and FFprobe binary discovery from project bin"""
        ff_bin = get_ffmpeg_bin()
        fp_bin = get_ffprobe_bin()
        self.assertTrue(Path(ff_bin).exists())
        self.assertTrue(Path(fp_bin).exists())
        self.assertTrue(str(ff_bin).lower().endswith("ffmpeg.exe"))
        self.assertTrue(str(fp_bin).lower().endswith("ffprobe.exe"))

    def test_06_security_dpapi(self):
        """Verify Windows DPAPI credential encryption and decryption"""
        plain_token = "sp_dc_enterprise_secret_token_12345"
        encrypted = SecurityManager.encrypt_secret(plain_token)
        self.assertTrue(len(encrypted) > 0)
        decrypted = SecurityManager.decrypt_secret(encrypted)
        self.assertEqual(decrypted, plain_token)

    def test_07_web_endpoints(self):
        """Verify Flask REST endpoints"""
        client = app.test_client()
        # Status
        status_res = client.get("/api/status")
        self.assertEqual(status_res.status_code, 200)
        self.assertIn("disk_free_gb", status_res.get_json())

        # Readiness
        ready_res = client.get("/api/system/readiness")
        self.assertEqual(ready_res.status_code, 200)
        ready_data = ready_res.get_json()
        self.assertTrue(ready_data["ready"])
        self.assertTrue(ready_data["ffmpeg_installed"])
        self.assertTrue(ready_data["database_integrity"])

        # Setup Complete
        setup_res = client.post("/api/system/complete-setup", json={"downloads_path": "downloads"})
        self.assertEqual(setup_res.status_code, 200)
        self.assertTrue(setup_res.get_json()["success"])

        # Queue snapshot
        queue_res = client.get("/api/queue/snapshot")
        self.assertEqual(queue_res.status_code, 200)
        self.assertIn("counts", queue_res.get_json())

    def test_08_library_intelligence_and_storage(self):
        """Verify Library Intelligence health scoring and cleanup"""
        from src.library_intelligence import LibraryIntelligence
        intel = LibraryIntelligence.get_intelligence_overview()
        self.assertIn("health_score", intel)
        self.assertIn("total_tracks", intel)
        self.assertGreaterEqual(intel["health_score"], 0)

        cleanup_res = LibraryIntelligence.cleanup_temp_buffers()
        self.assertIn("deleted_files", cleanup_res)
        self.assertIn("reclaimed_mb", cleanup_res)

    def test_09_quality_profiles(self):
        """Verify quality profiles structure and defaults"""
        from src.quality_service import QUALITY_PROFILES
        self.assertIn("Archival", QUALITY_PROFILES)
        self.assertIn("Standard", QUALITY_PROFILES)
        self.assertEqual(QUALITY_PROFILES["Standard"]["format"], "mp3")
        self.assertEqual(QUALITY_PROFILES["Archival"]["format"], "flac")

    def test_10_scheduler_and_monitors(self):
        """Verify artist monitoring and release tracking"""
        from src.scheduler_service import SchedulerService
        artist_id = "test_art_001"
        SchedulerService.add_monitored_artist(artist_id, "Queen Test", auto_download=False)
        artists = SchedulerService.get_monitored_artists()
        found = any(a["id"] == artist_id for a in artists)
        self.assertTrue(found)
        SchedulerService.remove_monitored_artist(artist_id)

    def test_11_metadata_provenance(self):
        """Verify field-level metadata provenance persistence"""
        from src.release_comparison import ReleaseComparisonStudio
        track_id = "trk_provenance_001"
        ReleaseComparisonStudio.save_provenance(track_id, "title", "Bohemian Rhapsody", "spotify_api", 1.0)
        prov = ReleaseComparisonStudio.get_track_provenance(track_id)
        self.assertEqual(len(prov), 1)
        self.assertEqual(prov[0]["source"], "spotify_api")

    def test_12_plugin_hooks_and_archive(self):
        """Verify plugin hooks registration and download archive"""
        from src.plugin_engine import PluginEngine, DownloadArchive
        hook_id = "hook_test_01"
        PluginEngine.register_hook(hook_id, "Test Hook", "on_download_complete", "webhook", "http://127.0.0.1:9999/hook")
        hooks = PluginEngine.list_hooks()
        self.assertTrue(any(h["id"] == hook_id for h in hooks))
        PluginEngine.delete_hook(hook_id)

        # Download archive idempotent check
        import time
        res_id = f"spotify_track_archive_test_{time.time()}"
        self.assertFalse(DownloadArchive.is_archived(res_id))
        DownloadArchive.record_completed(res_id, "track", "Test Title", "Test Artist", "test_out.mp3")
        self.assertTrue(DownloadArchive.is_archived(res_id))

    def test_13_shortlink_parsing(self):
        """Verify shortlink parsing and redirect resolution"""
        from unittest.mock import patch, MagicMock
        raw_short = "https://spotify.link/AbCdEf123"

        # Without redirect resolution
        parsed = LinkParser.parse_single(raw_short, resolve_redirects=False)
        self.assertEqual(parsed.kind, "shortlink")
        self.assertEqual(parsed.item_id, "AbCdEf123")

        # With mocked redirect resolution
        mock_resp = MagicMock()
        mock_resp.url = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        with patch("src.http_client.head", return_value=mock_resp):
            resolved = LinkParser.resolve_shortlink(raw_short)
            self.assertEqual(resolved.kind, "track")
            self.assertEqual(resolved.item_id, "4cOdK2wGLETKBW3PvgPWqT")

    def test_14_compliant_audio_inprocess(self):
        """Verify CompliantAudioProvider in-process execution with mock yt_dlp"""
        from unittest.mock import patch, MagicMock
        from src.compliant_audio import CompliantAudioProvider

        item_id = "trk_test_mock_001"

        mock_ydl_instance = MagicMock()
        def fake_extract(url, download=True):
            self.assertEqual(url, f"https://www.youtube.com/watch?v={item_id}")
            temp_file = TEMP_DIR / f"provider_youtube_{os.getpid()}_{item_id}.opus"
            with open(temp_file, "wb") as f:
                f.write(b"OggS_MOCK_OPUS_AUDIO_STREAM_DATA")

        mock_ydl_instance.extract_info.side_effect = fake_extract
        mock_ydl_instance.__enter__.return_value = mock_ydl_instance

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl_instance):
            audio_bytes, _ = CompliantAudioProvider.fetch_audio_for_provider_item(
                provider="youtube", provider_item_id=item_id
            )
            self.assertEqual(audio_bytes, b"OggS_MOCK_OPUS_AUDIO_STREAM_DATA")

    def test_15_queue_batch_and_state(self):
        """Verify QueueManager batch enqueuing, pause, and state snapshot"""
        import time
        qm = QueueManager()
        track_a = {"id": f"q_t1_{time.time()}", "title": "Track 1", "artists": ["A"]}
        track_b = {"id": f"q_t2_{time.time()}", "title": "Track 2", "artists": ["B"]}

        added = qm.add_batch([track_a, track_b], collection_id="coll_test", collection_name="Batch Test")
        self.assertEqual(added, 2)

        snapshot = qm.get_snapshot()
        self.assertIn("counts", snapshot)
        self.assertGreaterEqual(snapshot["counts"]["total"], 2)

        qm.pause()
        self.assertTrue(qm.is_paused)
        qm.resume()
        self.assertFalse(qm.is_paused)

    def test_16_schema_migrations_and_indexes(self):
        """Verify schema migrations tracking, version discovery, and crash recovery"""
        from src.db import get_migration_version, recover_stale_jobs, get_db_connection
        v = get_migration_version()
        self.assertGreaterEqual(v, 3)

        conn = get_db_connection()
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_jobs_status'")
        self.assertIsNotNone(cur.fetchone())

        recovered = recover_stale_jobs()
        self.assertIsInstance(recovered, int)

    def test_17_path_traversal_and_sanitization(self):
        """Verify path traversal prevention and Windows reserved name sanitization"""
        from src.file_organizer import FileOrganizer
        # Test reserved name escaping
        sanitized_con = FileOrganizer.sanitize("CON.mp3")
        self.assertTrue(sanitized_con.startswith("_CON"))

        # Test invalid char cleanup
        cleaned = FileOrganizer.sanitize('AC/DC: Back in "Black"?*')
        self.assertNotIn("/", cleaned)
        self.assertNotIn(":", cleaned)
        self.assertNotIn("?", cleaned)
        self.assertNotIn("*", cleaned)

        # Test path containment
        base_dir = get_downloads_path()
        meta = {"title": "Test Track", "artists": ["Test Artist"]}
        target_path, should_skip = FileOrganizer.resolve_output_path(
            base_dir=base_dir,
            template="{artist}/{title}.{ext}",
            meta=meta,
            output_format="mp3"
        )
        self.assertTrue(str(target_path).startswith(str(base_dir.resolve())))

    def test_18_match_engine_variant_penalties(self):
        """Verify match engine audio variant detection and low-confidence rejection"""
        expected_studio = {
            "title": "Hotel California",
            "artists": ["Eagles"],
            "duration_ms": 391000
        }
        cand_live = MatchCandidate(
            candidate_id="cand_live_001",
            title="Hotel California (Live at The Forum)",
            artists=["Eagles"],
            duration_ms=420000
        )
        explanation = MatchEngine.explain_match(expected_studio, cand_live, min_threshold=0.75)
        self.assertFalse(explanation["is_acceptable"])
        self.assertIn("variant_penalty", explanation["breakdown"])
        self.assertGreater(explanation["breakdown"]["variant_penalty"], 0.0)

    def test_19_staging_service_preview_and_commit(self):
        """Verify dry-run staging preview and batch commitment"""
        from src.staging_service import StagingService
        raw_input = "Queen - Bohemian Rhapsody"
        plan = StagingService.create_staging_preview(raw_input)
        self.assertIn("batch_id", plan)
        self.assertIn("summary", plan)
        self.assertEqual(plan["total_items"], 1)
        self.assertEqual(plan["items"][0]["status"], "READY")

        qm = QueueManager()
        enqueued = StagingService.commit_staging_batch(plan["batch_id"], qm)
        self.assertEqual(enqueued, 1)

    def test_20_diagnostics_service_report(self):
        """Verify comprehensive diagnostics report generation without credential leakage"""
        from src.diagnostics_service import DiagnosticsService
        rep = DiagnosticsService.generate_report()
        self.assertEqual(rep["containment"]["zero_c_drive_writes"], True)
        self.assertEqual(rep["database"]["integrity_check"], "PASS")
        self.assertFalse(rep["security"]["passwords_exposed"])
        self.assertIn("Spotify Downloader Studio", rep["application"]["name"])

        md = DiagnosticsService.generate_markdown_report()
        self.assertIn("# Spotify Downloader Studio — System Diagnostic Report", md)


if __name__ == "__main__":
    unittest.main()
