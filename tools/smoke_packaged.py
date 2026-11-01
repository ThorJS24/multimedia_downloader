"""Smoke-test the app by actually running it: start it headless, use it over HTTP, stop it.

    python tools/smoke_packaged.py --exe dist/SpotifyDownloader/SpotifyDownloader.exe     # the built app
    python tools/smoke_packaged.py --source                                               # main.py from this checkout

It uses a throwaway data folder inside the project (nothing of yours is touched) and checks what a user would hit
first: the commands exit cleanly, the page and every script it references are served, the API refuses requests
without the session token, the database is healthy, and the newer screens' endpoints answer. Exit code 0 = all
good, 1 = something failed (the output says what, and shows the end of the app's log).
"""
import argparse
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parent.parent
READY_TIMEOUT_SEC = 90
MIN_SCHEMA_VERSION = 12


def expected_version() -> str:
    match = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "src" / "version.py").read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError("Could not read the version from src/version.py")
    return match.group(1)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Smoke:
    def __init__(self, command: List[str], keep: bool):
        self.command = command
        self.keep = keep
        self.data_dir = ROOT / "temp" / f"smoke_{int(time.time())}_{free_port()}"
        self.port = free_port()
        self.proc: Optional[subprocess.Popen] = None
        self.base = f"http://127.0.0.1:{self.port}"
        self.token = ""
        self.results: List[Tuple[str, bool, str]] = []

    # ---- plumbing
    def check(self, name: str, fn: Callable[[], Optional[str]]) -> bool:
        try:
            detail = fn() or ""
            self.results.append((name, True, detail))
            print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""), flush=True)
            return True
        except Exception as e:
            self.results.append((name, False, str(e)))
            print(f"  FAIL  {name}: {e}", flush=True)
            return False

    def api(self, path: str, token: bool = True, **kwargs) -> requests.Response:
        headers = {"X-Session-Token": self.token} if token else {}
        return requests.get(self.base + path, headers=headers, timeout=20, **kwargs)

    def json(self, path: str) -> dict:
        resp = self.api(path)
        if resp.status_code != 200:
            raise AssertionError(f"GET {path} answered HTTP {resp.status_code}")
        return resp.json()

    def run_once(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([*self.command, "--data-dir", str(self.data_dir), *args], capture_output=True, text=True, timeout=120)

    # ---- steps
    def commands(self) -> None:
        def version():
            done = subprocess.run([*self.command, "--version"], capture_output=True, text=True, timeout=120)
            assert done.returncode == 0, f"--version exited with {done.returncode}"
            # A windowed exe has no console to print to when its output is captured; the running app is asked for its version below.
            if done.stdout.strip():
                assert expected_version() in done.stdout, f"unexpected output: {done.stdout.strip()}"
        self.check("--version exits cleanly", version)

        def diagnostics():
            done = self.run_once("--diagnostics")
            assert done.returncode == 0, f"--diagnostics exited with {done.returncode}\n{done.stderr[-500:]}"
        self.check("--diagnostics exits cleanly", diagnostics)

    def start(self) -> bool:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        url_file = self.data_dir / "startup-url.txt"

        def launch() -> str:
            self.proc = subprocess.Popen(
                [*self.command, "--data-dir", str(self.data_dir), "web", "--headless", "--port", str(self.port), "--url-file", str(url_file)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.time() + READY_TIMEOUT_SEC
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    raise AssertionError(f"the app exited during start-up (code {self.proc.returncode})")
                if url_file.exists() and url_file.read_text(encoding="utf-8").strip():
                    break
                time.sleep(0.25)
            else:
                raise AssertionError(f"the app did not become ready within {READY_TIMEOUT_SEC}s")
            match = re.search(r"token=([^&\s]+)", url_file.read_text(encoding="utf-8"))
            assert match, "the startup address has no session token"
            self.token = match.group(1)
            return f"ready on port {self.port}"
        return self.check("starts headless and reports its address", launch)

    def web(self) -> None:
        pages: dict = {}

        def index():
            resp = requests.get(f"{self.base}/?token={self.token}", timeout=20)
            assert resp.status_code == 200, f"HTTP {resp.status_code}"
            assert "Spotify Downloader Studio" in resp.text, "the page title is missing"
            pages["html"] = resp.text
            pages["url"] = resp.url
        self.check("the main page loads with the session token", index)
        html = pages.get("html", "")
        assets = sorted(set(re.findall(r'<script[^>]+src="([^"]+)"', html)) | set(re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"', html)))

        def static_files():
            assert assets, "the page references no scripts or stylesheets"
            missing = [a for a in assets if requests.get(urljoin(self.base + "/", a), timeout=20).status_code != 200]
            assert not missing, f"not served: {', '.join(missing)}"
            return f"{len(assets)} files"
        self.check("every script and stylesheet the page uses is served", static_files)

        def refuses_without_token():
            for path in ("/api/status", "/api/history", "/api/plugins/packs"):
                code = self.api(path, token=False).status_code
                assert code == 401, f"{path} answered HTTP {code} without a token"
        self.check("the API refuses requests without the session token", refuses_without_token)

    def api_checks(self) -> None:
        def version():
            got = self.json("/api/settings")["version"]
            assert got == expected_version(), f"the app says {got}, the source says {expected_version()}"
            return got
        self.check("the running app reports the expected version", version)

        def queue():
            snap = self.json("/api/queue/snapshot")
            assert {"counts", "schedule", "is_paused"} <= set(snap), f"keys: {sorted(snap)}"
        self.check("queue snapshot (with schedule)", queue)

        def plugins():
            data = self.json("/api/plugins/packs")
            assert len(data["packs"]) >= 6 and data["builtin"], "plugin catalogue is empty"
            return f"{len(data['packs'])} packs"
        self.check("plugin packs are listed", plugins)

        def history():
            assert "items" in self.json("/api/history")
        self.check("download history answers", history)

        def duplicates():
            assert "groups" in self.json("/api/duplicates/resolve/plan")
        self.check("duplicate cleanup plan answers", duplicates)

        def health():
            report = self.json("/api/diagnostics/export")
            assert report["database"]["integrity_check"] == "PASS", report["database"]
            assert report["database"]["schema_migration_version"] >= MIN_SCHEMA_VERSION, report["database"]
            assert report["binaries"]["ffmpeg_exists"] and report["binaries"]["ffprobe_exists"], "ffmpeg/ffprobe not found"
            return f"schema v{report['database']['schema_migration_version']}"
        self.check("database healthy, migrations applied, ffmpeg found", health)

        def backup():
            resp = requests.post(f"{self.base}/api/backups/create", headers={"X-Session-Token": self.token}, timeout=30)
            assert resp.status_code == 200 and resp.json().get("success"), resp.text[:200]
            assert self.json("/api/backups/list")["backups"], "the new backup is not listed"
        self.check("a database backup can be made and listed", backup)

    def stop(self) -> None:
        def shutdown():
            assert self.proc is not None
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    raise AssertionError("the app ignored the stop request and had to be killed") from None
        self.check("stops when asked", shutdown)

        def cleanup():
            if self.keep:
                return f"kept {self.data_dir}"
            last: Optional[Exception] = None
            for _ in range(20):                     # Windows may need a moment to release the database files
                try:
                    shutil.rmtree(self.data_dir)
                    return None
                except OSError as e:
                    last = e
                    time.sleep(0.5)
            raise AssertionError(f"the data folder could not be removed (a file is still in use?): {last}")
        self.check("leaves no file locked behind", cleanup)

    def log_tail(self) -> str:
        log = self.data_dir / "logs" / "studio.log"
        if not log.exists():
            return "(no log was written)"
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:])

    def run(self) -> int:
        print(f"Smoke test: {' '.join(self.command)}\n  data folder: {self.data_dir}", flush=True)
        self.commands()
        if self.start():
            self.web()
            self.api_checks()
        failed = [r for r in self.results if not r[1]]
        if failed and self.proc is not None:
            print("\nEnd of the app's log:\n" + self.log_tail(), flush=True)
        self.stop() if self.proc is not None else None
        failed = [r for r in self.results if not r[1]]
        print(f"\n{len(self.results) - len(failed)} of {len(self.results)} checks passed" + ("" if not failed else f"; FAILED: {', '.join(n for n, _, _ in failed)}"))
        return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--exe", type=Path, help="path to the built SpotifyDownloader.exe")
    where.add_argument("--source", action="store_true", help="run main.py from this checkout with the current Python")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway data folder (for looking at the log)")
    args = parser.parse_args()
    if args.exe:
        exe = args.exe.resolve()
        if not exe.exists():
            print(f"Not found: {exe}", file=sys.stderr)
            return 2
        command = [str(exe)]
    else:
        command = [sys.executable, str(ROOT / "main.py")]
    return Smoke(command, keep=args.keep).run()


if __name__ == "__main__":
    sys.exit(main())
