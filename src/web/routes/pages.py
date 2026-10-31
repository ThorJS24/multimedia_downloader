from pathlib import Path
import time

from flask import jsonify, request, Blueprint

from src.config_loader import get_downloads_path
from src.repositories import LibraryRepository
from src.library_indexer import LibraryIndexer
from src.tag_writer import TagWriter
from src.web.state import _is_user_media_path, logger

bp = Blueprint("pages", __name__)


# ================= DEDICATED SPOTIFY PAGE EXPERIENCES =================

@bp.route("/api/home", methods=["GET"])
def get_home_page_data():
    try:
        # 1. Recently Played (from library_files where last_played_at > 0)
        recent_played_rows = LibraryRepository.recently_played()
        recent_played = [
            {
                "path": r["path"],
                "title": r["title"] or Path(r["path"]).stem,
                "artist": r["artist"] or "Unknown Artist",
                "album": r["album"] or "Singles",
                "duration_sec": r["duration_sec"] or 0,
                "has_artwork": bool(r["has_artwork"]),
                "last_played_at": r["last_played_at"],
                "play_count": r["play_count"] or 1
            }
            for r in recent_played_rows
        ]

        # 2. Recently Added Tracks (newest last_scanned / indexed)
        recent_added_rows = LibraryRepository.recently_added()
        recent_added = [
            {
                "path": r["path"],
                "title": r["title"] or Path(r["path"]).stem,
                "artist": r["artist"] or "Unknown Artist",
                "album": r["album"] or "Singles",
                "duration_sec": r["duration_sec"] or 0,
                "has_artwork": bool(r["has_artwork"])
            }
            for r in recent_added_rows
        ]

        # 3. Top Artists with track counts and sample track for artwork
        top_artists_rows = LibraryRepository.top_artists()
        top_artists = [
            {
                "artist": r["artist"],
                "track_count": r["track_count"],
                "sample_path": r["sample_path"]
            }
            for r in top_artists_rows
        ]

        # 4. Top Albums with artist, year, track count, and sample track for artwork
        top_albums_rows = LibraryRepository.top_albums()
        top_albums = [
            {
                "album": r["album"],
                "artist": r["artist"] or "Various Artists",
                "year": r["year"] or "",
                "track_count": r["track_count"],
                "sample_path": r["sample_path"]
            }
            for r in top_albums_rows
        ]

        # 5. Playlists (from collections table and downloaded playlist folders)
        col_rows = LibraryRepository.collections()
        playlists = [
            {
                "id": r["id"],
                "name": r["name"],
                "type": r["type"] or "playlist",
                "track_count": r["track_count"] or 0,
                "cover_url": r["cover_url"] or ""
            }
            for r in col_rows
        ]

        down_dir = get_downloads_path()
        if down_dir.exists():
            for sub in down_dir.iterdir():
                if sub.is_dir() and not sub.name.startswith((".", "_")):
                    audio_count = len([f for f in sub.iterdir() if f.is_file() and f.suffix.lower() in (".mp3", ".flac", ".m4a", ".opus", ".wav")])
                    if audio_count > 0 and not any(p["name"].lower() == sub.name.lower() for p in playlists):
                        first_file = next((str(f) for f in sub.iterdir() if f.is_file() and f.suffix.lower() in (".mp3", ".flac", ".m4a")), "")
                        playlists.append({
                            "id": f"folder_{sub.name}",
                            "name": sub.name,
                            "type": "folder_playlist",
                            "track_count": audio_count,
                            "sample_path": first_file
                        })

        return jsonify({
            "success": True,
            "recently_played": recent_played,
            "recently_added": recent_added,
            "top_artists": top_artists,
            "top_albums": top_albums,
            "playlists": playlists
        })
    except Exception as e:
        logger.error(f"Error fetching home data: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/library/album", methods=["GET"])
def get_album_details():
    album_name = request.args.get("name", "").strip()
    artist_name = request.args.get("artist", "").strip()
    if not album_name:
        return jsonify({"success": False, "error": "Album name required"}), 400

    rows = LibraryRepository.album_tracks(album_name, artist_name)

    if not rows:
        return jsonify({"success": False, "error": f"Album '{album_name}' not found in library"}), 404

    tracks = [
        {
            "path": r["path"],
            "filename": r["filename"],
            "title": r["title"] or Path(r["path"]).stem,
            "artist": r["artist"] or artist_name or "Unknown Artist",
            "album": r["album"] or album_name,
            "duration_sec": r["duration_sec"] or 0,
            "format": r["format"] or "MP3",
            "bitrate_kbps": r["bitrate_kbps"] or 0,
            "track_number": r["track_number"] or 0,
            "disc_number": r["disc_number"] or 1,
            "year": r["year"] or "",
            "genre": r["genre"] or "",
            "has_artwork": bool(r["has_artwork"])
        }
        for r in rows
    ]

    total_dur = sum(t["duration_sec"] for t in tracks)
    year = rows[0]["year"] if rows[0]["year"] else ""
    artist = rows[0]["artist"] if rows[0]["artist"] else (artist_name or "Various Artists")
    sample_path = rows[0]["path"]

    return jsonify({
        "success": True,
        "album": album_name,
        "artist": artist,
        "year": year,
        "track_count": len(tracks),
        "total_duration_sec": total_dur,
        "sample_path": sample_path,
        "tracks": tracks
    })


@bp.route("/api/library/artist", methods=["GET"])
def get_artist_details():
    artist_name = request.args.get("name", "").strip()
    if not artist_name:
        return jsonify({"error": "Artist name required"}), 400

    album_rows = LibraryRepository.artist_albums(artist_name)
    albums = [
        {
            "album": r["album"],
            "year": r["year"] or "",
            "track_count": r["track_count"],
            "sample_path": r["sample_path"]
        }
        for r in album_rows
    ]

    track_rows = LibraryRepository.artist_top_tracks(artist_name)
    tracks = [
        {
            "path": r["path"],
            "filename": r["filename"],
            "title": r["title"] or Path(r["path"]).stem,
            "artist": r["artist"],
            "album": r["album"] or "Singles",
            "duration_sec": r["duration_sec"] or 0,
            "format": r["format"] or "MP3",
            "bitrate_kbps": r["bitrate_kbps"] or 0,
            "has_artwork": bool(r["has_artwork"]),
            "play_count": r["play_count"] or 0
        }
        for r in track_rows
    ]

    total_tracks = LibraryRepository.artist_track_count(artist_name)
    sample_path = track_rows[0]["path"] if track_rows else ""

    return jsonify({
        "success": True,
        "artist": artist_name,
        "total_tracks": total_tracks,
        "album_count": len(albums),
        "sample_path": sample_path,
        "albums": albums,
        "top_tracks": tracks
    })


@bp.route("/api/library/playlist", methods=["GET"])
def get_playlist_details():
    playlist_name = request.args.get("name", "").strip()
    if not playlist_name:
        return jsonify({"error": "Playlist name required"}), 400

    rows = LibraryRepository.folder_tracks(playlist_name)

    tracks = [
        {
            "path": r["path"],
            "filename": r["filename"],
            "title": r["title"] or Path(r["path"]).stem,
            "artist": r["artist"] or "Unknown Artist",
            "album": r["album"] or playlist_name,
            "duration_sec": r["duration_sec"] or 0,
            "format": r["format"] or "MP3",
            "bitrate_kbps": r["bitrate_kbps"] or 0,
            "has_artwork": bool(r["has_artwork"])
        }
        for r in rows
    ]

    total_dur = sum(t["duration_sec"] for t in tracks)
    sample_path = tracks[0]["path"] if tracks else ""

    return jsonify({
        "success": True,
        "name": playlist_name,
        "track_count": len(tracks),
        "total_duration_sec": total_dur,
        "sample_path": sample_path,
        "tracks": tracks
    })


@bp.route("/api/search/omni", methods=["GET"])
def search_omni():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({
            "query": "",
            "top_result": None,
            "tracks": [],
            "artists": [],
            "albums": []
        })

    track_rows = LibraryRepository.search_tracks(q)
    tracks = [
        {
            "path": r["path"],
            "title": r["title"] or Path(r["path"]).stem,
            "artist": r["artist"] or "Unknown Artist",
            "album": r["album"] or "Singles",
            "duration_sec": r["duration_sec"] or 0,
            "format": r["format"] or "MP3",
            "has_artwork": bool(r["has_artwork"])
        }
        for r in track_rows
    ]

    artist_rows = LibraryRepository.search_artists(q)
    artists = [
        {"artist": r["artist"], "track_count": r["track_count"], "sample_path": r["sample_path"]}
        for r in artist_rows
    ]

    album_rows = LibraryRepository.search_albums(q)
    albums = [
        {"album": r["album"], "artist": r["artist"], "year": r["year"], "track_count": r["track_count"], "sample_path": r["sample_path"]}
        for r in album_rows
    ]

    top_result = None
    if artists and q.lower() in artists[0]["artist"].lower():
        top_result = {
            "type": "artist",
            "title": artists[0]["artist"],
            "subtitle": f"Artist • {artists[0]['track_count']} tracks",
            "sample_path": artists[0]["sample_path"]
        }
    elif albums and q.lower() in albums[0]["album"].lower():
        top_result = {
            "type": "album",
            "title": albums[0]["album"],
            "subtitle": f"Album • {albums[0]['artist']}",
            "sample_path": albums[0]["sample_path"],
            "artist": albums[0]["artist"]
        }
    elif tracks:
        top_result = {
            "type": "track",
            "title": tracks[0]["title"],
            "subtitle": f"Song • {tracks[0]['artist']}",
            "path": tracks[0]["path"],
            "artist": tracks[0]["artist"],
            "album": tracks[0]["album"]
        }

    return jsonify({
        "query": q,
        "top_result": top_result,
        "tracks": tracks,
        "artists": artists,
        "albums": albums
    })


@bp.route("/api/player/played", methods=["POST"])
def record_track_played():
    data = request.json or {}
    path_str = data.get("path") or data.get("file_path", "")
    if not path_str:
        return jsonify({"error": "Path required"}), 400

    now = time.time()
    LibraryRepository.record_play(path_str, now)
    return jsonify({"success": True, "played_at": now})


@bp.route("/api/library/export-m3u", methods=["POST"])
def export_m3u():
    data = request.json or {}
    selected_paths = data.get("paths", [])
    playlist_name = data.get("playlist_name", "library").strip()
    playlist_name = "".join(c for c in playlist_name if c.isalnum() or c in ("-", "_", " ")).strip() or "library"

    if selected_paths:
        rows = LibraryRepository.tracks_by_paths(selected_paths)
        files = [
            {"path": r["path"], "title": r["title"] or Path(r["path"]).stem, "artist": r["artist"] or "Unknown", "duration_sec": r["duration_sec"] or 0}
            for r in rows
        ]
        m3u_path = get_downloads_path() / f"{playlist_name}.m3u"
    else:
        lib_data = LibraryIndexer.get_library(limit=10000)
        files = lib_data.get("files", [])
        m3u_path = get_downloads_path() / "library.m3u"

    LibraryIndexer.export_m3u(m3u_path, files)
    return jsonify({"success": True, "path": str(m3u_path), "track_count": len(files)})


@bp.route("/api/library/batch-tags", methods=["POST"])
def batch_edit_tags():
    data = request.json or {}
    file_paths = data.get("paths", [])
    meta = data.get("metadata", {})

    if not file_paths or not isinstance(file_paths, list):
        return jsonify({"error": "No paths provided", "success": False}), 400

    results = []
    errors = []

    for path_str in file_paths:
        try:
            p = Path(path_str).resolve()
            if not _is_user_media_path(p):
                errors.append({"path": path_str, "error": "Access denied: Path outside project boundary"})
                continue
            if not p.exists():
                errors.append({"path": path_str, "error": "File does not exist"})
                continue

            TagWriter.apply_tags(p, meta)
            LibraryIndexer.index_single_file(p)
            results.append(str(p))
        except Exception as e:
            errors.append({"path": path_str, "error": str(e)})

    return jsonify({
        "success": len(errors) == 0,
        "updated_count": len(results),
        "error_count": len(errors),
        "updated": results,
        "errors": errors
    })


@bp.route("/api/library/batch-reindex", methods=["POST"])
def batch_reindex():
    data = request.json or {}
    file_paths = data.get("paths", [])
    if not file_paths or not isinstance(file_paths, list):
        return jsonify({"error": "No paths provided", "success": False}), 400

    reindexed = []
    errors = []

    for path_str in file_paths:
        try:
            p = Path(path_str).resolve()
            if not _is_user_media_path(p) or not p.exists():
                continue
            LibraryIndexer.index_single_file(p)
            reindexed.append(str(p))
        except Exception as e:
            errors.append({"path": path_str, "error": str(e)})

    return jsonify({
        "success": True,
        "reindexed_count": len(reindexed),
        "reindexed": reindexed,
        "errors": errors
    })


@bp.route("/api/library/export-csv", methods=["POST"])
def export_csv():
    lib_data = LibraryIndexer.get_library(limit=10000)
    csv_path = get_downloads_path() / "library.csv"
    LibraryIndexer.export_csv(csv_path, lib_data.get("files", []))
    return jsonify({"success": True, "path": str(csv_path)})


@bp.route("/api/library/export-audit-csv", methods=["POST", "GET"])
def export_audit_csv():
    down_dir = get_downloads_path()
    out_csv = down_dir / "library_audit_report.csv"
    count = LibraryIndexer.export_audit_csv(out_csv)
    return jsonify({"success": True, "path": str(out_csv), "exported_count": count})


@bp.route("/api/tags/edit", methods=["POST"])
def edit_tags():
    data = request.json or {}
    file_path_str = data.get("path")
    meta = data.get("metadata", {})

    if not file_path_str:
        return jsonify({"error": "Path missing"}), 400

    p = Path(file_path_str).resolve()
    if not _is_user_media_path(p):
        return jsonify({"error": "Access denied: Path outside project boundary"}), 403

    if not p.exists():
        return jsonify({"error": "File does not exist"}), 404

    TagWriter.apply_tags(p, meta)
    # Re-index this single file
    LibraryIndexer.index_single_file(p)
    return jsonify({"success": True, "message": "Tags updated"})
