
from flask import Flask, Response, jsonify, redirect, render_template, request
from flask.testing import FlaskClient

from src.config_loader import APP_DIR, BUNDLE_ROOT, PROJECT_ROOT, get_downloads_path
from src.library_indexer import LibraryIndexer
from src.security import (
    SESSION_AUTH_TOKEN,
    SESSION_COOKIE_NAME,
    is_allowed_host,
    is_same_origin,
    is_valid_session_token,
)
from src.web.routes import audio_lab, automation, downloads, library, pages, system
from src.web.state import logger, queue_manager  # noqa: F401  (queue_manager re-exported for callers/tests)

template_dir = BUNDLE_ROOT / "src" / "web" / "templates"
if not template_dir.exists():
    template_dir = APP_DIR / "src" / "web" / "templates"
if not template_dir.exists():
    template_dir = PROJECT_ROOT / "src" / "web" / "templates"

static_dir = BUNDLE_ROOT / "src" / "web" / "static"
if not static_dir.exists():
    static_dir = APP_DIR / "src" / "web" / "static"
if not static_dir.exists():
    static_dir = PROJECT_ROOT / "src" / "web" / "static"

app = Flask(
    __name__,
    template_folder=str(template_dir),
    static_folder=str(static_dir)
)

class _TrustedTestClient(FlaskClient):
    """In-process test client; it never crosses a socket, so it is pre-authenticated."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_cookie(SESSION_COOKIE_NAME, SESSION_AUTH_TOKEN, domain="localhost")


app.test_client_class = _TrustedTestClient

for _module in (system, downloads, library, pages, automation, audio_lab):
    app.register_blueprint(_module.bp)



@app.before_request
def enforce_session_auth():
    """Loopback Host + same-origin + per-run session token on every non-static request."""
    if not is_allowed_host(request.host):
        return Response("Forbidden host", status=403)
    if request.path.startswith("/static/"):
        return None
    if not is_same_origin(request.headers.get("Origin"), request.host):
        return Response("Forbidden origin", status=403)

    # One-time bootstrap: the launcher opens /?token=...; swap it for a cookie and strip the URL.
    if request.path == "/" and is_valid_session_token(request.args.get("token")):
        resp = redirect("/")
        resp.set_cookie(SESSION_COOKIE_NAME, SESSION_AUTH_TOKEN, httponly=True, samesite="Strict")
        return resp

    supplied = request.cookies.get(SESSION_COOKIE_NAME) or request.headers.get("X-Session-Token")
    if not is_valid_session_token(supplied):
        return Response(
            "Unauthorized. Launch Spotify Downloader Studio from the app to open this page.",
            status=401,
        )
    return None


CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",                    # no inline scripts or inline on*= handlers (see static/js/handlers.js)
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https:",    # album art comes from Spotify / YouTube CDNs
    "media-src 'self' blob:",
    "font-src 'self'",
    "frame-src https://www.youtube-nocookie.com https://www.youtube.com https://music.youtube.com",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


@app.after_request
def add_security_headers(resp):
    resp.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.route("/")
def index():
    return render_template("index.html")


# --- Centralized Global Error Handlers (JSON API Compliance) ---
@app.errorhandler(400)
def handle_bad_request(e):
    logger.warning("Bad Request (400): %s", e)
    return jsonify({"success": False, "error": str(e), "status_code": 400}), 400


@app.errorhandler(404)
def handle_not_found(e):
    logger.warning("Not Found (404): %s %s", request.method, request.path)
    return jsonify({"success": False, "error": "Requested resource was not found", "status_code": 404}), 404


@app.errorhandler(500)
def handle_internal_error(e):
    logger.error("Internal Server Error (500): %s", e, exc_info=True)
    return jsonify({"success": False, "error": "Internal server processing error", "status_code": 500}), 500


@app.errorhandler(Exception)
def handle_unhandled_exception(e):
    logger.error("Unhandled Exception: %s", e, exc_info=True)
    return jsonify({"success": False, "error": str(e), "status_code": 500}), 500


def start_background_services():
    """Start scheduled housekeeping (retention, backups) and the release monitor."""
    from src.maintenance_service import MaintenanceService
    MaintenanceService.start()


# The UI keeps one long-lived SSE connection (live log) open per window plus short polling requests,
# so the pool must be comfortably larger than the number of streams. Everything is bound to loopback.
SERVER_THREADS = 24


def create_wsgi_server(host: str = "127.0.0.1", port: int = 0):
    """Production WSGI server (waitress) instead of Flask's development server."""
    from waitress import create_server
    return create_server(
        app, host=host, port=port, threads=SERVER_THREADS, ident="StudioServer",
        channel_timeout=600, cleanup_interval=30, expose_tracebacks=False, clear_untrusted_proxy_headers=True,
    )


def serve_app(host: str = "127.0.0.1", port: int = 0):
    """Blocking call: serve the app until the process ends."""
    create_wsgi_server(host, port).run()


def start_server(host="127.0.0.1", port=5000, debug=False):
    logger.info(f"Starting Spotify Downloader Studio on http://{host}:{port}")
    # Auto-index downloads on startup
    try:
        LibraryIndexer.scan_directory(get_downloads_path())
    except Exception:
        pass
    start_background_services()
    if debug:
        app.run(host=host, port=port, debug=True, threaded=True)   # development only
    else:
        serve_app(host, port)


if __name__ == "__main__":
    start_server()
