"""The launcher opens the app directly: no banner, no interactive menu, no CLI download commands."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import main as launcher  # noqa: E402


def run_main(*argv):
    with patch.object(sys, "argv", ["main.py", *argv]), patch("builtins.input", side_effect=AssertionError("the app must never prompt")), \
            patch("src.desktop_shell.DesktopApp") as app:
        launcher.main()
    return app


class TestLauncher(unittest.TestCase):
    def test_no_arguments_opens_the_desktop_app_immediately(self):
        app = run_main()
        app.assert_called_once_with(port=None, use_browser=False, headless=False)
        app.return_value.launch.assert_called_once()

    def test_browser_and_headless_switches_still_work(self):
        self.assertTrue(run_main("--browser").call_args.kwargs["use_browser"])
        self.assertTrue(run_main("--headless").call_args.kwargs["headless"])
        self.assertTrue(run_main("--no-browser").call_args.kwargs["headless"])
        self.assertTrue(run_main("web", "--no-browser").call_args.kwargs["headless"])
        self.assertEqual(run_main("web", "--port", "5123").call_args.kwargs["port"], 5123)

    def test_the_cli_menu_and_commands_are_gone(self):
        for name in ("interactive_menu", "show_banner", "cmd_login", "cmd_playlist", "cmd_track", "cmd_info", "cmd_status"):
            self.assertFalse(hasattr(launcher, name), name)
        for command in ("login", "playlist", "track", "info", "status", "audit", "monitor", "optimize", "compare", "stage"):
            with patch.object(sys, "argv", ["main.py", command]), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as ctx:
                launcher.main()
            self.assertNotEqual(ctx.exception.code, 0, f"'{command}' should be rejected")

    def test_diagnostics_prints_and_never_starts_the_app(self):
        out = io.StringIO()
        with patch.object(sys, "argv", ["main.py", "--diagnostics"]), patch("src.desktop_shell.DesktopApp") as app, redirect_stdout(out):
            launcher.main()
        app.assert_not_called()
        self.assertIn("Spotify Downloader Studio Diagnostics", out.getvalue())
        self.assertIn("Database Integrity", out.getvalue())

    def test_version_flag(self):
        out = io.StringIO()
        with patch.object(sys, "argv", ["main.py", "--version"]), redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
            launcher.main()
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("v", out.getvalue())

    def test_mcp_command_is_kept_for_ai_assistants(self):
        with patch.object(sys, "argv", ["main.py", "mcp", "--transport", "stdio"]), patch("src.mcp_server.server.mcp") as mcp:
            launcher.main()
        mcp.run.assert_called_once()

    def test_packaged_exe_is_windowed_and_no_menu_wording_remains(self):
        spec = (ROOT / "package.spec").read_text(encoding="utf-8")
        self.assertIn("console=False", spec)
        self.assertNotIn("console=True", spec)
        text = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("Select an Option", text)
        self.assertNotIn("Enter choice", text)
        self.assertNotIn("rich", text.replace("RICH", ""))          # the CLI's only UI dependency is gone

    def test_second_launch_of_the_packaged_exe_shows_a_message_box(self):
        from src import desktop_shell
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "platform", "win32"), \
                patch("ctypes.windll", create=True) as windll:
            desktop_shell.notify_user("t", "m")
        windll.user32.MessageBoxW.assert_called_once()


if __name__ == "__main__":
    unittest.main()
