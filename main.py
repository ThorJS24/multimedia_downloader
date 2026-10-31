"""Spotify Downloader Studio launcher.

Opening the app (no arguments) goes straight to the desktop window. There is no command-line mode: all
features live in the application. The remaining flags exist for packaging, support and AI-assistant
integration (--version, --diagnostics, --headless, --browser, `web`, `mcp`).
"""
import argparse
import ctypes
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _ensure_console_output() -> None:
    """The packaged exe is a windowed app (no console pops up). When it is started from a terminal for
    --version / --diagnostics, reattach to that terminal so the text is visible there."""
    if sys.platform != "win32" or sys.stdout is not None:
        return
    try:
        if ctypes.windll.kernel32.AttachConsole(-1):          # ATTACH_PARENT_PROCESS
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            sys.stderr = sys.stdout
    except Exception:
        pass


_ensure_console_output()

# Ensure Windows terminal handles UTF-8 cleanly
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Enforce strictly local project paths (importing config_loader redirects TEMP/TMP into the project)
from src.config_loader import PROJECT_ROOT  # noqa: E402,F811
from src.logging_setup import setup_logging  # noqa: E402
from src.version import VERSION_LABEL  # noqa: E402

setup_logging()


def cmd_web(port=None, use_browser=False, headless=False):
    from src.desktop_shell import DesktopApp
    DesktopApp(port=port, use_browser=use_browser, headless=headless).launch()


def cmd_diagnostics():
    import shutil

    from src.backup_service import BackupService
    from src.config_loader import get_app_dir, get_data_dir, is_portable_mode

    ok, msg = BackupService.verify_database_integrity()
    app_d = get_app_dir()
    data_d = get_data_dir()
    portable = is_portable_mode()
    total, used, free = shutil.disk_usage(data_d)
    print("=== Spotify Downloader Studio Diagnostics ===")
    print(f"Operation Mode: {'PORTABLE' if portable else 'INSTALLED'}")
    print(f"Application Directory: {app_d}")
    print(f"User Data Directory: {data_d}")
    print(f"Database Integrity: {'PASS (OK)' if ok else 'FAIL'} ({msg})")
    print(f"Storage Free: {free // (2**30)} GB Free / {total // (2**30)} GB Total")
    if portable:
        print("Containment: 100% Isolated to application folder (0 bytes on C:)")
    else:
        print("Containment: Standard Windows per-user storage (%LOCALAPPDATA%)")


def cmd_mcp(transport: str, port: int):
    from src.mcp_server.server import mcp
    if transport == "sse":
        import asyncio
        asyncio.run(mcp.run_sse_async(port=port))
    else:
        mcp.run()           # stdio transport for Claude Desktop / IDEs / other MCP clients


def main():
    parser = argparse.ArgumentParser(description="Spotify Downloader Studio - Desktop Edition")
    parser.add_argument("--version", action="version", version=VERSION_LABEL)
    parser.add_argument("--diagnostics", action="store_true", help="Print system diagnostics and exit")
    parser.add_argument("--portable", action="store_true", help="Run in portable mode (store all data within application folder)")
    parser.add_argument("--data-dir", type=str, default=None, help="Custom directory for storing user data, DB, and configs")
    parser.add_argument("--browser", action="store_true", help="Open in the default web browser instead of the native desktop window")
    parser.add_argument("--headless", "--no-browser", dest="headless", action="store_true",
                        help="Run the local server on 127.0.0.1 without a GUI (--no-browser is an alias)")

    subparsers = parser.add_subparsers(dest="command", help="Optional command")

    w_parser = subparsers.add_parser("web", help="Launch the desktop app (same as running with no command)")
    w_parser.add_argument("--port", type=int, default=None, help="Specific port to bind (default: dynamic free port)")
    w_parser.add_argument("--browser", action="store_true", help="Force launch in web browser")
    w_parser.add_argument("--headless", "--no-browser", dest="web_headless", action="store_true",
                          help="Run the local server without a GUI (--no-browser is an alias)")

    mcp_parser = subparsers.add_parser("mcp", help="Start the Model Context Protocol (MCP) server for AI assistants")
    mcp_parser.add_argument("--transport", default="stdio", choices=["stdio", "sse"], help="MCP transport protocol (stdio or sse)")
    mcp_parser.add_argument("--port", type=int, default=8000, help="Port for SSE transport")

    args = parser.parse_args()

    if args.diagnostics:
        cmd_diagnostics()
        return

    headless = bool(args.headless or getattr(args, "web_headless", False))

    if args.command == "mcp":
        cmd_mcp(args.transport, args.port)
    elif args.command == "web":
        cmd_web(port=args.port, use_browser=(args.browser and not headless), headless=headless)
    else:
        # No command: open the application directly (desktop window; browser/headless only if asked).
        cmd_web(port=None, use_browser=(args.browser and not headless), headless=headless)


if __name__ == "__main__":
    main()
