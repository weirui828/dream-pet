"""Mask secrets in text that may end up in logs, the events table or the live stream.

Two layers: the exact secret values this process loaded (admin token, role API keys, everything
in .secrets, database passwords), and patterns for secrets we never saw (query-string tokens,
bearer headers, credentials in URLs, well-known key formats in third-party error messages)."""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

MASK = "[REDACTED]"
MIN_SECRET_LEN = 8  # shorter values would mask ordinary words

_PATTERNS = [
    (re.compile(r"(?i)([?&](?:token|access_token|api_key|apikey|key|secret|password|sig|signature)=)[^&#\s\"']*"),
     r"\1" + MASK),
    (re.compile(r"(?i)\b(bearer|key|token)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + MASK),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@\"']+:[^/\s@\"']+@"), r"\1" + MASK + "@"),
    (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{30,}|r8_[A-Za-z0-9]{20,}"
                r"|tvly-[A-Za-z0-9_-]{16,}|fc-[A-Za-z0-9]{16,}|(?:AKIA|ASIA)[A-Z0-9]{16})"), MASK),
]

_lock = threading.Lock()
_secrets: list[str] = []


def register_secrets(values: Iterable[str | None]) -> None:
    """Remember exact values to mask. Longest first, so a secret containing another masks whole."""
    global _secrets
    new = {v for v in values if isinstance(v, str) and len(v) >= MIN_SECRET_LEN}
    if not new:
        return
    with _lock:
        _secrets = sorted(set(_secrets) | new, key=len, reverse=True)


def url_password(url: str | None) -> str | None:
    try:
        return urlsplit(url).password if url else None
    except ValueError:
        return None


def redact(text: str) -> str:
    for s in _secrets:
        if s in text:
            text = text.replace(s, MASK)
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def redact_obj(obj: Any) -> Any:
    """Redact every string inside a JSON-like value (event payloads)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return type(obj)(redact_obj(v) for v in obj)
    return obj
