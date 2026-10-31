"""SpotifyDirectClient: metadata-only path, auth gating, error mapping and the chunk-to-disk writer."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import math
import os
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

from librespot.audio import AbsChunkedInputStream

from src.config_loader import TEMP_DIR
from src.spotify_direct import STREAM_CHUNK_BYTES, SpotifyDirectClient

TRACK_ID = "4cOdK2wGLETKBW3PvgPWqT"   # any valid 22-char base62 id
WORK = TEMP_DIR / f"spotify_direct_{os.getpid()}"


def track_proto():
    return NS(
        name="Proto Song", artist=[NS(name="A1"), NS(name="A2")], number=7, disc_number=2, duration=210000,
        album=NS(name="Proto Album", date=NS(year=2018),
                 cover_group=NS(image=[NS(file_id=bytes.fromhex("deadbeef"))])),
        external_id=[NS(type="upc", id="x"), NS(type="isrc", id="USPROTO1")],
    )


class FakeLibrespotStream(AbsChunkedInputStream):
    """A real librespot chunked stream (same base class and read() logic) fed from memory."""

    def __init__(self, data: bytes):
        self._data = data
        self._n = math.ceil(len(data) / STREAM_CHUNK_BYTES)
        self._bufs = [data[i * STREAM_CHUNK_BYTES:(i + 1) * STREAM_CHUNK_BYTES] for i in range(self._n)]
        self._requested = [False] * self._n
        self._available = [True] * self._n
        super().__init__(retry_on_chunk_error=False)

    def buffer(self):
        return self._bufs

    def size(self):
        return len(self._data)

    def chunks(self):
        return self._n

    def requested_chunks(self):
        return self._requested

    def available_chunks(self):
        return self._available

    def request_chunk_from_stream(self, index):
        pass

    def stream_read_halted(self, chunk, _time):
        pass

    def stream_read_resumed(self, chunk, _time):
        pass


def payload(size: int) -> bytes:
    return bytes((i * 31 + 7) % 256 for i in range(size))


class TestMetadataAndAuth(unittest.TestCase):
    def client(self, authenticated=True, proto=None):
        c = SpotifyDirectClient()
        c.ensure_authenticated = MagicMock(return_value=authenticated)
        c.session = MagicMock()
        c.session.api.return_value.get_metadata_4_track.return_value = proto
        return c

    def test_metadata_only_reads_every_field_without_touching_audio(self):
        c = self.client(proto=track_proto())
        with patch("src.spotify_direct.PlayableContentFeeder") as feeder:
            meta = c.get_track_metadata(f"spotify:track:{TRACK_ID}?si=x")
        feeder.assert_not_called()                                  # no stream was opened
        self.assertEqual(meta["id"], TRACK_ID)
        self.assertEqual((meta["title"], meta["artists"], meta["album"], meta["year"]), ("Proto Song", ["A1", "A2"], "Proto Album", 2018))
        self.assertEqual((meta["track_number"], meta["disc_number"], meta["isrc"], meta["duration_ms"]), (7, 2, "USPROTO1", 210000))
        self.assertEqual(meta["cover_url"], "https://i.scdn.co/image/deadbeef")

    def test_metadata_requires_a_session(self):
        with self.assertRaises(RuntimeError):
            self.client(authenticated=False).get_track_metadata(TRACK_ID)

    def test_missing_metadata_is_an_error(self):
        with self.assertRaises(RuntimeError):
            self.client(proto=None).get_track_metadata(TRACK_ID)

    def test_playlist_extraction_filters_non_track_items(self):
        c = self.client()
        proto = NS(attributes=NS(name="PL"), contents=NS(items=[NS(uri="spotify:track:aaa"), NS(uri="spotify:episode:bbb"), NS(uri="")]))
        c.session.api.return_value.get_playlist.return_value = proto
        with patch("src.spotify_direct.PlaylistId") as pid:
            pid.from_base62.return_value = "pid"
            name, uris = c.extract_playlist_info("37i9dQZF1DXcBWIGoYBM5M")
        self.assertEqual((name, uris), ("PL", ["spotify:track:aaa"]))

    def test_login_failures_return_false_not_raise(self):
        c = SpotifyDirectClient()
        with patch("src.spotify_direct.Session") as sess:
            sess.Builder.return_value.create.side_effect = RuntimeError("bad password")
            self.assertFalse(c.login_with_user_pass("u", "p"))
            self.assertFalse(c.is_logged_in())


class TestDownloadToFile(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.dest = WORK / "track.ogg"
        self.addCleanup(lambda: [p.unlink() for p in WORK.glob("*")])

    def client(self, authenticated=True):
        c = SpotifyDirectClient()
        c.ensure_authenticated = MagicMock(return_value=authenticated)
        c.session = MagicMock()
        c.session.api.return_value.get_metadata_4_track.return_value = track_proto()
        return c

    def loaded(self, stream):
        loaded = MagicMock()
        loaded.input_stream.stream.return_value = stream
        return loaded

    def test_requires_a_session(self):
        with self.assertRaises(RuntimeError):
            self.client(authenticated=False).download_track_to_file(TRACK_ID, self.dest)
        self.assertFalse(self.dest.exists())

    def test_writes_the_stream_to_disk_and_returns_metadata(self):
        data = payload(STREAM_CHUNK_BYTES * 2 + 123)
        with patch("src.spotify_direct.PlayableContentFeeder") as feeder:
            feeder.return_value.load.return_value = self.loaded(FakeLibrespotStream(data))
            meta = self.client().download_track_to_file(TRACK_ID, self.dest, quality="HIGH")
        self.assertEqual(self.dest.read_bytes(), data)
        self.assertEqual((meta["title"], meta["isrc"]), ("Proto Song", "USPROTO1"))
        self.assertFalse(list(WORK.glob("*.part")))

    def test_free_account_errors_become_a_clear_permission_error(self):
        with patch("src.spotify_direct.PlayableContentFeeder") as feeder:
            feeder.return_value.load.side_effect = RuntimeError("403 Restricted")
            with self.assertRaises(PermissionError) as ctx:
                self.client().download_track_to_file(TRACK_ID, self.dest)
        self.assertIn("Premium", str(ctx.exception))
        self.assertFalse(self.dest.exists())

    def test_other_stream_failures_are_wrapped(self):
        with patch("src.spotify_direct.PlayableContentFeeder") as feeder:
            feeder.return_value.load.side_effect = ConnectionError("reset")
            with self.assertRaises(RuntimeError) as ctx:
                self.client().download_track_to_file(TRACK_ID, self.dest)
        self.assertIn("Failed to load stream", str(ctx.exception))


class TestChunkWriter(unittest.TestCase):
    """The chunk-to-disk writer must produce exactly what librespot's own read() produces."""

    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.dest = WORK / "w.ogg"
        self.addCleanup(lambda: [p.unlink() for p in WORK.glob("*")])

    def test_matches_librespot_read_for_awkward_sizes(self):
        C = STREAM_CHUNK_BYTES
        for size in (0, 1, C - 1, C, C + 1, 2 * C, 3 * C + 17, 7 * C + C // 2):
            data = payload(size)
            expected = FakeLibrespotStream(data).read()
            written = SpotifyDirectClient.write_stream_to_file(FakeLibrespotStream(data), self.dest)
            self.assertEqual(self.dest.read_bytes(), expected, f"size {size}")
            self.assertEqual(written, size)
            self.assertEqual(expected, data, f"librespot read() sanity for {size}")   # the fake is faithful

    def test_large_stream_is_never_assembled_in_one_buffer(self):
        data = payload(STREAM_CHUNK_BYTES * 20)
        stream = FakeLibrespotStream(data)
        with patch.object(stream, "read", side_effect=AssertionError("fallback read() must not run")):
            SpotifyDirectClient.write_stream_to_file(stream, self.dest)
        self.assertEqual(self.dest.read_bytes(), data)

    def test_falls_back_to_read_when_the_chunk_walk_fails(self):
        stream = MagicMock()
        stream.size.side_effect = RuntimeError("internals changed")
        stream.read.return_value = b"fallback-bytes"
        self.assertEqual(SpotifyDirectClient.write_stream_to_file(stream, self.dest), 14)
        self.assertEqual(self.dest.read_bytes(), b"fallback-bytes")

    def test_a_short_stream_triggers_the_fallback(self):
        stream = MagicMock()
        stream.size.return_value = STREAM_CHUNK_BYTES * 2
        stream.buffer.return_value = [b"x" * STREAM_CHUNK_BYTES, b"y" * 10]       # second chunk truncated
        stream.read.return_value = b"complete"
        SpotifyDirectClient.write_stream_to_file(stream, self.dest)
        self.assertEqual(self.dest.read_bytes(), b"complete")

    def test_failure_leaves_neither_part_file_nor_destination(self):
        stream = MagicMock()
        stream.size.side_effect = RuntimeError("boom")
        stream.read.side_effect = ConnectionError("network gone")
        with self.assertRaises(ConnectionError):
            SpotifyDirectClient.write_stream_to_file(stream, self.dest)
        self.assertEqual(list(WORK.glob("*")), [])

    def test_existing_destination_is_replaced_atomically(self):
        self.dest.write_bytes(b"old")
        SpotifyDirectClient.write_stream_to_file(FakeLibrespotStream(b"new-data"), self.dest)
        self.assertEqual(self.dest.read_bytes(), b"new-data")


def tearDownModule():
    import shutil
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
