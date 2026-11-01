"""Support bundle: contains what helps a bug report and nothing that must stay private."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import json
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from src import diagnostics_service as ds
from src.db import get_db_connection
from src.diagnostics_service import DiagnosticsService, scrub_text
from src.plugin_packs import PluginPacks
from src.web.server import app

WEBHOOK = "https://discord.com/api/webhooks/987654321/ULTRA-SECRET-WEBHOOK-TOKEN"
PROFILE = "C:\\Users\\Alice Example\\AppData\\Local\\thing"


class TestScrub(unittest.TestCase):
    def test_addresses_lose_their_path_unless_the_site_is_a_known_music_host(self):
        self.assertNotIn("ULTRA-SECRET", scrub_text(f"POST {WEBHOOK} failed"))
        self.assertIn("discord.com", scrub_text(f"POST {WEBHOOK} failed"))
        spotify = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"
        self.assertEqual(scrub_text(f"loading {spotify}"), f"loading {spotify}")
        self.assertIn("https://hooks.slack.com/…", scrub_text("https://hooks.slack.com/services/T0/B0/XYZ"))

    def test_windows_user_names_and_secrets_are_removed(self):
        self.assertNotIn("Alice", scrub_text(f"file at {PROFILE}"))
        self.assertIn("<user>", scrub_text(f"file at {PROFILE}"))
        self.assertNotIn("hunter2", scrub_text("login failed password=hunter2 for someone"))


class TestBundle(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.log = self.tmp / "logs" / "studio.log"
        self.log.parent.mkdir(parents=True)
        self.log.write_text(f"2026-11-01 INFO Library: scanning {PROFILE}\n2026-11-01 WARNING Hook failed: {WEBHOOK}\n"
                            "2026-11-01 INFO login password=hunter2\n2026-11-01 INFO ok https://open.spotify.com/track/abc\n", encoding="utf-8")
        (self.tmp / "logs" / "studio.log.1").write_text("older line\n", encoding="utf-8")
        for target, value in (("DIAGNOSTICS_DIR", self.tmp / "diagnostics"), ("LOG_FILE", self.log)):
            patcher = patch.object(ds, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.conn = get_db_connection()
        with self.conn:
            self.conn.execute("DELETE FROM plugin_packs")
            self.conn.execute("DELETE FROM jobs WHERE id = 'bundle_fail'")
            self.conn.execute("INSERT INTO jobs (id, track_id, title, artists, status, error, added_at, completed_at) VALUES (?,?,?,?,?,?,?,?)",
                              ("bundle_fail", "b1", "Bundle Song", "Someone", "FAILED", f"hook {WEBHOOK} returned 401 in {PROFILE}", time.time(), time.time()))
        self.addCleanup(self.clean)
        PluginPacks.configure("discord-notify", {"webhook_url": WEBHOOK}, enabled=True)
        self.client = app.test_client()

    def clean(self):
        with self.conn:
            self.conn.execute("DELETE FROM plugin_packs")
            self.conn.execute("DELETE FROM jobs WHERE id = 'bundle_fail'")

    def read(self, path):
        with zipfile.ZipFile(path) as z:
            return {name: z.read(name).decode("utf-8") for name in z.namelist()}

    def test_the_bundle_has_everything_useful_and_nothing_private(self):
        files = self.read(DiagnosticsService.create_support_bundle())
        for expected in ("README.txt", "report.json", "report.md", "settings.json", "plugins.json", "recent_failures.json", "logs/studio.log", "logs/studio.log.1"):
            self.assertIn(expected, files)
        everything = "\n".join(files.values())
        for private in ("ULTRA-SECRET-WEBHOOK-TOKEN", "hunter2", "Alice Example"):
            self.assertNotIn(private, everything, private)
        self.assertIn("discord.com", everything)                       # the host stays: it helps a bug report
        self.assertIn("https://open.spotify.com/track/abc", files["logs/studio.log"])
        self.assertIn("Bundle Song", files["recent_failures.json"])
        self.assertEqual(json.loads(files["report.json"])["application"]["name"], "Spotify Downloader Studio")
        plugins = json.loads(files["plugins.json"])
        self.assertTrue(next(p for p in plugins if p["id"] == "discord-notify")["enabled"])
        self.assertNotIn("webhook_url", files["plugins.json"] .replace("webhook_url\"", ""))   # no stored values, only status

    def test_only_the_newest_bundles_are_kept(self):
        made = []
        for _ in range(ds.KEEP_BUNDLES + 3):
            made.append(DiagnosticsService.create_support_bundle())
            time.sleep(0.02)
        left = sorted((self.tmp / "diagnostics").glob("studio-diagnostics-*.zip"))
        self.assertEqual(len(left), ds.KEEP_BUNDLES)
        self.assertIn(made[-1], left)
        self.assertNotIn(made[0], left)

    def test_route_creates_the_bundle_inside_the_data_folder_and_reports_it(self):
        res = self.client.post("/api/diagnostics/bundle").get_json()
        self.assertTrue(res["success"])
        self.assertTrue(Path(res["path"]).exists())
        self.assertGreater(res["size"], 500)
        self.assertEqual(Path(res["path"]).parent, self.tmp / "diagnostics")


if __name__ == "__main__":
    unittest.main()
