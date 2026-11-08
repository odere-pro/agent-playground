"""The model proxy under failure (CH-16), on both proxy routes: the service's budget refusal is
429 `service_budget_exhausted` with a counter per route, the run's remaining time reaches the port
as `timeout_s`, and a call whose run ends mid-flight is cut and answered 403 `run_ended`. Driven in
process, no socket.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import pytest
from chassis.fakes import ScriptedModel, ScriptRule
from chassis.ports.model import (
    SERVICE_BUDGET_EXHAUSTED,
    ModelChunk,
    ModelError,
    ModelMessage,
    ModelResult,
    ToolSpec,
    Usage,
)
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from poc_harness.promtext import families, value
from proxy_fakes import (
    ANTHROPIC_BODY,
    TRACEPARENT,
    FailingModel,
    asgi_client,
    proxy_ports,
    run_request,
    telemetry_of,
)

CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "big-default"},
    },
}
CHAT_BODY: dict[str, Any] = {
    "model": "big-default",
    "messages": [{"role": "user", "content": "simplify: the quick brown fox"}],
    "max_tokens": 64,
}
UPSTREAM_TEXT = "ExceededBudget: Crossed spend threshold for key sk-abc123 (budget-marker)"
BUDGET_COUNTER = "chassis.model_budget_exhausted"
RUN_TIMEOUT_MS = 5_000


def _app(model: Any) -> Any:
    return create_app(ChassisConfig.model_validate(CONFIG), proxy_ports(model))


def _chunks(text: str) -> list[Any]:
    out: list[Any] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            out.append(payload if payload == "[DONE]" else json.loads(payload))
    return out


# ---- the service's budget refusal -------------------------------------------------------------


async def test_the_chat_route_answers_429_service_budget_exhausted_and_counts_it() -> None:
    """A complete call: 429, the fixed text, `retryable: false`, `x-should-retry: false`; the
    upstream text stays out of the response; `chassis.model_budget_exhausted{route}` rises."""
    app = _app(FailingModel(ModelError(SERVICE_BUDGET_EXHAUSTED, UPSTREAM_TEXT)))
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json=CHAT_BODY)
    assert res.status_code == 429, res.text
    assert res.headers["x-should-retry"] == "false"
    assert "budget-marker" not in res.text and "sk-abc123" not in res.text
    error = res.json()["error"]
    assert error["code"] == SERVICE_BUDGET_EXHAUSTED
    assert error["retryable"] is False
    assert error["type"] == "model_error"
    assert error["message"] == "the service's model budget is exhausted"
    telemetry = telemetry_of(app.state.ports)
    assert telemetry.counter_value(BUDGET_COUNTER, route="big-default") == 1


async def test_the_chat_stream_sends_the_code_in_its_error_frame_and_counts_it() -> None:
    app = _app(FailingModel(ModelError(SERVICE_BUDGET_EXHAUSTED, UPSTREAM_TEXT)))
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json={**CHAT_BODY, "stream": True})
    assert res.status_code == 200
    chunks = _chunks(res.text)
    errors = [c for c in chunks if isinstance(c, dict) and "error" in c]
    assert len(errors) == 1 and chunks[-1] == "[DONE]"
    assert errors[0]["error"]["code"] == SERVICE_BUDGET_EXHAUSTED
    assert errors[0]["error"]["retryable"] is False
    assert "budget-marker" not in res.text
    telemetry = telemetry_of(app.state.ports)
    assert telemetry.counter_value(BUDGET_COUNTER, route="big-default") == 1


@pytest.mark.parametrize("stream", [False, True])
async def test_the_anthropic_route_answers_429_service_budget_exhausted(stream: bool) -> None:
    """Anthropic's shape: `rate_limit_error`, `<code>: <fixed text>`, `x-should-retry: false`;
    for a stream the hold window turns the early error into the same HTTP status."""
    app = _app(FailingModel(ModelError(SERVICE_BUDGET_EXHAUSTED, UPSTREAM_TEXT)))
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json={**ANTHROPIC_BODY, "stream": stream})
    assert res.status_code == 429, res.text
    assert res.headers["x-should-retry"] == "false"
    assert "budget-marker" not in res.text
    assert res.json()["error"] == {
        "type": "rate_limit_error",
        "message": f"{SERVICE_BUDGET_EXHAUSTED}: the service's model budget is exhausted",
    }
    telemetry = telemetry_of(app.state.ports)
    assert telemetry.counter_value(BUDGET_COUNTER, route="big-default") == 1


async def test_the_counter_is_a_declared_family_on_metrics() -> None:
    """`/metrics` on the public port shows `chassis_model_budget_exhausted_total{route}` with a
    HELP line, so the alert rule in `deploy/prometheus/rules` has a metric to read."""
    app = _app(FailingModel(ModelError(SERVICE_BUDGET_EXHAUSTED, UPSTREAM_TEXT)))
    async with (
        asgi_client(create_proxy_app(app)) as proxy,
        asgi_client(app) as public,
        app.router.lifespan_context(app),
    ):
        await proxy.post("/v1/chat/completions", json=CHAT_BODY)
        await proxy.post("/v1/messages", json=ANTHROPIC_BODY)
        text = (await public.get("/metrics")).text
    assert families(text)["chassis_model_budget_exhausted_total"] == "counter"
    assert value(text, "chassis_model_budget_exhausted_total", route="big-default") == 2


async def test_every_chat_error_carries_x_should_retry_from_retryable() -> None:
    """A retryable upstream failure is 502 with `x-should-retry: true`; a non-retryable one is
    500 with `false` (the OpenAI SDK would otherwise retry the 500 on its own)."""
    for error, status, header in (
        (ModelError("upstream_down", "x", retryable=True), 502, "true"),
        (ModelError("bad_response", "x"), 500, "false"),
    ):
        app = _app(FailingModel(error))
        async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
            res = await client.post("/v1/chat/completions", json=CHAT_BODY)
        assert (res.status_code, res.headers["x-should-retry"]) == (status, header), res.text


# ---- the run's remaining time reaches the port -------------------------------------------------


class TimeoutsModel(ScriptedModel):
    """Records the `timeout_s` each call was given."""

    def __init__(self) -> None:
        super().__init__([ScriptRule(reply="Plain words.")])
        self.timeouts: list[float | None] = []

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> ModelResult:
        self.timeouts.append(timeout_s)
        return await super().complete(messages, route=route, max_tokens=max_tokens)

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> AsyncIterator[ModelChunk]:
        self.timeouts.append(timeout_s)
        async for chunk in super().stream(messages, route=route, max_tokens=max_tokens):
            yield chunk


@pytest.mark.parametrize(
    ("path", "body"),
    [("/v1/chat/completions", CHAT_BODY), ("/v1/messages", ANTHROPIC_BODY)],
    ids=["chat", "anthropic"],
)
@pytest.mark.parametrize("stream", [False, True])
async def test_a_correlated_call_gets_the_runs_remaining_time(
    path: str, body: dict[str, Any], stream: bool
) -> None:
    """A run with `timeout_ms` 5000 that just opened: the port's `timeout_s` is what is left."""
    model = TimeoutsModel()
    app = _app(model)
    request, ctx = run_request(max_tokens=1000, timeout_ms=RUN_TIMEOUT_MS)
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx),
    ):
        res = await client.post(
            path, json={**body, "stream": stream}, headers={"traceparent": TRACEPARENT}
        )
    assert res.status_code == 200, res.text
    (timeout_s,) = model.timeouts
    assert timeout_s is not None and 4.0 < timeout_s <= 5.0, timeout_s


@pytest.mark.parametrize("stream", [False, True])
async def test_an_uncorrelated_chat_call_gets_no_timeout_from_the_proxy(stream: bool) -> None:
    """No run, no remaining time: the adapter's own default bounds the call."""
    model = TimeoutsModel()
    app = _app(model)
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json={**CHAT_BODY, "stream": stream})
    assert res.status_code == 200, res.text
    assert model.timeouts == [None]


# ---- a run that ends mid-call -----------------------------------------------------------------


class HeldModel:
    """Never answers until `release`; records when it was called and whether it was cancelled."""

    name = "held"

    def __init__(self) -> None:
        self.called = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = 0

    async def _hold(self) -> None:
        self.called.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise

    async def complete(self, messages: Sequence[ModelMessage], **_: Any) -> ModelResult:
        await self._hold()
        return ModelResult(text="late", usage=Usage())

    async def stream(self, messages: Sequence[ModelMessage], **_: Any) -> AsyncIterator[ModelChunk]:
        await self._hold()
        yield ModelChunk(text="late")
        yield ModelChunk(usage=Usage(), finish=True)


async def _call_then_end_the_run(
    path: str, body: dict[str, Any]
) -> tuple[httpx.Response, HeldModel]:
    model = HeldModel()
    app = _app(model)
    request, ctx = run_request(max_tokens=1000)
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        record = app.state.runs.open(request, ctx)
        call = asyncio.create_task(
            client.post(path, json=body, headers={"traceparent": TRACEPARENT})
        )
        try:
            await asyncio.wait_for(model.called.wait(), 5)
            app.state.runs.close(record)  # the run ended: cancelled, timed out, or done
            res = await asyncio.wait_for(call, 5)
        finally:
            model.release.set()
            call.cancel()
    return res, model


async def test_a_chat_call_whose_run_ends_is_cut_and_answered_403_run_ended() -> None:
    """The model call is cancelled (the adapter then closes its connection) and the workload gets
    403 `run_ended`, not retryable: the run is gone, a retry would name no run."""
    res, model = await _call_then_end_the_run("/v1/chat/completions", CHAT_BODY)
    assert res.status_code == 403, res.text
    assert res.headers["x-should-retry"] == "false"
    assert res.json()["error"] == {
        "message": "the run this call belongs to has ended",
        "type": "permission_error",
        "code": "run_ended",
        "retryable": False,
    }
    assert model.cancelled == 1


@pytest.mark.parametrize("stream", [False, True])
async def test_an_anthropic_call_whose_run_ends_is_cut_and_answered_403_run_ended(
    stream: bool,
) -> None:
    res, model = await _call_then_end_the_run("/v1/messages", {**ANTHROPIC_BODY, "stream": stream})
    assert res.status_code == 403, res.text
    assert res.headers["x-should-retry"] == "false"
    assert res.json()["error"] == {
        "type": "permission_error",
        "message": "run_ended: the run this call belongs to has ended",
    }
    assert model.cancelled == 1
