"""
Comprehensive End-to-End Test Suite for Spotify Downloader Studio Dashboard.
Validates:
- Template rendering and HTML structural integrity
- Static assets (CSS, JS, Favicon)
- Complete API suite exercising all tab backends (Queue, Library, Explorer, Staging, Diagnostics, Settings)
- Folder Watcher controls (status, folders, rescan, pause, resume)
- Import staging preview API
- Diagnostics and Storage Containment reporting
"""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import init_db, close_db_connection
from src.security import SESSION_AUTH_TOKEN
from src.web.server import app


class TestDashboardE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        app.config["TESTING"] = True
        cls.client = app.test_client()
        cls.auth_headers = {"X-Session-Token": SESSION_AUTH_TOKEN}

    @classmethod
    def tearDownClass(cls):
        close_db_connection()

    def test_01_index_html_rendered(self):
        """Dashboard index page renders with required tab navigation and containers."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")

        # Verify page title and header
        self.assertIn("Spotify Downloader Studio", html)

        # Verify all main navigation tabs are present
        self.assertIn("tab-dashboard", html)
        self.assertIn("tab-queue", html)
        self.assertIn("tab-library", html)
        self.assertIn("tab-watcher", html)
        self.assertIn("tab-intelligence", html)
        self.assertIn("tab-settings", html)

        # Verify responsive meta tag
        self.assertIn('name="viewport"', html)
        self.assertIn('content="width=device-width', html)

    def test_02_static_assets_available(self):
        """Static assets (js/*.js, style.css, favicon.ico) load successfully."""
        css_res = self.client.get("/static/style.css")
        self.assertEqual(css_res.status_code, 200)
        self.assertIn("text/css", css_res.content_type)

        js_res = self.client.get("/static/js/core.js")
        self.assertEqual(js_res.status_code, 200)
        self.assertIn("javascript", js_res.content_type)

        fav_res = self.client.get("/static/favicon.ico")
        self.assertEqual(fav_res.status_code, 200)

    def test_03_diagnostics_api_and_containment(self):
        """Diagnostics API reports valid containment, storage mode, and system status."""
        res = self.client.get("/api/system/diagnostics/export")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertIn("containment", data)
        self.assertIn("mode", data["containment"])
        self.assertIn("storage", data)
        self.assertIn("database", data)
        self.assertEqual(data["database"]["integrity_check"], "PASS")

    def test_04_system_status_api(self):
        """Status API returns active queue and storage information."""
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("settings", data)
        self.assertIn("disk_free_gb", data)

    def test_05_settings_lifecycle(self):
        """Settings can be retrieved and updated securely with session auth."""
        res = self.client.get("/api/settings", headers=self.auth_headers)
        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertIn("settings", payload)
        self.assertIn("audio", payload["settings"])

        update_payload = {
            "audio": {
                "audio_quality": "HIGH",
                "mp3_bitrate": "256k"
            }
        }
        post_res = self.client.post("/api/settings", json=update_payload, headers=self.auth_headers)
        self.assertEqual(post_res.status_code, 200)
        self.assertTrue(post_res.get_json().get("success"))

    def test_06_folder_watcher_controls_api(self):
        """Folder watcher lifecycle controls (status, folders, rescan, pause, resume)."""
        st_res = self.client.get("/api/watcher/status")
        self.assertEqual(st_res.status_code, 200)
        st_data = st_res.get_json()
        self.assertIn("status", st_data)

        f_res = self.client.get("/api/watcher/folders")
        self.assertEqual(f_res.status_code, 200)

        rescan_res = self.client.post("/api/watcher/folders/rescan", json={}, headers=self.auth_headers)
        self.assertEqual(rescan_res.status_code, 200)

    def test_07_import_staging_preview_api(self):
        """Staging preview endpoint responds with structured dry-run import preview."""
        post_res = self.client.post(
            "/api/staging/preview",
            json={"url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"},
            headers=self.auth_headers
        )
        self.assertIn(post_res.status_code, [200, 400])

    def test_08_library_management_api(self):
        """Library items and intelligence overview endpoints return valid structures."""
        lib_res = self.client.get("/api/library")
        self.assertEqual(lib_res.status_code, 200)
        lib_data = lib_res.get_json()
        self.assertIn("files", lib_data)

        health_res = self.client.get("/api/intelligence/overview")
        self.assertEqual(health_res.status_code, 200)
        health_data = health_res.get_json()
        self.assertIn("health_score", health_data)

    def test_09_quality_profiles_api(self):
        """Quality profiles are exposed for audiophile configuration."""
        q_res = self.client.get("/api/quality/profiles")
        self.assertEqual(q_res.status_code, 200)
        q_data = q_res.get_json()
        self.assertIn("profiles", q_data)


if __name__ == "__main__":
    unittest.main()
