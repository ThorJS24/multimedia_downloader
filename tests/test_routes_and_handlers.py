"""
Route smoke test (no endpoint may crash on empty input, offline), the alternatives flow, and static
checks for the inline-handler -> registry migration (CSP forbids inline scripts/handlers).
"""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import re
import socket
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = ROOT / "src" / "web" / "static" / "js"
INDEX = ROOT / "src" / "web" / "templates" / "index.html"
LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost")


class TestRouteSmoke(unittest.TestCase):
    def test_no_parameterless_route_crashes_on_empty_input(self):
        from src.web.server import app
        client = app.test_client()
        skip = {"/api/logs/stream"}
        failures = []
        real_connect = socket.socket.connect
        real_getaddrinfo = socket.getaddrinfo

        def offline_connect(sock, address):
            host = address[0] if isinstance(address, tuple) else address
            if host not in LOCAL_HOSTS:
                raise OSError("network disabled in tests")
            return real_connect(sock, address)

        def offline_getaddrinfo(host, *a, **k):
            if host in LOCAL_HOSTS or host is None:
                return real_getaddrinfo(host, *a, **k)
            raise OSError("network disabled in tests")

        with patch.object(socket.socket, "connect", offline_connect), patch.object(socket, "getaddrinfo", offline_getaddrinfo):
            for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
                if "<" in rule.rule or rule.rule in skip:
                    continue
                for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                    resp = client.open(rule.rule, method=method, json={} if method != "GET" else None)
                    if resp.status_code >= 500:
                        failures.append(f"{method} {rule.rule} -> {resp.status_code} {resp.get_data(as_text=True)[:120]}")
                    resp.close()
        self.assertEqual(failures, [], "routes returned 5xx on empty input:\n" + "\n".join(failures))


class TestAlternativesFlow(unittest.TestCase):
    ORIG = {"provider": "spotify", "provider_item_id": "alt_t_sp", "entity_type": "track", "title": "T",
            "primary_creator": "A", "canonical_url": "https://open.spotify.com/track/alt_t_sp"}
    ALT = {"provider": "ytmusic", "provider_item_id": "alt_t_yt", "entity_type": "song", "title": "T",
           "primary_creator": "A", "canonical_url": "https://music.youtube.com/watch?v=alt_t_yt"}

    @classmethod
    def setUpClass(cls):
        from src.web.server import app
        cls.client = app.test_client()

    def tearDown(self):
        self.client.post("/api/alternatives/clear", json={"provider": "spotify", "item_id": "alt_t_sp"})

    def current(self, provider="spotify"):
        return self.client.get(f"/api/alternatives/current?provider={provider}&item_id=alt_t_sp").get_json()

    def test_select_current_clear_round_trip(self):
        self.assertFalse(self.current()["has_alternative"])

        res = self.client.post("/api/alternatives/select", json={"original_item": self.ORIG, "alternative_item": self.ALT})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        self.assertTrue(res.get_json()["success"])

        cur = self.current()
        self.assertTrue(cur["has_alternative"])
        # the field names the UI reads
        self.assertEqual((cur["match"]["alternative_provider"], cur["match"]["alternative_item_id"]), ("ytmusic", "alt_t_yt"))
        self.assertEqual(cur["match"]["confidence_score"], 1.0)

        cleared = self.client.post("/api/alternatives/clear", json={"provider": "spotify", "item_id": "alt_t_sp"}).get_json()
        self.assertTrue(cleared["success"])
        self.assertGreaterEqual(cleared["removed"], 1)
        self.assertFalse(self.current()["has_alternative"])

    def test_find_returns_candidates_in_the_shape_the_modal_reads(self):
        from src.providers.base import NormalizedItem
        from src.source_resolution import get_source_resolution_service
        svc = get_source_resolution_service()
        candidate = NormalizedItem(provider="ytmusic", provider_item_id="alt_t_found", entity_type="song", title="T",
                                   primary_creator="A", duration_ms=200000, canonical_url="https://music.youtube.com/watch?v=alt_t_found")
        orig = dict(self.ORIG, duration_ms=200000)
        with patch.object(svc.ytmusic_provider, "is_enabled", return_value=True),                 patch.object(svc.ytmusic_provider, "search", return_value={"tracks": [candidate]}),                 patch.object(svc.youtube_provider, "is_enabled", return_value=False):
            res = self.client.post("/api/alternatives/find", json={"item": orig})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        report = res.get_json()["report"]
        self.assertEqual(len(report["candidates"]), 1)
        cand = report["candidates"][0]
        self.assertEqual(cand["item"]["provider_item_id"], "alt_t_found")      # modal: cand.item
        self.assertGreater(cand["confidence_score"], 0.8)                       # modal: cand.confidence_score (0..1)
        self.assertTrue(cand["outcome"])                                        # modal: cand.outcome
        self.assertIn("match", cand["explanation"].lower())                     # modal: cand.explanation
        self.assertIsNone(report["associated_alternative"])

    def test_find_requires_an_item(self):
        self.assertEqual(self.client.post("/api/alternatives/find", json={}).status_code, 400)

    def test_validation(self):
        self.assertEqual(self.client.post("/api/alternatives/select", json={"original_item": self.ORIG}).status_code, 400)
        self.assertEqual(self.client.post("/api/alternatives/clear", json={}).status_code, 400)
        self.assertEqual(self.client.get("/api/alternatives/current").status_code, 400)

    def test_association_is_per_provider(self):
        self.client.post("/api/alternatives/select", json={"original_item": self.ORIG, "alternative_item": self.ALT})
        self.assertFalse(self.current(provider="youtube")["has_alternative"])


class TestHandlerRegistryMigration(unittest.TestCase):
    ATTR = r'data-on-(?:click|change|input|submit|keydown|error)="([^"$]+)"'

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")
        cls.scripts = {p.name: p.read_text(encoding="utf-8") for p in STATIC_JS.glob("*.js")}
        cls.registry = cls.scripts["handlers.js"]

    def test_no_inline_event_handlers_or_scripts_remain(self):
        pattern = re.compile(r"\son(?:click|change|input|error|load|submit|keydown|keyup|mouseover|focus|blur)\s*=", re.I)
        self.assertEqual(pattern.findall(self.html), [])
        for name, text in self.scripts.items():
            self.assertEqual(pattern.findall(text), [], f"inline handler left in {name}")
        self.assertNotRegex(self.html, r"<script(?![^>]*\ssrc=)[^>]*>")  # only external scripts
        self.assertNotIn("javascript:", self.html)

    def test_csp_forbids_inline_and_eval(self):
        from src.web.server import CONTENT_SECURITY_POLICY as csp
        script_src = next(d for d in csp.split("; ") if d.startswith("script-src"))
        self.assertEqual(script_src, "script-src 'self'")
        self.assertNotIn("unsafe-eval", csp)
        self.assertIn("frame-src https://www.youtube-nocookie.com", csp)   # the YouTube player modal

    def test_every_referenced_key_is_registered(self):
        registered = set(re.findall(r'H\["([^"]+)"\]\s*=', self.registry))
        used = set(re.findall(self.ATTR, self.html))
        for name, text in self.scripts.items():
            if name not in ("handlers.js", "dispatch.js"):
                used |= set(re.findall(self.ATTR, text))
                # Keys handed to card/row builders as quoted literals (handler: "openPlaylistView_0c783b").
                used |= {k for k in re.findall(r"""["']([A-Za-z]\w*_[0-9a-f]{6})["']""", text) if k in registered}
        self.assertTrue(used)
        self.assertEqual(sorted(used - registered), [])
        self.assertEqual(sorted(registered - used), [], "registry entries nothing refers to")

    def test_registry_only_calls_functions_that_exist(self):
        code = "\n".join(t for n, t in self.scripts.items() if n != "handlers.js")
        defined = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)", code))
        defined |= set(re.findall(r"(?:^|\n)\s*(?:let|const|var)\s+([A-Za-z_$][\w$]*)", code))
        builtins = {"parseInt", "parseFloat", "encodeURIComponent", "decodeURIComponent", "String", "Number", "Boolean",
                    "JSON", "confirm", "setTimeout", "function", "if", "for", "while", "switch", "return"}
        missing = {}
        for key, body in re.findall(r'H\["([^"]+)"\] = function \(event, __a\) \{(.*?)\n\};', self.registry, re.S):
            for name in set(re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", body)):
                if name not in defined and name not in builtins:
                    missing.setdefault(name, []).append(key)
        self.assertEqual(missing, {}, "handlers call functions that are not defined anywhere")

    def test_scripts_load_registry_before_dispatcher_before_app_code(self):
        order = re.findall(r'<script src="/static/(?:js|vendor)/([\w.]+)"', self.html)
        self.assertLess(order.index("handlers.js"), order.index("dispatch.js"))
        self.assertLess(order.index("dispatch.js"), order.index("core.js"))
        self.assertEqual(order[-1], "boot.js")

    def test_dynamic_markup_passes_arguments_through_one_escape(self):
        # Arguments travel as escapeHtml(JSON.stringify([...])): never pre-escaped JSON inside the array.
        self.assertNotRegex(self.scripts["sources.js"], r"const (?:itemJson|candJson) = escapeHtml\(")


if __name__ == "__main__":
    unittest.main()
