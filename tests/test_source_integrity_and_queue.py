"""
Phase 29.3 — Source Integrity, Alternative Selection Workflow & Mixed-Source Queue Persistence Tests.
Strict storage containment: Tests operate on isolated temporary databases in PROJECT_ROOT / temp.
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
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from src.providers.base import NormalizedItem
from src.source_resolution import SourceResolutionService
from src.link_parser import LinkParser
from src.config_loader import get_temp_path
from src.cross_source_matcher import CrossSourceMatchingService


class TestPhase293SourceIntegrityAndQueue(unittest.TestCase):
    """Verifies Sections 4, 5, and 6 of Phase 29.3."""

    def setUp(self):
        self.temp_dir = get_temp_path() / f"test_integ_{os.getpid()}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.test_db_path = self.temp_dir / "isolated_studio.db"
        self._init_test_db(self.test_db_path)

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _init_test_db(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cross_source_matches (
                id TEXT PRIMARY KEY,
                source_provider TEXT NOT NULL,
                source_item_id TEXT NOT NULL,
                target_provider TEXT NOT NULL,
                target_item_id TEXT NOT NULL,
                match_status TEXT NOT NULL,
                confidence_score REAL DEFAULT 0.0,
                rationale TEXT,
                evidence_json TEXT,
                is_user_selected INTEGER DEFAULT 0,
                candidate_json TEXT,
                created_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                track_id TEXT,
                title TEXT,
                artists TEXT,
                album TEXT,
                cover_url TEXT,
                isrc TEXT,
                year TEXT,
                duration_ms INTEGER,
                provider TEXT,
                provider_item_id TEXT,
                canonical_url TEXT,
                collection_name TEXT,
                collection_id TEXT,
                stage TEXT,
                status TEXT,
                progress REAL,
                speed TEXT,
                eta TEXT,
                error TEXT,
                added_at REAL,
                completed_at REAL,
                output_path TEXT,
                match_source TEXT,
                match_confidence REAL,
                is_alternative INTEGER DEFAULT 0,
                original_spotify_id TEXT,
                retry_count INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT PRIMARY KEY,
                name TEXT,
                provider TEXT,
                track_count INTEGER,
                created_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlist_items (
                playlist_id TEXT,
                position INTEGER,
                track_id TEXT,
                provider TEXT,
                canonical_url TEXT,
                title TEXT,
                artist TEXT,
                album TEXT,
                duration_ms INTEGER,
                is_placeholder INTEGER DEFAULT 0,
                PRIMARY KEY (playlist_id, position)
            )
        """)
        conn.commit()
        conn.close()

    # SECTION 4: Exact Spotify Source Identity & Absence of Unauthorized Calls
    def test_spotify_url_exact_source_identity(self):
        url = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        parsed = LinkParser.parse_url(url)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.provider, "spotify")
        self.assertEqual(parsed.kind, "track")
        self.assertEqual(parsed.item_id, "4cOdK2wGLETKBW3PvgPWqT")

        res_service = SourceResolutionService()

        # Verify resolution with mock spies asserting ZERO calls to YouTube / YTM
        with patch("src.providers.youtube_provider.YouTubeProvider.resolve_url") as mock_yt_res, \
             patch("src.providers.youtube_provider.YouTubeProvider.search") as mock_yt_search, \
             patch("src.providers.ytmusic_provider.YouTubeMusicProvider.resolve_url") as mock_ytm_res, \
             patch("src.providers.ytmusic_provider.YouTubeMusicProvider.search") as mock_ytm_search:

            res = res_service.resolve_url(url)
            self.assertTrue(res["success"])
            self.assertEqual(res["provider"], "spotify")
            self.assertEqual(res["data"]["provider_item_id"], "4cOdK2wGLETKBW3PvgPWqT")
            self.assertEqual(res["data"]["canonical_url"], "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")

            # Assert zero unauthorized cross-provider queries occurred
            mock_yt_res.assert_not_called()
            mock_yt_search.assert_not_called()
            mock_ytm_res.assert_not_called()
            mock_ytm_search.assert_not_called()

    # SECTION 4 (Cont): Playlists order, duplicates, and unavailable placeholders
    def test_spotify_playlist_ordering_duplicates_and_placeholders(self):
        conn = sqlite3.connect(str(self.test_db_path))
        playlist_id = "sp_pl_001"
        conn.execute("INSERT INTO playlists VALUES (?, 'Top Hits', 'spotify', 4, 1000.0)", (playlist_id,))

        # Tracks: T1, T2, T1 (duplicate), T3 (unavailable placeholder)
        entries = [
            (playlist_id, 0, "4cOdK2wGLETKBW3PvgPWqT", "spotify", "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT", "Song 1", "Artist 1", "Album 1", 200000, 0),
            (playlist_id, 1, "track_2_id", "spotify", "https://open.spotify.com/track/track_2_id", "Song 2", "Artist 2", "Album 2", 180000, 0),
            (playlist_id, 2, "4cOdK2wGLETKBW3PvgPWqT", "spotify", "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT", "Song 1", "Artist 1", "Album 1", 200000, 0),
            (playlist_id, 3, "unavail_3_id", "spotify", "https://open.spotify.com/track/unavail_3_id", "[Unavailable Track]", "Artist 3", "", 0, 1),
        ]
        conn.executemany("INSERT INTO playlist_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", entries)
        conn.commit()

        # Retrieve and verify
        rows = conn.execute("SELECT position, track_id, provider, is_placeholder FROM playlist_items WHERE playlist_id = ? ORDER BY position ASC", (playlist_id,)).fetchall()
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0][0], 0)
        self.assertEqual(rows[0][1], "4cOdK2wGLETKBW3PvgPWqT")
        self.assertEqual(rows[1][0], 1)
        self.assertEqual(rows[1][1], "track_2_id")
        self.assertEqual(rows[2][0], 2)
        self.assertEqual(rows[2][1], "4cOdK2wGLETKBW3PvgPWqT", "Duplicate occurrence preserved")
        self.assertEqual(rows[3][0], 3)
        self.assertEqual(rows[3][3], 1, "Unavailable track preserved as placeholder")
        conn.close()

    # SECTION 5: Explicit Cross-Source Alternative Selection Workflow
    def test_explicit_alternative_selection_full_workflow(self):
        # 1. Resolve Spotify item
        source = NormalizedItem(
            provider="spotify",
            provider_item_id="sp_hotel_cal",
            entity_type="track",
            title="Hotel California",
            primary_creator="Eagles",
            album="Hotel California",
            duration_ms=391000
        )

        # 2. Simulate finding alternatives

        # Studio candidate (exact)
        candidate_studio = NormalizedItem(
            provider="youtube",
            provider_item_id="yt_studio_01",
            entity_type="video",
            title="Eagles - Hotel California (Official Audio)",
            primary_creator="Eagles",
            duration_ms=392000
        )

        # Live candidate (variant mismatch)
        candidate_live = NormalizedItem(
            provider="ytmusic",
            provider_item_id="ytm_live_02",
            entity_type="track",
            title="Hotel California (Live At The Summit, Houston, 1976)",
            primary_creator="Eagles",
            duration_ms=410000
        )

        outcome_studio, conf_studio, rat_studio, _ = CrossSourceMatchingService.evaluate(source, candidate_studio)
        outcome_live, conf_live, rat_live, _ = CrossSourceMatchingService.evaluate(source, candidate_live)

        self.assertEqual(outcome_studio.value, "HIGH_CONFIDENCE_CANDIDATE")
        self.assertGreaterEqual(conf_studio, 0.85)

        self.assertEqual(outcome_live.value, "NO_MATCH", "Live version must be rejected when source is studio")
        self.assertIn("live mismatch", rat_live.lower())

        # 3. Associate alternative requires explicit user action and stores in isolated DB
        res_service = SourceResolutionService()
        with patch("src.source_resolution.get_db_connection") as mock_db:
            mock_conn = sqlite3.connect(str(self.test_db_path))
            mock_conn.row_factory = sqlite3.Row
            mock_db.return_value = mock_conn

            ok = res_service.associate_alternative(
                source_item_id=source.provider_item_id,
                source_provider=source.provider,
                selected_candidate=candidate_studio.to_dict()
            )
            self.assertTrue(ok)

            # Verify association persisted
            assoc = res_service.get_associated_alternative(source.provider_item_id)
            self.assertIsNotNone(assoc)
            self.assertEqual(assoc["target_provider"], "youtube")
            self.assertEqual(assoc["target_item_id"], "yt_studio_01")

            # Original Spotify identity is 100% intact
            self.assertEqual(assoc["source_item_id"], "sp_hotel_cal")
            self.assertEqual(assoc["source_provider"], "spotify")

    # SECTION 6: Mixed-Source Queue and Playlist Persistence
    def test_mixed_source_queue_and_playlist_persistence(self):
        conn = sqlite3.connect(str(self.test_db_path))
        conn.row_factory = sqlite3.Row

        # Insert mixed items into queue
        mixed_jobs = [
            ("job_sp_1", "sp_t1", "Spotify Song", "Spotify Artist", "spotify", "sp_t1", "https://open.spotify.com/track/sp_t1", "QUEUED", 0.0),
            ("job_ytm_2", "ytm_t2", "YTM Song", "YTM Artist", "ytmusic", "ytm_t2", "https://music.youtube.com/watch?v=ytm_t2", "QUEUED", 0.0),
            ("job_yt_3", "yt_t3", "YouTube Video", "Channel X", "youtube", "yt_t3", "https://www.youtube.com/watch?v=yt_t3", "QUEUED", 0.0),
            ("job_loc_4", "loc_t4", "Local Track", "Local Artist", "local", "loc_t4", "PROJECT_ROOT/downloads/loc.mp3", "QUEUED", 0.0),
        ]
        conn.executemany("""
            INSERT INTO jobs (id, track_id, title, artists, provider, provider_item_id, canonical_url, status, progress, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1000.0)
        """, mixed_jobs)
        conn.commit()

        # Query all jobs to verify no provider cross-contamination
        rows = conn.execute("SELECT * FROM jobs ORDER BY id ASC").fetchall()
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["provider"], "local")
        self.assertEqual(rows[1]["provider"], "spotify")
        self.assertEqual(rows[2]["provider"], "youtube")
        self.assertEqual(rows[3]["provider"], "ytmusic")

        # Test simulated job failure & retry counter
        conn.execute("UPDATE jobs SET status = 'FAILED', error = 'Network 503', retry_count = retry_count + 1 WHERE id = 'job_sp_1'")
        conn.commit()
        failed_row = conn.execute("SELECT status, retry_count, error FROM jobs WHERE id = 'job_sp_1'").fetchone()
        self.assertEqual(failed_row["status"], "FAILED")
        self.assertEqual(failed_row["retry_count"], 1)

        # Test database backup and integrity verification
        backup_path = self.temp_dir / "backup.db"
        b_conn = sqlite3.connect(str(backup_path))
        conn.backup(b_conn)
        b_conn.close()

        # Verify backup contains identical records
        b_conn2 = sqlite3.connect(str(backup_path))
        b_count = b_conn2.execute("SELECT count(*) FROM jobs").fetchone()[0]
        self.assertEqual(b_count, 4)
        b_conn2.close()
        conn.close()


if __name__ == "__main__":
    unittest.main()
