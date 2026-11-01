from pathlib import Path

from flask import Response, jsonify, request, Blueprint

from src.diagnostics_service import DiagnosticsService
from src.fingerprint_service import FingerprintService
from src.quality_service import QualityService, QUALITY_PROFILES
from src.release_comparison import ReleaseComparisonStudio
from src.web.state import _is_user_media_path

bp = Blueprint("audio_lab", __name__)


# ================= FINGERPRINTING & IDENTIFICATION =================
@bp.route("/api/fingerprint/duplicates", methods=["GET"])
def get_acoustic_duplicates():
    threshold = float(request.args.get("threshold", 0.85))
    dups = FingerprintService.find_all_acoustic_duplicates(threshold=threshold)
    return jsonify({"duplicates": dups, "count": len(dups)})


@bp.route("/api/fingerprint/compute", methods=["POST"])
def compute_single_fingerprint():
    data = request.json or {}
    path = data.get("path", "")
    p = Path(path).resolve()
    if not _is_user_media_path(p):
        return jsonify({"error": "Access denied: Path outside project boundary"}), 403
    if not p.exists():
        return jsonify({"error": "File not found"}), 404
    fp = FingerprintService.compute_fingerprint(p)
    return jsonify(fp)


# ================= QUALITY AUDIT & PROFILES =================
@bp.route("/api/quality/profiles", methods=["GET"])
def get_quality_profiles():
    return jsonify({"profiles": QUALITY_PROFILES})


@bp.route("/api/quality/audit", methods=["GET"])
def audit_library_quality():
    res = QualityService.audit_library()
    return jsonify(res)


@bp.route("/api/quality/inspect", methods=["POST"])
def inspect_quality_file():
    data = request.json or {}
    p = Path(data.get("path", "")).resolve()
    if not _is_user_media_path(p):
        return jsonify({"error": "Access denied: Path outside project boundary"}), 403
    if not p.exists():
        return jsonify({"error": "File not found"}), 404
    return jsonify(QualityService.audit_file(p))


# ================= RELEASE COMPARISON & PROVENANCE =================
@bp.route("/api/release/compare", methods=["POST"])
def compare_releases():
    data = request.json or {}
    id_a = data.get("id_a", "").strip()
    id_b = data.get("id_b", "").strip()
    kind = data.get("kind", "album")
    if not id_a or not id_b:
        return jsonify({"error": "Both release IDs required"}), 400
    try:
        res = ReleaseComparisonStudio.compare_releases(id_a, id_b, kind)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/release/repair-queue", methods=["GET"])
def get_metadata_repair_queue():
    queue = ReleaseComparisonStudio.get_metadata_repair_queue()
    return jsonify({"queue": queue, "count": len(queue)})


@bp.route("/api/release/provenance/<track_id>", methods=["GET"])
def get_track_provenance(track_id):
    prov = ReleaseComparisonStudio.get_track_provenance(track_id)
    return jsonify({"provenance": prov})


@bp.route("/api/metadata/diff", methods=["POST"])
def get_metadata_diff():
    data = request.json or {}
    path = data.get("path", "").strip()
    track_id = data.get("track_id", "").strip()
    if not path or not track_id:
        return jsonify({"error": "Both path and track_id are required"}), 400
    p = Path(path).resolve()
    if not _is_user_media_path(p):
        return jsonify({"error": "Access denied: Path outside project boundary"}), 403
    try:
        diff_res = ReleaseComparisonStudio.compute_tag_diff(p, track_id)
        return jsonify({"success": True, "diff": diff_res})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@bp.route("/api/metadata/apply", methods=["POST"])
def apply_metadata_diff():
    data = request.json or {}
    path = data.get("path", "").strip()
    updates = data.get("updates", {})
    track_id = data.get("track_id", "")
    if not path or not updates:
        return jsonify({"error": "Path and updates are required"}), 400
    p = Path(path).resolve()
    if not _is_user_media_path(p):
        return jsonify({"error": "Access denied: Path outside project boundary"}), 403
    try:
        success = ReleaseComparisonStudio.apply_tag_diff(p, updates, track_id=track_id)
        return jsonify({"success": success})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@bp.route("/api/diagnostics/bundle", methods=["POST"])
def create_support_bundle():
    """Write a scrubbed zip (report, settings, plugin status, recent failures, logs) for attaching to a bug report."""
    try:
        path = DiagnosticsService.create_support_bundle()
    except OSError as e:
        return jsonify({"success": False, "error": f"Could not write the bundle: {e}"}), 500
    return jsonify({"success": True, "path": str(path), "size": path.stat().st_size})


@bp.route("/api/diagnostics/export", methods=["GET"])
@bp.route("/api/system/diagnostics/export", methods=["GET"])
def export_diagnostics():
    fmt = request.args.get("format", "json").lower()
    if fmt in ("markdown", "md"):
        content = DiagnosticsService.generate_markdown_report()
        return Response(content, mimetype="text/markdown")
    report = DiagnosticsService.generate_report()
    return jsonify(report)
