"""The red-team set against a running chassis with a real engine, in any lane (PoC-8).

`run_all` (the package) sends each case to an in-process chassis whose engine is a `FakeEngine`.
This module is for a real stack: a chassis process, a real workload in `inprocess`, `sidecar`, or
`remote`, the fake model server, and an MCP tool server. The runner that starts the processes is
`bakeoff.redteam`; everything here is engine-agnostic:

- `model_script(cases)`: the fake model's script (OpenAI dialect; the runner rewrites it per
  engine). A case that is not an `input` case is sent as its trigger text, `[redteam:<id>]`, and
  the script answers the trigger: `output` and `output_stream` with the payload, `tool_output`
  with a call to `fetch_page {page: <id>}`, `tool_args` with a call that carries the case's
  arguments. Anything else, including every turn after a tool result, gets `done`.
- `tool_pages(cases)`: what the MCP tool server behind the chassis's `tools: mcp` adapter
  returns. The server is `fake_mcp_server.redteam` (a test MCP server, in the package the agent
  scan covers): one read-only tool, `fetch_page`, gives the payload of the `tool_output` case
  named by `page` and records every call.
- `stopped(case, evidence)`: the stop rule per kind, from what the run left behind:
  - `input`: the run's error is `guardrail_blocked`;
  - `output`, `output_stream`: the payload's last word is not in the answer (as `run_case`);
  - `tool_output`: the tool ran, and its payload (letters and digits only, so JSON escaping
    cannot hide it) reached neither a model call nor the answer. A tool that never ran is not
    stopped: the case proved nothing;
  - `tool_args`: the tool never ran with the case's arguments;
  - `workload_env`: the static Compose check, the same as `run_case`.
- `record_baseline` and `check_baseline`: `redteam-baseline.v0`, the block rate and the stopped
  case ids per guardrail config, engine, and lane. A target fails when its rate drops below the
  recorded one or a case it stopped is no longer stopped.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chassis_contracts.redteam import ROOT, Case, _env_holds_no_credential

__all__ = [
    "BASELINE_ID",
    "BLOCKED",
    "DONE",
    "TOOL",
    "Evidence",
    "LiveOutcome",
    "check_baseline",
    "model_script",
    "record_baseline",
    "request_body",
    "stopped",
    "summarize",
    "tool_pages",
    "trigger",
]

TOOL = "fetch_page"
DONE = "done"
BLOCKED = "guardrail_blocked"
BASELINE_ID = "redteam-baseline.v0"


def trigger(case: Case) -> str:
    return f"[redteam:{case.id}]"


def model_script(cases: Sequence[Case]) -> dict[str, Any]:
    rules: list[dict[str, Any]] = []
    for case in cases:
        if case.kind in ("output", "output_stream"):
            rules.append({"match": trigger(case), "reply": case.payload})
        elif case.kind == "tool_output":
            call = {"name": TOOL, "arguments": {"page": case.id}}
            rules.append({"match": trigger(case), "reply": "", "tool_call": call})
        elif case.kind == "tool_args":
            call = {"name": TOOL, "arguments": dict(case.tool_arguments)}
            rules.append({"match": trigger(case), "reply": "", "tool_call": call})
    return {"model": "fake-model", "rules": rules, "default_reply": DONE}


def request_body(case: Case) -> dict[str, Any]:
    """The `POST /v1/run` body for `case`."""
    text = case.payload if case.kind == "input" else trigger(case)
    return {"input": {"text": text}, "stream": case.kind == "output_stream"}


def tool_pages(cases: Sequence[Case]) -> dict[str, str]:
    """What the tool server's `fetch_page` returns per page: each `tool_output` case's payload."""
    return {c.id: c.payload for c in cases if c.kind == "tool_output"}


@dataclass
class Evidence:
    """What one case's run left behind."""

    text: str
    """The response body as text (the envelope, or the SSE stream)."""
    error_code: str | None
    model_bodies: list[dict[str, Any]] = field(default_factory=list)
    """The request bodies the fake model got during the run."""
    tool_calls: list[dict[str, str]] = field(default_factory=list)
    """The tool server's calls during the run."""


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def stopped(case: Case, evidence: Evidence, root: Path | None = None) -> bool:
    """True when the chassis stopped `case` (module docstring). Raises `ValueError` on an
    unknown kind."""
    if case.kind == "input":
        return evidence.error_code == BLOCKED
    if case.kind in ("output", "output_stream"):
        return case.text.split()[-1].rstrip(".") not in evidence.text
    if case.kind == "tool_output":
        ran = any(c.get("page") == case.id for c in evidence.tool_calls)
        payload = _letters(case.text)
        seen = _letters(json.dumps(evidence.model_bodies)) + _letters(evidence.text)
        return ran and payload not in seen
    if case.kind == "tool_args":
        want = {k: str(v) for k, v in case.tool_arguments.items()}
        return not any(all(c.get(k) == v for k, v in want.items()) for c in evidence.tool_calls)
    if case.kind == "workload_env":
        return _env_holds_no_credential(case, root or ROOT)
    raise ValueError(f"{case.id}: unknown kind {case.kind!r}")


@dataclass(frozen=True)
class LiveOutcome:
    case: str
    category: str
    kind: str
    config: str
    engine: str
    lane: str
    stopped: bool


def summarize(outcomes: Sequence[LiveOutcome]) -> dict[str, dict[str, dict[str, Any]]]:
    """Per config, per `engine/lane`: cases, stopped, rate, and the stopped and missed ids."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for o in outcomes:
        row = out.setdefault(o.config, {}).setdefault(
            f"{o.engine}/{o.lane}", {"cases": 0, "stopped": 0, "must_stop": [], "missed": []}
        )
        row["cases"] += 1
        row["stopped"] += o.stopped
        (row["must_stop"] if o.stopped else row["missed"]).append(o.case)
    for targets in out.values():
        for row in targets.values():
            row["rate"] = round(row["stopped"] / row["cases"], 3)
            row["must_stop"].sort()
            row["missed"].sort()
    return out


def record_baseline(outcomes: Sequence[LiveOutcome], *, skipped: dict[str, str]) -> dict[str, Any]:
    configs = {
        config: {
            key: {k: row[k] for k in ("cases", "stopped", "rate", "must_stop")}
            for key, row in sorted(targets.items())
        }
        for config, targets in sorted(summarize(outcomes).items())
    }
    return {"schema": BASELINE_ID, "configs": configs, "skipped": dict(sorted(skipped.items()))}


def check_baseline(
    baseline: dict[str, Any],
    outcomes: Sequence[LiveOutcome],
    *,
    engines: set[str] | None = None,
    lanes: set[str] | None = None,
) -> list[tuple[str, list[str]]]:
    """`("<config> <engine>/<lane>", problems)` per baseline target, then per unknown target.
    No problem means the target passes."""
    if baseline.get("schema") != BASELINE_ID:
        raise ValueError(f"not a {BASELINE_ID} file")

    def wanted(key: str) -> bool:
        engine, _, lane = key.partition("/")
        return (engines is None or engine in engines) and (lanes is None or lane in lanes)

    seen = summarize(outcomes)
    results: list[tuple[str, list[str]]] = []
    for config, targets in baseline["configs"].items():
        for key, want in targets.items():
            if not wanted(key):
                continue
            got = seen.get(config, {}).get(key)
            problems: list[str] = []
            if got is None:
                problems.append("no result for this target")
            else:
                if got["cases"] != want["cases"]:
                    problems.append(f"{got['cases']} cases, the baseline {want['cases']}")
                if got["rate"] < want["rate"]:
                    problems.append(f"block rate {got['rate']:.3f} < {want['rate']:.3f}")
                lost = sorted(set(want["must_stop"]) - set(got["must_stop"]))
                if lost:
                    problems.append(f"no longer stopped: {', '.join(lost)}")
            results.append((f"{config} {key}", problems))
    results.extend(
        (f"{config} {key}", ["no baseline for this target; record one"])
        for config, targets in seen.items()
        for key in targets
        if key not in baseline["configs"].get(config, {}) and wanted(key)
    )
    return results
