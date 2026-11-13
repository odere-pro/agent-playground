"""One budget across the evaluator gate's attempts (017 H-4): one `timeout_ms` and one
`max_tokens` for every attempt and the fallback, the judge's tokens included, and a scripted model
error that the gate never retries (ADR-013: the router retries a model call, not the chassis).

Offline: `FakeEngine` handles, a `ScriptedEvaluator` that reports judge tokens, and a
`ScriptedModel` behind the model proxy for the scripted model error.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
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
from chassis.ports.evaluator import EvalCase, EvaluatorPort, Score
from chassis.server import ChassisConfig, create_app
from chassis.server.evaluator_gate import (
    GATE_BUDGET,
    GATE_FALLBACKS,
    GATE_RETRIES,
    GATE_STOPPED,
    GATE_TIMEOUT,
    Allowance,
)
from chassis.server.proxy_app import create_proxy_app

BAD = "The report was signed."
INPUT = "simplify: The report was signed by Dana Ruiz on 4 May 2025."
FALLBACK = "fallback-route"
TRACE = "b" * 32
ATTEMPT_TOKENS = (40, 10)  # each attempt's metrics: 50 tokens
JUDGE_TOKENS = (30, 20)  # each scoring: 50 tokens


class BudgetEngine(FakeEngine):
    """Answers BAD on every route; records each attempt's budget and what its run record had
    spent when the attempt began. Sleeps `delay_s` per attempt."""

    def __init__(self, delay_s: float = 0.0) -> None:
        super().__init__(handle=self._handle_)
        self.delay_s = delay_s
        self.budgets: list[Budget] = []
        self.spent: list[int | None] = []
        self.app: Any = None

    async def _handle_(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        self.budgets.append(ctx.budget)
        record = self.app.state.runs.lookup(trace_id_hex(ctx.trace_id)) if self.app else None
        self.spent.append(None if record is None else record.spent_tokens)
        yield Start(request_id=ctx.request_id)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        yield Delta(text=BAD)
        yield Metrics(
            input_tokens=ATTEMPT_TOKENS[0],
            output_tokens=ATTEMPT_TOKENS[1],
            model_route=ctx.model_route,
        )
        yield End()


class CostlyJudge(ScriptedEvaluator):
    """Scores BAD low and reports `JUDGE_TOKENS` per scoring, after `delay_s`."""

    def __init__(self, delay_s: float = 0.0) -> None:
        super().__init__([ScoreRule(match=BAD, score=0.2)], default=0.9)
        self.delay_s = delay_s

    async def score(self, case: EvalCase) -> Score:
        scored = await super().score(case)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return Score(
            value=scored.value,
            evaluator=self.name,
            input_tokens=JUDGE_TOKENS[0],
            output_tokens=JUDGE_TOKENS[1],
        )


def config(**harness: Any) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-gate-budget",
            "profile": "fake",
            "agent": {"name": "simplifier", "version": "0.0.1"},
            "spec": {
                "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
                "model": {"route": "big-default", "fallback_route": FALLBACK},
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
        state=InMemoryState(),
        evaluator=ev,
    )


def client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


def telemetry(bundle: PortBundle) -> InMemoryTelemetry:
    assert isinstance(bundle.telemetry, InMemoryTelemetry)
    return bundle.telemetry


async def run(engine: BudgetEngine, bundle: PortBundle, budget: dict[str, int]) -> Any:
    app = create_app(config(), bundle)
    engine.app = app
    body = {"input": {"text": INPUT}, "trace_id": TRACE, "budget": budget}
    async with client(app) as c, app.router.lifespan_context(app):
        res = await c.post("/v1/run", json=body)
    return res.json()


async def test_each_attempt_gets_the_tokens_left_and_the_judge_tokens_count() -> None:
    engine, judge = BudgetEngine(), CostlyJudge()
    bundle = ports(engine, judge)
    body = await run(engine, bundle, {"max_tokens": 1000, "timeout_ms": 30_000})
    assert body["status"] == "fallback", body
    # Attempt 1: the whole budget. Each later attempt: less one attempt (50) and one judge (50).
    assert [b.max_tokens for b in engine.budgets] == [1000, 900, 800]
    # The judge's tokens are charged to the run's record, so the model proxy sees them too.
    assert engine.spent == [0, 50, 100]
    t = telemetry(bundle)
    assert t.counter_value(GATE_RETRIES) == 1 and t.counter_value(GATE_FALLBACKS) == 1


async def test_each_attempt_gets_the_time_left() -> None:
    engine = BudgetEngine(delay_s=0.1)
    bundle = ports(engine, CostlyJudge())
    body = await run(engine, bundle, {"max_tokens": 1000, "timeout_ms": 5_000})
    assert body["status"] == "fallback", body
    left = [b.timeout_ms for b in engine.budgets]
    assert left[0] <= 5_000
    # Each attempt slept 100 ms, so each later attempt has about that much less. The margin
    # (90 ms) allows for whole-ms truncation and a sleep that returns early; without the shared
    # budget every attempt would get the same 5000 ms.
    assert left[1] <= left[0] - 90 and left[2] <= left[1] - 90, left
    assert left[2] > 0


async def test_no_tokens_left_stops_before_the_retry_with_budget_exhausted() -> None:
    engine = BudgetEngine()
    bundle = ports(engine, CostlyJudge())
    body = await run(engine, bundle, {"max_tokens": 100, "timeout_ms": 30_000})
    assert body["status"] == "error", body
    error = body["output"]["error"]
    assert error["code"] == GATE_BUDGET
    assert BAD not in body["output"].get("text", "")  # the failed answer never leaves
    assert len(engine.budgets) == 1
    t = telemetry(bundle)
    assert t.counter_value(GATE_STOPPED, reason="budget") == 1
    assert t.counter_value(GATE_RETRIES) == 0 and t.counter_value(GATE_FALLBACKS) == 0
    # The attempt's tokens and the judge's are both in the response.
    assert body["metrics"]["input_tokens"] == 70 and body["metrics"]["output_tokens"] == 30


async def test_no_tokens_left_stops_before_the_fallback() -> None:
    engine = BudgetEngine()
    bundle = ports(engine, CostlyJudge())
    body = await run(engine, bundle, {"max_tokens": 200, "timeout_ms": 30_000})
    assert body["output"]["error"]["code"] == GATE_BUDGET, body
    assert [b.max_tokens for b in engine.budgets] == [200, 100]  # the retry ran, not the fallback
    t = telemetry(bundle)
    assert t.counter_value(GATE_RETRIES) == 1 and t.counter_value(GATE_FALLBACKS) == 0
    assert t.counter_value(GATE_STOPPED, reason="budget") == 1


async def test_no_time_left_stops_before_the_retry_with_a2a_timeout() -> None:
    engine = BudgetEngine(delay_s=0.05)
    bundle = ports(engine, CostlyJudge(delay_s=0.2))
    body = await run(engine, bundle, {"max_tokens": 1000, "timeout_ms": 200})
    assert body["status"] == "error", body
    error = body["output"]["error"]
    assert error["code"] == GATE_TIMEOUT
    assert len(engine.budgets) == 1
    assert telemetry(bundle).counter_value(GATE_STOPPED, reason="timeout") == 1


def test_an_unregistered_run_counts_its_own_time_and_tokens() -> None:
    allowance = Allowance(Budget(max_tokens=100, timeout_ms=60_000), None)
    allowance.spend(60)
    allowance.charge_judge(Score(value=0.1, evaluator="fake", input_tokens=30, output_tokens=5))
    assert allowance.left_tokens() == 5 and allowance.spent_reason() is None
    allowance.spend(5)
    assert allowance.spent_reason() == "budget"
    assert 0 < allowance.left_ms() <= 60_000
    assert Allowance(Budget(timeout_ms=0), None).spent_reason() == "timeout"


class ProxyErrorEngine(FakeEngine):
    """Calls the model proxy once per attempt; a refused call ends the attempt with the error a
    Python workload reports (`http_<status>`)."""

    def __init__(self) -> None:
        super().__init__(handle=self._handle_)
        self.proxy: Any = None
        self.attempts = 0

    async def _handle_(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        self.attempts += 1
        yield Start(request_id=ctx.request_id)
        headers = {"traceparent": f"00-{trace_id_hex(ctx.trace_id)}-00f067aa0ba902b7-01"}
        body = {"model": "big-default", "messages": [{"role": "user", "content": "simplify: x"}]}
        async with client(self.proxy) as c:
            res = await c.post("/v1/chat/completions", json=body, headers=headers)
        if res.status_code != 200:
            yield Error(code=f"http_{res.status_code}", message="model call failed", retryable=True)
            return
        yield Delta(text=BAD)
        yield End()


async def test_a_scripted_model_error_is_not_retried_by_the_gate() -> None:
    model = ScriptedModel([ScriptRule(match="simplify", error="upstream_error", retryable=True)])
    engine, judge = ProxyErrorEngine(), CostlyJudge()
    bundle = ports(engine, judge, model)
    app = create_app(config(retries=2), bundle)
    engine.proxy = create_proxy_app(app)
    async with client(app) as c, app.router.lifespan_context(app):
        res = await c.post("/v1/run", json={"input": {"text": INPUT}, "trace_id": TRACE})
    body = res.json()
    assert body["status"] == "error" and body["output"]["error"]["code"].startswith("http_")
    # One attempt, one upstream call: the router owns the model call's retries (ADR-013).
    assert engine.attempts == 1 and len(model.calls) == 1
    assert judge.cases == []  # an errored attempt is not scored
    t = telemetry(bundle)
    assert t.counter_value(GATE_RETRIES) == 0 and t.counter_value(GATE_FALLBACKS) == 0
