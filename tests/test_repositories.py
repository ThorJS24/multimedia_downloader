"""Repositories (the only place routes get data from) and the rule that routes contain no SQL."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import ast
import time
import unittest
from pathlib import Path

from src.db import get_db_connection
from src.repositories import (
    AuditRepository,
    JobRepository,
    LibraryRepository,
    StagingRepository,
    like_pattern,
)

ROUTES = Path(__file__).resolve().parent.parent / "src" / "web" / "routes"
PREFIX = "repo_t_"


def add_track(path, title="T", artist="A", album="Al", duration=100.0, folder="", year="2020", track_number=0,
              disc_number=1, play_count=0, last_played_at=0, last_scanned=None, has_artwork=0, genre="g"):
    with get_db_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO library_files
               (path, filename, folder, title, artist, album, duration_sec, format, year, track_number, disc_number,
                play_count, last_played_at, last_scanned, has_artwork, genre, bitrate_kbps)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"{PREFIX}{path}", Path(path).name, folder, title, artist, album, duration, "MP3", year, track_number,
             disc_number, play_count, last_played_at, last_scanned if last_scanned is not None else time.time(), has_artwork, genre, 320),
        )


class Base(unittest.TestCase):
    def setUp(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM library_files")     # these tests own the (hermetic) library table
            conn.execute("DELETE FROM collections WHERE id LIKE ?", (PREFIX + "%",))
            conn.execute("DELETE FROM staging_items WHERE id LIKE ?", (PREFIX + "%",))

    def tearDown(self):
        self.setUp()


class TestLikePattern(unittest.TestCase):
    def test_wildcards_are_escaped(self):
        self.assertEqual(like_pattern("100%"), "%100\\%%")
        self.assertEqual(like_pattern("a_b"), "%a\\_b%")
        self.assertEqual(like_pattern("c:\\x"), "%c:\\\\x%")
        self.assertEqual(like_pattern("plain"), "%plain%")


class TestHomeShelves(Base):
    def test_recently_played_orders_and_limits(self):
        for i in range(10):
            add_track(f"p{i}.mp3", title=f"P{i}", last_played_at=1000 + i)
        add_track("never.mp3", title="Never")
        rows = LibraryRepository.recently_played(limit=8)
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]["title"], "P9")
        self.assertNotIn("Never", [r["title"] for r in rows])        # last_played_at = 0 is excluded

    def test_recently_added_orders_by_scan_time(self):
        add_track("old.mp3", title="Old", last_scanned=1)
        add_track("new.mp3", title="New", last_scanned=999)
        self.assertEqual([r["title"] for r in LibraryRepository.recently_added(2)], ["New", "Old"])

    def test_top_artists_and_albums(self):
        for i in range(3):
            add_track(f"a{i}.mp3", artist="Big", album="Hits")
        add_track("b.mp3", artist="Small", album="One")
        add_track("c.mp3", artist="", album="")
        artists = LibraryRepository.top_artists()
        self.assertEqual((artists[0]["artist"], artists[0]["track_count"]), ("Big", 3))
        self.assertEqual({r["artist"] for r in artists}, {"Big", "Small"})        # blank artist excluded
        albums = LibraryRepository.top_albums()
        self.assertEqual((albums[0]["album"], albums[0]["track_count"]), ("Hits", 3))

    def test_collections_sorted_by_name(self):
        with get_db_connection() as conn:
            conn.execute("INSERT INTO collections (id, name, type) VALUES (?, 'Zed', 'playlist')", (PREFIX + "z",))
            conn.execute("INSERT INTO collections (id, name, type) VALUES (?, 'Alpha', 'playlist')", (PREFIX + "a",))
        names = [r["name"] for r in LibraryRepository.collections() if r["id"].startswith(PREFIX)]
        self.assertEqual(names, ["Alpha", "Zed"])


class TestDetailPages(Base):
    def test_album_is_case_insensitive_and_ordered_by_disc_then_track(self):
        add_track("d2t1.mp3", album="Double", artist="X", disc_number=2, track_number=1)
        add_track("d1t2.mp3", album="Double", artist="X", disc_number=1, track_number=2)
        add_track("d1t1.mp3", album="Double", artist="X", disc_number=1, track_number=1)
        rows = LibraryRepository.album_tracks("double", "x")
        self.assertEqual([Path(r["path"]).name.replace(PREFIX, "") for r in rows], ["d1t1.mp3", "d1t2.mp3", "d2t1.mp3"])

    def test_album_falls_back_to_any_artist(self):
        add_track("v.mp3", album="Compilation", artist="Various")
        self.assertEqual(len(LibraryRepository.album_tracks("Compilation", "Somebody Else")), 1)
        self.assertEqual(LibraryRepository.album_tracks("Nope", "Somebody"), [])

    def test_artist_pages(self):
        add_track("1.mp3", artist="Band", album="Old", year="1999", play_count=1)
        add_track("2.mp3", artist="Band", album="New", year="2020", play_count=9, title="Hit")
        add_track("3.mp3", artist="Other", album="X")
        self.assertEqual([r["album"] for r in LibraryRepository.artist_albums("Band")], ["New", "Old"])   # newest first
        self.assertEqual(LibraryRepository.artist_top_tracks("Band")[0]["title"], "Hit")                  # most played first
        self.assertEqual(LibraryRepository.artist_track_count("Band"), 2)

    def test_folder_tracks_treats_the_name_literally(self):
        add_track("Road Trip/1.mp3", folder="Road Trip", title="in")
        add_track("100%/1.mp3", folder="100%", title="percent")
        add_track("unrelated/1.mp3", folder="unrelated", title="out")
        self.assertEqual([r["title"] for r in LibraryRepository.folder_tracks("Road Trip")], ["in"])
        self.assertEqual([r["title"] for r in LibraryRepository.folder_tracks("100%")], ["percent"])    # not "match everything"
        self.assertEqual(LibraryRepository.folder_tracks("%"), [r for r in LibraryRepository.folder_tracks("%")])  # no crash
        self.assertEqual({r["title"] for r in LibraryRepository.folder_tracks("%")}, {"percent"})


class TestSearch(Base):
    def test_search_matches_title_artist_album_and_filename(self):
        add_track("needle_file.mp3", title="x", artist="y", album="z")
        add_track("a.mp3", title="has Needle inside", artist="y", album="z")
        add_track("b.mp3", title="x", artist="Needle Band", album="z")
        add_track("c.mp3", title="x", artist="y", album="The Needle")
        add_track("d.mp3", title="haystack", artist="y", album="z")
        self.assertEqual(len(LibraryRepository.search_tracks("needle")), 4)

    def test_percent_and_underscore_are_literal(self):
        add_track("1.mp3", title="100% Pure")
        add_track("2.mp3", title="Nothing")
        add_track("3.mp3", title="a_b")
        add_track("4.mp3", title="axb")
        self.assertEqual([r["title"] for r in LibraryRepository.search_tracks("%")], ["100% Pure"])
        self.assertEqual([r["title"] for r in LibraryRepository.search_tracks("a_b")], ["a_b"])

    def test_artist_and_album_search(self):
        add_track("1.mp3", artist="The Beatles", album="Abbey Road")
        add_track("2.mp3", artist="The Beatles", album="Abbey Road")
        add_track("3.mp3", artist="Other", album="Else")
        a = LibraryRepository.search_artists("beat")
        self.assertEqual((a[0]["artist"], a[0]["track_count"]), ("The Beatles", 2))
        self.assertEqual(LibraryRepository.search_albums("abbey")[0]["track_count"], 2)
        self.assertEqual(LibraryRepository.search_albums("zzz"), [])

    def test_limit_is_respected(self):
        for i in range(30):
            add_track(f"t{i}.mp3", title=f"song {i}")
        self.assertEqual(len(LibraryRepository.search_tracks("song", limit=5)), 5)


class TestWritesAndLookups(Base):
    def test_tracks_by_paths_handles_thousands_of_paths(self):
        add_track("keep.mp3", title="Keep")
        wanted = [PREFIX + "keep.mp3"] + [f"{PREFIX}missing{i}.mp3" for i in range(3000)]   # > SQLite's variable limit
        rows = LibraryRepository.tracks_by_paths(wanted)
        self.assertEqual([r["title"] for r in rows], ["Keep"])
        self.assertEqual(LibraryRepository.tracks_by_paths([]), [])

    def test_record_play_increments_from_null_and_sets_time(self):
        add_track("p.mp3")
        with get_db_connection() as conn:
            conn.execute("UPDATE library_files SET play_count = NULL WHERE path = ?", (PREFIX + "p.mp3",))
        LibraryRepository.record_play(PREFIX + "p.mp3", 1234.5)
        LibraryRepository.record_play(PREFIX + "p.mp3", 2345.5)
        row = get_db_connection().execute("SELECT play_count, last_played_at FROM library_files WHERE path = ?", (PREFIX + "p.mp3",)).fetchone()
        self.assertEqual((row["play_count"], row["last_played_at"]), (2, 2345.5))

    def test_summary(self):
        add_track("1.mp3", artist="A", album="X", duration=100)
        add_track("2.mp3", artist="A", album="Y", duration=50)
        add_track("3.mp3", artist="B", album="Y", duration=25)
        self.assertEqual(LibraryRepository.summary(), (3, 2, 2, 175))
        self.assertEqual(LibraryRepository.count(), 3)

    def test_summary_of_an_empty_library(self):
        self.assertEqual(LibraryRepository.summary(), (0, 0, 0, 0))

    def test_recently_indexed(self):
        add_track("a.mp3", title="A", last_scanned=1)
        add_track("b.mp3", title="B", last_scanned=2)
        self.assertEqual([r["title"] for r in LibraryRepository.recently_indexed(1)], ["B"])


class TestStagingAuditJobs(Base):
    def add_item(self, name, status="STAGED"):
        with get_db_connection() as conn:
            conn.execute("INSERT INTO staging_items (id, batch_id, track_id, status, target_path) VALUES (?, 'b', 't', ?, ?)",
                         (PREFIX + name, status, PREFIX + name + ".mp3"))
        return PREFIX + name

    def test_counts_get_and_set_status(self):
        base = StagingRepository.staged_count()
        a = self.add_item("a")
        self.add_item("b", status="COMMITTED")
        self.assertEqual(StagingRepository.staged_count(), base + 1)
        self.assertEqual(StagingRepository.get_item(a)["target_path"], PREFIX + "a.mp3")
        self.assertIsNone(StagingRepository.get_item(PREFIX + "ghost"))
        StagingRepository.set_status(a, "FAILED")
        self.assertEqual(StagingRepository.get_item(a)["status"], "FAILED")

    def test_commit_removal_is_applied_together(self):
        item = self.add_item("gone")
        add_track("gone.mp3")
        StagingRepository.commit_removal(item, PREFIX + "gone.mp3")
        self.assertEqual(StagingRepository.get_item(item)["status"], "COMMITTED")
        self.assertEqual(LibraryRepository.count(), 0)

    def test_audit_recent_is_newest_first_and_limited(self):
        with get_db_connection() as conn:
            for i in range(30):
                conn.execute("INSERT INTO audit_logs (timestamp, level, category, message) VALUES (?, 'INFO', 'REPO', ?)", (9e9 + i, f"m{i}"))
        recent = AuditRepository.recent(20)
        self.assertEqual((len(recent), recent[0]["message"]), (20, "m29"))
        with get_db_connection() as conn:
            conn.execute("DELETE FROM audit_logs WHERE category = 'REPO'")

    def test_completed_job_count(self):
        base = JobRepository.completed_count()
        with get_db_connection() as conn:
            conn.execute("INSERT INTO jobs (id, track_id, status) VALUES (?, 't', 'COMPLETED')", (PREFIX + "j1",))
            conn.execute("INSERT INTO jobs (id, track_id, status) VALUES (?, 't', 'FAILED')", (PREFIX + "j2",))
        try:
            self.assertEqual(JobRepository.completed_count(), base + 1)
        finally:
            with get_db_connection() as conn:
                conn.execute("DELETE FROM jobs WHERE id LIKE ?", (PREFIX + "%",))


class TestRoutesContainNoSql(unittest.TestCase):
    """Architecture rule: route modules ask repositories/services for data; they never touch the database."""

    def test_no_route_module_uses_the_database_directly(self):
        offenders = []
        for path in sorted(ROUTES.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "src.db":
                    bad = [a.name for a in node.names if a.name in ("get_db_connection", "get_db_path")]
                    if bad:
                        offenders.append(f"{path.name}: imports {bad}")
                if isinstance(node, ast.Import) and any(a.name == "sqlite3" for a in node.names):
                    offenders.append(f"{path.name}: imports sqlite3")
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("execute", "executescript", "executemany")
                        and node.args and isinstance(node.args[0], (ast.Constant, ast.JoinedStr))):   # a SQL string, not e.g. RulesEngine.execute(actions)
                    offenders.append(f"{path.name}:{node.lineno}: .{node.func.attr}(<sql>)")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
