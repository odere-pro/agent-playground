"""042 A-2: the gate scores only with the adapter `spec.adapters.evaluator` names, and the
judge's tokens and cost count in `chassis_model_tokens_total` and
`chassis_model_cost_microusd_total` under the judge's route."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from chassis.adapters.evaluators import EncoderEvaluator, JudgeEvaluator
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Event, Start
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.evaluator import ScriptedEvaluator
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.ports.evaluator import EvalCase, Score
from chassis.profiles import AdapterSpec, build_ports
from chassis.server import ChassisConfig, create_app
from chassis.server.evaluator_gate import GatedEngine
from poc_harness.promtext import value

ANSWER = "Dana Ruiz signed the report on 4 May 2025."
JUDGE_ROUTE = "judge-small"


class AnswerEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__(handle=self._answer)

    async def _answer(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        yield Delta(text=ANSWER)
        yield End()


class TokenJudge:
    """A judge double on `JUDGE_ROUTE` that reports the ADR-010 item 9 token counts."""

    name = "judge"
    route = JUDGE_ROUTE

    def __init__(self) -> None:
        self.cases: list[EvalCase] = []

    async def score(self, case: EvalCase) -> Score:
        self.cases.append(case)
        return Score(value=0.9, evaluator=self.name, input_tokens=254, output_tokens=22)

    async def aclose(self) -> None:
        return None


def config(**spec: Any) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-a2",
            "profile": "fake",
            "agent": {"name": "simplifier", "version": "0.0.1"},
            "spec": {
                "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
                "model": {"route": "big-default", "fallback_route": "big-fallback"},
                "harness": {"evaluator_gate": True, "retries": 1, "threshold": 0.7},
                **spec,
            },
        }
    )


def client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


@pytest.mark.parametrize(
    ("name", "kind"),
    [("fake", ScriptedEvaluator), ("judge", JudgeEvaluator), ("encoder", EncoderEvaluator)],
)
async def test_the_gate_holds_only_the_evaluator_spec_adapters_evaluator_names(
    monkeypatch: pytest.MonkeyPatch, name: str, kind: type
) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm.invalid/v1")
    monkeypatch.setenv("CHASSIS_ENCODER_URL", "http://encoder.invalid")
    bundle = build_ports("fake", AdapterSpec(evaluator=name), connector="inprocess")
    assert isinstance(bundle.evaluator, kind)
    app = create_app(config(), bundle)
    async with app.router.lifespan_context(app):
        gate = app.state.ports.engine
        assert isinstance(gate, GatedEngine)
        assert gate.evaluator is bundle.evaluator, "the gate scores with the built adapter only"


async def test_a_gated_run_scores_once_on_the_configured_evaluator() -> None:
    built = build_ports("fake", AdapterSpec(evaluator="fake"), connector="inprocess")
    assert isinstance(built.evaluator, ScriptedEvaluator)
    app = create_app(config(), built)
    async with client(app) as c, app.router.lifespan_context(app):
        res = await c.post("/v1/run", json={"input": {"text": "simplify: x"}})
    assert res.status_code == 200, res.text
    assert len(built.evaluator.cases) == 1


PRICES = {JUDGE_ROUTE: {"input_per_mtok": 0.15, "output_per_mtok": 0.60}}


def judge_bundle(judge: TokenJudge) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=AnswerEngine(),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
        evaluator=judge,
    )


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_the_judges_tokens_and_cost_count_under_the_judge_route(mode: str) -> None:
    judge = TokenJudge()
    harness = {"evaluator_gate": True, "retries": 1, "threshold": 0.7, "evaluator_mode": mode}
    app = create_app(config(harness=harness, observability={"prices": PRICES}), judge_bundle(judge))
    async with client(app) as c, app.router.lifespan_context(app):
        res = await c.post("/v1/run", json={"input": {"text": "simplify: x"}})
        gate = app.state.ports.engine
        assert isinstance(gate, GatedEngine)
        assert await gate.wait(5.0) == 0, "the async score finished"
        text = (await c.get("/metrics")).text
    assert res.json()["status"] == "ok" and len(judge.cases) == 1
    tokens = "chassis_model_tokens_total"
    assert value(text, tokens, route=JUDGE_ROUTE, direction="input") == 254
    assert value(text, tokens, route=JUDGE_ROUTE, direction="output") == 22
    # 254 * 0.15 + 22 * 0.60 = 51.3 micro-USD, rounded
    assert value(text, "chassis_model_cost_microusd_total", route=JUDGE_ROUTE) == 51


class ReloadedJudge(TokenJudge):
    """A judge whose route changes mid-call, as a reload of `spec.model.judge_route` would."""

    async def score(self, case: EvalCase) -> Score:
        self.route = "judge-reloaded"
        return await super().score(case)


async def test_the_judges_tokens_count_on_the_route_read_before_the_call() -> None:
    judge = ReloadedJudge()
    app = create_app(config(observability={"prices": PRICES}), judge_bundle(judge))
    async with client(app) as c, app.router.lifespan_context(app):
        await c.post("/v1/run", json={"input": {"text": "simplify: x"}})
        text = (await c.get("/metrics")).text
    tokens = "chassis_model_tokens_total"
    assert value(text, tokens, route=JUDGE_ROUTE, direction="input") == 254
