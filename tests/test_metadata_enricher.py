"""Online tag/artwork lookup: matching rules, proposals, the polite client, the background job and the apply step.
No test touches the network: MusicBrainz and the Cover Art Archive are replaced by canned answers."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import shutil
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from mutagen.wave import WAVE

from src import metadata_enricher as me
from src.config_loader import get_downloads_path
from src.db import get_db_connection
from src.metadata_enricher import LookupUnavailable, MetadataEnricher, MusicBrainzClient
from src.web.server import app

RELEASE_ID = "11111111-2222-3333-4444-555555555555"


def recording(title="Blue Monday", artist="New Order", score=100, length=449000, releases=None):
    return {
        "id": "rec-1", "score": score, "title": title, "length": length,
        "artist-credit": [{"name": artist, "joinphrase": ""}],
        "releases": releases if releases is not None else [
            {"id": "bad", "title": "Greatest Hits", "date": "2005-01-01", "status": "Official", "release-group": {"primary-type": "Compilation"}},
            {"id": RELEASE_ID, "title": "Power, Corruption & Lies", "date": "1983-05-02", "status": "Official", "track-count": 8,
             "release-group": {"primary-type": "Album"}, "media": [{"track": [{"number": "7"}]}]},
        ],
    }


class TestMatching(unittest.TestCase):
    def test_blank_values_include_the_placeholders_the_app_writes(self):
        for blank in (None, "", "  ", "Unknown Artist", "Unknown Album", "Singles", 0):
            self.assertTrue(me.is_blank(blank), blank)
        for real in ("Blue Monday", 7, "1983"):
            self.assertFalse(me.is_blank(real), real)

    def test_a_convincing_recording_is_picked_and_prefers_the_official_album(self):
        match = me.pick_match([recording()], "Blue Monday", "New Order", 449.0)
        self.assertEqual(match["release"]["id"], RELEASE_ID)
        self.assertGreaterEqual(match["confidence"], 0.9)

    def test_weak_or_inconsistent_matches_are_rejected(self):
        self.assertIsNone(me.pick_match([recording(score=70)], "Blue Monday", "New Order", 449.0))           # low service score
        self.assertIsNone(me.pick_match([recording()], "Blue Monday", "New Order", 200.0))                  # length is way off
        self.assertIsNone(me.pick_match([recording(title="Totally Different")], "Blue Monday", "New Order", 449.0))
        self.assertIsNone(me.pick_match([recording(artist="Someone Else")], "Blue Monday", "New Order", 449.0))

    def test_an_unknown_artist_does_not_block_a_match_but_a_wrong_one_does(self):
        self.assertIsNotNone(me.pick_match([recording()], "Blue Monday", "Unknown Artist", 449.0))
        self.assertIsNotNone(me.pick_match([recording(artist="New Order feat. Someone")], "Blue Monday", "New Order", 449.0))

    def test_a_missing_length_on_either_side_does_not_reject(self):
        self.assertIsNotNone(me.pick_match([recording(length=None)], "Blue Monday", "New Order", 449.0))
        self.assertIsNotNone(me.pick_match([recording()], "Blue Monday", "New Order", 0))

    def test_releases_prefer_official_albums_then_the_earliest(self):
        releases = [
            {"id": "a", "date": "2010", "status": "Official", "release-group": {"primary-type": "Album"}},
            {"id": "b", "date": "1999", "status": "Official", "release-group": {"primary-type": "Album"}},
            {"id": "c", "date": "1980", "status": "Bootleg", "release-group": {"primary-type": "Album"}},
        ]
        self.assertEqual(me.choose_release(releases)["id"], "b")
        self.assertIsNone(me.choose_release([]))


class TestProposals(unittest.TestCase):
    def match(self):
        return me.pick_match([recording()], "Blue Monday", "New Order", 449.0)

    def test_gaps_are_preselected_and_existing_values_are_only_offered(self):
        row = {"path": "x.mp3", "filename": "x.mp3", "title": "Blue Monday", "artist": "Unknown Artist", "album": "Old Album Name",
               "year": "", "track_number": 0, "has_artwork": 1}
        p = me.build_proposal(row, self.match())
        fields = {f["field"]: f for f in p["fields"]}
        self.assertNotIn("title", fields)                                    # already right: not even offered
        self.assertTrue(fields["artist"]["selected"] and fields["artist"]["fills_gap"])
        self.assertTrue(fields["year"]["selected"])
        self.assertEqual((fields["year"]["proposed"], fields["track_number"]["proposed"]), ("1983", "7"))
        self.assertFalse(fields["album"]["selected"])                        # would overwrite a real value
        self.assertFalse(fields["album"]["fills_gap"])
        self.assertIsNone(p["art"])                                          # it already has artwork

    def test_artwork_is_offered_only_when_missing_and_actually_available(self):
        row = {"path": "x.mp3", "title": "Blue Monday", "artist": "New Order", "album": "Power, Corruption & Lies", "year": "1983",
               "track_number": 7, "has_artwork": 0}
        with patch.object(MusicBrainzClient, "cover_available", return_value=True):
            self.assertTrue(me.build_proposal(row, self.match())["art"]["available"])
        with patch.object(MusicBrainzClient, "cover_available", return_value=False):
            self.assertIsNone(me.build_proposal(row, self.match()))          # nothing to fix and no art: not worth showing


class TestClient(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(MusicBrainzClient, "min_interval", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def reply(self, status=200, payload=None):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = payload if payload is not None else {"recordings": [recording()]}
        return r

    def test_the_query_is_quoted_escaped_and_identifies_the_app(self):
        with patch.object(me.http_client, "get", return_value=self.reply()) as get:
            result = MusicBrainzClient.search_recordings('He said "hi"', "Band")
        self.assertEqual(len(result), 1)
        params = get.call_args.kwargs["params"]
        self.assertIn('recording:"He said \\"hi\\""', params["query"])
        self.assertIn('artist:"Band"', params["query"])
        self.assertIn("SpotifyDownloaderStudio", get.call_args.kwargs["headers"]["User-Agent"])

    def test_outages_become_a_clear_error_instead_of_a_stack_trace(self):
        with patch.object(me.http_client, "get", return_value=self.reply(503)), self.assertRaises(LookupUnavailable) as ctx:
            MusicBrainzClient.search_recordings("a")
        self.assertIn("503", str(ctx.exception))
        with patch.object(me.http_client, "get", side_effect=requests.ConnectionError("down")), self.assertRaises(LookupUnavailable):
            MusicBrainzClient.search_recordings("a")

    def test_requests_are_spaced_out(self):
        with patch.object(MusicBrainzClient, "min_interval", 5.0), patch.object(me.time, "sleep") as sleep, \
                patch.object(me.http_client, "get", return_value=self.reply()):
            MusicBrainzClient._last_call = time.monotonic()
            MusicBrainzClient.search_recordings("a")
        self.assertTrue(sleep.called)
        self.assertGreater(sleep.call_args.args[0], 1.0)

    def test_cover_ids_must_be_uuids_so_nothing_else_can_be_requested(self):
        with patch.object(me.http_client, "get") as get, patch.object(me.http_client, "head") as head:
            self.assertFalse(MusicBrainzClient.cover_available("../../etc"))
            self.assertIsNone(MusicBrainzClient.fetch_cover("not-a-uuid"))
        get.assert_not_called()
        head.assert_not_called()

    def test_oversized_or_non_jpeg_covers_are_ignored(self):
        big = MagicMock(status_code=200, content=b"x" * (me.MAX_COVER_BYTES + 1), headers={"Content-Type": "image/jpeg"})
        png = MagicMock(status_code=200, content=b"\x89PNG", headers={"Content-Type": "image/png"})
        ok = MagicMock(status_code=200, content=b"\xff\xd8\xff\xe0jpeg", headers={"Content-Type": "image/jpeg"})
        for response, expected in ((big, None), (png, None), (ok, b"\xff\xd8\xff\xe0jpeg")):
            with patch.object(me.http_client, "get", return_value=response):
                self.assertEqual(MusicBrainzClient.fetch_cover(RELEASE_ID), expected)


class TestJobAndApply(unittest.TestCase):
    TAG = "enrichtest"

    def setUp(self):
        for patcher in (patch.object(MusicBrainzClient, "min_interval", 0),):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.dir = get_downloads_path() / self.TAG
        self.dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self.conn = get_db_connection()
        self.addCleanup(self.clean_rows)
        self.clean_rows()
        self.wav = self.dir / "01 - Blue Monday.wav"
        with wave.open(str(self.wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00\x00" * 4000)
        with self.conn:
            self.conn.execute("""INSERT OR REPLACE INTO library_files (path, filename, folder, title, artist, album, duration_sec, format, size_bytes,
                                 has_artwork, tag_status) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                              (str(self.wav), self.wav.name, str(self.dir), "Blue Monday", "Unknown Artist", "Unknown Album", 0.5, "wav", 8000, 0, "MISSING"))
        with MetadataEnricher._lock:
            MetadataEnricher._state = {"id": None, "running": False, "cancelled": False, "total": 0, "done": 0, "items": [],
                                       "counts": {}, "error": None, "finished_at": None}
        self.client = app.test_client()

    def clean_rows(self):
        with self.conn:
            self.conn.execute("DELETE FROM library_files WHERE path LIKE ?", (f"%{self.TAG}%",))

    def run_job(self, search, filter_type="missing_tags"):
        with patch.object(MusicBrainzClient, "search_recordings", search), patch.object(MusicBrainzClient, "cover_available", return_value=True):
            MetadataEnricher.start(filter_type, 200)
            deadline = time.time() + 10
            while MetadataEnricher.status()["running"] and time.time() < deadline:
                time.sleep(0.05)
        return MetadataEnricher.status()

    def mine(self, status):
        return [i for i in status["items"] if self.TAG in i["path"]]

    def test_a_lookup_proposes_changes_and_applying_writes_tags_and_art(self):
        status = self.run_job(lambda title, artist="": [recording(length=500)])
        self.assertFalse(status["running"])
        self.assertIsNone(status["error"])
        items = self.mine(status)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual({f["field"] for f in item["fields"]}, {"artist", "album", "year", "track_number"})
        self.assertTrue(item["art"]["available"])

        cover = b"\xff\xd8\xff\xe0" + b"0" * 64
        with patch.object(MusicBrainzClient, "fetch_cover", return_value=cover):
            res = MetadataEnricher.apply([{"path": item["path"], "fields": ["artist", "album", "year"], "art": True}])
        self.assertEqual((res["applied"], res["failed"]), (1, []))
        tags = WAVE(str(self.wav)).tags
        self.assertEqual(str(tags["TPE1"]), "New Order")
        self.assertEqual(str(tags["TALB"]), "Power, Corruption & Lies")
        self.assertEqual(str(tags["TDRC"]), "1983")
        self.assertNotIn("TRCK", tags)                                         # a field that was not ticked is not written
        self.assertTrue(any(k.startswith("APIC") for k in tags.keys()))
        row = self.conn.execute("SELECT artist, album FROM library_files WHERE path = ?", (str(self.wav),)).fetchone()
        self.assertEqual((row["artist"], row["album"]), ("New Order", "Power, Corruption & Lies"))
        again = MetadataEnricher.apply([{"path": item["path"], "fields": ["artist"]}])                # already applied: no longer offered
        self.assertEqual(again["applied"], 0)
        self.assertEqual(len(again["failed"]), 1)

    def test_only_what_the_lookup_found_can_be_applied(self):
        res = MetadataEnricher.apply([{"path": str(self.wav), "fields": ["artist"]}])
        self.assertEqual(res["applied"], 0)
        self.assertIn("not part of the last lookup", res["failed"][0]["error"])
        self.assertNotIn("TPE1", WAVE(str(self.wav)).tags or {})

    def test_no_match_is_counted_not_guessed(self):
        status = self.run_job(lambda title, artist="": [recording(title="Something Else Entirely")])
        self.assertEqual(self.mine(status), [])
        self.assertGreaterEqual(status["counts"]["no_match"], 1)

    def test_an_outage_stops_the_run_with_a_message(self):
        def down(title, artist=""):
            raise LookupUnavailable("Cannot reach MusicBrainz.")
        status = self.run_job(down)
        self.assertIn("Cannot reach MusicBrainz", status["error"])
        self.assertFalse(status["running"])

    def test_a_second_run_cannot_start_while_one_is_running_and_it_can_be_cancelled(self):
        gate = []

        def slow(title, artist=""):
            while not gate:
                time.sleep(0.02)
            return []
        with patch.object(MusicBrainzClient, "search_recordings", slow):
            MetadataEnricher.start("missing_tags", 200)
            with self.assertRaises(RuntimeError):
                MetadataEnricher.start("missing_tags", 200)
            self.assertTrue(MetadataEnricher.cancel())
            gate.append(1)
            deadline = time.time() + 10
            while MetadataEnricher.status()["running"] and time.time() < deadline:
                time.sleep(0.05)
        self.assertFalse(MetadataEnricher.status()["running"])

    def test_routes_validate_input(self):
        self.assertEqual(self.client.post("/api/autofix/start", json={"filter": "everything-else"}).status_code, 400)
        self.assertEqual(self.client.post("/api/autofix/apply", json={"selections": []}).status_code, 400)
        outside = self.client.post("/api/autofix/apply", json={"selections": [{"path": "C:\\Windows\\win.ini", "fields": ["title"]}]})
        self.assertEqual(outside.status_code, 403)
        self.assertIn("running", self.client.get("/api/autofix/status").get_json())

    def test_route_flow_start_poll_apply(self):
        with patch.object(MusicBrainzClient, "search_recordings", lambda title, artist="": [recording(length=500)]), \
                patch.object(MusicBrainzClient, "cover_available", return_value=False):
            started = self.client.post("/api/autofix/start", json={"filter": "missing_tags", "limit": 200})
            self.assertEqual(started.status_code, 202)
            deadline = time.time() + 10
            status = self.client.get("/api/autofix/status").get_json()
            while status["running"] and time.time() < deadline:
                time.sleep(0.05)
                status = self.client.get("/api/autofix/status").get_json()
        item = [i for i in status["items"] if self.TAG in i["path"]][0]
        self.assertIsNone(item["art"])                                          # no art available: not offered
        res = self.client.post("/api/autofix/apply", json={"selections": [{"path": item["path"], "fields": ["artist"]}]}).get_json()
        self.assertEqual(res["applied"], 1)


if __name__ == "__main__":
    unittest.main()
