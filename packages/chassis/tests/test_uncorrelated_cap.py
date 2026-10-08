"""The uncorrelated cap (PoC-5, H16): model calls that name no run in flight are served, then
refused with 429 `budget_exhausted` past `spec.limits.uncorrelated_tokens_per_minute` tokens in a
fixed one-minute window. `0` refuses all, a reload changes the cap, a correlated call is never
touched. Time is a fake clock; nothing sleeps. The scripted model reports 15 tokens a call.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, MutableMapping, Sequence
from typing import Any

import httpx
from chassis import CHASSIS_VERSION
from chassis.core.envelope import Budget, Context, Request, TaskInput, Versions
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel, ScriptRule
from chassis.ports.bundle import PortBundle
from chassis.ports.model import (
    ModelChunk,
    ModelError,
    ModelMessage,
    ModelResult,
    ToolSpec,
    Usage,
)
from chassis.server import ChassisConfig, create_app
from chassis.server.model_proxy import (
    DEFAULT_UNCORRELATED_MAX_TOKENS,
    WINDOW_S,
    UncorrelatedCap,
)
from chassis.server.proxy_app import create_proxy_app

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"
BODY: dict[str, Any] = {
    "model": "big-default",
    "messages": [{"role": "user", "content": "simplify: the quick brown fox"}],
    "max_tokens": 5,
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _config(cap: int | None) -> ChassisConfig:
    spec: dict[str, Any] = {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}
    }
    if cap is not None:
        spec["limits"] = {"uncorrelated_tokens_per_minute": cap}
    return ChassisConfig.model_validate(
        {"profile": "fake", "agent": {"name": "echo", "version": "0.0.1"}, "spec": spec}
    )


def _ports(model: Any = None) -> PortBundle:
    return PortBundle(
        model=model or ScriptedModel([ScriptRule(match="simplify", reply="Plain words.")]),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )


def _build(cap: int | None, model: Any = None) -> tuple[Any, Clock, PortBundle]:
    clock = Clock()
    ports = _ports(model)
    app = create_app(_config(cap), ports)
    proxy = create_proxy_app(app)
    # The router was built with the real clock; swap the cap for one on the fake clock.
    app.state.uncorrelated_cap = UncorrelatedCap(clock)
    return (app, proxy), clock, ports


def _client(proxy: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url="http://chassis")


def _run_ctx(max_tokens: int) -> tuple[Request, Context]:
    request = Request(
        request_id="req-1",
        trace_id=TRACE,
        idempotency_key="idem-1",
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text="x"),
        budget=Budget(max_tokens=max_tokens),
    )
    ctx = Context(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        budget=request.budget,
        versions=Versions(chassis=CHASSIS_VERSION),
    )
    return request, ctx


async def test_calls_are_served_until_the_window_has_spent_the_cap() -> None:
    (app, proxy), _, ports = _build(30)  # two calls of 15 tokens
    async with _client(proxy) as client, app.router.lifespan_context(app):
        first = await client.post("/v1/chat/completions", json=BODY)
        second = await client.post("/v1/chat/completions", json=BODY)
        third = await client.post("/v1/chat/completions", json=BODY)
    assert (first.status_code, second.status_code, third.status_code) == (200, 200, 429)
    error = third.json()["error"]
    assert error["code"] == "budget_exhausted" and error["retryable"] is True
    assert "authorization" not in json.dumps(error).lower()
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    assert telemetry.counter_value("chassis.model_calls_uncorrelated", route="big-default") == 3
    assert telemetry.counter_value("chassis.model_calls_refused", route="big-default") == 1
    model = ports.model
    assert isinstance(model, ScriptedModel) and len(model.calls) == 2


async def test_a_streamed_call_is_charged_and_refused_as_one_error_frame() -> None:
    (app, proxy), _, _ = _build(15)
    stream = {**BODY, "stream": True}
    async with _client(proxy) as client, app.router.lifespan_context(app):
        first = await client.post("/v1/chat/completions", json=stream)
        second = await client.post("/v1/chat/completions", json=stream)
    assert first.status_code == 200 and second.status_code == 429
    frames = [ln[5:].strip() for ln in second.text.splitlines() if ln.startswith("data:")]
    assert json.loads(frames[0])["error"]["code"] == "budget_exhausted"
    assert frames[-1] == "[DONE]"


async def test_the_window_resets_after_a_minute() -> None:
    (app, proxy), clock, _ = _build(15)
    async with _client(proxy) as client, app.router.lifespan_context(app):
        assert (await client.post("/v1/chat/completions", json=BODY)).status_code == 200
        assert (await client.post("/v1/chat/completions", json=BODY)).status_code == 429
        clock.now += WINDOW_S - 1
        assert (await client.post("/v1/chat/completions", json=BODY)).status_code == 429
        clock.now += 1
        assert (await client.post("/v1/chat/completions", json=BODY)).status_code == 200


async def test_zero_refuses_every_uncorrelated_call_and_never_reaches_the_model() -> None:
    (app, proxy), _, ports = _build(0)
    async with _client(proxy) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json=BODY)
    assert res.status_code == 429
    error = res.json()["error"]
    assert error["code"] == "budget_exhausted" and error["retryable"] is False
    model = ports.model
    assert isinstance(model, ScriptedModel) and model.calls == []


async def test_the_default_cap_serves_a_first_call() -> None:
    (app, proxy), _, _ = _build(None)
    async with _client(proxy) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json=BODY)
    assert res.status_code == 200


async def test_a_reload_changes_the_cap_without_a_restart() -> None:
    (app, proxy), _, _ = _build(15)
    async with _client(proxy) as client, app.router.lifespan_context(app):
        assert (await client.post("/v1/chat/completions", json=BODY)).status_code == 200
        assert (await client.post("/v1/chat/completions", json=BODY)).status_code == 429
        app.state.config = _config(100)  # what an accepted reload does: one assignment
        assert (await client.post("/v1/chat/completions", json=BODY)).status_code == 200
        app.state.config = _config(0)
        assert (await client.post("/v1/chat/completions", json=BODY)).status_code == 429


async def test_a_correlated_call_is_never_touched_by_the_cap() -> None:
    (app, proxy), _, _ = _build(0)
    request, ctx = _run_ctx(max_tokens=100)
    async with (
        _client(proxy) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        for _ in range(3):
            res = await client.post(
                "/v1/chat/completions", json=BODY, headers={"traceparent": TRACEPARENT}
            )
            assert res.status_code == 200, res.text
        # The run's own tokens did not count against the cap either.
        assert app.state.uncorrelated_cap.spent == 0
    assert record.model_calls == 3


async def test_a_traceparent_naming_no_run_in_flight_counts_as_uncorrelated() -> None:
    (app, proxy), _, _ = _build(0)
    async with _client(proxy) as client, app.router.lifespan_context(app):
        res = await client.post(
            "/v1/chat/completions", json=BODY, headers={"traceparent": TRACEPARENT}
        )
    assert res.status_code == 429


class GatedModel:
    """A model whose calls wait for `gate`. Usage is 10 input tokens plus the full `max_tokens`
    as output: the worst case an upstream that honors `max_tokens` can report.
    """

    PROMPT = 10

    def __init__(self, *, fail: bool = False) -> None:
        self.gate = asyncio.Event()
        self.max_tokens: list[int | None] = []
        self.fail = fail

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self.max_tokens.append(max_tokens)
        await self.gate.wait()
        if self.fail:
            raise ModelError("upstream_error", "the upstream failed", retryable=True)
        return ModelResult(text="ok", usage=self._usage(max_tokens))

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        self.max_tokens.append(max_tokens)
        yield ModelChunk(text="first")
        await self.gate.wait()
        yield ModelChunk(usage=self._usage(max_tokens), finish=True)

    def _usage(self, max_tokens: int | None) -> Usage:
        return Usage(input_tokens=self.PROMPT, output_tokens=max_tokens or 0)


async def _until(predicate: Any) -> None:
    """Yield to the event loop until `predicate()` holds; fail after a bounded number of turns."""
    for _ in range(10_000):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("the condition never held")


async def test_concurrent_calls_admit_only_what_fits_and_the_overshoot_is_the_prompts() -> None:
    model = GatedModel()
    (app, proxy), _, _ = _build(100, model)
    body = {**BODY, "max_tokens": 20}  # 100 / 20: five calls fit
    async with _client(proxy) as client, app.router.lifespan_context(app):
        calls = [
            asyncio.ensure_future(client.post("/v1/chat/completions", json=body)) for _ in range(8)
        ]
        try:
            # All eight are in the app at once: five wait at the model, three are refused.
            await _until(lambda: sum(c.done() for c in calls) == 3 and len(model.max_tokens) == 5)
            cap: UncorrelatedCap = app.state.uncorrelated_cap
            assert (cap.spent, cap.reserved) == (0, 100)
        finally:
            model.gate.set()
        results = await asyncio.gather(*calls)
        statuses = sorted(r.status_code for r in results)
        assert statuses == [200] * 5 + [429] * 3
        assert all(r.json()["error"]["retryable"] is True for r in results if r.status_code == 429)
        assert model.max_tokens == [20] * 5
        # The bound: the window is charged at most the limit plus the admitted calls' prompts.
        assert cap.reserved == 0
        assert cap.spent == 100 + 5 * GatedModel.PROMPT
        assert (await client.post("/v1/chat/completions", json=body)).status_code == 429


async def test_a_call_larger_than_the_cap_alone_is_refused_and_never_reaches_the_model() -> None:
    model = GatedModel()
    model.gate.set()
    (app, proxy), _, _ = _build(100, model)
    async with _client(proxy) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json={**BODY, "max_tokens": 101})
        assert res.status_code == 429
        error = res.json()["error"]
        assert error["code"] == "budget_exhausted" and error["retryable"] is False
        assert model.max_tokens == []
        # No max_tokens: the default ceiling, never more than the cap, is reserved and forwarded.
        unset = {k: v for k, v in BODY.items() if k != "max_tokens"}
        assert (await client.post("/v1/chat/completions", json=unset)).status_code == 200
    assert model.max_tokens == [min(DEFAULT_UNCORRELATED_MAX_TOKENS, 100)]


async def _stream_and_leave(proxy: Any, body: dict[str, Any]) -> list[Any]:
    """POST a stream over raw ASGI, read up to the first content delta, then disconnect."""
    raw = json.dumps(body).encode()
    gone = asyncio.Event()
    sent: list[Any] = []
    first = True

    async def receive() -> dict[str, Any]:
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": raw, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)
        if b'"content": "first"' in message.get("body", b""):
            gone.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw)).encode()),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("chassis", 80),
        "app": proxy,
    }
    await asyncio.wait_for(proxy(scope, receive, send), 5)
    return sent


async def test_a_stream_closed_before_its_usage_frame_is_charged_its_reservation() -> None:
    model = GatedModel()
    (app, proxy), _, _ = _build(100, model)
    async with app.router.lifespan_context(app):
        try:
            sent = await _stream_and_leave(proxy, {**BODY, "max_tokens": 30, "stream": True})
            assert sent[0]["status"] == 200 and len(model.max_tokens) == 1
            cap: UncorrelatedCap = app.state.uncorrelated_cap
            assert (cap.spent, cap.reserved) == (30, 0)
        finally:
            model.gate.set()  # the abandoned stream's late end changes nothing
        await asyncio.sleep(0)
        assert (cap.spent, cap.reserved) == (30, 0)


async def test_an_upstream_error_with_no_usage_is_charged_its_reservation() -> None:
    model = GatedModel(fail=True)
    model.gate.set()
    (app, proxy), _, _ = _build(100, model)
    async with _client(proxy) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json={**BODY, "max_tokens": 40})
        assert res.status_code == 502
        assert app.state.uncorrelated_cap.spent == 40


async def test_a_reload_mid_window_applies_to_the_next_admission_not_to_calls_in_flight() -> None:
    model = GatedModel()
    (app, proxy), _, _ = _build(100, model)
    body = {**BODY, "max_tokens": 60}
    async with _client(proxy) as client, app.router.lifespan_context(app):
        held = asyncio.ensure_future(client.post("/v1/chat/completions", json=body))
        small = {**BODY, "max_tokens": 10}
        try:
            await _until(lambda: len(model.max_tokens) == 1)
            app.state.config = _config(50)  # 60 held already: nothing more fits
            # Bounded: a call wrongly admitted here would wait at the closed gate.
            refused = await asyncio.wait_for(client.post("/v1/chat/completions", json=small), 5)
            assert refused.status_code == 429 and refused.json()["error"]["retryable"] is True
        finally:
            model.gate.set()
        app.state.config = _config(200)
        assert (await client.post("/v1/chat/completions", json=small)).status_code == 200
        assert (await held).status_code == 200
        app.state.config = _config(0)
        assert (await client.post("/v1/chat/completions", json=small)).status_code == 429
    assert model.max_tokens == [60, 10]
