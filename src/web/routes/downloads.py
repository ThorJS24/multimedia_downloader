import io
import json

from flask import Response, jsonify, request, Blueprint

from src.db import record_audit
from src.link_parser import LinkParser
from src.match_engine import MatchCandidate, MatchEngine
from src.staging_service import StagingService
from src.web.state import logger, metadata_service, queue_manager, sync_engine

bp = Blueprint("downloads", __name__)


# ================= LINK PARSING & RESOLUTION =================
@bp.route("/api/links/parse", methods=["POST"])
def parse_links():
    data = request.json or {}
    text = data.get("text", "")
    dedup = data.get("deduplicate", True)
    parsed = LinkParser.parse_multiline(text, deduplicate=dedup)
    return jsonify({"links": [p.to_dict() for p in parsed], "total": len(parsed)})


@bp.route("/api/links/import-file", methods=["POST"])
def import_links_file():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    content = f.read().decode("utf-8", errors="ignore")
    parsed = LinkParser.parse_file_content(content, filename=f.filename)
    return jsonify({"links": [p.to_dict() for p in parsed], "total": len(parsed)})


@bp.route("/api/resolve", methods=["POST"])
def resolve_resource():
    data = request.json or {}
    kind = data.get("kind", "playlist")
    item_id = data.get("id", "").strip()
    refresh = data.get("refresh", False)

    if not item_id:
        return jsonify({"error": "Missing item ID"}), 400

    try:
        data_res = metadata_service.resolve_item(kind, item_id, force_refresh=refresh)
        return jsonify({"success": True, "data": data_res})
    except Exception as e:
        logger.error(f"Failed to resolve {kind} {item_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ================= QUEUE MANAGER =================
@bp.route("/api/queue/snapshot", methods=["GET"])
def get_queue_snapshot():
    # The UI polls this; an ETag lets it skip the body (and re-render) when nothing changed.
    resp = jsonify(queue_manager.get_snapshot())
    resp.add_etag()
    return resp.make_conditional(request)


@bp.route("/api/queue/add", methods=["POST"])
def queue_add():
    data = request.json or {}
    tracks = data.get("tracks", [])
    collection_id = data.get("collection_id", "")
    collection_name = data.get("collection_name", "")

    if not tracks:
        return jsonify({"error": "No tracks provided"}), 400

    added = queue_manager.add_batch(tracks, collection_id=collection_id, collection_name=collection_name)
    record_audit("QUEUE", f"Enqueued {added} tracks from '{collection_name or 'Direct'}'")
    return jsonify({"success": True, "added": added})


@bp.route("/api/queue/pause", methods=["POST"])
def queue_pause():
    queue_manager.pause()
    return jsonify({"success": True})


@bp.route("/api/queue/resume", methods=["POST"])
def queue_resume():
    queue_manager.resume()
    return jsonify({"success": True})


@bp.route("/api/queue/cancel-all", methods=["POST"])
def queue_cancel_all():
    queue_manager.cancel_all()
    return jsonify({"success": True})


@bp.route("/api/queue/cancel-job", methods=["POST"])
def queue_cancel_job():
    data = request.json or {}
    job_id = data.get("job_id")
    if job_id:
        queue_manager.cancel_job(job_id)
        return jsonify({"success": True, "job_id": job_id})
    return jsonify({"error": "job_id is required"}), 400


@bp.route("/api/queue/retry", methods=["POST"])
def queue_retry():
    data = request.json or {}
    job_id = data.get("job_id")
    if job_id:
        queue_manager.retry_job(job_id)
    return jsonify({"success": True})


@bp.route("/api/queue/remove", methods=["POST"])
def queue_remove():
    data = request.json or {}
    job_id = data.get("job_id")
    if job_id:
        queue_manager.remove_job(job_id)
    return jsonify({"success": True})


@bp.route("/api/queue/reorder", methods=["POST"])
def queue_reorder():
    data = request.json or {}
    job_id = data.get("job_id")
    direction = data.get("direction", "up")
    if job_id and "before_id" in data:
        # drag & drop: place the job directly before another one (null = end of the queue)
        moved = queue_manager.move_job(job_id, data.get("before_id") or None)
        return jsonify({"success": True, "moved": moved})
    if job_id:
        queue_manager.reorder_job(job_id, direction)
    return jsonify({"success": True})


@bp.route("/api/queue/clear-finished", methods=["POST"])
def queue_clear_finished():
    queue_manager.clear_finished()
    return jsonify({"success": True})


@bp.route("/api/queue/retry-all-failed", methods=["POST"])
def queue_retry_all_failed():
    count = queue_manager.retry_all_failed()
    return jsonify({"success": True, "count": count})


@bp.route("/api/queue/clear-failed", methods=["POST"])
def queue_clear_failed():
    count = queue_manager.clear_failed()
    return jsonify({"success": True, "count": count})


@bp.route("/api/queue/batch-action", methods=["POST"])
def queue_batch_action():
    data = request.json or {}
    action = data.get("action", "")
    job_ids = data.get("job_ids", [])
    if not job_ids:
        return jsonify({"success": False, "error": "No jobs selected"}), 400

    if action == "retry":
        count = queue_manager.batch_retry(job_ids)
    elif action == "cancel":
        count = queue_manager.batch_cancel(job_ids)
    elif action == "remove":
        count = queue_manager.batch_remove(job_ids)
    elif action == "move_top":
        count = queue_manager.batch_reorder(job_ids, "top")
    elif action == "move_bottom":
        count = queue_manager.batch_reorder(job_ids, "bottom")
    elif action == "update":
        updates = data.get("updates", {})
        count = queue_manager.batch_update(job_ids, updates)
    else:
        return jsonify({"success": False, "error": f"Unknown action: {action}"}), 400

    return jsonify({"success": True, "action": action, "count": count})


@bp.route("/api/queue/update-job", methods=["POST"])
def queue_update_job():
    data = request.json or {}
    job_id = data.get("job_id", "")
    if not job_id:
        return jsonify({"success": False, "error": "job_id required"}), 400

    updated = queue_manager.update_job_metadata(job_id, data)
    if updated:
        return jsonify({"success": True, "job": updated})
    return jsonify({"success": False, "error": "Job not found"}), 404


@bp.route("/api/queue/export", methods=["GET"])
def queue_export():
    fmt = request.args.get("format", "csv").lower()
    snap = queue_manager.get_snapshot()
    jobs = snap.get("jobs", [])

    if fmt == "json":
        return Response(
            json.dumps(jobs, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=queue_export.json"}
        )

    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["#", "ID", "Title", "Artists", "Album", "Collection", "Status", "Stage", "Progress", "Error"])
    for idx, j in enumerate(jobs, 1):
        artists = ", ".join(j.get("artists", [])) if isinstance(j.get("artists"), list) else j.get("artists", "")
        writer.writerow([
            idx,
            j.get("id"),
            j.get("title"),
            artists,
            j.get("album"),
            j.get("collection_name"),
            j.get("status"),
            j.get("stage"),
            f"{j.get('progress')}%",
            j.get("error", "")
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=queue_export.csv"}
    )


@bp.route("/api/queue/concurrency", methods=["POST"])
def queue_concurrency():
    data = request.json or {}
    workers = int(data.get("workers", 1))
    queue_manager.set_concurrency(workers)
    return jsonify({"success": True, "workers": queue_manager.max_workers})


# ================= MATCH ENGINE & CANDIDATE REVIEW =================
@bp.route("/api/match/evaluate", methods=["POST"])
def match_evaluate():
    data = request.json or {}
    expected = data.get("expected", {})
    candidate_data = data.get("candidate", {})

    cand = MatchCandidate(
        candidate_id=candidate_data.get("id", expected.get("id", "")),
        title=candidate_data.get("title", expected.get("title", "")),
        artists=candidate_data.get("artists", expected.get("artists", [])),
        duration_ms=candidate_data.get("duration_ms", expected.get("duration_ms", 0)),
        source=candidate_data.get("source", "Spotify AP CDN"),
        isrc=candidate_data.get("isrc", expected.get("isrc", ""))
    )
    score = MatchEngine.evaluate_match(expected, cand)
    return jsonify({
        "confidence": score,
        "is_acceptable": cand.is_acceptable,
        "candidate": cand.to_dict()
    })


@bp.route("/api/match/explain", methods=["POST"])
def match_explain():
    data = request.json or {}
    expected = data.get("expected", {})
    candidate_data = data.get("candidate", {})
    threshold = float(data.get("threshold", 0.70))

    cand = MatchCandidate(
        candidate_id=candidate_data.get("id", expected.get("id", "")),
        title=candidate_data.get("title", expected.get("title", "")),
        artists=candidate_data.get("artists", expected.get("artists", [])),
        duration_ms=candidate_data.get("duration_ms", expected.get("duration_ms", 0)),
        source=candidate_data.get("source", "Spotify AP CDN"),
        isrc=candidate_data.get("isrc", expected.get("isrc", ""))
    )
    explanation = MatchEngine.explain_match(expected, cand, min_threshold=threshold)
    return jsonify(explanation)


# ================= IMPORT STAGING & DRY-RUN PREVIEW =================
@bp.route("/api/staging/preview", methods=["POST"])
def staging_preview():
    data = request.json or {}
    raw_input = data.get("input", "")
    template = data.get("template")
    output_format = data.get("output_format")
    collision_strategy = data.get("collision_strategy")

    if not raw_input:
        return jsonify({"error": "No input links or search query provided"}), 400

    try:
        plan = StagingService.create_staging_preview(
            raw_input=raw_input,
            template=template,
            output_format=output_format,
            collision_strategy=collision_strategy
        )
        return jsonify({"success": True, "plan": plan})
    except Exception as e:
        logger.error(f"Staging preview failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/staging/commit", methods=["POST"])
def staging_commit():
    data = request.json or {}
    batch_id = data.get("batch_id", "")
    excluded_ids = data.get("excluded_ids", [])
    if not batch_id:
        return jsonify({"error": "batch_id is required"}), 400

    enqueued = StagingService.commit_staging_batch(batch_id, queue_manager, excluded_ids=excluded_ids)
    return jsonify({"success": True, "enqueued": enqueued})


# ================= PLAYLIST SYNCHRONIZATION =================
@bp.route("/api/sync/compare", methods=["POST"])
def sync_compare():
    data = request.json or {}
    playlist_id = data.get("playlist_id", "").strip()
    dry_run = data.get("dry_run", True)

    if not playlist_id:
        return jsonify({"error": "Missing playlist ID"}), 400

    try:
        report = sync_engine.compare_and_sync(playlist_id, dry_run=dry_run)
        return jsonify({"success": True, "report": report})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/sync/apply", methods=["POST"])
def sync_apply():
    data = request.json or {}
    playlist_id = data.get("playlist_id", "").strip()
    if not playlist_id:
        return jsonify({"success": False, "error": "playlist_id is required"}), 400

    report = sync_engine.compare_and_sync(playlist_id, dry_run=False)
    added_tracks = report.get("added_tracks", [])
    if added_tracks:
        enqueued = queue_manager.add_batch(added_tracks, collection_id=playlist_id, collection_name=report.get("playlist_name", "Sync"))
        return jsonify({"success": True, "enqueued": enqueued})
    return jsonify({"success": True, "enqueued": 0, "message": "All tracks already synchronized!"})


# ================= PHASE 29 MULTI-SOURCE & UNIFIED RESOLUTION API =================
@bp.route("/api/search/unified", methods=["GET"])
def search_unified():
    """
    Unified multi-source search across Spotify, YouTube Music, YouTube, and local library.
    Query params:
      q: search query
      scope: 'all', 'spotify', 'ytmusic', 'youtube', 'local'
      entity_type: 'track', 'album', 'artist', 'playlist', 'video', 'all'
      limit: items per provider (default 10)
    """
    from src.unified_search import get_unified_search_service
    q = request.args.get("q", "").strip()
    scope = request.args.get("scope", "all").strip().lower()
    entity_type = request.args.get("entity_type", "all").strip().lower()
    try:
        limit = max(1, min(int(request.args.get("limit", 10)), 50))
    except ValueError:
        limit = 10

    if not q:
        return jsonify({"results": [], "query": "", "scope": scope, "provider_status": {}})

    service = get_unified_search_service()
    res = service.search(query=q, scope=scope, entity_type=entity_type, limit_per_source=limit)
    return jsonify(res)


@bp.route("/api/resolve/unified", methods=["POST"])
def resolve_unified():
    """
    Ingests and resolves any Spotify, YouTube Music, or YouTube URL adhering strictly to Rules A-D.
    Payload: {"url": "https://..."}
    """
    from src.source_resolution import get_source_resolution_service
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "URL is required"}), 400

    service = get_source_resolution_service()
    result = service.resolve_url(url)
    return jsonify(result)


@bp.route("/api/alternatives/find", methods=["POST"])
def find_alternatives():
    """
    Discovers candidates from other providers (Rule B/C) without auto-substituting.
    Payload: {"item": NormalizedItem dict}
    """
    from src.source_resolution import get_source_resolution_service
    from src.providers.base import NormalizedItem
    data = request.json or {}
    item_data = data.get("item")
    if not item_data:
        return jsonify({"success": False, "error": "item payload is required"}), 400

    try:
        normalized = NormalizedItem.from_dict(item_data)
        service = get_source_resolution_service()
        report = service.find_alternatives_for_track(normalized)
        # The service ranks candidates as {candidate, rationale, ...}; the modal reads {item, explanation, ...}.
        candidates = [
            {
                "item": c["candidate"],
                "confidence_score": c["confidence_score"],
                "outcome": c["outcome"],
                "explanation": c["rationale"],
                "breakdown": c.get("breakdown", {}),
            }
            for c in report.get("candidates", [])
        ]
        return jsonify({"success": True, "report": {**report, "candidates": candidates}})
    except Exception as e:
        logger.error(f"Error finding alternatives: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/alternatives/select", methods=["POST"])
def select_alternative():
    """
    Explicit user selection of a cross-source alternative (Rule D).
    Payload: {"original_item": {...}, "alternative_item": {...}}
    """
    from src.source_resolution import get_source_resolution_service
    from src.providers.base import NormalizedItem
    data = request.json or {}
    orig_data = data.get("original_item")
    alt_data = data.get("alternative_item")
    if not orig_data or not alt_data:
        return jsonify({"success": False, "error": "Both original_item and alternative_item are required"}), 400

    try:
        orig = NormalizedItem.from_dict(orig_data)
        alt = NormalizedItem.from_dict(alt_data)
        service = get_source_resolution_service()
        if not service.associate_alternative(orig.provider_item_id, orig.provider, alt.to_dict()):
            return jsonify({"success": False, "error": "Could not save the association"}), 500
        record_audit("ALTERNATIVE_SELECTED", f"User associated {alt.provider}:{alt.title} with {orig.provider}:{orig.title}")
        return jsonify({"success": True, "match": service.get_associated_alternative(orig.provider_item_id, orig.provider)})
    except Exception as e:
        logger.error(f"Error associating alternative: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/alternatives/current", methods=["GET"])
def get_current_alternative():
    """
    Returns any currently associated alternative for a provider item.
    Query params: provider, item_id
    """
    from src.source_resolution import get_source_resolution_service
    provider = request.args.get("provider", "spotify")
    item_id = request.args.get("item_id", "")
    if not item_id:
        return jsonify({"error": "item_id is required"}), 400

    service = get_source_resolution_service()
    match = service.get_associated_alternative(item_id, provider)
    return jsonify({"has_alternative": match is not None, "match": match})


@bp.route("/api/alternatives/clear", methods=["POST"])
def clear_alternative():
    """Removes the user's chosen alternative for an item. Payload: {"provider", "item_id"}."""
    from src.source_resolution import get_source_resolution_service
    data = request.json or {}
    item_id = (data.get("item_id") or "").strip()
    if not item_id:
        return jsonify({"success": False, "error": "item_id is required"}), 400
    provider = (data.get("provider") or "spotify").strip()
    removed = get_source_resolution_service().clear_associated_alternative(item_id, provider)
    record_audit("ALTERNATIVE_CLEARED", f"User removed alternative association for {provider}:{item_id}")
    return jsonify({"success": True, "removed": removed})


@bp.route("/api/providers/status", methods=["GET"])
def get_providers_status():
    """
    Returns current status and capabilities for all integrated providers.
    """
    from src.playback_capability import PlaybackCapabilityService
    status = PlaybackCapabilityService.get_all_provider_statuses()
    return jsonify(status)
