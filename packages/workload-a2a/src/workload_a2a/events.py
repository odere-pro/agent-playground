"""Event helpers the Python workloads share: one event dict, one `error`, the exception chain, and
the per-call timeout from `ctx.budget`.

Plain dicts in the wire form (`schemas/events.v0.json`), `schema_version: "0"` on each. The
version is a literal, not `server.SCHEMA_VERSION`: a workload keeps emitting `"0"` until it is
changed on purpose. No framework, no `chassis` import (ADR-002).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "MAX_MESSAGE",
    "SCHEMA_VERSION",
    "chain",
    "error",
    "event",
    "timeout_s",
]

SCHEMA_VERSION = "0"
# The longest `error.message` a workload sends.
MAX_MESSAGE = 500
# suggested: 30 s per call when `ctx.budget.timeout_ms` is not set; the epic gives none.
DEFAULT_TIMEOUT_S = 30.0


def event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "type": kind, **fields}


def error(code: str, message: str, retryable: bool) -> dict[str, Any]:
    """One `error` event, the message cut to `MAX_MESSAGE` characters."""
    return event("error", code=code, message=message[:MAX_MESSAGE], retryable=retryable)


def chain(exc: BaseException) -> Iterator[BaseException]:
    """The exception, its causes and contexts, and the members of any exception group."""
    seen: set[int] = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
        stack.extend(e for e in (current.__cause__, current.__context__) if e is not None)


def timeout_s(ctx: dict[str, Any]) -> float:
    """`ctx.budget.timeout_ms` in seconds, else `DEFAULT_TIMEOUT_S` (also for 0 or no budget)."""
    budget = ctx.get("budget")
    if isinstance(budget, dict) and budget.get("timeout_ms"):
        return float(budget["timeout_ms"]) / 1000
    return DEFAULT_TIMEOUT_S
