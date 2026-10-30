"""Shared harness for the PoC-8 scenarios. Not a test module.

`full_stack(engine, lane, monkeypatch, ...)` starts one chassis with every PoC-7/8 stage on at
once, for one engine in one lane:

- auth: `StaticTokens`; `RUNNER` holds the run and feedback scopes, `READER` only the read scope.
- guardrails: `LiteLLMGuardrails` -> the LiteLLM stub -> the guardrail server with the pattern
  stand-in (the PoC-7 config), wrapped in `RecordingGuardrails` so a test sees which stage ran.
- the evaluator gate: `ScriptedEvaluator` (`BAD` scores 0.1, all else 1.0), with `threshold`,
  `retries`, and `spec.model.fallback_route` from the config the test passes.
- feedback: `InMemoryFeedback` (`spec.adapters.feedback: memory`).
- telemetry: `InMemoryTelemetry`.
- marking: `spec.governance.risk_class: limited`.

The public app and the proxy app run on Unix sockets (the remote lane: the remote proxy listener,
`BearerAuth` and `RequireRun`, in place of the loopback proxy). The workload is the engine's real
`handle`: `inprocess` (imported into the chassis), `sidecar` (the template A2A server on a Unix
socket), or `remote` (the same server with `--require-token-env`, behind `RemoteConnector` with a
per-test bearer). Its model and MCP calls go to the chassis proxy socket through the engine's
test-only hooks. No TCP, no key.

The chassis code under test has no engine branch: the same app and the same ports for every cell.
Only `spec.engine` differs per cell, plus `spec.trust: untrusted` in the remote lane (the trust
rule allows an untrusted engine there only). Engine names appear here, in test wiring only.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from chassis.adapters.a2a import InProcessConnector
from chassis.adapters.a2a.remote import RemoteConnector
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.adapters.litellm.guardrails import LiteLLMGuardrails
from chassis.fakes import InMemoryConfig, InMemoryTelemetry
from chassis.fakes.auth import StaticTokens
from chassis.fakes.evaluator import ScoreRule, ScriptedEvaluator
from chassis.fakes.feedback import InMemoryFeedback
from chassis.fakes.tool import GLOSSARY_LOOKUP, InMemoryTools
from chassis.ports.auth import Principal
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis.ports.guardrail import GuardrailPort, Stage, Verdict
from chassis.ports.model import (
    ModelChunk,
    ModelMessage,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)
from chassis.ports.tool import ToolDefinition
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from chassis.server.remote_auth import create_remote_proxy_app, remote_tokens
from chassis_contracts.interface import UnixApp, serve_on_unix_sockets
from guardrail_server import PatternClassifier
from guardrail_server import create_app as create_guard
from guardrail_server.litellm_stub import StubSettings, create_stub
from poc05_harness import WORKLOAD_TOKEN_ENV, Tokens
from poc06_harness import ENGINES
from poc08_claude_cli import ClaudeCliStandIn
from poc_harness.servers import a2a_on_unix_socket

ROOT = Path(__file__).resolve().parents[3]
TRUSTED_ENGINES = ("echo-python", "echo-pydanticai", "echo-langgraph", "echo-openai-agents")
"""ADR-006: the supported trusted Python engines, each in `inprocess` and `sidecar`."""
REMOTE_ENGINES = ("echo-python", "echo-smolagents", "echo-claude-agent")
"""The `remote` lane: the PoC-5 baseline and the two untrusted engines of ADR-006."""
CELLS: tuple[tuple[str, str], ...] = (
    *((e, lane) for e in TRUSTED_ENGINES for lane in ("inprocess", "sidecar")),
    *((e, "remote") for e in REMOTE_ENGINES),
)
LANES = ("inprocess", "sidecar", "remote")

ROUTE = "big-default"
FALLBACK_ROUTE = "fallback-route"
RUNNER = "tok-poc08-runner"
READER = "tok-poc08-reader"
OTHER = "tok-poc08-other"
"""A second caller with the run and feedback scopes: the rate limit is per caller."""
TOKENS = {
    RUNNER: Principal("user-run", frozenset({"agent:run", "agent:feedback"})),
    READER: Principal("user-read", frozenset({"agent:read"})),
    OTHER: Principal("user-other", frozenset({"agent:run", "agent:feedback"})),
}
STUB_KEY = "poc08-stub-key-not-real"  # pragma: allowlist secret (a test value)
PROXY_URL = "http://chassis-proxy"
"""Where a workload's model and MCP calls go. Over the Unix socket it only fills `Host`."""
SIDECAR_URL = "http://127.0.0.1:9100"
REMOTE_URL = "http://echo-remote:9000"
REMOTE_TOKEN_ENV = "POC05_REMOTE_TOKEN"
START_TIMEOUT_S = 15.0

PLAIN = "Plain words. Short sentences."
LOOK_IT_UP = "look it up"
"""A task with this asks the model for `glossary_lookup`."""
FETCH = "fetch the page"
"""A task with this asks the model for `fetch_page` (the red-team tool)."""
AFTER_TOOL = "SLM means a small language model."
GATE_MARK = "gate-check"
"""A task with this gets `BAD` on the main route; the evaluator scores `BAD` below threshold."""
BAD = "The report was signed."
GOOD = "Dana Ruiz signed the report on 4 May 2025."
PORT_LOGGER = "chassis.poc08.ports"
"""The fake ports log one line per call here, as a real adapter would, so a test can read the
chassis's JSON log lines of a run in every lane (the workload's own logs never pass the chassis)."""
log = logging.getLogger(PORT_LOGGER)
FETCH_PAGE = ToolDefinition(
    name="fetch_page",
    description="Fetch a page, or store a note",
    parameters={"type": "object", "properties": {"text": {"type": "string"}}},
)


# --- The model: one script for every engine ------------------------------------------------------


def _code_mode(messages: Sequence[ModelMessage]) -> bool:
    """The prompt asks for Python in `<code>` blocks (a smolagents `CodeAgent`). A model that
    follows the format answers in it; ADR-006 names this as the engine's model requirement."""
    return any(m.role == "system" and "final_answer" in (m.content or "") for m in messages)


def _offered(tools: Sequence[ToolSpec] | None, bare: str, code: bool) -> str | None:
    """The offered tool whose name is `bare` or ends with `__bare` (`mcp__chassis__bare`). In
    code mode the tools are in the prompt, not in `tools`."""
    if code:
        return bare
    return next((t.name for t in tools or [] if t.name.split("__")[-1] == bare), None)


def _has_result(messages: Sequence[ModelMessage], code: bool) -> bool:
    if any(m.role == "tool" for m in messages):
        return True
    users = [m.content or "" for m in messages if m.role == "user"]
    return code and any("Execution logs" in u or "Observation" in u for u in users[1:])


def _code(text: str | None, call: ToolCallRequest | None) -> str:
    if call is not None:
        args = ", ".join(f"{k}={json.dumps(v)}" for k, v in call.arguments.items())
        return f"Thought: Call the tool.\n<code>\nprint({call.name}({args}))\n</code>"
    return f"Thought: Done.\n<code>\nfinal_answer({json.dumps(text)})\n</code>"


class StageModel:
    """A `ModelPort`. On `FALLBACK_ROUTE` it answers `GOOD`. Else, by the task text: `FETCH` or
    `LOOK_IT_UP` with the tool offered and no tool result yet is a tool call; after a tool result,
    `AFTER_TOOL`; `GATE_MARK` is `BAD`; anything else is `answer`. Records every message text and
    every route it was called on."""

    name = "poc08-stage"

    def __init__(self) -> None:
        self.answer = PLAIN
        self.fetch_args: dict[str, Any] = {"text": "page"}
        self.seen: list[str] = []
        self.routes: list[str] = []

    def reset(self) -> None:
        self.answer, self.fetch_args = PLAIN, {"text": "page"}
        self.seen.clear()
        self.routes.clear()

    def seen_text(self) -> str:
        return "\n".join(self.seen)

    def _decide(
        self, messages: Sequence[ModelMessage], tools: Sequence[ToolSpec] | None, route: str
    ) -> tuple[str | None, ToolCallRequest | None]:
        code = _code_mode(messages)
        task = next((m.content or "" for m in messages if m.role == "user"), "")
        done = _has_result(messages, code)
        if route == FALLBACK_ROUTE:
            return GOOD, None
        for marker, bare, args in (
            (FETCH, "fetch_page", self.fetch_args),
            (LOOK_IT_UP, "glossary_lookup", {"term": "SLM"}),
        ):
            name = _offered(tools, bare, code)
            if marker in task and name and not done:
                return None, ToolCallRequest(call_id="call_1", name=name, arguments=dict(args))
        if done:
            return AFTER_TOOL, None
        return (BAD if GATE_MARK in task else self.answer), None

    def _result(
        self, messages: Sequence[ModelMessage], tools: Sequence[ToolSpec] | None, route: str
    ) -> ModelResult:
        self.routes.append(route)
        log.info("model call", extra={"route": route})
        self.seen.extend(m.content or "" for m in messages)
        text, call = self._decide(messages, tools, route)
        usage = Usage(input_tokens=10, output_tokens=5)
        if _code_mode(messages):
            return ModelResult(text=_code(text, call), usage=usage, model=route)
        if call is not None:
            return ModelResult(text="", tool_calls=[call], usage=usage, model=route)
        return ModelResult(text=text or "", usage=usage, model=route)

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        return self._result(messages, tools, route)

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        result = self._result(messages, tools, route)
        for word in re.findall(r"\S+\s*", result.text or ""):  # word by word, as a model streams
            yield ModelChunk(text=word)
        for call in result.tool_calls:
            yield ModelChunk(tool_call=call)
        yield ModelChunk(usage=result.usage, finish=True)


# --- Tools and guardrails ------------------------------------------------------------------------


class ToolBox:
    """`glossary_lookup` and the red-team `fetch_page`, whose output is `page`."""

    def __init__(self) -> None:
        self.page = "A plain product page."
        self.tools = InMemoryTools(
            [
                (GLOSSARY_LOOKUP, lambda term: {"term": term, "definition": "A small model."}),
                (FETCH_PAGE, lambda text="": self.page),
            ]
        )

    @property
    def calls(self) -> list[Any]:
        return list(self.tools.calls)


class RecordingGuardrails:
    """A `GuardrailPort` around another: records each stage it was asked to check."""

    def __init__(self, inner: GuardrailPort) -> None:
        self.inner = inner
        self.name = inner.name
        self.stages: list[Stage] = []

    async def check(self, text: str, stage: Stage) -> Verdict:
        self.stages.append(stage)
        log.info("guardrail check", extra={"stage": stage})
        return await self.inner.check(text, stage)


def litellm_guardrails() -> LiteLLMGuardrails:
    """The PoC-7 stack: the LiteLLM stub -> the guardrail server (pattern stand-in)."""
    guard = create_guard(PatternClassifier())
    stub = create_stub(
        StubSettings(master_key=STUB_KEY), guardrail_transport=httpx.ASGITransport(app=guard)
    )
    return LiteLLMGuardrails(
        "http://litellm.invalid/v1", STUB_KEY, transport=httpx.ASGITransport(app=stub)
    )


# --- Wiring an engine's hooks to the chassis proxy socket ---------------------------------------


class _FreshHttpx(httpx.AsyncBaseTransport):
    """A new connection per request: the lanes run the workload on other event loops."""

    def __init__(self, uds: str) -> None:
        self._uds = uds

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        inner = httpx.AsyncHTTPTransport(uds=self._uds)
        try:
            response = await inner.handle_async_request(request)
            body = await response.aread()
        finally:
            await inner.aclose()
        return httpx.Response(response.status_code, headers=response.headers.raw, content=body)


class _FreshHttpx2(httpx2.AsyncBaseTransport):
    def __init__(self, uds: str) -> None:
        self._uds = uds

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        inner = httpx2.AsyncHTTPTransport(uds=self._uds)
        try:
            response = await inner.handle_async_request(request)
            body = await response.aread()
        finally:
            await inner.aclose()
        return httpx2.Response(response.status_code, headers=response.headers.raw, content=body)


class _FreshSync(httpx2.BaseTransport):
    """The sync form, for smolagents, which closes its client (and transport) after each run."""

    def __init__(self, uds: str) -> None:
        self._uds = uds

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        inner = httpx2.HTTPTransport(uds=self._uds)
        try:
            response = inner.handle_request(request)
            body = response.read()
        finally:
            inner.close()
        return httpx2.Response(response.status_code, headers=response.headers.raw, content=body)


def _set(monkeypatch: pytest.MonkeyPatch, dotted: str, value: object) -> None:
    module, _, attr = dotted.rpartition(".")
    monkeypatch.setattr(importlib.import_module(module), attr, value)


def wire_engine(monkeypatch: pytest.MonkeyPatch, engine: str, uds: str, home: Path) -> None:
    """Point the engine's test-only model and MCP hooks at `uds`, and set the two URLs. Test
    wiring: each engine names its hooks differently (`poc06_harness.ENGINES`)."""
    monkeypatch.setenv("CHASSIS_MODEL_URL", f"{PROXY_URL}/v1")
    monkeypatch.setenv("CHASSIS_TOOL_URL", f"{PROXY_URL}/mcp")
    spec = ENGINES[engine]
    if engine == "echo-claude-agent":
        for name in list(os.environ):  # the workload's own guard refuses these
            if name.startswith(("ANTHROPIC_", "CLAUDE")):
                monkeypatch.delenv(name)
        monkeypatch.setenv("CLAUDE_AGENT_HOME_BASE", str(home))
        _set(monkeypatch, "echo_claude_agent.handle.query_fn", ClaudeCliStandIn(uds))
        return
    assert spec.model_hook is not None and spec.tool_hook is not None
    model: object = _FreshHttpx2(uds)
    if engine == "echo-python":
        model = _FreshHttpx(uds)
    elif engine == "echo-smolagents":
        model = _FreshSync(uds)
    tools = _FreshHttpx2(uds)
    tool_hook: object = tools
    if spec.tool_hook.endswith("client_factory"):

        def client_factory(headers: dict[str, str], timeout: float) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(headers=headers, timeout=timeout, transport=tools)

        tool_hook = client_factory
    _set(monkeypatch, spec.model_hook, model)
    _set(monkeypatch, spec.tool_hook, tool_hook)


# --- Servers -------------------------------------------------------------------------------------


@contextmanager
def _remote_workload(handle: str, folder: str) -> Iterator[str]:
    """The template A2A server built by its CLI with `--require-token-env` (the remote lane), in
    a thread on a Unix socket; yields the path."""
    from workload_a2a.cli import build

    uds = str(Path(folder, "w.sock"))
    argv = ["serve", "--handle", handle, "--uds", uds, "--log-level", "warning"]
    argv += ["--drain-timeout-s", "0", "--name", handle, "--version", "0.0.1"]
    argv += ["--require-token-env", WORKLOAD_TOKEN_ENV]
    server = build(argv)
    thread = threading.Thread(target=server.run, daemon=True, name="poc08-remote")
    thread.start()
    try:
        deadline = time.monotonic() + START_TIMEOUT_S
        while not server.started:
            if time.monotonic() > deadline or not thread.is_alive():
                raise RuntimeError(f"the remote workload did not start on {uds}")
            time.sleep(0.02)
        yield uds
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _config(
    engine: dict[str, Any],
    trust: str,
    harness: dict[str, Any],
    fallback: str | None,
    overrides: dict[str, Any],
) -> Any:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-poc08",
            "profile": "fake",
            "agent": {"name": "simplifier", "version": "0.0.1"},
            "spec": {
                "trust": trust,
                "adapters": {
                    "model": "fake",
                    "auth": "static",
                    "guardrails": "litellm",
                    "evaluator": "fake",
                    "feedback": "memory",
                },
                "engine": engine,
                "model": {"route": ROUTE, "fallback_route": fallback},
                "prompt": {"version": "simplifier-v1"},
                "harness": {"evaluator_gate": True, **harness},
                "governance": {"risk_class": "limited"},
                **overrides,
            },
        }
    )


@dataclass
class Stack:
    """One running chassis with every stage on, for one engine in one lane."""

    engine: str
    lane: str
    public_uds: str
    config: Any
    model: StageModel
    toolbox: ToolBox
    telemetry: InMemoryTelemetry
    guardrails: RecordingGuardrails
    evaluator: ScriptedEvaluator
    feedback: InMemoryFeedback
    auth: StaticTokens

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=self.public_uds),
            base_url="http://chassis",
            timeout=60.0,
        )

    async def run(
        self, text: str, *, token: str | None = RUNNER, stream: bool = False
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        body = {"input": {"text": text}, "stream": stream}
        async with self.client() as client:
            return await client.post("/v1/run", json=body, headers=headers)

    async def send_feedback(self, request_id: str, token: str = RUNNER) -> httpx.Response:
        body = {"request_id": request_id, "score": 0, "label": "thumbs-down"}
        async with self.client() as client:
            return await client.post(
                "/v1/feedback", json=body, headers={"Authorization": f"Bearer {token}"}
            )

    def spans(self, name: str) -> list[Any]:
        return [s for s in self.telemetry.spans if s.name == name]


def _engine_spec(
    stack: ExitStack, engine: str, lane: str, folder: str
) -> tuple[dict[str, Any], EngineConnector]:
    handle = ENGINES[engine].handle
    if lane == "inprocess":
        return {"connector": "inprocess", "handle": handle}, InProcessConnector()
    if lane == "sidecar":
        url, uds = stack.enter_context(a2a_on_unix_socket(handle))
        return {"connector": "sidecar", "url": url, "uds": uds}, SidecarConnector()
    if lane == "remote":
        uds = stack.enter_context(_remote_workload(handle, folder))
        auth = {"scheme": "bearer", "token_env": REMOTE_TOKEN_ENV}
        spec = {"connector": "remote", "url": REMOTE_URL, "uds": uds, "auth": auth}
        return spec, RemoteConnector()
    raise ValueError(f"unknown lane {lane!r}; one of {LANES}")


@contextmanager
def full_stack(
    engine: str,
    lane: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    retries: int = 1,
    threshold: float = 0.7,
    fallback_route: str | None = FALLBACK_ROUTE,
    limits: dict[str, Any] | None = None,
    guardrails_spec: dict[str, Any] | None = None,
) -> Iterator[Stack]:
    """The chassis with every stage on for `engine` in `lane` (see the module docstring).
    `limits` and `guardrails_spec` set `spec.limits` and `spec.guardrails`; unset, the chassis
    defaults apply (the PoC-8 controls: rate limit, stream secret check, URL allow-list on)."""
    if (engine, lane) not in CELLS:
        raise ValueError(f"{engine} in {lane} is not a PoC-8 cell")
    folder = tempfile.mkdtemp(prefix="p8-")  # short: macOS caps a socket path at 104 bytes
    public_uds, proxy_uds = str(Path(folder, "pub.sock")), str(Path(folder, "px.sock"))
    if lane == "remote":
        Tokens.one().apply(monkeypatch)
    wire_engine(monkeypatch, engine, proxy_uds, Path(folder, "home"))
    with ExitStack() as stack:
        stack.callback(shutil.rmtree, folder, ignore_errors=True)
        spec, connector = _engine_spec(stack, engine, lane, folder)
        trust = "untrusted" if lane == "remote" else "trusted"
        harness = {"retries": retries, "threshold": threshold}
        overrides = {
            key: value
            for key, value in (("limits", limits), ("guardrails", guardrails_spec))
            if value is not None
        }
        config = _config(spec, trust, harness, fallback_route, overrides)
        model, toolbox, telemetry = StageModel(), ToolBox(), InMemoryTelemetry()
        guardrails = RecordingGuardrails(litellm_guardrails())
        evaluator = ScriptedEvaluator([ScoreRule(match=BAD, score=0.1)], default=1.0)
        feedback, auth = InMemoryFeedback(), StaticTokens(TOKENS)
        ports = PortBundle(
            model=model,
            engine=connector,
            config=InMemoryConfig(),
            telemetry=telemetry,
            tools=toolbox.tools,
            auth=auth,
            guardrails=guardrails,
            evaluator=evaluator,
            feedback=feedback,
        )
        public = create_app(config, ports)
        if lane == "remote":
            auth_spec = config.spec.engine.auth
            assert auth_spec is not None
            proxy = create_remote_proxy_app(public, remote_tokens(auth_spec.model_dump()))
        else:
            proxy = create_proxy_app(public)
        stack.enter_context(
            serve_on_unix_sockets(
                [UnixApp(public, public_uds), UnixApp(proxy, proxy_uds, lifespan=False)]
            )
        )
        yield Stack(
            engine,
            lane,
            public_uds,
            config,
            model,
            toolbox,
            telemetry,
            guardrails,
            evaluator,
            feedback,
            auth,
        )


def cell_params() -> list[Any]:
    """Every PoC-8 cell as a pytest param, id `engine-lane`."""
    return [pytest.param(e, lane, id=f"{e}-{lane}") for e, lane in CELLS]
