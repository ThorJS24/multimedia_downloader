"""System-tray icon, desktop notifications and minimise-to-tray (Windows; plain ctypes, no extra dependency).

The OS plumbing (`_Win32Tray`) is kept apart from the decisions (`TrayService`): what to show and when, whether
the setting allows a notification, how the message reads. The tray only exists in the desktop window and only while
at least one of the notification settings is on; otherwise there is no icon at all.
"""
import ctypes
import logging
import queue
import sys
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from src.config_loader import PROJECT_ROOT
from src.settings_service import SettingsService

logger = logging.getLogger("TrayService")

APP_TITLE = "Spotify Downloader Studio"
WM_USER = 0x0400
WM_TRAY = WM_USER + 1
WM_WORK = WM_USER + 2
WM_DESTROY, WM_CLOSE, WM_NULL = 0x0002, 0x0010, 0x0000
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
HWND_MESSAGE = -3
MF_STRING, MF_SEPARATOR = 0x0, 0x800
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x2, 0x100
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
IDI_APPLICATION = 32512
CMD_OPEN, CMD_PAUSE, CMD_QUIT = 1, 2, 3


def batch_message(counts: Dict[str, int]) -> Optional[tuple]:
    """(title, text) for a finished batch of downloads, or None when nothing finished."""
    done, failed, skipped = counts.get("completed", 0), counts.get("failed", 0), counts.get("skipped", 0)
    if done + failed == 0:
        return None
    parts = []
    if done:
        parts.append(f"{done} track{'' if done == 1 else 's'} downloaded")
    if failed:
        parts.append(f"{failed} failed")
    if skipped:
        parts.append(f"{skipped} skipped")
    return ("Downloads finished" if not failed else "Downloads finished with problems", ", ".join(parts) + ".")


if sys.platform == "win32":
    class _NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
            ("uTimeout", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON),
        ]

    _WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
        ]


class _Api:
    """The few Win32 calls the tray needs, with explicit signatures (handles are 64-bit on x64 Windows)."""

    def __init__(self) -> None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        sh = ctypes.WinDLL("shell32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        H, UINT, WP, LP = wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM

        def sig(fn, restype, *argtypes):
            fn.restype, fn.argtypes = restype, list(argtypes)
            return fn

        self.DefWindowProc = sig(u.DefWindowProcW, ctypes.c_ssize_t, H, UINT, WP, LP)
        self.CreateWindowEx = sig(u.CreateWindowExW, H, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
        self.RegisterClass = sig(u.RegisterClassW, wintypes.ATOM, ctypes.POINTER(_WNDCLASSW))
        self.RegisterWindowMessage = sig(u.RegisterWindowMessageW, UINT, wintypes.LPCWSTR)
        self.PostMessage = sig(u.PostMessageW, wintypes.BOOL, H, UINT, WP, LP)
        self.DestroyWindow = sig(u.DestroyWindow, wintypes.BOOL, H)
        self.PostQuitMessage = sig(u.PostQuitMessage, None, ctypes.c_int)
        self.GetMessage = sig(u.GetMessageW, ctypes.c_int, ctypes.POINTER(wintypes.MSG), H, UINT, UINT)
        self.TranslateMessage = sig(u.TranslateMessage, wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
        self.DispatchMessage = sig(u.DispatchMessageW, ctypes.c_ssize_t, ctypes.POINTER(wintypes.MSG))
        self.CreatePopupMenu = sig(u.CreatePopupMenu, wintypes.HMENU)
        self.AppendMenu = sig(u.AppendMenuW, wintypes.BOOL, wintypes.HMENU, UINT, ctypes.c_size_t, wintypes.LPCWSTR)
        self.TrackPopupMenu = sig(u.TrackPopupMenu, wintypes.BOOL, wintypes.HMENU, UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, H, wintypes.LPVOID)
        self.DestroyMenu = sig(u.DestroyMenu, wintypes.BOOL, wintypes.HMENU)
        self.GetCursorPos = sig(u.GetCursorPos, wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
        self.SetForegroundWindow = sig(u.SetForegroundWindow, wintypes.BOOL, H)
        self.LoadImage = sig(u.LoadImageW, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPCWSTR, UINT, ctypes.c_int, ctypes.c_int, UINT)
        self.LoadIcon = sig(u.LoadIconW, wintypes.HICON, wintypes.HINSTANCE, ctypes.c_void_p)
        self.ExtractIcon = sig(sh.ExtractIconW, wintypes.HICON, wintypes.HINSTANCE, wintypes.LPCWSTR, UINT)
        self.ShellNotifyIcon = sig(sh.Shell_NotifyIconW, wintypes.BOOL, wintypes.DWORD, ctypes.POINTER(_NOTIFYICONDATAW))
        self.GetModuleHandle = sig(k.GetModuleHandleW, wintypes.HINSTANCE, wintypes.LPCWSTR)


class _Win32Tray:
    """One hidden message window plus one notification-area icon, on a thread of its own."""

    def __init__(self, tooltip: str, on_open: Callable[[], None], on_quit: Callable[[], None],
                 on_toggle_pause: Callable[[], None], is_paused: Callable[[], bool]):
        self.tooltip, self.on_open, self.on_quit = tooltip, on_open, on_quit
        self.on_toggle_pause, self.is_paused = on_toggle_pause, is_paused
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._ok = False
        self._hwnd: Any = None
        self._work: "queue.Queue[tuple]" = queue.Queue()
        self._icon: Any = None
        self._wndproc: Any = None
        self._taskbar_created = 0
        self._api: Optional[_Api] = None

    # ---- public (any thread)
    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name="TrayIcon")
        self._thread.start()
        self._ready.wait(5.0)
        return self._ok

    def stop(self) -> None:
        if self._ok and self._hwnd and self._api:
            self._api.PostMessage(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(3.0)
        self._ok = False

    def notify(self, title: str, text: str) -> None:
        if self._ok and self._hwnd and self._api:
            self._work.put((title, text))
            self._api.PostMessage(self._hwnd, WM_WORK, 0, 0)

    # ---- tray thread
    def _load_icon(self) -> Any:
        api = self._api
        assert api is not None
        for candidate in (PROJECT_ROOT / "assets" / "icon.ico", Path(getattr(sys, "_MEIPASS", ".")) / "assets" / "icon.ico"):
            if candidate.exists():
                handle = api.LoadImage(None, str(candidate), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
                if handle:
                    return handle
        handle = api.ExtractIcon(None, sys.executable, 0)      # the packaged exe carries the app icon
        if handle and handle != 1:
            return handle
        return api.LoadIcon(None, ctypes.c_void_p(IDI_APPLICATION))

    def _nid(self, flags: int) -> "_NOTIFYICONDATAW":
        nid = _NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = flags
        return nid

    def _add_icon(self) -> bool:
        assert self._api is not None
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._icon
        nid.szTip = self.tooltip[:127]
        return bool(self._api.ShellNotifyIcon(NIM_ADD, ctypes.byref(nid)))

    def _balloon(self, title: str, text: str) -> None:
        assert self._api is not None
        nid = self._nid(NIF_INFO)
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = NIIF_INFO
        self._api.ShellNotifyIcon(NIM_MODIFY, ctypes.byref(nid))

    def _menu(self) -> None:
        api = self._api
        assert api is not None
        menu = api.CreatePopupMenu()
        api.AppendMenu(menu, MF_STRING, CMD_OPEN, f"Open {APP_TITLE}")
        api.AppendMenu(menu, MF_STRING, CMD_PAUSE, "Resume downloads" if self.is_paused() else "Pause downloads")
        api.AppendMenu(menu, MF_SEPARATOR, 0, None)
        api.AppendMenu(menu, MF_STRING, CMD_QUIT, "Quit")
        point = wintypes.POINT()
        api.GetCursorPos(ctypes.byref(point))
        api.SetForegroundWindow(self._hwnd)             # without this the menu does not close when you click elsewhere
        command = api.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y, 0, self._hwnd, None)
        api.PostMessage(self._hwnd, WM_NULL, 0, 0)
        api.DestroyMenu(menu)
        action = {CMD_OPEN: self.on_open, CMD_PAUSE: self.on_toggle_pause, CMD_QUIT: self.on_quit}.get(command)
        if action:
            threading.Thread(target=action, daemon=True, name="TrayAction").start()    # never block the message loop

    def _proc(self, hwnd, msg, wparam, lparam):
        api = self._api
        assert api is not None
        try:
            if msg == WM_TRAY:
                event = lparam & 0xFFFF
                if event == WM_LBUTTONUP:
                    threading.Thread(target=self.on_open, daemon=True, name="TrayOpen").start()
                elif event == WM_RBUTTONUP:
                    self._menu()
                return 0
            if msg == WM_WORK:
                while True:
                    try:
                        title, text = self._work.get_nowait()
                    except queue.Empty:
                        break
                    self._balloon(title, text)
                return 0
            if self._taskbar_created and msg == self._taskbar_created:      # Explorer restarted: put the icon back
                self._add_icon()
                return 0
            if msg == WM_CLOSE:
                api.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                nid = self._nid(0)
                api.ShellNotifyIcon(NIM_DELETE, ctypes.byref(nid))
                api.PostQuitMessage(0)
                return 0
        except Exception:
            logger.exception("Tray message handler failed")
        return api.DefWindowProc(hwnd, msg, wparam, lparam)

    def _run(self) -> None:
        api: Optional[_Api] = None
        try:
            api = self._api = _Api()
            self._taskbar_created = api.RegisterWindowMessage("TaskbarCreated")
            self._wndproc = _WNDPROC(self._proc)                       # kept on self so it is not garbage collected
            wc = _WNDCLASSW()
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = api.GetModuleHandle(None)
            wc.lpszClassName = "StudioTrayWindow"
            api.RegisterClass(ctypes.byref(wc))
            self._hwnd = api.CreateWindowEx(0, "StudioTrayWindow", APP_TITLE, 0, 0, 0, 0, 0,
                                            wintypes.HWND(HWND_MESSAGE), None, wc.hInstance, None)
            self._icon = self._load_icon()
            self._ok = bool(self._hwnd) and self._add_icon()
        except Exception:
            logger.exception("Could not create the tray icon")
            self._ok = False
        finally:
            self._ready.set()
        if not self._ok or api is None:
            return
        msg = wintypes.MSG()
        while api.GetMessage(ctypes.byref(msg), None, 0, 0) > 0:
            api.TranslateMessage(ctypes.byref(msg))
            api.DispatchMessage(ctypes.byref(msg))


class TrayService:
    """Decides whether the tray exists and what it says. Only the desktop window calls `configure`."""
    backend_factory: Callable[..., Any] = _Win32Tray
    _backend: Optional[Any] = None
    _callbacks: Optional[Dict[str, Callable]] = None
    _lock = threading.RLock()

    @classmethod
    def settings(cls) -> Dict[str, Any]:
        return dict(SettingsService.get_all()["notifications"])

    @classmethod
    def configure(cls, on_open: Callable[[], None], on_quit: Callable[[], None],
                  on_toggle_pause: Callable[[], None], is_paused: Callable[[], bool]) -> None:
        with cls._lock:
            cls._callbacks = {"on_open": on_open, "on_quit": on_quit, "on_toggle_pause": on_toggle_pause, "is_paused": is_paused}
        cls.apply_settings()

    @classmethod
    def active(cls) -> bool:
        return cls._backend is not None

    @classmethod
    def apply_settings(cls) -> None:
        """Create or remove the icon so it matches the settings (called at start and whenever settings are saved)."""
        with cls._lock:
            if cls._callbacks is None:
                return                                              # not the desktop window: there is no tray
            cfg = cls.settings()
            wanted = bool(cfg["enabled"] or cfg["minimize_to_tray"])
            if wanted and cls._backend is None:
                backend = cls.backend_factory(APP_TITLE, **cls._callbacks)
                if backend.start():
                    cls._backend = backend
                else:
                    logger.warning("The tray icon could not be created; notifications and minimise-to-tray are unavailable")
            elif not wanted and cls._backend is not None:
                cls._backend.stop()
                cls._backend = None

    @classmethod
    def minimize_to_tray_enabled(cls) -> bool:
        return cls.active() and bool(cls.settings()["minimize_to_tray"])

    @classmethod
    def notify(cls, title: str, text: str) -> bool:
        with cls._lock:
            if cls._backend is None or not cls.settings()["enabled"]:
                return False
            cls._backend.notify(title, text)
            return True

    @classmethod
    def notify_batch_finished(cls, counts: Dict[str, int]) -> bool:
        message = batch_message(counts)
        return cls.notify(*message) if message else False

    @classmethod
    def shutdown(cls) -> None:
        with cls._lock:
            if cls._backend is not None:
                try:
                    cls._backend.stop()
                except Exception:
                    logger.warning("Removing the tray icon failed", exc_info=True)
                cls._backend = None
            cls._callbacks = None


SettingsService.on_change(TrayService.apply_settings)
