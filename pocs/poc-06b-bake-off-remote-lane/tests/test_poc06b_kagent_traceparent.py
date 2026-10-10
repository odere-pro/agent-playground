"""The kagent-adk launcher forwards the inbound traceparent to the model (kind run 4 failed 403
`run_required`: with the OTel exporters off, nothing carried it).

The launcher is a ConfigMap file that imports kagent, which is not a dependency here. These tests
run its source with the kagent, ADK, and uvicorn modules replaced by stubs, so only our hook logic
runs. The end-to-end proof is the local repro in notes/2026-10-09-kagent-probe.md (Item 4)."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

POC06 = Path(__file__).resolve().parents[3] / "deploy/kind/poc06"
TRACE_ID = "17fe6f30aaaaaaaaaaaaaaaaaaaaaaaa"
INBOUND = f"00-{TRACE_ID}-5d5c5b55414bb7fe-01"
STUBBED = (
    "uvicorn",
    "a2a",
    "a2a.types",
    "google",
    "google.adk",
    "google.adk.plugins",
    "google.adk.plugins.base_plugin",
    "google.protobuf",
    "google.protobuf.json_format",
    "kagent",
    "kagent.adk",
    "kagent.adk._llm_passthrough_plugin",
    "kagent.core",
    "kagent.core.telemetry",
)


class _BasePlugin:
    def __init__(self, name: str) -> None:
        self.name = name


class _Request:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


def _source() -> str:
    docs = yaml.safe_load_all((POC06 / "kagent/remote-kagent-adk.yaml").read_text())
    cm = next(d for d in docs if d and d["kind"] == "ConfigMap")
    return str(cm["data"]["run_kagent_adk.py"])


@pytest.fixture
def launcher(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    for name in STUBBED:
        monkeypatch.setitem(sys.modules, name, MagicMock())
    plugin_mod = types.ModuleType("google.adk.plugins.base_plugin")
    plugin_mod.BasePlugin = _BasePlugin  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google.adk.plugins.base_plugin", plugin_mod)
    ns: dict[str, Any] = {"__name__": "run_kagent_adk"}
    exec(compile(_source(), "run_kagent_adk.py", "exec"), ns)
    return ns


def test_plugin_is_registered_after_the_bearer_plugin() -> None:
    src = _source()
    assert "class TraceparentPassthroughPlugin(BasePlugin)" in src
    assert "[LLMPassthroughPlugin(), TraceparentPassthroughPlugin()]" in src


def test_hook_installs_once_and_stamps_the_inbound_traceparent(launcher: dict[str, Any]) -> None:
    plugin = launcher["TraceparentPassthroughPlugin"]()
    hooks: dict[str, list[Any]] = {"request": [], "response": []}

    async def run() -> _Request:
        ctx = MagicMock()
        ctx.state = {"headers": {"traceparent": INBOUND, "authorization": "Bearer t"}}
        ctx._invocation_context.agent.model._client._client.event_hooks = hooks
        for _ in range(2):  # a second model call must not add a second hook
            await plugin.before_model_callback(callback_context=ctx, llm_request=MagicMock())
        request = _Request()
        for hook in hooks["request"]:
            await hook(request)
        return request

    request = asyncio.run(run())
    assert hooks["request"] == [launcher["stamp_traceparent"]]
    assert request.headers == {"traceparent": INBOUND}


@pytest.mark.parametrize("bad", ["", "garbage", f"01-{TRACE_ID}-5d5c5b55414bb7fe-01", INBOUND[:-2]])
def test_a_missing_or_malformed_traceparent_sends_none(launcher: dict[str, Any], bad: str) -> None:
    plugin = launcher["TraceparentPassthroughPlugin"]()
    hooks: dict[str, list[Any]] = {"request": []}

    async def run() -> _Request:
        ctx = MagicMock()
        ctx.state = {"headers": {"traceparent": bad}}
        ctx._invocation_context.agent.model._client._client.event_hooks = hooks
        await plugin.before_model_callback(callback_context=ctx, llm_request=MagicMock())
        request = _Request()
        await launcher["stamp_traceparent"](request)
        return request

    assert asyncio.run(run()).headers == {}
