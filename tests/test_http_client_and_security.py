"""Shared HTTP client (429/Retry-After), web auth/headers, secret redaction and log files."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import logging
import unittest
from unittest.mock import MagicMock, patch

import requests
from flask.testing import FlaskClient

from src import http_client
from src.security import SESSION_AUTH_TOKEN, SecurityManager


def _resp(status, headers=None):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.headers = headers or {}
    return r


class TestHttpClient(unittest.TestCase):
    def test_retry_after_parsing(self):
        self.assertEqual(http_client.retry_after_seconds("7"), 7.0)
        self.assertIsNone(http_client.retry_after_seconds(None))
        self.assertIsNone(http_client.retry_after_seconds("soon"))
        self.assertGreaterEqual(http_client.retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT"), 0.0)

    def test_backoff_grows_and_is_capped(self):
        self.assertLess(http_client.backoff_seconds(0), http_client.backoff_seconds(3))
        self.assertEqual(http_client.backoff_seconds(0, retry_after=5), 5)
        self.assertEqual(http_client.backoff_seconds(0, retry_after=9999), http_client.MAX_WAIT_SEC)
        self.assertLessEqual(http_client.backoff_seconds(30), http_client.MAX_WAIT_SEC)

    def test_429_is_retried_honouring_retry_after(self):
        session = MagicMock()
        session.request.side_effect = [_resp(429, {"Retry-After": "3"}), _resp(200)]
        with patch.object(http_client, "get_session", return_value=session), \
                patch.object(http_client, "time") as fake_time:
            res = http_client.get("https://example.test/x")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(session.request.call_count, 2)
        fake_time.sleep.assert_called_once_with(3.0)

    def test_client_errors_are_not_retried(self):
        session = MagicMock()
        session.request.return_value = _resp(404)
        with patch.object(http_client, "get_session", return_value=session), patch.object(http_client, "time") as fake_time:
            self.assertEqual(http_client.get("https://example.test/missing").status_code, 404)
        self.assertEqual(session.request.call_count, 1)
        fake_time.sleep.assert_not_called()

    def test_gives_up_after_retries_and_returns_last_response(self):
        session = MagicMock()
        session.request.return_value = _resp(503)
        with patch.object(http_client, "get_session", return_value=session), patch.object(http_client, "time"):
            self.assertEqual(http_client.get("https://example.test/down", retries=2).status_code, 503)
        self.assertEqual(session.request.call_count, 3)

    def test_network_errors_retry_then_raise(self):
        session = MagicMock()
        session.request.side_effect = requests.ConnectionError("boom")
        with patch.object(http_client, "get_session", return_value=session), patch.object(http_client, "time"):
            with self.assertRaises(requests.ConnectionError):
                http_client.get("https://example.test/x", retries=2)
        self.assertEqual(session.request.call_count, 3)

    def test_session_is_shared_and_sets_user_agent(self):
        self.assertIs(http_client.get_session(), http_client.get_session())
        self.assertIn("Mozilla", http_client.get_session().headers["User-Agent"])


class TestWebSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.web.server import app
        cls.app = app
        cls.raw = FlaskClient(app, app.response_class)  # no pre-authenticated cookie

    def test_api_requires_the_session_token(self):
        self.assertEqual(self.raw.get("/api/status").status_code, 401)
        self.assertEqual(self.raw.get("/").status_code, 401)
        self.assertEqual(self.raw.get("/api/status", headers={"X-Session-Token": "wrong"}).status_code, 401)
        self.assertEqual(self.raw.get("/api/plugins/hooks", headers={"X-Session-Token": SESSION_AUTH_TOKEN}).status_code, 200)

    def test_token_bootstrap_sets_a_strict_httponly_cookie_and_strips_the_url(self):
        res = self.raw.get(f"/?token={SESSION_AUTH_TOKEN}")
        self.assertEqual(res.status_code, 302)
        self.assertTrue(res.headers["Location"].endswith("/"))
        cookie = res.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        fresh = FlaskClient(self.app, self.app.response_class)  # the first client now holds a valid cookie
        self.assertEqual(fresh.get("/?token=nope").status_code, 401)

    def test_foreign_host_and_origin_are_rejected(self):
        hdr = {"X-Session-Token": SESSION_AUTH_TOKEN}
        self.assertEqual(self.raw.get("/api/status", headers={**hdr, "Host": "evil.example"}).status_code, 403)
        self.assertEqual(self.raw.post("/api/settings", json={}, headers={**hdr, "Origin": "http://evil.example"}).status_code, 403)
        self.assertEqual(self.raw.get("/api/status", headers={**hdr, "Host": "[::1]:8000"}).status_code, 200)

    def test_security_headers(self):
        res = self.raw.get("/api/status", headers={"X-Session-Token": SESSION_AUTH_TOKEN})
        csp = res.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertNotIn("http://", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(res.headers["X-Frame-Options"], "DENY")
        self.assertEqual(res.headers["Cache-Control"], "no-store")

    def test_page_loads_no_remote_assets(self):
        page = self.app.test_client().get("/").get_data(as_text=True)
        for needle in ("cdn.tailwindcss.com", "unpkg.com", "fonts.googleapis.com", "fonts.gstatic.com"):
            self.assertNotIn(needle, page)
        for local in ("/static/css/tailwind.css", "/static/vendor/lucide.min.js", "/static/js/core.js"):
            self.assertIn(local, page)
            res = self.app.test_client().get(local)
            self.assertEqual(res.status_code, 200, local)
            res.close()

    def test_shell_hooks_and_bad_webhooks_are_rejected(self):
        client = self.app.test_client()
        res = client.post("/api/plugins/hooks/register", json={"action_type": "shell_command", "target": "calc"})
        self.assertEqual(res.status_code, 400)
        res = client.post("/api/plugins/hooks/register", json={"action_type": "webhook", "target": "file:///etc/passwd"})
        self.assertEqual(res.status_code, 400)

    def test_file_routes_cannot_read_config_or_database(self):
        from src.config_loader import get_config_path
        client = self.app.test_client()
        res = client.get("/api/stream", query_string={"path": str(get_config_path())})
        self.assertEqual(res.status_code, 403)
        res = client.post("/api/quality/inspect", json={"path": str(get_config_path())})
        self.assertEqual(res.status_code, 403)


class TestRedactionAndLogging(unittest.TestCase):
    def test_redaction_rules(self):
        cases = {
            "login password=hunter2 ok": "hunter2",
            '{"password": "abc", "client_secret": "xyz"}': "xyz",
            "GET /?token=Abc_123-xyz&x=1": "Abc_123-xyz",
            "Authorization: Bearer abcdefgh12345678": "abcdefgh12345678",
            "blob dpapi:AQAAANCMnd8BFdERjHoAwE/Cl+sBAAAA end": "AQAAANCMnd8BFdERjHoAwE",
            "api_key: sk-live-0000": "sk-live-0000",
        }
        for text, secret in cases.items():
            out = SecurityManager.redact_secrets(text)
            self.assertNotIn(secret, out, text)
            self.assertIn("REDACTED", out, text)

    def test_ordinary_text_is_untouched(self):
        for text in ("Queued 12 tracks", "user=bob client_id=abc", "tokens counted: 5", ""):
            self.assertEqual(SecurityManager.redact_secrets(text), text)

    def test_formatter_redacts_messages_and_tracebacks(self):
        from src.logging_setup import RedactingFormatter
        fmt = RedactingFormatter("%(message)s")
        try:
            raise RuntimeError("failed with password=hunter2")
        except RuntimeError:
            import sys
            rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "token=abc123 leaked", None, sys.exc_info())
        out = fmt.format(rec)
        self.assertNotIn("abc123", out)
        self.assertNotIn("hunter2", out)

    def test_file_log_is_written_and_redacted(self):
        from src.logging_setup import LOG_FILE, setup_logging
        setup_logging()
        logging.getLogger("RedactionTest").warning("connecting with password=topsecret99")
        for h in logging.getLogger().handlers:
            h.flush()
        text = LOG_FILE.read_text(encoding="utf-8")
        self.assertIn("connecting with password=[REDACTED]", text)
        self.assertNotIn("topsecret99", text)

    def test_live_log_stream_is_redacted(self):
        from src.web.log_stream import log_handler
        logging.getLogger("StreamTest").warning("using client_secret=abc-very-secret")
        recent = " ".join(e["message"] for e in log_handler.get_history()[-5:])
        self.assertNotIn("abc-very-secret", recent)


if __name__ == "__main__":
    unittest.main()
