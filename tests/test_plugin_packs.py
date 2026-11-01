"""Plugin packs: validation, what the person is shown, secret handling, firing events, delivery, routes. No network."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import copy
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from src import http_client
from src.config_loader import get_downloads_path
from src.db import get_db_connection
from src.plugin_engine import PluginEngine
from src.plugin_packs import BUNDLED, PluginPacks, _render, permissions, validate_manifest
from src.settings_service import SECRET_MASK
from src.web.server import app

SECRET_URL = "https://discord.com/api/webhooks/123456/SUPER-SECRET-TOKEN"


def pack(**over):
    base = {
        "schema": 1, "id": "my-pack", "name": "My pack", "version": "1.0", "description": "Does a thing.",
        "settings": [{"key": "url", "label": "Address", "type": "url", "secret": True, "required": True}],
        "hooks": [{"event": "on_queue_finished", "action": "webhook", "target": "{url}", "body": {"text": "{completed} done"}}],
    }
    base.update(over)
    return base


class PackTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = get_db_connection()
        with self.conn:
            self.conn.execute("DELETE FROM plugin_packs")
        self.addCleanup(self.clear)
        self.client = app.test_client()

    def clear(self):
        with self.conn:
            self.conn.execute("DELETE FROM plugin_packs")


class TestManifest(unittest.TestCase):
    def test_every_bundled_pack_is_valid_and_ids_are_unique(self):
        ids = [m["id"] for m in BUNDLED]
        self.assertEqual(len(ids), len(set(ids)))
        for manifest in BUNDLED:
            self.assertEqual(validate_manifest(copy.deepcopy(manifest))["id"], manifest["id"])

    def test_a_good_pack_is_normalised(self):
        m = validate_manifest(pack(homepage="https://example.com"))
        self.assertEqual((m["id"], m["hooks"][0]["format"], m["homepage"]), ("my-pack", "json", "https://example.com"))
        self.assertEqual(validate_manifest(pack(hooks=[{"event": "on_queue_finished", "action": "webhook", "target": "{url}", "body": "hi {total}"}]))["hooks"][0]["format"], "text")

    def test_bad_packs_are_refused_with_a_reason(self):
        cases = {
            "format": pack(schema=2),
            "id": pack(id="Bad Id!"),
            "event": pack(hooks=[{"event": "on_nothing", "action": "webhook", "target": "https://x.example"}]),
            "action": pack(hooks=[{"event": "on_queue_finished", "action": "run_script", "target": "calc.exe"}]),
            "complete": pack(hooks=[{"event": "on_queue_finished", "action": "copy_to_folder", "target": "{url}"}]),
            "placeholder": pack(hooks=[{"event": "on_queue_finished", "action": "webhook", "target": "{url}", "body": "{nonsense}"}]),
            "clash": pack(settings=[{"key": "title", "label": "T", "type": "text"}]),
            "type": pack(settings=[{"key": "x", "label": "X", "type": "password"}]),
            "headers": pack(hooks=[{"event": "on_queue_finished", "action": "webhook", "target": "{url}", "headers": {"X-A": "line\r\nInjected: 1"}}]),
            "nohooks": pack(hooks=[]),
            "homepage": pack(homepage="javascript:alert(1)"),
            "toobig": pack(description="x" * 5000),
            "notobject": [],
        }
        for name, bad in cases.items():
            with self.assertRaises(ValueError, msg=name):
                validate_manifest(bad)

    def test_templates_only_ever_substitute_plain_names(self):
        self.assertEqual(_render("{a} and {b}", {"a": 1}), "1 and ")
        self.assertEqual(_render("{artists}", {"artists": ["A", "B"]}), "A, B")
        hostile = "{title.__class__} {0.__init__} {title!r}"
        self.assertEqual(_render(hostile, {"title": "T"}), hostile)          # not valid names: left exactly as written

    def test_the_permission_summary_says_what_will_happen_without_leaking_the_address(self):
        discord = validate_manifest(pack())
        text = " ".join(permissions(discord))
        self.assertIn("the queue finishes a batch", text)
        self.assertIn("completed", text)
        self.assertNotIn("SUPER-SECRET", " ".join(permissions(discord, {"url": SECRET_URL})))
        self.assertIn("discord.com", " ".join(permissions(discord, {"url": SECRET_URL})))                   # the host is fine to show
        generic = validate_manifest(pack(hooks=[{"event": "on_download_complete", "action": "webhook", "target": "{url}"}]))
        self.assertIn("output_path", " ".join(permissions(generic)))                                          # discloses the file location is sent
        copier = next(m for m in BUNDLED if m["id"] == "copy-to-folder")
        self.assertIn("copies the file", " ".join(permissions(validate_manifest(copy.deepcopy(copier)))))


class TestConfigure(PackTestCase):
    def test_required_and_invalid_values_are_explained(self):
        PluginPacks.import_pack(pack())
        with self.assertRaises(ValueError) as ctx:
            PluginPacks.configure("my-pack", {}, enabled=True)
        self.assertIn("Fill in", str(ctx.exception))
        with self.assertRaises(ValueError):
            PluginPacks.configure("my-pack", {"url": "ftp://nope.example/x"}, enabled=True)
        with self.assertRaises(ValueError):
            PluginPacks.configure("no-such-pack", {}, enabled=True)
        self.assertFalse(next(p for p in PluginPacks.list_packs() if p["id"] == "my-pack")["enabled"])

    def test_secrets_are_encrypted_at_rest_and_masked_in_what_the_page_sees(self):
        PluginPacks.import_pack(pack())
        listed = PluginPacks.configure("my-pack", {"url": SECRET_URL}, enabled=True)
        self.assertTrue(listed["enabled"])
        self.assertEqual(listed["values"]["url"], SECRET_MASK)
        raw = self.conn.execute("SELECT config_json FROM plugin_packs WHERE id = 'my-pack'").fetchone()["config_json"]
        self.assertNotIn("SUPER-SECRET", raw)
        self.assertNotIn("SUPER-SECRET", json.dumps(PluginPacks.list_packs()))
        again = PluginPacks.configure("my-pack", {"url": SECRET_MASK}, enabled=True)               # echoing the mask keeps the stored value
        self.assertTrue(again["enabled"])
        rows = PluginPacks._rows()
        self.assertEqual(PluginPacks._values(PluginPacks._manifest("my-pack", rows), rows["my-pack"], reveal=True)["url"], SECRET_URL)

    def test_turning_off_keeps_the_settings_and_turning_on_again_needs_no_retyping(self):
        PluginPacks.import_pack(pack())
        PluginPacks.configure("my-pack", {"url": SECRET_URL}, enabled=True)
        self.assertFalse(PluginPacks.configure("my-pack", {}, enabled=False)["enabled"])
        self.assertTrue(PluginPacks.configure("my-pack", {}, enabled=True)["enabled"])

    def test_folders_must_stay_inside_the_app_and_are_created(self):
        folder_pack = pack(id="folder-pack", settings=[{"key": "folder", "label": "Folder", "type": "folder", "required": True}],
                           hooks=[{"event": "on_download_complete", "action": "copy_to_folder", "target": "{folder}"}])
        PluginPacks.import_pack(folder_pack)
        with self.assertRaises(ValueError) as ctx:
            PluginPacks.configure("folder-pack", {"folder": "C:\\Windows\\Temp\\evil"}, enabled=True)
        self.assertIn("inside the application folder", str(ctx.exception))
        target = get_downloads_path() / "plugin_pack_test" / "sync"
        self.addCleanup(lambda: shutil.rmtree(target.parent, ignore_errors=True))
        PluginPacks.configure("folder-pack", {"folder": str(target)}, enabled=True)
        self.assertTrue(target.is_dir())

    def test_defaults_fill_in_optional_values(self):
        ntfy = next(p for p in PluginPacks.list_packs() if p["id"] == "ntfy-push")
        self.assertEqual(ntfy["values"]["server"], "https://ntfy.sh")
        listed = PluginPacks.configure("ntfy-push", {"topic": "a-long-random-topic"}, enabled=True)
        self.assertTrue(listed["enabled"])


class TestImport(PackTestCase):
    def test_an_imported_pack_starts_switched_off_and_can_be_removed(self):
        listed = PluginPacks.import_pack(pack())
        self.assertEqual((listed["source"], listed["enabled"]), ("imported", False))
        PluginPacks.remove("my-pack")
        self.assertNotIn("my-pack", [p["id"] for p in PluginPacks.list_packs()])

    def test_bundled_ids_cannot_be_impersonated(self):
        with self.assertRaises(ValueError):
            PluginPacks.import_pack(pack(id="discord-notify"))

    def test_replacing_a_pack_switches_it_off_so_the_new_version_is_reviewed(self):
        PluginPacks.import_pack(pack())
        PluginPacks.configure("my-pack", {"url": SECRET_URL}, enabled=True)
        PluginPacks.import_pack(pack(version="2.0", hooks=[{"event": "on_download_complete", "action": "webhook", "target": "https://elsewhere.example/x"}]))
        self.assertFalse(next(p for p in PluginPacks.list_packs() if p["id"] == "my-pack")["enabled"])

    def test_resetting_a_bundled_pack_clears_its_settings(self):
        PluginPacks.configure("discord-notify", {"webhook_url": SECRET_URL}, enabled=True)
        PluginPacks.remove("discord-notify")
        listed = next(p for p in PluginPacks.list_packs() if p["id"] == "discord-notify")
        self.assertEqual((listed["enabled"], listed["values"]["webhook_url"]), (False, ""))


class TestFiring(PackTestCase):
    def enable(self, **over):
        PluginPacks.import_pack(pack(**over))
        PluginPacks.configure("my-pack", {"url": SECRET_URL}, enabled=True)

    def test_matching_events_send_the_rendered_message_to_the_real_address(self):
        self.enable()
        with patch.object(PluginEngine, "deliver", return_value="Delivered (HTTP 204)") as deliver:
            PluginPacks.fire("on_queue_finished", {"completed": 5, "failed": 0, "skipped": 1, "cancelled": 0, "total": 6})
            PluginPacks.fire("on_download_failed", {"title": "x"})                 # a different event: ignored
        deliver.assert_called_once()
        args, kwargs = deliver.call_args
        self.assertEqual((args[0], args[1]), ("webhook", SECRET_URL))
        self.assertEqual(kwargs["body"], {"text": "5 done"})
        status = next(p for p in PluginPacks.list_packs() if p["id"] == "my-pack")["last_status"]
        self.assertTrue(status["ok"])

    def test_switched_off_packs_do_nothing(self):
        self.enable()
        PluginPacks.configure("my-pack", {}, enabled=False)
        with patch.object(PluginEngine, "deliver") as deliver:
            PluginPacks.fire("on_queue_finished", {"completed": 1})
        deliver.assert_not_called()

    def test_a_failing_pack_is_recorded_without_the_secret_and_does_not_stop_others(self):
        self.enable()
        PluginPacks.configure("generic-webhook", {"url": "https://example.org/hook"}, enabled=True)
        sent = []

        def flaky(action, target, payload, **kw):
            sent.append(target)
            if target == SECRET_URL:
                raise RuntimeError("discord.com answered HTTP 401")
            return "ok"
        with patch.object(PluginEngine, "deliver", side_effect=flaky):
            PluginPacks.fire("on_queue_finished", {"completed": 1})
        self.assertEqual(set(sent), {SECRET_URL, "https://example.org/hook"})          # the second still ran
        status = next(p for p in PluginPacks.list_packs() if p["id"] == "my-pack")["last_status"]
        self.assertFalse(status["ok"])
        self.assertIn("HTTP 401", status["detail"])
        self.assertNotIn("SUPER-SECRET", json.dumps(PluginPacks.list_packs()))

    def test_the_engine_runs_packs_after_its_own_hooks(self):
        with patch.object(PluginPacks, "fire") as fire:
            PluginEngine.trigger_event("on_queue_finished", {"completed": 1})
        fire.assert_called_once_with("on_queue_finished", {"completed": 1})

    def test_text_bodies_and_headers_are_rendered_with_event_and_setting_values(self):
        PluginPacks.configure("ntfy-push", {"topic": "secret-topic"}, enabled=True)
        with patch.object(PluginEngine, "deliver", return_value="ok") as deliver:
            PluginPacks.fire("on_queue_finished", {"completed": 2, "failed": 1, "skipped": 0, "cancelled": 0, "total": 3})
        args, kwargs = deliver.call_args
        self.assertEqual(args[1], "https://ntfy.sh/secret-topic")
        self.assertEqual((kwargs["body"], kwargs["as_text"]), ("2 downloaded, 1 failed, 0 skipped.", True))
        self.assertEqual(kwargs["headers"], {"Title": "Downloads finished"})

    def test_send_test_reports_per_hook_results_and_needs_the_pack_on(self):
        PluginPacks.import_pack(pack())
        with self.assertRaises(ValueError):
            PluginPacks.send_test("my-pack")
        PluginPacks.configure("my-pack", {"url": SECRET_URL}, enabled=True)
        with patch.object(PluginEngine, "deliver", return_value="Delivered (HTTP 204)"):
            results = PluginPacks.send_test("my-pack")
        self.assertEqual([(r["event"], r["ok"]) for r in results], [("on_queue_finished", True)])
        with patch.object(PluginEngine, "deliver", side_effect=RuntimeError("discord.com answered HTTP 404")):
            results = PluginPacks.send_test("my-pack")
        self.assertFalse(results[0]["ok"])
        self.assertIn("404", results[0]["detail"])


class TestDelivery(unittest.TestCase):
    def response(self, code=200):
        return MagicMock(status_code=code)

    def test_default_webhook_posts_the_event_as_json(self):
        with patch.object(http_client, "post", return_value=self.response(204)) as post:
            self.assertIn("204", PluginEngine.deliver("webhook", "https://example.org/h", {"title": "T"}))
        self.assertEqual(post.call_args.kwargs["json"], {"title": "T"})

    def test_text_bodies_carry_headers_and_a_content_type(self):
        with patch.object(http_client, "post", return_value=self.response()) as post:
            PluginEngine.deliver("webhook", "https://ntfy.sh/t", {}, body="hello", as_text=True, headers={"Title": "Hi"})
        kw = post.call_args.kwargs
        self.assertEqual(kw["data"], b"hello")
        self.assertEqual(kw["headers"]["Title"], "Hi")
        self.assertIn("text/plain", kw["headers"]["Content-Type"])

    def test_errors_name_the_host_never_the_address(self):
        url = "https://hooks.example.org/services/T000/B000/SECRETTOKEN"
        with patch.object(http_client, "post", return_value=self.response(401)), self.assertRaises(RuntimeError) as ctx:
            PluginEngine.deliver("webhook", url, {})
        self.assertIn("hooks.example.org", str(ctx.exception))
        self.assertIn("401", str(ctx.exception))
        self.assertNotIn("SECRETTOKEN", str(ctx.exception))
        for exc in (requests.ConnectionError("Max retries exceeded with url: /services/T000/B000/SECRETTOKEN"), requests.Timeout("slow")):
            with patch.object(http_client, "post", side_effect=exc), self.assertRaises(RuntimeError) as ctx:
                PluginEngine.deliver("webhook", url, {})
            self.assertNotIn("SECRETTOKEN", str(ctx.exception))
            self.assertIn("hooks.example.org", str(ctx.exception))

    def test_invalid_addresses_and_actions_are_refused_before_any_request(self):
        with patch.object(http_client, "post") as post:
            for bad in ("javascript:alert(1)", "file:///C:/x", "not a url", ""):
                with self.assertRaises(ValueError):
                    PluginEngine.deliver("webhook", bad, {})
            with self.assertRaises(ValueError):
                PluginEngine.deliver("run_script", "calc.exe", {})
        post.assert_not_called()

    def test_copy_needs_a_real_file_and_folder(self):
        base = get_downloads_path() / "deliver_test"
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        (base / "dest").mkdir(parents=True, exist_ok=True)
        src = base / "song.mp3"
        src.write_bytes(b"x")
        self.assertIn("Copied", PluginEngine.deliver("copy_to_folder", str(base / "dest"), {"output_path": str(src)}))
        self.assertTrue((base / "dest" / "song.mp3").exists())
        with self.assertRaises(FileNotFoundError):
            PluginEngine.deliver("copy_to_folder", str(base / "dest"), {"output_path": str(base / "missing.mp3")})
        with self.assertRaises(FileNotFoundError):
            PluginEngine.deliver("copy_to_folder", str(base / "nowhere"), {"output_path": str(src)})

    def test_logs_show_a_host_not_a_full_address(self):
        self.assertEqual(http_client.safe_url("https://discord.com/api/webhooks/1/TOKEN?x=1"), "https://discord.com/…")
        self.assertEqual(http_client.safe_url("https://example.org/"), "https://example.org")
        self.assertEqual(http_client.safe_url("https://example.org"), "https://example.org")


class TestRoutes(PackTestCase):
    def test_list_import_configure_test_remove(self):
        listing = self.client.get("/api/plugins/packs").get_json()
        self.assertIn("discord-notify", [p["id"] for p in listing["packs"]])
        self.assertIn("notifications", [b["id"] for b in listing["builtin"]])
        self.assertTrue(all(isinstance(b["enabled"], bool) for b in listing["builtin"]))

        bad = self.client.post("/api/plugins/packs/import", json={"manifest": {"schema": 1}})
        self.assertEqual(bad.status_code, 400)
        good = self.client.post("/api/plugins/packs/import", json={"manifest": pack()}).get_json()
        self.assertEqual((good["success"], good["pack"]["enabled"]), (True, False))

        missing = self.client.post("/api/plugins/packs/configure", json={"id": "my-pack", "enabled": True, "values": {}})
        self.assertEqual(missing.status_code, 400)
        self.assertIn("Fill in", missing.get_json()["error"])
        on = self.client.post("/api/plugins/packs/configure", json={"id": "my-pack", "enabled": True, "values": {"url": SECRET_URL}}).get_json()
        self.assertTrue(on["pack"]["enabled"])
        self.assertNotIn("SUPER-SECRET", json.dumps(self.client.get("/api/plugins/packs").get_json()))

        with patch.object(PluginEngine, "deliver", return_value="Delivered (HTTP 204)"):
            tested = self.client.post("/api/plugins/packs/test", json={"id": "my-pack"}).get_json()
        self.assertTrue(tested["results"][0]["ok"])
        self.assertEqual(self.client.post("/api/plugins/packs/test", json={"id": "discord-notify"}).status_code, 400)   # not switched on

        self.client.post("/api/plugins/packs/remove", json={"id": "my-pack"})
        self.assertNotIn("my-pack", [p["id"] for p in self.client.get("/api/plugins/packs").get_json()["packs"]])

    def test_oversized_uploads_are_refused(self):
        res = self.client.post("/api/plugins/packs/import", data=json.dumps({"manifest": {"x": "y" * 100000}}), content_type="application/json")
        self.assertEqual(res.status_code, 413)

    def test_the_built_in_switches_reflect_real_settings(self):
        from src.settings_service import SettingsService
        before = SettingsService.get_all()["audio"]["embed_lyrics"]
        self.addCleanup(lambda: SettingsService.update({"audio": {"embed_lyrics": before}}))
        SettingsService.update({"audio": {"embed_lyrics": not before}})
        lyrics = next(b for b in self.client.get("/api/plugins/packs").get_json()["builtin"] if b["id"] == "lyrics")
        self.assertEqual(lyrics["enabled"], (not before))


if __name__ == "__main__":
    unittest.main()
