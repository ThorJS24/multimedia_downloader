try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import unittest
import asyncio

from src.mcp_server.server import mcp


class TestMCPServer(unittest.TestCase):
    """
    Automated test suite verifying MCP Server functionality for ChatGPT / Claude / AI agents.
    """

    def test_01_tool_registration(self):
        tools = asyncio.run(mcp.list_tools())
        tool_names = [t.name for t in tools]
        expected_tools = [
            "search_music",
            "resolve_music_url",
            "find_cross_source_alternatives",
            "get_library_summary",
            "get_queue_status",
            "get_provider_capabilities"
        ]
        for et in expected_tools:
            self.assertIn(et, tool_names, f"Tool '{et}' must be registered on MCP server")

    def test_02_get_library_summary(self):
        res = asyncio.run(mcp.call_tool("get_library_summary", {}))
        self.assertFalse(res.is_error)
        data = res.structured_content["result"]
        self.assertIn("total_tracks", data)
        self.assertIn("downloads_location", data)
        self.assertGreaterEqual(data["total_tracks"], 0)

    def test_03_get_queue_status(self):
        res = asyncio.run(mcp.call_tool("get_queue_status", {}))
        self.assertFalse(res.is_error)
        data = res.structured_content["result"]
        self.assertIn("counts", data)
        self.assertIn("queued", data["counts"])

    def test_04_get_provider_capabilities(self):
        res = asyncio.run(mcp.call_tool("get_provider_capabilities", {}))
        self.assertFalse(res.is_error)
        data = res.structured_content["result"]
        self.assertIn("spotify", data)
        self.assertIn("ytmusic", data)
        self.assertIn("youtube", data)
        self.assertIn("local", data)
        self.assertTrue(data["spotify"]["is_authoritative"])

    def test_05_resolve_spotify_url(self):
        url = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        res = asyncio.run(mcp.call_tool("resolve_music_url", {"url": url}))
        self.assertFalse(res.is_error)
        data = res.structured_content["result"]
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("provider"), "spotify")
        self.assertEqual(data["data"]["title"], "Never Gonna Give You Up")

    def test_06_search_music(self):
        res = asyncio.run(mcp.call_tool("search_music", {"query": "Queen", "scope": "local", "limit": 2}))
        self.assertFalse(res.is_error)
        data = res.structured_content["result"]
        self.assertIn("results", data)

    def test_07_find_cross_source_alternatives(self):
        res = asyncio.run(mcp.call_tool("find_cross_source_alternatives", {
            "title": "Bohemian Rhapsody",
            "artist": "Queen"
        }))
        self.assertFalse(res.is_error)
        data = res.structured_content["result"]
        self.assertIn("candidates", data)


if __name__ == "__main__":
    unittest.main()
