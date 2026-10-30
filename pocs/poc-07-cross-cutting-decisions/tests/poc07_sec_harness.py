"""Shared harness for the PoC-7 security scenarios (W2). Not a test module.

`secured_chassis(engine, lane, ...)` is PoC-3's `chassis_on_unix_sockets` with the PoC-7 ports
set: an auth port (`StaticTokens`), a guardrail port, a tools port, and a scripted model. The
public app and the proxy app run on Unix sockets; the workload is a real engine (`echo_python`,
`echo_langgraph`) behind `inprocess` or `sidecar` (A2A over a Unix socket). Its model and MCP calls
reach the chassis proxy socket through PoC-3's `OutboundRouter`. No TCP, no key.

The chassis code under test has no engine-specific branch: the same app, the same ports, and only
`spec.engine` differs per cell.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from chassis.adapters.a2a import InProcessConnector
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.fakes import InMemoryConfig, InMemoryTelemetry
from chassis.fakes.auth import StaticTokens
from chassis.fakes.tool import GLOSSARY_LOOKUP, InMemoryTools
from chassis.ports.auth import Principal
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis.ports.guardrail import GuardrailPort
from chassis.ports.model import (
    ModelChunk,
    ModelMessage,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from chassis_contracts.interface import UnixApp, serve_on_unix_sockets
from poc03_harness import OutboundRouter
from poc_harness.servers import a2a_on_unix_socket

ENGINES = {"echo-python": "echo_python:handle", "echo-langgraph": "echo_langgraph:handle"}
LANES = ("inprocess", "sidecar")
ROUTE = "big-default"
RUNNER = "tok-runner"
READER = "tok-reader"
TOKENS = {
    RUNNER: Principal("user-run", frozenset({"agent:run"})),
    READER: Principal("user-read", frozenset({"agent:read"})),
}
LOOK_IT_UP = "look it up"
"""A user text with this asks the model for the tool."""


class AnswerModel:
    """A `ModelPort`: answers `answer`; when the user text says `LOOK_IT_UP` and the tool is
    offered and has not answered yet, calls `glossary_lookup`. Records every message it saw."""

    name = "answer"

    def __init__(self, answer: str = "Plain words. Short sentences.") -> None:
        self.answer = answer
        self.seen: list[ModelMessage] = []

    def _result(
        self, messages: Sequence[ModelMessage], tools: Sequence[ToolSpec] | None
    ) -> ModelResult:
        self.seen.extend(messages)
        usage = Usage(input_tokens=10, output_tokens=5)
        offered = {t.name for t in tools or []}
        users = " ".join(m.content or "" for m in messages if m.role == "user")
        has_tool = any(m.role == "tool" for m in messages)
        if LOOK_IT_UP in users and "glossary_lookup" in offered and not has_tool:
            call = ToolCallRequest(
                call_id="call_1", name="glossary_lookup", arguments={"term": "SLM"}
            )
            return ModelResult(text="", tool_calls=[call], usage=usage, model=ROUTE)
        return ModelResult(text=self.answer, usage=usage, model=ROUTE)

    def tool_texts(self) -> list[str]:
        return [m.content or "" for m in self.seen if m.role == "tool"]

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        return self._result(messages, tools)

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        result = self._result(messages, tools)
        if result.text:
            yield ModelChunk(text=result.text)
        for call in result.tool_calls:
            yield ModelChunk(tool_call=call)
        yield ModelChunk(usage=result.usage, finish=True)


def glossary_answering(text: str) -> InMemoryTools:
    """The glossary tool, answering `text` for every term (an injected tool output)."""
    return InMemoryTools([(GLOSSARY_LOOKUP, lambda term: {"term": term, "definition": text})])


@dataclass
class Secured:
    public_uds: str
    model: AnswerModel
    telemetry: InMemoryTelemetry
    auth: StaticTokens

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=self.public_uds),
            base_url="http://chassis",
            timeout=30.0,
        )


def _config(spec: dict[str, Any]) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-poc7-sec",
            "profile": "fake",
            "agent": {"name": "simplifier", "version": "0.0.1"},
            "spec": {
                "adapters": {"model": "fake"},
                "engine": spec,
                "model": {"route": ROUTE},
                "prompt": {"version": "simplifier-v1"},
            },
        }
    )


@contextmanager
def secured_chassis(
    engine: str,
    lane: str,
    *,
    guardrails: GuardrailPort,
    router: OutboundRouter,
    model: AnswerModel | None = None,
    tools: InMemoryTools | None = None,
) -> Iterator[Secured]:
    handle = ENGINES[engine]
    folder = tempfile.mkdtemp(prefix="poc07-")
    public_uds = str(Path(folder, "public.sock"))
    proxy_uds = str(Path(folder, "proxy.sock"))
    with ExitStack() as stack:
        stack.callback(shutil.rmtree, folder, ignore_errors=True)
        connector: EngineConnector
        if lane == "inprocess":
            spec: dict[str, Any] = {"connector": "inprocess", "handle": handle}
            connector = InProcessConnector()
        else:
            url, uds = stack.enter_context(a2a_on_unix_socket(handle))
            spec, connector = {"connector": "sidecar", "url": url, "uds": uds}, SidecarConnector()
        model = model or AnswerModel()
        telemetry = InMemoryTelemetry()
        auth = StaticTokens(TOKENS)
        ports = PortBundle(
            model=model,
            engine=connector,
            config=InMemoryConfig(),
            telemetry=telemetry,
            tools=tools or glossary_answering("A small language model."),
            auth=auth,
            guardrails=guardrails,
        )
        public = create_app(_config(spec), ports)
        proxy = create_proxy_app(public)
        router.add(public, proxy_uds)
        stack.callback(router.remove, public)
        stack.enter_context(
            serve_on_unix_sockets(
                [UnixApp(public, public_uds), UnixApp(proxy, proxy_uds, lifespan=False)]
            )
        )
        yield Secured(public_uds, model, telemetry, auth)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
