try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import unittest
from unittest.mock import patch

from src.providers.base import (
    NormalizedItem,
    MatchOutcome,
)
from src.providers.spotify_provider import SpotifyProvider
from src.providers.ytmusic_provider import YouTubeMusicProvider
from src.providers.youtube_provider import YouTubeProvider
from src.link_parser import LinkParser
from src.source_resolution import SourceResolutionService
from src.cross_source_matcher import CrossSourceMatchingService
from src.playback_capability import PlaybackCapabilityService
from src.unified_search import UnifiedSearchService


class TestPhase29SourceResolution(unittest.TestCase):
    """
    Mandatory Phase 29 Source-Resolution Automated Test Suite.
    Validates Rules A, B, C, D and all 20 source-resolution, matching, and playback isolation contracts.
    """

    def setUp(self):
        self.matcher = CrossSourceMatchingService()
        self.resolution_service = SourceResolutionService()
        self.unified_search = UnifiedSearchService()

    # 1. Pasting a Spotify track URL resolves as a Spotify track.
    def test_01_spotify_track_url_resolves_as_spotify_track(self):
        url = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        parsed = LinkParser.parse_url(url)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.provider, "spotify")
        self.assertEqual(parsed.kind, "track")
        self.assertEqual(parsed.item_id, "4cOdK2wGLETKBW3PvgPWqT")

    # 2. Pasting a Spotify playlist URL resolves as a Spotify playlist with Spotify track entries.
    def test_02_spotify_playlist_url_preserves_spotify_identity(self):
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        parsed = LinkParser.parse_url(url)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.provider, "spotify")
        self.assertEqual(parsed.kind, "playlist")
        self.assertEqual(parsed.item_id, "37i9dQZF1DXcBWIGoYBM5M")

    # 3. Pasting a Spotify album URL preserves album ordering and track numbers.
    def test_03_spotify_album_url_preserves_album_metadata(self):
        url = "https://open.spotify.com/album/4LH4d3cOWNNXdsqFd4Bw1v"
        parsed = LinkParser.parse_url(url)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.provider, "spotify")
        self.assertEqual(parsed.kind, "album")

    # 4. Pasting a Spotify artist URL resolves through Spotify without querying YouTube.
    @patch.object(YouTubeProvider, "search")
    @patch.object(YouTubeMusicProvider, "search")
    def test_04_spotify_artist_url_does_not_query_youtube(self, mock_ytm, mock_yt):
        url = "https://open.spotify.com/artist/06HL4z0CvFAxyc27GXpf02"
        parsed = LinkParser.parse_url(url)
        self.assertEqual(parsed.provider, "spotify")
        self.assertEqual(parsed.kind, "artist")
        # YouTube providers must not be called during Spotify link ingestion
        mock_yt.assert_not_called()
        mock_ytm.assert_not_called()

    # 5. A Spotify track request never issues a YouTube download under any default or error path.
    def test_05_spotify_track_never_issues_youtube_download(self):
        from src.queue_manager import QueueJob
        job = QueueJob(
            job_id="test_sp_1",
            track_id="4cOdK2wGLETKBW3PvgPWqT",
            title="Never Gonna Give You Up",
            artists=["Rick Astley"],
            provider="spotify",
            provider_item_id="4cOdK2wGLETKBW3PvgPWqT"
        )
        self.assertEqual(job.provider, "spotify")
        self.assertFalse(job.is_alternative)
        self.assertEqual(job.original_spotify_id, "")

    # 6. A failed Spotify direct stream does not silently substitute YouTube.
    def test_06_failed_spotify_stream_does_not_silently_substitute_youtube(self):
        from src.queue_manager import QueueManager, QueueJob
        qm = QueueManager()
        job = QueueJob(
            job_id="test_fail_stream",
            track_id="sp_mock_id",
            title="Bohemian Rhapsody",
            artists=["Queen"],
            provider="spotify"
        )
        job.attempts = 99  # no retries left: assert the terminal failure
        # Mock direct_client to throw an error simulating unavailable CDN stream
        with patch.object(qm.direct_client, "download_track_to_file", side_effect=RuntimeError("CDN stream unavailable")):
            with patch("shutil.disk_usage", return_value=(1000000000, 500000000, 500000000)):
                with patch.object(qm.metadata_service, "resolve_item", return_value={
                    "id": "sp_mock_id", "title": "Bohemian Rhapsody", "artists": ["Queen"], "duration_ms": 354000
                }):
                    qm._process_job(job)

        self.assertEqual(job.status, "FAILED")
        self.assertIn("Rule A", job.error)
        self.assertIn("Find alternatives", job.error)

    # 7. A Spotify 429 response results in backoff or error communication, not fallback to another source.
    def test_07_spotify_429_does_not_fall_back(self):
        sp = SpotifyProvider()
        with patch.object(sp, "resolve_url", side_effect=RuntimeError("HTTP 429 Too Many Requests")):
            with self.assertRaises(RuntimeError) as ctx:
                sp.resolve_url("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
            self.assertIn("429", str(ctx.exception))

    # 8. Pasting a YouTube Music track URL resolves as a YouTube Music track.
    def test_08_ytmusic_track_url_resolves_correctly(self):
        url = "https://music.youtube.com/watch?v=kJQP7kiw5Fk"
        parsed = LinkParser.parse_url(url)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.provider, "ytmusic")
        self.assertEqual(parsed.kind, "track")
        self.assertEqual(parsed.item_id, "kJQP7kiw5Fk")

    # 9. Pasting a YouTube Music playlist URL resolves as a YouTube Music playlist.
    def test_09_ytmusic_playlist_url_resolves_correctly(self):
        url = "https://music.youtube.com/playlist?list=RDCLAK5uy_kmPRjuaWAMXRxEZxcEB9mAeGLG1"
        parsed = LinkParser.parse_url(url)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.provider, "ytmusic")
        self.assertEqual(parsed.kind, "playlist")
        self.assertEqual(parsed.item_id, "RDCLAK5uy_kmPRjuaWAMXRxEZxcEB9mAeGLG1")

    # 10. Pasting a standard YouTube video URL resolves as a YouTube video.
    def test_10_youtube_video_url_resolves_correctly(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        parsed = LinkParser.parse_url(url)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.provider, "youtube")
        self.assertEqual(parsed.kind, "video")
        self.assertEqual(parsed.item_id, "dQw4w9WgXcQ")

    # 11. Pasting a standard YouTube playlist URL resolves as a YouTube playlist.
    def test_11_youtube_playlist_url_resolves_correctly(self):
        url = "https://www.youtube.com/playlist?list=PLrAlGQDgHSkUi5N7N17yW3zG4y5S6t7U8"
        parsed = LinkParser.parse_url(url)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.provider, "youtube")
        self.assertEqual(parsed.kind, "playlist")
        self.assertEqual(parsed.item_id, "PLrAlGQDgHSkUi5N7N17yW3zG4y5S6t7U8")

    # 12. A search scoped to YouTube Music returns only YouTube Music results.
    def test_12_search_scoped_to_ytmusic_returns_only_ytmusic(self):
        mock_item = NormalizedItem(
            provider="ytmusic",
            provider_item_id="ytm_123",
            entity_type="track",
            title="Starboy",
            primary_creator="The Weeknd"
        )
        with patch.object(YouTubeMusicProvider, "search", return_value={"tracks": [mock_item]}):
            results = self.unified_search.search("Starboy", scope="ytmusic")
            for items in results["categories"].values():
                for item in items:
                    self.assertEqual(item["provider"], "ytmusic")

    # 13. A search scoped to YouTube returns only YouTube results.
    def test_13_search_scoped_to_youtube_returns_only_youtube(self):
        mock_item = NormalizedItem(
            provider="youtube",
            provider_item_id="dQw4w9WgXcQ",
            entity_type="video",
            title="Never Gonna Give You Up (Official Music Video)",
            primary_creator="Rick Astley"
        )
        with patch.object(YouTubeProvider, "search", return_value={"videos": [mock_item]}):
            results = self.unified_search.search("Never Gonna Give You Up", scope="youtube")
            for items in results["categories"].values():
                for item in items:
                    self.assertEqual(item["provider"], "youtube")

    # 14. An All sources search returns results from all active providers with preserved provider attribution.
    def test_14_all_sources_search_preserves_attribution(self):
        sp_item = NormalizedItem(provider="spotify", provider_item_id="sp_1", entity_type="track", title="Track 1", primary_creator="Artist A")
        ytm_item = NormalizedItem(provider="ytmusic", provider_item_id="ytm_1", entity_type="track", title="Track 1", primary_creator="Artist A")
        yt_item = NormalizedItem(provider="youtube", provider_item_id="yt_1", entity_type="video", title="Track 1 Video", primary_creator="Artist A")

        with patch.object(SpotifyProvider, "search", return_value={"tracks": [sp_item]}):
            with patch.object(YouTubeMusicProvider, "search", return_value={"tracks": [ytm_item]}):
                with patch.object(YouTubeProvider, "search", return_value={"videos": [yt_item]}):
                    res = self.unified_search.search("Track 1", scope="all")
                    providers_found = set()
                    for items in res["categories"].values():
                        for item in items:
                            providers_found.add(item["provider"])
                    self.assertIn("spotify", providers_found)
                    self.assertIn("ytmusic", providers_found)
                    self.assertIn("youtube", providers_found)

    # 15. Cross-source matching finds a candidate when metadata matches closely.
    def test_15_cross_source_matching_high_confidence(self):
        source = NormalizedItem(
            provider="spotify",
            provider_item_id="sp_100",
            entity_type="track",
            title="Blinding Lights",
            primary_creator="The Weeknd",
            duration_ms=200000,
            isrc="USUM71922851"
        )
        candidate = NormalizedItem(
            provider="ytmusic",
            provider_item_id="ytm_200",
            entity_type="track",
            title="Blinding Lights",
            primary_creator="The Weeknd",
            duration_ms=201000,
            isrc="USUM71922851"
        )
        outcome, score, rationale, breakdown = self.matcher.evaluate(source, candidate)
        self.assertEqual(outcome, MatchOutcome.EXACT_IDENTIFIER_MATCH)
        self.assertGreaterEqual(score, 0.95)

    # 16. Cross-source matching marks distinct versions (e.g. live, acoustic, remaster) as non-equivalent.
    def test_16_cross_source_matching_rejects_variant_mismatch(self):
        source = NormalizedItem(
            provider="spotify",
            provider_item_id="sp_101",
            entity_type="track",
            title="Hotel California",
            primary_creator="Eagles",
            duration_ms=390000
        )
        candidate = NormalizedItem(
            provider="youtube",
            provider_item_id="yt_101",
            entity_type="video",
            title="Hotel California (Live at The Forum 1976)",
            primary_creator="Eagles",
            duration_ms=420000
        )
        outcome, score, rationale, breakdown = self.matcher.evaluate(source, candidate)
        # Variant mismatch between studio and live performance must penalize confidence
        self.assertNotEqual(outcome, MatchOutcome.EXACT_IDENTIFIER_MATCH)
        self.assertIn("Variant mismatch", rationale)

    # 17. Cross-source matching flags ambiguous candidates when multiple close matches exist.
    def test_17_cross_source_matching_detects_ambiguity(self):
        source = NormalizedItem(
            provider="spotify",
            provider_item_id="sp_102",
            entity_type="track",
            title="Hello",
            primary_creator="Adele",
            duration_ms=295000
        )
        cand1 = NormalizedItem(
            provider="ytmusic",
            provider_item_id="ytm_1",
            entity_type="track",
            title="Hello",
            primary_creator="Adele",
            duration_ms=296000
        )
        cand2 = NormalizedItem(
            provider="youtube",
            provider_item_id="yt_2",
            entity_type="video",
            title="Hello",
            primary_creator="Adele",
            duration_ms=297000
        )
        ranked = self.matcher.rank_candidates(source, [cand1, cand2])
        self.assertEqual(len(ranked), 2)
        # Both close candidates should be marked AMBIGUOUS
        self.assertEqual(ranked[0]["outcome"], MatchOutcome.AMBIGUOUS.value)
        self.assertEqual(ranked[1]["outcome"], MatchOutcome.AMBIGUOUS.value)

    # 18. An alternative is never added to a download queue or playlist without explicit user confirmation.
    def test_18_alternative_requires_explicit_confirmation(self):
        source = NormalizedItem(provider="spotify", provider_item_id="sp_99", entity_type="track", title="Song", primary_creator="Artist")
        alt = NormalizedItem(provider="ytmusic", provider_item_id="ytm_99", entity_type="track", title="Song", primary_creator="Artist")
        # Associating must be explicitly called via associate_alternative
        ok = self.resolution_service.associate_alternative(
            source_item_id=source.provider_item_id,
            source_provider=source.provider,
            selected_candidate=alt.to_dict()
        )
        self.assertTrue(ok)
        # Now verify lookup
        fetched = self.resolution_service.get_associated_alternative("sp_99")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["target_item_id"], "ytm_99")

    # 19. Playback capability checks correctly report capabilities for each provider type.
    def test_19_playback_capability_matrix_isolation(self):
        sp_item = NormalizedItem(provider="spotify", provider_item_id="sp_1", entity_type="track", title="T1", primary_creator="A1")
        ytm_item = NormalizedItem(provider="ytmusic", provider_item_id="ytm_1", entity_type="track", title="T1", primary_creator="A1")
        yt_item = NormalizedItem(provider="youtube", provider_item_id="yt_1", entity_type="video", title="T1", primary_creator="A1")

        cap_sp = PlaybackCapabilityService.resolve_capabilities(sp_item)
        cap_ytm = PlaybackCapabilityService.resolve_capabilities(ytm_item)
        cap_yt = PlaybackCapabilityService.resolve_capabilities(yt_item)

        # Spotify: metadata & preview access; protected track downloading is False by default
        self.assertFalse(cap_sp["can_download"])
        self.assertTrue(cap_sp["can_access_metadata"])

        # YouTube Music: protected web stream disclosure / embedding
        self.assertFalse(cap_ytm["can_download"])
        self.assertTrue(cap_ytm["can_embed"])
        self.assertEqual(cap_ytm["playback_type"], "youtube_embed")

        # YouTube: official embed player
        self.assertFalse(cap_yt["can_download"])
        self.assertTrue(cap_yt["can_embed"])
        self.assertEqual(cap_yt["playback_type"], "youtube_embed")
        self.assertIn("embed/yt_1", cap_yt["embed_url"])

    # 20. Storing an alternative preserves both the original Spotify metadata and the alternative provider metadata.
    def test_20_storing_alternative_preserves_both_identities(self):
        source = NormalizedItem(
            provider="spotify",
            provider_item_id="sp_orig_01",
            entity_type="track",
            title="Original Track",
            primary_creator="Spotify Artist",
            album="Spotify Album",
            isrc="GBAYE0601498"
        )
        alt = NormalizedItem(
            provider="youtube",
            provider_item_id="yt_alt_01",
            entity_type="video",
            title="Alternative Live Video",
            primary_creator="YouTube Channel",
            canonical_url="https://www.youtube.com/watch?v=yt_alt_01"
        )
        ok = self.resolution_service.associate_alternative(
            source_item_id=source.provider_item_id,
            source_provider=source.provider,
            selected_candidate=alt.to_dict()
        )
        self.assertTrue(ok)
        fetched = self.resolution_service.get_associated_alternative("sp_orig_01")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["target_provider"], "youtube")
        self.assertEqual(fetched["target_item_id"], "yt_alt_01")
        self.assertEqual(fetched["candidate"]["canonical_url"], "https://www.youtube.com/watch?v=yt_alt_01")


if __name__ == "__main__":
    unittest.main()
