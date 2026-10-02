"""The id rule every interface shares (PoC-3 open note, section 6), and the re-mint on a trace id
in use.

- `trace_id`: the native body field when set; else the trace id of a valid inbound `traceparent`;
  else `uuid4().hex`.
- `request_id`: the native body field when set; else `uuid4().hex`. Never taken from a header.
- `idempotency_key`: the `Idempotency-Key` header (suggested); else the native body field; else
  minted. `key_from` says which (`header`, `body`, `minted`); only a key the client sent
  (`header` or `body`) is checked against the idempotency store (PoC-4, `server.idempotency`).

An empty string counts as unset. On `TraceIdInUse`, native `/v1/run` with a body `trace_id` keeps
its 409 (contract v1). Every other source re-mints once: a fresh `uuid4().hex` replaces the trace
id, `chassis.trace_id_reminted{interface}` (suggested) is counted, and the run opens. So an SDK
client never sees a 409, which it would retry. The client reads the id it got from its format's
reply (`x-trace-id`, or the native envelope).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from chassis.core.envelope import Request
from chassis.core.inbound import Ids, Interface
from chassis.core.trace import parse_traceparent, trace_id_hex
from chassis.server.pipeline import Run, RunPipeline

__all__ = [
    "IDEMPOTENCY_KEY_HEADER",
    "REMINTED",
    "TRACEPARENT_HEADER",
    "KeyFrom",
    "ResolvedIds",
    "TraceFrom",
    "may_remint",
    "open_run",
    "resolve_ids",
]

TRACEPARENT_HEADER = "traceparent"
IDEMPOTENCY_KEY_HEADER = "idempotency-key"
REMINTED = "chassis.trace_id_reminted"

TraceFrom = Literal["body", "traceparent", "minted"]
"""Where the trace id came from; only a native body's keeps its 409."""

KeyFrom = Literal["header", "body", "minted"]
"""Where the idempotency key came from; a minted key never repeats, so it is never checked."""


@dataclass(frozen=True, slots=True)
class ResolvedIds:
    """The run's ids, and where its trace id and its idempotency key came from."""

    ids: Ids
    trace_from: TraceFrom
    key_from: KeyFrom = "minted"


def _mint() -> str:
    return uuid.uuid4().hex


def resolve_ids(
    headers: Mapping[str, str],
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    idempotency_key: str | None = None,
) -> ResolvedIds:
    """The ids for one inbound call. The keywords are the native body's fields; the other formats
    have no id fields and pass none. `headers` are read with lower-case names.
    """
    trace_from: TraceFrom = "body"
    if not trace_id:
        trace_id = parse_traceparent(headers.get(TRACEPARENT_HEADER))
        trace_from = "traceparent"
    if not trace_id:
        trace_id, trace_from = _mint(), "minted"
    key_from: KeyFrom = "header"
    key = headers.get(IDEMPOTENCY_KEY_HEADER)
    if not key:
        key, key_from = idempotency_key, "body"
    if not key:
        key, key_from = _mint(), "minted"
    return ResolvedIds(Ids(request_id or _mint(), trace_id, key), trace_from, key_from)


def may_remint(ids: ResolvedIds, interface: Interface) -> bool:
    """False only for a native body `trace_id`, which keeps its 409. MCP calls the native route
    but re-mints even a body `trace_id`, so a model-chosen id never causes a 409.
    """
    return not (interface == "native" and ids.trace_from == "body")


def open_run(pipeline: RunPipeline, request: Request, *, interface: Interface, remint: bool) -> Run:
    """`pipeline.open(request, interface=interface)`, re-minting the trace id once first when
    `remint` is set and an in-flight run holds it. The check and the open run with no await
    between, so they are one step on the event loop. Raises `TraceIdInUse` when `remint` is not
    set (counted as a refusal by `open`), and only then in practice.
    """
    if remint and pipeline.runs.lookup(trace_id_hex(request.trace_id)) is not None:
        fresh = _mint()
        telemetry = pipeline.ports.telemetry
        telemetry.counter(REMINTED, interface=interface)
        telemetry.log(
            "info",
            "trace id in use by an in-flight run; re-minted",
            request_id=request.request_id,
            trace_id=fresh,
            was=request.trace_id,
            interface=interface,
        )
        request = request.model_copy(update={"trace_id": fresh})
    return pipeline.open(request, interface=interface)
