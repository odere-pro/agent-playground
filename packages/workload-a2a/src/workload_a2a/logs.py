"""The workload's log format: one JSON object per line (PoC-8, 014 H-7). Standard library only.

The same field names as the chassis's `chassis.logs`, so one query reads both sides: `time` (UTC,
ISO 8601, milliseconds, `Z`), `level`, `logger`, `message`, `agent`, then the record's extra
fields, then `exception` when there is a traceback. Inside an A2A request, `request_id` and
`trace_id` (32 lowercase hex) too. Never imports the chassis (ADR-002).

**Setup.** `configure_logging(agent=..., level=...)` puts one handler with `JsonFormatter` on the
root logger, so the workload's own loggers and uvicorn's write the same format. `workload-a2a
serve` and `serve` call it at start; `build_server` gives uvicorn no log config of its own.

**The request's ids.** `HandleExecutor.execute` wraps each `handle` call in `request_context`:
`request_id` is `ctx["request_id"]` (the chassis puts it in `chassis.ctx`), and `trace_id` comes
from `trace_id_of`: the A2A request's `traceparent` header, else `ctx["traceparent"]`, else
`ctx["trace_id"]`. A line the workload logs anywhere inside `handle` carries both. uvicorn's
access line is written outside `handle` and carries neither.

**No redaction here.** Unlike the chassis's lines, these are not redacted in process: the OTel
Collector's log pipeline redacts workload logs (ADR-007 item 7). Do not copy `chassis.redaction`.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import re
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import IO, Any

__all__ = [
    "REQUEST_FIELDS",
    "JsonFormatter",
    "configure_logging",
    "current_request",
    "request_context",
    "trace_id_of",
]

REQUEST_FIELDS = ("request_id", "trace_id")
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_TRACEPARENT = re.compile(r"[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}")
_NO_TRACE = "0" * 32
_STANDARD = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn's copy of the message with ANSI colors
}
_REQUEST: contextvars.ContextVar[Mapping[str, str] | None] = contextvars.ContextVar(
    "workload_log_request", default=None
)


def current_request() -> Mapping[str, str]:
    """The request's ids for the code running now, or `{}` outside a request."""
    return _REQUEST.get() or {}


def _hex(trace_id: str) -> str:
    """The 32 lowercase hex form of a `ctx.trace_id`: the rule of the chassis's `trace_id_hex`
    (a test compares the two), so a ctx with no `traceparent` still matches the chassis's logs."""
    if _HEX32.fullmatch(trace_id):
        return trace_id
    return hashlib.sha256(trace_id.encode()).hexdigest()[:32]


def _from_traceparent(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _TRACEPARENT.fullmatch(value.strip().lower())
    if match is None or match.group(1) == _NO_TRACE:
        return None
    return match.group(1)


def trace_id_of(headers: Mapping[str, str], ctx: Mapping[str, Any]) -> str | None:
    """The request's 32-hex trace id: the `traceparent` header, else `ctx["traceparent"]`, else
    `ctx["trace_id"]`. None when none of them holds one. Never raises."""
    header = next((v for k, v in headers.items() if k.lower() == "traceparent"), None)
    found = _from_traceparent(header) or _from_traceparent(ctx.get("traceparent"))
    if found:
        return found
    trace_id = ctx.get("trace_id")
    return _hex(trace_id) if isinstance(trace_id, str) and trace_id else None


@contextmanager
def request_context(*, request_id: Any = None, trace_id: Any = None) -> Iterator[None]:
    """Log lines inside carry `request_id` and `trace_id`. A value that is not a non-empty string
    keeps the outer one."""
    ids = dict(current_request())
    if isinstance(request_id, str) and request_id:
        ids["request_id"] = request_id
    if isinstance(trace_id, str) and trace_id:
        ids["trace_id"] = trace_id
    token = _REQUEST.set(ids)
    try:
        yield
    finally:
        # An async generator closed from another context cannot reset: leave it.
        with contextlib.suppress(ValueError):
            _REQUEST.reset(token)


def _utc(created: float) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(created))
    return f"{stamp}.{int(created % 1 * 1000):03d}Z"


class JsonFormatter(logging.Formatter):
    """One JSON object per record. See the module docstring. No redaction (ADR-007 item 7)."""

    def __init__(self, agent: str | None = None) -> None:
        super().__init__()
        self.agent = agent

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "time": _utc(record.created),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if self.agent:
            line["agent"] = self.agent
        line.update(current_request())
        for key, value in record.__dict__.items():
            if key in _STANDARD or key in line:
                continue
            line[key] = value
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING}
_LEVELS |= {"error": logging.ERROR, "critical": logging.CRITICAL}


def configure_logging(*, agent: str, level: str = "info", stream: IO[str] | None = None) -> None:
    """One JSON handler on the root logger, at `level`. Called again, it replaces its own handler
    and leaves any other alone. uvicorn's loggers lose their handlers and write through the root."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter(agent=agent))
    root.addHandler(handler)
    root.setLevel(_LEVELS.get(level.lower(), logging.INFO))
    for name in UVICORN_LOGGERS:
        named = logging.getLogger(name)
        named.handlers.clear()
        named.propagate = True
