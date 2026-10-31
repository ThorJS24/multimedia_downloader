import json
import time

from src.db import get_db_connection
from src.metadata_service import MetadataService


class SyncEngine:
    """
    Playlist synchronization engine that compares live Spotify playlists
    against local snapshots, identifies additions, removals, and reordering,
    and supports dry-run previews before downloading missing items.
    """

    def __init__(self):
        self.metadata_service = MetadataService()

    def compare_and_sync(self, playlist_id: str, dry_run: bool = False) -> dict:
        conn = get_db_connection()

        # 1. Fetch current live playlist
        live_data = self.metadata_service.resolve_item("playlist", playlist_id, force_refresh=True)
        live_tracks = live_data.get("tracks", [])
        live_track_ids = [t["id"] for t in live_tracks]

        # 2. Fetch existing snapshot
        cur = conn.execute("SELECT snapshot_json FROM sync_snapshots WHERE collection_id = ?", (playlist_id,))
        row = cur.fetchone()

        added_tracks = []
        removed_tracks = []
        unchanged_tracks = []

        if row:
            try:
                old_snap = json.loads(row["snapshot_json"])
                old_tracks = old_snap.get("tracks", [])
                old_ids = {t["id"] for t in old_tracks}

                # Additions
                for t in live_tracks:
                    if t["id"] not in old_ids:
                        added_tracks.append(t)
                    else:
                        unchanged_tracks.append(t)

                # Removals
                live_ids_set = set(live_track_ids)
                for t in old_tracks:
                    if t["id"] not in live_ids_set:
                        removed_tracks.append(t)
            except Exception:
                added_tracks = live_tracks
        else:
            added_tracks = live_tracks

        # 3. Check which tracks are already downloaded locally in DB
        existing_local_count = 0
        cur_lib = conn.execute("SELECT filename, title, artist FROM library_files")
        lib_rows = cur_lib.fetchall()
        lib_titles = {r["title"].lower() for r in lib_rows}

        for t in added_tracks:
            if t.get("title", "").lower() in lib_titles:
                existing_local_count += 1

        result = {
            "playlist_id": playlist_id,
            "playlist_name": live_data.get("name", playlist_id),
            "total_tracks": len(live_tracks),
            "added_count": len(added_tracks),
            "removed_count": len(removed_tracks),
            "unchanged_count": len(unchanged_tracks),
            "already_local_count": existing_local_count,
            "missing_count": len(added_tracks) - existing_local_count,
            "added_tracks": added_tracks[:50],  # preview top 50
            "removed_tracks": removed_tracks[:50],
            "dry_run": dry_run
        }

        # If not dry run, update snapshot in database
        if not dry_run:
            with conn:
                conn.execute(
                    """
                    INSERT INTO sync_snapshots (collection_id, collection_name, track_count, snapshot_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(collection_id) DO UPDATE SET
                        collection_name = excluded.collection_name,
                        track_count = excluded.track_count,
                        snapshot_json = excluded.snapshot_json,
                        created_at = excluded.created_at
                    """,
                    (playlist_id, live_data.get("name", ""), len(live_tracks), json.dumps(live_data), time.time())
                )

        return result
