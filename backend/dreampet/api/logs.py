"""Keep secrets out of server logs.

The web UI has to put the admin token in the query string (`?token=`) for <video>, <img> and
EventSource, which can't send headers, and uvicorn's access log prints every request's full URL.
Error logs can carry exception text from provider SDKs."""

from __future__ import annotations

import logging
import sys
import threading
import traceback

from dreampet.redact import redact


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        if record.exc_info and record.exc_info[1] is not None:
            # tracebacks quote the exception message; format it now, redacted, and drop the original
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)).rstrip())
            record.exc_info = None
        return True


def install_redaction(names: tuple[str, ...] = ("uvicorn.access", "uvicorn.error", "uvicorn")) -> None:
    """Idempotent. Logger filters survive uvicorn's own dictConfig, which only replaces handlers."""
    for name in names:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactSecrets) for f in logger.filters):
            logger.addFilter(RedactSecrets())
    install_crash_redaction()


def _print_redacted(exc_type, exc, tb, header: str = "") -> None:
    sys.stderr.write(header + redact("".join(traceback.format_exception(exc_type, exc, tb))))


def _thread_hook(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread else "?"
    _print_redacted(args.exc_type, args.exc_value, args.exc_traceback, f"Exception in thread {name}:\n")


def install_crash_redaction() -> None:
    """Uncaught exceptions (scheduler and worker threads included) print redacted tracebacks."""
    threading.excepthook = _thread_hook
    sys.excepthook = _print_redacted
