"""`full_stack(engine, lane, monkeypatch, ...)` starts one chassis with every PoC-7/8 stage on at
once, for one engine in one lane:

- auth: `StaticTokens`; `RUNNER` holds the run and feedback scopes, `READER` only the read scope.
- guardrails: `LiteLLMGuardrails` -> the LiteLLM stub -> the guardrail server with the pattern
  stand-in (the security config), wrapped in `RecordingGuardrails` so a test sees which stage ran.
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

import shutil
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from chassis.adapters.a2a import InProcessConnector
from chassis.adapters.a2a.remote import RemoteConnector
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.fakes import InMemoryConfig, InMemoryTelemetry
from chassis.fakes.auth import StaticTokens
from chassis.fakes.evaluator import ScoreRule, ScriptedEvaluator
from chassis.fakes.feedback import InMemoryFeedback
from chassis.ports.auth import Principal
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from chassis.server.remote_auth import create_remote_proxy_app, remote_tokens
from chassis_contracts.interface import UnixApp, serve_on_unix_sockets
from fastapi import FastAPI

from poc_harness.bakeoff_tasks import ENGINES
from poc_harness.engine_wiring import wire_engine
from poc_harness.lane_tokens import WORKLOAD_TOKEN_ENV, Tokens
from poc_harness.servers import a2a_on_unix_socket
from poc_harness.stage_model import BAD, FALLBACK_ROUTE, ROUTE, StageModel
from poc_harness.stage_ports import RecordingGuardrails, ToolBox, litellm_guardrails

TRUSTED_ENGINES = ("echo-python", "echo-pydanticai", "echo-langgraph", "echo-openai-agents")
"""ADR-006: the supported trusted Python engines, each in `inprocess` and `sidecar`."""
REMOTE_ENGINES = ("echo-python", "echo-smolagents", "echo-claude-agent")
"""The `remote` lane: the PoC-5 baseline and the two untrusted engines of ADR-006."""
CELLS: tuple[tuple[str, str], ...] = (
    *((e, lane) for e in TRUSTED_ENGINES for lane in ("inprocess", "sidecar")),
    *((e, "remote") for e in REMOTE_ENGINES),
)
LANES = ("inprocess", "sidecar", "remote")

RUNNER = "tok-poc08-runner"
READER = "tok-poc08-reader"
OTHER = "tok-poc08-other"
"""A second caller with the run and feedback scopes: the rate limit is per caller."""
TOKENS = {
    RUNNER: Principal("user-run", frozenset({"agent:run", "agent:feedback"})),
    READER: Principal("user-read", frozenset({"agent:read"})),
    OTHER: Principal("user-other", frozenset({"agent:run", "agent:feedback"})),
}
REMOTE_URL = "http://echo-remote:9000"
REMOTE_TOKEN_ENV = "POC05_REMOTE_TOKEN"
START_TIMEOUT_S = 15.0


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


@dataclass(frozen=True)
class _Stages:
    """The ports of one stack, each one a double a test can read."""

    model: StageModel
    toolbox: ToolBox
    telemetry: InMemoryTelemetry
    guardrails: RecordingGuardrails
    evaluator: ScriptedEvaluator
    feedback: InMemoryFeedback
    auth: StaticTokens

    def ports(self, connector: EngineConnector) -> PortBundle:
        return PortBundle(
            model=self.model,
            engine=connector,
            config=InMemoryConfig(),
            telemetry=self.telemetry,
            tools=self.toolbox.tools,
            auth=self.auth,
            guardrails=self.guardrails,
            evaluator=self.evaluator,
            feedback=self.feedback,
        )


def _stages() -> _Stages:
    return _Stages(
        StageModel(),
        ToolBox(),
        InMemoryTelemetry(),
        RecordingGuardrails(litellm_guardrails()),
        ScriptedEvaluator([ScoreRule(match=BAD, score=0.1)], default=1.0),
        InMemoryFeedback(),
        StaticTokens(TOKENS),
    )


def _proxy_app(public: FastAPI, config: Any, lane: str) -> FastAPI:
    if lane != "remote":
        return create_proxy_app(public)
    auth_spec = config.spec.engine.auth
    assert auth_spec is not None
    return create_remote_proxy_app(public, remote_tokens(auth_spec.model_dump()))


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
        parts = _stages()
        public = create_app(config, parts.ports(connector))
        stack.enter_context(
            serve_on_unix_sockets(
                [
                    UnixApp(public, public_uds),
                    UnixApp(_proxy_app(public, config, lane), proxy_uds, lifespan=False),
                ]
            )
        )
        yield Stack(engine, lane, public_uds, config, **vars(parts))


def cell_params() -> list[Any]:
    """Every PoC-8 cell as a pytest param, id `engine-lane`."""
    return [pytest.param(e, lane, id=f"{e}-{lane}") for e, lane in CELLS]
