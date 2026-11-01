import json
import logging
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from src.config_loader import DATA_DIR
from src.instance_lock import instance_lock
from src.security import SESSION_AUTH_TOKEN

logger = logging.getLogger("DesktopShell")

WINDOW_STATE_FILE = DATA_DIR / "window_state.json"
DEFAULT_WINDOW = {"width": 1360, "height": 860}
MIN_WINDOW = (960, 640)


def notify_user(title: str, message: str) -> None:
    """Tell the person something when there may be no console (the packaged app is a windowed exe)."""
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)      # MB_ICONINFORMATION
        except Exception:
            logger.debug("Could not show message box", exc_info=True)


def find_free_port() -> int:
    """Finds an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, timeout: float = 15.0, alive=None) -> bool:
    """Block until the local server accepts connections (replaces a fixed sleep).

    `alive` lets the caller abort early when the server thread has already died.
    """
    interval = 0.05
    for _ in range(int(timeout / interval)):
        if alive is not None and not alive():
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return True
        except OSError:
            time.sleep(interval)
    return False


def load_window_state(path: Path = WINDOW_STATE_FILE) -> dict:
    """Last window size/position, sanitised so a bad file or a vanished monitor can't hide the window."""
    state = dict(DEFAULT_WINDOW)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    if not isinstance(saved, dict):
        return state
    for key, low, high in (("width", MIN_WINDOW[0], 7680), ("height", MIN_WINDOW[1], 4320)):
        value = saved.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
            state[key] = value
    for key in ("x", "y"):
        value = saved.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and -64 <= value <= 7680:
            state[key] = value
    return state


def save_window_state(state: dict, path: Path = WINDOW_STATE_FILE) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        logger.debug("Could not persist window state", exc_info=True)


class ShellBridge:
    """Lets Flask routes reach native desktop features (folder dialog) when running in the window."""

    def __init__(self):
        self._window = None

    def attach(self, window) -> None:
        self._window = window

    def detach(self) -> None:
        self._window = None

    def available(self) -> bool:
        return self._window is not None

    def pick_folder(self, start: str = "") -> str:
        if self._window is None:
            return ""
        import webview
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=start or "")
        if not result:
            return ""
        return str(result[0] if isinstance(result, (list, tuple)) else result)


shell_bridge = ShellBridge()


class DesktopApp:
    def __init__(self, port: Optional[int] = None, use_browser: bool = False, headless: bool = False, url_file: Optional[str] = None):
        self.port = port or find_free_port()
        self.use_browser = use_browser
        self.headless = headless
        self.url_file = url_file
        self.server_thread: Optional[threading.Thread] = None
        self.window: Any = None
        self.is_shutting_down = False

    def launch(self):
        # 1. Enforce single-instance lock
        if not instance_lock.acquire():
            print("\n[!] Another instance of Spotify Downloader Studio is already running.", flush=True)
            print("[!] Please check your system tray or taskbar.\n", flush=True)
            notify_user("Spotify Downloader Studio", "Spotify Downloader Studio is already running.\n\nLook for its window in the taskbar, or its icon next to the clock if it was minimised to the tray.")
            sys.exit(0)

        # 2. Start background Flask server
        from src.web.server import serve_app, start_background_services
        start_background_services()
        url = f"http://127.0.0.1:{self.port}?token={SESSION_AUTH_TOKEN}"

        def run_server():
            logger.info(f"Starting background server on port {self.port}")
            serve_app("127.0.0.1", self.port)      # waitress (production WSGI server)

        self.server_thread = threading.Thread(target=run_server, daemon=True, name="StudioServerThread")
        self.server_thread.start()

        # Open as soon as the server answers (usually well under a second) instead of a fixed sleep.
        if not wait_for_port(self.port, alive=self.server_thread.is_alive):
            logger.warning("Server did not accept connections within the startup window; continuing anyway.")

        # 3. Headless, Browser, OR Native WebView Window
        if self.headless:
            if self.url_file:
                try:
                    Path(self.url_file).write_text(url, encoding="utf-8")
                except OSError:
                    logger.warning("Could not write the startup address to %s", self.url_file, exc_info=True)
            print(f"\n[*] Spotify Downloader Studio running in headless mode: {url}", flush=True)
            print("[*] Press Ctrl+C to stop the local server.\n", flush=True)
            try:
                while not self.is_shutting_down:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.shutdown()
        elif self.use_browser:
            import webbrowser
            print(f"\n[*] Opening Spotify Downloader Studio in browser: {url}\n", flush=True)
            webbrowser.open(url)
            try:
                while not self.is_shutting_down:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.shutdown()
        else:
            try:
                import webview
                logger.info("Initializing pywebview native window...")

                state = load_window_state()
                kwargs: Dict[str, Any] = {}
                if "x" in state and "y" in state:
                    kwargs.update(x=state["x"], y=state["y"])
                self.window = webview.create_window(
                    title="Spotify Downloader Studio",
                    url=url,
                    width=state["width"],
                    height=state["height"],
                    min_size=MIN_WINDOW,
                    background_color="#0a0a0a",
                    text_select=True,
                    **kwargs,
                )
                shell_bridge.attach(self.window)

                # Bind closing callback for clean shutdown (and remember where the window was)
                # The closing handler runs on the WinForms UI thread: it must NOT raise (a SystemExit there
                # surfaces as an "Unhandled exception" dialog), so it only cleans up. The process ends below.
                self.window.events.closing += self._on_window_closing
                self.window.events.minimized += self._on_minimized
                self._start_tray()
                webview.start(debug=False)
                self.shutdown(exit_process=False)   # normally already done by the closing handler (idempotent)
                sys.exit(0)

            except Exception as e:
                logger.warning(f"pywebview initialization failed ({e}), falling back to browser.")
                shell_bridge.detach()
                import webbrowser
                webbrowser.open(url)
                try:
                    while not self.is_shutting_down:
                        time.sleep(1)
                except KeyboardInterrupt:
                    self.shutdown()

    # ---- tray icon (notifications and minimise-to-tray); only exists while the window does
    def _start_tray(self):
        try:
            from src.tray_service import TrayService
            from src.web.state import queue_manager

            def toggle_pause():
                queue_manager.resume() if queue_manager.is_paused else queue_manager.pause()

            TrayService.configure(on_open=self._show_window, on_quit=self._quit_from_tray,
                                  on_toggle_pause=toggle_pause, is_paused=lambda: queue_manager.is_paused)
        except Exception:
            logger.warning("The tray icon could not be set up; notifications are unavailable", exc_info=True)

    def _show_window(self):
        window = self.window
        if window is None:
            return
        window.show()
        window.restore()

    def _quit_from_tray(self):
        if self.window is not None:
            self.window.destroy()          # runs the normal closing path, so everything shuts down cleanly

    def _on_minimized(self):
        from src.tray_service import TrayService
        if self.window is not None and TrayService.minimize_to_tray_enabled():
            self.window.hide()

    def _remember_window(self):
        window = self.window
        if window is None:
            return
        try:
            state = {"width": int(window.width), "height": int(window.height)}
            x, y = getattr(window, "x", None), getattr(window, "y", None)
            if isinstance(x, int) and isinstance(y, int):
                state.update(x=x, y=y)
            save_window_state(state)
        except Exception:
            logger.debug("Could not read window geometry", exc_info=True)

    def _on_window_closing(self):
        """Window is closing: remember geometry and stop services, but never raise into the GUI thread."""
        try:
            self._remember_window()
            self.shutdown(exit_process=False)
        except Exception:
            logger.exception("Error while closing the window")

    def shutdown(self, exit_process: bool = True):
        if self.is_shutting_down:
            return
        self.is_shutting_down = True
        shell_bridge.detach()
        logger.info("Initiating clean application shutdown...")
        try:
            from src.tray_service import TrayService
            TrayService.shutdown()
        except Exception:
            logger.debug("Removing the tray icon during shutdown failed", exc_info=True)
        try:
            from src.folder_watcher import FolderWatcherService
            FolderWatcherService.stop()
        except Exception:
            logger.debug("Stopping the folder watcher during shutdown failed", exc_info=True)
        try:
            from src.maintenance_service import MaintenanceService
            MaintenanceService.stop()
        except Exception:
            logger.debug("Maintenance service was not running")
        try:
            # Release mutex lock
            instance_lock.release()
        except Exception:
            logger.debug("Releasing the single-instance lock during shutdown failed", exc_info=True)
        logger.info("Shutdown completed. Exiting cleanly.")
        if exit_process:
            sys.exit(0)
