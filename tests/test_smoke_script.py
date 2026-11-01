"""The smoke-test script (tools/smoke_packaged.py) works: it runs the app from source and every check passes.
It starts a real server process, so it belongs to the slow tier."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestSmokeScript(unittest.TestCase):
    def test_running_the_app_from_source_passes_every_check(self):
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "smoke_packaged.py"), "--source"],
                              capture_output=True, text=True, timeout=300, cwd=str(ROOT))
        self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-1000:])
        self.assertIn("checks passed", done.stdout)
        self.assertNotIn("FAIL", done.stdout)

    def test_a_missing_exe_is_reported_not_crashed(self):
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "smoke_packaged.py"), "--exe", str(ROOT / "nope" / "missing.exe")],
                              capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        self.assertEqual(done.returncode, 2)
        self.assertIn("Not found", done.stderr)


if __name__ == "__main__":
    unittest.main()
