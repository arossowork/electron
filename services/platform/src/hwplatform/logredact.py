"""Never log API keys: replace anything shaped like an Anthropic key in every log record."""

from __future__ import annotations

import logging
import re

_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{4,}")
REDACTED = "sk-ant-***"


def redact(text: str) -> str:
    return _KEY_RE.sub(REDACTED, text)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # noqa: S110 - a broken log call must not turn into a crash here
            pass
        if record.exc_info and not record.exc_text:  # Formatter reuses exc_text when it is set
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


_installed = False


def install_log_redaction() -> None:
    """Wrap the log record factory, so the redaction covers every logger and handler, uvicorn's too."""
    global _installed
    if _installed:
        return
    _installed = True
    old = logging.getLogRecordFactory()
    flt = RedactFilter()

    def factory(*a, **kw) -> logging.LogRecord:
        rec = old(*a, **kw)
        flt.filter(rec)
        return rec

    logging.setLogRecordFactory(factory)
