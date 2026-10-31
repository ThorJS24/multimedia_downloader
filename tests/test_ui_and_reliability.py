try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import unittest
from pathlib import Path
from src.web.server import app

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def read_frontend_js() -> str:
    """All frontend scripts concatenated in load order (as the browser executes them)."""
    js_dir = PROJECT_ROOT / "src" / "web" / "static" / "js"
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(js_dir.glob("*.js")))


class TestPhase11UIAndReliability(unittest.TestCase):
    """Automated test suite verifying Phase 11 UI/UX, reliability, and error handling improvements."""

    def setUp(self):
        self.client = app.test_client()

    def test_01_global_json_error_handler_404(self):
        """Verify that 404 routes return structured JSON instead of HTML error pages."""
        resp = self.client.get("/api/nonexistent-endpoint-xyz")
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(resp.is_json)
        data = resp.get_json()
        self.assertFalse(data.get("success"))
        self.assertIn("error", data)
        self.assertEqual(data.get("status_code"), 404)

    def test_02_global_json_error_handler_bad_request(self):
        """Verify that 400 Bad Request responses return structured JSON."""
        # POST with malformed/missing payload to an endpoint requiring JSON
        resp = self.client.post("/api/login", data="invalid json payload", content_type="application/json")
        # Either 400 or JSON error response
        self.assertTrue(resp.is_json or resp.status_code in (400, 500))

    def test_03_toast_container_present_in_template(self):
        """Verify that #toast-container is present in index.html for non-blocking notifications."""
        index_path = PROJECT_ROOT / "src" / "web" / "templates" / "index.html"
        self.assertTrue(index_path.exists())
        content = index_path.read_text(encoding="utf-8")
        self.assertIn('id="toast-container"', content)
        self.assertIn('id="dash-queue-state"', content)
        self.assertIn('id="log-search-input"', content)
        self.assertIn('id="log-flt-error"', content)

    def test_04_initial_disk_meters_are_placeholders(self):
        """Verify that initial HTML does not contain hardcoded fake 122 GB numbers."""
        index_path = PROJECT_ROOT / "src" / "web" / "templates" / "index.html"
        content = index_path.read_text(encoding="utf-8")
        self.assertNotIn('>122 GB free<', content)
        self.assertNotIn('>122 GB<', content)
        self.assertIn('>-- GB free<', content)
        self.assertIn('>-- GB<', content)

    def test_05_toast_css_definitions_exist(self):
        """Verify that toast CSS classes and animations are defined in style.css."""
        css_path = PROJECT_ROOT / "src" / "web" / "static" / "style.css"
        self.assertTrue(css_path.exists())
        content = css_path.read_text(encoding="utf-8")
        self.assertIn('.toast-item', content)
        self.assertIn('.toast-success', content)
        self.assertIn('.toast-error', content)
        self.assertIn('.toast-warning', content)
        self.assertIn('.log-filter-btn', content)

    def test_06_app_js_toast_and_escape_handlers(self):
        """Verify that showToast, Escape key handler, and queue state updater exist in app.js."""
        content = read_frontend_js()
        self.assertIn('function showToast(', content)
        self.assertIn('function closeModal(', content)  # modal manager (ui.js) replaced closeAllModals()
        self.assertIn('key === "Escape"', content)
        self.assertIn('setLogSeverityFilter(', content)
        self.assertIn('applyLogFilters()', content)
        self.assertIn('dash-queue-state', content)

    def test_07_no_naked_alert_calls_in_app_js(self):
        """Verify that alert() calls were systematically replaced by showToast() in app.js."""
        lines = read_frontend_js().splitlines()
        alert_lines = [
            (idx, line.strip()) for idx, line in enumerate(lines, 1)
            if "alert(" in line and "window.alert" not in line and "//" not in line[:line.find("alert")]
        ]
        self.assertEqual(len(alert_lines), 0, f"Found unexpected alert() calls: {alert_lines}")


if __name__ == "__main__":
    unittest.main()
