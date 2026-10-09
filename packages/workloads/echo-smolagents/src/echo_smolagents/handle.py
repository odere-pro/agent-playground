"""The simplifier as a smolagents `CodeAgent`, streamed back as chassis events.

`handle(input, ctx)` is the wire form of the contract (docs/contracts/contract-v0.md): plain dicts
in, event dicts out, `schema_version: "0"` on each. Same event order and error codes as
echo-python and echo-pydanticai; the framework mapping is in `mapping.py`.

This engine is `untrusted`: the `CodeAgent` asks the model for Python and runs it in this process
(smolagents' `LocalPythonExecutor`). So the workload belongs in the `remote` lane, in a sandbox.

- Model: an OpenAI-compatible chat model at `CHASSIS_MODEL_URL`, the chassis's model proxy. The
  route is `ctx["model_route"]`. No retries: the chassis owns retries and fallback.
- Tools: the chassis's MCP endpoint at `CHASSIS_TOOL_URL`, as smolagents `Tool`s the generated code
  calls like functions (`tools.py`). This workload defines no tool.
- Sync and async: smolagents is synchronous. The agent runs in a worker thread and passes events
  back through a `queue.Queue`; `handle` reads it without blocking the event loop.
- `ctx["traceparent"]`, when set, is a default header on both HTTP clients, so every model call
  and every MCP request carries it (contract v1 decision 4). A plain header, no OpenTelemetry.
- No model key. In the `remote` lane `CHASSIS_API_TOKEN`, when set and not empty, is the openai
  client's `api_key` (so `Authorization: Bearer <token>` on every model call) and a default header
  on the MCP client; unset, no `Authorization` header is sent. Read once per run, never logged.
- The input text is the task, its own user message, never merged into the system prompt.
- Timeouts: `ctx.budget.timeout_ms` is the timeout of each model call, each MCP request, and the
  generated code's run. There is no whole-run deadline here: the connector owns it.
- Steps: `MAX_STEPS` (4). Running out is `error{code: "tool_loop_exceeded"}`.
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import openai
from smolagents.monitoring import LogLevel  # type: ignore[import-untyped]

from echo_smolagents import tools
from echo_smolagents.agent import INSTRUCTIONS, MAX_STEPS, PROMPT_VERSION, ChassisModel
from echo_smolagents.agent import SimplifierAgent as _Agent
from echo_smolagents.mapping import EventMapper, ToolFailure, error_event, event

__all__ = ["PROMPT_VERSION", "handle", "transport"]

DEFAULT_ROUTE = "big-default"
MODEL_URL_VAR = "CHASSIS_MODEL_URL"
DEFAULT_MODEL_URL = "http://127.0.0.1:8090/v1"
TOKEN_VAR = "CHASSIS_API_TOKEN"
# suggested: 30 s per call when `ctx.budget.timeout_ms` is not set; the epic gives none.
DEFAULT_TIMEOUT_S = 30.0
# The openai SDK refuses an empty key, so it gets this placeholder, never an `*_API_KEY` variable.
# It is not sent either: `_scrub_headers` removes the header. The chassis model proxy ignores
# `Authorization` anyway and adds the real, scoped key itself; only the chassis holds credentials.
PLACEHOLDER_API_KEY = "not-a-key"

transport: httpx2.BaseTransport | None = None
"""Test-only hook: when set, model calls go through this sync `httpx2` transport, not a socket (for
example `httpx2.HTTPTransport(uds=...)`; the openai client here is the sync one, because smolagents
is). The MCP hook is the async `echo_smolagents.tools.transport`."""

_DONE = object()


def timeout_s(ctx: dict[str, Any]) -> float:
    budget = ctx.get("budget")
    if isinstance(budget, dict) and budget.get("timeout_ms"):
        return float(budget["timeout_ms"]) / 1000
    return DEFAULT_TIMEOUT_S


def _scrub_headers(token: str | None) -> Any:
    """A request hook: no `Authorization` without a remote token, never an openai org/project."""

    def hook(request: httpx2.Request) -> None:
        if not token:
            request.headers.pop("authorization", None)
        request.headers.pop("openai-organization", None)
        request.headers.pop("openai-project", None)

    return hook


def _model(
    route: str, headers: dict[str, str], timeout: float, token: str | None
) -> tuple[ChassisModel, httpx2.Client]:
    http = httpx2.Client(
        transport=transport,
        timeout=timeout,
        follow_redirects=True,
        event_hooks={"request": [_scrub_headers(token)]},
    )
    client = openai.OpenAI(
        base_url=os.environ.get(MODEL_URL_VAR, DEFAULT_MODEL_URL).rstrip("/"),
        api_key=token or PLACEHOLDER_API_KEY,
        admin_api_key="",  # never fall back to OPENAI_ADMIN_KEY
        organization="",  # nor to OPENAI_ORG_ID (the hook drops the header anyway)
        project="",
        default_headers=headers,
        http_client=http,
        max_retries=0,  # the chassis owns retries and fallback
        timeout=timeout,
    )
    return ChassisModel(route, client), http


class _Run:
    """One run in the worker thread. Puts chassis events on `out`; `_DONE` last."""

    def __init__(self, agent: _Agent, mapper: EventMapper, text: str) -> None:
        self.agent, self.mapper, self.text = agent, mapper, text
        self.out: queue.Queue[Any] = queue.Queue()
        self.failure: BaseException | None = None
        self.calls = 0
        self.lock = threading.Lock()

    def on_call(self, name: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
        with self.lock:
            self.calls += 1
            call_id = f"call_{self.calls}"
        self.out.put(self.mapper.tool_call(call_id, name, arguments, result))

    def on_failure(self, exc: BaseException) -> None:
        self.failure = self.failure or exc

    def work(self, http: httpx2.Client) -> None:
        try:
            for step in self.agent.run(self.text, stream=True):
                if self.failure is not None:
                    raise ToolFailure(str(self.failure)[:200] or "tool failed") from self.failure
                for out in self.mapper.map(step):
                    self.out.put(out)
            for out in self.mapper.finish():
                self.out.put(out)
        except BaseException as exc:
            self.out.put(error_event(exc))
        finally:
            http.close()
            self.out.put(_DONE)


async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """`start`, `tool_call` per MCP call, `delta`, `metrics`, `end`; or `start` then one `error`.
    Never raises for a model, tool, or transport failure.
    """
    yield event("start", request_id=str(ctx.get("request_id", "")))
    run: _Run | None = None
    try:
        route = str(ctx.get("model_route") or DEFAULT_ROUTE)
        timeout = timeout_s(ctx)
        traceparent = ctx.get("traceparent")
        headers = {"traceparent": str(traceparent)} if traceparent else {}
        token = os.environ.get(TOKEN_VAR) or None
        tool_headers = {**headers, "authorization": f"Bearer {token}"} if token else headers
        model, http = _model(route, headers, timeout, token)
        # Bound after `run` exists; the tools only call them while the worker is running.
        pending: list[_Run] = []
        loaded = await tools.load_tools(
            tool_headers,
            timeout,
            lambda *a: pending[0].on_call(*a),
            lambda exc: pending[0].on_failure(exc),
        )
        agent = _Agent(
            tools=loaded,
            model=model,
            instructions=INSTRUCTIONS,
            max_steps=MAX_STEPS,
            verbosity_level=LogLevel.OFF,  # the default prints the task and the code to stdout
            executor_kwargs={"timeout_seconds": int(timeout)},
        )
        monitor = agent.monitor
        mapper = EventMapper(
            route, lambda: (monitor.total_input_token_count, monitor.total_output_token_count)
        )
        run = _Run(agent, mapper, str(input.get("text") or ""))
        pending.append(run)
    except Exception as exc:
        yield error_event(exc)
        return
    worker = threading.Thread(target=run.work, args=(http,), name="smolagents-run", daemon=True)
    worker.start()
    try:
        while True:
            item = await asyncio.to_thread(run.out.get)
            if item is _DONE:
                return
            yield item
    finally:
        agent.interrupt()  # a consumer that stops early ends the run at the next step
