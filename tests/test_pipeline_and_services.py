"""Staged download pipeline (retries, archive, hooks, lyrics), plugin engine, maintenance, lyrics."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config_loader import get_downloads_path
from src.db import get_db_connection
from src.lyrics_service import LyricsService
from src.maintenance_service import MaintenanceService
from src.media_processor import MediaProcessor
from src.plugin_engine import DownloadArchive, PluginEngine
from src.queue_manager import JobStage, QueueJob, QueueManager
from src.settings_service import SettingsService
from src.tag_writer import TagWriter


def write_raw(track_id, dest, quality="VERY_HIGH"):
    """Stand-in for SpotifyDirectClient.download_track_to_file: writes the 'downloaded' stream to disk."""
    Path(dest).write_bytes(b"RAW")
    return {}


def _meta(track_id):
    return {"id": track_id, "title": "Pipeline Song", "artists": ["Pipeline Artist"], "album": "Pipeline Album",
            "duration_ms": 200000, "isrc": ""}


class PipelineBase(unittest.TestCase):
    def setUp(self):
        SettingsService.update({"performance": {"rate_limit_min_delay": 0, "rate_limit_max_delay": 0, "retry_limit": 2},
                                "audio": {"embed_lyrics": True}})
        self.qm = QueueManager()
        self.qm.pause()  # never let the dispatcher thread run jobs behind the test's back
        self._patches = [
            patch("shutil.disk_usage", return_value=(10**12, 10**11, 10**11)),
            patch.object(self.qm.metadata_service, "resolve_item", side_effect=lambda kind, tid: _meta(tid)),
            patch.object(MediaProcessor, "transcode", return_value=True),
            patch.object(MediaProcessor, "verify_output", return_value=(True, "ok")),
            patch.object(TagWriter, "apply_tags", return_value=True),
            patch.object(LyricsService, "fetch", return_value=""),  # never hit the network from tests
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def tearDown(self):
        conn = get_db_connection()
        with conn:  # leave nothing QUEUED behind for later QueueManager instances
            conn.execute("UPDATE jobs SET status = 'CANCELLED' WHERE id LIKE 'pipe_%'")

    def make_job(self, name, **kw):
        return QueueJob(job_id=f"pipe_{name}_{time.time_ns()}", track_id=f"pipe_{name}_{time.time_ns()}",
                        title="Pipeline Song", artists=["Pipeline Artist"], provider="spotify", **kw)


class TestPipelineStages(PipelineBase):
    def test_success_records_archive_fires_hook_and_embeds_lyrics(self):
        job = self.make_job("ok")
        with patch.object(self.qm.direct_client, "download_track_to_file", side_effect=write_raw), \
                patch.object(LyricsService, "fetch", return_value="la la la") as lyrics, \
                patch.object(PluginEngine, "dispatch") as dispatch:
            self.qm._process_job(job)

        self.assertEqual(job.status, "COMPLETED")
        self.assertEqual(job.stage, JobStage.COMPLETED)
        self.assertEqual(job.progress, 100.0)
        self.assertTrue(DownloadArchive.is_archived(job.track_id))
        event, payload = dispatch.call_args[0]
        self.assertEqual(event, "on_download_complete")
        self.assertEqual(payload["title"], "Pipeline Song")
        lyrics.assert_called_once()
        self.assertEqual(TagWriter.apply_tags.call_args.kwargs["meta"]["lyrics"], "la la la")

    def test_temp_download_is_used_for_transcoding_then_removed(self):
        job = self.make_job("tmpfile")
        seen = {}

        def capture(*args, **kwargs):
            seen["path"] = kwargs["raw_audio_path"]
            seen["existed_during_transcode"] = kwargs["raw_audio_path"].exists()
            seen["bytes_arg"] = kwargs["raw_audio_bytes"]
            return True
        with patch.object(self.qm.direct_client, "download_track_to_file", side_effect=write_raw),                 patch.object(MediaProcessor, "transcode", side_effect=capture), patch.object(PluginEngine, "dispatch"):
            self.qm._process_job(job)
        self.assertEqual(job.status, "COMPLETED")
        self.assertTrue(seen["existed_during_transcode"])
        self.assertIsNone(seen["bytes_arg"])                       # audio never travels through memory as bytes
        self.assertFalse(seen["path"].exists())                    # cleaned up afterwards

    def test_temp_download_is_removed_when_the_job_fails(self):
        job = self.make_job("tmpfail")
        job.attempts = 99
        captured = {}

        def download_then_fail(track_id, dest, quality="VERY_HIGH"):
            write_raw(track_id, dest)
            captured["path"] = Path(dest)
            return {}
        with patch.object(self.qm.direct_client, "download_track_to_file", side_effect=download_then_fail),                 patch.object(MediaProcessor, "transcode", side_effect=RuntimeError("encode failed")),                 patch.object(PluginEngine, "dispatch"):
            self.qm._process_job(job)
        self.assertEqual(job.status, "FAILED")
        self.assertFalse(captured["path"].exists())

    def test_lyrics_skipped_when_disabled(self):
        SettingsService.update({"audio": {"embed_lyrics": False}})
        job = self.make_job("nolyrics")
        with patch.object(self.qm.direct_client, "download_track_to_file", side_effect=write_raw), \
                patch.object(LyricsService, "fetch") as lyrics, patch.object(PluginEngine, "dispatch"):
            self.qm._process_job(job)
        self.assertEqual(job.status, "COMPLETED")
        lyrics.assert_not_called()

    def test_transient_failure_is_retried_with_backoff(self):
        job = self.make_job("retry")
        with patch.object(self.qm.direct_client, "download_track_to_file", side_effect=RuntimeError("connection reset")), \
                patch.object(PluginEngine, "dispatch") as dispatch:
            self.qm._process_job(job)
        self.assertEqual(job.status, "QUEUED")
        self.assertEqual(job.attempts, 1)
        self.assertGreater(job.retry_not_before, time.time())
        self.assertIn("Retry 1/2", job.speed)
        self.assertIn("Rule A", job.error)
        dispatch.assert_not_called()  # a retry is not a failure

    def test_retries_stop_at_the_limit_and_fire_the_failure_hook(self):
        job = self.make_job("exhaust")
        job.attempts = 2
        with patch.object(self.qm.direct_client, "download_track_to_file", side_effect=RuntimeError("connection reset")), \
                patch.object(PluginEngine, "dispatch") as dispatch:
            self.qm._process_job(job)
        self.assertEqual(job.status, "FAILED")
        self.assertEqual(dispatch.call_args[0][0], "on_download_failed")

    def test_auth_errors_are_never_retried(self):
        job = self.make_job("auth")
        with patch.object(self.qm.direct_client, "download_track_to_file", side_effect=RuntimeError("Premium account required")), \
                patch.object(PluginEngine, "dispatch"):
            self.qm._process_job(job)
        self.assertEqual(job.status, "FAILED")
        self.assertEqual(job.attempts, 0)

    def test_rate_limit_waits_longer(self):
        job = self.make_job("429")
        with patch.object(self.qm.direct_client, "download_track_to_file", side_effect=RuntimeError("HTTP 429 Too Many Requests")), \
                patch.object(PluginEngine, "dispatch"):
            before = time.time()
            self.qm._process_job(job)
        self.assertGreaterEqual(job.retry_not_before - before, 25)

    def test_dispatcher_skips_jobs_waiting_for_retry(self):
        job = self.make_job("gate")
        job.retry_not_before = time.time() + 3600
        self.qm.jobs = [job]  # ignore anything restored from the shared test database
        self.qm.resume()
        with patch.object(self.qm, "_process_job") as process:
            time.sleep(1.0)
        self.qm.pause()
        process.assert_not_called()

    def test_cancelled_job_stops_before_download(self):
        job = self.make_job("cancel")
        job.status = "CANCELLED"
        with patch.object(self.qm.direct_client, "download_track_to_file") as download:
            self.qm._process_job(job)
        download.assert_not_called()

    def test_low_disk_fails_fast(self):
        job = self.make_job("disk")
        with patch("shutil.disk_usage", return_value=(10**9, 10**9 - 1024, 1024)), \
                patch.object(self.qm.direct_client, "download_track_to_file") as download:
            self.qm._process_job(job)
        self.assertEqual(job.status, "FAILED")
        self.assertIn("disk space", job.error)
        download.assert_not_called()

    def test_verification_failure_is_reported(self):
        job = self.make_job("verify")
        job.attempts = 2
        with patch.object(self.qm.direct_client, "download_track_to_file", side_effect=write_raw), \
                patch.object(MediaProcessor, "verify_output", return_value=(False, "bad container")), \
                patch.object(PluginEngine, "dispatch"):
            self.qm._process_job(job)
        self.assertEqual(job.status, "FAILED")
        self.assertIn("bad container", job.error)

    def test_attempts_persist_to_the_database(self):
        job = self.make_job("persist")
        with patch.object(self.qm.direct_client, "download_track_to_file", side_effect=RuntimeError("blip")), \
                patch.object(PluginEngine, "dispatch"):
            self.qm._process_job(job)
        row = get_db_connection().execute("SELECT attempts, status FROM jobs WHERE id = ?", (job.id,)).fetchone()
        self.assertEqual((row["attempts"], row["status"]), (1, "QUEUED"))

    def test_finished_jobs_are_trimmed_from_memory(self):
        self.qm.jobs = [self.make_job(f"t{i}") for i in range(5)]
        for j in self.qm.jobs:
            j.status, j.completed_at = "COMPLETED", time.time()
        with patch.object(QueueManager, "MAX_FINISHED_IN_MEMORY", 2):
            self.qm._trim_finished()
        self.assertEqual(len(self.qm.jobs), 2)


class TestChosenAlternative(PipelineBase):
    """A YouTube/YouTube Music item the user explicitly chose, standing in for a Spotify track."""

    def alt_job(self, name, **kw):
        return QueueJob(job_id=f"pipe_{name}_{time.time_ns()}", track_id=f"yt_{name}", title="Original Title",
                        artists=["Original Artist"], provider="ytmusic", provider_item_id=f"yt_{name}",
                        canonical_url=f"https://music.youtube.com/watch?v=yt_{name}",
                        is_alternative=True, original_spotify_id="sp_original_id", **kw)

    def fake_fetch(self, **kwargs):
        path = get_downloads_path().parent / "temp" / f"alt_fetch_{time.time_ns()}.opus"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"OPUS")
        return path, {}

    def test_tags_come_from_the_original_spotify_track_not_the_youtube_id(self):
        job = self.alt_job("tags")
        resolved = []
        self.qm.metadata_service.resolve_item.side_effect = lambda kind, tid: (resolved.append(tid), _meta(tid))[1]
        with patch("src.compliant_audio.CompliantAudioProvider.fetch_audio_for_provider_item", side_effect=lambda **kw: self.fake_fetch(**kw)) as fetch,                 patch.object(self.qm.direct_client, "download_track_to_file") as spotify, patch.object(PluginEngine, "dispatch"):
            self.qm._process_job(job)
        self.assertEqual(job.status, "COMPLETED", job.error)
        self.assertEqual(resolved, ["sp_original_id"])              # looked up the Spotify track, never the YouTube id
        self.assertEqual(job.match_source, "YTMUSIC Explicit Selection")
        self.assertTrue(fetch.call_args.kwargs["return_path"])
        self.assertEqual(fetch.call_args.kwargs["provider_item_id"], "yt_tags")
        spotify.assert_not_called()                                 # no Spotify stream was requested

    def test_without_an_original_id_the_job_uses_its_own_fields_and_never_asks_spotify(self):
        job = self.alt_job("orphan")
        job.original_spotify_id = ""
        with patch("src.compliant_audio.CompliantAudioProvider.fetch_audio_for_provider_item", side_effect=lambda **kw: self.fake_fetch(**kw)),                 patch.object(PluginEngine, "dispatch"):
            self.qm._process_job(job)
        self.assertEqual(job.status, "COMPLETED", job.error)
        self.qm.metadata_service.resolve_item.assert_not_called()

    def test_a_spotify_job_still_never_touches_youtube(self):
        job = self.make_job("ruleA")
        job.attempts = 99
        with patch.object(self.qm.direct_client, "download_track_to_file", side_effect=RuntimeError("stream unavailable")),                 patch("src.compliant_audio.CompliantAudioProvider.fetch_audio_for_provider_item") as youtube,                 patch.object(PluginEngine, "dispatch"):
            self.qm._process_job(job)
        self.assertEqual(job.status, "FAILED")
        youtube.assert_not_called()                                 # Rule A: no silent fallback


class TestRestoreQueueSetting(unittest.TestCase):
    def tearDown(self):
        SettingsService.update({"general": {"restore_queue_on_startup": True}})
        with get_db_connection() as conn:
            conn.execute("UPDATE jobs SET status = 'CANCELLED' WHERE id LIKE 'restore_%'")

    def _insert(self):
        with get_db_connection() as conn:
            conn.execute("INSERT INTO jobs (id, track_id, title, artists, status, stage, added_at) VALUES (?,?,?,?,?,?,?)",
                         (f"restore_{time.time_ns()}", "rt", "Restore Me", "A", "QUEUED", "QUEUED", time.time()))

    def test_unfinished_jobs_are_restored_by_default(self):
        self._insert()
        qm = QueueManager()
        qm.pause()
        self.assertTrue(any(j.title == "Restore Me" for j in qm.jobs))

    def test_setting_off_cancels_instead_of_restoring(self):
        self._insert()
        SettingsService.update({"general": {"restore_queue_on_startup": False}})
        qm = QueueManager()
        qm.pause()
        self.assertFalse(any(j.title == "Restore Me" for j in qm.jobs))
        left = get_db_connection().execute("SELECT COUNT(*) c FROM jobs WHERE title = 'Restore Me' AND status = 'QUEUED'").fetchone()["c"]
        self.assertEqual(left, 0)


class TestPluginEngine(unittest.TestCase):
    def tearDown(self):
        for h in PluginEngine.list_hooks():
            if h["id"].startswith("ptest_"):
                PluginEngine.delete_hook(h["id"])

    def test_registration_rules(self):
        with self.assertRaises(ValueError):
            PluginEngine.register_hook("ptest_sh", "sh", "on_download_complete", "shell_command", "calc")
        with self.assertRaises(ValueError):
            PluginEngine.register_hook("ptest_file", "f", "on_download_complete", "webhook", "file:///etc/passwd")
        self.assertTrue(PluginEngine.register_hook("ptest_ok", "ok", "on_download_complete", "webhook", "http://127.0.0.1:9/h"))

    def test_webhook_fires_for_matching_event_only(self):
        PluginEngine.register_hook("ptest_wh", "wh", "on_download_complete", "webhook", "http://127.0.0.1:9/h")
        with patch("src.plugin_engine.http_client.post") as post:
            PluginEngine.trigger_event("on_download_failed", {"title": "x"})
            post.assert_not_called()
            PluginEngine.trigger_event("on_download_complete", {"title": "x"})
            post.assert_called_once()
            self.assertEqual(post.call_args.kwargs["json"], {"title": "x"})

    def test_copy_to_folder_hook(self):
        src_dir = get_downloads_path() / "hook_src"
        dst_dir = get_downloads_path() / "hook_dst"
        src_dir.mkdir(exist_ok=True)
        dst_dir.mkdir(exist_ok=True)
        f = src_dir / "song.mp3"
        f.write_bytes(b"data")
        PluginEngine.register_hook("ptest_cp", "cp", "on_download_complete", "copy_to_folder", str(dst_dir))
        PluginEngine.trigger_event("on_download_complete", {"output_path": str(f)})
        self.assertEqual((dst_dir / "song.mp3").read_bytes(), b"data")

    def test_a_failing_hook_does_not_stop_the_others(self):
        PluginEngine.register_hook("ptest_a", "a", "on_download_complete", "webhook", "http://127.0.0.1:9/a")
        PluginEngine.register_hook("ptest_b", "b", "on_download_complete", "webhook", "http://127.0.0.1:9/b")
        with patch("src.plugin_engine.http_client.post", side_effect=[RuntimeError("down"), MagicMock()]) as post:
            PluginEngine.trigger_event("on_download_complete", {})
        self.assertEqual(post.call_count, 2)

    def test_dispatch_runs_off_the_calling_thread(self):
        seen = []
        with patch.object(PluginEngine, "trigger_event", side_effect=lambda e, p: seen.append(e)):
            PluginEngine.dispatch("on_download_complete", {})
            for _ in range(50):
                if seen:
                    break
                time.sleep(0.02)
        self.assertEqual(seen, ["on_download_complete"])

    def test_archive_round_trip(self):
        DownloadArchive.record_completed("ptest_arc", "track", "T", "A", "x.mp3")
        self.assertTrue(DownloadArchive.is_archived("ptest_arc"))
        self.assertFalse(DownloadArchive.is_archived("ptest_missing"))
        self.assertTrue(any(r["resource_id"] == "ptest_arc" for r in DownloadArchive.get_archive_list(500)))


class TestMaintenance(unittest.TestCase):
    def setUp(self):
        SettingsService.update({"maintenance": {"log_retention_days": 7, "job_retention_days": 7,
                                                "backup_interval_hours": 24, "backup_enabled": True}})

    def test_prune_removes_only_old_finished_records(self):
        now = time.time()
        old, fresh = now - 30 * 86400, now - 86400
        with get_db_connection() as c:
            c.execute("INSERT INTO audit_logs (timestamp, level, category, message) VALUES (?, 'INFO', 'T', 'old-audit')", (old,))
            c.execute("INSERT INTO audit_logs (timestamp, level, category, message) VALUES (?, 'INFO', 'T', 'new-audit')", (fresh,))
            for jid, status, ts in (("mt_old_done", "COMPLETED", old), ("mt_old_queued", "QUEUED", old), ("mt_new_done", "COMPLETED", fresh)):
                c.execute("INSERT INTO jobs (id, track_id, status, added_at, completed_at) VALUES (?, ?, ?, ?, ?)", (jid, jid, status, ts, ts))
        removed = MaintenanceService.prune_old_records()
        self.assertGreaterEqual(removed["audit_logs"], 1)
        self.assertGreaterEqual(removed["jobs"], 1)
        ids = {r["id"] for r in get_db_connection().execute("SELECT id FROM jobs WHERE id LIKE 'mt_%'").fetchall()}
        self.assertEqual(ids, {"mt_old_queued", "mt_new_done"})   # unfinished and recent jobs survive
        msgs = {r["message"] for r in get_db_connection().execute("SELECT message FROM audit_logs").fetchall()}
        self.assertIn("new-audit", msgs)
        self.assertNotIn("old-audit", msgs)
        with get_db_connection() as c:
            c.execute("DELETE FROM jobs WHERE id LIKE 'mt_%'")

    def test_backup_created_when_due_and_skipped_when_recent(self):
        from src.backup_service import BackupService
        with patch.object(BackupService, "list_backups", return_value=[]), \
                patch.object(BackupService, "create_database_backup", return_value=Path("studio_auto_x.db")) as create:
            self.assertEqual(MaintenanceService.backup_if_due(), "studio_auto_x.db")
            create.assert_called_once_with(reason="auto")
        recent = [{"created": time.time() - 60, "filename": "studio_auto_r.db", "path": "x"}]
        with patch.object(BackupService, "list_backups", return_value=recent), \
                patch.object(BackupService, "create_database_backup") as create:
            self.assertIsNone(MaintenanceService.backup_if_due())
            create.assert_not_called()

    def test_backup_disabled(self):
        SettingsService.update({"maintenance": {"backup_enabled": False}})
        from src.backup_service import BackupService
        with patch.object(BackupService, "create_database_backup") as create:
            self.assertIsNone(MaintenanceService.backup_if_due())
            create.assert_not_called()

    def test_old_auto_backups_are_pruned_to_seven(self):
        import tempfile
        from src.backup_service import BackupService
        tmp = Path(tempfile.mkdtemp(prefix="bk_"))
        files = []
        for i in range(10):
            f = tmp / f"studio_auto_{i}.db"
            f.write_bytes(b"x")
            files.append({"created": time.time() - 90000 - i * 100, "filename": f.name, "path": str(f)})
        # Pretend the newest is stale so a backup is due, and the listing contains 10 autos
        with patch.object(BackupService, "list_backups", return_value=files), \
                patch.object(BackupService, "create_database_backup", return_value=Path("studio_auto_new.db")):
            MaintenanceService.backup_if_due()
        remaining = list(tmp.glob("*.db"))
        self.assertEqual(len(remaining), 7)

    def test_start_and_stop_are_idempotent(self):
        with patch("src.maintenance_service.SchedulerService") as sched, patch.object(MaintenanceService, "run_once"):
            MaintenanceService.start()
            MaintenanceService.start()
            sched.start_scheduler_thread.assert_called()
            MaintenanceService.stop()
            sched.stop_scheduler_thread.assert_called_once()
        self.assertTrue(MaintenanceService._stop.is_set())

    def test_monitor_can_be_disabled(self):
        SettingsService.update({"maintenance": {"monitor_enabled": False}})
        with patch("src.maintenance_service.SchedulerService") as sched, patch.object(MaintenanceService, "run_once"):
            MaintenanceService.start()
            sched.start_scheduler_thread.assert_not_called()
            MaintenanceService.stop()
        SettingsService.update({"maintenance": {"monitor_enabled": True}})


class TestLyricsService(unittest.TestCase):
    def test_found(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"plainLyrics": " hello world \n", "syncedLyrics": "[00:01] x"}
        with patch("src.lyrics_service.http_client.get", return_value=resp) as get:
            self.assertEqual(LyricsService.fetch("A", "T", "Alb", 201.4), "hello world")
        params = get.call_args.kwargs["params"]
        self.assertEqual((params["artist_name"], params["track_name"], params["duration"]), ("A", "T", 201))

    def test_not_found_and_errors_return_empty(self):
        with patch("src.lyrics_service.http_client.get", return_value=MagicMock(status_code=404)):
            self.assertEqual(LyricsService.fetch("A", "T"), "")
        with patch("src.lyrics_service.http_client.get", side_effect=RuntimeError("net down")):
            self.assertEqual(LyricsService.fetch("A", "T"), "")
        bad = MagicMock(status_code=200)
        bad.json.side_effect = ValueError("not json")
        with patch("src.lyrics_service.http_client.get", return_value=bad):
            self.assertEqual(LyricsService.fetch("A", "T"), "")

    def test_missing_fields_skip_the_network(self):
        with patch("src.lyrics_service.http_client.get") as get:
            self.assertEqual(LyricsService.fetch("", "T"), "")
            self.assertEqual(LyricsService.fetch("A", ""), "")
            get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
