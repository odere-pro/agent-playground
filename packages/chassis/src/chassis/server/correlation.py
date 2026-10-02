"""Request correlation: which in-flight `/v1/run` a model or tool call belongs to.

A workload's HTTP client propagates the W3C `traceparent` header on every call it makes to the
chassis proxies. `parse_traceparent` (in `chassis.core.trace`, re-exported here) takes the 32-hex
trace id out of it by hand (no OpenTelemetry in the chassis; `TelemetryPort` is the abstraction).
`RunRegistry` holds one `RunRecord` per in-flight run, keyed by that trace id, for the life of the
run: `/v1/run` opens it with `open` (or `register`), the model proxy finds it with `lookup` and
charges each call's usage to it. Two concurrent runs never share a record, so each keeps its own
`budget.max_tokens` (PoC-2 exit criterion: "two concurrent requests in one replica each get their
own budget").

One trace id, one in-flight run. The caller may set `trace_id`, so two concurrent `/v1/run`s can
name the same one; the proxy could then not tell their calls apart, and an unpicked call would run
with no budget. So a second registration of an in-flight trace id is refused (`TraceIdInUse`, a
409 `trace_id_in_use` on `/v1/run`), before the engine runs. The trace id is free again when its
run ends. This keying is a PoC-2 stopgap: the trace id is not a credential, and PoC-5 replaces it
with a chassis-minted per-run credential that the workload presents on every proxy call.

Budget reservations. A model call reserves the tokens it may spend (`reserve`, the `max_tokens`
the proxy forwards) at its start and gives them back when its usage is known (`settle`), so two
concurrent calls of one run cannot both be sent the whole remainder.

The key is `chassis.core.trace.trace_id_hex(ctx.trace_id)`: `ctx.trace_id` when it is 32
lowercase hex, else the first 32 hex of its sha256. It is the same id the A2A connectors put in
the `traceparent` they send to the workload, so the workload hands it back unchanged.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from chassis.core.envelope import Budget, Context, Request
from chassis.core.trace import parse_traceparent, trace_id_hex
from chassis.ports.model import Usage

__all__ = ["RunRecord", "RunRegistry", "TraceIdInUse", "parse_traceparent", "trace_id_hex"]


class TraceIdInUse(Exception):
    """A run with this trace id is in flight; a second one may not register it."""

    def __init__(self, trace_id: str, request_id: str) -> None:
        super().__init__(f"trace id {trace_id} is in use by an in-flight run")
        self.trace_id = trace_id
        self.request_id = request_id


@dataclass
class RunRecord:
    """What the proxies know about one in-flight run, and what it has spent so far."""

    request_id: str
    agent: str
    trace_id: str
    budget: Budget
    spent_input_tokens: int = 0
    spent_output_tokens: int = 0
    model_calls: int = 0
    reserved_tokens: int = 0
    keyed_run_key: str | None = None
    """PoC-5: the run key the request's idempotency key gives, set by `RunRegistry.open`:
    `keyed_run_key(request)`. The tool endpoint derives write keys from it while idempotency is
    on, so a replay under a new `request_id` dedups its writes. Neither the raw key nor its bare
    hash is held here."""

    @property
    def spent_tokens(self) -> int:
        return self.spent_input_tokens + self.spent_output_tokens

    @property
    def exhausted(self) -> bool:
        """No tokens left: `spent_tokens >= budget.max_tokens`. The next call is refused."""
        return self.spent_tokens >= self.budget.max_tokens

    @property
    def remaining_tokens(self) -> int:
        """What a new call may still be given: the budget less what is spent and reserved."""
        return max(0, self.budget.max_tokens - self.spent_tokens - self.reserved_tokens)

    def reserve(self, wanted: int | None) -> int:
        """Hold up to `wanted` tokens (all that remain when None) for one call; the amount held.

        The model proxy forwards the amount as the call's `max_tokens`. Zero holds nothing.
        """
        held = (
            self.remaining_tokens if wanted is None else min(max(wanted, 0), self.remaining_tokens)
        )
        self.reserved_tokens += held
        return held

    def release(self, held: int) -> None:
        """Give back a reservation without charging: the call never reached the model."""
        self.reserved_tokens = max(0, self.reserved_tokens - held)

    def settle(self, held: int, usage: Usage | None) -> bool:
        """Give back a reservation and charge the call's usage; True when now over budget."""
        self.release(held)
        return self.charge(usage)

    def charge(self, usage: Usage | None) -> bool:
        """Record one model call and its usage; True when the run is now over its budget."""
        self.model_calls += 1
        if usage is not None:
            self.spent_input_tokens += usage.input_tokens
            self.spent_output_tokens += usage.output_tokens
        return self.exhausted


RUN_KEY_PREFIX = "run1|"
"""The version of the keyed run-key derivation. A change to it bumps the prefix."""


def keyed_run_key(request: Request) -> str:
    """The run key of a request's idempotency key (review M1): the sha256 hex of
    `run1|agent|key_hash(idempotency_key)|fingerprint(request)`.

    - The agent scopes it: two agents behind one tool server never share a key for one client key.
    - The PoC-4 fingerprint (`agent`, `input`, `context_ref`) ties it to the request: a reused key
      with other input (the idempotency entry expired) is another run. A replay or a takeover
      carries the same fingerprint, or PoC-4 refuses it, so it gets the same run key.
    - The pipeline always fills `idempotency_key`; a key it minted is a fresh uuid4, so its run key
      is as unique as the `request_id`.
    """
    # Imported here: `results` and `idempotency` import the pipeline, which imports this module.
    from chassis.server.idempotency import fingerprint
    from chassis.server.results import key_hash

    payload = "|".join((request.agent, key_hash(request.idempotency_key), fingerprint(request)))
    return hashlib.sha256((RUN_KEY_PREFIX + payload).encode("utf-8")).hexdigest()


@dataclass
class RunRegistry:
    """In-flight runs by trace id, at most one per trace id. One object per public app, shared
    with the proxy app.
    """

    _runs: dict[str, RunRecord] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self._runs)

    def open(self, request: Request, ctx: Context) -> RunRecord:
        """A fresh record for the run, held until `close`. Raises `TraceIdInUse` when another
        in-flight run holds the trace id. No await, so check and insert are one step.
        """
        trace_id = trace_id_hex(ctx.trace_id)
        holder = self._runs.get(trace_id)
        if holder is not None:
            raise TraceIdInUse(trace_id, holder.request_id)
        record = RunRecord(
            request_id=request.request_id,
            agent=request.agent,
            trace_id=trace_id,
            budget=request.budget.model_copy(),
            keyed_run_key=keyed_run_key(request),
        )
        self._runs[trace_id] = record
        return record

    def close(self, record: RunRecord) -> None:
        """Free the record's trace id. Idempotent; never frees another run's record."""
        if self._runs.get(record.trace_id) is record:
            del self._runs[record.trace_id]

    @asynccontextmanager
    async def register(self, request: Request, ctx: Context) -> AsyncIterator[RunRecord]:
        """`open` for the life of the block, then `close`, also when the run fails."""
        record = self.open(request, ctx)
        try:
            yield record
        finally:
            self.close(record)

    def lookup(self, trace_id_hex: str) -> RunRecord | None:
        """The in-flight run with this trace id, or None."""
        return self._runs.get(trace_id_hex)
