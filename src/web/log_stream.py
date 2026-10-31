import collections
import datetime
import json
import logging
import queue
import threading
from typing import Deque, List

from src.logging_setup import RedactingFormatter, setup_logging


class WebLogHandler(logging.Handler):
    """
    Thread-safe logging handler that collects log records for SSE broadcast
    and keeps a ring buffer for new client connections.
    """
    def __init__(self, capacity: int = 500):
        super().__init__()
        self.capacity = capacity
        self.history: Deque[dict] = collections.deque(maxlen=capacity)
        self.listeners: List[queue.Queue] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            log_entry = {
                "timestamp": datetime.datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "message": msg
            }

            with self._lock:
                self.history.append(log_entry)
                # Broadcast to active SSE listeners
                dead_listeners = []
                for q in self.listeners:
                    try:
                        q.put_nowait(log_entry)
                    except queue.Full:
                        pass
                    except Exception:
                        dead_listeners.append(q)
                for dl in dead_listeners:
                    self.listeners.remove(dl)
        except Exception:
            self.handleError(record)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self.listeners.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def get_history(self) -> list:
        with self._lock:
            return list(self.history)


# Singleton log handler
log_handler = WebLogHandler()
log_handler.setFormatter(RedactingFormatter("%(message)s"))
log_handler.setLevel(logging.INFO)

# Attach to the root logger (plus the rotating, redacted file log)
setup_logging()
logging.getLogger().addHandler(log_handler)


def format_sse(data: dict, event: str = "log") -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
