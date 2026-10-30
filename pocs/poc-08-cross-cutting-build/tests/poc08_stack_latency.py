"""PoC-8 latency: p50 and p95 of `POST /v1/run` with the PoC-8 request stages on, against the same
chassis with them off. Needs `deploy/compose/poc08` up; CI runs it in poc08-stack.yml.

    deploy/compose/poc08/stack.sh up && deploy/compose/poc08/stack.sh wait
    export "$(deploy/compose/poc08/stack.sh env)"
    uv run python pocs/poc-08-cross-cutting-build/tests/poc08_stack_latency.py \\
        --out artifacts/poc08-stack/latency.json

- `stages_on`: `chassis-stages` (deploy/compose/poc08/chassis/stages.yaml): static auth, the rate
  limit per caller in Valkey, the keyword guardrails at input, tool output, and output, the stream
  and URL filters, the feedback lookup write to Valkey, marking.
- `stages_off`: `chassis-plain` (chassis/plain.yaml): the same agent, model, lane, and Valkey,
  every stage off.

One request at a time (suggested: concurrency 1, so the number is the stages' own cost, not
queueing), the two targets interleaved request by request, so drift on the runner hits both alike.
`--warmup` requests per target are sent first and not counted. Every counted request must answer
200 with `status: ok`; any other answer is counted in `errors`, and the script exits 1.

The JSON holds the setup (commit, runner, input, counts), each target's summary, the overhead
(on minus off), and a suggested budget. The budget is recorded, never enforced: suggested:
p50 overhead at most 25 ms and p95 at most 50 ms on a GitHub runner with the fake model. The
token is read from the env file and never printed or written.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from poc_harness.metrics_stack import (
    CHASSIS_PLAIN,
    CHASSIS_STAGES,
    PLAIN_INPUT,
    RUN_TIMEOUT_S,
    TOKEN_KEY,
    bearer,
    read_env_file,
    summarize,
)
from poc_harness.paths import ROOT

REQUESTS = 200
"""suggested: per target."""
WARMUP = 10
"""suggested: per target, not counted."""
BUDGET_MS = {"p50": 25.0, "p95": 50.0}
"""suggested: the overhead budget of the PoC-8 stages, recorded in the JSON, not enforced."""
STAGES_ON = "stages_on"
STAGES_OFF = "stages_off"
CONFIGS = {
    STAGES_ON: "deploy/compose/poc08/chassis/stages.yaml",
    STAGES_OFF: "deploy/compose/poc08/chassis/plain.yaml",
}


@dataclass
class Target:
    name: str
    url: str
    token: str | None
    timings_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


Timer = Callable[[], float]


def _one(client: httpx.Client, target: Target, text: str, timer: Timer) -> tuple[float, str | None]:
    """One run: its wall time in ms, and an error string when it did not answer 200 `ok`."""
    started = timer()
    response = client.post("/v1/run", json={"input": {"text": text}}, headers=bearer(target.token))
    elapsed_ms = (timer() - started) * 1000
    if response.status_code != 200:
        return elapsed_ms, f"http {response.status_code}"
    try:
        status = response.json().get("status")
    except ValueError:
        return elapsed_ms, "not JSON"
    return elapsed_ms, None if status == "ok" else f"status {status}"


def measure(
    clients: Mapping[str, httpx.Client],
    targets: Sequence[Target],
    *,
    requests: int,
    warmup: int,
    text: str = PLAIN_INPUT,
    timer: Timer = time.perf_counter,
) -> None:
    """Warm up, then `requests` runs per target, interleaved. Fills each target's lists."""
    for _ in range(warmup):
        for target in targets:
            _one(clients[target.name], target, text, timer)
    for _ in range(requests):
        for target in targets:
            elapsed, error = _one(clients[target.name], target, text, timer)
            if error is None:
                target.timings_ms.append(elapsed)
            else:
                target.errors.append(error)


def _commit() -> str:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha[:12]
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return done.stdout.strip()


def report(targets: Sequence[Target], *, requests: int, warmup: int, text: str) -> dict[str, Any]:
    """The JSON the script writes."""
    by_name = {t.name: t for t in targets}
    summaries: dict[str, dict[str, Any]] = {}
    for target in targets:
        entry: dict[str, Any] = {"url": target.url, "config": CONFIGS.get(target.name, "")}
        entry["errors"] = len(target.errors)
        entry["error_kinds"] = sorted(set(target.errors))
        if target.timings_ms:
            entry.update(summarize(target.timings_ms))
        summaries[target.name] = entry
    overhead: dict[str, float] = {}
    on, off = by_name.get(STAGES_ON), by_name.get(STAGES_OFF)
    if on and off and on.timings_ms and off.timings_ms:
        for q in ("p50", "p95"):
            key = f"{q}_ms"
            overhead[q] = round(summaries[STAGES_ON][key] - summaries[STAGES_OFF][key], 2)
    within = bool(overhead) and all(overhead[q] <= BUDGET_MS[q] for q in BUDGET_MS)
    return {
        "measurement": "poc08-stage-latency",
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": _commit(),
        "runner": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "cpus": os.cpu_count(),
            "python": platform.python_version(),
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        },
        "setup": {
            "endpoint": "POST /v1/run (complete)",
            "input": text,
            "requests_per_target": requests,
            "warmup_per_target": warmup,
            "concurrency": 1,
            "order": "interleaved, one request per target in turn",
            "lane": "inprocess (echo_python:handle)",
            "model": "fake-model-server (deploy/compose/poc08/fake-model/poc08.yaml)",
        },
        "targets": summaries,
        "overhead_ms": overhead,
        "suggested_budget_ms": BUDGET_MS,
        "within_suggested_budget": within,
        "suggested": "the request counts, the concurrency, and the budget are suggested values",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="where the JSON goes")
    parser.add_argument("--requests", type=int, default=REQUESTS)
    parser.add_argument("--warmup", type=int, default=WARMUP)
    parser.add_argument("--env-file", type=Path, default=None, help="default: $POC08_ENV_FILE")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    token = read_env_file(args.env_file).get(TOKEN_KEY)
    if not token:
        print(f"{TOKEN_KEY} is missing from the env file", file=sys.stderr)
        return 2
    targets = [
        Target(STAGES_ON, CHASSIS_STAGES, token),
        Target(STAGES_OFF, CHASSIS_PLAIN, None),
    ]
    clients = {t.name: httpx.Client(base_url=t.url, timeout=RUN_TIMEOUT_S) for t in targets}
    try:
        measure(clients, targets, requests=args.requests, warmup=args.warmup)
    finally:
        for client in clients.values():
            client.close()
    data = report(targets, requests=args.requests, warmup=args.warmup, text=PLAIN_INPUT)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2) + "\n")
    for name, entry in data["targets"].items():
        print(
            f"{name}: p50 {entry.get('p50_ms')} ms, p95 {entry.get('p95_ms')} ms, "
            f"{entry.get('count', 0)} ok, {entry['errors']} errors"
        )
    print(f"overhead: {data['overhead_ms']} (suggested budget {BUDGET_MS}); wrote {args.out}")
    return 1 if any(t.errors for t in targets) else 0


if __name__ == "__main__":
    raise SystemExit(main())
