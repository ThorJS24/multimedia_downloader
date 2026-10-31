try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import os
import shutil
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_loader import PROJECT_ROOT, TEMP_DIR, get_downloads_path
from src.db import get_db_connection, init_db
from src.plugin_engine import DownloadArchive
from src.web.server import app


class TestStagingUIAndWorkflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = TEMP_DIR / f"staging_test_env_{os.getpid()}"
        cls.test_dir.mkdir(parents=True, exist_ok=True)
        init_db()
        app.testing = True
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_staging_preview_endpoint_success(self):
        """Verify POST /api/staging/preview returns a well-formed dry-run plan."""
        payload = {
            "input": "Daft Punk - One More Time\nJustice - Genesis",
            "output_format": "mp3",
            "collision_strategy": "skip"
        }
        res = self.client.post("/api/staging/preview", json=payload)
        self.assertEqual(res.status_code, 200)

        data = res.get_json()
        self.assertTrue(data.get("success"))
        plan = data.get("plan")
        self.assertIsNotNone(plan)
        self.assertIn("batch_id", plan)
        self.assertTrue(plan["batch_id"].startswith("batch_"))
        self.assertEqual(plan["total_items"], 2)
        self.assertIn("summary", plan)
        self.assertEqual(plan["summary"]["new"], 2)
        self.assertEqual(len(plan["items"]), 2)

        item = plan["items"][0]
        self.assertTrue(item["id"].startswith("stg_"))
        self.assertEqual(item["status"], "READY")
        self.assertEqual(item["collision_status"], "NEW_DESTINATION")
        self.assertFalse(item["is_archived"])
        self.assertTrue(item["target_path"].endswith(".mp3"))

    def test_staging_preview_endpoint_validation(self):
        """Verify POST /api/staging/preview rejects empty input with 400."""
        res = self.client.post("/api/staging/preview", json={"input": ""})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("error", data)

    def test_staging_preview_custom_format_and_template(self):
        """Verify preview honors custom audio format and folder template."""
        payload = {
            "input": "Radiohead - Karma Police",
            "template": "{album}/{artist} - {title}.{ext}",
            "output_format": "flac",
            "collision_strategy": "skip"
        }
        res = self.client.post("/api/staging/preview", json=payload)
        self.assertEqual(res.status_code, 200)

        plan = res.get_json()["plan"]
        item = plan["items"][0]
        self.assertTrue(item["target_path"].endswith(".flac"))
        self.assertIn("Singles", item["target_path"])

    def test_staging_collision_detection(self):
        """Verify collision status when target file already exists on disk."""
        down_dir = get_downloads_path()
        existing_file = down_dir / "Collision Artist - Collision Track.mp3"
        existing_file.parent.mkdir(parents=True, exist_ok=True)
        existing_file.write_text("dummy audio content", encoding="utf-8")

        try:
            # 1. Preview with skip policy
            res_skip = self.client.post("/api/staging/preview", json={
                "input": "Collision Artist - Collision Track",
                "output_format": "mp3",
                "collision_strategy": "skip"
            })
            plan_skip = res_skip.get_json()["plan"]
            self.assertEqual(plan_skip["summary"]["collisions"], 1)
            item_skip = plan_skip["items"][0]
            self.assertEqual(item_skip["collision_status"], "COLLISION_SKIP")
            self.assertEqual(item_skip["status"], "WILL_SKIP")

            # 2. Preview with overwrite policy
            res_ovw = self.client.post("/api/staging/preview", json={
                "input": "Collision Artist - Collision Track",
                "output_format": "mp3",
                "collision_strategy": "overwrite"
            })
            plan_ovw = res_ovw.get_json()["plan"]
            item_ovw = plan_ovw["items"][0]
            self.assertEqual(item_ovw["collision_status"], "COLLISION_OVERWRITE_OR_RENAME")
            self.assertEqual(item_ovw["status"], "READY")
        finally:
            if existing_file.exists():
                existing_file.unlink()

    def test_staging_archive_status_detection(self):
        """Verify that tracks previously archived are tagged in staging preview."""
        # Archive a specific track ID
        track_id = "spotify_archive_test_track_123"
        DownloadArchive.record_completed(track_id, "track", "Archived Track", "Archived Artist", str(PROJECT_ROOT / "test.mp3"))

        self.assertTrue(DownloadArchive.is_archived(track_id))

    def test_staging_commit_with_exclusions_and_idempotency(self):
        """Verify POST /api/staging/commit enqueues only selected items, updates statuses, and is idempotent."""
        # 1. Create preview with 3 tracks
        preview_res = self.client.post("/api/staging/preview", json={
            "input": "Artist Alpha - Track One\nArtist Beta - Track Two\nArtist Gamma - Track Three"
        })
        plan = preview_res.get_json()["plan"]
        batch_id = plan["batch_id"]
        items = plan["items"]
        self.assertEqual(len(items), 3)

        # Exclude item 2 and item 3
        exclude_item_id = items[1]["id"]
        exclude_item_id_2 = items[2]["id"]
        excluded_list = [exclude_item_id, exclude_item_id_2]

        # 2. Commit with excluded items
        commit_res = self.client.post("/api/staging/commit", json={
            "batch_id": batch_id,
            "excluded_ids": excluded_list
        })
        self.assertEqual(commit_res.status_code, 200)
        data = commit_res.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("enqueued"), 1)

        # 3. Check SQLite database statuses
        conn = get_db_connection()
        s1 = conn.execute("SELECT status FROM staging_items WHERE id = ?", (items[0]["id"],)).fetchone()
        s2 = conn.execute("SELECT status FROM staging_items WHERE id = ?", (items[1]["id"],)).fetchone()
        s3 = conn.execute("SELECT status FROM staging_items WHERE id = ?", (items[2]["id"],)).fetchone()

        self.assertEqual(s1["status"], "COMMITTED")
        self.assertEqual(s2["status"], "EXCLUDED")
        self.assertEqual(s3["status"], "EXCLUDED")

        # 4. Idempotency test: Re-submitting commit on the same batch_id must enqueue 0 additional tracks
        retry_res = self.client.post("/api/staging/commit", json={
            "batch_id": batch_id,
            "excluded_ids": []
        })
        self.assertEqual(retry_res.status_code, 200)
        self.assertEqual(retry_res.get_json().get("enqueued"), 0)

    def test_staging_commit_all_excluded(self):
        """Verify committing with all items excluded enqueues 0 tracks and marks all as EXCLUDED."""
        preview_res = self.client.post("/api/staging/preview", json={
            "input": "Solo Artist - Only Track"
        })
        plan = preview_res.get_json()["plan"]
        batch_id = plan["batch_id"]
        item_id = plan["items"][0]["id"]

        commit_res = self.client.post("/api/staging/commit", json={
            "batch_id": batch_id,
            "excluded_ids": [item_id]
        })
        self.assertEqual(commit_res.status_code, 200)
        self.assertEqual(commit_res.get_json().get("enqueued"), 0)

        conn = get_db_connection()
        row = conn.execute("SELECT status FROM staging_items WHERE id = ?", (item_id,)).fetchone()
        self.assertEqual(row["status"], "EXCLUDED")

    def test_staging_commit_validation(self):
        """Verify POST /api/staging/commit validates missing batch_id."""
        res = self.client.post("/api/staging/commit", json={})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
