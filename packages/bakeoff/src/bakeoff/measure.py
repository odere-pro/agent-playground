"""Run the tasks against a chassis by URL and record what was measured.

Per repetition, one complete `POST /v1/run` (latency, tokens, output, tool calls) and one
streaming `POST /v1/run` (time to first token, the events). The model's request bodies come
from a `CallSource`: the in-process fake model server, or a JSONL request log.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from bakeoff.stats import mean, percentile
from bakeoff.tasks import Check, RunView, Task, check_task

__all__ = [
    "CallSource",
    "JsonlCalls",
    "RunError",
    "TaskResult",
    "call_summary",
    "measure_task",
    "run_complete",
    "run_stream",
]

TIMEOUT_S = 120.0


class RunError(RuntimeError):
    """A request failed at the HTTP level (not a failed task)."""


class CallSource(Protocol):
    """Where the model's request bodies are read from."""

    def mark(self) -> int: ...

    def since(self, mark: int) -> list[dict[str, Any]]: ...


class JsonlCalls:
    """A JSONL request log: one JSON object a line, the request body (or `{"body": ...}`)."""

    def __init__(self, path: Path):
        self.path = path

    def _bodies(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        bodies: list[dict[str, Any]] = []
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            body = row.get("body") if isinstance(row, dict) else None
            bodies.append(body if isinstance(body, dict) else row)
        return bodies

    def mark(self) -> int:
        return len(self._bodies())

    def since(self, mark: int) -> list[dict[str, Any]]:
        return self._bodies()[mark:]


def call_summary(body: dict[str, Any]) -> dict[str, Any]:
    """One model call: the bytes of its `messages` (compact JSON, UTF-8) and its body keys."""
    messages = body.get("messages", [])
    prompt = json.dumps(messages, separators=(",", ":"), ensure_ascii=False).encode()
    return {"prompt_bytes": len(prompt), "body_keys": sorted(body)}


def _body(task: Task, stream: bool) -> dict[str, Any]:
    return {"input": {"text": task.text}, "stream": stream}


def run_complete(client: httpx.Client, task: Task) -> tuple[RunView, dict[str, Any], float]:
    """A complete run: the view, the metrics, and the wall time in milliseconds."""
    started = time.perf_counter()
    try:
        response = client.post("/v1/run", json=_body(task, False))
    except httpx.HTTPError as exc:
        raise RunError(f"{type(exc).__name__} on /v1/run") from exc
    elapsed = (time.perf_counter() - started) * 1000
    if response.status_code != 200:
        raise RunError(f"/v1/run answered HTTP {response.status_code}")
    data = response.json()
    output = data.get("output") or {}
    view = RunView(
        status=str(data.get("status")),
        text=str(output.get("text") or ""),
        tool_calls=tuple(output.get("tool_calls") or ()),
    )
    return view, dict(data.get("metrics") or {}), elapsed


def run_stream(client: httpx.Client, task: Task) -> tuple[RunView, float | None]:
    """A streaming run: the view and the time to the first `delta` frame, in milliseconds."""
    started = time.perf_counter()
    first: float | None = None
    events: list[dict[str, Any]] = []
    name = ""
    try:
        with client.stream("POST", "/v1/run", json=_body(task, True)) as response:
            if response.status_code != 200:
                raise RunError(f"streaming /v1/run answered HTTP {response.status_code}")
            for line in response.iter_lines():
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:") and name:
                    if name == "delta" and first is None:
                        first = (time.perf_counter() - started) * 1000
                    if name != "response":
                        events.append(json.loads(line[5:]))
    except httpx.HTTPError as exc:
        raise RunError(f"{type(exc).__name__} on streaming /v1/run") from exc
    return view_from_events(events), first


def view_from_events(events: Sequence[dict[str, Any]]) -> RunView:
    """Fold a stream into a view. `deltas` counts those before the `end`."""
    deltas, text, calls = 0, [], []
    status = "error"
    for event in events:
        kind = event.get("type")
        if kind == "delta":
            deltas += 1
            text.append(str(event.get("text", "")))
        elif kind == "tool_call":
            calls.append(event)
        elif kind == "end":
            status = str(event.get("status", "ok"))
            break
        elif kind == "error":
            status = "error"
    return RunView(status=status, text="".join(text), tool_calls=tuple(calls), deltas=deltas)


@dataclass
class TaskResult:
    task: str
    runs: int = 0
    passed: int = 0
    tools_passed: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    ttft_ms: list[float] = field(default_factory=list)
    input_tokens: list[float] = field(default_factory=list)
    output_tokens: list[float] = field(default_factory=list)
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    """One entry per model call of the first repetition; None-free, empty without a source."""
    failures: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "runs": self.runs,
            "passed": self.passed,
            "pass_rate": self.passed / self.runs if self.runs else None,
            "tool_call_pass_rate": (
                self.tools_passed / self.runs if self.runs and self.task == "lookup" else None
            ),
            "latency_p50_ms": percentile(self.latencies_ms, 50),
            "latency_p95_ms": percentile(self.latencies_ms, 95),
            "ttft_p50_ms": percentile(self.ttft_ms, 50),
            "input_tokens": mean(self.input_tokens),
            "output_tokens": mean(self.output_tokens),
            "model_calls": self.model_calls,
            "failures": sorted(set(self.failures)),
        }


def _combine(complete: Check, streamed: Check) -> Check:
    if not complete.ok:
        return Check(False, f"complete run: {complete.reason}", complete.tools_ok)
    if not streamed.ok:
        return Check(False, f"streaming run: {streamed.reason}", streamed.tools_ok)
    return Check(True, tools_ok=complete.tools_ok and streamed.tools_ok)


def measure_task(
    client: httpx.Client,
    task: Task,
    repeat: int,
    calls: CallSource | None,
    log: Callable[[str], None] = lambda _msg: None,
) -> TaskResult:
    """Run `task` `repeat` times. A request that fails at the HTTP level counts as a failure."""
    result = TaskResult(task.name)
    for index in range(repeat):
        result.runs += 1
        mark = calls.mark() if calls is not None else 0
        try:
            view, metrics, elapsed = run_complete(client, task)
        except RunError as exc:
            result.failures.append(str(exc))
            log(f"{task.name} #{index + 1}: {exc}")
            continue
        if calls is not None and index == 0:
            result.model_calls = [call_summary(b) for b in calls.since(mark)]
        result.latencies_ms.append(elapsed)
        result.input_tokens.append(float(metrics.get("input_tokens", 0)))
        result.output_tokens.append(float(metrics.get("output_tokens", 0)))
        complete = check_task(task.name, view)
        try:
            streamed_view, first = run_stream(client, task)
        except RunError as exc:
            result.failures.append(str(exc))
            continue
        if first is not None:
            result.ttft_ms.append(first)
        check = _combine(complete, check_task(task.name, streamed_view))
        if check.ok:
            result.passed += 1
        else:
            result.failures.append(check.reason)
        if check.tools_ok:
            result.tools_passed += 1
    return result
