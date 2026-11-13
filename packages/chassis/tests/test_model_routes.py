"""The route map per task (`docs/specs/models.md`, "The route map per task"). `spec.model.routes`
picks the route for the task the caller names in `input.data.task`, else `spec.model.route`; the
pipeline puts it in `ctx.model_route` and on the `chassis.run` span; the model proxy refuses a
workload call that names another route with 403 `model_route_denied` (012 H-3), and the gate's
fallback route replaces the run's route for its attempt; the judge runs on
`spec.model.judge_route`. The last test is the demo: a `config/agent.yaml` with a `routes` map,
two tasks, two routes, through the fake model server. Offline.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from chassis.adapters.evaluators import JudgeEvaluator
from chassis.adapters.litellm import LiteLLMModel
from chassis.core.envelope import Context, Request, TaskInput
from chassis.core.events import Delta, End, Event, Start
from chassis.core.handle import echo
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
from chassis.schemas import SCHEMA_DIR
from chassis.server import ChassisConfig, create_app
from chassis.server.config import RELOADABLE, ModelSpec, load_config
from chassis.server.model_admission import ROUTE_REFUSED, ROUTE_SPAN
from chassis.server.pipeline import context_for, task_of
from chassis.server.proxy_app import create_proxy_app
from fake_model_server import Script
from fake_model_server import create_app as fake_models
from pydantic import ValidationError

BAD = "The report was signed."
TRACE = "c" * 32
CONFIG: dict[str, Any] = {
    "version": "cfg-routes",
    "profile": "fake",
    "agent": {"name": "simplifier", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {
            "route": "big-default",
            "routes": {"simplify": "small-default", "lookup": "lookup-default"},
        },
    },
}


def _config(model: dict[str, Any] | None = None, **blocks: Any) -> ChassisConfig:
    """`CONFIG` with `spec.model` fields updated and other `spec` blocks replaced."""
    data = json.loads(json.dumps(CONFIG))
    data["spec"]["model"].update(model or {})
    data["spec"].update(blocks)
    return ChassisConfig.model_validate(data)


def _request(task: Any = None) -> Request:
    data = {} if task is None else {"task": task}
    return Request(
        request_id="req-1",
        trace_id=TRACE,
        idempotency_key="idem-1",
        agent="simplifier",
        agent_version="0.0.1",
        input=TaskInput(text="simplify: x", data=data),
    )


def _body(text: str, task: str | None = None) -> dict[str, Any]:
    data = {} if task is None else {"task": task}
    return {"input": {"text": text, "data": data}}


class RouteModel(ScriptedModel):
    """A scripted model that records the route of every call."""

    def __init__(self, rules: Sequence[ScriptRule] | None = None) -> None:
        super().__init__(rules)
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
        return await super().complete(
            messages,
            route=route,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )


class ProxyWorkload(FakeEngine):
    """A workload that calls the model proxy once per run: with `ctx.model_route`, as a workload
    should, or with the route `asks` names, as a workload that picks its own would."""

    def __init__(self, asks: str | None = None) -> None:
        super().__init__(handle=self._handle_)
        self.asks = asks
        self.proxy: Any = None
        self.answers: list[tuple[int, Any]] = []
        """The model proxy's status and body, per call."""

    async def _handle_(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        headers = {"traceparent": f"00-{ctx.trace_id}-00f067aa0ba902b7-01"}
        body = {
            "model": self.asks or ctx.model_route,
            "messages": [{"role": "user", "content": input.text}],
        }
        async with _client(self.proxy) as c:
            res = await c.post("/v1/chat/completions", json=body, headers=headers)
        self.answers.append((res.status_code, res.json()))
        if res.status_code != 200:
            yield Delta(text=f"refused: {res.json()['error']['code']}")
            yield End()
            return
        yield Delta(text=res.json()["choices"][0]["message"]["content"])
        yield End()


def _ports(
    engine: FakeEngine | None = None, model: Any = None, evaluator: Any = None
) -> PortBundle:
    extra = {} if evaluator is None else {"evaluator": evaluator}
    return PortBundle(
        model=model or ScriptedModel(),
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
        **extra,
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


def _telemetry(app: Any) -> InMemoryTelemetry:
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    return telemetry


# --- 1. Config -----------------------------------------------------------------------------------


def test_model_spec_has_no_map_and_no_judge_route_by_default() -> None:
    spec = ModelSpec()
    assert (spec.routes, spec.judge_route) == ({}, None)


@pytest.mark.parametrize(
    ("task", "route"),
    [
        ("simplify", "small-default"),
        ("lookup", "big-default"),
        ("other", "default"),
        (None, "default"),
    ],
)
def test_model_spec_resolves_a_task_through_the_map_else_the_route(
    task: str | None, route: str
) -> None:
    spec = ModelSpec(
        route="default",
        routes={"simplify": "small-default", "lookup": "big-default"},
        judge_route="decision-small",
    )
    assert spec.route_for(task) == route


@pytest.mark.parametrize(
    "routes",
    [{"simplify": ""}, {"simplify": " "}, {"": "small-default"}, {"a task": "small-default"}],
    ids=["empty-route", "blank-route", "empty-task", "task-with-space"],
)
def test_model_spec_refuses_an_empty_task_or_route_name(routes: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match=r"spec\.model\.routes"):
        ModelSpec(routes=routes)


@pytest.mark.parametrize("judge_route", ["", " "])
def test_model_spec_refuses_an_empty_judge_route(judge_route: str) -> None:
    with pytest.raises(ValidationError, match=r"spec\.model\.judge_route"):
        ModelSpec(judge_route=judge_route)


def test_the_map_and_the_judge_route_are_reloadable() -> None:
    assert {"spec.model.route", "spec.model.routes", "spec.model.judge_route"} <= set(RELOADABLE)


def test_the_published_config_schema_has_the_map_and_the_judge_route() -> None:
    schema = json.loads((SCHEMA_DIR / "chassis-config.v0.json").read_text())
    properties = schema["$defs"]["ModelSpec"]["properties"]
    assert set(properties) == {"route", "fallback_route", "routes", "judge_route"}
    assert properties["routes"]["additionalProperties"] == {"type": "string"}


# --- 2. Resolution --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("task", "expected"),
    [("simplify", "simplify"), ("", None), ("  ", None), (5, None), (None, None), ({"a": 1}, None)],
)
def test_task_of_reads_a_non_empty_string_under_input_data_task(task: Any, expected: Any) -> None:
    assert task_of(_request(task)) == expected


@pytest.mark.parametrize(
    ("task", "route"),
    [
        ("simplify", "small-default"),
        ("lookup", "lookup-default"),
        ("unknown", "big-default"),
        (None, "big-default"),
        (5, "big-default"),
    ],
)
def test_context_for_resolves_the_route_per_task_else_the_default(task: Any, route: str) -> None:
    ctx = context_for(_request(task), _config())
    assert ctx.model_route == route
    # `versions.model_route` stays the agent's configured route; `ctx.model_route` is the run's.
    assert ctx.versions.model_route == "big-default"


async def test_the_workload_gets_the_resolved_route_and_the_run_span_names_it() -> None:
    """The run span carries `route` (the resolved route) and `task` only when the map resolved it.
    The echo handle reports `ctx.model_route` in its `metrics`, so the response shows it too."""
    app = create_app(_config(), _ports())
    async with _client(app) as c, app.router.lifespan_context(app):
        mapped = await c.post("/v1/run", json=_body("simplify: x", "simplify"))
        unknown = await c.post("/v1/run", json=_body("x", "unknown"))
        plain = await c.post("/v1/run", json=_body("x"))
    assert mapped.json()["metrics"]["model_route"] == "small-default"
    assert unknown.json()["metrics"]["model_route"] == "big-default"
    assert plain.json()["metrics"]["model_route"] == "big-default"
    runs = [s for s in _telemetry(app).spans if s.name == "chassis.run"]
    assert [s.attributes.get("route") for s in runs] == [
        "small-default",
        "big-default",
        "big-default",
    ]
    assert [s.attributes.get("task") for s in runs] == ["simplify", None, None]


async def test_a_reload_changes_the_map_with_no_restart() -> None:
    """The config port's new document swaps the map; the next run resolves through it."""
    app = create_app(_config(), _ports())
    document = json.loads(json.dumps(CONFIG))
    document["version"] = "cfg-routes-2"
    document["spec"]["model"]["routes"] = {"simplify": "tiny-default"}
    document["spec"]["model"]["judge_route"] = "decision-small"
    async with _client(app) as c, app.router.lifespan_context(app):
        before = await c.post("/v1/run", json=_body("simplify: x", "simplify"))
        await app.state.ports.config.put("simplifier", document)
        after = await c.post("/v1/run", json=_body("simplify: x", "simplify"))
        config: ChassisConfig = app.state.config
    assert before.json()["metrics"]["model_route"] == "small-default"
    assert after.json()["metrics"]["model_route"] == "tiny-default"
    assert config.spec.model.routes == {"simplify": "tiny-default"}
    assert config.spec.model.judge_route == "decision-small"
    assert _telemetry(app).counter_value("chassis.config.reloaded") == 1


# --- 3. Enforcement -------------------------------------------------------------------------------


async def _run_through_proxy(
    config: ChassisConfig, engine: ProxyWorkload, model: RouteModel, evaluator: Any = None
) -> tuple[httpx.Response, Any]:
    app = create_app(config, _ports(engine, model, evaluator))
    engine.proxy = create_proxy_app(app)
    async with _client(app) as c, app.router.lifespan_context(app):
        res = await c.post("/v1/run", json={**_body("simplify: x", "simplify"), "trace_id": TRACE})
    return res, app


async def test_the_proxy_refuses_a_call_that_names_another_route() -> None:
    """012 H-3 (user decision 2026-11-13): `big-default` is listed in the config but is not the
    run's route, so the call is 403 `model_route_denied`, never redirected."""
    engine, model = ProxyWorkload(asks="big-default"), RouteModel()
    res, app = await _run_through_proxy(_config(), engine, model)
    assert res.status_code == 200, res.text
    assert [(s, b["error"]["code"]) for s, b in engine.answers] == [(403, "model_route_denied")]
    assert model.routes == []
    telemetry = _telemetry(app)
    assert telemetry.counter_value(ROUTE_REFUSED, route="small-default") == 1
    assert not [s for s in telemetry.spans if s.name == ROUTE_SPAN]


async def test_the_proxy_leaves_a_call_on_the_resolved_route_alone() -> None:
    engine, model = ProxyWorkload(), RouteModel()  # sends `ctx.model_route`
    res, app = await _run_through_proxy(_config(), engine, model)
    assert res.status_code == 200, res.text
    assert model.routes == ["small-default"]
    telemetry = _telemetry(app)
    assert telemetry.counter_value(ROUTE_REFUSED, route="small-default") == 0
    (span,) = [s for s in telemetry.spans if s.name == ROUTE_SPAN]
    assert span.attributes["route.forced"] is False


async def test_the_gates_fallback_route_still_replaces_the_mapped_route() -> None:
    """Two attempts on the task's route, then the fallback attempt on the fallback route: the
    gate puts it in `ctx.model_route`, the workload sends it, and admission lets it through."""
    engine = ProxyWorkload()  # sends `ctx.model_route`
    model = RouteModel([ScriptRule(match="simplify", reply=BAD)])
    evaluator = ScriptedEvaluator([ScoreRule(match=BAD, score=0.2)], default=0.9)
    config = _config({"fallback_route": "fallback-route"}, harness={"evaluator_gate": True})
    res, app = await _run_through_proxy(config, engine, model, evaluator)
    assert res.json()["status"] == "fallback", res.text
    assert model.routes == ["small-default", "small-default", "fallback-route"]
    assert [s for s, _ in engine.answers] == [200, 200, 200]
    telemetry = _telemetry(app)
    assert all(v == 0 for (name, _), v in telemetry.counters.items() if name == ROUTE_REFUSED)


async def test_a_fallback_attempt_that_names_the_first_route_is_refused() -> None:
    """A workload that ignores `ctx.model_route` in the fallback attempt is refused there."""
    engine = ProxyWorkload(asks="small-default")
    model = RouteModel([ScriptRule(match="simplify", reply=BAD)])
    evaluator = ScriptedEvaluator([ScoreRule(match=BAD, score=0.2)], default=0.9)
    config = _config({"fallback_route": "fallback-route"}, harness={"evaluator_gate": True})
    res, app = await _run_through_proxy(config, engine, model, evaluator)
    assert model.routes == ["small-default", "small-default"]
    assert [s for s, _ in engine.answers] == [200, 200, 403]
    assert _telemetry(app).counter_value(ROUTE_REFUSED, route="fallback-route") == 1
    assert res.status_code == 200, res.text


# --- 4. The judge ---------------------------------------------------------------------------------


def _judge_script() -> Script:
    return Script.model_validate(
        {"rules": [{"match": "</answer>", "reply": json.dumps({"score": 0.9, "reason": "ok"})}]}
    )


@pytest.mark.parametrize(
    ("judge_route", "expected"), [("decision-small", "decision-small"), (None, "judge")]
)
async def test_the_gate_scores_on_spec_model_judge_route_else_the_judges_own(
    judge_route: str | None, expected: str
) -> None:
    models = fake_models(_judge_script())
    judge = JudgeEvaluator(
        LiteLLMModel("http://judge/v1", transport=httpx.ASGITransport(app=models)), route="judge"
    )
    config = _config({"judge_route": judge_route}, harness={"evaluator_gate": True})
    app = create_app(config, _ports(evaluator=judge))
    async with _client(app) as c, app.router.lifespan_context(app):
        res = await c.post("/v1/run", json=_body("simplify: x", "simplify"))
    assert res.json()["status"] == "ok", res.text
    assert [call["model"] for call in models.state.calls] == [expected]


# --- 5. The demo: config/agent.yaml with a routes map, through the fake model server ----------

AGENT_YAML = """\
# A `config/agent.yaml` as `agentctl new` renders it, with the route map per task added
# (docs/specs/models.md). The `fake` profile here: the test passes the ports itself.
profile: fake
agent:
  name: simplifier
  version: 0.1.0
spec:
  engine:
    connector: inprocess
    handle: chassis.core.handle:echo_wire
  model:
    route: big-default            # the default for every task
    fallback_route: small-default
    routes:                       # which route per task
      simplify: small-default
      lookup: big-default
      classify: decision-small
    judge_route: decision-small   # the judge model
  prompt:
    version: simplifier-v1
"""

MODEL_SCRIPT: dict[str, Any] = {
    "rules": [
        {"model": "small-default", "reply": "Plain words. Short sentences."},
        {"model": "big-default", "reply": "SLM is a small language model."},
        {"model": "decision-small", "reply": "billing"},
    ]
}

DEMO_RUNS: list[tuple[str | None, str, str, str]] = [
    ("simplify", "simplify: The report was signed by Dana Ruiz.", "small-default", "Plain words."),
    ("lookup", "lookup: SLM", "big-default", "SLM is a small language model."),
    ("classify", "classify: The invoice is overdue.", "decision-small", "billing"),
    (None, "lookup: SLM", "big-default", "SLM is a small language model."),
]


async def test_agent_yaml_sends_each_task_to_its_route_through_the_fake_model_server(
    tmp_path: Path,
) -> None:
    """The demo: `simplify` goes to `small-default`, `lookup` to `big-default`, `classify` to
    `decision-small`, no task to `route`; the workload sends `ctx.model_route`, nothing is refused,
    and the fake model server answers by route."""
    path = tmp_path / "agent.yaml"
    path.write_text(AGENT_YAML)
    config = load_config(path)
    models = fake_models(Script.model_validate(MODEL_SCRIPT))
    model = LiteLLMModel("http://models/v1", transport=httpx.ASGITransport(app=models))
    engine = ProxyWorkload()
    app = create_app(config, _ports(engine, model))
    engine.proxy = create_proxy_app(app)
    rows: list[str] = []
    async with _client(app) as c, app.router.lifespan_context(app):
        for task, text, route, answer in DEMO_RUNS:
            res = await c.post("/v1/run", json=_body(text, task))
            body = res.json()
            assert res.status_code == 200 and body["status"] == "ok", res.text
            assert body["output"]["text"].startswith(answer), (task, body)
            rows.append(f"task={task or '-':<9} route={route:<15} answer={body['output']['text']}")
    assert [call["model"] for call in models.state.calls] == [r[2] for r in DEMO_RUNS]
    assert all(v == 0 for (name, _), v in _telemetry(app).counters.items() if name == ROUTE_REFUSED)
    print("\n" + "\n".join(rows))  # the demo's record, shown with `pytest -s`
