"""
Shared outbound HTTP client.

One pooled session with a stable User-Agent, and one place that implements the platform rules
from AGENTS.md: honour HTTP 429 / Retry-After, otherwise back off exponentially with jitter.
"""
import logging
import random
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import requests

logger = logging.getLogger("HttpClient")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_WAIT_SEC = 60.0

_session: Optional[requests.Session] = None
_session_lock = threading.Lock()


def get_session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            _session = requests.Session()
            _session.headers.update({"User-Agent": USER_AGENT})
        return _session


def retry_after_seconds(value: Optional[str]) -> Optional[float]:
    """Parse a Retry-After header (delta-seconds or HTTP-date). None if absent or unparseable."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        delta = parsedate_to_datetime(value).timestamp() - time.time()
        return max(0.0, delta)
    except (TypeError, ValueError):
        return None


def backoff_seconds(attempt: int, retry_after: Optional[float] = None) -> float:
    """Wait before retry number `attempt` (0-based). Retry-After wins; capped at MAX_WAIT_SEC."""
    if retry_after is not None:
        return min(retry_after, MAX_WAIT_SEC)
    return min((2 ** attempt) + random.uniform(0, 0.5), MAX_WAIT_SEC)


def request(method: str, url: str, *, retries: int = 3, timeout: float = 10.0, **kwargs: Any) -> requests.Response:
    """
    Perform an HTTP request, retrying on 429/5xx and transient network errors.
    Returns the final response (which may still be an error status); raises only on network failure.
    """
    session = get_session()
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt == retries:
                raise
            wait = backoff_seconds(attempt)
            logger.warning("%s %s failed (%s); retry %d/%d in %.1fs", method, url, type(exc).__name__, attempt + 1, retries, wait)
            time.sleep(wait)
            continue

        if resp.status_code in RETRY_STATUSES and attempt < retries:
            wait = backoff_seconds(attempt, retry_after_seconds(resp.headers.get("Retry-After")))
            logger.warning("%s %s -> HTTP %d; retry %d/%d in %.1fs", method, url, resp.status_code, attempt + 1, retries, wait)
            resp.close()
            time.sleep(wait)
            continue
        return resp

    raise last_exc or RuntimeError("request failed")  # pragma: no cover


def get(url: str, **kwargs: Any) -> requests.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> requests.Response:
    return request("POST", url, **kwargs)


def head(url: str, **kwargs: Any) -> requests.Response:
    return request("HEAD", url, **kwargs)


def get_text(url: str, **kwargs: Any) -> str:
    """GET and return the body as text; raises on a final non-2xx status."""
    resp = get(url, **kwargs)
    resp.raise_for_status()
    return resp.text
