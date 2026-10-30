"""The lane contract's case handles, vendored from `chassis_contracts.lane` and
`chassis_contracts.engine` (PoC-2, PoC-3), so they run in a workload's environment.

`chassis_contracts` depends on `chassis`, which the workload's environment never holds, so the
kit cannot import the suite. These are its wire-form handles, copied: plain dicts, standard
library only. `tests/test_agent_testkit_vendored.py` checks in the monorepo that each one yields
exactly what the original yields, and that `ENVELOPES` is what the chassis's `collect` builds.

Vendored: `deltas`, `tool_call`, `error`, `end_output`, `bad_event`, `no_schema_version`,
`json_values` (with the traceparent check), and `stalled` (the run deadline). Not vendored: `echo`
(`chassis.core.handle:echo_wire` is chassis code) and the pairwise lane comparison (the kit has one
lane, the agent's own `sidecar` over A2A; it compares that lane with the handle called directly).

`lane_handle` serves them all from one workload: `input.data.case` picks the case.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

Handle = Callable[[dict[str, Any], dict[str, Any]], AsyncIterator[dict[str, Any]]]
LANE_HANDLE = "agent_testkit.lane_cases:lane_handle"
CASE_KEY = "case"
"""The `input.data` key `lane_handle` reads."""


def _event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": "0", "type": kind, **fields}


DELTAS = ("one ", "two ", "three ", "four ", "five")


async def deltas_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """Five deltas, then `metrics` with every field set, then `end`."""
    yield _event("start", request_id=ctx["request_id"])
    for text in DELTAS:
        yield _event("delta", text=text)
    yield _event(
        "metrics",
        input_tokens=7,
        output_tokens=5,
        cost_usd=0.25,
        model_route=ctx.get("model_route"),
        latency_ms=12,
        attempt=2,
    )
    yield _event("end", status="ok")


TOOL_ARGUMENTS: dict[str, Any] = {"term": "SLM", "limit": 2}
TOOL_RESULT: dict[str, Any] = {"definition": "small language model", "hits": 1, "score": 0.5}


async def tool_call_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """A `tool_call` with its `result`, then the answer, `metrics`, and `end`."""
    yield _event("start", request_id=ctx["request_id"])
    yield _event(
        "tool_call",
        call_id="call_1",
        name="glossary_lookup",
        arguments=TOOL_ARGUMENTS,
        result=TOOL_RESULT,
    )
    yield _event("delta", text="SLM means small language model.")
    yield _event("metrics", input_tokens=10, output_tokens=5, attempt=1)
    yield _event("end", status="ok")


async def error_handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """One delta, then a retryable `error`."""
    yield _event("start", request_id=ctx["request_id"])
    yield _event("delta", text="partial")
    yield _event("error", code="model.overloaded", message="try again later", retryable=True)


END_OUTPUT: dict[str, Any] = {"text": "set by handle", "score": 2, "tags": ["a", "b"], "p": 0.5}


async def end_output_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """A delta, then `end` with `output` set and status `fallback`: `collect` keeps the output."""
    yield _event("start", request_id=ctx["request_id"])
    yield _event("delta", text="not the output")
    yield _event("end", status="fallback", output=END_OUTPUT)


async def bad_event_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """A `delta` with no `text`: every server must stop the run with `workload.bad_event`."""
    yield _event("start", request_id=ctx["request_id"])
    yield _event("delta")
    yield _event("delta", text="never sent")
    yield _event("end", status="ok")


async def no_schema_version_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """Events with no `schema_version`: contract v1 fills it with `"0"` in every server."""
    yield {"type": "start", "request_id": ctx["request_id"]}
    yield {"type": "delta", "text": "no version"}
    yield {"type": "end", "status": "ok"}


JSON_VALUE: dict[str, Any] = {"n": 3, "big": 9007199254740991, "f": 1.5, "nested": [1, {"k": 2}]}
"""The value the "JSON values survive the lane" case sends both ways: `big` is 2^53 - 1."""


async def json_values_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """A `tool_call` whose `arguments` are `JSON_VALUE`, and an `end` whose `output` echoes
    `input.data`, `ctx.budget`, and `ctx.traceparent` next to `JSON_VALUE`."""
    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
    yield {
        "schema_version": "0",
        "type": "tool_call",
        "call_id": "c1",
        "name": "json_values",
        "arguments": JSON_VALUE,
    }
    yield {
        "schema_version": "0",
        "type": "end",
        "status": "ok",
        "output": {
            "value": JSON_VALUE,
            "data": input.get("data"),
            "budget": ctx.get("budget"),
            "traceparent": ctx.get("traceparent"),
        },
    }


STALL_S = 2.0
"""How long `stalled_handle` sleeps after `start`: well past `STALL_BUDGET_MS`."""
STALL_BUDGET_MS = 200
"""The run budget of the deadline case (suggested)."""
STALL_BOUND_S = 1.5
"""The run of the deadline case must end within this, well before the handle wakes up."""


async def stalled_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """`start`, then a sleep past the run budget: only the connector's run deadline ends it."""
    yield _event("start", request_id=ctx["request_id"])
    await asyncio.sleep(STALL_S)
    yield _event("end", status="ok")


HANDLES: dict[str, Handle] = {
    "deltas": deltas_handle,
    "tool_call": tool_call_handle,
    "error": error_handle,
    "end_output": end_output_handle,
    "bad_event": bad_event_handle,
    "no_schema_version": no_schema_version_handle,
    "json_values": json_values_handle,
    "stalled": stalled_handle,
}

TYPES: dict[str, tuple[str, ...]] = {
    "deltas": ("start", *["delta"] * len(DELTAS), "metrics", "end"),
    "tool_call": ("start", "tool_call", "delta", "metrics", "end"),
    "error": ("start", "delta", "error"),
    "end_output": ("start", "delta", "end"),
    "bad_event": ("start", "error"),
    "no_schema_version": ("start", "delta", "end"),
    "json_values": ("start", "tool_call", "end"),
}
"""The event types the workload must stream over A2A, per case, in order (`chassis_contracts.lane
.CASES`). `stalled` has its own test."""

VERBATIM: frozenset[str] = frozenset(
    {"deltas", "tool_call", "error", "end_output", "no_schema_version", "json_values"}
)
"""Cases whose A2A stream must equal what the handle yields when called directly, once the
schema's defaults are filled in. Not `bad_event`: the server stops it on purpose."""

ENVELOPES: dict[str, tuple[str, dict[str, Any] | None]] = {
    "deltas": ("ok", {"text": "".join(DELTAS)}),
    "tool_call": (
        "ok",
        {
            "text": "SLM means small language model.",
            "tool_calls": [
                {
                    "call_id": "call_1",
                    "name": "glossary_lookup",
                    "arguments": TOOL_ARGUMENTS,
                    "result": TOOL_RESULT,
                }
            ],
        },
    ),
    "error": (
        "error",
        {"text": "partial", "error": {"code": "model.overloaded", "message": "try again later"}},
    ),
    "end_output": ("fallback", END_OUTPUT),
    "no_schema_version": ("ok", {"text": "no version"}),
    "bad_event": ("error", None),
}
"""`(status, output)` of the envelope the chassis returns for each case; `None` skips the output
(the `workload.bad_event` message is worded by the validator). `json_values` and `stalled` have
their own tests."""


async def lane_handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """The case named by `input.data.case`, as is. An unknown case is one `error` event."""
    data = input.get("data") or {}
    name = data.get(CASE_KEY) if isinstance(data, dict) else None
    handle = HANDLES.get(name) if isinstance(name, str) else None
    if handle is None:
        yield _event("start", request_id=ctx["request_id"])
        yield _event("error", code="testkit.unknown_case", message=f"no lane case {name!r}")
        return
    async for event in handle(input, ctx):
        yield event
