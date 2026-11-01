"""MetadataService: cache TTL, embed-page parsing, web-page enrichment and fallbacks (no network)."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import json
import time
import unittest
from unittest.mock import MagicMock, patch

from src.db import get_db_connection
from src.metadata_service import CACHE_TTL_SECONDS, MetadataService


def embed_page(entity):
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


TRACK_ENTITY = {
    "name": "Song Name", "artists": [{"name": "Artist One"}, {"name": "Artist Two"}], "duration": 201000,
    "isExplicit": True, "coverArt": {"sources": [{"url": "https://img/cover.jpg"}]},
    "releaseDate": {"isoString": "2021-05-07T00:00:00Z"}, "album": {"name": "The Album"},
}
LIST_ENTITY = {
    "name": "My Mix", "description": "desc", "owner": {"name": "someone"},
    "coverArt": {"sources": [{"url": "https://img/mix.jpg"}]},
    "trackList": [
        {"uri": "spotify:track:AAA", "title": "One", "subtitle": "X, Y", "duration": 1000},
        {"uri": "spotify:track:BBB", "title": "Two", "subtitle": "Z", "duration": 2000},
    ],
}


class Base(unittest.TestCase):
    def setUp(self):
        with patch("src.metadata_service.SpotifyApiHelper") as helper, patch("src.metadata_service.SpotifyDirectClient"):
            helper.return_value.has_web_api.return_value = False
            self.svc = MetadataService()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM sync_snapshots WHERE collection_id LIKE 'meta_t_%'")

    def fetch(self, pages):
        """Patch http_client.get_text to return pages by URL substring."""
        def fake(url, **kw):
            for key, body in pages.items():
                if key in url:
                    return body
            raise RuntimeError(f"unexpected URL {url}")
        return patch("src.metadata_service.http_client.get_text", side_effect=fake)


class TestEmbedParsing(Base):
    def test_track(self):
        with self.fetch({"/embed/track/meta_t_1": embed_page(TRACK_ENTITY)}):
            t = self.svc.resolve_item("track", "meta_t_1")
        self.assertEqual(t["title"], "Song Name")
        self.assertEqual(t["artists"], ["Artist One", "Artist Two"])
        self.assertEqual((t["album"], t["year"], t["duration_ms"], t["explicit"]), ("The Album", "2021", 201000, True))
        self.assertEqual(t["cover_url"], "https://img/cover.jpg")
        self.assertEqual(t["spotify_url"], "https://open.spotify.com/track/meta_t_1")

    def test_track_missing_fields_are_enriched_from_the_web_page(self):
        sparse = {"name": "Sparse", "artists": [{"name": "A"}]}
        web = ('<meta property="og:image" content="https://img/hi.jpg">'
               '<meta property="music:release_date" content="1999-01-02">'
               '<meta property="music:album:track" content="4">'
               '<meta property="og:description" content="A · Real Album · Song · 1999">')
        with self.fetch({"/embed/track/meta_t_2": embed_page(sparse), "open.spotify.com/track/meta_t_2": web}):
            t = self.svc.resolve_item("track", "meta_t_2")
        self.assertEqual((t["album"], t["year"], t["track_number"], t["cover_url"]),
                         ("Real Album", "1999", 4, "https://img/hi.jpg"))

    def test_track_without_artists_or_web_page_still_resolves(self):
        with self.fetch({"/embed/track/meta_t_3": embed_page({"name": "Bare"})}):
            t = self.svc.resolve_item("track", "meta_t_3")
        self.assertEqual((t["artists"], t["album"]), (["Unknown Artist"], "Singles"))

    def test_playlist(self):
        with self.fetch({"/embed/playlist/meta_t_p": embed_page(LIST_ENTITY)}):
            p = self.svc.resolve_item("playlist", "meta_t_p")
        self.assertEqual((p["name"], p["owner"], p["total_tracks"]), ("My Mix", "someone", 2))
        first = p["tracks"][0]
        self.assertEqual((first["id"], first["artists"], first["position"]), ("AAA", ["X", "Y"], 1))
        self.assertTrue(first["is_playlist"])
        self.assertEqual(first["collection_name"], "My Mix")

    def test_album_uses_web_page_for_artist_cover_and_year(self):
        web = ('<meta property="og:image" content="https://img/album-hi.jpg">'
               '<meta property="og:description" content="Real Artist · Album · 2010 · 5 songs">'
               '<meta property="music:release_date" content="2010-03-04">'
               '<meta property="music:musician" content="https://open.spotify.com/artist/zzz">')
        entity = dict(LIST_ENTITY, name="The Album", subtitle="Fallback Artist")
        with self.fetch({"/embed/album/meta_t_a": embed_page(entity), "open.spotify.com/album/meta_t_a": web}):
            a = self.svc.resolve_item("album", "meta_t_a")
        self.assertEqual(a["name"], "The Album")
        self.assertEqual(a["total_tracks"], 2)
        self.assertTrue(all(t["is_album"] and t["album"] == "The Album" for t in a["tracks"]))
        self.assertEqual(a["year"], "2010")

    def test_embed_without_data_returns_none(self):
        with self.fetch({"/embed/track/": "<html>no data</html>"}):
            self.assertIsNone(MetadataService._resolve_from_embed("track", "x"))
        with self.fetch({"/embed/track/": embed_page({})}):
            self.assertIsNone(MetadataService._resolve_from_embed("track", "x"))

    def test_network_failure_is_contained(self):
        with patch("src.metadata_service.http_client.get_text", side_effect=RuntimeError("offline")):
            self.assertIsNone(MetadataService._resolve_from_embed("track", "x"))
            self.assertEqual(MetadataService._scrape_track_web_info("x"), {})
            self.assertEqual(MetadataService._scrape_album_web_info("x"), {})


class TestResolution(Base):
    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.svc.resolve_item("podcast", "x")

    def test_cache_hit_avoids_the_network_and_force_refresh_bypasses_it(self):
        with self.fetch({"/embed/track/meta_t_c": embed_page(TRACK_ENTITY)}) as fetch:
            first = self.svc.resolve_item("track", "meta_t_c")
            again = self.svc.resolve_item("track", "meta_t_c")
            calls_after_cache = fetch.call_count
            self.svc.resolve_item("track", "meta_t_c", force_refresh=True)
        self.assertEqual(first, again)
        self.assertEqual(calls_after_cache, 1)
        self.assertGreater(fetch.call_count, calls_after_cache)

    def test_stale_cache_entries_are_refetched(self):
        old_title = dict(TRACK_ENTITY, name="Old Title")
        new_title = dict(TRACK_ENTITY, name="New Title")
        with self.fetch({"/embed/track/meta_t_s": embed_page(old_title)}):
            self.svc.resolve_item("track", "meta_t_s")
        with get_db_connection() as conn:   # age the cache beyond its TTL
            conn.execute("UPDATE sync_snapshots SET created_at = ? WHERE collection_id = 'meta_t_s'",
                         (time.time() - CACHE_TTL_SECONDS["track"] - 10,))
        with self.fetch({"/embed/track/meta_t_s": embed_page(new_title)}):
            self.assertEqual(self.svc.resolve_item("track", "meta_t_s")["title"], "New Title")

    def test_playlists_expire_sooner_than_tracks(self):
        self.assertLess(CACHE_TTL_SECONDS["playlist"], CACHE_TTL_SECONDS["track"])

    def test_web_api_is_preferred_when_configured(self):
        self.svc.web_api = MagicMock()
        self.svc.web_api.has_web_api.return_value = True
        self.svc.web_api.client.return_value.track.return_value = {
            "name": "API Song", "artists": [{"name": "API Artist"}], "track_number": 2, "disc_number": 1,
            "duration_ms": 123, "explicit": False, "external_ids": {"isrc": "USX"},
            "album": {"name": "API Album", "release_date": "2020-01-01", "total_tracks": 9,
                      "images": [{"url": "https://img/api.jpg"}], "artists": [{"name": "API Artist"}]},
        }
        t = self.svc.resolve_item("track", "meta_t_api")
        self.assertEqual((t["title"], t["isrc"], t["year"], t["total_tracks"]), ("API Song", "USX", "2020", 9))

    def test_api_failure_falls_back_to_embed(self):
        self.svc.web_api = MagicMock()
        self.svc.web_api.has_web_api.return_value = True
        self.svc.web_api.client.return_value.track.side_effect = RuntimeError("403 premium required")
        with self.fetch({"/embed/track/meta_t_fb": embed_page(TRACK_ENTITY)}):
            self.assertEqual(self.svc.resolve_item("track", "meta_t_fb")["title"], "Song Name")

    def test_direct_fallback_reads_metadata_without_downloading_audio(self):
        self.svc.direct_client = MagicMock()
        self.svc.direct_client.get_track_metadata.return_value = {
            "title": "Direct Song", "artists": ["Direct Artist"], "album": "DA", "year": 2019, "track_number": 5,
            "duration_ms": 1234, "isrc": "USD", "cover_url": "https://i.scdn.co/image/abc"}
        with patch("src.metadata_service.http_client.get_text", side_effect=RuntimeError("offline")):
            t = self.svc.resolve_item("track", "meta_t_direct")
        self.assertEqual((t["title"], t["artists"], t["isrc"]), ("Direct Song", ["Direct Artist"], "USD"))
        self.svc.direct_client.get_track_metadata.assert_called_once_with("meta_t_direct")
        self.svc.direct_client.download_track_to_file.assert_not_called()   # never rip a whole track to read its tags

    def test_unresolvable_track_raises_a_clear_error(self):
        self.svc.direct_client = MagicMock()
        self.svc.direct_client.get_track_metadata.side_effect = RuntimeError("no auth")
        with patch("src.metadata_service.http_client.get_text", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError) as ctx:
                self.svc.resolve_item("track", "meta_t_dead")
        self.assertIn("Could not resolve track", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
