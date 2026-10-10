"""PoC-6b, exit criterion 1 offline: `echo-smolagents` and `echo-claude-agent` pass the
`EngineConnectorContract` suite in the `remote` lane, over A2A on a Unix socket.

Each engine is served by its own template A2A server (`workload-a2a`) with `--require-token-env`
on, and driven by `RemoteConnector` with the per-remote bearer token (`poc05_harness`:
`Tokens`, `remote_lane_connector`). A missing or wrong bearer would fail every case. No TCP,
no key; `*_API_KEY` and `*_TOKEN` are not read from the environment.

- smolagents: the fake model server on its code-reply script
  (`echo-smolagents/tests/scripts/smolagents.yaml`), on a Unix socket, through the workload's
  `handle.transport` hook. The generated code is the scripted reply; nothing else answers.
- Claude: the workload's `handle.query_fn` hook returns replayed SDK messages. The real `claude`
  CLI never starts. The environment guard still runs (no `ANTHROPIC_*` or `CLAUDE*` variable).

The JSON-integer and `ctx.traceparent` cases run the suite's own `json_values_handle` behind the
same remote lane (the template server and `RemoteConnector`), once per engine id, because an
engine serves one fixed handle.

No in-memory binding, on purpose. The in-memory mode of the suite is the `inprocess` connector,
which imports the handle into the chassis process. These two engines are `untrusted`: smolagents
runs model-written Python in its own process and Claude runs shell and file tools. The registry
puts them in the `remote` lane only (`ENGINES[...].lane == "remote"`), and an in-process run
would put that code in the chassis, which holds the credentials (ADR-001). The in-memory half of
criterion 1 stays with the trusted Python engines in 6a (`TestEngineInMemory`).
"""

from __future__ import annotations

import importlib
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from chassis.core.envelope import Request
from chassis.core.events import Delta, End
from chassis.ports.engine import EngineConnector
from chassis_contracts import EngineConnectorContract
from chassis_contracts.engine import JSON_VALUES_HANDLE
from chassis_contracts.helpers import make_context, make_request
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from fake_model_server import Script, create_app
from poc05_harness import Tokens, app_on_socket, remote_lane_connector
from poc06_harness import ENGINES, SMOKE

SMOLAGENTS_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "packages/workloads/echo-smolagents/tests/scripts/smolagents.yaml"
)
REMOTE_ENGINES = ["echo-smolagents", "echo-claude-agent"]
"""The two new Python workloads of the remote lane."""
GUARDED = ("ANTHROPIC_", "CLAUDE", "CHASSIS_")


def _module(dotted: str) -> Any:
    return importlib.import_module(dotted)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No key and no CLI variable in the process; the Claude run dirs go under `tmp_path`."""
    for name in list(os.environ):
        if name.endswith(("_API_KEY", "_TOKEN")) or name.startswith(GUARDED):
            monkeypatch.delenv(name)
    monkeypatch.setenv("CLAUDE_AGENT_HOME_BASE", str(tmp_path / "claude-home"))


class _ReplayedQuery:
    """Stands in for the SDK's `query`: yields the smoke task's SDK messages, starts no CLI."""

    def __call__(self, *, prompt: Any, options: Any = None) -> AsyncIterator[Any]:
        return self._run()

    async def _run(self) -> AsyncIterator[Any]:
        yield AssistantMessage(content=[TextBlock("Hello.")], model="big-default")
        yield ResultMessage(
            subtype="success",
            duration_ms=120,
            duration_api_ms=100,
            is_error=False,
            num_turns=1,
            session_id="s-1",
            usage={"input_tokens": 40, "output_tokens": 9},
        )


def _wire_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_module("echo_claude_agent.handle"), "query_fn", _ReplayedQuery())


async def _wire_smolagents(monkeypatch: pytest.MonkeyPatch, stack: list[Any]) -> None:
    """The fake model server on a Unix socket; the workload's sync model hook points at it."""
    cm = app_on_socket(create_app(Script.from_yaml(SMOLAGENTS_SCRIPT)), "model.sock")
    uds = await cm.__aenter__()
    stack.append(cm)
    monkeypatch.setattr(
        _module("echo_smolagents.handle"), "transport", httpx2.HTTPTransport(uds=uds)
    )
    monkeypatch.setenv("CHASSIS_MODEL_URL", "http://model.invalid/v1")


class TestRemoteEngineOverA2A(EngineConnectorContract):
    """Exit criterion 1, over A2A on a Unix socket: each remote engine behind `RemoteConnector`."""

    @pytest.fixture(params=REMOTE_ENGINES)
    def engine_name(self, request: pytest.FixtureRequest) -> str:
        return str(request.param)

    @pytest.fixture
    def run_request(self) -> Request:
        return make_request(text=SMOKE.text)

    @pytest.fixture
    async def engine(
        self, engine_name: str, monkeypatch: pytest.MonkeyPatch
    ) -> AsyncIterator[EngineConnector]:
        spec = ENGINES[engine_name]
        assert spec.lane == "remote" and spec.trust == "untrusted"
        Tokens.one().apply(monkeypatch)
        stack: list[Any] = []
        try:
            if engine_name == "echo-claude-agent":
                _wire_claude(monkeypatch)
            else:
                await _wire_smolagents(monkeypatch, stack)
            async with remote_lane_connector(spec.handle) as connector:
                yield connector
        finally:
            for cm in reversed(stack):
                await cm.__aexit__(None, None, None)

    @pytest.fixture
    async def json_values_engine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> AsyncIterator[EngineConnector]:
        Tokens.one().apply(monkeypatch)
        async with remote_lane_connector(JSON_VALUES_HANDLE) as connector:
            yield connector

    async def test_the_engine_answers_the_smoke_task(
        self, engine: EngineConnector, run_request: Request
    ) -> None:
        """The control for the binding: the scripted engine, not a stub, answers `Hello.`."""
        events = [e async for e in engine.run(run_request, make_context(run_request))]
        assert "".join(e.text for e in events if isinstance(e, Delta)).strip() == "Hello."
        assert isinstance(events[-1], End) and events[-1].status == "ok"


def test_the_registry_names_both_engines_as_remote() -> None:
    """The control for the binding: both engines are untrusted and in the remote lane."""
    lanes: Iterator[str] = (ENGINES[n].lane for n in REMOTE_ENGINES)
    assert set(lanes) == {"remote"}
