"""Drive a workload over A2A the way the chassis's `sidecar` connector does, with no chassis.

`a2a-sdk`'s client over the workload's Unix socket, and `workload_a2a.mapping` (the byte-for-byte
copy of the chassis's mapping) both ways: `request_to_message` builds the request,
`update_to_event` reads each stream event back. The result is the list of raw event dicts, in
order, up to and including the first `end` or `error`.

Sync wrappers (`events`) run their own event loop, so a test needs no pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any

from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import SendMessageRequest
from workload_a2a.mapping import INPUT_KEY, request_to_message, update_to_event

from agent_testkit.processes import Workload

TERMINAL = ("end", "error")
RUN_TIMEOUT_S = 30.0
"""suggested: one A2A run of one case; a workload that hangs fails instead."""
TESTKIT_VERSION = "0.0.0-testkit"
"""What `ctx.versions.chassis` says when the kit, not the chassis, sends the request."""


def make_ctx(
    agent: str,
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    budget: Mapping[str, int] | None = None,
    traceparent: str | None = None,
) -> dict[str, Any]:
    """A `Context` (`context.v0.json`) as the chassis sends it, with fresh ids by default."""
    request_id = request_id or uuid.uuid4().hex
    trace_id = trace_id or uuid.uuid4().hex
    return {
        "request_id": request_id,
        "trace_id": trace_id,
        "idempotency_key": request_id,
        "agent": agent,
        "agent_version": "0.0.0",
        "budget": dict(budget or {"max_tokens": 2000, "timeout_ms": 30000}),
        "versions": {"chassis": TESTKIT_VERSION, "model_route": "fake-route"},
        "model_route": "fake-route",
        "traceparent": traceparent,
    }


def message_for(
    input: Mapping[str, Any],
    ctx: Mapping[str, Any],
    *,
    schema_version: str = "0",
    raw_input: str | None = None,
) -> SendMessageRequest:
    """The A2A request for one run. `raw_input` replaces the `chassis.input` string as is."""
    message = request_to_message(dict(input), dict(ctx), schema_version=schema_version)
    if raw_input is not None:
        message.metadata[INPUT_KEY] = raw_input
    return message


async def stream(
    workload: Workload, message: SendMessageRequest, *, timeout_s: float = RUN_TIMEOUT_S
) -> list[dict[str, Any]]:
    """Send `message` to `workload` over A2A; the chassis events it streams back."""
    out: list[dict[str, Any]] = []
    async with asyncio.timeout(timeout_s), workload.async_client(timeout=timeout_s) as http:
        card = await A2ACardResolver(http, workload.url).get_agent_card()
        client = ClientFactory(ClientConfig(httpx_client=http, streaming=True)).create(card)
        async for response in client.send_message(message):
            event = update_to_event(response)
            if event is None:
                continue
            out.append(event)
            if event.get("type") in TERMINAL:
                break
    return out


def events(
    workload: Workload,
    input: Mapping[str, Any],
    ctx: Mapping[str, Any],
    *,
    schema_version: str = "0",
    raw_input: str | None = None,
    timeout_s: float = RUN_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """`stream` with its own event loop: one run of `input` over A2A, the events in order."""
    message = message_for(input, ctx, schema_version=schema_version, raw_input=raw_input)
    return asyncio.run(stream(workload, message, timeout_s=timeout_s))


async def _collect(handle: Any, input: dict[str, Any], ctx: dict[str, Any]) -> list[Any]:
    return [event async for event in handle(input, ctx)]


def direct(handle: Any, input: Mapping[str, Any], ctx: Mapping[str, Any]) -> list[Any]:
    """The events `handle` yields when called with no lane at all."""
    return asyncio.run(_collect(handle, dict(input), dict(ctx)))
