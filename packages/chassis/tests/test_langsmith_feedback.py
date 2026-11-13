"""`LangSmithFeedback` (130 CH-11, ADR-009 item 3a): the LangSmith feedback adapter passes the
same `FeedbackPortContract` as `InMemoryFeedback` and `LangfuseFeedback`, offline.

The binding talks to `fake-langsmith` (its feedback route) in process over an ASGI transport.
No key, no network.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from chassis.adapters.langsmith import (
    LangSmithFeedback,
    feedback_body,
    feedback_from_record,
    langsmith_feedback_id,
)
from chassis.core.handle import echo
from chassis.core.trace import trace_id_hex
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.fakes.state import InMemoryState
from chassis.ports.auth import NoAuth
from chassis.ports.bundle import PortBundle
from chassis.ports.feedback import Feedback, FeedbackError
from chassis.profiles import AdapterNotAvailable, resolve
from chassis.server import ChassisConfig, create_app
from chassis.server.feedback import FEEDBACK_PATH as ROUTE
from chassis.server.feedback import bind_score_name
from chassis_contracts import FeedbackPortContract, make_feedback
from fake_langsmith import FakeLangSmith
from fake_langsmith import create_app as create_fake
from fastapi import FastAPI

KEY = "lsv2-test-not-a-secret"  # pragma: allowlist secret


@pytest.fixture
def route() -> FakeLangSmith:
    return FakeLangSmith(api_key=KEY)


def adapter_for(route: FakeLangSmith, key: str = KEY, **kwargs: Any) -> LangSmithFeedback:
    transport = httpx.ASGITransport(app=create_fake(route))
    return LangSmithFeedback("http://langsmith", key, transport=transport, **kwargs)


class TestLangSmithFeedback(FeedbackPortContract):
    @pytest.fixture
    def feedback_port(self, route: FakeLangSmith) -> LangSmithFeedback:
        return adapter_for(route)

    @pytest.fixture
    def read_feedback(self, route: FakeLangSmith) -> Callable[[str], Feedback | None]:
        def read(feedback_id: str) -> Feedback | None:
            record = route.feedback.get(langsmith_feedback_id(feedback_id))
            return None if record is None else feedback_from_record(record)

        return read

    @pytest.fixture
    def count_feedback(self, route: FakeLangSmith) -> Callable[[str], int]:
        return lambda fid: int(langsmith_feedback_id(fid) in route.feedback)

    @pytest.fixture
    def make_unavailable(self, route: FakeLangSmith) -> Callable[[], None]:
        return lambda: setattr(route, "unavailable", True)


async def test_the_score_lands_on_the_run_of_the_trace(route: FakeLangSmith) -> None:
    feedback = make_feedback(label="helpful", comment="Clear.", subject="user-1")
    await adapter_for(route).submit(feedback)
    (record,) = route.feedback.values()
    run = str(uuid.UUID(feedback.trace_id))
    assert (record["run_id"], record["trace_id"]) == (run, run)
    assert (record["key"], record["score"], record["comment"]) == ("user-feedback", 1.0, "Clear.")
    assert record["id"] == langsmith_feedback_id(feedback.feedback_id)
    assert record["feedback_source"]["metadata"] == {
        "feedback_id": feedback.feedback_id,
        "request_id": feedback.request_id,
        "label": "helpful",
        "subject": "user-1",
    }


async def test_the_score_name_comes_from_the_spec(route: FakeLangSmith) -> None:
    port = adapter_for(route)
    bind_score_name(port, "thumbs")
    await port.submit(make_feedback())
    assert [r["key"] for r in route.feedback.values()] == ["thumbs"]


async def test_a_wrong_key_is_rejected_and_never_echoed(route: FakeLangSmith) -> None:
    wrong = "lsv2-wrong-not-a-secret"
    with pytest.raises(FeedbackError) as info:
        await adapter_for(route, key=wrong).submit(make_feedback())
    assert (info.value.code, info.value.retryable) == ("feedback_rejected", False)
    assert wrong not in str(info.value)


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_a_5xx_or_429_is_unavailable_and_retryable(status: int) -> None:
    port = LangSmithFeedback(
        "http://langsmith", KEY, transport=httpx.MockTransport(lambda r: httpx.Response(status))
    )
    with pytest.raises(FeedbackError) as info:
        await port.submit(make_feedback())
    assert (info.value.code, info.value.retryable) == ("feedback_unavailable", True)


async def test_a_transport_error_is_unavailable_and_holds_no_key() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    port = LangSmithFeedback("http://langsmith", KEY, transport=httpx.MockTransport(fail))
    with pytest.raises(FeedbackError) as info:
        await port.submit(make_feedback())
    assert (info.value.code, info.value.retryable) == ("feedback_unavailable", True)
    assert KEY not in str(info.value)
    assert info.value.__cause__ is None


def test_the_key_is_not_in_repr() -> None:
    assert KEY not in repr(LangSmithFeedback("http://langsmith", KEY))


def test_the_body_omits_an_empty_comment() -> None:
    feedback = make_feedback(comment=None, label=None, subject=None)
    body = feedback_body(feedback)
    assert "comment" not in body
    assert body["feedback_source"]["metadata"] == {
        "feedback_id": feedback.feedback_id,
        "request_id": feedback.request_id,
    }


def test_any_feedback_id_maps_to_one_stable_uuid() -> None:
    first = langsmith_feedback_id("caller-chose-this")
    assert first == langsmith_feedback_id("caller-chose-this")
    assert uuid.UUID(first).version == 5
    assert first != langsmith_feedback_id("another")


def test_from_env_reads_the_endpoint_and_key_and_has_no_default_host() -> None:
    port = LangSmithFeedback.from_env(
        {"LANGSMITH_ENDPOINT": "http://ls.local/", "LANGSMITH_API_KEY": KEY}
    )
    assert port.host == "http://ls.local"
    with pytest.raises(LookupError, match="LANGSMITH_ENDPOINT"):
        LangSmithFeedback.from_env({"LANGSMITH_API_KEY": KEY})
    with pytest.raises(LookupError, match="LANGSMITH_API_KEY"):
        LangSmithFeedback.from_env({"LANGSMITH_ENDPOINT": "http://ls.local"})


def test_spec_adapters_feedback_langsmith_builds_the_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://ls.local")
    monkeypatch.setenv("LANGSMITH_API_KEY", KEY)
    assert isinstance(resolve("feedback", "langsmith"), LangSmithFeedback)


def test_spec_adapters_feedback_langsmith_without_a_key_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://ls.local")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    with pytest.raises(AdapterNotAvailable, match="not configured"):
        resolve("feedback", "langsmith")


def _langsmith_app(feedback: LangSmithFeedback) -> FastAPI:
    config = ChassisConfig.model_validate(
        {
            "version": "cfg-1",
            "profile": "fake",
            "agent": {"name": "echo", "version": "0.0.1"},
            "spec": {
                "adapters": {"feedback": "langsmith"},
                "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
            },
        }
    )
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
        events=InMemoryBus(),
        feedback=feedback,
        auth=NoAuth(),
    )
    return create_app(config, ports)


@asynccontextmanager
async def _chassis(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chassis") as client:
            yield client


async def test_post_v1_feedback_writes_one_score_on_the_runs_langsmith_trace(
    route: FakeLangSmith,
) -> None:
    """`spec.adapters.feedback: langsmith`: a run, then `POST /v1/feedback`, is one score in
    fake-langsmith on that run's trace, read back over the fake's run route."""
    async with _chassis(_langsmith_app(adapter_for(route))) as client:
        run = await client.post("/v1/run", json={"input": {"text": "hello"}})
        assert run.status_code == 200
        answer = await client.post(
            ROUTE, json={"request_id": run.json()["request_id"], "score": 1, "label": "helpful"}
        )
    assert answer.status_code == 202
    trace = trace_id_hex(run.json()["trace_id"])
    assert answer.json()["trace_id"] == trace
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_fake(route)), base_url="http://langsmith"
    ) as fake:
        read = await fake.get(f"/fake/runs/{trace}/feedback")
    (score,) = read.json()["feedback"]
    run_uuid = str(uuid.UUID(trace))
    assert (score["run_id"], score["trace_id"]) == (run_uuid, run_uuid)
    assert (score["key"], score["score"]) == ("user-feedback", 1.0)
    assert score["feedback_source"]["metadata"]["request_id"] == run.json()["request_id"]
    assert score["id"] == langsmith_feedback_id(answer.json()["feedback_id"])
