"""`LangfuseFeedback` (PoC-7, exit criterion 2): the Langfuse scores adapter passes the same
`FeedbackPortContract` as `InMemoryFeedback`, offline, against the fake Langfuse API
(`packages/fake-langfuse`) over an ASGI transport. The live binding is
`tests/integration/test_langfuse_feedback_contract.py` (`network`).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from chassis.adapters.langfuse import DEFAULT_SCORE_NAME, LangfuseFeedback, feedback_from_score
from chassis.ports.feedback import Feedback, FeedbackError
from chassis.profiles import REGISTRY
from chassis_contracts import FeedbackPortContract, make_feedback
from fake_langfuse import FakeLangfuse, create_app

PK, SK = "pk-lf-test", "sk-lf-test-not-a-secret"


@pytest.fixture
def fake() -> FakeLangfuse:
    return FakeLangfuse(PK, SK)


def adapter_for(fake: FakeLangfuse, **kwargs: Any) -> LangfuseFeedback:
    transport = httpx.ASGITransport(app=create_app(fake))
    return LangfuseFeedback("http://langfuse", PK, SK, transport=transport, **kwargs)


class TestLangfuseFeedback(FeedbackPortContract):
    @pytest.fixture
    def feedback_port(self, fake: FakeLangfuse) -> LangfuseFeedback:
        return adapter_for(fake)

    @pytest.fixture
    def read_feedback(self, fake: FakeLangfuse) -> Callable[[str], Feedback | None]:
        def read(feedback_id: str) -> Feedback | None:
            score = fake.scores.get(feedback_id)
            return None if score is None else feedback_from_score(score)

        return read

    @pytest.fixture
    def count_feedback(self, fake: FakeLangfuse) -> Callable[[str], int]:
        return lambda fid: int(fid in fake.scores)

    @pytest.fixture
    def make_unavailable(self, fake: FakeLangfuse) -> Callable[[], None]:
        return lambda: setattr(fake, "unavailable", True)


async def test_the_score_is_a_numeric_score_on_the_trace(fake: FakeLangfuse) -> None:
    feedback = make_feedback(label="helpful", comment="Clear.", subject="user-1")
    await adapter_for(fake).submit(feedback)
    score = fake.scores[feedback.feedback_id]
    assert score["traceId"] == feedback.trace_id
    assert (score["name"], score["value"], score["dataType"]) == (
        DEFAULT_SCORE_NAME,
        1.0,
        "NUMERIC",
    )
    assert score["comment"] == "Clear."
    assert score["metadata"] == {
        "request_id": feedback.request_id,
        "label": "helpful",
        "subject": "user-1",
    }
    assert list(fake.scores) == [feedback.feedback_id], "one score per feedback, no label score"


async def test_the_score_name_comes_from_the_spec(fake: FakeLangfuse) -> None:
    feedback = make_feedback()
    await adapter_for(fake, score_name="thumbs").submit(feedback)
    assert fake.scores[feedback.feedback_id]["name"] == "thumbs"


async def test_a_timestamp_is_sent_so_a_repeat_updates_the_same_score(fake: FakeLangfuse) -> None:
    at = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    feedback = make_feedback(timestamp=at)
    port = adapter_for(fake)
    await port.submit(feedback)
    await port.submit(feedback)
    assert fake.scores[feedback.feedback_id]["timestamp"] == "2026-10-28T12:00:00Z"
    assert fake.writes[feedback.feedback_id] == 2
    assert len(fake.scores) == 1


async def test_wrong_keys_are_rejected_not_retryable(fake: FakeLangfuse) -> None:
    port = LangfuseFeedback(
        "http://langfuse", PK, "wrong", transport=httpx.ASGITransport(app=create_app(fake))
    )
    with pytest.raises(FeedbackError) as info:
        await port.submit(make_feedback())
    assert (info.value.code, info.value.retryable) == ("feedback_rejected", False)
    assert "401" in info.value.message


async def test_a_207_with_a_rejected_score_is_rejected() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        body = {"accepted": 0, "rejected": 1, "errors": [{"message": "bad value"}]}
        return httpx.Response(207, json=body)

    port = LangfuseFeedback("http://langfuse", PK, SK, transport=httpx.MockTransport(answer))
    with pytest.raises(FeedbackError) as info:
        await port.submit(make_feedback())
    assert info.value.code == "feedback_rejected"
    assert "bad value" in info.value.message


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_a_5xx_or_429_is_unavailable_and_retryable(status: int) -> None:
    port = LangfuseFeedback(
        "http://langfuse", PK, SK, transport=httpx.MockTransport(lambda r: httpx.Response(status))
    )
    with pytest.raises(FeedbackError) as info:
        await port.submit(make_feedback())
    assert (info.value.code, info.value.retryable) == ("feedback_unavailable", True)


async def test_a_transport_error_is_unavailable_and_holds_no_secret() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    port = LangfuseFeedback("http://langfuse", PK, SK, transport=httpx.MockTransport(fail))
    with pytest.raises(FeedbackError) as info:
        await port.submit(make_feedback())
    assert (info.value.code, info.value.retryable) == ("feedback_unavailable", True)
    assert SK not in str(info.value)
    assert info.value.__cause__ is None


async def test_the_request_is_one_list_with_basic_auth() -> None:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"message": "Accepted"})

    port = LangfuseFeedback("http://langfuse/", PK, SK, transport=httpx.MockTransport(record))
    await port.submit(make_feedback(label=None, comment=None, subject=None))
    (request,) = seen
    assert (request.method, request.url.path) == ("POST", "/api/public/scores")
    assert request.headers["authorization"] == httpx.BasicAuth(PK, SK)._auth_header
    body = json.loads(request.content)
    assert isinstance(body, list)
    assert len(body) == 1
    assert "comment" not in body[0]
    assert body[0]["metadata"]["request_id"]


def test_from_env_reads_the_three_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.local:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", PK)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", SK)
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    port = LangfuseFeedback.from_env()
    assert port.host == "http://langfuse.local:3000"
    assert SK not in repr(port)
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://base.local")
    assert LangfuseFeedback.from_env().host == "http://base.local", "the SDK 5.x name wins"


@pytest.mark.parametrize("missing", ["LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"])
def test_from_env_without_a_variable_is_not_configured(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    for name in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.setenv(name, "x")
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    monkeypatch.delenv(missing)
    with pytest.raises(LookupError, match=missing):
        LangfuseFeedback.from_env()


def test_the_registry_builds_it_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LANGFUSE_BASE_URL", "LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    factory = REGISTRY["feedback"]["langfuse"]
    assert callable(factory)
    with pytest.raises(LookupError):
        factory()
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.local")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", PK)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", SK)
    assert isinstance(factory(), LangfuseFeedback)
