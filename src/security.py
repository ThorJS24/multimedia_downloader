import base64
import ctypes
from ctypes import wintypes
import os
import re
import secrets
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# Generated per-run secret session token to protect local REST endpoints
SESSION_AUTH_TOKEN = secrets.token_urlsafe(32)
SESSION_COOKIE_NAME = "studio_session"
ALLOWED_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})


def is_allowed_host(host_header: Optional[str]) -> bool:
    """Accepts only loopback Host headers (blocks DNS-rebinding attacks)."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        name = host.split("]")[0] + "]"
    else:
        name = host.rsplit(":", 1)[0]
    return name in ALLOWED_HOSTNAMES


def is_valid_session_token(candidate: Optional[str]) -> bool:
    """Constant-time comparison against the per-run session token."""
    if not candidate:
        return False
    return secrets.compare_digest(candidate.encode("utf-8"), SESSION_AUTH_TOKEN.encode("utf-8"))


def is_same_origin(origin: Optional[str], host_header: Optional[str]) -> bool:
    """True when a browser Origin header is absent or matches the Host the request was sent to."""
    if not origin:
        return True
    return urlparse(origin).netloc.lower() == (host_header or "").strip().lower()

_SECRET_KEYS = r"(?:password|passwd|client_secret|secret|access_token|refresh_token|token|api[_-]?key|authorization)"
_REDACTION_RULES = (
    # JSON / dict style:  "password": "value"   or   'token': 'value'
    (re.compile(r"""(["']""" + _SECRET_KEYS + r"""["']\s*:\s*["'])([^"']*)(["'])""", re.IGNORECASE), r"\1[REDACTED]\3"),
    # key=value or key: value (query strings, kwargs, headers)
    (re.compile(r"(\b" + _SECRET_KEYS + r"\b\s*[=:]\s*)(?!\[REDACTED\]|Bearer\s)([^\s&,;\"')]+)", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.=]{8,}", re.IGNORECASE), r"\1[REDACTED]"),
    # DPAPI blobs stored in config
    (re.compile(r"dpapi:[A-Za-z0-9+/=]{16,}"), "dpapi:[REDACTED]"),
)


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char))
    ]


class SecurityManager:
    """
    Windows security utilities:
    - Windows DPAPI credential encryption (CryptProtectData)
    - Secret redaction for logs and diagnostic exports
    - Path traversal prevention
    - Per-session API authentication
    """

    @staticmethod
    def encrypt_secret(plaintext: str) -> str:
        """Encrypts a string using Windows Data Protection API (DPAPI)."""
        if not plaintext:
            return ""
        if os.name != "nt":
            # Fallback for non-Windows (base64 obfuscation)
            return base64.b64encode(plaintext.encode("utf-8")).decode("ascii")

        data_bytes = plaintext.encode("utf-8")
        in_buffer = ctypes.create_string_buffer(data_bytes, len(data_bytes))
        blob_in = DATA_BLOB(len(data_bytes), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()

        success = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            "SpotifyStudioSecret",
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out)
        )

        if not success:
            raise RuntimeError("Windows DPAPI encryption failed")

        encrypted_bytes = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return base64.b64encode(encrypted_bytes).decode("ascii")

    @staticmethod
    def decrypt_secret(ciphertext: str) -> str:
        """Decrypts a DPAPI-encrypted string."""
        if not ciphertext:
            return ""
        if os.name != "nt":
            try:
                return base64.b64decode(ciphertext.encode("ascii")).decode("utf-8")
            except Exception:
                return ciphertext

        try:
            encrypted_bytes = base64.b64decode(ciphertext.encode("ascii"))
        except Exception:
            return ciphertext

        in_buffer = ctypes.create_string_buffer(encrypted_bytes, len(encrypted_bytes))
        blob_in = DATA_BLOB(len(encrypted_bytes), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()

        success = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out)
        )

        if not success:
            return ciphertext  # Return raw if not encrypted with current DPAPI

        decrypted_bytes = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return decrypted_bytes.decode("utf-8", errors="replace")

    @staticmethod
    def redact_secrets(text: str) -> str:
        """Sanitizes text by redacting passwords, client secrets, tokens and authorization headers."""
        if not text:
            return ""
        s = text
        for pattern, replacement in _REDACTION_RULES:
            s = pattern.sub(replacement, s)
        return s

    @staticmethod
    def validate_safe_path(target_path: Path, allowed_root: Path) -> bool:
        """Ensures target path does not escape allowed root directory (anti-path traversal)."""
        try:
            resolved_target = target_path.resolve()
            resolved_root = allowed_root.resolve()
            return resolved_target.is_relative_to(resolved_root)
        except (ValueError, TypeError, Exception):
            return False
