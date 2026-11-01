"""Release pipeline: the update feed it publishes is exactly what the app's update checker accepts, a wrong tag is refused,
and the workflow files are well formed and only call things that exist."""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import make_update_feed as feed_tool  # noqa: E402

from src import update_service  # noqa: E402
from src.settings_service import SettingsService  # noqa: E402
from src.update_service import UpdateService  # noqa: E402
from src.version import RELEASE_FEED_URL, RELEASE_REPO, __version__  # noqa: E402

CHANGELOG = """# Changelog

## 2.0.1 (unreleased)

### Added
- Something new.


### Fixed
- Something fixed.

## 2.0.0

### Initial
- The first release.
"""


class TestFeed(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.installer = self.tmp / f"SpotifyDownloader-v{__version__}-Setup.exe"
        self.installer.write_bytes(b"MZ" + b"installer bytes" * 1000)
        self.changelog = self.tmp / "CHANGELOG.md"
        self.changelog.write_text(CHANGELOG.replace("2.0.1 (unreleased)", f"{__version__} (unreleased)"), encoding="utf-8")

    def feed(self, **over):
        args = dict(installer=self.installer, tag=f"v{__version__}", repo="owner/name", changelog=self.changelog)
        args.update(over)
        return feed_tool.build_feed(**args)

    def test_the_feed_points_at_the_release_asset_with_the_right_hash_and_notes(self):
        f = self.feed()
        self.assertEqual(f["version"], __version__)
        self.assertEqual(f["url"], f"https://github.com/owner/name/releases/download/v{__version__}/{self.installer.name}")
        self.assertEqual(f["sha256"], hashlib.sha256(self.installer.read_bytes()).hexdigest())
        self.assertIn("Something new.", f["notes"])
        self.assertIn("Something fixed.", f["notes"])
        self.assertNotIn("The first release", f["notes"])                  # only this version's section
        self.assertNotIn("\n\n\n", f["notes"])

    def test_a_tag_that_does_not_match_the_app_version_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self.feed(tag="v9.9.9")
        self.assertIn("9.9.9", str(ctx.exception))
        with self.assertRaises(ValueError):
            self.feed(tag="vnext")

    def test_bad_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            self.feed(installer=self.tmp / "missing.exe")
        notexe = self.tmp / "thing.zip"
        notexe.write_bytes(b"x")
        with self.assertRaises(ValueError):
            self.feed(installer=notexe)
        with self.assertRaises(ValueError):
            self.feed(repo="not a repo")

    def test_long_notes_are_trimmed_to_what_the_app_keeps(self):
        self.changelog.write_text(f"## {__version__}\n" + ("- a line of notes\n" * 400) + "\n## 1.0.0\n", encoding="utf-8")
        notes = self.feed()["notes"]
        self.assertLessEqual(len(notes), feed_tool.NOTES_LIMIT)
        self.assertTrue(notes.endswith("…"))

    def test_the_apps_own_update_checker_accepts_it_and_sees_an_update(self):
        feed = self.feed()
        response = MagicMock()
        response.json.return_value = feed
        with patch.object(update_service.http_client, "get", return_value=response), \
                patch.object(update_service, "__version__", "1.0.0"), \
                patch.object(SettingsService, "get_all", return_value={"maintenance": {"update_feed_url": RELEASE_FEED_URL}}):
            result = UpdateService.check()
        self.assertTrue(result["available"], result)
        self.assertEqual((result["latest"], result["sha256"], result["url"]), (feed["version"], feed["sha256"], feed["url"]))
        self.assertNotIn("error", result)

    def test_the_command_line_writes_the_file_and_fails_loudly_on_a_mismatch(self):
        out = self.tmp / "updates.json"
        run = lambda *extra: subprocess.run(  # noqa: E731
            [sys.executable, str(ROOT / "tools" / "make_update_feed.py"), "--installer", str(self.installer), "--out", str(out), *extra],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60)
        good = run("--tag", f"v{__version__}", "--repo", "owner/name", "--notes", "Hello")
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["notes"], "Hello")
        out.unlink()
        bad = run("--tag", "v0.0.1")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("version", bad.stderr)
        self.assertFalse(out.exists())

    def test_the_default_feed_address_is_the_stable_latest_release_asset(self):
        self.assertEqual(RELEASE_FEED_URL, f"https://github.com/{RELEASE_REPO}/releases/latest/download/updates.json")
        self.assertTrue(RELEASE_FEED_URL.startswith("https://"))                 # the app only accepts https feeds


class TestWorkflows(unittest.TestCase):
    def load(self, name):
        return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))

    def steps(self, workflow):
        return [step for job in workflow["jobs"].values() for step in job["steps"]]

    def test_both_workflows_parse_and_have_the_expected_triggers(self):
        ci, release = self.load("ci.yml"), self.load("release.yml")
        self.assertIn("pull_request", ci[True])                                 # PyYAML reads the key `on` as boolean True
        self.assertEqual(release[True]["push"]["tags"], ["v*"])
        self.assertIn("workflow_dispatch", release[True])

    def test_the_release_only_publishes_for_tags_and_asks_for_no_more_than_it_needs(self):
        release = self.load("release.yml")
        self.assertEqual(release["permissions"], {"contents": "write"})
        publishing = [s for s in self.steps(release) if "action-gh-release" in s.get("uses", "")]
        self.assertEqual(len(publishing), 1)
        self.assertIn("refs/tags/v", publishing[0]["if"])

    def test_every_action_is_pinned_to_a_version(self):
        for name in ("ci.yml", "release.yml"):
            for step in self.steps(self.load(name)):
                if "uses" in step:
                    self.assertRegex(step["uses"], r"@v\d+", f"{name}: {step['uses']}")

    def test_scripts_the_release_calls_exist_and_the_tag_never_goes_through_a_shell_template(self):
        text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        for script in set(re.findall(r"(?:python )((?:tools/)?[\w/]+\.py)", text)):
            self.assertTrue((ROOT / script).exists(), script)
        for line in text.splitlines():
            if line.strip().startswith(("python", "$installer")):
                self.assertNotIn("${{ github.ref_name }}", line)                # passed through an env var instead

    def test_the_smoke_test_runs_before_anything_is_published(self):
        names = [s.get("name", "") for s in self.steps(self.load("release.yml"))]
        build = next(i for i, n in enumerate(names) if n.startswith("Build"))
        smoke = next(i for i, n in enumerate(names) if "built app" in n)
        publish = next(i for i, n in enumerate(names) if n.startswith("Publish"))
        self.assertLess(build, smoke)
        self.assertLess(smoke, publish)


if __name__ == "__main__":
    unittest.main()
