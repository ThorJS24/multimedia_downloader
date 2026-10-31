"""
Real-browser checks of the delegated event dispatcher (static/js/dispatch.js + handlers.js) under the
strict CSP: handlers fire, bubbling/stopPropagation behave like inline handlers did, arguments pass
through, and injected inline handlers are blocked.
"""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
try:
    from _nav import go_tab  # noqa: F401
except ImportError:
    from tests._nav import go_tab  # noqa: F401
import socket
import threading
import unittest

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from src.security import SESSION_AUTH_TOKEN
from src.web.server import app


class TestBrowserHandlers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()
        cls.server = make_server("127.0.0.1", cls.port, app, threaded=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception:
            cls.pw.stop()          # never leave Playwright's event loop running for the tests that follow
            cls.server.shutdown()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()

    def unexpected_problems(self):
        """Console/page errors, ignoring cover-art 403s for fake library rows other tests seed (placeholder shown instead)."""
        bad_requests = [r for r in self.failed_requests if "/api/library/cover" not in r]
        problems = [e for e in self.errors if not e.startswith("Failed to load resource")]
        return problems + bad_requests

    def open_page(self):
        page = self.browser.new_page()
        self.errors = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.failed_requests = []
        page.on("response", lambda r: self.failed_requests.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        page.goto(f"http://127.0.0.1:{self.port}/?token={SESSION_AUTH_TOKEN}", wait_until="networkidle")
        page.wait_for_function("() => window.lucide && document.querySelectorAll('svg.lucide').length > 20")
        self.addCleanup(page.close)
        return page

    def test_page_loads_cleanly_under_the_csp(self):
        page = self.open_page()
        self.assertEqual(self.unexpected_problems(), [])
        self.assertTrue(page.evaluate("Object.keys(window.UI_HANDLERS).length > 200"))

    def test_navigation_buttons_work_through_the_registry(self):
        page = self.open_page()
        page.evaluate("document.getElementById('wizard-modal') && document.getElementById('wizard-modal').classList.add('hidden')")
        from _nav import HUB_OF
        for tab in ("settings", "queue", "library", "dashboard"):
            go_tab(page, tab)
            self.assertTrue(page.locator(f"#tab-{tab}").is_visible(), tab)
            hub_btn = "#tab-btn-dashboard" if tab == "dashboard" else f"#nav-hub-{HUB_OF[tab]}"
            self.assertEqual(page.locator(hub_btn).get_attribute("aria-current"), "page", tab)
            if tab != "dashboard":
                self.assertEqual(page.locator(f"#tab-btn-{tab}").get_attribute("aria-selected"), "true", tab)
        self.assertEqual(self.unexpected_problems(), [])

    def test_toast_close_button_uses_this(self):
        page = self.open_page()
        page.evaluate("showToast('hello from test', 'info')")
        toast = page.locator("#toast-container >> text=hello from test")
        self.assertEqual(toast.count(), 1)
        page.locator("#toast-container .toast-close").first.click()
        page.wait_for_selector("#toast-container >> text=hello from test", state="detached", timeout=3000)  # fades out first
        self.assertEqual(page.locator("#toast-container >> text=hello from test").count(), 0)

    def test_dispatch_semantics(self):
        page = self.open_page()
        page.evaluate("""() => {
            window.__log = [];
            const R = window.UI_HANDLERS;
            R.t_parent = function (e, a) { __log.push('parent'); };
            R.t_child_stop = function (e, a) { e.stopPropagation(); __log.push('child-stop'); };
            R.t_child = function (e, a) { __log.push('child:' + JSON.stringify(a) + ':' + this.id); };
            R.t_img_err = function (e, a) { __log.push('img-error:' + this.id); };
            document.addEventListener('click', () => __log.push('document-listener'));
            const box = document.createElement('div');
            box.innerHTML = `
              <div id="p" data-on-click="t_parent">
                <button id="c_stop" data-on-click="t_child_stop">stop</button>
                <button id="c_arg" data-on-click="t_child" data-args-click='["a", 2, {"k": true}]'>arg</button>
                <button id="c_bad" data-on-click="constructor">bad</button>
                <button id="c_missing" data-on-click="nope">missing</button>
                <button id="c_badjson" data-on-click="t_child" data-args-click="{not json">badjson</button>
              </div>
              <img id="broken" src="/static/does-not-exist.png" data-on-error="t_img_err">`;
            document.body.appendChild(box);
        }""")
        def log():
            return page.evaluate("window.__log.splice(0)")

        # the broken image's error event does not bubble; it must still reach its handler (capture listener)
        page.wait_for_function("() => window.__log.length > 0")
        self.assertEqual(log(), ["img-error:broken"])

        page.locator("#c_stop").click()
        # stopPropagation in a delegated handler: parent handler AND document-level listener are skipped
        self.assertEqual(log(), ["child-stop"])

        page.locator("#c_arg").click()
        self.assertEqual(log(), ['child:["a",2,{"k":true}]:c_arg', "parent", "document-listener"])  # args parsed, bubbles

        page.locator("#c_bad").click()          # keys not own-properties of the registry are ignored
        page.locator("#c_missing").click()
        page.locator("#c_badjson").click()      # malformed args fall back to []
        self.assertEqual(log(), ["parent", "document-listener", "parent", "document-listener", "child:[]:c_badjson", "parent", "document-listener"])

        self.assertEqual([e for e in self.errors if not e.startswith("Failed to load resource")], [])  # the deliberate 404 is ignored

    def test_injected_inline_handlers_and_scripts_are_blocked(self):
        page = self.open_page()
        violations = page.evaluate("""() => new Promise(resolve => {
            window.__pwned = 0;
            const seen = [];
            document.addEventListener('securitypolicyviolation', e => seen.push(e.violatedDirective));
            const d = document.createElement('div');
            d.innerHTML = '<img src="x" onerror="window.__pwned = 1"><button id="evil" onclick="window.__pwned = 2">x</button>';
            document.body.appendChild(d);
            document.getElementById('evil').click();
            setTimeout(() => resolve(seen), 500);
        })""")
        self.assertEqual(page.evaluate("window.__pwned"), 0)
        self.assertTrue(violations, "the browser should report CSP violations for inline handlers")
        self.assertTrue(all(v.startswith("script-src") for v in violations), violations)

    def test_download_this_alternative_records_the_choice_then_queues_it_tagged_as_the_original(self):
        import json
        page = self.open_page()
        calls = []

        def fulfil(route):
            req = route.request
            calls.append((req.url.rsplit("/api/", 1)[1], json.loads(req.post_data or "{}")))
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"success": True, "added": 1}))
        page.route("**/api/alternatives/select", fulfil)
        page.route("**/api/queue/add", fulfil)
        page.route("**/api/queue/snapshot*", lambda r: r.fulfill(status=200, content_type="application/json", body="{}"))

        page.evaluate("""() => {
            currentAlternativeTarget = { provider: 'spotify', provider_item_id: 'sp_orig', title: 'Original Song', primary_creator: 'Original Artist',
                                         album: 'Original Album', isrc: 'USORIG1', duration_ms: 200000, thumbnail_url: 'https://img/orig.jpg' };
            downloadAlternativeCandidate({ item: { provider: 'ytmusic', provider_item_id: 'yt_abc', title: 'Song (Official Video)',
                                                   primary_creator: 'Some Channel', canonical_url: 'https://music.youtube.com/watch?v=yt_abc' } });
        }""")
        page.wait_for_function("() => true")
        deadline = 50
        while len(calls) < 2 and deadline:
            page.wait_for_timeout(100)
            deadline -= 1

        self.assertEqual([c[0] for c in calls], ["alternatives/select", "queue/add"])     # choice recorded BEFORE queueing
        track = calls[1][1]["tracks"][0]
        self.assertEqual((track["provider"], track["provider_item_id"], track["is_alternative"]), ("ytmusic", "yt_abc", True))
        self.assertEqual(track["original_spotify_id"], "sp_orig")
        self.assertEqual((track["title"], track["album"], track["isrc"]), ("Original Song", "Original Album", "USORIG1"))
        self.assertEqual(track["artists"], ["Original Artist"])
        self.assertEqual(track["canonical_url"], "https://music.youtube.com/watch?v=yt_abc")

    def test_alternatives_handlers_receive_real_objects(self):
        page = self.open_page()
        got = page.evaluate("""() => {
            window.__captured = null;
            window.openAlternativesModal = function (item) { window.__captured = item; };
            const item = { provider: 'youtube', provider_item_id: 'abc', title: 'A "quoted" <b>title</b> & more' };
            const host = document.createElement('div');
            host.innerHTML = `<button id="alt" data-on-click="openAlternativesModal_8254e2"
                data-args-click="${escapeHtml(JSON.stringify([item]))}">x</button>`;
            document.body.appendChild(host);
            document.getElementById('alt').click();
            return window.__captured;
        }""")
        self.assertEqual(got["title"], 'A "quoted" <b>title</b> & more')
        self.assertEqual(got["provider_item_id"], "abc")


if __name__ == "__main__":
    unittest.main()
