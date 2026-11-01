"""Write the update feed (updates.json) that the app's "Check for updates" reads, for a GitHub release.

    python tools/make_update_feed.py --installer dist/SpotifyDownloader-v2.1.0-Setup.exe --tag v2.1.0 --out dist/updates.json

The feed points at the installer attached to the release (https://github.com/<repo>/releases/download/<tag>/<file>)
and carries its SHA-256 and the release notes from CHANGELOG.md. Publishing it as a release asset gives a stable
address, https://github.com/<repo>/releases/latest/download/updates.json, which is what people put in Settings.

It refuses to run when the tag does not match the version the app reports, so a mistyped tag cannot publish an
installer under the wrong number.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.update_service import parse_version  # noqa: E402
from src.version import RELEASE_REPO, __version__  # noqa: E402

NOTES_LIMIT = 2000      # the app keeps at most this many characters


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def changelog_notes(version: str, changelog: Path) -> str:
    """The text under the '## <version>' heading (up to the next '## '), trimmed for the update dialog."""
    text = changelog.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not match:
        return ""
    body = re.sub(r"\n{3,}", "\n\n", match.group(1).strip())
    return body if len(body) <= NOTES_LIMIT else body[:NOTES_LIMIT - 1].rstrip() + "…"


def build_feed(installer: Path, tag: str, repo: str, notes: Optional[str] = None, changelog: Optional[Path] = None) -> dict:
    version = tag.strip().lstrip("vV")
    parse_version(version)                                        # rejects anything the app could not compare
    if version != __version__:
        raise ValueError(f"The tag says {version} but the app is version {__version__}. Update src/version.py (or the tag) first.")
    if not installer.is_file() or installer.suffix.lower() != ".exe":
        raise ValueError(f"The installer must be an existing .exe file: {installer}")
    if not re.match(r"^[\w.-]+/[\w.-]+$", repo):
        raise ValueError(f"The repository must look like owner/name, not '{repo}'")
    return {
        "version": version,
        "url": f"https://github.com/{repo}/releases/download/{tag}/{installer.name}",
        "sha256": sha256_of(installer),
        "notes": notes if notes is not None else changelog_notes(version, changelog or ROOT / "CHANGELOG.md"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--installer", type=Path, required=True, help="the built ...-Setup.exe")
    parser.add_argument("--tag", required=True, help="the release tag, for example v2.1.0")
    parser.add_argument("--repo", default=RELEASE_REPO, help=f"owner/name on GitHub (default {RELEASE_REPO})")
    parser.add_argument("--notes", default=None, help="release notes text (default: this version's section of CHANGELOG.md)")
    parser.add_argument("--out", type=Path, required=True, help="where to write updates.json")
    args = parser.parse_args()
    try:
        feed = build_feed(args.installer, args.tag, args.repo, args.notes)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(feed, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}: version {feed['version']}, sha256 {feed['sha256'][:12]}..., {len(feed['notes'])} characters of notes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
