"""Database integrity: enforced foreign keys, cascade deletes, and the migration that introduced them."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import sqlite3
import unittest

from src.db import MIGRATIONS, get_db_connection, get_migration_version
from src.scheduler_service import SchedulerService


class TestForeignKeys(unittest.TestCase):
    def setUp(self):
        self.conn = get_db_connection()
        with self.conn:
            self.conn.execute("DELETE FROM watched_folders WHERE id LIKE 'fk_t_%'")
            self.conn.execute("DELETE FROM monitored_artists WHERE id LIKE 'fk_t_%'")

    def test_enforcement_is_on_and_migration_applied(self):
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(get_migration_version(), max(v for v, _, _ in MIGRATIONS))
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_orphan_rows_are_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute("INSERT INTO watcher_events (id, folder_id, file_path, event_type) VALUES ('fk_t_e', 'no_such_folder', 'x', 'CREATED')")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute("INSERT INTO release_calendar (id, artist_id, title) VALUES ('fk_t_r', 'no_such_artist', 'T')")

    def test_removing_an_artist_removes_its_release_calendar(self):
        SchedulerService.add_monitored_artist("fk_t_artist", "Band")
        with self.conn:
            for i in range(3):
                self.conn.execute("INSERT INTO release_calendar (id, artist_id, title) VALUES (?, 'fk_t_artist', 'T')", (f"fk_t_rel{i}",))
        SchedulerService.remove_monitored_artist("fk_t_artist")
        left = self.conn.execute("SELECT COUNT(*) FROM release_calendar WHERE id LIKE 'fk_t_rel%'").fetchone()[0]
        self.assertEqual(left, 0)

    def test_removing_a_watched_folder_removes_its_events(self):
        with self.conn:
            self.conn.execute("INSERT INTO watched_folders (id, path) VALUES ('fk_t_folder', 'C:/fk_t_folder')")
            self.conn.execute("INSERT INTO watcher_events (id, folder_id, file_path, event_type) VALUES ('fk_t_ev', 'fk_t_folder', 'a.mp3', 'CREATED')")
        with self.conn:
            self.conn.execute("DELETE FROM watched_folders WHERE id = 'fk_t_folder'")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM watcher_events WHERE id = 'fk_t_ev'").fetchone()[0], 0)


class TestMigration11(unittest.TestCase):
    """Run the migration against a database shaped like the old schema and containing orphans."""

    OLD = """
        CREATE TABLE watched_folders (id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL);
        CREATE TABLE monitored_artists (id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE watcher_events (id TEXT PRIMARY KEY, folder_id TEXT, file_path TEXT NOT NULL, event_type TEXT NOT NULL,
            file_size INTEGER, mtime REAL, content_hash TEXT, detected_at REAL, status TEXT DEFAULT 'PENDING',
            staging_item_id TEXT, error TEXT);
        CREATE TABLE release_calendar (id TEXT PRIMARY KEY, artist_id TEXT, artist_name TEXT, title TEXT, release_type TEXT,
            release_date TEXT, cover_url TEXT, spotify_url TEXT, status TEXT DEFAULT 'UPCOMING', detected_at REAL);
        CREATE INDEX idx_watcher_folder_id ON watcher_events (folder_id);
        INSERT INTO watched_folders VALUES ('f1', '/a');
        INSERT INTO monitored_artists VALUES ('a1', 'Band');
        INSERT INTO watcher_events (id, folder_id, file_path, event_type)
            VALUES ('e_ok', 'f1', 'x', 'CREATED'), ('e_null', NULL, 'y', 'CREATED'), ('e_orphan', 'gone', 'z', 'CREATED');
        INSERT INTO release_calendar (id, artist_id, title) VALUES ('r_ok', 'a1', 'T'), ('r_orphan', 'gone', 'T');
    """

    def test_keeps_valid_rows_drops_orphans_and_enforces_going_forward(self):
        sql = next(sql for v, _, sql in MIGRATIONS if v == 11)
        db = sqlite3.connect(":memory:")
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(self.OLD)
        db.executescript(sql)
        self.assertEqual({r[0] for r in db.execute("SELECT id FROM watcher_events")}, {"e_ok", "e_null"})
        self.assertEqual({r[0] for r in db.execute("SELECT id FROM release_calendar")}, {"r_ok"})
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO watcher_events (id, folder_id, file_path, event_type) VALUES ('n', 'nope', 'q', 'CREATED')")
        db.execute("DELETE FROM monitored_artists WHERE id = 'a1'")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM release_calendar").fetchone()[0], 0)
        indexes = {r[1] for r in db.execute("PRAGMA index_list(watcher_events)")}
        self.assertTrue({"idx_watcher_folder_id", "idx_watcher_file_path", "idx_watcher_status"} <= indexes)


if __name__ == "__main__":
    unittest.main()
