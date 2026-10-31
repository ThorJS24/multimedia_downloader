"""
Update checker (opt-in).

Nothing happens until `maintenance.update_feed_url` is set to an HTTPS URL serving JSON like

    {"version": "2.1.0", "url": "https://example.com/SpotifyDownloader-v2.1.0-Setup.exe",
     "sha256": "<64 hex chars>", "notes": "What changed"}

Flow: check -> (user clicks) download + verify SHA-256 -> (user clicks) launch installer. The launch step
additionally requires a valid Authenticode signature unless `maintenance.update_require_signature` is off,
because the hash comes from the same server as the file and only guards against corruption.
"""
import hashlib
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

from src import http_client
from src.config_loader import DATA_DIR
from src.settings_service import SettingsService
from src.version import __version__

logger = logging.getLogger("UpdateService")

UPDATES_DIR: Path = DATA_DIR / "data" / "updates"
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")


def parse_version(text: str) -> Tuple[int, ...]:
    if not isinstance(text, str) or not _VERSION_RE.match(text.strip().lstrip("vV")):
        raise ValueError(f"unsupported version string: {text!r}")
    parts = tuple(int(p) for p in text.strip().lstrip("vV").split("."))
    return parts + (0,) * (4 - len(parts))


class UpdateService:
    _last_result: Optional[Dict[str, Any]] = None

    @classmethod
    def last_result(cls) -> Optional[Dict[str, Any]]:
        return cls._last_result

    # ------------------------------------------------------------------ check
    @classmethod
    def check(cls) -> Dict[str, Any]:
        """Never raises. Returns {enabled, current, available, ...} or {error}."""
        cfg = SettingsService.get_all()["maintenance"]
        feed = cfg["update_feed_url"]
        result: Dict[str, Any] = {"enabled": bool(feed), "current": __version__, "available": False, "checked_at": time.time()}
        if not feed:
            cls._last_result = result
            return result
        try:
            resp = http_client.get(feed, timeout=10, retries=1)
            resp.raise_for_status()
            data = resp.json()
            latest = str(data["version"]).strip()
            url = str(data["url"]).strip()
            sha = str(data.get("sha256", "")).strip().lower()
            if urlparse(url).scheme != "https":
                raise ValueError("update URL must be https")
            if not _SHA256_RE.match(sha):
                raise ValueError("feed must provide a 64-character sha256")
            result.update({
                "latest": latest, "url": url, "sha256": sha, "notes": str(data.get("notes", ""))[:2000],
                "available": parse_version(latest) > parse_version(__version__),
            })
        except Exception as exc:
            logger.warning("Update check failed: %s", exc)
            result["error"] = f"Update check failed: {exc}"
        cls._last_result = result
        return result

    # --------------------------------------------------------------- download
    @classmethod
    def _target_path(cls, url: str) -> Path:
        name = Path(unquote(urlparse(url).path)).name
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "update.exe"
        if not name.lower().endswith(".exe"):
            raise ValueError("update file must be a .exe installer")
        return UPDATES_DIR / name

    @classmethod
    def download(cls, info: Optional[Dict[str, Any]] = None) -> Path:
        """Download the update announced by the last check and verify its SHA-256. Returns the verified path."""
        info = info or cls._last_result or {}
        if not info.get("available"):
            raise ValueError("No update is available")
        url, expected = info["url"], info["sha256"]
        if urlparse(url).scheme != "https":
            raise ValueError("update URL must be https")

        UPDATES_DIR.mkdir(parents=True, exist_ok=True)
        target = cls._target_path(url)
        part = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        size = 0
        with http_client.get(url, stream=True, timeout=30, retries=2) as resp:
            resp.raise_for_status()
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        fh.close()
                        part.unlink(missing_ok=True)
                        raise ValueError("update is larger than the 1 GB safety limit")
                    digest.update(chunk)
                    fh.write(chunk)
        if digest.hexdigest() != expected:
            part.unlink(missing_ok=True)
            raise ValueError("Downloaded file does not match the published SHA-256; it was discarded")
        os.replace(part, target)
        logger.info("Update %s downloaded and verified (%d bytes)", info.get("latest"), size)
        return target

    # ----------------------------------------------------------------- launch
    @classmethod
    def signature_status(cls, path: Path) -> str:
        """Authenticode status via PowerShell ('Valid', 'NotSigned', 'HashMismatch', ...)."""
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-AuthenticodeSignature -LiteralPath $args[0]).Status.ToString()", str(path)],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.stdout.strip() or "Unknown"

    @classmethod
    def launch_installer(cls, path: Path) -> None:
        """Start a previously downloaded installer (explicit user action)."""
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(UPDATES_DIR.resolve()) or not resolved.is_file():
            raise ValueError("Installer must be a downloaded update")
        if SettingsService.get_all()["maintenance"]["update_require_signature"]:
            status = cls.signature_status(resolved)
            if status != "Valid":
                raise PermissionError(
                    f"Installer signature is '{status}'. Updates are only launched when validly signed "
                    "(turn off maintenance.update_require_signature to override)."
                )
        os.startfile(str(resolved))  # noqa: S606 - deliberate, user-initiated
