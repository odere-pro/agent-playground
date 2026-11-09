"""The PoC-4 load matrix on the Compose scale stack (plan, section 9; criteria 4 and 11).

    make load-test ARGS="--dry-run"
    make load-test ARGS="--engine echo-python --pairs 1"
    uv run python -m load_tools.run_matrix --only hop

One scenario at a time, because the Docker VM is shared and small:

1. `deploy/compose/scale.sh up <engine> <pairs>`, then wait until `/ready` answers 200 several
   times in a row through Traefik (127.0.0.1:18080).
2. One idle `docker stats` sample, then a Locust warm-up whose numbers are thrown away.
3. Locust headless for `--duration-s`, while `docker stats` samples every container of Compose
   project `poc04` every 2 s. Locust is never a workspace dependency: before the first scenario
   `main` installs it (`install_locust`) into a venv in a new temp dir (`locust_venv`) from the
   hashed lock `LOCUST_LOCK`, with `uv pip install --require-hashes --no-deps`.
4. `scale.sh down`, always, even when a step failed.

Scenarios: `main` is every engine (echo-python, echo-pydanticai, echo-langgraph, echo-openai-agents,
echo-typescript)
at every pair count (1, 2, 4) with `--users`. `hop` is echo-python at 1 pair with `--hop-users`,
in the sidecar lane and in the inprocess lane (`packages/chassis/configs/scale-inprocess.yaml`);
the hop is sidecar minus inprocess, at p50 and p95 as Locust sees it (`hop_ms`) and as the
chassis's own timer sees it (`chassis_hop_ms`: the `chassis.engine.run` histogram on `/metrics`,
read before and after the measured run of every 1-pair scenario, `engine_run`). `idem` is
echo-python at 1 pair with no
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
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
# packages/load-tools/src/load_tools: the workspace installs this package editable, from source.
ROOT = HERE.parents[3]
SCALE_SH = ROOT / "deploy" / "compose" / "scale.sh"
# The default output: the PoC-4 evidence folder, where the dated results live.
NOTES = ROOT / "pocs" / "poc-04-stateless-scalable" / "notes" / "load"
# Locust loads this file by path (`locust -f`), outside the workspace, so it imports no package.
LOCUSTFILE = HERE / "locustfile.py"
LOCUST_VERSION = "2.46.6"  # the newest 2.x on 2026-10-01
# Locust and every dependency, each pinned by sha256 (`requirements.in` names the inputs). To bump:
# uv pip compile packages/load-tools/locust/requirements.in --generate-hashes --universal
#   --python-version 3.12 -o packages/load-tools/locust/requirements.txt
LOCUST_LOCK = HERE.parents[1] / "locust" / "requirements.txt"
# What `--dry-run` prints for the venv `locust_venv` makes: a new temp dir on every run.
PLAN_VENV = Path("<temp dir>/venv")
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


def locust_bin(venv: Path) -> Path:
    return venv / "bin" / "locust"


def locust_install_argv(venv: Path) -> list[list[str]]:
    """The commands that install Locust into `venv` from `LOCUST_LOCK`, in order: a fresh venv,
    then every pinned file by hash and nothing else."""
    return [
        ["uv", "venv", "--clear", "--quiet", "--python", sys.executable, str(venv)],
        [
            "uv",
            "pip",
            "install",
            "--quiet",
            "--python",
            str(venv / "bin" / "python"),
            "--require-hashes",
            "--no-deps",
            "-r",
            str(LOCUST_LOCK),
        ],
    ]


def install_locust(venv: Path) -> None:
    """Install Locust into `venv` from its lock. Raises `subprocess.CalledProcessError`."""
    for argv in locust_install_argv(venv):
        run(argv)


@contextmanager
def locust_venv() -> Iterator[Path]:
    """Where `install_locust` puts this run's Locust: a new private temp dir, removed on exit, so
    no other run and no other user shares it."""
    with tempfile.TemporaryDirectory(prefix="load-tools-locust-") as tmp:
        yield Path(tmp) / "venv"


def locust_cmd(
    s: Scenario,
    seconds: int,
    csv_prefix: Path | None,
    processes: int,
    venv: Path,
) -> list[str]:
    """One Locust run of `s`, from the venv `locust_venv` filled."""
    cmd = [str(locust_bin(venv)), "-f", str(LOCUSTFILE)]
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
    """Per role: the mean of each replica's mean, the p95 over every sample of every replica
    (122 X-5 reads the sidecar at p95), and the max over replicas, of CPU and memory."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_name.setdefault(row["name"], []).append(row)
    by_role: dict[str, list[list[dict[str, Any]]]] = {}
    for name, samples in by_name.items():
        by_role.setdefault(role(name), []).append(samples)
    out: dict[str, dict[str, float]] = {}
    for name, replicas in by_role.items():
        every = [s for r in replicas for s in r]
        out[name] = {
            "replicas": len(replicas),
            "vcpu_mean": round(statistics.fmean(_mean(r, "vcpu") for r in replicas), 4),
            "vcpu_p95": round(_p95(every, "vcpu"), 4),
            "vcpu_max": round(max(max(s["vcpu"] for s in r) for r in replicas), 4),
            "mib_mean": round(statistics.fmean(_mean(r, "mib") for r in replicas), 2),
            "mib_p95": round(_p95(every, "mib"), 2),
            "mib_max": round(max(max(s["mib"] for s in r) for r in replicas), 2),
        }
    return out


def _mean(samples: Sequence[dict[str, Any]], key: str) -> float:
    return statistics.fmean(s[key] for s in samples)


def _p95(samples: Sequence[dict[str, Any]], key: str) -> float:
    """The nearest-rank 95th percentile: the value at least 95% of the samples are at or below."""
    ordered = sorted(float(s[key]) for s in samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


# The chassis-side timer (122 X-5) -------------------------------------------------------------

ENGINE_FAMILY = "chassis_engine_run_duration_seconds"
"""The chassis's own histogram of the A2A call to the workload (`chassis.engine.run`), by lane
and status, on `GET /metrics`. Read for a 1-pair scenario only: Traefik sends each scrape to one
replica, and with one pair that is the replica under load."""
_SAMPLE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)\{(?P<labels>[^}]*)\} (?P<value>\S+)$")
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class Histogram:
    """One Prometheus histogram: the cumulative count at each finite bound, the sum, the count."""

    buckets: dict[float, int]
    total: float
    count: int


def engine_histogram(text: str) -> Histogram:
    """The `status="ok"` series of `ENGINE_FAMILY` in the metrics text, summed over the other
    labels (one lane per scenario)."""
    buckets: dict[float, int] = {}
    total, count = 0.0, 0
    for line in text.splitlines():
        m = _SAMPLE.match(line.strip())
        if m is None or not m["name"].startswith(ENGINE_FAMILY):
            continue
        labels = dict(_LABEL.findall(m["labels"]))
        if labels.get("status") != "ok":
            continue
        suffix = m["name"].removeprefix(ENGINE_FAMILY)
        if suffix == "_bucket" and labels.get("le") not in (None, "+Inf"):
            le = float(labels["le"])
            buckets[le] = buckets.get(le, 0) + int(float(m["value"]))
        elif suffix == "_sum":
            total += float(m["value"])
        elif suffix == "_count":
            count += int(float(m["value"]))
    return Histogram(dict(sorted(buckets.items())), total, count)


def _quantile(q: float, buckets: dict[float, int], count: int) -> float:
    """Prometheus's `histogram_quantile`: linear inside the bucket that holds the rank; past the
    last finite bound, that bound."""
    rank = q * count
    lower, before = 0.0, 0
    for upper, cumulative in sorted(buckets.items()):
        if cumulative >= rank:
            inside = cumulative - before
            return lower if inside == 0 else lower + (upper - lower) * (rank - before) / inside
        lower, before = upper, cumulative
    return lower


def engine_timer(after: Histogram, before: Histogram) -> dict[str, float]:
    """The measured window, `after` minus `before` (the warm-up is not kept), in milliseconds:
    the exact mean, and p50 and p95 interpolated inside their bucket."""
    buckets = {le: n - before.buckets.get(le, 0) for le, n in after.buckets.items()}
    count = after.count - before.count
    total = after.total - before.total
    if count <= 0:
        return {"count": 0, "mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0}
    return {
        "count": count,
        "mean_ms": round(total / count * 1000, 2),
        "p50_ms": round(_quantile(0.5, buckets, count) * 1000, 2),
        "p95_ms": round(_quantile(0.95, buckets, count) * 1000, 2),
    }


def scrape_engine_histogram() -> Histogram | None:
    """`GET /metrics` through Traefik; None when the scrape fails (the run goes on without it)."""
    try:
        res = httpx.get(f"{URL}/metrics", timeout=5)
        res.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"run_matrix: /metrics not read ({type(exc).__name__}); no chassis-side timer")
        return None
    return engine_histogram(res.text)


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


def run_scenario(s: Scenario, args: argparse.Namespace, venv: Path) -> dict[str, Any]:
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
            run(locust_cmd(s, args.warmup_s, None, args.locust_processes, venv), s.env)
        before = scrape_engine_histogram() if s.pairs == 1 else None
        sampler = Sampler(names, SAMPLE_EVERY_S)
        sampler.start()
        try:
            run(locust_cmd(s, args.duration_s, raw / s.name, args.locust_processes, venv), s.env)
        finally:
            rows = sampler.stop()
            write_samples(stats_csv, rows, "load")
        after = scrape_engine_histogram() if before is not None else None
    finally:
        run([str(SCALE_SH), "down"])
    result = read_locust(raw / s.name)
    if before is not None and after is not None:
        result["engine_run"] = engine_timer(after, before)
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


def diff(
    a: dict[str, Any] | None,
    b: dict[str, Any] | None,
    keys: Sequence[str] = ("p50_ms", "p95_ms"),
) -> dict[str, float] | None:
    if not a or not b:
        return None
    return {k: round(a[k] - b[k], 2) for k in keys}


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
    hop["chassis_hop_ms"] = diff(
        _timed(hop.get("sidecar")),
        _timed(hop.get("inprocess")),
        keys=("mean_ms", "p50_ms", "p95_ms"),
    )
    idem = results.get("idempotency")
    if idem is not None:
        keyed = results.get("engines", {}).get("echo-python", {}).get("1")
        idem["cost_ms"] = diff(keyed, idem.get("no_key"))


def _timed(run_result: dict[str, Any] | None) -> dict[str, Any] | None:
    """A run's chassis-side timer, or None when it was not read or saw no run."""
    timer = (run_result or {}).get("engine_run")
    return timer if timer and timer.get("count") else None


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


def print_plan(todo: Sequence[Scenario], args: argparse.Namespace) -> None:
    """What `main` would run, in order, without running it."""
    print(f"run_matrix: {len(todo)} scenario(s), one at a time; results -> {args.results}")
    print("\ninstall Locust from its hashed lock:")
    for argv in locust_install_argv(PLAN_VENV):
        print(f"  {' '.join(argv)}")
    for s in todo:
        env = " ".join(f"{k}={v}" for k, v in s.env.items())
        print(f"\n[{s.kind}] {s.name}: {s.users} users, {args.duration_s} s")
        print(f"  {SCALE_SH} up {s.engine} {s.pairs}")
        print(f"  wait for {URL}/ready 200 x{READY_IN_A_ROW}; one idle docker stats sample")
        if args.warmup_s > 0:
            warm = locust_cmd(s, args.warmup_s, None, args.locust_processes, PLAN_VENV)
            print(f"  {env + ' ' if env else ''}{' '.join(warm)}")
        raw = NOTES / "raw" / s.name
        cmd = locust_cmd(s, args.duration_s, raw, args.locust_processes, PLAN_VENV)
        print(f"  {env + ' ' if env else ''}{' '.join(cmd)}")
        print(f"  meanwhile every {SAMPLE_EVERY_S:g} s: docker stats of project {PROJECT}")
        print(f"  {SCALE_SH} down")


def run_all(todo: Sequence[Scenario], args: argparse.Namespace, venv: Path) -> list[str]:
    """Run each scenario and merge it into `--results`. Returns the names that failed."""
    results = load_results(args.results)
    results["host"] = host_info()
    failed: list[str] = []
    for s in todo:
        print(f"\n=== {s.name} ===", flush=True)
        try:
            outcome = run_scenario(s, args, venv)
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
    return failed


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    todo = scenarios(args)
    if not todo:
        print("run_matrix: nothing selected")
        return 2
    if args.dry_run:
        print_plan(todo, args)
        return 0
    with locust_venv() as venv:
        try:
            install_locust(venv)
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"run_matrix: Locust install failed: {exc}", file=sys.stderr)
            return 1
        failed = run_all(todo, args, venv)
    print(f"\nrun_matrix: results in {args.results}")
    if failed:
        print(f"run_matrix: failed scenarios: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
