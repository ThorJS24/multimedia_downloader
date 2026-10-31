"""Opt-in update flow: feed check, verified download, signature-gated launch, routes (no network)."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import hashlib
import time
import unittest
from unittest.mock import MagicMock, patch

from src import update_service
from src.maintenance_service import MaintenanceService
from src.settings_service import SettingsService
from src.update_service import UPDATES_DIR, UpdateService, parse_version
from src.version import __version__

PAYLOAD = b"MZ-fake-installer-bytes" * 1000
GOOD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
URL = "https://updates.example.test/files/SpotifyDownloader-v9.9.9-Setup.exe"


def feed(version="9.9.9", url=URL, sha=GOOD_SHA, **extra):
    return {"version": version, "url": url, "sha256": sha, "notes": "fixes", **extra}


def feed_response(payload):
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = payload
    return r


def download_response(data, chunk=4096):
    r = MagicMock()
    r.__enter__.return_value = r
    r.__exit__.return_value = False
    r.raise_for_status.return_value = None
    r.iter_content.side_effect = lambda chunk_size: (data[i:i + chunk] for i in range(0, len(data), chunk))
    return r


class Base(unittest.TestCase):
    def setUp(self):
        SettingsService.update({"maintenance": {"update_feed_url": "https://updates.example.test/feed.json",
                                                "update_require_signature": True}})
        UpdateService._last_result = None
        for f in UPDATES_DIR.glob("*") if UPDATES_DIR.exists() else []:
            f.unlink()

    def tearDown(self):
        SettingsService.update({"maintenance": {"update_feed_url": ""}})


class TestVersions(unittest.TestCase):
    def test_ordering(self):
        self.assertGreater(parse_version("2.1.0"), parse_version("2.0.9"))
        self.assertGreater(parse_version("v10.0"), parse_version("9.99.99"))
        self.assertEqual(parse_version("2"), (2, 0, 0, 0))
        self.assertEqual(parse_version("2.0.0"), parse_version("2.0.0.0"))

    def test_rejects_garbage(self):
        for bad in ("", "latest", "2.x", "2.0.0-beta", None, "1.2.3.4.5"):
            with self.assertRaises(ValueError):
                parse_version(bad)


class TestSettingsValidation(unittest.TestCase):
    def tearDown(self):
        SettingsService.update({"maintenance": {"update_feed_url": ""}})

    def test_feed_url_must_be_https_or_empty(self):
        SettingsService.update({"maintenance": {"update_feed_url": ""}})
        SettingsService.update({"maintenance": {"update_feed_url": "https://ok.example/u.json"}})
        for bad in ("http://insecure.example/u.json", "ftp://x/y", "file:///c:/x.json", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                SettingsService.update({"maintenance": {"update_feed_url": bad}})

    def test_off_by_default(self):
        self.assertEqual(SettingsService.get_all()["maintenance"]["update_feed_url"], "")
        self.assertTrue(SettingsService.get_all()["maintenance"]["update_require_signature"])


class TestCheck(Base):
    def test_disabled_without_a_feed_and_never_touches_the_network(self):
        SettingsService.update({"maintenance": {"update_feed_url": ""}})
        with patch("src.update_service.http_client.get") as get:
            res = UpdateService.check()
        self.assertEqual((res["enabled"], res["available"]), (False, False))
        get.assert_not_called()

    def test_newer_version_is_reported(self):
        with patch("src.update_service.http_client.get", return_value=feed_response(feed())):
            res = UpdateService.check()
        self.assertTrue(res["available"])
        self.assertEqual((res["latest"], res["current"], res["notes"]), ("9.9.9", __version__, "fixes"))

    def test_same_or_older_version_is_not_an_update(self):
        for v in (__version__, "0.1.0"):
            with patch("src.update_service.http_client.get", return_value=feed_response(feed(version=v))):
                self.assertFalse(UpdateService.check()["available"])

    def test_malformed_feeds_are_errors_not_crashes(self):
        bad_feeds = [
            feed(url="http://insecure.example/x.exe"),       # not https
            feed(sha="abc"),                                  # not a sha256
            {"version": "9.9.9"},                             # missing fields
            feed(version="soon"),                             # unparseable version
        ]
        for payload in bad_feeds:
            with patch("src.update_service.http_client.get", return_value=feed_response(payload)):
                res = UpdateService.check()
            self.assertIn("error", res, payload)
            self.assertFalse(res["available"])

    def test_network_failure_is_an_error_not_a_crash(self):
        with patch("src.update_service.http_client.get", side_effect=OSError("offline")):
            res = UpdateService.check()
        self.assertIn("offline", res["error"])


class TestDownload(Base):
    def available(self, **kw):
        info = {"available": True, "latest": "9.9.9", "url": URL, "sha256": GOOD_SHA}
        info.update(kw)
        return info

    def test_verified_download_lands_in_the_updates_folder(self):
        with patch("src.update_service.http_client.get", return_value=download_response(PAYLOAD)):
            path = UpdateService.download(self.available())
        self.assertEqual(path.parent, UPDATES_DIR)
        self.assertEqual(path.name, "SpotifyDownloader-v9.9.9-Setup.exe")
        self.assertEqual(path.read_bytes(), PAYLOAD)
        self.assertFalse(list(UPDATES_DIR.glob("*.part")))

    def test_hash_mismatch_discards_the_file(self):
        with patch("src.update_service.http_client.get", return_value=download_response(PAYLOAD + b"tampered")):
            with self.assertRaises(ValueError) as ctx:
                UpdateService.download(self.available())
        self.assertIn("SHA-256", str(ctx.exception))
        self.assertEqual(list(UPDATES_DIR.glob("*")), [])

    def test_refuses_without_an_available_update_or_with_http(self):
        with self.assertRaises(ValueError):
            UpdateService.download({"available": False})
        with self.assertRaises(ValueError):
            UpdateService.download(self.available(url="http://insecure.example/a.exe"))

    def test_only_exe_installers_and_sanitised_names(self):
        with self.assertRaises(ValueError):
            UpdateService._target_path("https://x.test/payload.bat")
        name = UpdateService._target_path("https://x.test/a/..%2F..%2Fevil name!.exe").name
        self.assertRegex(name, r"^[A-Za-z0-9._-]+\.exe$")
        self.assertEqual(UpdateService._target_path("https://x.test/a/b/Setup.exe").parent, UPDATES_DIR)

    def test_size_limit(self):
        with patch.object(update_service, "MAX_DOWNLOAD_BYTES", 1000), \
                patch("src.update_service.http_client.get", return_value=download_response(b"x" * 5000, chunk=600)):
            with self.assertRaises(ValueError) as ctx:
                UpdateService.download(self.available(sha=hashlib.sha256(b"x" * 5000).hexdigest()))
        self.assertIn("safety limit", str(ctx.exception))
        self.assertEqual(list(UPDATES_DIR.glob("*")), [])


class TestLaunch(Base):
    def make_file(self, name="Setup.exe"):
        UPDATES_DIR.mkdir(parents=True, exist_ok=True)
        f = UPDATES_DIR / name
        f.write_bytes(b"MZ")
        return f

    def test_unsigned_installer_is_refused_by_default(self):
        f = self.make_file()
        with patch.object(UpdateService, "signature_status", return_value="NotSigned"), \
                patch("src.update_service.os.startfile", create=True) as start:
            with self.assertRaises(PermissionError):
                UpdateService.launch_installer(f)
        start.assert_not_called()

    def test_validly_signed_installer_starts(self):
        f = self.make_file()
        with patch.object(UpdateService, "signature_status", return_value="Valid"), \
                patch("src.update_service.os.startfile", create=True) as start:
            UpdateService.launch_installer(f)
        start.assert_called_once_with(str(f.resolve()))

    def test_signature_requirement_can_be_switched_off_explicitly(self):
        SettingsService.update({"maintenance": {"update_require_signature": False}})
        f = self.make_file()
        with patch.object(UpdateService, "signature_status") as status, \
                patch("src.update_service.os.startfile", create=True) as start:
            UpdateService.launch_installer(f)
        status.assert_not_called()
        start.assert_called_once()

    def test_only_files_inside_the_updates_folder_can_run(self):
        outside = UPDATES_DIR.parent / "outside.exe"
        outside.write_bytes(b"MZ")
        try:
            with patch("src.update_service.os.startfile", create=True) as start:
                with self.assertRaises(ValueError):
                    UpdateService.launch_installer(outside)
                with self.assertRaises(ValueError):
                    UpdateService.launch_installer(UPDATES_DIR / "missing.exe")
                with self.assertRaises(ValueError):
                    UpdateService.launch_installer(UPDATES_DIR / ".." / "outside.exe")
            start.assert_not_called()
        finally:
            outside.unlink()


class TestRoutes(Base):
    @classmethod
    def setUpClass(cls):
        from src.web.server import app
        cls.client = app.test_client()

    def test_check_route(self):
        with patch("src.update_service.http_client.get", return_value=feed_response(feed())):
            body = self.client.get("/api/update/check").get_json()
        self.assertTrue(body["available"])

    def test_download_without_a_pending_update_is_a_400(self):
        res = self.client.post("/api/update/download")
        self.assertEqual(res.status_code, 400)

    def test_download_then_launch_gate(self):
        with patch("src.update_service.http_client.get", side_effect=[feed_response(feed()), download_response(PAYLOAD)]):
            self.client.get("/api/update/check")
            res = self.client.post("/api/update/download").get_json()
        self.assertTrue(res["success"])
        with patch.object(UpdateService, "signature_status", return_value="NotSigned"):
            denied = self.client.post("/api/update/launch", json={"file": res["file"]})
        self.assertEqual(denied.status_code, 403)
        with patch.object(UpdateService, "signature_status", return_value="Valid"), \
                patch("src.update_service.os.startfile", create=True):
            ok = self.client.post("/api/update/launch", json={"file": res["file"]})
        self.assertEqual(ok.status_code, 200)

    def test_launch_validation_and_traversal(self):
        self.assertEqual(self.client.post("/api/update/launch", json={}).status_code, 400)
        res = self.client.post("/api/update/launch", json={"file": "../../../Windows/System32/calc.exe"})
        self.assertEqual(res.status_code, 400)       # only the bare file name is used, and it does not exist


class TestScheduledCheck(Base):
    def test_daily_check_runs_only_when_due_and_configured(self):
        with patch("src.update_service.http_client.get", return_value=feed_response(feed())) as get:
            first = MaintenanceService.check_updates_if_due()
            again = MaintenanceService.check_updates_if_due()                       # checked a moment ago
            later = MaintenanceService.check_updates_if_due(now=time.time() + 90000)  # > 24 h later
        self.assertTrue(first["available"])
        self.assertIsNone(again)
        self.assertIsNotNone(later)
        self.assertEqual(get.call_count, 2)

    def test_nothing_happens_when_unconfigured(self):
        SettingsService.update({"maintenance": {"update_feed_url": ""}})
        with patch("src.update_service.http_client.get") as get:
            self.assertIsNone(MaintenanceService.check_updates_if_due())
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
