"""Build helpers: version resource generation, SHA-256, and the (optional) code-signing hook."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import hashlib
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_common  # noqa: E402
from src.config_loader import TEMP_DIR  # noqa: E402
from src.version import __version__  # noqa: E402

SIGN_VARS = ("STUDIO_SIGN_PFX", "STUDIO_SIGN_PASSWORD", "STUDIO_SIGNTOOL", "STUDIO_SIGN_TIMESTAMP")


class TestSigningHook(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in SIGN_VARS}
        self.addCleanup(self.restore)
        self.target = TEMP_DIR / "to_sign.exe"
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_bytes(b"MZ")

    def restore(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def sign(self):
        out = io.StringIO()
        with redirect_stdout(out):
            result = build_common.sign_file(self.target)
        return result, out.getvalue()

    def test_skipped_without_a_certificate(self):
        with patch.object(build_common.subprocess, "run") as run:
            result, output = self.sign()
        self.assertFalse(result)
        run.assert_not_called()
        self.assertIn("unsigned", output)

    def test_signtool_command_is_well_formed(self):
        os.environ.update({"STUDIO_SIGN_PFX": "C:/certs/me.pfx", "STUDIO_SIGNTOOL": "C:/sdk/signtool.exe",
                           "STUDIO_SIGN_TIMESTAMP": "http://ts.example/rfc3161"})
        with patch.object(build_common.subprocess, "run", return_value=MagicMock(returncode=0, stdout="", stderr="")) as run:
            result, output = self.sign()
        self.assertTrue(result)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:2], ["C:/sdk/signtool.exe", "sign"])
        self.assertIn("/fd", cmd)
        self.assertEqual(cmd[cmd.index("/fd") + 1], "sha256")
        self.assertEqual(cmd[cmd.index("/tr") + 1], "http://ts.example/rfc3161")
        self.assertEqual(cmd[cmd.index("/f") + 1], "C:/certs/me.pfx")
        self.assertEqual(cmd[-1], str(self.target))
        self.assertNotIn("/p", cmd)                       # no password configured, none passed
        self.assertIn("signed", output)

    def test_password_is_passed_but_never_printed(self):
        os.environ.update({"STUDIO_SIGN_PFX": "C:/certs/me.pfx", "STUDIO_SIGN_PASSWORD": "hunter2-secret"})
        failing = MagicMock(returncode=1, stdout="signtool said hunter2-secret", stderr="bad password hunter2-secret")
        with patch.object(build_common.subprocess, "run", return_value=failing) as run:
            result, output = self.sign()
        self.assertFalse(result)
        self.assertIn("/p", run.call_args[0][0])
        self.assertNotIn("hunter2-secret", output)        # signtool's own output is never echoed
        self.assertIn("FAILED", output)


class TestVersionResource(unittest.TestCase):
    def test_generated_from_the_single_version_source(self):
        target = TEMP_DIR / "version_info_test.txt"
        with patch.object(build_common, "VERSION_INFO_FILE", target):
            build_common.write_version_info()
        text = target.read_text(encoding="utf-8")
        parts = tuple((int(p) for p in __version__.split("."))) + (0,) * 4
        self.assertIn(f"filevers={parts[:4]}", text)
        self.assertIn(f"u'FileVersion', u'{'.'.join(map(str, parts[:4]))}'", text)
        self.assertIn("MIT License", text)
        # it must be a valid PyInstaller version resource (a Python expression built from these constructors)
        stub = lambda *a, **k: (a, k)  # noqa: E731
        exec(text, {n: stub for n in ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable", "StringStruct", "VarFileInfo", "VarStruct")})
        target.unlink()


class TestSha256(unittest.TestCase):
    def test_matches_hashlib_for_multi_chunk_files(self):
        f = TEMP_DIR / "sha_test.bin"
        data = os.urandom(3 * 1024 * 1024 + 17)
        f.write_bytes(data)
        self.assertEqual(build_common.compute_sha256(f), hashlib.sha256(data).hexdigest())
        f.unlink()


if __name__ == "__main__":
    unittest.main()
