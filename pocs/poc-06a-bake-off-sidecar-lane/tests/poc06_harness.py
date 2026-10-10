"""Shared harness for the PoC-6 bake-off tests. Not a test module; the test files import it.

Holds the three benchmark tasks as data, a pass check for each, the engine registry, and an
in-process tool stub with both read-only tools. Everything runs offline: the model is the fake
model server on `scripts/bakeoff.yaml`, the tools are `fake_mcp_server` over an ASGI transport.
The chassis is never imported here: a workload is driven through its wire-form `handle`.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import jsonschema
from fake_mcp_server.server import FakeMcpState, create_app

ROOT = Path(__file__).resolve().parents[3]
BAKEOFF_SCRIPT = ROOT / "packages/fake-model-server/scripts/bakeoff.yaml"
EVENTS_SCHEMA = json.loads((ROOT / "packages/chassis/schemas/events.v0.json").read_text())
ROUTE = "big-default"
MODEL_URL = "http://model.invalid/v1"
TOOL_URL = "http://tools.invalid/mcp/"
"""The fake MCP app serves `/mcp/`; the trailing slash avoids a redirect."""

# --- The task spec, as data ---------------------------------------------------------------------


@dataclass(frozen=True)
class ToolStep:
    """One expected tool call: the tool, its arguments, and a text its result must hold."""

    name: str
    arguments: Mapping[str, str]
    result_key: str
    result_contains: str


@dataclass(frozen=True)
class Task:
    """A benchmark task: the input text, the facts the answer must hold, and the tool calls the
    run must make, in order. `exact_text`, when set, is the whole answer."""

    name: str
    text: str
    facts: tuple[str, ...]
    tool_calls: tuple[ToolStep, ...] = ()
    exact_text: str | None = None


SMOKE = Task(name="smoke", text="simplify: Hello.", facts=("Hello.",), exact_text="Hello.")
SIMPLIFIER = Task(
    name="simplifier",
    text=(
        "simplify: The SLM was released by Acme in 2026, thereby reducing operating costs "
        "by approximately 30 percent."
    ),
    facts=("SLM", "Acme", "2026", "30 percent"),
)
LOOKUP = Task(
    name="lookup",
    text="lookup: What does SLM mean, and what does RAG stand for?",
    facts=("small language model", "retrieval-augmented generation"),
    tool_calls=(
        ToolStep("glossary_lookup", {"term": "SLM"}, "definition", "small language model"),
        ToolStep(
            "acronym_expand", {"acronym": "RAG"}, "expansion", "retrieval-augmented generation"
        ),
    ),
)
TASKS: tuple[Task, ...] = (SMOKE, SIMPLIFIER, LOOKUP)
TOOL_NAMES = ("glossary_lookup", "acronym_expand")
"""The two read-only tools the lookup task uses."""

# --- Pass checks --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """The outcome of one pass check: `problems` is empty when the task passed."""

    task: str
    problems: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.problems


def answer_text(events: Sequence[Mapping[str, Any]]) -> str:
    """The streamed answer: every `delta` text, joined."""
    return "".join(str(e.get("text", "")) for e in events if e.get("type") == "delta")


def _check(task: Task, events: Sequence[Mapping[str, Any]]) -> Verdict:
    problems: list[str] = []
    types = [e.get("type") for e in events]
    for index, event in enumerate(events):
        try:
            jsonschema.validate(dict(event), EVENTS_SCHEMA)
        except jsonschema.ValidationError as exc:
            problems.append(f"event {index} breaks the schema: {exc.message}")
    if "error" in types:
        errors = [e for e in events if e.get("type") == "error"]
        problems.append(f"error event: {errors[0].get('code')}: {errors[0].get('message')}")
    if not types or types[0] != "start":
        problems.append(f"the first event is {types[:1]}, not start")
    end = events[-1] if events else {}
    if end.get("type") != "end" or end.get("status") != "ok":
        problems.append(f"the last event is {dict(end)}, not end/ok")
    text = answer_text(events)
    if task.exact_text is not None and text.strip() != task.exact_text:
        problems.append(f"answer {text!r}, want exactly {task.exact_text!r}")
    problems.extend(
        f"answer lacks the fact {fact!r}: {text!r}"
        for fact in task.facts
        if fact.casefold() not in text.casefold()
    )
    calls = [e for e in events if e.get("type") == "tool_call"]
    got = [(str(c.get("name")), dict(c.get("arguments") or {})) for c in calls]
    want = [(s.name, dict(s.arguments)) for s in task.tool_calls]
    if got != want:
        problems.append(f"tool calls {got}, want {want}")
    for call, step in zip(calls, task.tool_calls, strict=False):
        value = (call.get("result") or {}).get(step.result_key)
        if not isinstance(value, str) or step.result_contains.casefold() not in value.casefold():
            problems.append(
                f"{step.name} result {call.get('result')}, want {step.result_contains!r}"
            )
    return Verdict(task.name, tuple(problems))


def check_smoke(events: Sequence[Mapping[str, Any]]) -> Verdict:
    """Smoke: a valid run whose whole answer is `Hello.` and that calls no tool."""
    return _check(SMOKE, events)


def check_simplifier(events: Sequence[Mapping[str, Any]]) -> Verdict:
    """Simplifier: a valid run whose answer keeps every fact and calls no tool."""
    return _check(SIMPLIFIER, events)


def check_lookup(events: Sequence[Mapping[str, Any]]) -> Verdict:
    """Lookup: a valid run that calls `glossary_lookup` then `acronym_expand`, each with a
    non-null result, and answers with both facts."""
    return _check(LOOKUP, events)


CHECKS: dict[str, Callable[[Sequence[Mapping[str, Any]]], Verdict]] = {
    "smoke": check_smoke,
    "simplifier": check_simplifier,
    "lookup": check_lookup,
}

# --- The engine registry ------------------------------------------------------------------------


@dataclass(frozen=True)
class Engine:
    """One PoC-6 engine. `handle` is `module:function`, or `"node"` for the TypeScript agent
    (a separate process; no module hooks). `model_hook` and `tool_hook` are the dotted names of
    the test-only module attributes that route the model and the MCP calls to an in-process
    transport, or None. echo-python's tool hook is a client factory; the others hold a transport.
    """

    name: str
    handle: str
    language: str
    trust: str
    lane: str
    model_hook: str | None
    tool_hook: str | None
    hooks_extra: tuple[str, ...] = field(default=())


ENGINES: dict[str, Engine] = {
    e.name: e
    for e in (
        Engine(
            "echo-python",
            "echo_python:handle",
            "python",
            "trusted",
            "sidecar",
            "echo_python.handle.transport",
            "echo_python.tools.client_factory",
        ),
        Engine(
            "echo-pydanticai",
            "echo_pydanticai:handle",
            "python",
            "trusted",
            "sidecar",
            "echo_pydanticai.handle.model_transport",
            "echo_pydanticai.handle.tool_transport",
        ),
        Engine(
            "echo-langgraph",
            "echo_langgraph:handle",
            "python",
            "trusted",
            "sidecar",
            "echo_langgraph.handle.model_transport",
            "echo_langgraph.tools.transport",
        ),
        Engine(
            "echo-openai-agents",
            "echo_openai_agents:handle",
            "python",
            "trusted",
            "sidecar",
            "echo_openai_agents.handle.transport",
            "echo_openai_agents.tools.transport",
        ),
        Engine("echo-typescript", "node", "typescript", "trusted", "sidecar", None, None),
        Engine(
            "echo-claude-agent",
            "echo_claude_agent:handle",
            "python",
            "untrusted",
            "remote",
            "echo_claude_agent.handle.transport",
            "echo_claude_agent.tools.transport",
        ),
        Engine(
            "echo-smolagents",
            "echo_smolagents:handle",
            "python",
            "untrusted",
            "remote",
            "echo_smolagents.handle.transport",
            "echo_smolagents.tools.transport",
        ),
    )
}
"""Keyed by workload name. Trust `trusted` runs in the `sidecar` lane; `untrusted`, `remote`."""

# --- Running a handle ---------------------------------------------------------------------------

CTX: dict[str, Any] = {
    "request_id": "req-poc06",
    "trace_id": "0af7651916cd43dd8448eb211c80319c",
    "idempotency_key": "idem-poc06",
    "agent": "echo",
    "agent_version": "0.0.1",
    "budget": {"max_tokens": 2000, "timeout_ms": 30000},
    "versions": {"chassis": "0.1.0"},
    "model_route": ROUTE,
}


def load_handle(path: str) -> Callable[[dict[str, Any], dict[str, Any]], AsyncIterator[Any]]:
    """Import `module:function`."""
    module, _, attr = path.partition(":")
    handle: Callable[[dict[str, Any], dict[str, Any]], AsyncIterator[Any]] = getattr(
        importlib.import_module(module), attr
    )
    return handle


async def collect(path: str, text: str) -> list[dict[str, Any]]:
    """Call a workload's wire-form `handle` and collect its events as dicts."""
    return [dict(raw) async for raw in load_handle(path)({"text": text}, dict(CTX))]


# --- The two-tool stub --------------------------------------------------------------------------


@dataclass
class ToolStub:
    """The fake MCP server (`fake_mcp_server`) in process: both read-only tools, no socket."""

    app: Any
    state: FakeMcpState

    def transport(self) -> httpx2.AsyncBaseTransport:
        """For a workload whose hook is an `httpx2` transport."""
        return httpx2.ASGITransport(app=self.app)

    def client_factory(self, headers: dict[str, str], timeout: float) -> httpx2.AsyncClient:
        """For echo-python's `tools.client_factory`."""
        return httpx2.AsyncClient(headers=headers, timeout=timeout, transport=self.transport())

    @property
    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        """The tool calls the server received: (tool, arguments)."""
        return [(str(c["tool"]), dict(c["arguments"])) for c in self.state.calls]


@asynccontextmanager
async def two_tool_stub() -> AsyncIterator[ToolStub]:
    """Start the fake MCP app's lifespan and yield the stub; point `CHASSIS_TOOL_URL` at
    `TOOL_URL`."""
    state = FakeMcpState()
    app = create_app(state)
    async with app.router.lifespan_context(app):
        yield ToolStub(app=app, state=state)


def engine_names(*, trust: str | None = None, lane: str | None = None) -> Iterable[str]:
    """Registry names, optionally of one trust level or lane."""
    return [
        n
        for n, e in ENGINES.items()
        if (trust is None or e.trust == trust) and (lane is None or e.lane == lane)
    ]
