try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import os
import shutil
import sys
import unittest
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config_loader import (
    APP_DIR,
    get_app_dir,
    get_data_dir,
    is_portable_mode
)
from src.db import get_db_path


class TestStoragePolicy(unittest.TestCase):
    def setUp(self):
        self.orig_env = os.environ.copy()
        self.orig_argv = list(sys.argv)
        os.environ.pop("SPOTIFY_STUDIO_DATA_DIR", None)  # harness override would mask the policy under test
        self.test_dir = _hermetic.TEST_DATA_DIR / f"test_storage_{os.getpid()}_{id(self)}"
        self.test_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)
        sys.argv = list(self.orig_argv)
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_app_dir_resolution(self):
        """App directory correctly resolves to project/binary root."""
        app_dir = get_app_dir()
        self.assertTrue(app_dir.exists())
        self.assertEqual(app_dir, APP_DIR)

    def test_default_dev_mode_is_portable(self):
        """In development / unfrozen mode, default mode is portable within project workspace."""
        os.environ.pop("SPOTIFY_STUDIO_PORTABLE", None)
        os.environ.pop("SPOTIFY_STUDIO_MODE", None)
        sys.argv = [sys.argv[0]]
        self.assertTrue(is_portable_mode())
        self.assertEqual(get_data_dir(), APP_DIR)

    def test_explicit_portable_env(self):
        """SPOTIFY_STUDIO_PORTABLE=1 forces portable mode."""
        os.environ["SPOTIFY_STUDIO_PORTABLE"] = "1"
        self.assertTrue(is_portable_mode())
        self.assertEqual(get_data_dir(), APP_DIR)

        os.environ["SPOTIFY_STUDIO_PORTABLE"] = "0"
        self.assertFalse(is_portable_mode())

    def test_mode_installed_env(self):
        """SPOTIFY_STUDIO_MODE=installed routes to %LOCALAPPDATA%."""
        dummy_localappdata = str(self.test_dir / "LocalApp")
        os.environ["LOCALAPPDATA"] = dummy_localappdata
        os.environ["SPOTIFY_STUDIO_MODE"] = "installed"
        os.environ.pop("SPOTIFY_STUDIO_PORTABLE", None)

        self.assertFalse(is_portable_mode())
        expected_data_dir = Path(dummy_localappdata) / "SpotifyDownloaderStudio"
        self.assertEqual(get_data_dir(), expected_data_dir)

    def test_custom_data_dir_env(self):
        """SPOTIFY_STUDIO_DATA_DIR overrides data directory completely."""
        custom_target = self.test_dir / "custom_data"
        custom_target.mkdir(parents=True, exist_ok=True)
        os.environ["SPOTIFY_STUDIO_DATA_DIR"] = str(custom_target)

        self.assertEqual(get_data_dir(), custom_target.resolve())
        self.assertEqual(get_db_path(), custom_target.resolve() / "data" / "studio.db")

    def test_cli_flags_override(self):
        """CLI arguments --portable and --data-dir correctly dictate storage."""
        custom_cli = self.test_dir / "cli_data"
        sys.argv = [sys.argv[0], "--data-dir", str(custom_cli)]
        self.assertEqual(get_data_dir(), custom_cli.resolve())

        sys.argv = [sys.argv[0], "--portable"]
        self.assertTrue(is_portable_mode())

    def test_portable_dat_marker_file(self):
        """Presence of portable.dat in APP_DIR indicates portable mode."""
        # Simulated frozen environment
        try:
            sys.frozen = True
            marker = APP_DIR / "portable.dat"
            marker.write_text("portable_mode_test", encoding="utf-8")
            os.environ.pop("SPOTIFY_STUDIO_PORTABLE", None)
            os.environ.pop("SPOTIFY_STUDIO_MODE", None)

            self.assertTrue(is_portable_mode())
        finally:
            marker = APP_DIR / "portable.dat"
            if marker.exists():
                marker.unlink(missing_ok=True)
            if hasattr(sys, "frozen"):
                delattr(sys, "frozen")


if __name__ == "__main__":
    unittest.main()
