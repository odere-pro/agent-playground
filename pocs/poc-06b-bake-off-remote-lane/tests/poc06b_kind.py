"""Shared helpers for the PoC-6b kind tests. Not a test module; the `test_poc06b_kind_*.py` files
import it. The cluster is PoC-5's (`kind-poc05`), so the PoC-5 helpers (`poc05_kind`) do the
kubectl calls, and this file adds what PoC-6 needs on top:

- `KindEngine`: the six PoC-6 engine deployments (two sidecar pods, four remotes) as data;
- `run_stream`: one `POST /v1/run` with `stream: true` on a chassis, from inside its container;
- `gateway_view`: the events as the offline harness sees them (see its docstring);
- `NODE_PROBE` and `probe_in`: the PoC-5 in-pod probe for an image with no Python (Node), and the
  one entry point that picks the right probe for a pod.

Every kubectl call pins `--context kind-poc05` (through `poc05_kind.kubectl`). A credential is
never returned: a check names its env variable (`auth_env`) and the probe reads it in the pod.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from poc05_kind import AGENTS_NS, CHASSIS, PROBE, REMOTE_NS, WORKLOAD, kubectl
from poc06_harness import LOOKUP, SIMPLIFIER, SMOKE, Task, Verdict, answer_text

ROOT = Path(__file__).resolve().parents[3]
GATEWAY_PREFIX = "fake_tools-"
"""LiteLLM's MCP gateway lists a tool as `<server>-<tool>` (PoC-5 blind-spots note, B11)."""
RUN_TIMEOUT_S = 240


@dataclass(frozen=True)
class KindEngine:
    """One PoC-6 engine on kind. `chassis` is the Deployment in `poc05-agents` whose `/v1/run` the
    tests call; `pod` is the workload pod's `app.kubernetes.io/name` (`namespace` says where).
    `marker` is added to the end of the task text for the engines whose reply the script cannot
    tell from the others' (deploy/kind/poc06/platform/fake-model-script.yaml)."""

    name: str
    lane: str
    chassis: str
    pod: str
    namespace: str
    language: str = "python"
    marker: str = ""
    plain: bool = False
    proxy_ip: str | None = None
    token_secret: str | None = None

    @property
    def id(self) -> str:
        return f"{self.name}-{self.lane}"

    def text(self, task: Task) -> str:
        return task.text + self.marker


ENGINES: tuple[KindEngine, ...] = (
    KindEngine(
        "echo-openai-agents", "sidecar", "agent-openai-agents", "agent-openai-agents", AGENTS_NS
    ),
    KindEngine(
        "echo-typescript",
        "sidecar",
        "agent-typescript",
        "agent-typescript",
        AGENTS_NS,
        language="node",
    ),
    KindEngine(
        "echo-smolagents",
        "remote",
        "chassis-smolagents-remote",
        "remote-smolagents",
        REMOTE_NS,
        marker=" [code]",
        proxy_ip="10.96.85.92",
        token_secret="remote-smolagents-token",  # pragma: allowlist secret (a Secret name)
    ),
    KindEngine(
        "echo-claude-agent",
        "remote",
        "chassis-claude-agent-remote",
        "remote-claude-agent",
        REMOTE_NS,
        proxy_ip="10.96.85.93",
        token_secret="remote-claude-agent-token",  # pragma: allowlist secret (a Secret name)
    ),
    KindEngine(
        "echo-typescript",
        "remote",
        "chassis-typescript-remote",
        "remote-typescript",
        REMOTE_NS,
        language="node",
        proxy_ip="10.96.85.94",
        token_secret="remote-typescript-token",  # pragma: allowlist secret (a Secret name)
    ),
    KindEngine(
        "kagent-adk",
        "remote",
        "chassis-kagent-adk-remote",
        "remote-kagent-adk",
        REMOTE_NS,
        marker=" [text]",
        plain=True,
        proxy_ip="10.96.85.95",
    ),
)
REMOTES = tuple(e for e in ENGINES if e.lane == "remote")
SIDECARS = tuple(e for e in ENGINES if e.lane == "sidecar")
TASKS: tuple[Task, ...] = (SMOKE, SIMPLIFIER, LOOKUP)


# --- one run through a chassis --------------------------------------------------------------

STREAM_PY = r"""
import json, os, sys, urllib.error, urllib.request
body = json.dumps({"input": {"text": sys.argv[1]}, "stream": True}).encode()
url = "http://%s:8080/v1/run" % os.environ["POD_IP"]
req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=int(sys.argv[2])) as r:
        code, raw = r.status, r.read()
except urllib.error.HTTPError as e:
    code, raw = e.code, e.read()
frames = []
for block in raw.decode(errors="replace").split("\n\n"):
    lines = dict(l.split(": ", 1) for l in block.splitlines() if ": " in l)
    if "event" in lines:
        frames.append([lines["event"], json.loads(lines.get("data", "null"))])
print(json.dumps({"status": code, "frames": frames, "head": raw[:300].decode(errors="replace")}))
"""


@dataclass(frozen=True)
class RunResult:
    status: int
    events: list[dict[str, Any]]
    envelope: dict[str, Any]
    head: str


def run_stream(engine: KindEngine, text: str, timeout_s: int = RUN_TIMEOUT_S) -> RunResult:
    """`POST /v1/run` with `stream: true`, from inside the chassis container to its pod IP (the
    public port binds the pod IP only), with the image's own Python. Nothing secret is in the
    command or the output. The events are the raw wire dicts, the `response` frame apart."""
    got = kubectl(
        "exec", "-n", AGENTS_NS, f"deploy/{engine.chassis}", "-c", CHASSIS, "--",
        "python", "-c", STREAM_PY, text, str(timeout_s),
        timeout=timeout_s + 60,
    )  # fmt: skip
    assert got.returncode == 0, f"exec in {engine.chassis} failed: {got.stderr[-400:]}"
    out = json.loads(got.stdout.strip().splitlines()[-1])
    frames: list[list[Any]] = out["frames"]
    events = [dict(data) for name, data in frames if name != "response"]
    envelopes = [dict(data) for name, data in frames if name == "response"]
    return RunResult(out["status"], events, envelopes[0] if envelopes else {}, out["head"])


def gateway_view(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The events as the offline harness checks expect them, for a run through the LiteLLM MCP
    gateway. Two differences from the in-process run, and nothing else is changed:

    - the tool name carries the gateway's `fake_tools-` prefix (the harness names are bare);
    - a result the gateway sent back as text only (`{"text": "<json object>"}`) is read as the
      object (the harness reads `result["definition"]`).

    Arguments, order, and the answer text are left as they are. The raw events stay in the test's
    failure message."""
    out: list[dict[str, Any]] = []
    for raw in events:
        event = dict(raw)
        if event.get("type") == "tool_call":
            name = str(event.get("name", ""))
            event["name"] = name.removeprefix(GATEWAY_PREFIX)
            result = event.get("result")
            if isinstance(result, dict) and set(result) == {"text"}:
                try:
                    parsed = json.loads(str(result["text"]))
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    event["result"] = parsed
        out.append(event)
    return out


def answer_only_verdict(task: Task, events: Sequence[Mapping[str, Any]]) -> Verdict:
    """The check for a plain-A2A engine, whose tool calls the chassis cannot see: a run that
    starts, ends `ok`, has no `error`, no `tool_call`, and an answer that holds every fact. The
    absence of `tool_call` is asserted so the limit is on record, not assumed."""
    problems: list[str] = []
    types = [e.get("type") for e in events]
    if "error" in types:
        problems.append(f"error event: {next(e for e in events if e.get('type') == 'error')}")
    if not types or types[0] != "start":
        problems.append(f"the first event is {types[:1]}, not start")
    end = events[-1] if events else {}
    if end.get("type") != "end" or end.get("status") != "ok":
        problems.append(f"the last event is {dict(end)}, not end/ok")
    if "tool_call" in types:
        problems.append("a tool_call event: plain mode should not show tool calls")
    text = answer_text(events)
    if task.exact_text is not None and text.strip() != task.exact_text:
        problems.append(f"answer {text!r}, want exactly {task.exact_text!r}")
    problems.extend(
        f"answer lacks the fact {fact!r}: {text!r}"
        for fact in task.facts
        if fact.casefold() not in text.casefold()
    )
    return Verdict(task.name, tuple(problems))


# --- a probe for an image with no Python ----------------------------------------------------

NODE_PROBE = (Path(__file__).resolve().parent / "node_probe.js").read_text()
"""The probe script, `node_probe.js`: the PoC-5 `PROBE` kinds a Node image can run."""


def probe_node(
    namespace: str, pod_name: str, container: str, checks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Run `checks` in `container` with the image's own Node; one result per check, in order."""
    got = kubectl(
        "exec", "-n", namespace, pod_name, "-c", container, "--",
        "node", "-e", NODE_PROBE, json.dumps(checks),
    )  # fmt: skip
    assert got.returncode == 0, f"exec in {pod_name}/{container} failed: {got.stderr[-400:]}"
    results: list[dict[str, Any]] = json.loads(got.stdout.strip().splitlines()[-1])
    assert len(results) == len(checks)
    return results


NO_PYTHON = (
    "the image has no Python, so the PoC-5 in-pod probe cannot run; recorded as an exception in "
    "pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-lanes-b-kind.md"
)
PYTHONS = ("python", "python3", "/.kagent/.venv/bin/python", "/app/.venv/bin/python")


def probe_python(
    namespace: str, pod_name: str, container: str, checks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The PoC-5 probe (`poc05_kind.PROBE`) with the first Python the image has. An image with
    none is an exception: the test is marked xfail with `NO_PYTHON`, not dropped."""
    for exe in PYTHONS:
        got = kubectl(
            "exec", "-n", namespace, pod_name, "-c", container, "--",
            exe, "-c", PROBE, json.dumps(checks),
        )  # fmt: skip
        if got.returncode == 0:
            results: list[dict[str, Any]] = json.loads(got.stdout.strip().splitlines()[-1])
            assert len(results) == len(checks)
            return results
        assert "not found" in got.stderr.lower(), (
            f"exec in {pod_name}/{container} failed: {got.stderr[-400:]}"
        )
    pytest.xfail(NO_PYTHON)


def probe_in(
    engine: KindEngine, pod_name: str, checks: list[dict[str, Any]], container: str = WORKLOAD
) -> list[dict[str, Any]]:
    """Run `checks` in the engine's workload container, with Python or Node by its image."""
    if engine.language == "node":
        return probe_node(engine.namespace, pod_name, container, checks)
    return probe_python(engine.namespace, pod_name, container, checks)
