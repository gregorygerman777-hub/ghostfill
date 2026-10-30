"""Logging setup with secret redaction. The RPC URL is never logged in full."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit


def redact_url(url: str) -> str:
    """Keep scheme and host only. Paths and query strings often carry API keys."""
    if not url:
        return ""
    parts = urlsplit(url)
    host = parts.hostname or "?"
    if parts.port:
        host = f"{host}:{parts.port}"
    hidden = bool(parts.path.strip("/") or parts.query or parts.username or parts.password)
    return f"{parts.scheme}://{host}" + ("/<redacted>" if hidden else "")


class RedactingFormatter(logging.Formatter):
    """Redacts secrets from the whole formatted record, tracebacks included."""

    def __init__(self, secrets: list[str], fmt: str):
        super().__init__(fmt)
        self._pairs = [(s, redact_url(s)) for s in secrets if s]

    def format(self, record: logging.LogRecord) -> str:
        out = super().format(record)
        for secret, safe in self._pairs:
            out = out.replace(secret, safe)
        return out


def setup_logging(secrets: list[str], level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(secrets, "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
    # httpx logs every request URL at INFO, which would include an RPC key.
    for noisy in ("httpx", "httpcore", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
