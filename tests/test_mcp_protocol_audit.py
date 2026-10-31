"""
Phase 29.2 Protocol Handshake and Client Compatibility Verification Suite
Spawns its own MCP SSE server on a free port and runs real JSON-RPC calls against it
"""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401

import json
import sys
import unittest
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from src.db import get_db_connection

MCP_SSE_URL = ""
_server_proc = None


def _free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def setUpModule():
    """Start an isolated MCP server (same hermetic data dir) so the suite is self-contained."""
    global MCP_SSE_URL, _server_proc
    import os
    import socket
    import subprocess
    import time

    import atexit
    port = _free_port()
    _server_proc = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "main.py"), "mcp", "--transport", "sse", "--port", str(port)],
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    atexit.register(lambda p=_server_proc: p.poll() is None and p.kill())   # even if the runner crashes
    deadline = time.time() + 30
    while time.time() < deadline:
        if _server_proc.poll() is not None:
            raise RuntimeError("MCP server exited during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    else:
        _server_proc.kill()
        raise RuntimeError("MCP server did not start within 30s")
    MCP_SSE_URL = f"http://127.0.0.1:{port}/sse"


def tearDownModule():
    if _server_proc is not None:
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=10)
        except Exception:
            _server_proc.kill()


class TestMCPProtocolAudit(unittest.IsolatedAsyncioTestCase):
    """Protocol-level audit of the running MCP server."""

    async def asyncSetUp(self):
        # Record database state to verify immutability
        conn = get_db_connection()
        self.initial_jobs_count = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
        self.initial_library_count = conn.execute("SELECT count(*) FROM library_files").fetchone()[0]

    async def asyncTearDown(self):
        # Verify no database state was mutated
        conn = get_db_connection()
        jobs_count = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
        library_count = conn.execute("SELECT count(*) FROM library_files").fetchone()[0]
        self.assertEqual(self.initial_jobs_count, jobs_count, "Database jobs table was mutated by diagnostic tool!")
        self.assertEqual(self.initial_library_count, library_count, "Database library table was mutated by diagnostic tool!")

    async def test_01_protocol_handshake_and_init(self):
        """Verify full MCP JSON-RPC 2.0 handshake, version negotiation, and session initialization."""
        async with sse_client(MCP_SSE_URL) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                init_result = await session.initialize()

                # Check protocol version negotiation
                self.assertIsNotNone(init_result.protocol_version)
                self.assertIn("2025-11-25", init_result.protocol_version)

                # Check server implementation identity
                self.assertEqual(init_result.server_info.name, "spotify-downloader-studio")

                # Check capabilities advertised
                self.assertIsNotNone(init_result.capabilities.tools)

    async def test_02_tools_discovery_and_schema_validation(self):
        """Verify all 6 tools are advertised with valid schemas."""
        async with sse_client(MCP_SSE_URL) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_result = await session.list_tools()

                tool_names = [t.name for t in tools_result.tools]
                expected_tools = [
                    "get_provider_capabilities",
                    "get_library_summary",
                    "get_queue_status",
                    "search_music",
                    "resolve_music_url",
                    "find_cross_source_alternatives"
                ]
                for exp in expected_tools:
                    self.assertIn(exp, tool_names, f"Expected tool {exp} missing from MCP tools/list")

                # Inspect search_music schema
                search_tool = next(t for t in tools_result.tools if t.name == "search_music")
                self.assertIn("query", search_tool.input_schema.get("properties", {}))
                self.assertIn("scope", search_tool.input_schema.get("properties", {}))

    async def test_03_invoke_get_provider_capabilities(self):
        """Invoke get_provider_capabilities over the MCP protocol wire."""
        async with sse_client(MCP_SSE_URL) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                res = await session.call_tool("get_provider_capabilities", {})

                # Result content contains JSON string representation
                self.assertFalse(res.is_error)
                self.assertTrue(len(res.content) > 0)
                data = json.loads(res.content[0].text)
                self.assertIn("spotify", data)
                self.assertIn("ytmusic", data)
                self.assertIn("youtube", data)
                self.assertIn("local", data)
                self.assertFalse(data["spotify"]["can_download"], "Spotify must not claim can_download=True by default")

    async def test_04_invoke_get_library_summary(self):
        """Invoke get_library_summary over the MCP protocol wire and verify path redaction."""
        async with sse_client(MCP_SSE_URL) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                res = await session.call_tool("get_library_summary", {})

                self.assertFalse(res.is_error)
                data = json.loads(res.content[0].text)
                self.assertIn("total_tracks", data)
                self.assertIn("downloads_location", data)
                self.assertEqual(data["downloads_location"], "PROJECT_ROOT/downloads", "Host filesystem path leaked!")
                self.assertNotIn("D:", data["downloads_location"])
                self.assertNotIn("C:", data["downloads_location"])

    async def test_05_invoke_get_queue_status(self):
        """Invoke get_queue_status over the MCP protocol wire."""
        async with sse_client(MCP_SSE_URL) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                res = await session.call_tool("get_queue_status", {})

                self.assertFalse(res.is_error)
                data = json.loads(res.content[0].text)
                self.assertIn("counts", data)
                self.assertIn("recent_jobs", data)

    async def test_06_invoke_search_music_and_scope_isolation(self):
        """Invoke search_music over MCP protocol across different scopes."""
        async with sse_client(MCP_SSE_URL) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                # Scope: local
                res_local = await session.call_tool("search_music", {"query": "Bohemian", "scope": "local", "limit": 5})
                self.assertFalse(res_local.is_error)
                data_local = json.loads(res_local.content[0].text)
                self.assertIn("results", data_local)
                for item in data_local["results"]:
                    self.assertEqual(item["provider"], "local")

                # Scope: youtube
                res_yt = await session.call_tool("search_music", {"query": "Bohemian", "scope": "youtube", "limit": 2})
                self.assertFalse(res_yt.is_error)
                data_yt = json.loads(res_yt.content[0].text)
                for item in data_yt["results"]:
                    self.assertEqual(item["provider"], "youtube")

    async def test_07_invoke_resolve_music_url(self):
        """Invoke resolve_music_url over MCP protocol for Spotify and YouTube."""
        async with sse_client(MCP_SSE_URL) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                # Spotify Track URL
                res = await session.call_tool("resolve_music_url", {"url": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"})
                self.assertFalse(res.is_error)
                data = json.loads(res.content[0].text)
                self.assertTrue(data["success"])
                self.assertEqual(data["provider"], "spotify")
                self.assertEqual(data["data"]["primary_creator"], "Rick Astley")
                self.assertEqual(data["data"]["title"], "Never Gonna Give You Up")

    async def test_08_invoke_find_cross_source_alternatives(self):
        """Invoke find_cross_source_alternatives over MCP protocol."""
        async with sse_client(MCP_SSE_URL) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                res = await session.call_tool("find_cross_source_alternatives", {
                    "title": "Hotel California",
                    "artist": "Eagles",
                    "duration_ms": 391000
                })
                self.assertFalse(res.is_error)
                data = json.loads(res.content[0].text)
                self.assertIn("candidates", data)
                self.assertGreaterEqual(data["candidates_count"], 1)

    async def test_09_negative_unknown_tool(self):
        """Calling non-existent tool must return proper MCP JSON-RPC tool error."""
        async with sse_client(MCP_SSE_URL) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                res = await session.call_tool("non_existent_tool_12345", {})
                self.assertTrue(res.is_error)
                self.assertTrue(len(res.content) > 0)
                self.assertIn("Unknown tool", res.content[0].text)

    async def test_10_negative_invalid_url_resolution(self):
        """Resolving an unsupported URL must fail safely without crashing."""
        async with sse_client(MCP_SSE_URL) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                res = await session.call_tool("resolve_music_url", {"url": "https://not-a-supported-service.com/test"})
                self.assertFalse(res.is_error)
                data = json.loads(res.content[0].text)
                self.assertFalse(data["success"])
                self.assertIn("error", data)

    async def test_11_bounded_limits_enforcement(self):
        """Query with limit > 25 must be safely bounded."""
        async with sse_client(MCP_SSE_URL) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                res = await session.call_tool("search_music", {"query": "test", "scope": "local", "limit": 9999})
                self.assertFalse(res.is_error)
                data = json.loads(res.content[0].text)
                self.assertLessEqual(len(data.get("results", [])), 25)


if __name__ == "__main__":
    unittest.main()
