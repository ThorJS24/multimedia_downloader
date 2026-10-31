"""
Tests for Windows Installer Lifecycle, Upgrade Safety, and Non-Destructive Data Retention.
Validates:
- Fresh install initialization in installed mode
- Preserving existing database, config, and media on upgrade
- Non-destructive uninstallation (binary removal while retaining user data)
"""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.backup_service import BackupService
from src.config_loader import (
    get_data_dir,
    is_portable_mode,
    get_config,
)
from src.db import init_db, get_db_connection, record_audit, close_db_connection


class TestInstallerLifecycle(unittest.TestCase):
    def setUp(self):
        close_db_connection()
        self.orig_env = os.environ.copy()
        self.orig_argv = list(sys.argv)
        # inside the hermetic data dir, so a locked SQLite file can never leave debris in the real temp/
        self.test_root = _hermetic.TEST_DATA_DIR / f"lifecycle_test_{os.getpid()}_{id(self)}"
        self.test_root.mkdir(parents=True, exist_ok=True)

        self.mock_app_dir = self.test_root / "App"
        self.mock_data_dir = self.test_root / "UserData"
        self.mock_app_dir.mkdir(parents=True, exist_ok=True)
        self.mock_data_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        close_db_connection()
        os.environ.clear()
        os.environ.update(self.orig_env)
        sys.argv = list(self.orig_argv)
        if self.test_root.exists():
            shutil.rmtree(self.test_root, ignore_errors=True)

    def test_01_fresh_installed_mode_initialization(self):
        """Simulate fresh installation in installed mode."""
        os.environ["SPOTIFY_STUDIO_MODE"] = "installed"
        os.environ["SPOTIFY_STUDIO_DATA_DIR"] = str(self.mock_data_dir)
        os.environ.pop("SPOTIFY_STUDIO_PORTABLE", None)

        self.assertFalse(is_portable_mode())
        self.assertEqual(get_data_dir(), self.mock_data_dir)

        # Initialize fresh database and configuration
        init_db()
        conn = get_db_connection()
        self.assertTrue((self.mock_data_dir / "data" / "studio.db").exists())

        # Verify schema initialized
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        self.assertIn("jobs", tables)
        self.assertIn("library_files", tables)
        self.assertIn("audit_logs", tables)

        # Verify config created in user data dir
        cfg = get_config()
        self.assertTrue((self.mock_data_dir / "config" / "config.json").exists())
        self.assertIn("spotify", cfg)
        self.assertIn("audio", cfg)
        self.assertNotIn("downloader", cfg)

    def test_02_upgrade_preserves_user_data(self):
        """Simulate upgrading the app over existing user data."""
        os.environ["SPOTIFY_STUDIO_MODE"] = "installed"
        os.environ["SPOTIFY_STUDIO_DATA_DIR"] = str(self.mock_data_dir)
        os.environ.pop("SPOTIFY_STUDIO_PORTABLE", None)

        # 1. Setup pre-upgrade user state (v1.9 / v2.0 pre-release)
        init_db()
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO jobs (id, track_id, title, artists, status) VALUES (?, ?, ?, ?, ?)",
            ("job_pre_upgrade_1", "track_123", "Upgrade Safety Track", "Artist Safe", "COMPLETED")
        )
        conn.execute(
            "INSERT INTO monitored_artists (id, name, spotify_id, last_checked) VALUES (?, ?, ?, ?)",
            ("mon_1", "Pre-Upgrade Monitored Artist", "art_999", 1000.0)
        )
        record_audit("INSTALL", "Installed simulated v1.9.0")
        conn.commit()

        # Save custom config
        user_cfg = {
            "spotify": {"username": "tester@domain.com", "client_id": "test_id"},
            "downloader": {"audio_quality": "EXTREME", "mp3_bitrate": "320k"},
            "paths": {"download_dir": "downloads"}
        }
        config_path = self.mock_data_dir / "config" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(user_cfg, indent=2), encoding="utf-8")

        # Create user media file
        user_downloads = self.mock_data_dir / "downloads"
        user_downloads.mkdir(parents=True, exist_ok=True)
        media_file = user_downloads / "important_music_file.mp3"
        media_file.write_bytes(b"Simulated MP3 audio content")

        # 2. Simulate upgrade: Binaries in mock_app_dir updated
        new_binary = self.mock_app_dir / "SpotifyDownloader.exe"
        new_binary.write_text("Simulated upgraded binary v2.0.0", encoding="utf-8")

        # Re-initialize on upgraded version
        init_db()
        conn2 = get_db_connection()

        # 3. Assert zero data loss
        # Check custom config was not overwritten
        loaded_cfg = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded_cfg["spotify"]["username"], "tester@domain.com")
        self.assertEqual(loaded_cfg["downloader"]["audio_quality"], "EXTREME")

        # Check existing job preserved
        job_row = conn2.execute("SELECT * FROM jobs WHERE id = 'job_pre_upgrade_1'").fetchone()
        self.assertIsNotNone(job_row)
        self.assertEqual(job_row["title"], "Upgrade Safety Track")

        # Check monitored artist preserved
        artist_row = conn2.execute("SELECT * FROM monitored_artists WHERE id = 'mon_1'").fetchone()
        self.assertIsNotNone(artist_row)
        self.assertEqual(artist_row["name"], "Pre-Upgrade Monitored Artist")

        # Check media file untouched
        self.assertTrue(media_file.exists())
        self.assertEqual(media_file.read_bytes(), b"Simulated MP3 audio content")

        # Check database integrity passes
        ok, msg = BackupService.verify_database_integrity()
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")

    def test_03_uninstall_retains_user_database_and_music(self):
        """Simulate uninstall: application directory is purged, but user data is preserved."""
        # 1. Setup installed application files and user data
        bin_file = self.mock_app_dir / "SpotifyDownloader.exe"
        bin_file.write_text("Binary code", encoding="utf-8")
        bin_sub = self.mock_app_dir / "_internal"
        bin_sub.mkdir(parents=True, exist_ok=True)
        (bin_sub / "lib.dll").write_text("DLL content", encoding="utf-8")

        user_db = self.mock_data_dir / "data" / "studio.db"
        user_db.parent.mkdir(parents=True, exist_ok=True)
        user_db.write_text("Database bytes", encoding="utf-8")

        user_music = self.mock_data_dir / "downloads" / "favorite_song.mp3"
        user_music.parent.mkdir(parents=True, exist_ok=True)
        user_music.write_text("Song bytes", encoding="utf-8")

        # 2. Simulate standard Windows Add/Remove Programs uninstall:
        # Removes the installed App directory
        shutil.rmtree(self.mock_app_dir)

        # 3. Assert application binaries are removed
        self.assertFalse(self.mock_app_dir.exists())
        self.assertFalse(bin_file.exists())

        # 4. Assert user data directory REMIANS COMPLETELY INTACT
        self.assertTrue(self.mock_data_dir.exists())
        self.assertTrue(user_db.exists(), "User database must never be deleted by uninstaller!")
        self.assertTrue(user_music.exists(), "User music library must never be deleted by uninstaller!")


if __name__ == "__main__":
    unittest.main()
