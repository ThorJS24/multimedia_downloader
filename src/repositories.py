"""
Read/write access to the tables the web layer needs, so route handlers contain no SQL.

Repositories return plain `sqlite3.Row` objects / numbers; shaping them into JSON stays in the routes.
All user-supplied text goes through bound parameters, and `LIKE` patterns are escaped so a search for
"100%" or "a_b" means those literal characters rather than wildcards.
"""
import sqlite3
from typing import Iterable, List, Optional, Sequence, Tuple

from src.db import get_db_connection

SQLITE_MAX_VARIABLES = 900   # stay well below SQLite's default limit for IN (...) lists


def like_pattern(text: str) -> str:
    """Contains-style LIKE pattern with %, _ and \\ escaped (use with ESCAPE '\\')."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _chunks(items: Sequence, size: int = SQLITE_MAX_VARIABLES) -> Iterable[Sequence]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


TRACK_DETAIL_COLUMNS = (
    "path, filename, title, artist, album, duration_sec, format, bitrate_kbps, "
    "track_number, disc_number, year, genre, has_artwork"
)


class LibraryRepository:
    # ------------------------------------------------------------------ home shelves
    @staticmethod
    def recently_played(limit: int = 8) -> List[sqlite3.Row]:
        return get_db_connection().execute(
            """SELECT path, title, artist, album, duration_sec, has_artwork, last_played_at, play_count
               FROM library_files
               WHERE last_played_at > 0
               ORDER BY last_played_at DESC LIMIT ?""", (limit,)
        ).fetchall()

    @staticmethod
    def recently_added(limit: int = 8) -> List[sqlite3.Row]:
        return get_db_connection().execute(
            """SELECT path, title, artist, album, duration_sec, has_artwork, last_scanned
               FROM library_files
               ORDER BY last_scanned DESC LIMIT ?""", (limit,)
        ).fetchall()

    @staticmethod
    def top_artists(limit: int = 6) -> List[sqlite3.Row]:
        return get_db_connection().execute(
            """SELECT artist, COUNT(*) as track_count, MIN(path) as sample_path
               FROM library_files
               WHERE artist IS NOT NULL AND artist != ''
               GROUP BY artist
               ORDER BY track_count DESC LIMIT ?""", (limit,)
        ).fetchall()

    @staticmethod
    def top_albums(limit: int = 6) -> List[sqlite3.Row]:
        return get_db_connection().execute(
            """SELECT album, artist, year, COUNT(*) as track_count, MIN(path) as sample_path
               FROM library_files
               WHERE album IS NOT NULL AND album != ''
               GROUP BY album, artist
               ORDER BY track_count DESC LIMIT ?""", (limit,)
        ).fetchall()

    @staticmethod
    def collections() -> List[sqlite3.Row]:
        return get_db_connection().execute(
            "SELECT id, name, type, track_count, cover_url FROM collections ORDER BY name ASC"
        ).fetchall()

    # ------------------------------------------------------------------ detail pages
    @staticmethod
    def album_tracks(album: str, artist: str = "") -> List[sqlite3.Row]:
        """Tracks of an album (case-insensitive); narrowed to `artist` first, falling back to any artist."""
        conn = get_db_connection()
        order = "ORDER BY disc_number ASC, track_number ASC, filename ASC"
        rows: List[sqlite3.Row] = []
        if artist:
            rows = conn.execute(
                f"""SELECT {TRACK_DETAIL_COLUMNS} FROM library_files
                    WHERE (album = ? OR LOWER(album) = LOWER(?)) AND (artist = ? OR LOWER(artist) = LOWER(?))
                    {order}""", (album, album, artist, artist)
            ).fetchall()
        if not rows:
            rows = conn.execute(
                f"""SELECT {TRACK_DETAIL_COLUMNS} FROM library_files
                    WHERE album = ? OR LOWER(album) = LOWER(?)
                    {order}""", (album, album)
            ).fetchall()
        return rows

    @staticmethod
    def artist_albums(artist: str) -> List[sqlite3.Row]:
        return get_db_connection().execute(
            """SELECT album, year, COUNT(*) as track_count, MIN(path) as sample_path
               FROM library_files
               WHERE artist = ? AND album IS NOT NULL AND album != ''
               GROUP BY album
               ORDER BY year DESC, album ASC""", (artist,)
        ).fetchall()

    @staticmethod
    def artist_top_tracks(artist: str, limit: int = 30) -> List[sqlite3.Row]:
        return get_db_connection().execute(
            """SELECT path, filename, title, artist, album, duration_sec, format, bitrate_kbps,
                      year, genre, has_artwork, play_count
               FROM library_files
               WHERE artist = ?
               ORDER BY play_count DESC, title ASC LIMIT ?""", (artist, limit)
        ).fetchall()

    @staticmethod
    def artist_track_count(artist: str) -> int:
        return get_db_connection().execute("SELECT COUNT(*) FROM library_files WHERE artist = ?", (artist,)).fetchone()[0]

    @staticmethod
    def folder_tracks(name: str) -> List[sqlite3.Row]:
        """Tracks whose folder is exactly `name`, or whose path contains it (literally)."""
        return get_db_connection().execute(
            """SELECT path, filename, title, artist, album, duration_sec, format, bitrate_kbps, has_artwork
               FROM library_files
               WHERE folder = ? OR path LIKE ? ESCAPE '\\'
               ORDER BY filename ASC""", (name, like_pattern(name))
        ).fetchall()

    # ------------------------------------------------------------------ search
    @staticmethod
    def search_tracks(query: str, limit: int = 20) -> List[sqlite3.Row]:
        p = like_pattern(query)
        return get_db_connection().execute(
            """SELECT path, filename, title, artist, album, duration_sec, format, bitrate_kbps, has_artwork
               FROM library_files
               WHERE title LIKE ? ESCAPE '\\' OR artist LIKE ? ESCAPE '\\' OR album LIKE ? ESCAPE '\\' OR filename LIKE ? ESCAPE '\\'
               LIMIT ?""", (p, p, p, p, limit)
        ).fetchall()

    @staticmethod
    def search_artists(query: str, limit: int = 8) -> List[sqlite3.Row]:
        return get_db_connection().execute(
            """SELECT artist, COUNT(*) as track_count, MIN(path) as sample_path
               FROM library_files
               WHERE artist LIKE ? ESCAPE '\\' AND artist IS NOT NULL AND artist != ''
               GROUP BY artist
               ORDER BY track_count DESC LIMIT ?""", (like_pattern(query), limit)
        ).fetchall()

    @staticmethod
    def search_albums(query: str, limit: int = 8) -> List[sqlite3.Row]:
        return get_db_connection().execute(
            """SELECT album, artist, year, COUNT(*) as track_count, MIN(path) as sample_path
               FROM library_files
               WHERE album LIKE ? ESCAPE '\\' AND album IS NOT NULL AND album != ''
               GROUP BY album, artist
               ORDER BY track_count DESC LIMIT ?""", (like_pattern(query), limit)
        ).fetchall()

    # ------------------------------------------------------------------ misc reads / writes
    @staticmethod
    def tracks_by_paths(paths: Sequence[str]) -> List[sqlite3.Row]:
        """Look up tracks for any number of paths (IN lists are chunked to respect SQLite's variable limit)."""
        conn = get_db_connection()
        rows: List[sqlite3.Row] = []
        for chunk in _chunks(list(paths)):
            marks = ",".join("?" for _ in chunk)
            rows.extend(conn.execute(
                f"SELECT path, title, artist, album, duration_sec FROM library_files WHERE path IN ({marks})", list(chunk)
            ).fetchall())
        return rows

    @staticmethod
    def record_play(path: str, when: float) -> None:
        conn = get_db_connection()
        with conn:
            conn.execute(
                "UPDATE library_files SET play_count = COALESCE(play_count, 0) + 1, last_played_at = ? WHERE path = ?",
                (when, path),
            )

    @staticmethod
    def summary() -> Tuple[int, int, int, float]:
        """(tracks, distinct artists, distinct albums, total duration in seconds)."""
        conn = get_db_connection()
        return (
            conn.execute("SELECT COUNT(*) FROM library_files").fetchone()[0],
            conn.execute("SELECT COUNT(DISTINCT artist) FROM library_files WHERE artist IS NOT NULL AND artist != ''").fetchone()[0],
            conn.execute("SELECT COUNT(DISTINCT album) FROM library_files WHERE album IS NOT NULL AND album != ''").fetchone()[0],
            conn.execute("SELECT SUM(duration_sec) FROM library_files").fetchone()[0] or 0,
        )

    @staticmethod
    def recently_indexed(limit: int = 12) -> List[sqlite3.Row]:
        return get_db_connection().execute(
            "SELECT path, title, artist, album, duration_sec, has_artwork, last_scanned FROM library_files "
            "ORDER BY last_scanned DESC LIMIT ?", (limit,)
        ).fetchall()

    @staticmethod
    def count() -> int:
        return get_db_connection().execute("SELECT COUNT(*) FROM library_files").fetchone()[0]


class JobRepository:
    FINISHED = ("COMPLETED", "FAILED", "CANCELLED", "SKIPPED")

    @staticmethod
    def completed_count() -> int:
        return get_db_connection().execute("SELECT COUNT(*) FROM jobs WHERE status = 'COMPLETED'").fetchone()[0]

    @staticmethod
    def history(query: str = "", status: str = "", limit: int = 50, offset: int = 0) -> Tuple[List[sqlite3.Row], int]:
        """Finished downloads, newest first, optionally narrowed by text (title/artist/album/playlist) and one status."""
        where = [f"status IN ({','.join('?' * len(JobRepository.FINISHED))})"]
        args: list = list(JobRepository.FINISHED)
        if status in JobRepository.FINISHED:
            where = ["status = ?"]
            args = [status]
        if query:
            p = like_pattern(query)
            where.append("(title LIKE ? ESCAPE '\\' OR artists LIKE ? ESCAPE '\\' OR album LIKE ? ESCAPE '\\' OR collection_name LIKE ? ESCAPE '\\')")
            args += [p, p, p, p]
        clause = " AND ".join(where)
        conn = get_db_connection()
        total = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {clause}", args).fetchone()[0]
        rows = conn.execute(
            f"""SELECT id, track_id, title, artists, album, duration_ms, cover_url, isrc, year, status, error, output_path,
                       collection_name, added_at, completed_at, provider, provider_item_id, canonical_url
                FROM jobs WHERE {clause}
                ORDER BY COALESCE(completed_at, added_at) DESC, id DESC LIMIT ? OFFSET ?""", args + [limit, offset]
        ).fetchall()
        return rows, total

    @staticmethod
    def get(job_id: str) -> Optional[sqlite3.Row]:
        return get_db_connection().execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


class StagingRepository:
    @staticmethod
    def staged_count() -> int:
        return get_db_connection().execute("SELECT COUNT(*) FROM staging_items WHERE status = 'STAGED'").fetchone()[0]

    @staticmethod
    def get_item(item_id: str) -> Optional[sqlite3.Row]:
        return get_db_connection().execute("SELECT * FROM staging_items WHERE id = ?", (item_id,)).fetchone()

    @staticmethod
    def set_status(item_id: str, status: str) -> None:
        conn = get_db_connection()
        with conn:
            conn.execute("UPDATE staging_items SET status = ? WHERE id = ?", (status, item_id))

    @staticmethod
    def commit_removal(item_id: str, library_path: str) -> None:
        """Atomically drop a library entry whose file disappeared and mark its staging item committed."""
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM library_files WHERE path = ?", (library_path,))
            conn.execute("UPDATE staging_items SET status = 'COMMITTED' WHERE id = ?", (item_id,))


class AuditRepository:
    @staticmethod
    def recent(limit: int = 20) -> List[sqlite3.Row]:
        return get_db_connection().execute(
            "SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
