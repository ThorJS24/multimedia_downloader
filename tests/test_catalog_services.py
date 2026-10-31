"""Release comparison + tag repair, artist monitoring scheduler, Spotify link/API helper (no network)."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import mutagen

from src.config_loader import TEMP_DIR
from src.db import get_db_connection
from src.media_processor import get_ffmpeg_bin
from src.release_comparison import ReleaseComparisonStudio
from src.scheduler_service import SchedulerService
from src.spotify_api import SpotifyApiHelper

WORK = TEMP_DIR / f"catalog_tests_{time.time_ns()}"


def tearDownModule():
    shutil.rmtree(WORK, ignore_errors=True)


def make_mp3(name):
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / name
    subprocess.run([get_ffmpeg_bin(), "-y", "-v", "quiet", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(out)],
                   check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out


def album(name, titles, artist="Artist"):
    return {"name": name, "artist": artist, "cover_url": f"https://img/{name}.jpg",
            "tracks": [{"title": t, "duration_ms": d} for t, d in titles]}


class TestReleaseComparison(unittest.TestCase):
    def test_compare_finds_common_and_exclusive_tracks(self):
        a = album("Standard", [("One", 180000), ("Two", 200000), ("Three", 150000)])
        b = album("Deluxe", [("one", 180500), ("TWO", 215000), ("Bonus", 120000)])
        with patch("src.release_comparison.MetadataService") as ms:
            ms.return_value.resolve_item.side_effect = [a, b]
            res = ReleaseComparisonStudio.compare_releases("A", "B")
        self.assertEqual(res["matched_tracks_count"], 2)
        self.assertEqual([t["title"] for t in res["only_in_a"]], ["Three"])
        self.assertEqual([t["title"] for t in res["only_in_b"]], ["Bonus"])
        by_title = {m["title"].lower(): m for m in res["matched_tracks"]}
        self.assertTrue(by_title["one"]["is_identical_duration"])
        self.assertFalse(by_title["two"]["is_identical_duration"])
        self.assertEqual(by_title["two"]["duration_diff_sec"], 15.0)
        self.assertEqual((res["release_a"]["track_count"], res["release_b"]["name"]), (3, "Deluxe"))

    def test_provenance_upsert(self):
        ReleaseComparisonStudio.save_provenance("prov_t_1", "title", "Old", "user", 0.5)
        ReleaseComparisonStudio.save_provenance("prov_t_1", "title", "New", "spotify_api", 1.0)
        rows = ReleaseComparisonStudio.get_track_provenance("prov_t_1")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["value"], rows[0]["source"]), ("New", "spotify_api"))

    def test_tag_diff_classifies_each_field(self):
        f = make_mp3("diff.mp3")
        tags = mutagen.File(str(f), easy=True)
        tags["title"], tags["artist"], tags["album"] = "same title", "Wrong Artist", ""
        tags.save()
        target = {"title": "Same Title", "artists": ["Right Artist"], "album": "Real Album", "year": "2020", "isrc": "USX1"}
        with patch("src.release_comparison.MetadataService") as ms:
            ms.return_value.resolve_item.return_value = target
            res = ReleaseComparisonStudio.compute_tag_diff(f, "trk")
        status = {d["field"]: d["status"] for d in res["diffs"]}
        self.assertEqual(status["title"], "MATCH")            # case-insensitive match
        self.assertEqual(status["artist"], "CONFLICT")
        self.assertEqual(status["album"], "ABSENT")
        self.assertTrue(res["has_changes"])

    def test_tag_diff_errors(self):
        with self.assertRaises(FileNotFoundError):
            ReleaseComparisonStudio.compute_tag_diff(WORK / "missing.mp3", "x")
        f = make_mp3("unresolved.mp3")
        with patch("src.release_comparison.MetadataService") as ms:
            ms.return_value.resolve_item.return_value = {}
            with self.assertRaises(ValueError):
                ReleaseComparisonStudio.compute_tag_diff(f, "x")

    def test_apply_diff_writes_tags_and_records_provenance(self):
        f = make_mp3("apply.mp3")
        ok = ReleaseComparisonStudio.apply_tag_diff(f, {"title": "Patched", "album": "Patched Album"}, track_id="prov_t_apply")
        self.assertTrue(ok)
        easy = mutagen.File(str(f), easy=True)
        self.assertEqual(easy["title"], ["Patched"])
        fields = {r["field_name"] for r in ReleaseComparisonStudio.get_track_provenance("prov_t_apply")}
        self.assertEqual(fields, {"title", "album"})
        with self.assertRaises(FileNotFoundError):
            ReleaseComparisonStudio.apply_tag_diff(WORK / "nope.mp3", {"title": "x"})

    def test_repair_queue_flags_missing_metadata(self):
        f = make_mp3("repair.mp3")
        with get_db_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO library_files (path, filename, title, artist, album) VALUES (?,?,?,?,?)",
                         (str(f), f.name, "Unknown", "Some Artist", ""))
        try:
            item = next(i for i in ReleaseComparisonStudio.get_metadata_repair_queue() if i["path"] == str(f))
            self.assertIn("Missing title", item["issues"])
            self.assertIn("Missing album", item["issues"])
            self.assertIn("Missing cover art file", item["issues"])
            self.assertNotIn("Missing artist", item["issues"])
        finally:
            with get_db_connection() as conn:
                conn.execute("DELETE FROM library_files WHERE path = ?", (str(f),))


class TestScheduler(unittest.TestCase):
    def setUp(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM monitored_artists WHERE id LIKE 'sch_t_%'")
            conn.execute("DELETE FROM release_calendar WHERE id LIKE 'sch_t_%'")

    def tearDown(self):
        SchedulerService.stop_scheduler_thread()
        self.setUp()

    def test_artist_crud(self):
        SchedulerService.add_monitored_artist("sch_t_a1", "Band", auto_download=True)
        SchedulerService.add_monitored_artist("sch_t_a1", "Band Renamed")       # upsert
        mine = [a for a in SchedulerService.get_monitored_artists() if a["id"] == "sch_t_a1"]
        self.assertEqual((len(mine), mine[0]["name"], mine[0]["auto_download"]), (1, "Band Renamed", 0))
        SchedulerService.remove_monitored_artist("sch_t_a1")
        self.assertFalse([a for a in SchedulerService.get_monitored_artists() if a["id"] == "sch_t_a1"])

    def _discography(self, *ids):
        return {"name": "Band", "discography": [
            {"id": i, "name": f"Album {i}", "type": "album", "release_date": "2026-01-01", "cover_url": "c"} for i in ids]}

    def test_new_releases_are_recorded_once(self):
        SchedulerService.add_monitored_artist("sch_t_a2", "Band")
        with patch("src.scheduler_service.MetadataService") as ms:
            ms.return_value.resolve_item.return_value = self._discography("sch_t_r1", "sch_t_r2")
            first = SchedulerService.check_artist_releases("sch_t_a2")
            ms.return_value.resolve_item.return_value = self._discography("sch_t_r1", "sch_t_r2", "sch_t_r3")
            second = SchedulerService.check_artist_releases("sch_t_a2")
        self.assertEqual(len(first), 2)
        self.assertEqual([r["id"] for r in second], ["sch_t_r3"])
        cal = [r for r in SchedulerService.get_release_calendar() if r["id"].startswith("sch_t_")]
        self.assertEqual(len(cal), 3)
        last = get_db_connection().execute("SELECT last_checked FROM monitored_artists WHERE id = 'sch_t_a2'").fetchone()
        self.assertGreater(last["last_checked"], 0)

    def test_run_all_isolates_a_failing_artist(self):
        SchedulerService.add_monitored_artist("sch_t_ok", "Good")
        SchedulerService.add_monitored_artist("sch_t_bad", "Bad")

        def resolve(kind, artist_id, force_refresh=False):
            if artist_id == "sch_t_bad":
                raise RuntimeError("boom")
            return self._discography("sch_t_r9")
        with patch("src.scheduler_service.MetadataService") as ms:
            ms.return_value.resolve_item.side_effect = resolve
            res = SchedulerService.run_all_monitors()
        names = {d["artist"] for d in res["details"]}
        self.assertIn("Good", names)
        self.assertNotIn("Bad", names)
        self.assertGreaterEqual(res["new_releases_found"], 1)

    def test_thread_starts_once_and_stops(self):
        with patch.object(SchedulerService, "run_all_monitors", return_value={}) as run:
            SchedulerService.start_scheduler_thread(interval_sec=3600)
            SchedulerService.start_scheduler_thread(interval_sec=3600)     # second start is a no-op
            for _ in range(50):
                if run.call_count:
                    break
                time.sleep(0.02)
            SchedulerService.stop_scheduler_thread()
        self.assertEqual(run.call_count, 1)
        self.assertFalse(SchedulerService._is_running)


class TestSpotifyApiHelper(unittest.TestCase):
    def test_link_parsing(self):
        parse = SpotifyApiHelper.parse_spotify_link
        self.assertEqual(parse("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"), ("playlist", "37i9dQZF1DXcBWIGoYBM5M"))
        self.assertEqual(parse("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT?si=abc"), ("track", "4cOdK2wGLETKBW3PvgPWqT"))
        self.assertEqual(parse("https://open.spotify.com/album/1A2B3C"), ("album", "1A2B3C"))
        self.assertEqual(parse("4cOdK2wGLETKBW3PvgPWqT"), ("unknown", "4cOdK2wGLETKBW3PvgPWqT"))
        with self.assertRaises(ValueError):
            parse("https://example.com/not-spotify")

    def test_no_credentials_means_no_web_api(self):
        with patch("src.spotify_api.SettingsService.get_all", return_value={"spotify": {"client_id": "", "client_secret": ""}}):
            helper = SpotifyApiHelper()
        self.assertFalse(helper.has_web_api())
        with self.assertRaises(RuntimeError):
            helper.get_playlist_metadata("x")

    def test_playlist_pagination(self):
        helper = SpotifyApiHelper.__new__(SpotifyApiHelper)
        helper.sp = MagicMock()
        helper.sp.playlist.return_value = {"name": "Big"}

        def item(i):
            return {"track": {"id": f"id{i}", "uri": f"spotify:track:id{i}", "name": f"T{i}", "artists": [{"name": "A"}],
                              "album": {"name": "Al", "release_date": "2020-01-01", "images": [{"url": "u"}]},
                              "duration_ms": 1000}}
        page1 = {"items": [item(1), {"track": None}], "next": "more"}
        page2 = {"items": [item(2)], "next": None}
        helper.sp.playlist_items.return_value = page1
        helper.sp.next.side_effect = lambda r: page2 if r is page1 else None
        name, tracks = helper.get_playlist_metadata("pl")
        self.assertEqual(name, "Big")
        self.assertEqual([t["id"] for t in tracks], ["id1", "id2"])       # None track skipped, page 2 followed


if __name__ == "__main__":
    unittest.main()


class TestM3uExport(unittest.TestCase):
    def test_null_fields_and_newlines_do_not_break_or_inject(self):
        from src.library_indexer import LibraryIndexer
        out = WORK / "export.m3u"
        WORK.mkdir(parents=True, exist_ok=True)
        LibraryIndexer.export_m3u(out, [
            {"path": "a.mp3", "duration_sec": None, "artist": None, "title": None, "filename": "a.mp3"},
            {"path": "b.mp3", "duration_sec": 200.7, "artist": "Evil\nArtist", "title": "Line1\r\n#EXTINF:1,Injected"},
        ])
        lines = out.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "#EXTM3U")
        self.assertEqual(lines[1:3], ["#EXTINF:0,Unknown - a.mp3", "a.mp3"])
        self.assertEqual(lines[3], "#EXTINF:200,Evil Artist - Line1 #EXTINF:1,Injected")
        self.assertEqual(lines[4], "b.mp3")
        self.assertEqual(len(lines), 5)                         # no injected lines


class TestSearchCapabilities(unittest.TestCase):
    def test_results_carry_the_canonical_capabilities(self):
        from src.providers.base import NormalizedItem
        from src.unified_search import UnifiedSearchService
        yt = NormalizedItem(provider="youtube", provider_item_id="vid1", entity_type="video", title="V",
                            primary_creator="C", canonical_url="https://www.youtube.com/watch?v=vid1",
                            capabilities={"can_play": True, "playback_type": "inline-guess"})
        svc = UnifiedSearchService.__new__(UnifiedSearchService)
        provider = MagicMock(provider_name="youtube")
        provider.is_enabled.return_value = True
        provider.search.return_value = {"videos": [yt], "tracks": []}
        svc._providers = {"youtube": provider}
        res = svc.search("anything", scope="youtube")
        caps = res["categories"]["videos"][0]["capabilities"]
        self.assertEqual(caps["playback_type"], "youtube_embed")      # resolver overrides the provider's inline guess
        self.assertFalse(caps["can_download"])
        self.assertIn("download_auth_basis", caps)
