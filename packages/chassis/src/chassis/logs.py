"""The chassis's one log format: one JSON object per line (PoC-8, 014 H-7). Standard library only.

`configure_logging(agent=..., level=...)` puts one handler with `JsonFormatter` on the root logger,
so every chassis logger, uvicorn's included, writes the same format. `chassis serve` calls it with
`spec.observability.log_level` and gives uvicorn no log config of its own.

**A line.** `time` (UTC, ISO 8601, milliseconds, `Z`), `level`, `logger`, `message`, `agent`, then
the record's extra fields, then `exception` when there is a traceback. Inside a run, `request_id`
and `trace_id` (32 lowercase hex, `trace_id_hex`) too. A record from `OtelTelemetry.log` carries its
own fields in `chassis_record`; they go on the line as fields, never as JSON inside `message`.

**The run's ids.** `run_context(request_id=..., trace_id=...)` sets them for the code inside it.
The telemetry adapters call it for every span that has those attributes (`chassis.metrics.
observe_span`), so a line logged anywhere under the `chassis.run` span or a proxy span carries them.

**Redaction.** The message, every field except the ids in `ID_FIELDS`, and the traceback pass
`chassis.redaction` (regex only, 022 H-6). Workload logs never pass here; the OTel Collector's log
pipeline redacts them (ADR-007 item 7).
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import IO, Any

from chassis.core.trace import trace_id_hex
from chassis.redaction import redact, redact_value

__all__ = ["ID_FIELDS", "JsonFormatter", "configure_logging", "current_run", "run_context"]

ID_FIELDS = frozenset(
    {"time", "level", "logger", "agent", "request_id", "trace_id", "span_id", "route", "interface"}
)
"""Fields that are ids or set by the formatter, never redacted. suggested: the list."""
RUN_FIELDS = ("request_id", "trace_id")
_STANDARD = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
    "chassis_record",
}
_RUN: contextvars.ContextVar[Mapping[str, str] | None] = contextvars.ContextVar(
    "chassis_log_run", default=None
)


def current_run() -> Mapping[str, str]:
    """The run's ids for the code running now, or `{}` outside a run."""
    return _RUN.get() or {}


@contextmanager
def run_context(*, request_id: Any = None, trace_id: Any = None) -> Iterator[None]:
    """Log lines inside carry `request_id` and the 32-hex `trace_id`. A value that is not a
    non-empty string keeps the outer one."""
    ids = dict(current_run())
    if isinstance(request_id, str) and request_id:
        ids["request_id"] = request_id
    if isinstance(trace_id, str) and trace_id:
        ids["trace_id"] = trace_id_hex(trace_id)
    token = _RUN.set(ids)
    try:
        yield
    finally:
        # An async generator closed from another context cannot reset: leave it.
        with contextlib.suppress(ValueError):
            _RUN.reset(token)


def _utc(created: float) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(created))
    return f"{stamp}.{int(created % 1 * 1000):03d}Z"


class JsonFormatter(logging.Formatter):
    """One JSON object per record. See the module docstring."""

    def __init__(self, agent: str | None = None) -> None:
        super().__init__()
        self.agent = agent

    def format(self, record: logging.LogRecord) -> str:
        own = getattr(record, "chassis_record", None)
        message = own.get("message") if isinstance(own, Mapping) else None
        line: dict[str, Any] = {
            "time": _utc(record.created),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact(str(message) if message is not None else record.getMessage()),
        }
        if self.agent:
            line["agent"] = self.agent
        line.update(current_run())
        if isinstance(own, Mapping):
            self._add(line, {k: v for k, v in own.items() if k not in ("time", "message")})
        self._add(line, {k: v for k, v in record.__dict__.items() if k not in _STANDARD})
        if record.exc_info:
            line["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(line, default=str)

    @staticmethod
    def _add(line: dict[str, Any], fields: Mapping[str, Any]) -> None:
        for key, value in fields.items():
            if key in ("level", "logger") or (key in RUN_FIELDS and key in line):
                continue
            line[key] = value if key in ID_FIELDS else redact_value(value)


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
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        named = logging.getLogger(name)
        named.handlers.clear()
        named.propagate = True
