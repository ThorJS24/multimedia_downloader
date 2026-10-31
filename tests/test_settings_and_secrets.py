"""Typed settings: validation, migration, secret encryption/masking, export/import."""
import json
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import unittest

from src.config_loader import get_config, get_config_path, save_config
from src.settings_service import SECRET_MASK, SECRET_PREFIX, SettingsService, defaults


class TestSettingsService(unittest.TestCase):
    def setUp(self):
        save_config({})

    def test_defaults_fill_every_schema_key(self):
        s = SettingsService.get_all()
        self.assertEqual(s["performance"]["max_workers"], 1)
        self.assertEqual(s["maintenance"]["log_retention_days"], 90)
        self.assertEqual(s["audio"]["output_format"], "mp3")
        for section, values in defaults().items():
            for key in values:
                self.assertIn(key, s[section])

    def test_update_validates_and_clamps(self):
        out = SettingsService.update({"performance": {"max_workers": 99, "retry_limit": "5"}})
        self.assertEqual(out["performance"]["max_workers"], 4)       # clamped to schema max
        self.assertEqual(out["performance"]["retry_limit"], 5)       # coerced from string
        with self.assertRaises(ValueError):
            SettingsService.update({"audio": {"output_format": "exe"}})
        with self.assertRaises(ValueError):
            SettingsService.update({"audio": {"mp3_bitrate": "fast"}})
        with self.assertRaises(ValueError):
            SettingsService.update({"naming": {"template": "no-placeholders"}})

    def test_unknown_sections_and_keys_are_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            SettingsService.update({"nope": {"x": 1}, "audio": {"language": "en"}})
        self.assertIn("unknown section 'nope'", str(ctx.exception))
        self.assertIn("unknown setting 'audio.language'", str(ctx.exception))

    def test_delay_range_must_be_ordered(self):
        with self.assertRaises(ValueError):
            SettingsService.update({"performance": {"rate_limit_min_delay": 10, "rate_limit_max_delay": 2}})

    def test_failed_update_changes_nothing(self):
        SettingsService.update({"audio": {"output_format": "flac"}})
        with self.assertRaises(ValueError):
            SettingsService.update({"audio": {"output_format": "ogg"}, "performance": {"max_workers": "abc"}})
        self.assertEqual(SettingsService.get_all()["audio"]["output_format"], "flac")

    def test_secrets_are_encrypted_at_rest_and_masked(self):
        SettingsService.update({"spotify": {"client_id": "cid", "client_secret": "s3cr3t-value"}})
        raw_text = get_config_path().read_text(encoding="utf-8")
        self.assertNotIn("s3cr3t-value", raw_text)
        self.assertTrue(get_config()["spotify"]["client_secret"].startswith(SECRET_PREFIX))

        masked = SettingsService.get_all()["spotify"]
        self.assertEqual(masked["client_secret"], SECRET_MASK)
        self.assertEqual(masked["client_id"], "cid")                  # non-secret stays readable
        self.assertEqual(SettingsService.get_secret("spotify", "client_secret"), "s3cr3t-value")

    def test_echoing_the_mask_or_blank_keeps_the_secret(self):
        SettingsService.update({"spotify": {"client_secret": "keep-me"}})
        SettingsService.update({"spotify": {"client_secret": SECRET_MASK}})
        SettingsService.update({"spotify": {"client_secret": ""}})
        self.assertEqual(SettingsService.get_secret("spotify", "client_secret"), "keep-me")

    def test_legacy_plaintext_secret_is_encrypted_on_read(self):
        save_config({"spotify": {"client_secret": "legacy-plain", "username": "u"}})
        SettingsService.get_all()
        self.assertNotIn("legacy-plain", get_config_path().read_text(encoding="utf-8"))
        self.assertEqual(SettingsService.get_secret("spotify", "client_secret"), "legacy-plain")

    def test_legacy_downloader_section_and_dead_keys_are_migrated(self):
        save_config({
            "downloader": {"audio_quality": "HIGH", "mp3_bitrate": "192k", "rate_limit_min_delay": 1.5, "retry_count": 5},
            "matching": {"duration_tolerance_sec": 4.0},
            "general": {"language": "en", "first_run_completed": True},
            "paths": {"download_dir": "music"},
        })
        s = SettingsService.get_all()
        self.assertEqual(s["audio"]["audio_quality"], "HIGH")
        self.assertEqual(s["audio"]["mp3_bitrate"], "192k")
        self.assertEqual(s["performance"]["retry_limit"], 5)
        self.assertTrue(s["general"]["first_run_completed"])
        raw = get_config()
        self.assertNotIn("downloader", raw)
        self.assertNotIn("matching", raw)
        self.assertNotIn("language", raw["general"])
        self.assertEqual(raw["paths"]["download_dir"], "music")       # unrelated sections survive

    def test_reset_keeps_credentials_and_unrelated_sections(self):
        SettingsService.update({"audio": {"output_format": "flac"}, "spotify": {"client_secret": "abc", "client_id": "id1"}})
        cfg = get_config()
        cfg["paths"] = {"download_dir": "music"}
        save_config(cfg)
        out = SettingsService.reset_defaults()
        self.assertEqual(out["audio"]["output_format"], "mp3")
        self.assertEqual(out["spotify"]["client_id"], "id1")
        self.assertEqual(SettingsService.get_secret("spotify", "client_secret"), "abc")
        self.assertEqual(get_config()["paths"]["download_dir"], "music")

    def test_export_has_no_secrets_or_username(self):
        SettingsService.update({"spotify": {"username": "me@example.com", "client_secret": "topsecret"}})
        exported = json.dumps(SettingsService.export_sanitized())
        self.assertNotIn("topsecret", exported)
        self.assertNotIn("me@example.com", exported)
        self.assertNotIn(SECRET_MASK, exported)

    def test_corrupt_config_is_preserved_not_discarded(self):
        path = get_config_path()
        path.write_text("{ this is not json", encoding="utf-8")
        self.assertEqual(get_config(), {})
        self.assertTrue(path.with_name("config.json.corrupt").exists())
        self.assertEqual(SettingsService.get_all()["audio"]["output_format"], "mp3")


class TestSettingsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.web.server import app
        cls.client = app.test_client()

    def setUp(self):
        save_config({})

    def test_post_rejects_bad_input_with_400(self):
        res = self.client.post("/api/settings", json={"audio": {"output_format": "exe"}})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.get_json()["success"])

    def test_secret_never_leaves_the_api(self):
        self.client.post("/api/settings", json={"spotify": {"client_secret": "leak-check-123"}})
        body = self.client.get("/api/settings").get_data(as_text=True)
        self.assertNotIn("leak-check-123", body)
        self.assertIn(SECRET_MASK, body)
        self.assertNotIn("leak-check-123", self.client.get("/api/status").get_data(as_text=True))

    def test_export_import_round_trip(self):
        self.client.post("/api/settings", json={"audio": {"output_format": "flac"}, "performance": {"max_workers": 3}})
        exported = self.client.get("/api/settings/export")
        self.assertIn("attachment", exported.headers["Content-Disposition"])
        payload = exported.get_json()

        self.client.post("/api/settings/reset")
        self.assertEqual(self.client.get("/api/settings").get_json()["settings"]["audio"]["output_format"], "mp3")
        res = self.client.post("/api/settings/import", json=payload)
        self.assertEqual(res.status_code, 200)
        settings = res.get_json()["settings"]
        self.assertEqual(settings["audio"]["output_format"], "flac")
        self.assertEqual(settings["performance"]["max_workers"], 3)

    def test_import_ignores_credentials_and_paths(self):
        res = self.client.post("/api/settings/import", json={
            "spotify": {"client_secret": "evil"}, "paths": {"download_dir": "C:/Windows"},
            "audio": {"output_format": "ogg"},
        })
        self.assertEqual(res.status_code, 200)
        self.assertNotEqual(SettingsService.get_secret("spotify", "client_secret"), "evil")
        self.assertNotIn("C:/Windows", json.dumps(get_config()))

    def test_wizard_no_longer_offers_a_fake_engine_choice(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("wizard_engine", html)


def tearDownModule():
    """Other modules (browser tests) expect the seeded 'first run completed' config."""
    _hermetic._seed_config(_hermetic.TEST_DATA_DIR)


if __name__ == "__main__":
    unittest.main()
