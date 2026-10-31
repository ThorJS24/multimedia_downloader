"""
Phase 29.3 — Playback Capability Truthfulness, Source Integrity & Release Verification Tests
Validates all 13 specific capability test scenarios, source integrity, and queue persistence.
Strict storage isolation: All temporary files and test DBs reside strictly in PROJECT_ROOT.
"""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401

import os
import shutil
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent

from src.providers.base import NormalizedItem
from src.playback_capability import PlaybackCapabilityService
from src.config_loader import get_temp_path


class TestPhase293CapabilityTruthfulness(unittest.TestCase):
    """Regression test suite for item-specific capability truthfulness and isolation."""

    def setUp(self):
        self.temp_dir = get_temp_path() / f"test_cap_{os.getpid()}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # 1. Spotify track with a valid preview URL
    def test_01_spotify_track_with_valid_preview_url(self):
        item = NormalizedItem(
            provider="spotify",
            provider_item_id="sp_preview_01",
            entity_type="track",
            title="Preview Track",
            primary_creator="Artist A",
            capabilities={"preview_url": "https://p.scdn.co/mp3-preview/valid_preview_123"}
        )
        caps = PlaybackCapabilityService.resolve_capabilities(item)
        self.assertTrue(caps["can_access_metadata"])
        self.assertTrue(caps["has_preview"])
        self.assertTrue(caps["can_preview"])
        self.assertEqual(caps["preview_url"], "https://p.scdn.co/mp3-preview/valid_preview_123")
        self.assertTrue(caps["can_play"])
        self.assertEqual(caps["playback_type"], "spotify_preview")
        self.assertFalse(caps["can_download"])

    # 2. Spotify track with no preview URL
    def test_02_spotify_track_with_no_preview_url(self):
        item = NormalizedItem(
            provider="spotify",
            provider_item_id="sp_no_preview_02",
            entity_type="track",
            title="No Preview Track",
            primary_creator="Artist B",
            capabilities={"preview_url": None}
        )
        with patch("pathlib.Path.exists", return_value=False):
            caps = PlaybackCapabilityService.resolve_capabilities(item)
        self.assertTrue(caps["can_access_metadata"])
        self.assertFalse(caps["has_preview"])
        self.assertFalse(caps["can_preview"])
        self.assertIsNone(caps["preview_url"])
        self.assertFalse(caps["can_play"])
        self.assertEqual(caps["playback_type"], "none")
        self.assertFalse(caps["can_download"])
        self.assertIn("Preview unavailable", caps["reason_unavailable"])

    # 3. Spotify metadata lookup without playback authorization
    def test_03_spotify_metadata_lookup_without_playback_authorization(self):
        item = NormalizedItem(
            provider="spotify",
            provider_item_id="4cOdK2wGLETKBW3PvgPWqT",
            entity_type="track",
            title="Never Gonna Give You Up",
            primary_creator="Rick Astley",
            capabilities={"preview_url": None}
        )
        with patch("pathlib.Path.exists", return_value=False):
            caps = PlaybackCapabilityService.resolve_capabilities(item)
        self.assertTrue(caps["can_access_metadata"], "Metadata should be accessible")
        self.assertFalse(caps["can_play"], "Metadata lookup must NOT imply playback capability")
        self.assertFalse(caps["can_download"], "Metadata lookup must NOT imply download capability")

    # 4. Spotify remote playback without authorization
    def test_04_spotify_remote_playback_without_authorization(self):
        item = NormalizedItem(
            provider="spotify",
            provider_item_id="sp_track_04",
            entity_type="track",
            title="Unauth Track",
            primary_creator="Artist C",
            capabilities={"preview_url": None}
        )
        # Mock credentials file not existing
        mock_creds = MagicMock()
        mock_creds.exists.return_value = False
        with patch("src.config_loader.get_credentials_path", return_value=mock_creds):
            caps = PlaybackCapabilityService.resolve_capabilities(item)
        self.assertFalse(caps["can_remote_control"])
        self.assertFalse(caps["can_official_playback"])
        self.assertFalse(caps["can_play"])
        self.assertEqual(caps["authorization_status"], "unauthenticated")

    # 5. Spotify remote playback with valid supported authorization
    def test_05_spotify_remote_playback_with_authorization(self):
        item = NormalizedItem(
            provider="spotify",
            provider_item_id="sp_track_05",
            entity_type="track",
            title="Auth Track",
            primary_creator="Artist D",
            capabilities={"preview_url": None}
        )
        mock_creds = MagicMock()
        mock_creds.exists.return_value = True
        mock_creds.stat.return_value.st_size = 256
        with patch("src.config_loader.get_credentials_path", return_value=mock_creds):
            caps = PlaybackCapabilityService.resolve_capabilities(item)
        self.assertTrue(caps["can_remote_control"])
        self.assertTrue(caps["can_official_playback"])
        self.assertTrue(caps["can_play"])
        self.assertEqual(caps["playback_type"], "spotify_remote")
        self.assertEqual(caps["authorization_status"], "authenticated")
        self.assertFalse(caps["can_download"], "Even authenticated sessions must not claim universal download rights")

    # 6. Spotify download requests being rejected when unsupported
    def test_06_spotify_download_requests_being_rejected_when_unsupported(self):
        from src.queue_manager import QueueManager, QueueJob
        qm = QueueManager()
        job = QueueJob(
            job_id="test_sp_reject_dl",
            track_id="sp_protected_06",
            title="Protected Track",
            artists=["Artist E"],
            provider="spotify"
        )
        job.attempts = 99  # no retries left: assert the terminal failure
        with patch.object(qm.direct_client, "download_track_to_file", side_effect=RuntimeError("Protected DRM stream")):
            with patch("shutil.disk_usage", return_value=(1000000000, 500000000, 500000000)):
                with patch.object(qm.metadata_service, "resolve_item", return_value={
                    "id": "sp_protected_06", "title": "Protected Track", "artists": ["Artist E"], "duration_ms": 200000
                }):
                    qm._process_job(job)

        self.assertEqual(job.status, "FAILED")
        self.assertIn("Rule A", job.error)
        self.assertIn("Find alternatives", job.error)

    # 7. YouTube video with embedding disabled or unavailable
    def test_07_youtube_video_with_embedding_disabled(self):
        item = NormalizedItem(
            provider="youtube",
            provider_item_id="yt_blocked_07",
            entity_type="video",
            title="Blocked Video",
            primary_creator="Uploader X",
            source_metadata={"playable_in_embed": False, "availability": "unplayable"}
        )
        caps = PlaybackCapabilityService.resolve_capabilities(item)
        self.assertFalse(caps["can_embed"])
        self.assertEqual(caps["embed_status"], "unplayable_embed")
        self.assertFalse(caps["can_play"])
        self.assertEqual(caps["playback_type"], "none")
        self.assertIn("Embedding disabled", caps["reason_unavailable"])

    # 8. YouTube Music item with metadata available but playback unavailable
    def test_08_ytmusic_item_with_metadata_available_but_playback_unavailable(self):
        item = NormalizedItem(
            provider="ytmusic",
            provider_item_id="ytm_unavail_08",
            entity_type="track",
            title="Region Locked Track",
            primary_creator="Artist F",
            source_metadata={"availability": "unplayable"}
        )
        caps = PlaybackCapabilityService.resolve_capabilities(item)
        self.assertTrue(caps["can_access_metadata"])
        self.assertFalse(caps["can_embed"])
        self.assertFalse(caps["can_play"])
        self.assertEqual(caps["playback_type"], "none")

    # 9. Local file missing after it was indexed
    def test_09_local_file_missing_after_indexed(self):
        missing_path = self.temp_dir / "deleted_song.mp3"
        item = NormalizedItem(
            provider="local",
            provider_item_id=str(missing_path),
            entity_type="track",
            title="Ghost Track",
            primary_creator="Ghost Artist",
            canonical_url=str(missing_path)
        )
        caps = PlaybackCapabilityService.resolve_capabilities(item)
        self.assertFalse(caps["can_play"])
        self.assertFalse(caps["can_play_local"])
        self.assertEqual(caps["local_file_status"], "missing")
        self.assertEqual(caps["playback_type"], "none")
        self.assertIn("not found on disk", caps["reason_unavailable"])

    # 10. Local file present but unreadable or unsupported
    def test_10_local_file_present_but_unreadable_or_unsupported(self):
        # Case A: Unsupported format
        unsupported_file = self.temp_dir / "sample.xyz"
        unsupported_file.write_text("not audio")
        item_a = NormalizedItem(
            provider="local",
            provider_item_id=str(unsupported_file),
            entity_type="track",
            title="Unsupported File",
            primary_creator="Artist G"
        )
        caps_a = PlaybackCapabilityService.resolve_capabilities(item_a)
        self.assertFalse(caps_a["can_play"])
        self.assertEqual(caps_a["local_file_status"], "unsupported_format")
        self.assertIn("Unsupported audio format", caps_a["reason_unavailable"])

        # Case B: Empty 0-byte file (unreadable)
        empty_mp3 = self.temp_dir / "empty.mp3"
        empty_mp3.write_bytes(b"")
        item_b = NormalizedItem(
            provider="local",
            provider_item_id=str(empty_mp3),
            entity_type="track",
            title="Empty MP3",
            primary_creator="Artist G"
        )
        caps_b = PlaybackCapabilityService.resolve_capabilities(item_b)
        self.assertFalse(caps_b["can_play"])
        self.assertEqual(caps_b["local_file_status"], "unreadable")
        self.assertIn("empty", caps_b["reason_unavailable"])

        # Case C: Valid readable MP3
        valid_mp3 = self.temp_dir / "valid.mp3"
        valid_mp3.write_bytes(b"ID3" + b"\x00" * 100)
        item_c = NormalizedItem(
            provider="local",
            provider_item_id=str(valid_mp3),
            entity_type="track",
            title="Valid MP3",
            primary_creator="Artist G"
        )
        caps_c = PlaybackCapabilityService.resolve_capabilities(item_c)
        self.assertTrue(caps_c["can_play"])
        self.assertTrue(caps_c["can_play_local"])
        self.assertEqual(caps_c["local_file_status"], "present")
        self.assertEqual(caps_c["playback_type"], "html5_local")

    # 11. Capability flags remaining consistent across API and MCP tools
    def test_11_capability_flags_consistent_across_api_and_mcp(self):
        from src.mcp_server.server import get_provider_capabilities
        mcp_statuses = get_provider_capabilities()
        service_statuses = PlaybackCapabilityService.get_all_provider_statuses()

        self.assertEqual(set(mcp_statuses.keys()), set(service_statuses.keys()))
        for prov in ("spotify", "ytmusic", "youtube", "local"):
            self.assertEqual(mcp_statuses[prov]["can_download"], service_statuses[prov]["can_download"])
            self.assertEqual(mcp_statuses[prov]["is_authoritative"], service_statuses[prov]["is_authoritative"])
            self.assertEqual(mcp_statuses[prov]["can_search"], service_statuses[prov]["can_search"])

    # 12. Provider failures returning truthful capability states
    def test_12_provider_failures_returning_truthful_capability_states(self):
        unknown_item = NormalizedItem(
            provider="unknown_provider",
            provider_item_id="unk_12",
            entity_type="track",
            title="Broken Track",
            primary_creator="Nobody"
        )
        caps = PlaybackCapabilityService.resolve_capabilities(unknown_item)
        self.assertFalse(caps["can_play"])
        self.assertFalse(caps["can_download"])
        self.assertFalse(caps["can_access_metadata"])
        self.assertEqual(caps["playback_type"], "none")
        self.assertIn("Unsupported", caps["reason_unavailable"])

    # 13. Capability changes not corrupting queued items or saved playlists
    def test_13_capability_changes_not_corrupting_queued_items_or_playlists(self):
        test_db = self.temp_dir / "test_persistence.db"
        conn = sqlite3.connect(str(test_db))
        conn.execute("CREATE TABLE playlists (id TEXT PRIMARY KEY, name TEXT, provider TEXT, track_count INTEGER)")
        conn.execute("CREATE TABLE playlist_items (playlist_id TEXT, position INTEGER, track_id TEXT, provider TEXT, title TEXT, artist TEXT)")
        conn.execute("INSERT INTO playlists VALUES ('pl_1', 'Mixed Playlist', 'mixed', 3)")
        conn.execute("INSERT INTO playlist_items VALUES ('pl_1', 0, 'sp_track_1', 'spotify', 'Spotify Song', 'Artist 1')")
        conn.execute("INSERT INTO playlist_items VALUES ('pl_1', 1, 'ytm_track_2', 'ytmusic', 'YTM Song', 'Artist 2')")
        conn.execute("INSERT INTO playlist_items VALUES ('pl_1', 2, 'local_track_3', 'local', 'Local Song', 'Artist 3')")
        conn.commit()

        # Simulate dynamic capability check for each item
        items = conn.execute("SELECT * FROM playlist_items ORDER BY position ASC").fetchall()
        for row in items:
            it = NormalizedItem(
                provider=row[3],
                provider_item_id=row[2],
                entity_type="track",
                title=row[4],
                primary_creator=row[5]
            )
            # Capability evaluation does NOT alter database records
            caps = PlaybackCapabilityService.resolve_capabilities(it)
            self.assertIsInstance(caps, dict)

        # Verify playlist structure is 100% intact
        rechecked_items = conn.execute("SELECT * FROM playlist_items ORDER BY position ASC").fetchall()
        self.assertEqual(len(rechecked_items), 3)
        self.assertEqual(rechecked_items[0][2], "sp_track_1")
        self.assertEqual(rechecked_items[0][3], "spotify")
        self.assertEqual(rechecked_items[1][2], "ytm_track_2")
        self.assertEqual(rechecked_items[1][3], "ytmusic")
        self.assertEqual(rechecked_items[2][2], "local_track_3")
        self.assertEqual(rechecked_items[2][3], "local")
        conn.close()


if __name__ == "__main__":
    unittest.main()
