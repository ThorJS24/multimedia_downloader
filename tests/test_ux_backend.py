"""
Backend behaviour added with the UX overhaul: cached account probe, logout, ETag'd queue snapshot, reveal /
downloads-dir containment, the desktop folder-picker bridge and persisted window geometry.
"""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestAccountStatus(unittest.TestCase):
    def setUp(self):
        from src.web.server import app
        from src.web.routes import system
        self.client = app.test_client()
        self.system = system
        system._auth_state.update(checked_at=0.0, authenticated=False, account_type="unknown", username="")

    def test_status_does_not_open_a_spotify_session_on_every_poll(self):
        probe = {"checked_at": 0.0, "authenticated": True, "account_type": "premium", "username": "dj"}
        with patch.object(self.system, "_probe_auth", side_effect=lambda: dict(probe, checked_at=__import__("time").time())) as fake:
            for _ in range(4):
                data = self.client.get("/api/status").get_json()
            self.assertEqual(fake.call_count, 1)          # one network probe, then served from the cache
            self.assertTrue(data["authenticated"])
            self.assertEqual(data["account_type"], "premium")
            self.client.get("/api/status?refresh=1")
            self.assertEqual(fake.call_count, 2)          # ...unless explicitly refreshed

    def test_status_exposes_drive_for_the_ui(self):
        with patch.object(self.system, "_probe_auth", return_value={"checked_at": 9e12, "authenticated": False,
                                                                     "account_type": "unknown", "username": ""}):
            data = self.client.get("/api/status").get_json()
        self.assertIn("drive", data)
        self.assertFalse(data["authenticated"])

    def test_logout_forgets_the_session_and_updates_the_cache(self):
        from src.config_loader import get_credentials_path
        cred = get_credentials_path()
        cred.parent.mkdir(parents=True, exist_ok=True)
        cred.write_text('{"fake": true}', encoding="utf-8")
        self.system._set_auth_state(authenticated=True, account_type="premium", username="dj")
        res = self.client.post("/api/logout")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["success"])
        self.assertFalse(cred.exists())
        data = self.client.get("/api/status").get_json()
        self.assertFalse(data["authenticated"])
        self.assertEqual(data["username"], "")


class TestQueueSnapshotEtag(unittest.TestCase):
    def test_unchanged_snapshot_returns_304_without_a_body(self):
        from src.web.server import app
        client = app.test_client()
        first = client.get("/api/queue/snapshot")
        etag = first.headers.get("ETag")
        self.assertTrue(etag)
        again = client.get("/api/queue/snapshot", headers={"If-None-Match": etag})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.data, b"")


class TestDesktopBridgeRoutes(unittest.TestCase):
    def setUp(self):
        from src.web.server import app
        self.client = app.test_client()

    def test_folder_picker_is_a_client_error_in_a_plain_browser(self):
        res = self.client.post("/api/system/pick-folder", json={})
        self.assertEqual(res.status_code, 409)             # never 5xx: nothing crashed, the feature just isn't here
        self.assertTrue(res.get_json()["unsupported"])

    def test_folder_picker_returns_the_chosen_path_inside_the_window(self):
        from src.desktop_shell import shell_bridge
        window = MagicMock()
        with patch("src.desktop_shell.shell_bridge._window", window), \
                patch.object(type(shell_bridge), "pick_folder", return_value=r"D:\Music"):
            res = self.client.post("/api/system/pick-folder", json={"start": ""})
        self.assertEqual(res.get_json()["path"], r"D:\Music")

    def test_reveal_refuses_paths_outside_the_application_folders(self):
        res = self.client.post("/api/system/reveal", json={"path": r"C:\Windows\System32"})
        self.assertIn(res.status_code, (403, 404))
        self.assertFalse(res.get_json()["success"])
        self.assertEqual(self.client.post("/api/system/reveal", json={}).status_code, 400)

    def test_reveal_opens_a_real_file_in_downloads(self):
        from src.config_loader import get_downloads_path
        f = get_downloads_path() / "reveal_me.m3u"
        f.write_text("#EXTM3U\n", encoding="utf-8")
        self.addCleanup(lambda: f.unlink(missing_ok=True))
        with patch("src.web.routes.system.subprocess.Popen") as popen, patch("src.web.routes.system.os.startfile", create=True):
            res = self.client.post("/api/system/reveal", json={"path": str(f)})
        self.assertTrue(res.get_json()["success"])
        self.assertEqual(popen.call_args[0][0][:2], ["explorer", "/select,"])      # the file is selected, not just the folder opened

    def test_downloads_dir_must_stay_inside_the_application(self):
        from src.config_loader import get_config
        before = get_config().get("paths", {}).get("download_dir", "downloads")
        res = self.client.post("/api/settings/downloads-dir", json={"path": r"C:\Users\Public\Music"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(get_config().get("paths", {}).get("download_dir", "downloads"), before)   # rolled back
        self.assertEqual(self.client.post("/api/settings/downloads-dir", json={"path": ""}).status_code, 400)

    def test_relative_downloads_dir_is_accepted(self):
        from src.config_loader import get_config, save_config
        before = get_config().get("paths", {}).get("download_dir", "downloads")
        self.addCleanup(lambda: (lambda c: (c.setdefault("paths", {}).__setitem__("download_dir", before), save_config(c)))(get_config()))
        res = self.client.post("/api/settings/downloads-dir", json={"path": "my_music"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["path"].endswith("my_music"))


class TestQueueMove(unittest.TestCase):
    def setUp(self):
        from src.web.server import app
        from src.web.state import queue_manager
        self.client = app.test_client()
        self.qm = queue_manager
        self.qm.pause()
        self.addCleanup(self.qm.resume)
        with self.qm._lock:      # the queue is process-wide: start every test from a clean slate
            self.qm.jobs = [j for j in self.qm.jobs if not j.track_id.startswith("mv")]
        tracks =[{"id": f"mv{i}", "title": f"Move {i}", "artists": ["A"], "album": "B", "duration_ms": 1000, "cover_url": "", "isrc": ""} for i in range(4)]
        self.qm.add_batch(tracks, collection_name="MoveSet")
        self.ids = {j.track_id: j.id for j in self.qm.jobs if j.track_id.startswith("mv")}

    def order(self):
        return [j.track_id for j in self.qm.jobs if j.track_id.startswith("mv")]

    def test_move_before_another_job_and_to_the_end(self):
        res = self.client.post("/api/queue/reorder", json={"job_id": self.ids["mv3"], "before_id": self.ids["mv1"]}).get_json()
        self.assertTrue(res["moved"])
        self.assertEqual(self.order(), ["mv0", "mv3", "mv1", "mv2"])
        self.client.post("/api/queue/reorder", json={"job_id": self.ids["mv0"], "before_id": None})
        self.assertEqual(self.order(), ["mv3", "mv1", "mv2", "mv0"])

    def test_only_waiting_jobs_move_and_unknown_targets_go_last(self):
        done = next(j for j in self.qm.jobs if j.id == self.ids["mv2"])
        done.status = "COMPLETED"
        res = self.client.post("/api/queue/reorder", json={"job_id": self.ids["mv2"], "before_id": self.ids["mv0"]}).get_json()
        self.assertFalse(res["moved"])
        self.client.post("/api/queue/reorder", json={"job_id": self.ids["mv0"], "before_id": "no-such-job"})
        self.assertEqual(self.order()[-1], "mv0")

    def test_legacy_up_down_still_works(self):
        self.client.post("/api/queue/reorder", json={"job_id": self.ids["mv1"], "direction": "up"})
        self.assertEqual(self.order()[:2], ["mv1", "mv0"])


class TestLibrarySortKeys(unittest.TestCase):
    def test_year_genre_and_duration_are_sortable(self):
        from src.library_indexer import LibraryIndexer
        for key in ("year", "genre", "duration"):
            data = LibraryIndexer.get_library(sort_by=key, sort_dir="asc", limit=5)
            self.assertIn("files", data)


class TestM3UExportContract(unittest.TestCase):
    """The album/playlist pages send playlist_name and read track_count; the server must honour both."""

    def test_named_export_does_not_overwrite_the_library_playlist(self):
        from src.web.server import app
        from src.db import get_db_connection
        conn = get_db_connection()
        with conn:
            conn.execute("""INSERT OR REPLACE INTO library_files (path, filename, title, artist, album, duration_sec, format)
                            VALUES (?,?,?,?,?,?,?)""", (r"D:\x\a.mp3", "a.mp3", "A", "Art", "Alb", 10, "mp3"))
        client = app.test_client()
        res = client.post("/api/library/export-m3u", json={"paths": [r"D:\x\a.mp3"], "playlist_name": "My Album"}).get_json()
        self.assertTrue(res["success"])
        self.assertEqual(res["track_count"], 1)
        self.assertTrue(res["path"].endswith("My Album.m3u"))
        self.addCleanup(lambda: Path(res["path"]).unlink(missing_ok=True))


class TestWindowState(unittest.TestCase):
    def test_round_trip_and_sanitising(self):
        from src.desktop_shell import load_window_state, save_window_state, DEFAULT_WINDOW
        with tempfile.TemporaryDirectory(dir=str(_hermetic.TEST_DATA_DIR)) as d:
            p = Path(d) / "w.json"
            self.assertEqual(load_window_state(p), DEFAULT_WINDOW)                     # missing file
            save_window_state({"width": 1500, "height": 900, "x": 40, "y": 30}, p)
            self.assertEqual(load_window_state(p), {"width": 1500, "height": 900, "x": 40, "y": 30})
            p.write_text(json.dumps({"width": 10, "height": 99999, "x": -99999, "y": "no"}), encoding="utf-8")
            self.assertEqual(load_window_state(p), DEFAULT_WINDOW)                     # absurd values ignored
            p.write_text("not json", encoding="utf-8")
            self.assertEqual(load_window_state(p), DEFAULT_WINDOW)

    def test_wait_for_port_gives_up_when_the_server_thread_died(self):
        from src.desktop_shell import wait_for_port
        self.assertFalse(wait_for_port(1, timeout=5, alive=lambda: False))


class TestWindowClosing(unittest.TestCase):
    """Regression: SystemExit inside the WinForms FormClosing handler showed an 'Unhandled exception' dialog."""

    def test_closing_handler_cleans_up_without_raising(self):
        from src.desktop_shell import DesktopApp
        app_ = DesktopApp(port=5996, headless=True)
        with patch("src.maintenance_service.MaintenanceService.stop") as stop,                 patch("src.folder_watcher.FolderWatcherService.stop") as watcher,                 patch("src.desktop_shell.instance_lock") as lock,                 patch.object(DesktopApp, "_remember_window") as remember:
            app_._on_window_closing()          # must return normally: no SystemExit, no exception
        remember.assert_called_once()
        stop.assert_called_once()
        watcher.assert_called_once()
        lock.release.assert_called_once()
        self.assertTrue(app_.is_shutting_down)

    def test_closing_handler_swallows_cleanup_errors(self):
        from src.desktop_shell import DesktopApp
        app_ = DesktopApp(port=5995, headless=True)
        with patch.object(DesktopApp, "_remember_window", side_effect=RuntimeError("boom")):
            app_._on_window_closing()          # logged, never raised into the GUI thread

    def test_explicit_shutdown_still_exits_and_is_idempotent(self):
        from src.desktop_shell import DesktopApp
        app_ = DesktopApp(port=5994, headless=True)
        with patch("src.desktop_shell.instance_lock"), patch("src.maintenance_service.MaintenanceService.stop"):
            app_.shutdown(exit_process=False)
            app_.shutdown()                    # already shut down: returns instead of exiting twice
        app_2 = DesktopApp(port=5993, headless=True)
        with patch("src.desktop_shell.instance_lock"), patch("src.maintenance_service.MaintenanceService.stop"):
            with self.assertRaises(SystemExit):
                app_2.shutdown()               # the CLI/headless path still ends the process


class TestFrontendContracts(unittest.TestCase):
    """Static guards for bugs fixed in the UI scripts (cheap, no browser)."""

    @classmethod
    def setUpClass(cls):
        js = Path(__file__).resolve().parent.parent / "src" / "web" / "static" / "js"
        cls.src = {p.name: p.read_text(encoding="utf-8") for p in js.glob("*.js")}

    def test_no_native_confirm_or_prompt_dialogs_remain(self):
        import re
        for name, text in self.src.items():
            if name in ("handlers.js", "ui.js"):   # ui.js is the replacement layer (its comments mention the old calls)
                continue
            self.assertEqual(re.findall(r"(?<![\w.])(?:confirm|prompt)\(", text), [], f"native dialog in {name}")

    def test_wizard_completion_no_longer_reads_an_undefined_variable(self):
        self.assertNotIn("engine.toUpperCase", self.src["settings.js"])

    def test_monitor_download_uses_a_real_function(self):
        self.assertNotIn("parseLinks()", self.src["intelligence.js"])

    def test_organizer_success_is_not_reported_as_an_error(self):
        text = self.src["intelligence.js"]
        self.assertNotIn('Bulk Reorganization Complete', text)
        self.assertIn("rollbackBulkOrganizer(true)", text)   # one-click undo, not a batch-id prompt

    def test_m3u_exports_send_the_name_the_server_reads(self):
        text = self.src["pages.js"]
        self.assertIn("playlist_name: name", text)
        self.assertNotIn("exported_count", text)

    def test_collection_names_are_escaped_in_the_queue_filter(self):
        self.assertIn("escapeHtml(col)", self.src["queue.js"])

    def test_csv_export_neutralises_formula_cells(self):
        self.assertIn("csvCell", self.src["queue.js"])

    def test_tag_editor_resets_fields_between_files(self):
        text = self.src["player.js"]
        self.assertIn('year: "", genre: ""', text)
        self.assertIn("tagOriginal", text)


if __name__ == "__main__":
    unittest.main()
