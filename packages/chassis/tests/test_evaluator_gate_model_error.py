"""A scripted model error inside a gated run (017 H-4, ADR-017): the gate retries it up to
`spec.harness.retries` times, then falls back to `spec.model.fallback_route`, within the run's one
budget, and counts the retry and the fallback.

Offline. Through the chassis app and its model proxy (`ScriptedModel` fails on the main route),
and through the `sidecar` and `remote` connectors over a Unix socket (the template A2A server).
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from a2a_uds import SIDECAR_URL, ASGIApp, Receive, Scope, Send, serve_uds
from chassis.adapters.a2a import SidecarConnector
from chassis.adapters.a2a.remote import RemoteConnector
from chassis.adapters.a2a.server import build_agent_card, build_app
from chassis.core.envelope import Budget, Context, TaskInput
from chassis.core.events import Delta, End, Error, Event, Metrics, Start
from chassis.core.trace import trace_id_hex
from chassis.fakes import (
    FakeEngine,
    InMemoryConfig,
    InMemoryTelemetry,
    ScoreRule,
    ScriptedEvaluator,
    ScriptedModel,
    ScriptRule,
)
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.ports.model import ModelMessage, ModelResult, ToolSpec
from chassis.server import ChassisConfig, create_app
from chassis.server.evaluator_gate import (
    GATE_BUDGET,
    GATE_FALLBACKS,
    GATE_RETRIES,
    GATE_STOPPED,
    GATE_TIMEOUT,
    GatedEngine,
)
from chassis.server.proxy_app import create_proxy_app
from chassis_contracts.helpers import make_context, make_request

GOOD = "Dana Ruiz signed the report on 4 May 2025."
INPUT = "simplify: The report was signed by Dana Ruiz on 4 May 2025."
ROUTE = "big-default"
FALLBACK = "fallback-route"
TRACE = "c" * 32
MODEL_ERROR = "http_502"
"""What a Python workload reports for the proxy's failed upstream call: `http_<status>`."""
REMOTE_URL = "http://remote.test:8443"
TOKEN_ENV = "H4_TEST_REMOTE_TOKEN"


def config(**harness: Any) -> ChassisConfig:
    fallback = harness.pop("fallback_route", FALLBACK)
    return ChassisConfig.model_validate(
        {
            "version": "cfg-gate-model-error",
            "profile": "fake",
            "agent": {"name": "simplifier", "version": "0.0.1"},
            "spec": {
                "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
                "model": {"route": ROUTE, "fallback_route": fallback},
                "harness": {"evaluator_gate": True, "retries": 2, "threshold": 0.7, **harness},
            },
        }
    )


def judge() -> ScriptedEvaluator:
    return ScriptedEvaluator([ScoreRule(match=GOOD, score=0.9)], default=0.1)


class RouteModel(ScriptedModel):
    """Fails every call on the main route with a retryable `upstream_error` (the router already
    retried it); answers GOOD on the fallback route. Records each call's route."""

    def __init__(self) -> None:
        super().__init__([ScriptRule(match=None, error="upstream_error", retryable=True)])
        self.fallback = ScriptedModel([ScriptRule(match=None, reply=GOOD)])
        self.routes: list[str] = []

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
        self.routes.append(route)
        complete = self.fallback.complete if route == FALLBACK else super().complete
        return await complete(
            messages,
            route=route,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )


class ProxyEngine(FakeEngine):
    """A workload that makes one model call per attempt through the chassis model proxy, on the
    attempt's route, and reports a refused call as a Python workload does (`http_<status>`)."""

    def __init__(self) -> None:
        super().__init__(handle=self._handle_)
        self.proxy: Any = None
        self.attempts = 0

    async def _handle_(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        self.attempts += 1
        yield Start(request_id=ctx.request_id)
        headers = {"traceparent": f"00-{trace_id_hex(ctx.trace_id)}-00f067aa0ba902b7-01"}
        body = {"model": ctx.model_route, "messages": [{"role": "user", "content": input.text}]}
        transport = httpx.ASGITransport(app=self.proxy)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as c:
            res = await c.post("/v1/chat/completions", json=body, headers=headers)
        if res.status_code != 200:  # as echo-python: a 5xx is retryable, a 4xx is not
            retryable = res.status_code >= 500
            yield Error(
                code=f"http_{res.status_code}", message="model call failed", retryable=retryable
            )
            return
        reply = res.json()
        yield Delta(text=reply["choices"][0]["message"]["content"])
        usage = reply.get("usage") or {}
        yield Metrics(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            model_route=ctx.model_route,
        )
        yield End()


def ports(engine: FakeEngine, model: Any) -> PortBundle:
    return PortBundle(
        model=model,
        engine=engine,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
        evaluator=judge(),
    )


def counted(bundle: PortBundle) -> InMemoryTelemetry:
    assert isinstance(bundle.telemetry, InMemoryTelemetry)
    return bundle.telemetry


async def run_app(cfg: ChassisConfig) -> Any:
    model, engine = RouteModel(), ProxyEngine()
    bundle = ports(engine, model)
    app = create_app(cfg, bundle)
    engine.proxy = create_proxy_app(app)
    body = {"input": {"text": INPUT}, "trace_id": TRACE}
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as c,
        app.router.lifespan_context(app),
    ):
        res = await c.post("/v1/run", json=body)
    return res.json(), model, engine, counted(bundle)


async def test_a_scripted_model_error_is_retried_then_falls_back() -> None:
    body, model, engine, t = await run_app(config(retries=2))
    assert body["status"] == "fallback", body
    assert body["output"]["text"] == GOOD
    # retries + 1 attempts on the main route, each one upstream call, then the fallback route.
    assert model.routes == [ROUTE, ROUTE, ROUTE, FALLBACK]
    assert engine.attempts == 4
    assert body["metrics"]["attempts"] == 4
    assert body["metrics"]["model_route"] == FALLBACK
    assert t.counter_value(GATE_RETRIES) == 2 and t.counter_value(GATE_FALLBACKS) == 1


async def test_a_scripted_model_error_with_no_fallback_ends_in_the_model_error() -> None:
    body, model, _, t = await run_app(config(retries=1, fallback_route=None))
    assert body["status"] == "error", body
    assert body["output"]["error"]["code"] == MODEL_ERROR
    assert model.routes == [ROUTE, ROUTE]
    assert t.counter_value(GATE_RETRIES) == 1 and t.counter_value(GATE_FALLBACKS) == 0


class SlowError(FakeEngine):
    """Each attempt takes `delay_s`, reports `tokens` spent, then a retryable model error."""

    def __init__(self, delay_s: float = 0.0, tokens: int = 0) -> None:
        super().__init__(handle=self._handle_)
        self.delay_s, self.tokens, self.attempts = delay_s, tokens, 0

    async def _handle_(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        self.attempts += 1
        yield Start(request_id=ctx.request_id)
        await asyncio.sleep(self.delay_s)
        yield Metrics(input_tokens=self.tokens, output_tokens=0, model_route=ctx.model_route)
        yield Error(code=MODEL_ERROR, message="model call failed", retryable=True)


async def gated(engine: FakeEngine, budget: Budget) -> tuple[list[Event], InMemoryTelemetry]:
    telemetry, cfg = InMemoryTelemetry(), config(retries=2)
    gate = GatedEngine(engine, judge(), config=lambda: cfg, telemetry=telemetry)
    request = make_request(text=INPUT).model_copy(update={"budget": budget})
    events = [e async for e in gate.run(request, make_context(request, ROUTE))]
    return events, telemetry


async def test_a_model_error_is_not_retried_with_no_time_left() -> None:
    engine = SlowError(delay_s=0.15)
    events, t = await gated(engine, Budget(max_tokens=1000, timeout_ms=100))
    assert engine.attempts == 1
    error = events[-1]
    assert isinstance(error, Error) and error.code == GATE_TIMEOUT, events
    assert t.counter_value(GATE_STOPPED, reason="timeout") == 1
    assert t.counter_value(GATE_RETRIES) == 0 and t.counter_value(GATE_FALLBACKS) == 0


async def test_a_model_error_is_not_retried_with_no_tokens_left() -> None:
    engine = SlowError(tokens=100)
    events, t = await gated(engine, Budget(max_tokens=100, timeout_ms=30_000))
    assert engine.attempts == 1
    error = events[-1]
    assert isinstance(error, Error) and error.code == GATE_BUDGET, events
    assert t.counter_value(GATE_STOPPED, reason="budget") == 1
    assert t.counter_value(GATE_RETRIES) == 0


@pytest.mark.parametrize("code", ["a2a.transport", "a2a.timeout", "a2a.rpc_error"])
async def test_a_retryable_lane_error_is_not_rerun(code: str) -> None:
    """After `a2a.transport` the workload's task may still run; a rerun would repeat its tools."""
    attempts = 0

    async def cut(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        nonlocal attempts
        attempts += 1
        yield Start(request_id=ctx.request_id)
        yield Error(code=code, message="lane failed", retryable=True)

    events, t = await gated(FakeEngine(handle=cut), Budget(max_tokens=1000, timeout_ms=30_000))
    assert attempts == 1
    error = events[-1]
    assert isinstance(error, Error) and error.code == code and error.retryable, events
    assert t.counter_value(GATE_RETRIES) == 0 and t.counter_value(GATE_FALLBACKS) == 0


async def test_a_model_error_that_is_not_retryable_is_sent_as_it_is() -> None:
    async def refused(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        yield Error(code="model_route_denied", message="refused", retryable=False)

    engine = FakeEngine(handle=refused)
    events, t = await gated(engine, Budget(max_tokens=1000, timeout_ms=30_000))
    error = events[-1]
    assert isinstance(error, Error) and error.code == "model_route_denied", events
    assert t.counter_value(GATE_RETRIES) == 0 and t.counter_value(GATE_FALLBACKS) == 0


# --- the same gate over the sidecar and remote lanes ---


async def failing_on_main(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Event]:
    """The wire handle: a scripted model error on the main route, GOOD on the fallback."""
    yield Start(request_id=ctx["request_id"])
    if ctx.get("model_route") != FALLBACK:
        yield Error(code=MODEL_ERROR, message="model call failed", retryable=True)
        return
    yield Delta(text=GOOD)
    yield Metrics(input_tokens=10, output_tokens=5, model_route=FALLBACK)
    yield End()


@dataclass
class RequireBearer:
    app: ASGIApp
    expected: str

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            if headers.get("authorization") != f"Bearer {self.expected}":
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    value = secrets.token_hex(32)
    monkeypatch.setenv(TOKEN_ENV, value)
    yield value


def lane_bundle(engine: Any) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=engine,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
    )


@pytest.mark.parametrize("lane", ["sidecar", "remote"])
async def test_a_scripted_model_error_is_retried_in_both_lanes(lane: str, token: str) -> None:
    url = SIDECAR_URL if lane == "sidecar" else REMOTE_URL
    app: ASGIApp = build_app(failing_on_main, build_agent_card(name="h4", version="1", url=url))
    if lane == "remote":
        app = RequireBearer(app, token)
    telemetry = InMemoryTelemetry()
    cfg = config(retries=1)
    async with serve_uds(app) as path:
        connector: Any
        if lane == "sidecar":
            connector = SidecarConnector()
            spec: dict[str, Any] = {"connector": "sidecar", "url": url, "uds": path}
        else:
            connector = RemoteConnector()
            spec = {
                "connector": "remote",
                "url": url,
                "uds": path,
                "auth": {"scheme": "bearer", "token_env": TOKEN_ENV, "previous_token_env": None},
                "probe_timeout_s": 2.0,
            }
        await connector.setup(spec, lane_bundle(connector))
        gate = GatedEngine(connector, judge(), config=lambda: cfg, telemetry=telemetry)
        request = make_request(text=INPUT)
        try:
            events = [e async for e in gate.run(request, make_context(request, ROUTE))]
        finally:
            await gate.close()
    end = events[-1]
    assert isinstance(end, End) and end.status == "fallback", events
    assert "".join(e.text for e in events if isinstance(e, Delta)) == GOOD
    assert not any(isinstance(e, Error) for e in events)  # the failed attempts never leave
    assert [e.attempt for e in events if isinstance(e, Metrics)][-1] == 3
    assert telemetry.counter_value(GATE_RETRIES) == 1
    assert telemetry.counter_value(GATE_FALLBACKS) == 1
    assert telemetry.counter_value(GATE_STOPPED, reason="budget") == 0
