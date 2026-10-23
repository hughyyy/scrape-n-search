"""Log redaction.

Provider keys ride in query strings (Scrappey, ZenRows) or headers, and
``httpx`` exception strings embed the full request URL. Logging those at DEBUG
or WARNING would leak the key into harness logs, so every message that may
carry a URL or an exception is redacted — both at the call site (router) and
centrally for handlers installed by ``configure_logging``.
"""

from __future__ import annotations

import logging
import re

_PATTERNS = (
    # key=…, apikey=…, api_key=…, access_token=… in URLs / messages.
    re.compile(r"(?i)\b((?:api[_-]?key|apikey|key|token|access_token)=)([^&\s'\"]+)"),
    # Authorization: Bearer …
    re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/=-]{8,})"),
)


def redact(text: str) -> str:
    """Replace secret-looking query parameters and bearer tokens with ``***``."""
    for pattern in _PATTERNS:
        text = pattern.sub(lambda match: match.group(1) + "***", text)
    return text


class RedactFilter(logging.Filter):
    """Redact a record's message before any handler formats it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True
