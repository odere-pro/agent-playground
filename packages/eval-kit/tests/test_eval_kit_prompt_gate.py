"""A simplifier prompt change reaches the eval gate's input, and starts the gate in CI (037 M-2).

In CI the gate runs each case through the chassis to the workload. The workload's model call goes
to the chassis's outbound model proxy, which forwards it (rewritten: route, `max_tokens` capped
at the run's remainder) through the `LiteLLMModel` adapter to the fake model server with the gate
script (`eval_kit.evalrun.gate_golden`). Here the same pieces run in process, with no socket:
`echo_python.handle` with its prompt changed, on an ASGI transport to the chassis proxy app, whose
model port is `LiteLLMModel` on an ASGI transport to the fake model server. The server records
every upstream request body, so the test reads the model input the gate scores.

The scripted model ignores the prompt (its rules match the last user message), so the scored
answer does not change; what this proves is that the changed prompt is in the input.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from bakeoff.registry import ROOT
from chassis import CHASSIS_VERSION
from chassis.adapters.litellm import LiteLLMModel
from chassis.core.envelope import Budget, Context, Request, TaskInput, Versions
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from echo_python import tools
from eval_kit.evalrun import gate_golden
from eval_kit.golden import Case, parse_cases
from fake_model_server import Script
from fake_model_server import create_app as create_fake_model

# `echo_python.handle` the attribute is the function; the module holds the prompt and the hook.
handle_module: Any = importlib.import_module("echo_python.handle")

CHANGED_PROMPT = "Rewrite for a child of ten. One idea per line. Keep every fact."
WORKFLOW = ROOT / ".github/workflows/poc08-evals.yml"
WORKLOADS = ROOT / "packages/workloads"
WORKLOADS_FILTER = "packages/workloads/**"
ROUTE = "big-default"
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"  # pragma: allowlist secret (a trace id in a test)
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"
RUN_MAX_TOKENS = 2000  # suggested: a run budget large enough for one scripted answer
CHASSIS_CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "simplifier", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": ROUTE},
    },
}


def _first_simplifier_case(cases_file: Path) -> Case:
    data = yaml.safe_load(cases_file.read_text(encoding="utf-8"))
    cases = parse_cases(data, size=None)
    return next(c for c in cases if c.task == "simplifier" and not c.model_calls)


async def _no_tools(headers: dict[str, str], timeout_s: float) -> list[dict[str, Any]]:
    return []


def _run(case: Case) -> tuple[Request, Context]:
    """The chassis run the workload's model call is correlated with (by `TRACEPARENT`)."""
    request = Request(
        request_id="req-prompt",
        trace_id=TRACE,
        idempotency_key="idem-prompt",
        agent="simplifier",
        agent_version="0.0.1",
        input=TaskInput(text=case.input),
        budget=Budget(max_tokens=RUN_MAX_TOKENS, timeout_ms=30_000),
    )
    ctx = Context(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        budget=request.budget,
        versions=Versions(chassis=CHASSIS_VERSION),
        model_route=ROUTE,
    )
    return request, ctx


async def _upstream_call(
    case: Case, script: Script, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """One gate case: workload, chassis model proxy, fake model server. The one upstream body."""
    fake = create_fake_model(script)
    model = LiteLLMModel("http://fake/v1", transport=httpx.ASGITransport(app=fake))
    ports = PortBundle(
        model=model,
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
    )
    app = create_app(ChassisConfig.model_validate(CHASSIS_CONFIG), ports)
    proxy = create_proxy_app(app)
    monkeypatch.setattr(handle_module, "transport", httpx.ASGITransport(app=proxy))
    request, ctx = _run(case)
    wire_ctx = {"request_id": request.request_id, "model_route": ROUTE, "traceparent": TRACEPARENT}
    async with app.router.lifespan_context(app), app.state.runs.register(request, ctx) as record:
        events = [e async for e in handle_module.handle({"text": case.input}, wire_ctx)]
        assert record.model_calls == 1, "the call went through the proxy, charged to the run"
    assert events[-1] == {"schema_version": "0", "type": "end", "status": "ok"}, events
    (call,) = fake.state.calls
    body: dict[str, Any] = call
    return body


async def test_a_changed_prompt_changes_the_gate_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: the gate's own case file and model script, for the normal logic.
    golden = gate_golden("normal", tmp_path)
    script = Script.from_yaml(ROOT / golden.model_script)
    case = _first_simplifier_case(golden.cases_file)
    monkeypatch.setattr(tools, "list_openai_tools", _no_tools)  # a simplifier case calls no tool
    shipped_prompt = handle_module.SYSTEM_PROMPT
    assert shipped_prompt != CHANGED_PROMPT

    # Act: the same case, with the shipped prompt and then with the changed one.
    before = await _upstream_call(case, script, monkeypatch)
    monkeypatch.setattr(handle_module, "SYSTEM_PROMPT", CHANGED_PROMPT)
    after = await _upstream_call(case, script, monkeypatch)

    # Assert: the body the fake model server got, after the chassis proxy, holds the prompt in
    # force and the case input.
    assert before["messages"] == [
        {"role": "system", "content": shipped_prompt},
        {"role": "user", "content": case.input},
    ]
    assert after["messages"] == [
        {"role": "system", "content": CHANGED_PROMPT},
        {"role": "user", "content": case.input},
    ]
    assert after["model"] == ROUTE
    # The workload sends no `max_tokens`; the proxy's cap (the run's remainder) proves the hop.
    assert after["max_tokens"] == RUN_MAX_TOKENS


def _github_glob(pattern: str) -> re.Pattern[str]:
    """A GitHub Actions path filter as a regex: `**` crosses `/`, `*` does not."""
    parts = (re.escape(p).replace(r"\*", "[^/]*") for p in pattern.split("**"))
    return re.compile(".*".join(parts) + r"\Z")


def _workflow_on(data: dict[Any, Any]) -> dict[str, Any]:
    on = data["on"] if "on" in data else data[True]  # YAML 1.1 reads a bare `on` as True
    assert isinstance(on, dict)
    return on


def test_a_changed_prompt_starts_the_gate_workflow() -> None:
    """The gate's path filter holds `packages/workloads/**`, on pull requests and on pushes to
    main, so an edit to any workload, its prompt included, runs the gate before release. This
    guards against removing or narrowing that filter. The text match below finds only the files
    that hold the shipped wording; a workload with a differently worded prompt is covered by the
    glob, not by that check."""
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = _workflow_on(data)
    paths: list[str] = on["pull_request"]["paths"]
    assert WORKLOADS_FILTER in paths
    assert on["push"]["paths"] == paths

    prompt = handle_module.SYSTEM_PROMPT
    holders = sorted(
        p.relative_to(ROOT).as_posix()
        for p in WORKLOADS.rglob("*")
        if p.suffix in {".py", ".ts"}
        and "node_modules" not in p.parts
        and "dist" not in p.parts
        and prompt in p.read_text(encoding="utf-8", errors="replace")
    )
    assert "packages/workloads/echo-python/src/echo_python/handle.py" in holders
    assert "packages/workloads/echo-typescript/src/handle.ts" in holders
    filters = [_github_glob(p) for p in paths]
    assert [h for h in holders if not any(f.match(h) for f in filters)] == []

    runs = " ".join(str(step.get("run", "")) for step in data["jobs"]["evals"]["steps"])
    assert "python -m eval_kit.evalrun" in runs
    assert "python -m eval_kit.evalgate" in runs
