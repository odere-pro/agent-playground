"""The kagent-adk launcher forwards the inbound traceparent to the model (kind run 4 failed 403
`run_required`: with the OTel exporters off, nothing carried it), and checks the inbound bearer
itself (055 CH-6: kagent-adk checks none, so the check is our wrapper, `BearerGate`).

The launcher is a ConfigMap file that imports kagent, which is not a dependency here. These tests
run its source with the kagent, ADK, and uvicorn modules replaced by stubs, so only our hook logic
runs. The end-to-end proof is the local repro in notes/2026-10-09-kagent-probe.md (Item 4)."""

from __future__ import annotations

import asyncio
import os
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


def test_plugin_is_always_registered_and_the_bearer_is_never_passed_through() -> None:
    """055 CH-6: key passthrough is off, so the traceparent plugin is the only plugin."""
    src = _source()
    assert "class TraceparentPassthroughPlugin(BasePlugin)" in src
    assert "plugins = [TraceparentPassthroughPlugin()]" in src
    assert "LLMPassthroughPlugin" not in src


# --- the inbound bearer check (055 CH-6): our wrapper, not kagent's -------------------------------

TO_WORKLOAD = "a" * 64  # pragma: allowlist secret (a test value)
TO_CHASSIS = "b" * 64  # pragma: allowlist secret (a test value)


def _call(gate: Any, scope: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Run one ASGI call through the gate; return what it sent and what reached the app."""
    sent: list[dict[str, Any]] = []
    reached: list[str] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        reached.append(scope["type"])

    asyncio.run(gate(app, TO_WORKLOAD)(scope, receive, send))
    return sent, reached


def _http(authorization: bytes | None) -> dict[str, Any]:
    headers = [(b"content-type", b"application/json")]
    if authorization is not None:
        headers.append((b"authorization", authorization))
    return {"type": "http", "method": "POST", "path": "/", "headers": headers}


@pytest.mark.parametrize("scheme", [b"Bearer", b"bearer"])
def test_the_gate_passes_the_chassis_to_workload_key(
    launcher: dict[str, Any], scheme: bytes
) -> None:
    sent, reached = _call(launcher["BearerGate"], _http(scheme + b" " + TO_WORKLOAD.encode()))
    assert reached == ["http"] and sent == []


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        b"",
        b"Bearer",
        b"Bearer ",
        f"Bearer {TO_CHASSIS}".encode(),  # the other direction's key
        f"Bearer {TO_WORKLOAD[:-1]}".encode(),
        f"Bearer {TO_WORKLOAD}x".encode(),
        f"Basic {TO_WORKLOAD}".encode(),
        TO_WORKLOAD.encode(),
    ],
    ids=[
        "none",
        "empty",
        "scheme-only",
        "scheme-space",
        "other-key",
        "short",
        "long",
        "basic",
        "bare",
    ],
)
def test_the_gate_answers_401_and_never_calls_the_app(
    launcher: dict[str, Any], authorization: bytes | None
) -> None:
    sent, reached = _call(launcher["BearerGate"], _http(authorization))
    assert reached == []
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 401
    assert (b"www-authenticate", b"Bearer") in sent[0]["headers"]
    body = b"".join(m.get("body", b"") for m in sent[1:])
    assert body == launcher["UNAUTHENTICATED"]
    assert TO_WORKLOAD.encode() not in body and TO_CHASSIS.encode() not in body


def test_the_gate_lets_lifespan_through_and_closes_a_websocket(launcher: dict[str, Any]) -> None:
    _, reached = _call(launcher["BearerGate"], {"type": "lifespan"})
    sent, ws_reached = _call(launcher["BearerGate"], {"type": "websocket", "headers": []})
    assert reached == ["lifespan"]
    assert ws_reached == [] and sent == [{"type": "websocket.close", "code": 1008}]


def test_the_launcher_reads_two_distinct_keys(launcher: dict[str, Any]) -> None:
    read_keys = launcher["read_keys"]
    keys = {"CHASSIS_A2A_TOKEN": TO_WORKLOAD, "CHASSIS_API_TOKEN": TO_CHASSIS}
    assert read_keys(keys) == (TO_WORKLOAD, TO_CHASSIS)
    for bad in (
        {},
        {"CHASSIS_A2A_TOKEN": TO_WORKLOAD},
        {"CHASSIS_API_TOKEN": TO_CHASSIS},
        {"CHASSIS_A2A_TOKEN": "", "CHASSIS_API_TOKEN": TO_CHASSIS},
        {"CHASSIS_A2A_TOKEN": TO_WORKLOAD, "CHASSIS_API_TOKEN": TO_WORKLOAD},
    ):
        with pytest.raises(SystemExit) as stop:
            read_keys(bad)
        assert TO_WORKLOAD not in str(stop.value) and TO_CHASSIS not in str(stop.value)


def test_main_gates_the_built_app_and_hands_the_model_its_own_key(
    launcher: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """main wraps the app from kapp.build(local=True) in the gate before uvicorn.run, and the
    model client's OPENAI_API_KEY is the workload-to-chassis key, never the inbound one."""
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "agent-card.json").write_text("{}")
    monkeypatch.setattr(sys, "argv", ["run_kagent_adk.py", str(tmp_path), "127.0.0.1", "9000"])
    monkeypatch.setenv("CHASSIS_A2A_TOKEN", TO_WORKLOAD)
    monkeypatch.setenv("CHASSIS_API_TOKEN", TO_CHASSIS)
    monkeypatch.setenv("OPENAI_API_KEY", "unset-by-the-test")  # restored after the test
    monkeypatch.setenv("KAGENT_PORT", "10000")

    launcher["main"]()

    kapp = launcher["KAgentApp"]
    (plugin,) = kapp.call_args.kwargs["plugins"]
    served = launcher["uvicorn"].run.call_args.args[0]
    assert isinstance(plugin, launcher["TraceparentPassthroughPlugin"])
    assert isinstance(served, launcher["BearerGate"])
    assert served.app is kapp.return_value.build.return_value
    kapp.return_value.build.assert_called_once_with(local=True)
    assert os.environ["OPENAI_API_KEY"] == TO_CHASSIS


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
