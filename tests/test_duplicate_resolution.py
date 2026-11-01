"""One-click duplicate resolution: plan, apply as one batch, undo. Real (tiny) audio files, hermetic data dir."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import shutil
import unittest
import wave
from pathlib import Path

from src.config_loader import get_downloads_path
from src.db import get_db_connection
from src.library_intelligence import LibraryIntelligence
from src.web.server import app


def write_wav(path: Path, frames: int = 800) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * frames)


class TestDuplicateResolution(unittest.TestCase):
    TAG = "dedupres"

    def setUp(self):
        self.root = get_downloads_path() / self.TAG
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(get_downloads_path() / "_duplicates_backup", ignore_errors=True))
        self.conn = get_db_connection()
        self.addCleanup(self.clean_rows)
        self.clean_rows()
        self.client = app.test_client()

    def clean_rows(self):
        with self.conn:
            self.conn.execute("DELETE FROM library_files WHERE path LIKE ?", (f"%{self.TAG}%",))
            self.conn.execute("DELETE FROM organize_history WHERE original_path LIKE ?", (f"%{self.TAG}%",))

    def add(self, rel, *, sha="", title="Song", artist="Artist", album="Album", fmt="mp3", bitrate=320, art=1, status="VALID", duration=200.0):
        path = self.root / rel
        write_wav(path)
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO library_files (path, filename, folder, title, artist, album, duration_sec, format, size_bytes,
                       sha256, has_artwork, bitrate_kbps, tag_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(path), path.name, str(path.parent), title, artist, album, duration, fmt, path.stat().st_size, sha, art, bitrate, status))
        return str(path)

    def plan(self, **kw):
        plan = LibraryIntelligence.plan_duplicate_resolution(**kw)
        return [g for g in plan["groups"] if self.TAG in g["keep"]["path"]], plan

    # ------------------------------------------------------------------ planning
    def test_identical_files_keep_the_one_with_the_shortest_path(self):
        a = self.add("a/song.wav", sha="same" * 16, fmt="wav")
        b = self.add("a/deeper/folder/song copy.wav", sha="same" * 16, fmt="wav")
        groups, plan = self.plan(include_probable=False)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["keep"]["path"], a)
        self.assertEqual([r["path"] for r in groups[0]["remove"]], [b])
        self.assertEqual(groups[0]["remove"][0]["reason"], "Identical file")
        self.assertGreater(plan["reclaim_bytes"], 0)

    def test_lossless_beats_lossy_and_the_reason_says_why(self):
        keep = self.add("flac/song.flac", title="Same Song", fmt="flac", bitrate=900)
        drop = self.add("mp3/song.mp3", title="Same Song", fmt="mp3", bitrate=128)
        groups, _ = self.plan()
        self.assertEqual(groups[0]["keep"]["path"], keep)
        self.assertEqual(groups[0]["remove"][0]["path"], drop)
        self.assertIn("Lower quality", groups[0]["remove"][0]["reason"])
        self.assertIn("MP3", groups[0]["remove"][0]["quality"])                   # what it is is shown beside the reason

    def test_a_copy_that_is_better_in_some_way_is_left_for_you_to_decide(self):
        self.add("x/sharp.mp3", title="Tradeoff", bitrate=320, art=0)
        self.add("x/pretty.mp3", title="Tradeoff", bitrate=128, art=1)
        groups, plan = self.plan()
        self.assertEqual(groups, [])                         # neither copy dominates the other: nothing is proposed
        self.assertGreaterEqual(plan["needs_review"], 1)

    def test_a_file_is_never_planned_twice(self):
        self.add("p/one.wav", sha="dup" * 21 + "d", title="Twice", fmt="wav")
        self.add("p/two longer name.wav", sha="dup" * 21 + "d", title="Twice", fmt="wav")
        groups, _ = self.plan()
        paths = [r["path"] for g in groups for r in g["remove"]] + [g["keep"]["path"] for g in groups]
        self.assertEqual(len(paths), len(set(paths)))

    # ------------------------------------------------------------------ apply + undo
    def pairs_from_plan(self, groups):
        return [{"keep": g["keep"]["path"], "remove": r["path"]} for g in groups for r in g["remove"]]

    def test_apply_moves_copies_aside_as_one_batch_and_undo_restores_and_reindexes(self):
        keep = self.add("k/best.wav", sha="u" * 64, fmt="wav", title="Round Trip")
        drop = self.add("k/best other.wav", sha="u" * 64, fmt="wav", title="Round Trip")
        groups, _ = self.plan(include_probable=False)
        res = LibraryIntelligence.apply_duplicate_resolution(self.pairs_from_plan(groups))
        self.assertEqual(res["moved"], 1)
        self.assertTrue(res["batch_id"].startswith("dedup_"))
        self.assertTrue(Path(keep).exists() and not Path(drop).exists())
        self.assertIsNone(self.conn.execute("SELECT 1 FROM library_files WHERE path = ?", (drop,)).fetchone())
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM library_files WHERE path = ?", (keep,)).fetchone())
        self.assertEqual(LibraryIntelligence.last_duplicate_resolution()["batch_id"], res["batch_id"])

        self.assertEqual(LibraryIntelligence.undo_duplicate_resolution(res["batch_id"]), 1)
        self.assertTrue(Path(drop).exists())
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM library_files WHERE path = ?", (drop,)).fetchone())   # back in the library
        last = LibraryIntelligence.last_duplicate_resolution()
        self.assertTrue(last is None or last["batch_id"] != res["batch_id"])                                       # nothing left to undo

    def test_apply_revalidates_and_refuses_pairs_that_are_not_duplicates(self):
        a = self.add("n/a.wav", sha="a" * 64, title="Alpha", fmt="wav")
        b = self.add("n/b.wav", sha="b" * 64, title="Beta", fmt="wav")
        res = LibraryIntelligence.apply_duplicate_resolution([{"keep": a, "remove": b}])
        self.assertEqual(res["moved"], 0)
        self.assertIsNone(res["batch_id"])
        self.assertIn("not duplicates", res["refused"][0]["reason"])
        self.assertTrue(Path(a).exists() and Path(b).exists())

    def test_apply_refuses_a_worse_keeper_even_if_a_client_asks(self):
        worse = self.add("w/worse.mp3", title="Order", bitrate=96, art=0)
        better = self.add("w/better.flac", title="Order", fmt="flac", bitrate=900)
        res = LibraryIntelligence.apply_duplicate_resolution([{"keep": worse, "remove": better}])
        self.assertEqual(res["moved"], 0)
        self.assertTrue(Path(better).exists())

    def test_the_same_file_cannot_be_kept_and_removed(self):
        a = self.add("s/a.wav", sha="s" * 64, fmt="wav")
        b = self.add("s/b.wav", sha="s" * 64, fmt="wav")
        with self.assertRaises(ValueError):
            LibraryIntelligence.apply_duplicate_resolution([{"keep": a, "remove": b}, {"keep": b, "remove": a}])
        with self.assertRaises(ValueError):
            LibraryIntelligence.apply_duplicate_resolution([{"keep": a}])
        self.assertTrue(Path(a).exists() and Path(b).exists())

    def test_only_cleanup_batches_can_be_undone_through_this_door(self):
        with self.assertRaises(ValueError):
            LibraryIntelligence.undo_duplicate_resolution("batch_org_123")

    # ------------------------------------------------------------------ routes
    def test_routes_plan_apply_undo_and_containment(self):
        self.add("r/one.wav", sha="r" * 64, fmt="wav", title="Routes")
        self.add("r/two copy.wav", sha="r" * 64, fmt="wav", title="Routes")
        plan = self.client.get("/api/duplicates/resolve/plan?probable=0").get_json()
        groups = [g for g in plan["groups"] if self.TAG in g["keep"]["path"]]
        self.assertEqual(len(groups), 1)
        outside = self.client.post("/api/duplicates/resolve/apply", json={"pairs": [{"keep": "C:\\Windows\\win.ini", "remove": "C:\\Windows\\system.ini"}]})
        self.assertEqual(outside.status_code, 403)
        self.assertEqual(self.client.post("/api/duplicates/resolve/apply", json={"pairs": []}).status_code, 400)
        applied = self.client.post("/api/duplicates/resolve/apply", json={"pairs": self.pairs_from_plan(groups)}).get_json()
        self.assertTrue(applied["success"])
        self.assertEqual(applied["moved"], 1)
        self.assertEqual(self.client.get("/api/duplicates/resolve/last").get_json()["last"]["batch_id"], applied["batch_id"])
        bad = self.client.post("/api/duplicates/resolve/undo", json={"batch_id": "batch_org_1"})
        self.assertEqual(bad.status_code, 400)
        undone = self.client.post("/api/duplicates/resolve/undo", json={"batch_id": applied["batch_id"]}).get_json()
        self.assertEqual(undone["restored"], 1)


if __name__ == "__main__":
    unittest.main()
