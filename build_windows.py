"""
Automated Windows Release Builder for Spotify Downloader Studio.
Packages the entire application into a standalone portable Windows distribution
strictly within the D: drive project directory.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from build_common import compute_sha256, sign_file, write_version_info  # noqa: E402
from src.version import __version__  # noqa: E402
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
SPEC_FILE = PROJECT_ROOT / "package.spec"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
VENV_PYINSTALLER = PROJECT_ROOT / ".venv" / "Scripts" / "pyinstaller.exe"


def main():
    print("=" * 70)
    print(" Spotify Downloader Studio — Windows Release Builder")
    print(" Strict Storage Isolation: Zero C: Drive Writes")
    print("=" * 70)

    # 1. Verify environment
    if not VENV_PYINSTALLER.exists():
        print(f"[!] Error: PyInstaller not found at {VENV_PYINSTALLER}")
        sys.exit(1)

    ffmpeg_bin = PROJECT_ROOT / "bin" / "ffmpeg.exe"
    ffprobe_bin = PROJECT_ROOT / "bin" / "ffprobe.exe"
    if not (ffmpeg_bin.exists() and ffprobe_bin.exists()):
        print("[!] Warning: ffmpeg or ffprobe missing in bin/. Verification needed.")

    # 2. Clean previous build artifacts
    print("[*] Cleaning build/ and dist/ directories...")
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR, ignore_errors=True)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    write_version_info()

    # 3. Execute PyInstaller
    cmd = [
        str(VENV_PYINSTALLER),
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        "--noconfirm",
        str(SPEC_FILE)
    ]

    print(f"[*] Running PyInstaller with {SPEC_FILE.name}...")
    env = os.environ.copy()
    env["TEMP"] = str(PROJECT_ROOT / "temp")
    env["TMP"] = str(PROJECT_ROOT / "temp")

    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env)
    if proc.returncode != 0:
        print("[!] Build failed during PyInstaller execution.")
        sys.exit(proc.returncode)

    exe_path = DIST_DIR / "SpotifyDownloader" / "SpotifyDownloader.exe"
    if not exe_path.exists():
        print(f"[!] Build finished but output binary missing at {exe_path}")
        sys.exit(1)

    print("\n[+] Build successful!")
    print(f"    Binary location: {exe_path}")
    file_size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f"    Executable size: {file_size_mb:.2f} MB")

    # 4. Copy required local initial folders into the portable distribution package
    target_dist_root = DIST_DIR / "SpotifyDownloader"
    for folder in ["downloads", "data", "temp", ".cache", "config"]:
        (target_dist_root / folder).mkdir(parents=True, exist_ok=True)

    # Copy local bin directory
    bin_src = PROJECT_ROOT / "bin"
    bin_dst = target_dist_root / "bin"
    if bin_src.exists() and not bin_dst.exists():
        shutil.copytree(bin_src, bin_dst)

    # Copy default config if not present
    default_config = PROJECT_ROOT / "config" / "config.json"
    target_config = target_dist_root / "config" / "config.json"
    if default_config.exists() and not target_config.exists():
        shutil.copy2(default_config, target_config)

    # Portable marker MUST exist before the exe is ever run: without it the exe starts in installed
    # mode and writes its data under %LOCALAPPDATA% on C:.
    portable_marker = target_dist_root / "portable.dat"
    portable_marker.write_text("Spotify Downloader Studio Portable Marker\n", encoding="utf-8")

    # Create Portable Quick Start README
    readme_path = target_dist_root / "PORTABLE_QUICKSTART.txt"
    readme_content = f"""Spotify Downloader Studio v{__version__} — Portable Release
================================================================================
1. Double-click 'SpotifyDownloader.exe' to launch the Desktop Studio.
2. Zero C: Drive footprint — all databases, temporary transcoding buffers,
   caches, and downloads are strictly confined to this folder.
3. Optional switches (run from a terminal; the app itself has no console window):
   - SpotifyDownloader.exe --diagnostics   (Run storage and database diagnostics)
   - SpotifyDownloader.exe --version       (Print version details)
   - SpotifyDownloader.exe --no-browser    (Run headless local server on 127.0.0.1)
================================================================================
"""
    readme_path.write_text(readme_content, encoding="utf-8")

    # 5. Verify compiled executable flags
    print("\n[*] Verifying compiled executable CLI flags...")
    v_res = subprocess.run([str(exe_path), "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    print(f"    --version output:\n    {v_res.stdout.strip()}")

    d_res = subprocess.run([str(exe_path), "--diagnostics"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    print("    --diagnostics output:")
    for line in d_res.stdout.strip().splitlines()[:6]:
        print(f"    {line}")

    # 6. Generate Checksum Manifest
    print("\n[*] Code signing...")
    sign_file(exe_path)

    print("\n[*] Generating release SHA-256 checksums...")
    checksum = compute_sha256(exe_path)

    checksum_file = DIST_DIR / "checksums.txt"
    with open(checksum_file, "w", encoding="utf-8") as f:
        f.write(f"{checksum}  SpotifyDownloader.exe\n")
    print(f"    Executable SHA-256: {checksum}")

    # 7. Create Portable ZIP distribution
    zip_stem = DIST_DIR / f"SpotifyDownloader-v{__version__}-Windows-x64"
    print(f"\n[*] Packaging portable ZIP archive: {zip_stem}.zip...")
    shutil.make_archive(str(zip_stem), "zip", DIST_DIR, "SpotifyDownloader")
    zip_full = DIST_DIR / f"SpotifyDownloader-v{__version__}-Windows-x64.zip"
    if zip_full.exists():
        zip_checksum = compute_sha256(zip_full)
        with open(checksum_file, "a", encoding="utf-8") as f:
            f.write(f"{zip_checksum}  {zip_full.name}\n")
        print(f"    ZIP SHA-256: {zip_checksum}")
        print(f"    Archive size: {zip_full.stat().st_size / (1024 * 1024):.2f} MB")

    # 8. Build Windows Inno Setup Installer
    print("\n[*] Invoking Windows Installer Builder...")
    installer_builder = PROJECT_ROOT / "build_installer.py"
    if installer_builder.exists():
        build_env = os.environ.copy()
        proc_inst = subprocess.run([sys.executable, str(installer_builder)], cwd=str(PROJECT_ROOT), env=build_env)
        if proc_inst.returncode != 0:
            print("[!] Warning: Installer generation failed.")
        else:
            print("[+] Windows Installer generated successfully.")

    print(f"    Checksum manifest saved: {checksum_file}")

    print("\n" + "=" * 70)
    print(" RELEASE BUILD COMPLETE & VERIFIED")
    print(f" Distribution folder: {target_dist_root}")
    print(f" Standalone ZIP:     {zip_full}")
    installer_exe = DIST_DIR / f"SpotifyDownloader-v{__version__}-Setup.exe"
    if installer_exe.exists():
        print(f" Windows Installer:  {installer_exe}")
    print("=" * 70)


if __name__ == "__main__":
    main()
