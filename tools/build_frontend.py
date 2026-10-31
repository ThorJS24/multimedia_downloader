"""
Rebuild the vendored frontend assets (Tailwind CSS, lucide icons, fonts) into src/web/static.

Run after changing classes in templates/JS or bumping versions in tools/frontend/package.json:
    python tools/build_frontend.py

Needs Node/npm at build time only; the shipped app loads everything from /static (no CDN).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONT = ROOT / "tools" / "frontend"
STATIC = ROOT / "src" / "web" / "static"
FONTS = {
    "@fontsource/plus-jakarta-sans": [f"plus-jakarta-sans-latin-{w}-normal.woff2" for w in (300, 400, 500, 600, 700, 800)],
    "@fontsource/jetbrains-mono": [f"jetbrains-mono-latin-{w}-normal.woff2" for w in (400, 500, 600)],
}


def run(cmd, **kw):
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        sys.exit("npm not found: install Node.js to rebuild frontend assets")
    env = {**os.environ, "TEMP": str(ROOT / "temp"), "TMP": str(ROOT / "temp"),
           "npm_config_cache": str(FRONT / ".npm-cache"), "npm_config_update_notifier": "false"}
    subprocess.run([npm, *cmd], cwd=FRONT, env=env, check=True, **kw)


def main():
    if not (FRONT / "node_modules").exists():
        run(["ci", "--ignore-scripts", "--no-audit", "--no-fund"])  # exact versions from the lockfile
    (STATIC / "css").mkdir(parents=True, exist_ok=True)
    (STATIC / "fonts").mkdir(parents=True, exist_ok=True)
    (STATIC / "vendor").mkdir(parents=True, exist_ok=True)

    run(["run", "build"])
    shutil.copy2(FRONT / "node_modules" / "lucide" / "dist" / "umd" / "lucide.min.js", STATIC / "vendor" / "lucide.min.js")
    for pkg, files in FONTS.items():
        for name in files:
            shutil.copy2(FRONT / "node_modules" / pkg / "files" / name, STATIC / "fonts" / name)
    print("Frontend assets rebuilt in", STATIC)


if __name__ == "__main__":
    main()
