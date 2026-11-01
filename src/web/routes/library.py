from typing import Any
from pathlib import Path

from flask import Response, jsonify, request, Blueprint

from src.config_loader import get_downloads_path, PROJECT_ROOT
from src.repositories import LibraryRepository
from src.library_indexer import LibraryIndexer
from src.library_intelligence import LibraryIntelligence
from src.metadata_enricher import MetadataEnricher
from src.web.state import _is_user_media_path, logger

bp = Blueprint("library", __name__)


# ================= LOCAL LIBRARY & AUDIO PLAYER =================
@bp.route("/api/library", methods=["GET"])
def get_library():
    search = request.args.get("search", "").strip()
    filter_type = request.args.get("filter", "all").strip()
    sort_by = request.args.get("sort_by", "last_scanned").strip()
    sort_dir = request.args.get("sort_dir", "desc").strip()
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    return jsonify(LibraryIndexer.get_library(
        search=search,
        filter_type=filter_type,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset
    ))


@bp.route("/api/library/scan", methods=["POST"])
def scan_library():
    down_dir = get_downloads_path()
    scanned = LibraryIndexer.scan_directory(down_dir)
    return jsonify({"success": True, "scanned_count": scanned})


@bp.route("/api/library/scan/start", methods=["POST"])
def start_library_scan():
    down_dir = get_downloads_path()
    scan_id = LibraryIndexer.start_background_scan(down_dir)
    return jsonify({"success": True, "scan_id": scan_id})


@bp.route("/api/library/scan/status", methods=["GET"])
def get_library_scan_status():
    return jsonify(LibraryIndexer.get_scan_status())


@bp.route("/api/library/scan/pause", methods=["POST"])
def pause_library_scan():
    return jsonify({"success": LibraryIndexer.pause_scan()})


@bp.route("/api/library/scan/resume", methods=["POST"])
def resume_library_scan():
    return jsonify({"success": LibraryIndexer.resume_scan()})


@bp.route("/api/library/scan/cancel", methods=["POST"])
def cancel_library_scan():
    return jsonify({"success": LibraryIndexer.cancel_scan()})


@bp.route("/api/library/inspect", methods=["GET"])
def inspect_library_file():
    file_path = request.args.get("path", "")
    if not file_path:
        return jsonify({"error": "Path missing"}), 400
    p = Path(file_path)
    if not p.exists():
        return jsonify({"error": "File not found"}), 404
    meta, tech, tag_status, issues = LibraryIndexer.inspect_file_metadata(p)
    return jsonify({
        "path": str(p),
        "filename": p.name,
        "metadata": meta,
        "technical": tech,
        "tag_status": tag_status,
        "issues": issues
    })


@bp.route("/api/library/cover", methods=["GET"])
def get_library_cover():
    raw_path = request.args.get("path", "")
    if not raw_path:
        return jsonify({"error": "Path required"}), 400
    try:
        p = Path(raw_path).resolve()
        down_dir = get_downloads_path().resolve()
        # Verify containment
        if not str(p).startswith(str(down_dir)) and not str(p).startswith(str(PROJECT_ROOT)):
            return jsonify({"error": "Access denied: Path outside library"}), 403
        if not p.exists() or not p.is_file():
            return jsonify({"error": "File not found"}), 404

        # 1. Check for companion cover image in same folder
        for cand in [p.parent / "cover.jpg", p.parent / "folder.jpg", p.parent / "cover.png", p.parent / f"{p.stem}.jpg"]:
            if cand.exists() and cand.is_file():
                mime = "image/png" if cand.suffix.lower() == ".png" else "image/jpeg"
                return Response(cand.read_bytes(), mimetype=mime)

        # 2. Extract embedded artwork via mutagen
        ext = p.suffix.lower()
        if ext == ".mp3":
            from mutagen.id3 import ID3, APIC
            try:
                tags = ID3(p)
                for v in tags.values():
                    if isinstance(v, APIC):
                        apic: Any = v
                        return Response(apic.data, mimetype=apic.mime or "image/jpeg")
            except Exception:
                logger.debug("No ID3 cover art in this file; trying other formats", exc_info=True)
        elif ext in (".flac", ".ogg"):
            from mutagen.flac import FLAC
            try:
                audio = FLAC(p)
                if audio.pictures:
                    pic = audio.pictures[0]
                    mime = pic.mime or "image/jpeg"
                    return Response(pic.data, mimetype=mime)
            except Exception:
                logger.debug("No embedded picture in this file; trying other formats", exc_info=True)
        elif ext in (".m4a", ".mp4", ".aac"):
            from mutagen.mp4 import MP4
            try:
                mp4 = MP4(p)
                covs: list = mp4.tags.get("covr", []) if mp4.tags else []
                if covs:
                    data = covs[0]
                    mime = "image/png" if bytes(data).startswith(b"\x89PNG") else "image/jpeg"
                    return Response(bytes(data), mimetype=mime)
            except Exception:
                logger.debug("No MP4 cover art in this file", exc_info=True)

        return jsonify({"error": "No embedded cover found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/library/lyrics", methods=["GET"])
def get_library_lyrics():
    raw_path = request.args.get("path", "")
    if not raw_path:
        return jsonify({"lyrics": "", "has_lyrics": False}), 400
    try:
        p = Path(raw_path).resolve()
        down_dir = get_downloads_path().resolve()
        if not str(p).startswith(str(down_dir)) and not str(p).startswith(str(PROJECT_ROOT)):
            return jsonify({"error": "Access denied"}), 403
        if not p.exists() or not p.is_file():
            return jsonify({"lyrics": "", "has_lyrics": False}), 404

        # 1. Check for .lrc companion file
        lrc_path = p.with_suffix(".lrc")
        if lrc_path.exists():
            return jsonify({"lyrics": lrc_path.read_text(encoding="utf-8", errors="ignore"), "has_lyrics": True, "source": "lrc_file"})

        # 2. Check embedded USLT frame via mutagen
        ext = p.suffix.lower()
        if ext == ".mp3":
            from mutagen.id3 import ID3, USLT
            try:
                tags = ID3(p)
                for k, v in tags.items():
                    if isinstance(v, USLT) or k.startswith("USLT"):
                        return jsonify({"lyrics": str(v.text), "has_lyrics": True, "source": "id3_embedded"})
            except Exception:
                logger.debug("Reading embedded lyrics failed (ID3)", exc_info=True)
        elif ext in (".flac", ".ogg"):
            from mutagen.flac import FLAC
            try:
                audio = FLAC(p)
                lyrics = audio.get("lyrics", []) or audio.get("unsyncedlyrics", [])
                if lyrics:
                    return jsonify({"lyrics": str(lyrics[0]), "has_lyrics": True, "source": "flac_embedded"})
            except Exception:
                logger.debug("Reading embedded lyrics failed (other formats)", exc_info=True)

        return jsonify({"lyrics": "No synchronized or embedded lyrics found for this track.", "has_lyrics": False})
    except Exception as e:
        return jsonify({"error": str(e), "has_lyrics": False}), 500


@bp.route("/api/library/overview", methods=["GET"])
def get_library_overview():
    try:
        total_tracks, total_artists, total_albums, total_dur = LibraryRepository.summary()

        # Recent 12 tracks
        recent_rows = LibraryRepository.recently_indexed(12)
        recent_tracks = [
            {
                "path": r["path"],
                "title": r["title"] or Path(r["path"]).stem,
                "artist": r["artist"] or "Unknown Artist",
                "album": r["album"] or "Singles",
                "duration_sec": r["duration_sec"] or 0,
                "has_artwork": bool(r["has_artwork"])
            }
            for r in recent_rows
        ]

        return jsonify({
            "success": True,
            "total_tracks": total_tracks,
            "total_artists": total_artists,
            "total_albums": total_albums,
            "total_duration_sec": total_dur,
            "recent_tracks": recent_tracks
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ================= LIBRARY INTELLIGENCE & OPTIMIZATION =================
@bp.route("/api/intelligence/overview", methods=["GET"])
def get_intelligence_overview():
    return jsonify(LibraryIntelligence.get_intelligence_overview())


# ================= DUPLICATE DETECTION & ACTIONS =================
@bp.route("/api/duplicates/exact", methods=["GET"])
def get_exact_duplicates():
    dups = LibraryIntelligence.get_exact_duplicates()
    return jsonify({"duplicates": dups, "count": len(dups)})


@bp.route("/api/duplicates/probable", methods=["GET"])
def get_probable_duplicates():
    dups = LibraryIntelligence.find_probable_recording_duplicates()
    return jsonify({"duplicates": dups, "count": len(dups)})


@bp.route("/api/duplicates/resolve/plan", methods=["GET"])
def plan_duplicate_resolution():
    """What a one-click cleanup would do. Nothing is changed."""
    return jsonify(LibraryIntelligence.plan_duplicate_resolution(include_probable=request.args.get("probable", "1") != "0"))


@bp.route("/api/duplicates/resolve/apply", methods=["POST"])
def apply_duplicate_resolution():
    pairs = (request.json or {}).get("pairs") or []
    if not isinstance(pairs, list) or not pairs:
        return jsonify({"success": False, "error": "Nothing selected"}), 400
    for pair in pairs:
        if not isinstance(pair, dict) or not all(_is_user_media_path(Path(str(pair.get(k, ""))).resolve()) for k in ("keep", "remove")):
            return jsonify({"success": False, "error": "Access denied: path outside project boundary"}), 403
    try:
        res = LibraryIntelligence.apply_duplicate_resolution([{"keep": str(Path(p["keep"]).resolve()), "remove": str(Path(p["remove"]).resolve())} for p in pairs])
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True, **res})


@bp.route("/api/duplicates/resolve/undo", methods=["POST"])
def undo_duplicate_resolution():
    batch_id = ((request.json or {}).get("batch_id") or "").strip()
    try:
        restored = LibraryIntelligence.undo_duplicate_resolution(batch_id)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True, "restored": restored})


@bp.route("/api/duplicates/resolve/last", methods=["GET"])
def last_duplicate_resolution():
    return jsonify({"last": LibraryIntelligence.last_duplicate_resolution()})


@bp.route("/api/duplicates/move-backup", methods=["POST"])
def move_duplicate_backup():
    data = request.json or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"error": "File path missing"}), 400
    p = Path(path).resolve()
    if not _is_user_media_path(p):
        return jsonify({"error": "Access denied: Path outside project boundary"}), 403
    try:
        res = LibraryIntelligence.move_duplicate_to_backup(str(p))
        return jsonify(res)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@bp.route("/api/duplicates/delete", methods=["POST"])
def delete_duplicate_safe():
    data = request.json or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"error": "File path missing"}), 400
    p = Path(path).resolve()
    if not _is_user_media_path(p):
        return jsonify({"error": "Access denied: Path outside project boundary"}), 403
    try:
        success = LibraryIntelligence.delete_duplicate_safely(str(p))
        return jsonify({"success": success})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


# Legacy alias
@bp.route("/api/optimizer/duplicates", methods=["GET"])
def get_optimizer_duplicates():
    dups = LibraryIntelligence.get_exact_duplicates()
    return jsonify({"duplicates": dups, "count": len(dups)})


@bp.route("/api/optimizer/delete-duplicate", methods=["POST"])
def delete_duplicate_file():
    data = request.json or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"error": "File path missing"}), 400
    p = Path(path).resolve()
    if not _is_user_media_path(p):
        return jsonify({"error": "Access denied: Path outside project boundary"}), 403
    try:
        success = LibraryIntelligence.delete_duplicate_safely(str(p))
        return jsonify({"success": success})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@bp.route("/api/optimizer/clean-temp", methods=["POST"])
def clean_temp_buffers():
    res = LibraryIntelligence.cleanup_temp_buffers()
    return jsonify(res)


# ================= METADATA AUDIT & REPAIR WORKFLOW =================
# ---- online lookup (MusicBrainz + Cover Art Archive); proposals stay on the server until applied
@bp.route("/api/autofix/start", methods=["POST"])
def autofix_start():
    data = request.json or {}
    filter_type = data.get("filter", "all")
    if filter_type not in ("all", "missing_tags", "missing_art"):
        return jsonify({"success": False, "error": "Unknown filter"}), 400
    try:
        run_id = MetadataEnricher.start(filter_type, int(data.get("limit", 50)))
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 409
    return jsonify({"success": True, "id": run_id}), 202


@bp.route("/api/autofix/status", methods=["GET"])
def autofix_status():
    return jsonify(MetadataEnricher.status())


@bp.route("/api/autofix/cancel", methods=["POST"])
def autofix_cancel():
    return jsonify({"success": True, "was_running": MetadataEnricher.cancel()})


@bp.route("/api/autofix/apply", methods=["POST"])
def autofix_apply():
    selections = (request.json or {}).get("selections") or []
    if not isinstance(selections, list) or not selections:
        return jsonify({"success": False, "error": "Nothing selected"}), 400
    for sel in selections:
        if not isinstance(sel, dict) or not _is_user_media_path(Path(str(sel.get("path", ""))).resolve()):
            return jsonify({"success": False, "error": "Access denied: path outside project boundary"}), 403
    return jsonify({"success": True, **MetadataEnricher.apply(selections)})


@bp.route("/api/metadata/repair/items", methods=["GET"])
def get_metadata_repair_items():
    filter_type = request.args.get("filter", "all")
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    res = LibraryIntelligence.get_metadata_audit_items(filter_type, limit, offset)
    return jsonify(res)


@bp.route("/api/metadata/repair/propose", methods=["POST"])
def propose_metadata_repair():
    data = request.json or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"error": "Path missing"}), 400
    p = Path(path).resolve()
    if not _is_user_media_path(p):
        return jsonify({"error": "Access denied: Path outside project boundary"}), 403
    try:
        proposal = LibraryIntelligence.generate_proposed_metadata(str(p))
        return jsonify({"success": True, "proposal": proposal})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@bp.route("/api/metadata/repair/apply", methods=["POST"])
def apply_metadata_repair():
    data = request.json or {}
    path = data.get("path", "")
    approved = data.get("approved_fields", {})
    if not path or not approved:
        return jsonify({"error": "Path and approved_fields are required"}), 400
    p = Path(path).resolve()
    if not _is_user_media_path(p):
        return jsonify({"error": "Access denied: Path outside project boundary"}), 403
    try:
        success = LibraryIntelligence.apply_metadata_repair(str(p), approved)
        return jsonify({"success": success})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


# ================= BULK REORGANIZATION =================
@bp.route("/api/organizer/preview", methods=["POST"])
def preview_bulk_organizer():
    data = request.json or {}
    template = data.get("template", "{artist}/{album}/{track_number} - {title}.{ext}")
    collision_strategy = data.get("collision_strategy", "skip")
    filter_format = data.get("filter_format")
    try:
        res = LibraryIntelligence.preview_bulk_organization(template, collision_strategy, filter_format)
        return jsonify({"success": True, "plan": res})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@bp.route("/api/organizer/execute", methods=["POST"])
def execute_bulk_organizer():
    data = request.json or {}
    moves = data.get("moves", [])
    collision_strategy = data.get("collision_strategy", "skip")
    if not moves:
        return jsonify({"error": "No moves provided"}), 400
    try:
        res = LibraryIntelligence.execute_bulk_organization(moves, collision_strategy)
        return jsonify({"success": True, "result": res})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/organizer/rollback", methods=["POST"])
def rollback_bulk_organizer():
    data = request.json or {}
    batch_id = data.get("batch_id", "")
    if not batch_id:
        return jsonify({"error": "batch_id is required"}), 400
    try:
        restored = LibraryIntelligence.rollback_bulk_organization(batch_id)
        return jsonify({"success": True, "restored_count": restored})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
