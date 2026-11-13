"""`LangSmithFeedback` (130 CH-11, ADR-009 item 3a): the LangSmith feedback adapter passes the
same `FeedbackPortContract` as `InMemoryFeedback` and `LangfuseFeedback`, offline.

`fake-langsmith` (129 CH-10) does not exist yet, so the binding here answers the feedback route
with a small in-test stand-in over `httpx.MockTransport`. No key, no network.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from chassis.adapters.langsmith import (
    FEEDBACK_PATH,
    LangSmithFeedback,
    feedback_body,
    feedback_from_record,
    langsmith_feedback_id,
)
from chassis.ports.feedback import Feedback, FeedbackError
from chassis.profiles import AdapterNotAvailable, resolve
from chassis.server.feedback import bind_score_name
from chassis_contracts import FeedbackPortContract, make_feedback

KEY = "lsv2-test-not-a-secret"


class FeedbackRouteStandIn:
    """The LangSmith feedback route, in memory: upsert by `id`, `x-api-key` auth, a scripted
    outage. A stand-in until `fake-langsmith` lands (129 CH-10)."""

    def __init__(self, key: str = KEY) -> None:
        self.key = key
        self.records: dict[str, dict[str, Any]] = {}
        self.unavailable = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.unavailable:
            return httpx.Response(503)
        if request.headers.get("x-api-key") != self.key:
            return httpx.Response(401, json={"detail": "Invalid token"})
        if (request.method, request.url.path) != ("POST", FEEDBACK_PATH):
            return httpx.Response(404)
        body = json.loads(request.content)
        self.records[body["id"]] = body
        return httpx.Response(200, json=body)


@pytest.fixture
def route() -> FeedbackRouteStandIn:
    return FeedbackRouteStandIn()


def adapter_for(route: FeedbackRouteStandIn, key: str = KEY, **kwargs: Any) -> LangSmithFeedback:
    return LangSmithFeedback(
        "http://langsmith", key, transport=httpx.MockTransport(route), **kwargs
    )


class TestLangSmithFeedback(FeedbackPortContract):
    @pytest.fixture
    def feedback_port(self, route: FeedbackRouteStandIn) -> LangSmithFeedback:
        return adapter_for(route)

    @pytest.fixture
    def read_feedback(self, route: FeedbackRouteStandIn) -> Callable[[str], Feedback | None]:
        def read(feedback_id: str) -> Feedback | None:
            record = route.records.get(langsmith_feedback_id(feedback_id))
            return None if record is None else feedback_from_record(record)

        return read

    @pytest.fixture
    def count_feedback(self, route: FeedbackRouteStandIn) -> Callable[[str], int]:
        return lambda fid: int(langsmith_feedback_id(fid) in route.records)

    @pytest.fixture
    def make_unavailable(self, route: FeedbackRouteStandIn) -> Callable[[], None]:
        return lambda: setattr(route, "unavailable", True)


async def test_the_score_lands_on_the_run_of_the_trace(route: FeedbackRouteStandIn) -> None:
    feedback = make_feedback(label="helpful", comment="Clear.", subject="user-1")
    await adapter_for(route).submit(feedback)
    (record,) = route.records.values()
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


async def test_the_score_name_comes_from_the_spec(route: FeedbackRouteStandIn) -> None:
    port = adapter_for(route)
    bind_score_name(port, "thumbs")
    await port.submit(make_feedback())
    assert [r["key"] for r in route.records.values()] == ["thumbs"]


async def test_a_wrong_key_is_rejected_and_never_echoed(route: FeedbackRouteStandIn) -> None:
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
