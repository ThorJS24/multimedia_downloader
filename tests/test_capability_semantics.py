try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import unittest
from src.providers.spotify_provider import SpotifyProvider
from src.providers.base import NormalizedItem
from src.playback_capability import PlaybackCapabilityService


class TestPhase296CapabilitySemantics(unittest.TestCase):
    def setUp(self):
        self.prov = SpotifyProvider()

    def test_spotify_provider_get_capabilities_disables_download(self):
        item = NormalizedItem(
            provider="spotify",
            provider_item_id="track_123",
            entity_type="track",
            title="Test Track",
            primary_creator="Test Artist",
            capabilities={"preview_url": None}
        )
        caps = self.prov.get_capabilities(item)
        self.assertFalse(caps["can_download"])
        self.assertFalse(caps["can_preview"])
        self.assertIn("Protected Spotify full tracks cannot be downloaded directly", caps["reason_unavailable"])

    def test_spotify_preview_available_only_when_url_present(self):
        # Without preview URL
        item_no_prev = NormalizedItem(
            provider="spotify",
            provider_item_id="track_no_prev",
            entity_type="track",
            title="No Preview",
            primary_creator="Artist",
            capabilities={"preview_url": None}
        )
        caps_no = PlaybackCapabilityService.resolve_capabilities(item_no_prev)
        self.assertFalse(caps_no["has_preview"])
        self.assertFalse(caps_no["can_preview"])
        self.assertIsNone(caps_no["preview_url"])

        # With preview URL
        item_with_prev = NormalizedItem(
            provider="spotify",
            provider_item_id="track_with_prev",
            entity_type="track",
            title="With Preview",
            primary_creator="Artist",
            capabilities={"preview_url": "https://p.scdn.co/mp3-preview/test.mp3"}
        )
        caps_with = PlaybackCapabilityService.resolve_capabilities(item_with_prev)
        self.assertTrue(caps_with["has_preview"])
        self.assertTrue(caps_with["can_preview"])
        self.assertEqual(caps_with["preview_url"], "https://p.scdn.co/mp3-preview/test.mp3")
        self.assertEqual(caps_with["playback_type"], "spotify_preview")

    def test_spotify_playback_service_strictly_blocks_download(self):
        item = NormalizedItem(
            provider="spotify",
            provider_item_id="track_auth_test",
            entity_type="track",
            title="Auth Test",
            primary_creator="Artist",
            capabilities={"preview_url": "https://p.scdn.co/mp3-preview/test.mp3"}
        )
        caps = PlaybackCapabilityService.resolve_capabilities(item)
        self.assertFalse(caps["can_download"])
        self.assertEqual(caps["download_auth_basis"], "unsupported")


if __name__ == "__main__":
    unittest.main()
