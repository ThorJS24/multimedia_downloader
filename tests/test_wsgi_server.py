"""The app is served by waitress (not Flask's development server): streaming, auth and concurrency."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import http.client
import socket
import threading
import time
import unittest

from src.security import SESSION_AUTH_TOKEN
from src.web.server import SERVER_THREADS, create_wsgi_server
from src.web.state import logger as studio_logger


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestWaitressServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.server = create_wsgi_server("127.0.0.1", cls.port)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def request(self, path, token=True, timeout=5):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        c.request("GET", path, headers={"X-Session-Token": SESSION_AUTH_TOKEN} if token else {})
        return c, c.getresponse()

    def test_it_is_waitress_and_pool_is_larger_than_the_streams_we_open(self):
        c, r = self.request("/api/status")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Server"), "StudioServer")        # ident set in create_wsgi_server (not Werkzeug)
        self.assertNotIn("Werkzeug", r.getheader("Server"))
        self.assertGreaterEqual(SERVER_THREADS, 16)
        c.close()

    def test_auth_and_security_headers_are_enforced_by_the_app_not_the_server(self):
        c, r = self.request("/api/queue/snapshot", token=False)
        self.assertEqual(r.status, 401)
        c.close()
        c, r = self.request("/api/queue/snapshot")
        self.assertEqual(r.status, 200)
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        c.close()

    def test_forbidden_host_header_is_rejected(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/api/status", headers={"Host": "evil.example.com", "X-Session-Token": SESSION_AUTH_TOKEN})
        self.assertEqual(c.getresponse().status, 403)
        c.close()

    def test_live_log_stream_delivers_events_immediately_not_after_a_buffer_fills(self):
        c, r = self.request("/api/logs/stream", timeout=8)
        self.assertEqual(r.status, 200)
        self.assertIn("text/event-stream", r.getheader("Content-Type"))
        marker = f"sse-probe-{time.time_ns()}"
        threading.Timer(0.4, lambda: studio_logger.warning(marker)).start()
        seen = ""
        deadline = time.time() + 6
        while time.time() < deadline and marker not in seen:
            line = r.fp.readline()
            if not line:
                break
            seen += line.decode("utf-8", "replace")
        c.close()
        self.assertIn(marker, seen, "the event must arrive within seconds, so waitress is not buffering the stream")

    def test_many_open_streams_do_not_starve_normal_requests(self):
        streams = []
        try:
            for _ in range(10):                                         # far more windows than anyone opens
                c, r = self.request("/api/logs/stream", timeout=8)
                self.assertEqual(r.status, 200)
                streams.append(c)
            t0 = time.time()
            for _ in range(5):
                c, r = self.request("/api/queue/snapshot")
                self.assertEqual(r.status, 200)
                r.read()
                c.close()
            self.assertLess(time.time() - t0, 3.0)
        finally:
            for c in streams:
                c.close()

    def test_unhandled_errors_do_not_leak_tracebacks(self):
        c, r = self.request("/api/definitely-missing")
        body = r.read().decode()
        self.assertEqual(r.status, 404)
        self.assertNotIn("Traceback", body)
        c.close()


class TestServerWiring(unittest.TestCase):
    def test_nothing_uses_the_development_server_in_production_paths(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        shell = (root / "src" / "desktop_shell.py").read_text(encoding="utf-8")
        self.assertIn("serve_app(", shell)
        self.assertNotIn("app.run(", shell)
        server = (root / "src" / "web" / "server.py").read_text(encoding="utf-8")
        self.assertIn("debug=True", server)          # the only app.run() left is the explicit debug path
        self.assertIn("waitress", (root / "requirements.txt").read_text(encoding="utf-8"))
        self.assertIn("'waitress'", (root / "package.spec").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
