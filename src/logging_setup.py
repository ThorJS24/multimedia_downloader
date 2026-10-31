"""
Central logging configuration.

- Every handler formats through RedactingFormatter, so secrets never reach the console, the
  rotating log file, the live log stream, or exception tracebacks.
- A rotating file log is kept under <data dir>/logs.
"""
import logging
import logging.handlers
import sys
import threading
from pathlib import Path

from src.config_loader import DATA_DIR
from src.security import SecurityManager

LOG_DIR: Path = DATA_DIR / "logs"
LOG_FILE: Path = LOG_DIR / "studio.log"
_FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_configured = False


class RedactingFormatter(logging.Formatter):
    """Formats a record, then scrubs secrets from the final text (message and traceback)."""

    def format(self, record: logging.LogRecord) -> str:
        return SecurityManager.redact_secrets(super().format(record))


def setup_logging(level: int = logging.INFO) -> None:
    """Idempotent. Safe to call from the CLI, the web server and tests."""
    global _configured
    root = logging.getLogger()
    root.setLevel(level)
    if _configured:
        return
    _configured = True

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8", delay=True
        )
        file_handler.setFormatter(RedactingFormatter(_FILE_FORMAT))
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except OSError:
        logging.getLogger("logging_setup").warning("File logging unavailable; continuing without it")

    _install_crash_hooks()


def _install_crash_hooks() -> None:
    """Send uncaught exceptions (main thread and worker threads) to the log instead of losing them."""
    crash_log = logging.getLogger("Crash")
    previous_hook = sys.excepthook

    def _main_hook(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            crash_log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))
        previous_hook(exc_type, exc, tb)

    def _thread_hook(args):
        if args.exc_type is not SystemExit:
            crash_log.critical(
                "Uncaught exception in thread %s", getattr(args.thread, "name", "?"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

    sys.excepthook = _main_hook
    threading.excepthook = _thread_hook


def shutdown_logging() -> None:
    """Flush and close file handlers (needed before deleting a data dir on Windows)."""
    global _configured
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            handler.close()
            root.removeHandler(handler)
    _configured = False
