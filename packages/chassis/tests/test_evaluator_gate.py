"""The evaluator gate (PoC-7, 017 H-4): `GatedEngine` around any `EngineConnector`, the lifespan
hook, and the fallback route the model proxy enforces for the run.

The engine here is `FakeEngine` with a handle that answers by `ctx.model_route`: a bad answer on
the default route, a good one on the fallback route. The evaluator is `ScriptedEvaluator`. The
lanes (sidecar over a Unix socket, two engines) are in `pocs/poc-07-cross-cutting-decisions/tests`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import pytest
from chassis.core.envelope import Context, Request, TaskInput
from chassis.core.events import Delta, End, Error, Event, Metrics, Start
from chassis.fakes import (
    FakeEngine,
    InMemoryConfig,
    InMemoryTelemetry,
    ScoreRule,
    ScriptedEvaluator,
    ScriptedModel,
    ScriptRule,
)
from chassis.ports.bundle import PortBundle
from chassis.ports.evaluator import EvalCase, EvaluatorError, EvaluatorPort, NoEvaluator, Score
from chassis.ports.model import ModelChunk, ModelMessage, ModelResult, ToolSpec
from chassis.server import ChassisConfig, create_app
from chassis.server.evaluator_gate import (
    EVALUATOR_CALLS,
    EVALUATOR_DROPPED,
    GATE_FAILED_OPEN,
    GATE_FALLBACKS,
    GATE_RETRIES,
    GatedEngine,
    GateNotConfigured,
    sampled,
)
from chassis.server.proxy_app import create_proxy_app

BAD = "The report was signed."
GOOD = "Dana Ruiz signed the report on 4 May 2025."
FALLBACK = "fallback-route"
INPUT = "simplify: The report was signed by Dana Ruiz on 4 May 2025."


class RouteEngine(FakeEngine):
    """Answers BAD on any route but the fallback, GOOD on it; records each run's route."""

    def __init__(self, answers: Sequence[str] | None = None, error_first: bool = False) -> None:
        super().__init__(handle=self._handle_)
        self.routes: list[str | None] = []
        self.answers = list(answers or [])
        self.error_first = error_first

    async def _handle_(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        self.routes.append(ctx.model_route)
        yield Start(request_id=ctx.request_id)
        if self.error_first and len(self.routes) == 1:
            yield Error(code="tool_error", message="scripted", retryable=False)
            return
        if self.answers:
            text = self.answers[min(len(self.routes), len(self.answers)) - 1]
        else:
            text = GOOD if ctx.model_route == FALLBACK else BAD
        yield Delta(text=text)
        yield Metrics(input_tokens=40, output_tokens=10, model_route=ctx.model_route)
        yield End()


def evaluator() -> ScriptedEvaluator:
    return ScriptedEvaluator([ScoreRule(match=BAD, score=0.2)], default=0.9)


def config(**harness: Any) -> ChassisConfig:
    fallback = harness.pop("fallback_route", FALLBACK)
    return ChassisConfig.model_validate(
        {
            "version": "cfg-gate",
            "profile": "fake",
            "agent": {"name": "simplifier", "version": "0.0.1"},
            "spec": {
                "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
                "model": {"route": "big-default", "fallback_route": fallback},
                "harness": {"evaluator_gate": True, "retries": 1, "threshold": 0.7, **harness},
            },
        }
    )


def ports(engine: FakeEngine, ev: EvaluatorPort, model: Any = None) -> PortBundle:
    return PortBundle(
        model=model or ScriptedModel(),
        engine=engine,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        evaluator=ev,
    )


def client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


async def run(cfg: ChassisConfig, bundle: PortBundle, **body: Any) -> tuple[Any, Any]:
    app = create_app(cfg, bundle)
    async with client(app) as c, app.router.lifespan_context(app):
        res = await c.post("/v1/run", json={"input": {"text": INPUT}, **body})
        gate = app.state.ports.engine
        if isinstance(gate, GatedEngine):
            await gate.wait(5)
    return res, app


def telemetry(bundle: PortBundle) -> InMemoryTelemetry:
    assert isinstance(bundle.telemetry, InMemoryTelemetry)
    return bundle.telemetry


async def test_a_failing_answer_retries_once_then_falls_back() -> None:
    engine, ev = RouteEngine(), evaluator()
    bundle = ports(engine, ev)
    res, _ = await run(config(), bundle)
    body = res.json()
    assert body["status"] == "fallback", body
    assert body["output"]["text"] == GOOD  # the failed attempts' events never left
    assert engine.routes == ["big-default", "big-default", FALLBACK]
    assert [c.output for c in ev.cases] == [BAD, BAD]  # the fallback answer is not scored
    assert ev.cases[0].input == INPUT
    t = telemetry(bundle)
    assert t.counter_value(GATE_RETRIES) == 1
    assert t.counter_value(GATE_FALLBACKS) == 1
    assert t.counter_value(EVALUATOR_CALLS, evaluator="fake", mode="sync", outcome="fail") == 2
    # One metrics per attempt (3 x 50 tokens), the judge's own (0 tokens, scripted), the last
    # route is the fallback's.
    assert body["metrics"]["attempts"] == 3
    assert body["metrics"]["input_tokens"] == 120 and body["metrics"]["output_tokens"] == 30
    assert body["metrics"]["model_route"] == FALLBACK


async def test_a_passing_answer_goes_out_once_with_the_judge_metrics() -> None:
    engine = RouteEngine(answers=[GOOD])
    ev = evaluator()
    bundle = ports(engine, ev)
    res, _ = await run(config(), bundle)
    body = res.json()
    assert body["status"] == "ok" and body["output"]["text"] == GOOD
    assert engine.routes == ["big-default"]
    assert body["metrics"]["attempts"] == 1 and body["metrics"]["model_route"] == "big-default"
    assert telemetry(bundle).counter_value(GATE_RETRIES) == 0


async def test_a_retry_that_passes_is_ok_not_fallback() -> None:
    engine = RouteEngine(answers=[BAD, GOOD])
    bundle = ports(engine, evaluator())
    res, _ = await run(config(), bundle)
    body = res.json()
    assert body["status"] == "ok" and body["output"]["text"] == GOOD
    assert body["metrics"]["attempts"] == 2
    assert engine.routes == ["big-default", "big-default"]


async def test_no_fallback_route_ends_in_evaluator_gate_failed() -> None:
    engine = RouteEngine()
    res, _ = await run(config(fallback_route=None), ports(engine, evaluator()))
    body = res.json()
    assert body["status"] == "error"
    assert body["output"]["error"]["code"] == "evaluator_gate_failed"
    assert BAD not in body["output"].get("text", "")
    assert engine.routes == ["big-default", "big-default"]


async def test_an_evaluator_outage_fails_open() -> None:
    engine, ev = RouteEngine(), evaluator()
    ev.unavailable = True
    bundle = ports(engine, ev)
    res, _ = await run(config(), bundle)
    body = res.json()
    assert body["status"] == "ok" and body["output"]["text"] == BAD
    assert engine.routes == ["big-default"]
    assert telemetry(bundle).counter_value(GATE_FAILED_OPEN) == 1


class FailsOnRetry(ScriptedEvaluator):
    """Scores the first answer, then is unavailable."""

    async def score(self, case: EvalCase) -> Score:
        if self.cases:
            self.cases.append(case)
            raise EvaluatorError("evaluator_unavailable", "scripted outage", retryable=True)
        return await super().score(case)


class StalledEvaluator(ScriptedEvaluator):
    """Never answers until `release` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def score(self, case: EvalCase) -> Score:
        await self.release.wait()
        return await super().score(case)


async def test_gate_sends_the_retry_answer_and_fails_open_when_the_evaluator_fails_on_it() -> None:
    """An evaluator outage on the retry sends the retry's answer as it is."""
    engine = RouteEngine(answers=[BAD, GOOD])
    ev = FailsOnRetry([ScoreRule(match=BAD, score=0.2)], default=0.9)
    bundle = ports(engine, ev)

    res, _ = await run(config(), bundle)

    body = res.json()
    assert body["status"] == "ok" and body["output"]["text"] == GOOD
    assert engine.routes == ["big-default", "big-default"]
    assert telemetry(bundle).counter_value(GATE_FAILED_OPEN) == 1
    assert telemetry(bundle).counter_value(GATE_FALLBACKS) == 0


@pytest.mark.parametrize(
    ("harness", "body"),
    [({"evaluator_mode": "async"}, {}), ({}, {"stream": True})],
    ids=["async", "stream"],
)
async def test_gate_records_an_error_outcome_and_keeps_the_answer_when_async_scoring_fails(
    harness: dict[str, Any], body: dict[str, Any]
) -> None:
    """A background scoring that fails is counted as `error`; the answer already went out."""
    engine, ev = RouteEngine(), evaluator()
    ev.unavailable = True
    bundle = ports(engine, ev)

    res, _ = await run(config(**harness), bundle, **body)

    assert res.status_code == 200 and BAD in res.text
    t = telemetry(bundle)
    assert t.counter_value(EVALUATOR_CALLS, evaluator="fake", mode="async", outcome="error") == 1
    assert t.counter_value(GATE_FAILED_OPEN) == 0


async def test_gate_drops_a_background_score_when_the_pending_cap_is_full(
    request_: Request, context: Context
) -> None:
    """With `max_pending=1` and one score stalled, the next score is dropped and counted."""
    ev = StalledEvaluator()
    t = InMemoryTelemetry()
    cfg = config(evaluator_mode="async")
    gate = GatedEngine(RouteEngine(), ev, config=lambda: cfg, telemetry=t, max_pending=1)

    first = [e async for e in gate.run(request_, context)]
    await asyncio.sleep(0)
    second = [e async for e in gate.run(request_, context)]

    assert t.counter_value(EVALUATOR_DROPPED) == 1
    assert gate.pending == 1 and len(ev.cases) == 0
    assert first == second
    ev.release.set()
    assert await gate.wait(5) == 0


async def test_an_engine_error_is_sent_as_is_and_not_scored() -> None:
    engine, ev = RouteEngine(error_first=True), evaluator()
    res, _ = await run(config(), ports(engine, ev))
    body = res.json()
    assert body["status"] == "error" and body["output"]["error"]["code"] == "tool_error"
    assert ev.cases == [] and engine.routes == ["big-default"]


async def test_sample_rate_zero_makes_no_evaluator_call() -> None:
    engine, ev = RouteEngine(), evaluator()
    res, _ = await run(config(evaluator_sample_rate=0.0), ports(engine, ev))
    assert res.json()["output"]["text"] == BAD
    assert ev.cases == [] and engine.routes == ["big-default"]


async def test_gate_off_makes_no_evaluator_call_and_does_not_wrap() -> None:
    engine, ev = RouteEngine(), evaluator()
    res, app = await run(config(evaluator_gate=False), ports(engine, ev))
    assert res.json()["output"]["text"] == BAD and ev.cases == []
    assert app.state.ports.engine is engine


async def test_gate_on_with_no_evaluator_refuses_to_start() -> None:
    app = create_app(config(), ports(RouteEngine(), NoEvaluator()))
    with pytest.raises(GateNotConfigured):
        async with app.router.lifespan_context(app):
            pass


async def test_async_mode_never_changes_the_answer_and_records_the_score() -> None:
    engine, ev = RouteEngine(), evaluator()
    bundle = ports(engine, ev)
    res, _ = await run(config(evaluator_mode="async"), bundle)
    body = res.json()
    assert body["status"] == "ok" and body["output"]["text"] == BAD
    assert engine.routes == ["big-default"]
    assert [c.output for c in ev.cases] == [BAD]  # scored after the answer
    t = telemetry(bundle)
    assert t.counter_value(EVALUATOR_CALLS, evaluator="fake", mode="async", outcome="fail") == 1
    span = next(s for s in t.spans if s.name == "chassis.evaluator.score")
    assert span.attributes["evaluator.score"] == 0.2
    assert span.attributes["evaluator.passed"] is False
    assert span.attributes["request_id"] == body["request_id"]


async def test_a_stream_is_always_async() -> None:
    engine, ev = RouteEngine(), evaluator()
    bundle = ports(engine, ev)
    res, _ = await run(config(), bundle, stream=True)
    assert res.status_code == 200
    assert BAD in res.text and "fallback" not in res.text
    assert engine.routes == ["big-default"]
    assert [c.output for c in ev.cases] == [BAD]
    assert (
        telemetry(bundle).counter_value(
            EVALUATOR_CALLS, evaluator="fake", mode="async", outcome="fail"
        )
        == 1
    )


def test_sampling_is_deterministic_per_request_id() -> None:
    ids = [f"req-{i}" for i in range(2000)]
    first = [sampled(i, 0.25) for i in ids]
    assert first == [sampled(i, 0.25) for i in ids]
    assert 0.2 < sum(first) / len(ids) < 0.3
    assert not any(sampled(i, 0.0) for i in ids)
    assert all(sampled(i, 1.0) for i in ids)


# --- The fallback route through the model proxy -------------------------------------------------


class RouteModel(ScriptedModel):
    """A `ScriptedModel` that records the route of each call."""

    def __init__(self) -> None:
        super().__init__([ScriptRule(match="simplify", reply="ok")])
        self.routes: list[str] = []

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self.routes.append(route)
        return await super().complete(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        )

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        self.routes.append(route)
        async for chunk in super().stream(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        ):
            yield chunk


class ProxyCallingEngine(FakeEngine):
    """Calls the model proxy once per run with the route the workload would pick by mistake."""

    def __init__(self, proxy: Any) -> None:
        super().__init__(handle=self._handle_)
        self.proxy = proxy

    async def _handle_(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        trace = ctx.trace_id
        headers = {"traceparent": f"00-{trace}-00f067aa0ba902b7-01"}
        body = {"model": "big-default", "messages": [{"role": "user", "content": "simplify: x"}]}
        async with client(self.proxy) as c:
            await c.post("/v1/chat/completions", json=body, headers=headers)
            await c.post("/v1/messages", json={**body, "max_tokens": 16}, headers=headers)
        yield Delta(text=BAD)
        yield End()


async def test_the_proxy_sends_every_call_of_the_fallback_attempt_to_the_fallback_route() -> None:
    model = RouteModel()
    engine = ProxyCallingEngine(None)
    bundle = ports(engine, evaluator(), model)
    app = create_app(config(), bundle)
    engine.proxy = create_proxy_app(app)
    trace = "a" * 32
    async with client(app) as c, app.router.lifespan_context(app):
        res = await c.post("/v1/run", json={"input": {"text": INPUT}, "trace_id": trace})
    assert res.json()["status"] == "fallback"
    # Two attempts on the route the workload names, then both calls of the fallback attempt
    # forced to the fallback route, even though the workload still asked for big-default.
    assert model.routes == ["big-default"] * 4 + [FALLBACK] * 2
    t = telemetry(bundle)
    assert t.counter_value("chassis.model_route_forced", route=FALLBACK) == 2
