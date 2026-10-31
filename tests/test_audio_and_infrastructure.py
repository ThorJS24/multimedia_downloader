"""
Audio analysis (fingerprint, loudness/quality, tagging) on real generated audio, plus the sync
engine, single-instance lock and desktop shell. No network is used anywhere in this module.
"""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import json
import os
import shutil
import subprocess
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

from src.config_loader import TEMP_DIR
from src.db import get_db_connection
from src.fingerprint_service import FingerprintService
from src.media_processor import get_ffmpeg_bin
from src.quality_service import QualityService
from src.sync_engine import SyncEngine
from src.tag_writer import TagWriter

WORK = TEMP_DIR / f"audio_tests_{time.time_ns()}"


def make_audio(name: str, seconds: int = 3, freq: int = 440, volume: float = 0.1, extra=()):
    """Generate a real sine-wave file with the bundled ffmpeg."""
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / name
    cmd = [get_ffmpeg_bin(), "-y", "-v", "quiet", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
           "-af", f"volume={volume}", *extra, str(out)]
    subprocess.run(cmd, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out


def tearDownModule():
    shutil.rmtree(WORK, ignore_errors=True)


class TestFingerprint(unittest.TestCase):
    def test_pcm_extraction_reports_duration(self):
        wav = make_audio("fp_len.wav", seconds=2)
        pcm, dur = FingerprintService.extract_pcm_samples(wav)
        self.assertAlmostEqual(dur, 2.0, delta=0.15)
        self.assertEqual(len(pcm), int(dur * 11025 * 2))

    def test_extraction_fails_loudly_on_garbage(self):
        bad = WORK / "garbage.wav"
        WORK.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"not audio at all")
        with self.assertRaises(RuntimeError):
            FingerprintService.extract_pcm_samples(bad)

    def test_same_audio_matches_and_different_audio_does_not(self):
        a = make_audio("fp_a.wav", freq=440)
        a_copy = WORK / "fp_a_copy.wav"
        shutil.copy2(a, a_copy)
        b = make_audio("fp_b.wav", freq=1760)
        self.assertEqual(FingerprintService.compare_files(a, a_copy), 1.0)
        self.assertLess(FingerprintService.compare_files(a, b), 0.85)

    def test_fingerprint_is_cached(self):
        wav = make_audio("fp_cache.wav", freq=523)
        first = FingerprintService.compute_fingerprint(wav)
        second = FingerprintService.compute_fingerprint(wav)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(first["hash"], second["hash"])
        self.assertTrue(first["hash"])

    def test_acoustic_duplicates_are_found_in_the_library(self):
        a = make_audio("dup_a.wav", freq=660)
        b = WORK / "dup_b.wav"
        shutil.copy2(a, b)
        with get_db_connection() as conn:
            for p in (a, b):
                conn.execute("INSERT OR REPLACE INTO library_files (path, filename, title, artist, album) VALUES (?,?,?,?,?)",
                             (str(p), p.name, "Dup", "Artist", "Album"))
        dups = FingerprintService.find_all_acoustic_duplicates()
        pairs = {frozenset((d["file_a"]["path"], d["file_b"]["path"])) for d in dups}
        self.assertIn(frozenset((str(a), str(b))), pairs)
        with get_db_connection() as conn:
            conn.execute("DELETE FROM library_files WHERE path IN (?, ?)", (str(a), str(b)))


class TestQuality(unittest.TestCase):
    def test_stream_inspection(self):
        info = QualityService.inspect_stream(make_audio("q_stream.flac"))
        self.assertEqual(info["codec"], "flac")
        self.assertEqual(info["channels"], 1)
        self.assertAlmostEqual(info["duration_sec"], 3.0, delta=0.2)

    def test_unreadable_file_reports_an_error(self):
        bad = WORK / "q_bad.mp3"
        WORK.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"\x00" * 64)
        self.assertIn("error", QualityService.inspect_stream(bad))
        self.assertEqual(QualityService.audit_file(bad)["status"], "ERROR")

    def test_loudness_comes_from_the_summary_not_the_first_frame(self):
        quiet = QualityService.analyze_loudness_and_peaks(make_audio("q_quiet.wav", volume=0.05))
        loud = QualityService.analyze_loudness_and_peaks(make_audio("q_loud.wav", volume=0.5))
        self.assertTrue(quiet["measured"] and loud["measured"])
        # A sine 20 dB louder must measure roughly 20 LU louder (the old first-frame parse could not)
        self.assertAlmostEqual(loud["integrated_lufs"] - quiet["integrated_lufs"], 20.0, delta=1.5)
        self.assertLess(loud["true_peak_dbtp"], 0.0)
        self.assertFalse(loud["is_clipping"])

    def test_clipping_is_detected(self):
        clipped = QualityService.analyze_loudness_and_peaks(make_audio("q_clip.wav", volume=12.0))
        self.assertTrue(clipped["is_clipping"])
        self.assertGreaterEqual(clipped["true_peak_dbtp"], 0.0)

    def test_unmeasurable_audio_is_not_given_invented_numbers(self):
        with patch("src.quality_service.subprocess.run") as run:
            run.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"garbage with no summary")
            res = QualityService.analyze_loudness_and_peaks(Path("x.wav"))
        self.assertFalse(res["measured"])
        self.assertIsNone(res["integrated_lufs"])
        self.assertIsNone(res["true_peak_dbtp"])

    def test_audit_reports_every_finding_and_the_worst_status(self):
        # 3 s of clipped, low-bitrate audio: clipping + truncated (+ low bitrate) all apply
        path = make_audio("q_multi.mp3", volume=12.0, extra=("-b:a", "64k"))
        res = QualityService.audit_file(path)
        self.assertEqual(res["status"], "CLIPPING_RISK")           # most severe wins
        text = " ".join(res["reasons"])
        self.assertIn("True peak", text)
        self.assertIn("Short audio", text)
        row = get_db_connection().execute("SELECT status FROM quality_audits WHERE path = ?", (str(path),)).fetchone()
        self.assertEqual(row["status"], "CLIPPING_RISK")

    def test_audit_library_counts_statuses(self):
        path = make_audio("q_lib.wav")
        with get_db_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO library_files (path, filename, title, artist, album) VALUES (?,?,?,?,?)",
                         (str(path), path.name, "T", "A", "B"))
        res = QualityService.audit_library()
        self.assertGreaterEqual(res["total_audited"], 1)
        self.assertEqual(sum(res["summary"].values()), res["total_audited"])
        with get_db_connection() as conn:
            conn.execute("DELETE FROM library_files WHERE path = ?", (str(path),))


class TestTagWriter(unittest.TestCase):
    META = {"title": "Tag Title", "artists": ["Tag Artist", "Guest"], "album": "Tag Album", "album_artist": "Tag Artist",
            "year": "2024", "track_number": 3, "total_tracks": 10, "isrc": "USABC1234567", "lyrics": "la la la"}
    COVER = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")  # minimal JPEG

    def test_mp3_tags_cover_and_lyrics(self):
        f = make_audio("t.mp3")
        self.assertTrue(TagWriter.apply_tags(f, dict(self.META), self.COVER, save_cover_art_file=True))
        tags = ID3(f)
        self.assertEqual(str(tags["TIT2"]), "Tag Title")
        self.assertEqual(str(tags["TALB"]), "Tag Album")
        self.assertIn("la la la", str(tags.getall("USLT")[0]))
        self.assertTrue(tags.getall("APIC"))
        self.assertTrue((f.parent / "cover.jpg").exists())

    def test_flac_tags(self):
        f = make_audio("t.flac")
        self.assertTrue(TagWriter.apply_tags(f, dict(self.META), self.COVER))
        audio = FLAC(f)
        self.assertEqual(audio["title"], ["Tag Title"])
        self.assertEqual(audio["album"], ["Tag Album"])
        self.assertTrue(audio.pictures)

    def test_ogg_tags(self):
        f = make_audio("t.ogg", extra=("-c:a", "libvorbis"))
        self.assertTrue(TagWriter.apply_tags(f, dict(self.META), self.COVER))
        self.assertEqual(OggVorbis(f)["title"], ["Tag Title"])

    def test_m4a_tags(self):
        f = make_audio("t.m4a", extra=("-c:a", "aac"))
        self.assertTrue(TagWriter.apply_tags(f, dict(self.META), self.COVER))
        mp4 = MP4(f)
        self.assertEqual(mp4["\xa9nam"], ["Tag Title"])
        self.assertEqual(mp4["\xa9alb"], ["Tag Album"])

    def test_wav_is_tolerated(self):
        f = make_audio("t.wav")
        self.assertIn(TagWriter.apply_tags(f, dict(self.META), self.COVER), (True, False))

    def test_corrupt_file_is_restored_after_a_failed_tagging(self):
        f = WORK / "corrupt.mp3"
        WORK.mkdir(parents=True, exist_ok=True)
        original = b"definitely not an mp3 " * 20
        f.write_bytes(original)
        with patch.object(TagWriter, "_tag_mp3", side_effect=RuntimeError("tagger exploded")):
            try:
                TagWriter.apply_tags(f, dict(self.META), self.COVER)
            except RuntimeError:
                pass
        self.assertEqual(f.read_bytes(), original)


class TestSyncEngine(unittest.TestCase):
    def setUp(self):
        self.engine = SyncEngine()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM sync_snapshots WHERE collection_id LIKE 'sync_t_%'")

    def _live(self, ids, name="Mix"):
        return {"name": name, "tracks": [{"id": i, "title": f"Song {i}", "artists": ["A"]} for i in ids]}

    def test_first_sync_adds_everything_then_diffs(self):
        with patch.object(self.engine.metadata_service, "resolve_item", return_value=self._live(["1", "2", "3"])):
            first = self.engine.compare_and_sync("sync_t_a")
        self.assertEqual((first["added_count"], first["removed_count"], first["total_tracks"]), (3, 0, 3))

        with patch.object(self.engine.metadata_service, "resolve_item", return_value=self._live(["2", "3", "4"])):
            second = self.engine.compare_and_sync("sync_t_a")
        self.assertEqual((second["added_count"], second["removed_count"], second["unchanged_count"]), (1, 1, 2))
        self.assertEqual(second["added_tracks"][0]["id"], "4")
        self.assertEqual(second["removed_tracks"][0]["id"], "1")

    def test_dry_run_does_not_move_the_snapshot(self):
        with patch.object(self.engine.metadata_service, "resolve_item", return_value=self._live(["1"])):
            self.engine.compare_and_sync("sync_t_b")
        with patch.object(self.engine.metadata_service, "resolve_item", return_value=self._live(["1", "2"])):
            dry = self.engine.compare_and_sync("sync_t_b", dry_run=True)
            self.assertTrue(dry["dry_run"])
            again = self.engine.compare_and_sync("sync_t_b", dry_run=True)
        self.assertEqual(again["added_count"], 1)                  # still pending, snapshot unchanged
        row = get_db_connection().execute("SELECT track_count FROM sync_snapshots WHERE collection_id = 'sync_t_b'").fetchone()
        self.assertEqual(row["track_count"], 1)

    def test_tracks_already_in_the_library_are_counted(self):
        with get_db_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO library_files (path, filename, title, artist, album) VALUES (?,?,?,?,?)",
                         ("sync_t_lib.mp3", "sync_t_lib.mp3", "Song 7", "A", "B"))
        try:
            with patch.object(self.engine.metadata_service, "resolve_item", return_value=self._live(["7", "8"])):
                res = self.engine.compare_and_sync("sync_t_c", dry_run=True)
            self.assertEqual((res["already_local_count"], res["missing_count"]), (1, 1))
        finally:
            with get_db_connection() as conn:
                conn.execute("DELETE FROM library_files WHERE path = 'sync_t_lib.mp3'")

    def test_corrupt_snapshot_falls_back_to_full_add(self):
        with get_db_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO sync_snapshots (collection_id, collection_name, track_count, snapshot_json, created_at) "
                         "VALUES ('sync_t_d', 'x', 1, '{not json', 1)")
        with patch.object(self.engine.metadata_service, "resolve_item", return_value=self._live(["1", "2"])):
            res = self.engine.compare_and_sync("sync_t_d", dry_run=True)
        self.assertEqual(res["added_count"], 2)


class TestInstanceLock(unittest.TestCase):
    def test_second_instance_is_refused_until_release(self):
        import src.instance_lock as il
        with patch.object(il, "MUTEX_NAME", f"Local\\StudioTest_{time.time_ns()}"):
            first, second = il.SingleInstanceLock(), il.SingleInstanceLock()
            self.assertTrue(first.acquire())
            self.assertTrue(first.is_primary)
            self.assertFalse(second.acquire())
            self.assertFalse(second.is_primary)
            second.release()                                       # a non-primary must not clear the lock
            third = il.SingleInstanceLock()
            self.assertFalse(third.acquire())
            first.release()
            self.assertTrue(il.SingleInstanceLock().acquire())

    def test_lockfile_fallback_when_mutex_is_unavailable(self):
        import src.instance_lock as il
        lock = il.SingleInstanceLock()
        with patch.object(il.os, "name", "posix"), patch.object(il, "LOCK_FILE_PATH", WORK / "app.lock"):
            self.assertTrue(lock.acquire())
            self.assertEqual((WORK / "app.lock").read_text(), str(il.os.getpid()))
            lock.release()
            self.assertFalse((WORK / "app.lock").exists())


class TestDesktopShell(unittest.TestCase):
    def test_free_port_is_usable(self):
        import socket
        from src.desktop_shell import find_free_port
        port = find_free_port()
        self.assertTrue(1024 <= port <= 65535)
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))

    def test_second_instance_exits_cleanly(self):
        from src.desktop_shell import DesktopApp
        with patch("src.desktop_shell.instance_lock") as lock:
            lock.acquire.return_value = False
            with self.assertRaises(SystemExit):
                DesktopApp(port=5999, headless=True).launch()

    def test_headless_launch_prints_a_tokenised_link_and_starts_services(self):
        from src.desktop_shell import DesktopApp
        from src.security import SESSION_AUTH_TOKEN
        app_ = DesktopApp(port=5998, headless=True)
        out = []
        with patch("src.desktop_shell.instance_lock") as lock, \
                patch("src.web.server.app.run") as run, \
                patch("src.web.server.start_background_services") as services, \
                patch("builtins.print", side_effect=lambda *a, **k: out.append(" ".join(map(str, a)))), \
                patch("src.desktop_shell.time") as fake_time:
            fake_time.sleep.side_effect = lambda s: setattr(app_, "is_shutting_down", True)
            lock.acquire.return_value = True
            app_.launch()
            app_.server_thread.join(timeout=2)
        services.assert_called_once()
        self.assertTrue(any(f"?token={SESSION_AUTH_TOKEN}" in line for line in out))
        run.assert_called()

    def test_shutdown_stops_services_and_exits(self):
        from src.desktop_shell import DesktopApp
        app_ = DesktopApp(port=5997, headless=True)
        with patch("src.maintenance_service.MaintenanceService.stop") as stop, \
                patch("src.desktop_shell.instance_lock") as lock:
            with self.assertRaises(SystemExit):
                app_.shutdown()
        stop.assert_called_once()
        lock.release.assert_called_once()
        app_.shutdown()  # idempotent: second call returns without exiting


if __name__ == "__main__":
    unittest.main()


class TestTranscode(unittest.TestCase):
    def setUp(self):
        from src.media_processor import MediaProcessor
        self.mp = MediaProcessor

    def out(self, name):
        WORK.mkdir(parents=True, exist_ok=True)
        return WORK / name

    def test_file_input_is_converted_and_left_untouched(self):
        src = make_audio("tc_in.wav", seconds=2)
        before = src.read_bytes()
        out = self.out("tc_out.mp3")
        self.assertTrue(self.mp.transcode(None, out, "mp3", "128k", raw_audio_path=src))
        ok, msg = self.mp.verify_output(out, min_duration_sec=1.0)
        self.assertTrue(ok, msg)
        self.assertEqual(src.read_bytes(), before)               # caller owns its input file

    def test_bytes_input_still_works(self):
        src = make_audio("tc_bytes.wav", seconds=2)
        out = self.out("tc_bytes_out.flac")
        self.assertTrue(self.mp.transcode(src.read_bytes(), out, "flac"))
        self.assertTrue(self.mp.verify_output(out)[0])

    def test_needs_some_input(self):
        with self.assertRaises(ValueError):
            self.mp.transcode(None, self.out("never.mp3"))

    def test_failed_encode_leaves_nothing_at_the_destination(self):
        bad = self.out("tc_garbage.bin")
        bad.write_bytes(b"this is not audio" * 100)
        out = self.out("tc_fail.mp3")
        with self.assertRaises(RuntimeError):
            self.mp.transcode(None, out, "mp3", raw_audio_path=bad)
        self.assertFalse(out.exists())                           # no half-written file for the next run to "skip"
        self.assertFalse(list(TEMP_DIR.glob(f"out_{os.getpid()}_tc_fail*")))

    def test_failed_encode_does_not_clobber_an_existing_file(self):
        out = self.out("tc_keep.mp3")
        out.write_bytes(b"previous good file")
        bad = self.out("tc_garbage2.bin")
        bad.write_bytes(b"nope" * 100)
        with self.assertRaises(RuntimeError):
            self.mp.transcode(None, out, "mp3", raw_audio_path=bad)
        self.assertEqual(out.read_bytes(), b"previous good file")

    def test_ogg_is_copied_unless_loudness_normalisation_is_requested(self):
        src = make_audio("tc_vorbis.ogg", extra=("-c:a", "libvorbis"))
        copy_out = self.out("tc_copy.ogg")
        self.mp.transcode(None, copy_out, "ogg", raw_audio_path=src)
        self.assertEqual(copy_out.read_bytes(), src.read_bytes())          # passthrough, no re-encode
        norm_out = self.out("tc_norm.ogg")
        self.mp.transcode(None, norm_out, "ogg", "128k", normalize_loudness=True, raw_audio_path=src)
        self.assertNotEqual(norm_out.read_bytes(), src.read_bytes())       # normalisation actually re-encoded it
        self.assertTrue(self.mp.verify_output(norm_out)[0])
