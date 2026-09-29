from __future__ import annotations

import pytest
from chassis import CHASSIS_VERSION
from chassis.core.envelope import Context, Request, TaskInput, Versions


@pytest.fixture
def request_() -> Request:
    return Request(
        request_id="req-1",
        trace_id="trace-1",
        idempotency_key="idem-1",
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text="hello big world"),
    )


@pytest.fixture
def context(request_: Request) -> Context:
    return Context(
        request_id=request_.request_id,
        trace_id=request_.trace_id,
        idempotency_key=request_.idempotency_key,
        agent=request_.agent,
        agent_version=request_.agent_version,
        budget=request_.budget,
        versions=Versions(chassis=CHASSIS_VERSION, model_route="fake-route"),
        model_route="fake-route",
    )
