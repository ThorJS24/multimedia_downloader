"""
Automated Windows Installer Builder for Spotify Downloader Studio.
Compiles installer.iss using local Inno Setup Compiler (ISCC.exe).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from build_common import compute_sha256, sign_file  # noqa: E402
from src.version import __version__  # noqa: E402
DIST_DIR = PROJECT_ROOT / "dist"
ISS_FILE = PROJECT_ROOT / "installer.iss"
LOCAL_ISCC = PROJECT_ROOT / "tools" / "InnoSetup" / "ISCC.exe"


def find_iscc() -> Path:
    if LOCAL_ISCC.exists():
        return LOCAL_ISCC

    system_iscc = shutil.which("iscc") or shutil.which("ISCC")
    if system_iscc:
        return Path(system_iscc)

    common_paths = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    ]
    for p in common_paths:
        if p.exists():
            return p

    raise FileNotFoundError("Inno Setup Compiler (ISCC.exe) not found.")


def main():
    print("=" * 70)
    print(" Spotify Downloader Studio — Windows Installer Builder")
    print("=" * 70)

    try:
        iscc_path = find_iscc()
        print(f"[*] Inno Setup Compiler: {iscc_path}")
    except FileNotFoundError as e:
        print(f"[!] Error: {e}")
        sys.exit(1)

    app_bin = DIST_DIR / "SpotifyDownloader" / "SpotifyDownloader.exe"
    if not app_bin.exists():
        print(f"[!] Error: Built application binary missing at {app_bin}")
        print("    Please run build_windows.py first.")
        sys.exit(1)

    icon_path = PROJECT_ROOT / "assets" / "icon.ico"
    if not icon_path.exists():
        print(f"[!] Error: Application icon missing at {icon_path}")
        sys.exit(1)

    print(f"[*] Compiling installer script {ISS_FILE.name}...")
    cmd = [str(iscc_path), f"/DMyAppVersion={__version__}", str(ISS_FILE)]

    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if proc.returncode != 0:
        print("[!] Installer compilation failed.")
        sys.exit(proc.returncode)

    installer_path = DIST_DIR / f"SpotifyDownloader-v{__version__}-Setup.exe"
    if not installer_path.exists():
        print(f"[!] Output installer not found at {installer_path}")
        sys.exit(1)

    sign_file(installer_path)

    size_mb = installer_path.stat().st_size / (1024 * 1024)
    sha256 = compute_sha256(installer_path)

    print("\n[+] Installer generated successfully!")
    print(f"    Path:    {installer_path}")
    print(f"    Size:    {size_mb:.2f} MB")
    print(f"    SHA-256: {sha256}")

    # Update checksums.txt
    checksum_file = DIST_DIR / "checksums.txt"
    with open(checksum_file, "a", encoding="utf-8") as f:
        f.write(f"{sha256}  {installer_path.name}\n")
    print(f"    Updated {checksum_file.name}")


if __name__ == "__main__":
    main()
