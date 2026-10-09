"""The PoC-4 load matrix on the Compose scale stack (plan, section 9; criteria 4 and 11).

    make load-test ARGS="--dry-run"
    make load-test ARGS="--engine echo-python --pairs 1"
    uv run python pocs/poc-04-stateless-scalable/load/run_matrix.py --only hop

One scenario at a time, because the Docker VM is shared and small:

1. `deploy/compose/scale.sh up <engine> <pairs>`, then wait until `/ready` answers 200 several
   times in a row through Traefik (127.0.0.1:18080).
2. One idle `docker stats` sample, then a Locust warm-up whose numbers are thrown away.
3. Locust headless for `--duration-s` (through `uv run --with`, never a workspace dependency),
   while `docker stats` samples every container of Compose project `poc04` every 2 s.
4. `scale.sh down`, always, even when a step failed.

Scenarios: `main` is every engine (echo-python, echo-pydanticai, echo-langgraph, echo-openai-agents,
echo-typescript)
at every pair count (1, 2, 4) with `--users`. `hop` is echo-python at 1 pair with `--hop-users`,
in the sidecar lane and in the inprocess lane (`packages/chassis/configs/scale-inprocess.yaml`);
the hop is sidecar minus inprocess, at p50 and p95. `idem` is echo-python at 1 pair with no
`Idempotency-Key` (`LOAD_NO_KEY=1`), against the keyed run of `main`, to price idempotency.

Output, under `pocs/poc-04-stateless-scalable/notes/load/`: `raw/<scenario>_*.csv` (Locust's
CSVs) and `raw/<scenario>-docker-stats.csv` (the samples), and `results.json`, which each run
merges into, so one engine or one pair count at a time adds up to the full matrix.

Containers outside project `poc04` are never named, stopped, or sampled.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SCALE_SH = ROOT / "deploy" / "compose" / "scale.sh"
NOTES = HERE.parent / "notes" / "load"
LOCUSTFILE = HERE / "locustfile.py"
LOCUST = "locust==2.46.6"  # the newest 2.x on 2026-10-01
PROJECT = "poc04"
URL = "http://127.0.0.1:18080"

ENGINES = (
    "echo-python",
    "echo-pydanticai",
    "echo-langgraph",
    "echo-openai-agents",
    "echo-typescript",
)
PAIRS = (1, 2, 4)
SCENARIO_KINDS = ("main", "hop", "idem", "rate")
# suggested values (plan, section 9).
DURATION_S = 60
WARMUP_S = 10
USERS = 64
# 1 user (suggested, was 8): at 8 users the chassis neared its 1-CPU cap and the inprocess lane,
# which runs echo_python inside that one CPU, came out slower than the sidecar lane (2026-10-01).
HOP_USERS = 1
# The `rate` scenarios: a fixed moderate load for the chassis's CPU and memory (suggested: 10
# users at 1 call per second each, 10 RPS on 1 pair).
RATE_USERS = 10
RATE_PER_USER = "1"

LOCUST_PROCESSES = 4
SAMPLE_EVERY_S = 2.0
READY_TIMEOUT_S = 180.0
READY_IN_A_ROW = 5


@dataclass(frozen=True)
class Scenario:
    name: str
    kind: str
    engine: str  # what `scale.sh up` gets: an engine or `inprocess`
    pairs: int
    users: int
    env: dict[str, str] = field(default_factory=dict)


def scenarios(args: argparse.Namespace) -> list[Scenario]:
    """The scenarios `args` selects, in run order."""
    out: list[Scenario] = []
    kinds = set(args.only or SCENARIO_KINDS)
    if "main" in kinds:
        for engine in args.engine or ENGINES:
            out.extend(
                Scenario(f"{engine}-{pairs}p", "main", engine, pairs, args.users)
                for pairs in args.pairs or PAIRS
            )
    wants_python_1p = (not args.engine or "echo-python" in args.engine) and (
        not args.pairs or 1 in args.pairs
    )
    if "hop" in kinds and wants_python_1p:
        out.append(Scenario("hop-sidecar", "hop", "echo-python", 1, args.hop_users))
        out.append(Scenario("hop-inprocess", "hop", "inprocess", 1, args.hop_users))
    if "rate" in kinds:
        for engine in args.engine or ENGINES:
            rate = {"LOAD_RATE_PER_USER": RATE_PER_USER}
            out.append(Scenario(f"{engine}-1p-rate10", "rate", engine, 1, RATE_USERS, rate))
    if "idem" in kinds and wants_python_1p:
        no_key = {"LOAD_NO_KEY": "1"}
        out.append(Scenario("echo-python-1p-nokey", "idem", "echo-python", 1, args.users, no_key))
    return out


def locust_cmd(s: Scenario, seconds: int, csv_prefix: Path | None, processes: int) -> list[str]:
    cmd = ["uv", "run", "--no-project", "--with", LOCUST, "locust", "-f", str(LOCUSTFILE)]
    cmd += ["--host", URL, "--headless", "-u", str(s.users), "-r", str(s.users)]
    # A run with failed requests is still a result: keep it with its failure count (Locust exits 1
    # otherwise, and the scenario was dropped on 2026-10-01). `ok_rps` excludes the failures.
    cmd += ["--run-time", f"{seconds}s", "--only-summary", "--stop-timeout", "35"]
    cmd += ["--exit-code-on-error", "0"]
    # One process per user at most: 1 user in 4 processes leaves 3 idle and skews the hop.
    processes = min(processes, s.users)
    if processes > 1:
        cmd += ["--processes", str(processes)]
    if csv_prefix is not None:
        cmd += ["--csv", str(csv_prefix)]
    return cmd


def run(cmd: Sequence[str], env: dict[str, str] | None = None, timeout: float = 900) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True, timeout=timeout, env={**os.environ, **(env or {})})


# docker stats -------------------------------------------------------------------------------


def project_containers() -> list[str]:
    """Names of the running containers of project poc04, and of no other project."""
    out = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={PROJECT}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    return sorted(n for n in out.split() if n.startswith(f"{PROJECT}-"))


_UNITS = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "KB": 1e3, "MB": 1e6, "GB": 1e9}


def parse_bytes(text: str) -> float:
    """`12.5MiB` to bytes."""
    text = text.strip()
    for unit in sorted(_UNITS, key=len, reverse=True):
        if text.upper().endswith(unit):
            return float(text[: -len(unit)]) * _UNITS[unit]
    return float(text or 0)


def sample_stats(names: Sequence[str]) -> list[dict[str, Any]]:
    """One `docker stats --no-stream` sample: name, vCPU, and MiB per container."""
    if not names:
        return []
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *names],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    ).stdout
    now = time.time()
    rows = []
    for line in out.splitlines():
        try:
            item = json.loads(line)
            cpu = float(item["CPUPerc"].rstrip("%")) / 100
            mem = parse_bytes(item["MemUsage"].split("/")[0]) / 1024**2
        except (ValueError, KeyError):
            continue
        rows.append({"t": round(now, 2), "name": item["Name"], "vcpu": cpu, "mib": round(mem, 2)})
    return rows


class Sampler(threading.Thread):
    """Samples the project's containers every `every_s` until stopped."""

    def __init__(self, names: Sequence[str], every_s: float) -> None:
        super().__init__(daemon=True)
        self.names, self.every_s = list(names), every_s
        self.rows: list[dict[str, Any]] = []
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            self.rows.extend(sample_stats(self.names))
            self._stop_event.wait(self.every_s)

    def stop(self) -> list[dict[str, Any]]:
        self._stop_event.set()
        self.join(timeout=60)
        return self.rows


def role(name: str) -> str:
    """`poc04-chassis-2-1` -> `chassis`; `poc04-fake-model-1` -> `fake-model`."""
    middle = name.removeprefix(f"{PROJECT}-").rsplit("-", 1)[0]
    head, _, tail = middle.rpartition("-")
    return head if tail.isdigit() and head in ("chassis", "workload") else middle


def per_role(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Per role: the mean of each replica's mean, and the max over replicas, of CPU and memory."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_name.setdefault(row["name"], []).append(row)
    by_role: dict[str, list[list[dict[str, Any]]]] = {}
    for name, samples in by_name.items():
        by_role.setdefault(role(name), []).append(samples)
    out: dict[str, dict[str, float]] = {}
    for name, replicas in by_role.items():
        out[name] = {
            "replicas": len(replicas),
            "vcpu_mean": round(statistics.fmean(_mean(r, "vcpu") for r in replicas), 4),
            "vcpu_max": round(max(max(s["vcpu"] for s in r) for r in replicas), 4),
            "mib_mean": round(statistics.fmean(_mean(r, "mib") for r in replicas), 2),
            "mib_max": round(max(max(s["mib"] for s in r) for r in replicas), 2),
        }
    return out


def _mean(samples: Sequence[dict[str, Any]], key: str) -> float:
    return statistics.fmean(s[key] for s in samples)


# Locust results ------------------------------------------------------------------------------


def read_locust(prefix: Path) -> dict[str, Any]:
    """The `Aggregated` row of Locust's `<prefix>_stats.csv`."""
    with Path(f"{prefix}_stats.csv").open(newline="", encoding="utf-8") as fh:
        rows = {row["Name"]: row for row in csv.DictReader(fh)}
    agg = rows["Aggregated"]
    return {
        "requests": int(agg["Request Count"]),
        "failures": int(agg["Failure Count"]),
        "rps": round(float(agg["Requests/s"]), 2),
        "p50_ms": float(agg["50%"]),
        "p95_ms": float(agg["95%"]),
        "ok_rps": ok_rps(int(agg["Request Count"]), int(agg["Failure Count"]), agg["Requests/s"]),
    }


def ok_rps(requests: int, failures: int, rps: str | float) -> float:
    """Successful calls per second: fast failures (a 503 with no server behind it) do not count."""
    return round(float(rps) * (requests - failures) / requests, 2) if requests else 0.0


def wait_ready(timeout_s: float = READY_TIMEOUT_S) -> None:
    """`/ready` 200 `READY_IN_A_ROW` times in a row through Traefik, so every pair is in."""
    deadline, streak = time.monotonic() + timeout_s, 0
    while streak < READY_IN_A_ROW:
        if time.monotonic() > deadline:
            raise TimeoutError(f"{URL}/ready not 200 {READY_IN_A_ROW} times in {timeout_s} s")
        try:
            streak = streak + 1 if httpx.get(f"{URL}/ready", timeout=2).status_code == 200 else 0
        except httpx.HTTPError:
            streak = 0
        time.sleep(0.5)


def write_samples(path: Path, rows: Sequence[dict[str, Any]], phase: str) -> None:
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["phase", "t", "name", "vcpu", "mib"])
        if new:
            writer.writeheader()
        for row in rows:
            writer.writerow({"phase": phase, **row})


def run_scenario(s: Scenario, args: argparse.Namespace) -> dict[str, Any]:
    raw = NOTES / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    stats_csv = raw / f"{s.name}-docker-stats.csv"
    stats_csv.unlink(missing_ok=True)
    run([str(SCALE_SH), "up", s.engine, str(s.pairs)])
    try:
        wait_ready()
        names = project_containers()
        idle = sample_stats(names)
        write_samples(stats_csv, idle, "idle")
        if args.warmup_s > 0:
            run(locust_cmd(s, args.warmup_s, None, args.locust_processes), s.env)
        sampler = Sampler(names, SAMPLE_EVERY_S)
        sampler.start()
        try:
            run(locust_cmd(s, args.duration_s, raw / s.name, args.locust_processes), s.env)
        finally:
            rows = sampler.stop()
            write_samples(stats_csv, rows, "load")
    finally:
        run([str(SCALE_SH), "down"])
    result = read_locust(raw / s.name)
    roles = per_role(rows)
    idle_roles = per_role(idle)
    for name, numbers in roles.items():
        numbers["idle_vcpu"] = idle_roles.get(name, {}).get("vcpu_mean", 0.0)
        numbers["idle_mib"] = idle_roles.get(name, {}).get("mib_mean", 0.0)
    if "chassis" in roles and result["rps"] > 0:
        total = roles["chassis"]["vcpu_mean"] * roles["chassis"]["replicas"]
        roles["chassis"]["vcpu_per_100rps"] = round(total / result["rps"] * 100, 4)
    return {
        **result,
        "engine": s.engine,
        "pairs": s.pairs,
        "users": s.users,
        "duration_s": args.duration_s,
        "env": s.env,
        "containers": roles,
        "finished": datetime.now(UTC).isoformat(timespec="seconds"),
    }


# results.json --------------------------------------------------------------------------------


def host_info() -> dict[str, Any]:
    info: dict[str, Any] = {"platform": platform.platform(), "cpus": os.cpu_count()}
    out = subprocess.run(
        ["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    ).stdout.split()
    if len(out) == 2:
        info["docker_cpus"] = int(out[0])
        info["docker_mem_mib"] = round(int(out[1]) / 1024**2)
    others = subprocess.run(
        ["docker", "ps", "--format", '{{.Label "com.docker.compose.project"}}'],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    ).stdout.split("\n")
    info["other_running_containers"] = sum(1 for p in others if p.strip() != PROJECT and p)
    return info


def diff(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, float] | None:
    if not a or not b:
        return None
    return {k: round(a[k] - b[k], 2) for k in ("p50_ms", "p95_ms")}


def merge(results: dict[str, Any], s: Scenario, run_result: dict[str, Any]) -> None:
    """Put one scenario's numbers into `results` and recompute the derived figures."""
    if s.kind == "main":
        results.setdefault("engines", {}).setdefault(s.engine, {})[str(s.pairs)] = run_result
    elif s.kind == "hop":
        lane = "inprocess" if s.engine == "inprocess" else "sidecar"
        results.setdefault("hop", {"engine": "echo-python", "pairs": 1})[lane] = run_result
    elif s.kind == "rate":
        results.setdefault("rate", {})[s.engine] = run_result
    else:
        results.setdefault("idempotency", {"engine": "echo-python", "pairs": 1})["no_key"] = (
            run_result
        )
    hop = results.get("hop", {})
    hop["hop_ms"] = diff(hop.get("sidecar"), hop.get("inprocess"))
    idem = results.get("idempotency")
    if idem is not None:
        keyed = results.get("engines", {}).get("echo-python", {}).get("1")
        idem["cost_ms"] = diff(keyed, idem.get("no_key"))


def load_results(path: Path) -> dict[str, Any]:
    if path.exists():
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data
    return {"version": 1}


def save_results(path: Path, results: dict[str, Any]) -> None:
    results["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


# CLI -----------------------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--engine", action="append", choices=ENGINES, help="repeatable")
    parser.add_argument("--pairs", action="append", type=int, choices=PAIRS, help="repeatable")
    parser.add_argument("--only", action="append", choices=SCENARIO_KINDS, help="repeatable")
    parser.add_argument("--duration-s", type=int, default=DURATION_S)
    parser.add_argument("--warmup-s", type=int, default=WARMUP_S)
    parser.add_argument("--users", type=int, default=USERS)
    parser.add_argument("--hop-users", type=int, default=HOP_USERS)
    parser.add_argument("--locust-processes", type=int, default=LOCUST_PROCESSES)
    parser.add_argument("--results", type=Path, default=NOTES / "results.json")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    todo = scenarios(args)
    if not todo:
        print("run_matrix: nothing selected")
        return 2
    if args.dry_run:
        print(f"run_matrix: {len(todo)} scenario(s), one at a time; results -> {args.results}")
        for s in todo:
            env = " ".join(f"{k}={v}" for k, v in s.env.items())
            print(f"\n[{s.kind}] {s.name}: {s.users} users, {args.duration_s} s")
            print(f"  {SCALE_SH} up {s.engine} {s.pairs}")
            print(f"  wait for {URL}/ready 200 x{READY_IN_A_ROW}; one idle docker stats sample")
            if args.warmup_s > 0:
                warm = locust_cmd(s, args.warmup_s, None, args.locust_processes)
                print(f"  {env + ' ' if env else ''}{' '.join(warm)}")
            cmd = locust_cmd(s, args.duration_s, NOTES / "raw" / s.name, args.locust_processes)
            print(f"  {env + ' ' if env else ''}{' '.join(cmd)}")
            print(f"  meanwhile every {SAMPLE_EVERY_S:g} s: docker stats of project {PROJECT}")
            print(f"  {SCALE_SH} down")
        return 0
    results = load_results(args.results)
    results["host"] = host_info()
    failed: list[str] = []
    for s in todo:
        print(f"\n=== {s.name} ===", flush=True)
        try:
            outcome = run_scenario(s, args)
        except (subprocess.SubprocessError, TimeoutError, OSError, KeyError) as exc:
            print(f"run_matrix: {s.name} failed: {exc}", file=sys.stderr)
            failed.append(s.name)
            continue
        merge(results, s, outcome)
        save_results(args.results, results)
        print(
            f"{s.name}: {outcome['rps']} rps, p50 {outcome['p50_ms']} ms,"
            f" p95 {outcome['p95_ms']} ms, failures {outcome['failures']}/{outcome['requests']}"
        )
    print(f"\nrun_matrix: results in {args.results}")
    if failed:
        print(f"run_matrix: failed scenarios: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
