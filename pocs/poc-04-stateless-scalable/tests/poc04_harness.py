"""Shared harness for the PoC-4 scenario tests: N chassis replicas behind one store. Not a test
module; the tests import it.

Builds on `poc03_harness` (the outbound router, the agent, the engines) and `poc02_harness` (the
workload template server on a Unix socket, the TypeScript echo):

- **Replicas.** `replicas_on_unix_sockets(n, target, lane, ...)` serves N chassis, each a public
  app and a proxy app on their own Unix sockets, all on one event loop
  (`chassis_contracts.interface.serve_on_unix_sockets`). One loop matters: `InMemoryState` is
  atomic on one loop, and it stands for Valkey here. No TCP anywhere.
- **Shared ports.** `state` and `events` are one object for every replica (what a shared Valkey
  or broker is), or a sequence with one per replica (what a real adapter built per replica from
  the same config is). Defaults: one `InMemoryState`, one `InMemoryBus`.
- **One workload per replica.** In the `sidecar` lane each replica gets its own workload server
  (the template A2A server for the Python echoes, with a `RecordingTaskStore`; Node for the
  TypeScript echo). In the `inprocess` lane the handle is loaded by path in each replica.
- **One model for all.** The `HarnessModel` is shared, so `len(model.calls)` counts the model
  calls of every replica. `Replicas.engine_runs()` counts `chassis.engine.run` spans.
- **A kill switch.** `SeverableState` wraps the shared store for one replica; `sever()` makes
  every later call from that replica fail, as after SIGKILL: its lease is no longer renewed, and
  its finish and release never land.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any

import httpx2
from a2a.server.tasks import InMemoryTaskStore
from chassis.adapters.a2a import InProcessConnector, build_agent_card
from chassis.adapters.a2a.inprocess import load_handle
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel, default_tools
from chassis.fakes.events import InMemoryBus
from chassis.fakes.model import ScriptRule
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis.ports.events import EventPort
from chassis.ports.model import ModelChunk, ModelMessage, ModelResult, ToolSpec
from chassis.ports.state import InMemoryState, StatePort, StateUnavailable
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from chassis_contracts.interface import BASE_URL, Chassis, UnixApp, serve_on_unix_sockets
from poc02_harness import CHASSIS_PROXY_URL, ROUTE, SIDECAR_URL, on_unix_socket, typescript_echo
from poc03_harness import AGENT, ENGINES, SIMPLIFIED, SIMPLIFY, TYPESCRIPT, OutboundRouter

__all__ = [
    "AGENT",
    "ENGINES",
    "FAIL",
    "HANG",
    "SIMPLIFIED",
    "SIMPLIFY",
    "SLOW",
    "TYPESCRIPT",
    "HarnessModel",
    "OffsetClock",
    "RecordingTaskStore",
    "Replicas",
    "SeverableState",
    "Workload",
    "call",
    "replica_config",
    "replicas_on_unix_sockets",
    "sse_events",
    "without_created",
]

SLOW = "simplify: SLOW"
"""A prompt the model answers after `HarnessModel.slow_s`."""
HANG = "simplify: HANG"
"""A prompt the model never answers while `HarnessModel.hang_armed` is set (once)."""
FAIL = "simplify: FAIL"
"""A prompt the model answers with a retryable error."""
CALL_TIMEOUT_S = 30.0


class OffsetClock:
    """`time.monotonic()` plus an offset the test moves forward. For `InMemoryState(clock=...)`."""

    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.monotonic() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


class HarnessModel(ScriptedModel):
    """`ScriptedModel` answering `SIMPLIFIED` to anything, `FAIL` with a retryable error, `SLOW`
    after `slow_s`, and `HANG` never while `hang_armed` is set (the first such call disarms it, so
    a retry of the same input is answered). Runs on the replicas' loop, in another thread, so its
    signals are `threading.Event`s.
    """

    def __init__(self, slow_s: float = 0.4) -> None:
        super().__init__(
            [
                ScriptRule(match=FAIL, error="model.overloaded", retryable=True),
                ScriptRule(match=None, reply=SIMPLIFIED),
            ]
        )
        self.slow_s = slow_s
        self.hang_armed = threading.Event()
        self.hung = threading.Event()

    async def _wait(self, messages: Sequence[ModelMessage]) -> None:
        text = " ".join(m.content or "" for m in messages if m.role == "user")
        if HANG in text and self.hang_armed.is_set():
            self.hang_armed.clear()
            self.hung.set()
            await asyncio.Event().wait()  # until the run is cancelled
        if SLOW in text:
            await asyncio.sleep(self.slow_s)

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        await self._wait(messages)
        return await super().complete(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        )

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        await self._wait(messages)
        async for chunk in super().stream(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        ):
            yield chunk

    def user_texts(self) -> list[str]:
        """Every model call's user and assistant texts joined, one string per call."""
        return [
            " ".join(m.content or "" for m in call if m.role in ("user", "assistant"))
            for call in self.calls
        ]


class SeverableState:
    """One replica's view of the shared store. After `sever()` every call raises
    `StateUnavailable`, so nothing this replica does reaches the store any more: a killed process.
    """

    def __init__(self, inner: StatePort) -> None:
        self.inner = inner
        self.severed = False

    def sever(self) -> None:
        self.severed = True

    def _check(self) -> None:
        if self.severed:
            raise StateUnavailable("replica killed")

    async def get(self, key: str) -> bytes | None:
        self._check()
        return await self.inner.get(key)

    async def set(self, key: str, value: bytes, *, ttl_s: float | None = None) -> None:
        self._check()
        await self.inner.set(key, value, ttl_s=ttl_s)

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        self._check()
        return await self.inner.set_if_absent(key, value, ttl_s=ttl_s)

    async def compare_and_set(
        self, key: str, expected: bytes, value: bytes | None, *, ttl_s: float | None = None
    ) -> bool:
        self._check()
        return await self.inner.compare_and_set(key, expected, value, ttl_s=ttl_s)

    async def delete(self, key: str) -> None:
        self._check()
        await self.inner.delete(key)

    async def aclose(self) -> None:
        return None  # the shared store outlives every replica


class RecordingTaskStore(InMemoryTaskStore):
    """The A2A server's task store, with the ids of the tasks it holds in a plain set the test
    thread can read (the store itself lives on the workload's loop)."""

    def __init__(self) -> None:
        super().__init__()
        self.live: set[str] = set()
        self.ever: set[str] = set()

    async def save(self, task: Any, *args: Any, **kwargs: Any) -> None:
        await super().save(task, *args, **kwargs)
        self.live.add(task.id)
        self.ever.add(task.id)

    async def delete(self, task_id: str, *args: Any, **kwargs: Any) -> None:
        await super().delete(task_id, *args, **kwargs)
        self.live.discard(task_id)


@dataclass
class Workload:
    """One workload instance: its socket, and for the Python echoes its task store."""

    uds: str
    task_store: RecordingTaskStore | None = None


@dataclass
class Replicas:
    """The running replicas, in order, and what they share."""

    chassis: list[Chassis]
    apps: list[Any]
    workloads: list[Workload | None]
    model: HarnessModel
    states: list[StatePort]
    events: list[EventPort]

    def __getitem__(self, index: int) -> Chassis:
        return self.chassis[index]

    def engine_runs(self) -> int:
        """`chassis.engine.run` spans on every replica: one per call that reached the engine."""
        return sum(
            1
            for c in self.chassis
            for span in c.telemetry.spans
            if span.name == "chassis.engine.run"
        )

    def counter(self, name: str, **labels: Any) -> int:
        """`name` summed over every replica; with `labels`, only the series that match them."""
        total = 0
        for c in self.chassis:
            for (counter, series), value in c.telemetry.counters.items():
                if counter == name and all(dict(series).get(k) == v for k, v in labels.items()):
                    total += value
        return total


def replica_config(
    engine: Mapping[str, Any],
    *,
    adapters: Mapping[str, str] | None = None,
    idempotency: Mapping[str, Any] | None = None,
    events: Mapping[str, Any] | None = None,
) -> ChassisConfig:
    """The served agent (`poc03_harness.AGENT`), every interface on."""
    spec: dict[str, Any] = {
        "adapters": {"model": "fake", **(adapters or {})},
        "engine": dict(engine),
        "model": {"route": ROUTE},
        "prompt": {"version": "simplifier-v1"},
    }
    if idempotency is not None:
        spec["idempotency"] = dict(idempotency)
    if events is not None:
        spec["events"] = dict(events)
    return ChassisConfig.model_validate(
        {
            "version": "cfg-poc4",
            "profile": "fake",
            "agent": {"name": AGENT, "version": "0.0.1"},
            "spec": spec,
        }
    )


@contextmanager
def _python_workload(handle: str) -> Iterator[Workload]:
    from workload_a2a.server import build_app

    store = RecordingTaskStore()
    card = build_agent_card(name=handle, version="0.0.1", url=SIDECAR_URL)
    with on_unix_socket(build_app(load_handle(handle), card, task_store=store), "a2a.sock") as uds:
        yield Workload(uds, store)


def _each[T](value: T | Sequence[T] | None, n: int, default: Callable[[], T]) -> list[T]:
    """One object for every replica, or one per replica from a list or tuple."""
    if isinstance(value, list | tuple):
        if len(value) != n:
            raise ValueError(f"{len(value)} values for {n} replicas")
        return list(value)
    one: T = default() if value is None else value  # type: ignore[assignment]
    return [one] * n


@contextmanager
def replicas_on_unix_sockets(
    n: int,
    target: str,
    lane: str,
    *,
    model: HarnessModel,
    router: OutboundRouter,
    state: StatePort | Sequence[StatePort] | None = None,
    events: EventPort | Sequence[EventPort] | None = None,
    adapters: Mapping[str, str] | None = None,
    idempotency: Mapping[str, Any] | None = None,
    events_spec: Mapping[str, Any] | None = None,
    extra_apps: Callable[[Replicas], Sequence[UnixApp]] | None = None,
) -> Iterator[Replicas]:
    """`n` running replicas of `target` (an engine label or a handle path) in `lane`.

    `state` and `events`: one object shared by all, or one per replica. `adapters`,
    `idempotency`, and `events_spec` go into each replica's config as `spec.adapters`,
    `spec.idempotency`, and `spec.events`. `extra_apps` adds apps served on the same loop (a test
    listener, say), built once the replicas exist.
    """
    states: list[StatePort] = _each(state, n, InMemoryState)
    events_list: list[EventPort] = _each(events, n, InMemoryBus)
    folder = tempfile.mkdtemp(prefix="poc04-")
    with ExitStack() as stack:
        stack.callback(shutil.rmtree, folder, ignore_errors=True)
        apps: list[UnixApp] = []
        chassis: list[Chassis] = []
        publics: list[Any] = []
        workloads: list[Workload | None] = []
        for i in range(n):
            public_uds = os.path.join(folder, f"p{i}.sock")
            proxy_uds = os.path.join(folder, f"x{i}.sock")
            connector: EngineConnector
            workload: Workload | None = None
            if target == TYPESCRIPT:
                if lane != "sidecar":
                    raise ValueError(f"{TYPESCRIPT} runs in the sidecar lane only")
                ts_uds = os.path.join(folder, f"t{i}.sock")
                env = {
                    "CHASSIS_MODEL_URL": f"{CHASSIS_PROXY_URL}/v1",
                    "CHASSIS_MODEL_UDS": proxy_uds,
                }
                url = stack.enter_context(typescript_echo(uds=ts_uds, env=env))
                engine: dict[str, Any] = {"connector": "sidecar", "url": url, "uds": ts_uds}
                connector, workload = SidecarConnector(), Workload(ts_uds)
            else:
                handle = ENGINES.get(target) or target
                if lane == "inprocess":
                    engine = {"connector": "inprocess", "handle": handle}
                    connector = InProcessConnector()
                elif lane == "sidecar":
                    workload = stack.enter_context(_python_workload(handle))
                    engine = {"connector": "sidecar", "url": SIDECAR_URL, "uds": workload.uds}
                    connector = SidecarConnector()
                else:
                    raise ValueError(f"unknown lane {lane!r}")
            config = replica_config(
                engine, adapters=adapters, idempotency=idempotency, events=events_spec
            )
            telemetry = InMemoryTelemetry()
            ports = PortBundle(
                model=model,
                engine=connector,
                config=InMemoryConfig(),
                telemetry=telemetry,
                tools=default_tools(),
                state=states[i],
                events=events_list[i],
            )
            public = create_app(config, ports)
            proxy = create_proxy_app(public)
            router.add(public, proxy_uds)
            stack.callback(router.remove, public)
            apps += [UnixApp(public, public_uds), UnixApp(proxy, proxy_uds, lifespan=False)]
            chassis.append(Chassis(public_uds, telemetry, config))
            publics.append(public)
            workloads.append(workload)
        running = Replicas(chassis, publics, workloads, model, states, events_list)
        if extra_apps is not None:
            apps += list(extra_apps(running))
        stack.enter_context(serve_on_unix_sockets(apps))
        yield running


# --- calls -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reply:
    """One HTTP answer: status, headers (lowercase names), and the body as JSON or SSE events."""

    status: int
    headers: dict[str, str]
    body: Any

    @property
    def replayed(self) -> bool:
        return self.headers.get("idempotent-replayed") == "true"


def sse_events(text: str) -> list[tuple[str | None, Any]]:
    """`(event name or None, data as JSON, or the raw string when it is not JSON)` per frame."""
    out: list[tuple[str | None, Any]] = []
    for frame in text.replace("\r\n", "\n").split("\n\n"):
        lines = frame.strip().splitlines()
        name = next((line[6:].strip() for line in lines if line.startswith("event:")), None)
        data = "\n".join(line[5:].lstrip() for line in lines if line.startswith("data:"))
        if not data:
            continue
        try:
            out.append((name, json.loads(data)))
        except json.JSONDecodeError:
            out.append((name, data))
    return out


def without_created(value: Any) -> Any:
    """`value` with every `created` key dropped: a replayed chat format gets a fresh one."""
    if isinstance(value, dict):
        return {k: without_created(v) for k, v in value.items() if k != "created"}
    if isinstance(value, list | tuple):
        return [without_created(v) for v in value]
    return value


PATHS = {"native": "/v1/run", "openai": "/v1/chat/completions", "anthropic": "/v1/messages"}


def _body(interface: str, text: str, stream: bool, timeout_ms: int) -> dict[str, Any]:
    if interface == "native":
        return {
            "input": {"text": text, "data": {}},
            "stream": stream,
            "budget": {"timeout_ms": timeout_ms},
        }
    message = {"role": "user", "content": text}
    if interface == "openai":
        return {"model": AGENT, "messages": [message], "stream": stream}
    return {"model": AGENT, "max_tokens": 256, "messages": [message], "stream": stream}


async def call(
    chassis: Chassis,
    interface: str,
    text: str,
    *,
    key: str | None,
    stream: bool = False,
    timeout_ms: int = 10_000,
) -> Reply:
    """One call through `interface` (`native`, `openai`, `anthropic`, or `mcp`) with
    `Idempotency-Key: key` when `key` is set. MCP is complete only; its body is the envelope."""
    headers = {"idempotency-key": key} if key is not None else {}
    async with asyncio.timeout(CALL_TIMEOUT_S):
        if interface == "mcp":
            from chassis_contracts.interface import mcp_client

            arguments = _body("native", text, False, timeout_ms)
            async with mcp_client(chassis.public_uds, headers) as client:
                result = await client.call_tool(chassis.agent, arguments, raise_on_error=False)
            return Reply(200 if not result.is_error else 500, {}, result.structured_content)
        transport = httpx2.AsyncHTTPTransport(uds=chassis.public_uds)
        async with httpx2.AsyncClient(
            transport=transport, base_url=BASE_URL, timeout=CALL_TIMEOUT_S
        ) as http:
            response = await http.post(
                PATHS[interface],
                json=_body(interface, text, stream, timeout_ms),
                headers=headers,
            )
    lowered = {k.lower(): v for k, v in response.headers.items()}
    if stream and response.status_code == 200:
        return Reply(response.status_code, lowered, sse_events(response.text))
    try:
        parsed: Any = response.json()
    except ValueError:
        parsed = response.text
    return Reply(response.status_code, lowered, parsed)
