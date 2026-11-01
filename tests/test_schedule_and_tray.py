"""Download schedule, desktop notifications / tray decisions, and how the queue uses them (no real tray, no network)."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import threading
import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.download_schedule import DownloadSchedule
from src.plugin_engine import PluginEngine
from src.queue_manager import QueueManager
from src.settings_service import SettingsService, days_valid, parse_hhmm
from src.tray_service import TrayService, batch_message
from src.web.server import app

ALL_DAYS = "mon,tue,wed,thu,fri,sat,sun"


def cfg(start="01:00", end="07:00", days=ALL_DAYS, enabled=True):
    return {"enabled": enabled, "start": start, "end": end, "days": days}


def at(day, hhmm):
    """2026-11-02 is a Monday; day 0 = Monday."""
    h, m = hhmm.split(":")
    return datetime(2026, 11, 2 + day, int(h), int(m))


class TestValidators(unittest.TestCase):
    def test_times(self):
        for ok in ("00:00", "07:30", "23:59"):
            self.assertIsNotNone(parse_hhmm(ok), ok)
        for bad in ("24:00", "7:5", "07:60", "noon", "", "07-30", None):
            self.assertIsNone(parse_hhmm(bad), bad)

    def test_days(self):
        self.assertTrue(days_valid("mon,sat"))
        self.assertTrue(days_valid(" mon , tue "))
        for bad in ("", "monday", "mon,xyz", ",,"):
            self.assertFalse(days_valid(bad), bad)

    def test_the_settings_schema_refuses_bad_schedules_and_keeps_good_ones(self):
        before = SettingsService.get_all()
        self.addCleanup(lambda: SettingsService.update({"schedule": before["schedule"], "notifications": before["notifications"]}))
        for bad in ({"start": "25:00"}, {"end": "soon"}, {"days": "funday"}):
            with self.assertRaises(ValueError):
                SettingsService.update({"schedule": bad})
        saved = SettingsService.update({"schedule": {"enabled": True, "start": "22:00", "end": "06:00", "days": "fri,sat"}})
        self.assertEqual((saved["schedule"]["start"], saved["schedule"]["days"]), ("22:00", "fri,sat"))


class TestWindow(unittest.TestCase):
    def setUp(self):
        DownloadSchedule.override = False
        self.addCleanup(lambda: setattr(DownloadSchedule, "override", False))

    def test_disabled_schedule_never_blocks(self):
        self.assertTrue(DownloadSchedule.allowed_now(at(0, "12:00"), cfg(enabled=False)))
        self.assertIsNone(DownloadSchedule.next_start(at(0, "12:00"), cfg(enabled=False)))

    def test_a_daytime_window_includes_its_start_and_excludes_its_end(self):
        c = cfg("09:00", "17:00")
        self.assertFalse(DownloadSchedule.allowed_now(at(0, "08:59"), c))
        self.assertTrue(DownloadSchedule.allowed_now(at(0, "09:00"), c))
        self.assertTrue(DownloadSchedule.allowed_now(at(0, "16:59"), c))
        self.assertFalse(DownloadSchedule.allowed_now(at(0, "17:00"), c))

    def test_a_night_window_crosses_midnight_and_belongs_to_the_day_it_starts_on(self):
        c = cfg("22:00", "06:00", days="fri")
        self.assertTrue(DownloadSchedule.allowed_now(at(4, "23:00"), c))       # Friday evening
        self.assertTrue(DownloadSchedule.allowed_now(at(5, "05:59"), c))       # ...still on Saturday early morning
        self.assertFalse(DownloadSchedule.allowed_now(at(5, "06:00"), c))
        self.assertFalse(DownloadSchedule.allowed_now(at(5, "23:00"), c))      # Saturday night is not a selected day
        self.assertFalse(DownloadSchedule.allowed_now(at(4, "05:00"), c))      # Friday early morning belongs to Thursday's window

    def test_equal_start_and_end_means_the_whole_day_on_selected_days(self):
        c = cfg("00:00", "00:00", days="mon")
        self.assertTrue(DownloadSchedule.allowed_now(at(0, "13:37"), c))
        self.assertFalse(DownloadSchedule.allowed_now(at(1, "13:37"), c))

    def test_next_start_is_today_tomorrow_or_the_next_selected_day(self):
        c = cfg("01:00", "07:00")
        self.assertEqual(DownloadSchedule.next_start(at(0, "12:00"), c), at(1, "01:00"))
        self.assertEqual(DownloadSchedule.next_start(at(0, "00:30"), c), at(0, "01:00"))
        self.assertIsNone(DownloadSchedule.next_start(at(0, "03:00"), c))                 # a window is open right now
        weekend_only = cfg("01:00", "07:00", days="sat,sun")
        self.assertEqual(DownloadSchedule.next_start(at(0, "12:00"), weekend_only), at(5, "01:00"))

    def test_override_lifts_the_schedule_and_describe_says_so(self):
        c = cfg("01:00", "02:00")
        with patch.object(DownloadSchedule, "config", return_value=c):
            self.assertFalse(DownloadSchedule.allowed_now(at(0, "12:00")))
            waiting = DownloadSchedule.describe(at(0, "12:00"))
            self.assertEqual((waiting["enabled"], waiting["allowed_now"], waiting["window"]), (True, False, "01:00–02:00"))
            self.assertEqual(waiting["next_start"], "2026-11-03T01:00")
            self.assertIn("Tuesday", waiting["next_start_text"])
            DownloadSchedule.set_override(True)
            self.assertTrue(DownloadSchedule.allowed_now(at(0, "12:00")))
            self.assertTrue(DownloadSchedule.describe(at(0, "12:00"))["override"])
            self.assertIsNone(DownloadSchedule.describe(at(0, "12:00"))["next_start"])

    def test_settings_changes_are_picked_up_without_waiting_for_the_cache(self):
        before = SettingsService.get_all()
        self.addCleanup(lambda: SettingsService.update({"schedule": before["schedule"]}))
        SettingsService.update({"schedule": {"enabled": False}})
        self.assertFalse(DownloadSchedule.config()["enabled"])
        SettingsService.update({"schedule": {"enabled": True, "start": "03:00", "end": "04:00"}})
        self.assertTrue(DownloadSchedule.config()["enabled"])
        self.assertEqual(DownloadSchedule.config()["start"], "03:00")


class TestQueueUsesTheSchedule(unittest.TestCase):
    def test_a_waiting_job_does_not_start_outside_the_window_and_is_kept(self):
        from src.web.state import queue_manager
        job = SimpleNamespace(id="sched_job", status="QUEUED", retry_not_before=0.0, stage=None, track_id="sched_t", to_dict=lambda: {})
        with patch("src.queue_manager.DownloadSchedule.allowed_now", return_value=False), patch.object(queue_manager, "_save_job_to_db"):
            queue_manager.jobs.append(job)
            try:
                time.sleep(1.3)                                               # several dispatcher ticks
                self.assertEqual(job.status, "QUEUED")
                self.assertFalse([w for w in queue_manager.active_workers if "sched_t" in w.name])
            finally:
                queue_manager.jobs.remove(job)

    def test_snapshot_and_override_route_expose_the_schedule(self):
        client = app.test_client()
        snap = client.get("/api/queue/snapshot").get_json()
        self.assertIn("schedule", snap)
        self.assertEqual(set(snap["schedule"]), {"enabled", "override", "allowed_now", "window", "next_start", "next_start_text"})
        self.addCleanup(lambda: DownloadSchedule.set_override(False))
        on = client.post("/api/queue/schedule-override", json={"on": True}).get_json()
        self.assertTrue(on["schedule"]["override"])
        self.assertFalse(client.post("/api/queue/schedule-override", json={"on": False}).get_json()["schedule"]["override"])


class TestBatchFinished(unittest.TestCase):
    def manager(self, statuses):
        qm = QueueManager.__new__(QueueManager)          # only the state _track_batch reads; no threads, no database
        qm._lock = threading.RLock()
        qm._batch_ids = set()
        qm.jobs = [SimpleNamespace(id=f"j{i}", status=s) for i, s in enumerate(statuses)]
        return qm

    def test_the_end_of_a_batch_is_announced_once_with_counts(self):
        qm = self.manager(["ACTIVE", "ACTIVE", "QUEUED"])
        with patch.object(PluginEngine, "dispatch") as dispatch, patch.object(TrayService, "notify_batch_finished") as notify:
            qm._track_batch()
            dispatch.assert_not_called()                                  # still busy
            for job, final in zip(qm.jobs, ["COMPLETED", "COMPLETED", "FAILED"], strict=True):
                job.status = final
            qm._track_batch()
            qm._track_batch()                                             # a later idle tick must not announce again
        dispatch.assert_called_once()
        event, payload = dispatch.call_args.args
        self.assertEqual(event, "on_queue_finished")
        self.assertEqual((payload["completed"], payload["failed"], payload["total"]), (2, 1, 3))
        notify.assert_called_once()

    def test_a_batch_that_only_got_cancelled_says_nothing(self):
        qm = self.manager(["QUEUED"])
        with patch.object(PluginEngine, "dispatch") as dispatch, patch.object(TrayService, "notify_batch_finished") as notify:
            qm._track_batch()
            qm.jobs[0].status = "CANCELLED"
            qm._track_batch()
        dispatch.assert_not_called()
        notify.assert_not_called()

    def test_a_retrying_job_keeps_the_batch_open(self):
        qm = self.manager(["ACTIVE"])
        with patch.object(PluginEngine, "dispatch") as dispatch:
            qm._track_batch()
            qm.jobs[0].status = "QUEUED"                                  # failed once, waiting to retry
            qm._track_batch()
            dispatch.assert_not_called()
            qm.jobs[0].status = "COMPLETED"
            qm._track_batch()
            dispatch.assert_called_once()

    def test_hooks_can_listen_for_it(self):
        self.assertIn("on_queue_finished", PluginEngine.EVENTS)
        from src.web.routes.automation import HOOK_EVENTS
        self.assertEqual(tuple(HOOK_EVENTS), PluginEngine.EVENTS)


class TestMessages(unittest.TestCase):
    def test_wording(self):
        self.assertEqual(batch_message({"completed": 1}), ("Downloads finished", "1 track downloaded."))
        self.assertEqual(batch_message({"completed": 12, "skipped": 2}), ("Downloads finished", "12 tracks downloaded, 2 skipped."))
        title, text = batch_message({"completed": 3, "failed": 1})
        self.assertEqual(title, "Downloads finished with problems")
        self.assertEqual(text, "3 tracks downloaded, 1 failed.")
        self.assertIsNone(batch_message({"skipped": 4}))
        self.assertIsNone(batch_message({}))


class FakeBackend:
    instances = []
    start_ok = True

    def __init__(self, tooltip, **callbacks):
        self.callbacks, self.sent, self.stopped = callbacks, [], False
        FakeBackend.instances.append(self)

    def start(self):
        return FakeBackend.start_ok

    def stop(self):
        self.stopped = True

    def notify(self, title, text):
        self.sent.append((title, text))


class TestTrayDecisions(unittest.TestCase):
    def setUp(self):
        FakeBackend.instances, FakeBackend.start_ok = [], True
        self.before = SettingsService.get_all()["notifications"]
        self.addCleanup(lambda: SettingsService.update({"notifications": self.before}))
        patcher = patch.object(TrayService, "backend_factory", FakeBackend)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(TrayService.shutdown)
        TrayService.shutdown()
        self.callbacks = dict(on_open=MagicMock(), on_quit=MagicMock(), on_toggle_pause=MagicMock(), is_paused=lambda: False)

    def set(self, **values):
        SettingsService.update({"notifications": values})

    def test_there_is_no_tray_outside_the_desktop_window(self):
        self.set(enabled=True, minimize_to_tray=True)
        TrayService.apply_settings()                        # configure() was never called: headless / browser mode
        self.assertFalse(TrayService.active())
        self.assertFalse(TrayService.notify("t", "x"))
        self.assertEqual(FakeBackend.instances, [])

    def test_the_icon_follows_the_settings_live(self):
        self.set(enabled=False, minimize_to_tray=False)
        TrayService.configure(**self.callbacks)
        self.assertFalse(TrayService.active())              # both off: no icon at all
        self.set(enabled=True)                              # saving settings applies them immediately
        self.assertTrue(TrayService.active())
        backend = FakeBackend.instances[0]
        self.set(enabled=False, minimize_to_tray=True)
        self.assertTrue(TrayService.active())               # still wanted for minimise-to-tray
        self.assertFalse(backend.stopped)
        self.set(minimize_to_tray=False)
        self.assertFalse(TrayService.active())
        self.assertTrue(backend.stopped)

    def test_notifications_respect_their_own_switch(self):
        self.set(enabled=False, minimize_to_tray=True)
        TrayService.configure(**self.callbacks)
        self.assertFalse(TrayService.notify_batch_finished({"completed": 2}))          # tray exists, notifications are off
        self.assertEqual(FakeBackend.instances[0].sent, [])
        self.set(enabled=True)
        self.assertTrue(TrayService.notify_batch_finished({"completed": 2}))
        self.assertEqual(FakeBackend.instances[0].sent, [("Downloads finished", "2 tracks downloaded.")])
        self.assertFalse(TrayService.notify_batch_finished({"cancelled": 3}))          # nothing worth saying

    def test_minimise_to_tray_needs_the_setting_and_a_working_icon(self):
        self.set(enabled=False, minimize_to_tray=True)
        TrayService.configure(**self.callbacks)
        self.assertTrue(TrayService.minimize_to_tray_enabled())
        TrayService.shutdown()
        self.assertFalse(TrayService.minimize_to_tray_enabled())
        FakeBackend.start_ok = False                        # the icon could not be created: never hide the window
        TrayService.configure(**self.callbacks)
        self.assertFalse(TrayService.minimize_to_tray_enabled())

    def test_shutdown_removes_the_icon(self):
        self.set(enabled=True)
        TrayService.configure(**self.callbacks)
        backend = FakeBackend.instances[0]
        TrayService.shutdown()
        self.assertTrue(backend.stopped)
        self.assertFalse(TrayService.active())


if __name__ == "__main__":
    unittest.main()
