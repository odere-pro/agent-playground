"""Turn an event stream into one complete response."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from chassis.core.envelope import Request, Response, Status, Versions
from chassis.core.events import Delta, End, Error, Event, Metrics, ToolCall


async def collect(events: AsyncIterator[Event], request: Request, versions: Versions) -> Response:
    text: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0, "attempts": 0}
    status: Status = "error"
    output: dict[str, Any] | None = None
    error: Error | None = None

    async for event in events:
        if isinstance(event, Delta):
            text.append(event.text)
        elif isinstance(event, ToolCall):
            tool_calls.append(event.model_dump(exclude={"type", "schema_version"}))
        elif isinstance(event, Metrics):
            metrics["input_tokens"] += event.input_tokens
            metrics["output_tokens"] += event.output_tokens
            metrics["attempts"] = max(metrics["attempts"], event.attempt)
            if event.cost_usd is not None:
                metrics["cost_usd"] = metrics.get("cost_usd", 0.0) + event.cost_usd
            if event.model_route:
                metrics["model_route"] = event.model_route
        elif isinstance(event, End):
            status = event.status
            output = event.output
        elif isinstance(event, Error):
            error = event
            status = "error"

    if output is None:
        output = {"text": "".join(text)}
        if tool_calls:
            output["tool_calls"] = tool_calls
    if error is not None:
        output["error"] = {"code": error.code, "message": error.message}

    return Response(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        output=output,
        metrics=metrics,
        status=status,
        versions=versions,
        context_ref=request.context_ref,
    )
