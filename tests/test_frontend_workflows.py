try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import os
import shutil
import sys
import time
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_loader import PROJECT_ROOT, TEMP_DIR, get_downloads_path
from src.db import get_db_connection, init_db
from src.library_indexer import LibraryIndexer
from src.web.server import app, queue_manager
from tests.test_e2e_workflows import generate_synthetic_wav


class TestFrontendWorkflows(unittest.TestCase):
    """
    Comprehensive programmatic validation of web frontend endpoints and workflows
    mirroring all user actions in the dashboard UI across all 13 tabs and modals.
    """
    @classmethod
    def setUpClass(cls):
        cls.test_dir = TEMP_DIR / f"frontend_workflow_test_{os.getpid()}"
        cls.test_dir.mkdir(parents=True, exist_ok=True)
        init_db()
        app.testing = True
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir, ignore_errors=True)

    # ================= 1. INTERACTIVE IMPORT STAGING WORKFLOW =================
    def test_interactive_staging_full_sequence(self):
        """
        Validates the complete staging workflow:
        1. Preview creation with multiline input
        2. Plan validation (batch_id, collision detection, summary)
        3. Exclusion of selected tracks
        4. Commit execution (enqueues only non-excluded tracks)
        5. DB state transitions (COMMITTED vs EXCLUDED)
        6. Idempotency test (re-commit yields 0)
        7. Overwrite vs Skip collision detection
        """
        # Step 1: Create preview
        raw_input = "Artist Alpha - Track One\nArtist Beta - Track Two\nArtist Gamma - Track Three"
        res = self.client.post("/api/staging/preview", json={
            "input": raw_input,
            "output_format": "mp3",
            "collision_strategy": "skip"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        plan = data["plan"]
        batch_id = plan["batch_id"]
        items = plan["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual(plan["summary"]["new"], 3)

        # Step 2: Simulate UI checkbox toggling -> exclude items[1]
        excluded_ids = [items[1]["id"]]
        commit_res = self.client.post("/api/staging/commit", json={
            "batch_id": batch_id,
            "excluded_ids": excluded_ids
        })
        self.assertEqual(commit_res.status_code, 200)
        commit_data = commit_res.get_json()
        self.assertTrue(commit_data["success"])
        self.assertEqual(commit_data["enqueued"], 2)

        # Step 3: Verify DB records
        conn = get_db_connection()
        s0 = conn.execute("SELECT status FROM staging_items WHERE id = ?", (items[0]["id"],)).fetchone()["status"]
        s1 = conn.execute("SELECT status FROM staging_items WHERE id = ?", (items[1]["id"],)).fetchone()["status"]
        s2 = conn.execute("SELECT status FROM staging_items WHERE id = ?", (items[2]["id"],)).fetchone()["status"]
        self.assertEqual(s0, "COMMITTED")
        self.assertEqual(s1, "EXCLUDED")
        self.assertEqual(s2, "COMMITTED")

        # Step 4: Re-commit (idempotent, 0 re-enqueued)
        recommit = self.client.post("/api/staging/commit", json={
            "batch_id": batch_id,
            "excluded_ids": []
        })
        self.assertEqual(recommit.status_code, 200)
        self.assertEqual(recommit.get_json()["enqueued"], 0)

        # Step 5: Collision detection in staging preview
        down_dir = get_downloads_path()
        coll_file = down_dir / "Collision Artist - Existing Track.mp3"
        coll_file.parent.mkdir(parents=True, exist_ok=True)
        coll_file.write_text("dummy mp3 content", encoding="utf-8")
        try:
            coll_preview = self.client.post("/api/staging/preview", json={
                "input": "Collision Artist - Existing Track",
                "output_format": "mp3",
                "collision_strategy": "skip"
            }).get_json()["plan"]
            self.assertEqual(coll_preview["summary"]["collisions"], 1)
            self.assertEqual(coll_preview["items"][0]["collision_status"], "COLLISION_SKIP")

            # Overwrite policy
            coll_preview_ovw = self.client.post("/api/staging/preview", json={
                "input": "Collision Artist - Existing Track",
                "output_format": "mp3",
                "collision_strategy": "overwrite"
            }).get_json()["plan"]
            self.assertEqual(coll_preview_ovw["items"][0]["collision_status"], "COLLISION_OVERWRITE_OR_RENAME")
        finally:
            if coll_file.exists():
                coll_file.unlink()

    # ================= 2. QUEUE LIFECYCLE WORKFLOW =================
    def test_queue_lifecycle_controls(self):
        """
        Validates queue controls: snapshot, pause, resume, reorder, concurrency update, clear.
        """
        # Add test jobs directly
        t1 = {"id": f"q_fw_1_{time.time()}", "title": "Queue Track 1", "artists": ["Artist A"]}
        t2 = {"id": f"q_fw_2_{time.time()}", "title": "Queue Track 2", "artists": ["Artist B"]}
        j1 = queue_manager.add_job(t1)
        j2 = queue_manager.add_job(t2)

        # GET queue snapshot
        res = self.client.get("/api/queue/snapshot")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("jobs", data)

        # Pause queue
        res_pause = self.client.post("/api/queue/pause")
        self.assertEqual(res_pause.status_code, 200)
        self.assertTrue(res_pause.get_json()["success"])
        self.assertTrue(queue_manager.is_paused)

        # Resume queue
        res_resume = self.client.post("/api/queue/resume")
        self.assertEqual(res_resume.status_code, 200)
        self.assertTrue(res_resume.get_json()["success"])
        self.assertFalse(queue_manager.is_paused)

        # Concurrency adjustment
        res_conc = self.client.post("/api/queue/concurrency", json={"workers": 4})
        self.assertEqual(res_conc.status_code, 200)
        self.assertEqual(res_conc.get_json()["workers"], 4)
        self.assertEqual(queue_manager.max_workers, 4)

        # Reorder job
        res_reorder = self.client.post("/api/queue/reorder", json={"job_id": j2.id, "direction": "up"})
        self.assertEqual(res_reorder.status_code, 200)

        # Cancel job
        res_cancel = self.client.post("/api/queue/cancel-job", json={"job_id": j1.id})
        self.assertEqual(res_cancel.status_code, 200)
        self.assertEqual(j1.stage.value, "CANCELLED")

        # Clear finished queue
        res_clear = self.client.post("/api/queue/clear-finished")
        self.assertEqual(res_clear.status_code, 200)

    # ================= 3. LIBRARY & INTELLIGENCE WORKFLOWS =================
    def test_library_intelligence_and_inspection(self):
        """
        Validates library scanner controls, intelligence reports, and technical inspection.
        """
        # Create a synthetic audio file in downloads folder
        down_dir = get_downloads_path()
        audio_file = down_dir / "Synthetic Artist - Test Sine Wave.wav"
        generate_synthetic_wav(audio_file, duration_sec=1.5)

        try:
            # 1. Scan status / scanner controls
            status_res = self.client.get("/api/library/scan/status")
            self.assertEqual(status_res.status_code, 200)
            self.assertIn("status", status_res.get_json())

            # 2. Intelligence overview
            intel_res = self.client.get("/api/intelligence/overview")
            self.assertEqual(intel_res.status_code, 200)
            intel_data = intel_res.get_json()
            self.assertIn("health_score", intel_data)
            self.assertIn("format_breakdown", intel_data)
            self.assertIn("recommendations", intel_data)

            # 3. Technical audio inspector (GET /api/library/inspect)
            inspect_res = self.client.get(f"/api/library/inspect?path={audio_file}")
            self.assertEqual(inspect_res.status_code, 200)
            inspect_data = inspect_res.get_json()
            self.assertIn("technical", inspect_data)
            self.assertIn("metadata", inspect_data)
            self.assertEqual(inspect_data["technical"]["codec"], "wav")

            # 4. Repair proposal (POST /api/metadata/repair/propose)
            repair_prev = self.client.post("/api/metadata/repair/propose", json={"path": str(audio_file)})
            self.assertEqual(repair_prev.status_code, 200)
            r_data = repair_prev.get_json()
            self.assertTrue(r_data["success"])
            self.assertIn("proposal", r_data)

            # 5. Tag edit (POST /api/tags/edit)
            tag_res = self.client.post("/api/tags/edit", json={
                "path": str(audio_file),
                "metadata": {
                    "title": "Inspected Audio Track",
                    "artists": ["Inspected Artist"],
                    "album": "Synthetic Album",
                    "year": "2026"
                }
            })
            self.assertEqual(tag_res.status_code, 200)

            # 6. CSV Export (POST /api/library/export-csv)
            csv_res = self.client.post("/api/library/export-csv")
            self.assertEqual(csv_res.status_code, 200)
            csv_path = Path(csv_res.get_json()["path"])
            self.assertTrue(csv_path.exists())
            csv_path.unlink()
        finally:
            if audio_file.exists():
                audio_file.unlink()

    # ================= 4. BULK ORGANIZER WORKFLOW =================
    def test_bulk_organizer_preview_and_rollback(self):
        """
        Validates bulk organizer: preview (dry run with zero file moves), execute, and rollback.
        """
        down_dir = get_downloads_path()
        orig_file = down_dir / "Unorganized Artist - Unorganized Song.wav"
        generate_synthetic_wav(orig_file, duration_sec=1.0)
        # Scan file into library so organizer detects it
        LibraryIndexer.scan_directory(down_dir)

        try:
            # 1. Preview
            prev_res = self.client.post("/api/organizer/preview", json={
                "template": "{artist}/{album}/{title}.{ext}",
                "collision_strategy": "skip"
            })
            self.assertEqual(prev_res.status_code, 200)
            prev_data = prev_res.get_json()
            self.assertTrue(prev_data["success"])
            plan = prev_data["plan"]
            self.assertIn("moves", plan)
            # Original file MUST still exist at its original location (zero mutations during preview)
            self.assertTrue(orig_file.exists())

            # 2. Execute moves if any moves planned
            if plan["moves"]:
                exec_res = self.client.post("/api/organizer/execute", json={
                    "moves": plan["moves"],
                    "collision_strategy": "skip"
                })
                self.assertEqual(exec_res.status_code, 200)
                exec_data = exec_res.get_json()
                self.assertTrue(exec_data["success"])
                batch_id = exec_data["result"].get("batch_id")

                # 3. Rollback
                if batch_id:
                    roll_res = self.client.post("/api/organizer/rollback", json={
                        "batch_id": batch_id
                    })
                    self.assertEqual(roll_res.status_code, 200)
                    self.assertTrue(roll_res.get_json()["success"])
                    self.assertTrue(orig_file.exists())
        finally:
            if orig_file.exists():
                orig_file.unlink()

    # ================= 5. CONFIGURATION & DIAGNOSTICS =================
    def test_settings_persistence_and_diagnostics_export(self):
        """
        Validates settings read/write, readiness probe, and diagnostics export alias.
        """
        # 1. Readiness check
        ready_res = self.client.get("/api/system/readiness")
        self.assertEqual(ready_res.status_code, 200)
        self.assertIn("ready", ready_res.get_json())

        # 2. Settings update
        cfg_res = self.client.post("/api/settings", json={
            "audio": {"output_format": "flac"},
            "performance": {"max_workers": 3}
        })
        self.assertEqual(cfg_res.status_code, 200)
        cfg_data = cfg_res.get_json()
        self.assertTrue(cfg_data["success"])
        self.assertEqual(cfg_data["settings"]["audio"]["output_format"], "flac")
        self.assertEqual(cfg_data["settings"]["performance"]["max_workers"], 3)

        # Unknown sections/keys and out-of-range values are rejected, not silently ignored
        bad = self.client.post("/api/settings", json={"output": {"format": "flac"}})
        self.assertEqual(bad.status_code, 400)
        clamped = self.client.post("/api/settings", json={"performance": {"max_workers": 99}})
        self.assertEqual(clamped.get_json()["settings"]["performance"]["max_workers"], 4)
        self.client.post("/api/settings", json={"audio": {"output_format": "mp3"}, "performance": {"max_workers": 1}})

        # 3. Diagnostics export (both original and alias routes)
        diag1 = self.client.get("/api/system/diagnostics/export")
        self.assertEqual(diag1.status_code, 200)
        self.assertIn("application/json", diag1.headers.get("Content-Type", ""))

        diag2 = self.client.get("/api/diagnostics/export")
        self.assertEqual(diag2.status_code, 200)
        self.assertIn("application/json", diag2.headers.get("Content-Type", ""))

        # 4. Storage cleanup
        clean_res = self.client.post("/api/optimizer/clean-temp")
        self.assertEqual(clean_res.status_code, 200)
        self.assertIn("deleted_files", clean_res.get_json())
        self.assertIn("reclaimed_mb", clean_res.get_json())

    # ================= 6. DOM AND FRONTEND STRUCTURE VERIFICATION =================
    def test_frontend_dom_elements_and_xss_protection(self):
        """
        Validates index.html contains all 13 required tab sections, all 7 modals,
        and verifies app.js contains robust HTML escaping.
        """
        html_res = self.client.get("/")
        self.assertEqual(html_res.status_code, 200)
        html = html_res.data.decode("utf-8")

        # 14 tabs
        tabs = [
            "tab-dashboard", "tab-fetch", "tab-queue", "tab-matching",
            "tab-library", "tab-intelligence", "tab-quality_lab", "tab-comparator",
            "tab-sync", "tab-monitor", "tab-watcher", "tab-optimizer", "tab-logs", "tab-settings"
        ]
        for tab in tabs:
            self.assertIn(f'id="{tab}"', html, f"Missing tab element: {tab}")

        # 7 modals
        modals = [
            "wizard-modal", "login-modal", "staging-modal", "tag-modal",
            "file-inspector-modal", "metadata-repair-modal", "bulk-organizer-modal"
        ]
        for modal in modals:
            self.assertIn(f'id="{modal}"', html, f"Missing modal dialog: {modal}")

        # Static app.js verification
        js_dir = Path(__file__).resolve().parent.parent / "src" / "web" / "static" / "js"
        self.assertTrue(js_dir.is_dir())
        js_content = "\n".join(p.read_text(encoding="utf-8") for p in sorted(js_dir.glob("*.js")))

        # Verify XSS protection function exists and escapes tags and quotes
        self.assertIn("&amp;", js_content)
        self.assertIn("&lt;", js_content)
        self.assertIn("&gt;", js_content)
        self.assertIn("&quot;", js_content)
        self.assertIn("&#039;", js_content)


if __name__ == "__main__":
    unittest.main()

