import ctypes
import logging
import os

from src.config_loader import PROJECT_ROOT

logger = logging.getLogger("InstanceLock")

MUTEX_NAME = "Global\\SpotifyDownloaderStudio_SingleInstanceMutex"
LOCK_FILE_PATH = PROJECT_ROOT / "data" / "app.lock"


class SingleInstanceLock:
    """
    Guarantees only one instance of Spotify Downloader Studio runs at a time.
    Uses Windows Named Mutex where available with cross-platform lockfile fallback.
    """
    def __init__(self):
        self.mutex_handle = None
        self.lock_fd = None
        self.is_primary = False

    def acquire(self) -> bool:
        if os.name == "nt":
            try:
                # ERROR_ALREADY_EXISTS = 183
                kernel32 = ctypes.windll.kernel32
                self.mutex_handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
                last_error = kernel32.GetLastError()
                if last_error == 183:  # Another instance is already running
                    # Close our handle to the existing mutex, otherwise it stays alive after the
                    # real owner exits and blocks every future launch.
                    kernel32.CloseHandle(self.mutex_handle)
                    self.mutex_handle = None
                    self.is_primary = False
                    return False
                self.is_primary = True
                return True
            except Exception:
                logger.warning("Creating the single-instance mutex failed; falling back to a PID lock file", exc_info=True)

        # Fallback to PID lockfile
        try:
            LOCK_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            if LOCK_FILE_PATH.exists():
                try:
                    pid = int(LOCK_FILE_PATH.read_text().strip())
                    # Check if process is actually running
                    if os.name == "nt":
                        import subprocess
                        res = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], stdout=subprocess.PIPE, text=True)
                        if str(pid) in res.stdout:
                            self.is_primary = False
                            return False
                except Exception:
                    logger.debug("Instance-lock process probe failed", exc_info=True)

            LOCK_FILE_PATH.write_text(str(os.getpid()))
            self.is_primary = True
            return True
        except Exception:
            self.is_primary = True
            return True

    def release(self):
        if self.mutex_handle and os.name == "nt":
            try:
                ctypes.windll.kernel32.ReleaseMutex(self.mutex_handle)
                ctypes.windll.kernel32.CloseHandle(self.mutex_handle)
                self.mutex_handle = None
            except Exception:
                logger.debug("Releasing the instance mutex failed", exc_info=True)

        if LOCK_FILE_PATH.exists() and self.is_primary:
            try:
                LOCK_FILE_PATH.unlink()
            except Exception:
                logger.debug("Removing the instance lock file failed", exc_info=True)


# Global singleton lock
instance_lock = SingleInstanceLock()
