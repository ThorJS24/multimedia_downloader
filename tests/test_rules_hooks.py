"""Rules & hooks API: validation, dry-run preview, safe apply (only indexed files, only into downloads), undo, hooks."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import shutil
import unittest
from pathlib import Path


class RulesBase(unittest.TestCase):
    def setUp(self):
        from src.config_loader import get_downloads_path
        from src.db import get_db_connection
        from src.web.server import app
        self.client = app.test_client()
        self.conn = get_db_connection()
        self.root = get_downloads_path() / "rules_test"
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.addCleanup(self._cleanup_db)
        self.paths = []
        for i, (title, artist, album, genre) in enumerate([("Song A", "Alpha", "First", "Rock"), ("Song B", "Beta", "Second", "Pop")]):
            f = self.root / f"loose_{i}.mp3"
            f.write_bytes(b"ID3" + bytes(64) + str(i).encode())
            self.paths.append(str(f.resolve()))
            with self.conn:
                self.conn.execute(
                    """INSERT OR REPLACE INTO library_files (path, filename, folder, title, artist, album, genre, format, duration_sec, bitrate_kbps)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""", (self.paths[-1], f.name, str(self.root), title, artist, album, genre, "mp3", 200, 320))

    def _cleanup_db(self):
        with self.conn:
            for p in self.paths:
                self.conn.execute("DELETE FROM library_files WHERE path = ?", (p,))
            self.conn.execute("DELETE FROM organization_rules WHERE name LIKE 'T:%'")
            self.conn.execute("DELETE FROM plugin_hooks WHERE name LIKE 'T:%'")

    def save(self, **kw):
        body = {"name": "T: rule", "priority": 5, "condition_type": "genre_contains", "condition_value": "rock",
                "action_type": "move_to_template", "action_value": "{artist}/{album}/{title}.{ext}"}
        body.update(kw)
        return self.client.post("/api/rules/save", json=body)


class TestRuleValidation(RulesBase):
    def test_a_valid_rule_is_saved_and_listed(self):
        res = self.save()
        self.assertEqual(res.status_code, 200)
        rule = res.get_json()["rule"]
        listed = self.client.get("/api/rules").get_json()["rules"]
        self.assertIn(rule["id"], [r["id"] for r in listed])

    def test_bad_input_is_rejected_with_a_readable_message(self):
        cases = [
            ({"name": "  "}, "name"),
            ({"condition_type": "teleport"}, "Unsupported condition"),
            ({"action_type": "format_disk"}, "Unsupported action"),
            ({"condition_value": ""}, "needs a value"),
            ({"priority": "abc"}, "whole number"),
            ({"priority": 100000}, "between 0 and 9999"),
            ({"action_value": ""}, "pattern is required"),
            ({"action_value": "../../etc/{title}.{ext}"}, "relative"),
            ({"action_value": "C:/Windows/{title}.{ext}"}, "relative"),
            ({"action_value": "/abs/{title}.{ext}"}, "relative"),
            ({"action_type": "rename_template", "action_value": "sub/{title}.{ext}"}, "file name"),
        ]
        for override, fragment in cases:
            res = self.save(**override)
            self.assertEqual(res.status_code, 400, override)
            self.assertIn(fragment.lower(), res.get_json()["error"].lower(), override)

    def test_a_rule_that_matches_everything_needs_no_value(self):
        self.assertEqual(self.save(condition_type="all", condition_value="").status_code, 200)

    def test_meta_lists_what_the_ui_may_offer(self):
        meta = self.client.get("/api/rules/meta").get_json()
        self.assertIn("genre_contains", meta["conditions"])
        self.assertIn("move_to_template", meta["actions"])
        self.assertEqual(meta["hook_events"], ["on_download_complete", "on_download_failed", "on_queue_finished"])


class TestPreviewApplyUndo(RulesBase):
    def preview(self):
        return self.client.post("/api/rules/preview", json={"paths": self.paths}).get_json()["preview"]

    def test_preview_never_touches_the_disk(self):
        self.save()
        p = self.preview()
        self.assertEqual(p["total_evaluated"], 2)
        self.assertTrue(all(Path(x).exists() for x in self.paths))
        moves = [a for a in p["planned_actions"] if a["will_move"]]
        self.assertTrue(moves)

    def test_apply_moves_files_and_one_click_undo_restores_them(self):
        self.save(condition_type="all", condition_value="")
        moves = [a for a in self.preview()["planned_actions"] if a["will_move"]]
        self.assertEqual(len(moves), 2)
        res = self.client.post("/api/rules/execute", json={"actions": moves, "collision_policy": "skip"}).get_json()
        self.assertEqual(res["executed_count"], 2)
        self.assertTrue(all(not Path(p).exists() for p in self.paths))
        self.assertTrue(all(Path(m["target_path"]).exists() for m in moves))
        batches = self.client.get("/api/rules/batches").get_json()["batches"]
        self.assertEqual(batches[0]["batch_id"], res["batch_id"])
        self.assertEqual(batches[0]["moved"], 2)
        undo = self.client.post("/api/rules/rollback", json={"batch_id": res["batch_id"]}).get_json()
        self.assertEqual(undo["reverted_count"], 2)
        self.assertTrue(all(Path(p).exists() for p in self.paths))                       # back where they were
        self.assertEqual(self.client.get("/api/rules/batches").get_json()["batches"][0]["rolled_back"], 2)

    def test_execute_refuses_unknown_sources_and_destinations_outside_downloads(self):
        self.save(condition_type="all", condition_value="")
        good = [a for a in self.preview()["planned_actions"] if a["will_move"]][0]
        outside = {**good, "target_path": str(Path(self.root).anchor + "stolen.mp3")}
        stranger = {**good, "source_path": str((self.root / "not_indexed.mp3").resolve())}
        for bad in (outside, stranger):
            res = self.client.post("/api/rules/execute", json={"actions": [bad]})
            self.assertEqual(res.status_code, 400, bad)
        self.assertTrue(Path(self.paths[0]).exists())

    def test_execute_validates_its_request_shape(self):
        self.assertEqual(self.client.post("/api/rules/execute", json={"actions": []}).status_code, 400)
        self.assertEqual(self.client.post("/api/rules/execute", json={"actions": ["x"]}).status_code, 400)
        self.assertEqual(self.client.post("/api/rules/execute", json={"actions": [{"source_path": "a"}]}).status_code, 400)
        self.assertEqual(self.client.post("/api/rules/execute", json={"actions": [{}], "collision_policy": "explode"}).status_code, 400)


class TestHooks(RulesBase):
    def register(self, **kw):
        body = {"name": "T: hook", "event_type": "on_download_complete", "action_type": "webhook", "target": "https://example.com/h"}
        body.update(kw)
        return self.client.post("/api/plugins/hooks/register", json=body)

    def test_register_list_delete(self):
        res = self.register().get_json()
        self.assertTrue(res["success"])
        self.assertIn(res["id"], [h["id"] for h in self.client.get("/api/plugins/hooks").get_json()["hooks"]])
        self.client.post("/api/plugins/hooks/delete", json={"id": res["id"]})
        self.assertNotIn(res["id"], [h["id"] for h in self.client.get("/api/plugins/hooks").get_json()["hooks"]])

    def test_invalid_hooks_are_rejected(self):
        self.assertEqual(self.register(name="").status_code, 400)
        self.assertEqual(self.register(event_type="on_whatever").status_code, 400)
        self.assertEqual(self.register(target="").status_code, 400)
        self.assertEqual(self.register(target="ftp://example.com").status_code, 400)           # webhooks must be http(s)
        self.assertEqual(self.register(action_type="run_script", target="x").status_code, 400)

    def test_copy_hooks_may_not_write_outside_the_application(self):
        res = self.register(action_type="copy_to_folder", target=r"C:\Users\Public\Music")
        self.assertEqual(res.status_code, 400)
        self.assertIn("inside the application", res.get_json()["error"])
        ok = self.register(action_type="copy_to_folder", target=str(self.root))
        self.assertEqual(ok.status_code, 200)


if __name__ == "__main__":
    unittest.main()
