"""Builders for the request and context the suites use."""

from __future__ import annotations

from chassis import CHASSIS_VERSION
from chassis.core.envelope import Context, Request, TaskInput, Versions


def make_request(text: str = "hello world", request_id: str = "req-1") -> Request:
    return Request(
        request_id=request_id,
        trace_id="trace-1",
        idempotency_key="idem-1",
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text=text),
    )


def make_context(request: Request, model_route: str | None = "fake-route") -> Context:
    return Context(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        budget=request.budget,
        versions=Versions(chassis=CHASSIS_VERSION, model_route=model_route),
        model_route=model_route,
    )
