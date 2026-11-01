# Releasing

Releases are built by GitHub Actions (`.github/workflows/release.yml`) from a tag. Nothing is published from your machine.

## Cut a release

1. Update the version in `src/version.py` (for example `2.1.0`) and move the finished entries in `CHANGELOG.md` under a `## 2.1.0` heading. Commit and push.
2. Create and push a tag that matches: `git tag v2.1.0` then `git push origin v2.1.0`.
3. The workflow then:
   - installs the Python dependencies, FFmpeg (checked against its published SHA-256) and Inno Setup;
   - runs ruff, mypy and the unit tests;
   - builds the app, the portable zip and the installer (`build_windows.py`);
   - **runs the built exe** headless and checks it over HTTP (`tools/smoke_packaged.py`): version, every script served, the API refusing requests without the session token, a healthy database, the newer screens' endpoints;
   - writes `updates.json` (`tools/make_update_feed.py`), which refuses to run when the tag and `src/version.py` disagree;
   - publishes a GitHub Release with the installer, zip, `checksums.txt` and `updates.json`.

To build and test without publishing, run it by hand: *Actions → Release → Run workflow*. The artifacts are attached to the run.

## Updates for installed copies

In **Settings → Backups & updates**, *Use this project's GitHub releases* fills in
`https://github.com/ThorJS24/multimedia_downloader/releases/latest/download/updates.json`. After saving, **Check now** compares the installed version with the feed, **Download & verify** checks the installer's SHA-256, and **Install update** starts it. Update checks stay off until an address is entered; nothing contacts GitHub on its own.

The installer is only launched when it carries a valid code signature (a setting lets you relax that). That is why signing matters for self-updates: without it, the SHA-256 only protects against corruption.

## Signing

Add two repository secrets (*Settings → Secrets and variables → Actions*):

| Secret | Value |
|---|---|
| `STUDIO_SIGN_PFX_BASE64` | your code-signing certificate (`.pfx`), base64-encoded: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx"))` |
| `STUDIO_SIGN_PASSWORD` | its password |

With them present the build signs the exe and installer; without them it still builds, unsigned (Windows SmartScreen will warn on first run).

## Check a build yourself

```powershell
.\.venv\Scripts\python.exe tools\smoke_packaged.py --exe dist\SpotifyDownloader\SpotifyDownloader.exe
.\.venv\Scripts\python.exe tools\smoke_packaged.py --source        # the same checks against the source tree
```

It uses a throwaway data folder inside the project and exits non-zero (printing the end of the app's log) if anything fails.
