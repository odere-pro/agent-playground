"""PoC-6c exit criterion 1, the offline half: only `spec.model.route` changes.

Every trusted PoC-6a engine (plain Python, PydanticAI, LangGraph, OpenAI Agents SDK, and the
TypeScript agent when its `node_modules` is installed) runs the smoke, simplifier, and lookup
tasks behind a real chassis app on route `big-default`. The route is then switched to
`local-small` through the chassis config reload (the PoC-4 mechanism: `InMemoryConfig.put` plays
the store change, as in `pocs/poc-04-stateless-scalable/tests/test_config_reload.py`), with no new
app, no new engine, and no restart. The tasks run again. The fake model server records each
request body, so the test reads the `model` the engine sent: `big-default`, then `local-small`.
The fake model plays both routes; the engines, the workloads, the images, and the rest of the
config are untouched.

The real SLM half (Qwen3-1.7B behind llama.cpp and LiteLLM) does not run here. It comes from
`make poc06-mac`, which runs the same engines on the real `local-small` route on a Mac.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from chassis.core.envelope import Context, Request
from chassis.core.events import Event
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector, Lane
from chassis.server import ChassisConfig, create_app
from chassis.server.config_loader import changed_paths, restart_required
from poc06_harness import CHECKS, ENGINES, TASKS, Task
from poc06a_harness import (
    Backends,
    SidecarAddress,
    connected,
    serve_backends,
    sidecar_engines,
    typescript_agent,
    wire_python_engine,
)

BIG = "big-default"
SMALL = "local-small"
AGENT = "echo"


@pytest.fixture(scope="module")
def _backends_for_module() -> Iterator[Backends]:
    with serve_backends() as backends:
        yield backends


@pytest.fixture
def backends(_backends_for_module: Backends) -> Backends:
    _backends_for_module.reset()
    return _backends_for_module


@pytest.fixture(scope="module")
def typescript(_backends_for_module: Backends) -> Iterator[SidecarAddress]:
    """The TypeScript agent on a Unix socket. Skips when its `node_modules` is absent."""
    with typescript_agent(_backends_for_module) as address:
        yield address


@dataclass
class Recording:
    """What the engine saw through the chassis: each run's events and the context it got."""

    runs: list[tuple[list[dict[str, Any]], Context]] = field(default_factory=list)


class RecordingEngine:
    """Wraps a connected engine. The chassis app runs it as its engine port; it records every run
    and passes everything else through. `setup` and `close` do nothing: the harness owns them."""

    def __init__(self, inner: EngineConnector, recording: Recording) -> None:
        self._inner = inner
        self._recording = recording
        self.kind: Lane = inner.kind
        self.capabilities = inner.capabilities

    async def setup(self, config: Mapping[str, Any], ports: PortBundle) -> None:
        return None

    async def run(self, request: Request, ctx: Context) -> AsyncIterator[Event]:
        events: list[dict[str, Any]] = []
        self._recording.runs.append((events, ctx))
        async for event in self._inner.run(request, ctx):
            events.append(event.model_dump(mode="json", exclude_none=True))
            yield event

    async def probe(self) -> bool:
        return await self._inner.probe()

    async def close(self) -> None:
        return None


def _config(engine_name: str, address: SidecarAddress | None) -> dict[str, Any]:
    engine = ENGINES[engine_name]
    spec: dict[str, Any] = (
        {"connector": "inprocess", "handle": engine.handle}
        if address is None
        else {"connector": "sidecar", "url": address.url, "uds": address.uds}
    )
    return {
        "version": "poc06c-1",
        "profile": "fake",
        "agent": {"name": AGENT, "version": "0.0.1"},
        "spec": {
            "adapters": {"config": "memory"},
            "engine": spec,
            "model": {"route": BIG},
            "prompt": {"version": "p1"},
        },
    }


@dataclass
class Running:
    app: Any
    client: httpx.AsyncClient
    store: InMemoryConfig
    recording: Recording
    document: dict[str, Any]


@asynccontextmanager
async def running(engine_name: str, address: SidecarAddress | None) -> AsyncIterator[Running]:
    """A chassis app (profile `fake`, config in memory) in front of the engine, on `big-default`.
    Python engines run in memory, the TypeScript agent as a sidecar on its Unix socket."""
    engine = ENGINES[engine_name]
    lane = "sidecar" if address is not None else "inprocess"
    document = _config(engine_name, address)
    store = InMemoryConfig()
    recording = Recording()
    async with connected(engine, lane, address) as connector:
        ports = PortBundle(
            model=ScriptedModel(),
            engine=RecordingEngine(connector, recording),
            config=store,
            telemetry=InMemoryTelemetry(),
        )
        app = create_app(ChassisConfig.model_validate(document), ports)
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
            app.router.lifespan_context(app),
        ):
            yield Running(app, client, store, recording, document)


async def _run_task(live: Running, backends: Backends, task: Task) -> tuple[Any, Context]:
    """One task through `/v1/run`. Returns the verdict and the context the engine got."""
    before = len(live.recording.runs)
    response = await live.client.post("/v1/run", json={"input": {"text": task.text}})
    assert response.status_code == 200, response.text
    assert len(live.recording.runs) == before + 1, "the engine was not run exactly once"
    events, ctx = live.recording.runs[-1]
    return CHECKS[task.name](events), ctx


def _seen_models(backends: Backends) -> list[str]:
    return [str(body.get("model")) for body in backends.model_app.state.calls]


@pytest.fixture
def address(request: pytest.FixtureRequest) -> SidecarAddress | None:
    if ENGINES[request.getfixturevalue("engine_name")].language != "typescript":
        return None
    found: SidecarAddress = request.getfixturevalue("typescript")
    return found


@pytest.fixture
def wired(engine_name: str, backends: Backends, monkeypatch: pytest.MonkeyPatch) -> Backends:
    if ENGINES[engine_name].language == "python":
        wire_python_engine(monkeypatch, ENGINES[engine_name], backends)
    return backends


@pytest.mark.parametrize("engine_name", sorted(sidecar_engines()))
async def test_only_the_route_changes_and_every_task_passes_both_times(
    engine_name: str, wired: Backends, address: SidecarAddress | None
) -> None:
    async with running(engine_name, address) as live:
        assert live.app.state.config.spec.model.route == BIG
        old = live.app.state.config
        engine_before = live.app.state.ports.engine

        first_ctx: dict[str, Context] = {}
        for task in TASKS:
            wired.reset()
            verdict, ctx = await _run_task(live, wired, task)
            assert verdict.passed, (BIG, task.name, verdict.problems)
            assert ctx.model_route == BIG
            models = _seen_models(wired)
            assert models, f"{task.name}: the model server was never called"
            assert set(models) == {BIG}, (task.name, models)
            assert wired.tool_calls == [(s.name, dict(s.arguments)) for s in task.tool_calls]
            first_ctx[task.name] = ctx

        # The switch: the store change only. No restart, no new app, no new engine.
        changed = copy.deepcopy(live.document)
        changed["spec"]["model"]["route"] = SMALL
        await live.store.put(AGENT, changed)
        new = live.app.state.config
        assert new is not old, "the reload did not take effect"
        assert new.spec.model.route == SMALL
        # `version` is the config version plus the content hash (`poc06c-1+<hash>`): it moves
        # with any content change. Nothing else moves.
        assert sorted(changed_paths(old, new)) == ["spec.model.route", "version"]
        assert new.version.startswith(f"{live.document['version']}+")
        assert restart_required(old, new) == []
        assert live.app.state.ports.engine is engine_before
        assert new.spec.engine == old.spec.engine
        assert new.profile == old.profile and new.agent == old.agent
        assert new.spec.prompt == old.spec.prompt and new.spec.limits == old.spec.limits

        for task in TASKS:
            wired.reset()
            verdict, ctx = await _run_task(live, wired, task)
            assert verdict.passed, (SMALL, task.name, verdict.problems)
            assert ctx.model_route == SMALL
            models = _seen_models(wired)
            assert models, f"{task.name}: the model server was never called"
            assert set(models) == {SMALL}, (task.name, models)
            assert wired.tool_calls == [(s.name, dict(s.arguments)) for s in task.tool_calls]
            _only_route_differs(first_ctx[task.name], ctx)


def _only_route_differs(before: Context, after: Context) -> None:
    """The context the workload gets differs only in the route, the config version it names, and
    the per-run ids. Everything else (agent, budget, versions of chassis and prompt) is equal."""
    a, b = before.model_dump(mode="json"), after.model_dump(mode="json")
    per_run = {"request_id", "trace_id", "traceparent", "idempotency_key"}
    differing = {k for k in a if a[k] != b[k]} - per_run
    assert differing == {"model_route", "versions"}, differing
    va, vb = a["versions"], b["versions"]
    assert {k for k in va if va[k] != vb[k]} == {"model_route", "config"}, (va, vb)
    assert (va["model_route"], vb["model_route"]) == (BIG, SMALL)


async def test_the_registry_has_the_four_python_engines_and_the_typescript_agent() -> None:
    assert {"echo-python", "echo-pydanticai", "echo-langgraph", "echo-openai-agents"} <= set(
        sidecar_engines()
    )
    assert any(e.language == "typescript" for e in sidecar_engines().values())
