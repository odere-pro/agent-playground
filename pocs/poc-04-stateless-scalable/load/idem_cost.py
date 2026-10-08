"""The cost of idempotency on one pair, measured back to back (plan, section 4; review of
2026-10-01). Needs `deploy/compose/scale.sh up echo-python 1` first; the stack is not restarted
between runs, so keyed and unkeyed runs alternate on the same containers.

For each point (`rate10`: 10 users at 1 call/s; `rate30`: 30 users at 1 call/s; `sat`: 64 users
with no wait) and each alternation, one keyed run (a fresh `Idempotency-Key` per call) then one
unkeyed run (`LOAD_NO_KEY=1`), `--duration-s` each. Per run: RPS, p50, p95, the chassis's mean
vCPU (docker stats every 2 s), chassis CPU ms per call, and Valkey commands per call (the
difference of `total_commands_processed`, read inside the Valkey container; the password stays
in that container's env and is never printed). Writes `notes/load/idem-cost.json`.

    uv run python pocs/poc-04-stateless-scalable/load/idem_cost.py
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_matrix import LOCUST, LOCUSTFILE, URL, read_locust, sample_stats  # noqa: E402

OUT = HERE.parent / "notes" / "load" / "idem-cost.json"
RAW = HERE.parent / "notes" / "load" / "idem-raw"
CHASSIS, VALKEY = "poc04-chassis-1-1", "poc04-valkey-1"
# suggested: name -> (users, calls per second per user or "" for no wait, alternations, processes)
POINTS: dict[str, tuple[int, str, int, int]] = {
    "rate10": (10, "1", 3, 1),
    "rate30": (30, "1", 3, 1),
    "sat": (64, "", 2, 4),
}


def valkey_commands() -> int:
    script = 'REDISCLI_AUTH="$VALKEY_PASSWORD" valkey-cli info stats'
    out = subprocess.run(
        ["docker", "exec", VALKEY, "sh", "-c", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    for line in out.splitlines():
        if line.startswith("total_commands_processed:"):
            return int(line.split(":")[1])
    raise RuntimeError("no total_commands_processed")


def one_run(
    name: str, users: int, rate: str, keyed: bool, seconds: int, procs: int
) -> dict[str, Any]:
    env = {**os.environ}
    if rate:
        env["LOAD_RATE_PER_USER"] = rate
    if not keyed:
        env["LOAD_NO_KEY"] = "1"
    prefix = RAW / name
    cmd = [
        "uv",
        "run",
        "--no-project",
        "--with",
        LOCUST,
        "locust",
        "-f",
        str(LOCUSTFILE),
        "--host",
        URL,
        "--headless",
        "-u",
        str(users),
        "-r",
        str(users),
        "--run-time",
        f"{seconds}s",
        "--only-summary",
        "--stop-timeout",
        "35",
        "--exit-code-on-error",
        "0",
        "--csv",
        str(prefix),
    ]
    if procs > 1:
        cmd += ["--processes", str(procs)]
    rows: list[dict[str, Any]] = []
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            rows.extend(sample_stats([CHASSIS, VALKEY]))
            stop.wait(2.0)

    before = valkey_commands()
    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    subprocess.run(cmd, env=env, check=True, timeout=seconds + 120, capture_output=True)
    stop.set()
    thread.join(timeout=60)
    commands = valkey_commands() - before
    result = read_locust(prefix)
    cpu = [r["vcpu"] for r in rows if r["name"] == CHASSIS]
    vk = [r["vcpu"] for r in rows if r["name"] == VALKEY]
    vcpu = statistics.mean(cpu) if cpu else 0.0
    rps = result["ok_rps"] or 1e-9
    return {
        **result,
        "name": name,
        "keyed": keyed,
        "chassis_vcpu_mean": round(vcpu, 4),
        "chassis_cpu_ms_per_call": round(vcpu / rps * 1000, 2),
        "valkey_vcpu_mean": round(statistics.mean(vk), 4) if vk else 0.0,
        "valkey_commands_per_call": round(commands / max(result["requests"], 1), 2),
    }


def summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for keyed in (True, False):
        sel = [r for r in runs if r["keyed"] is keyed]
        out["keyed" if keyed else "unkeyed"] = {
            k: {"values": [r[k] for r in sel], "median": statistics.median(r[k] for r in sel)}
            for k in (
                "ok_rps",
                "p50_ms",
                "p95_ms",
                "chassis_cpu_ms_per_call",
                "valkey_commands_per_call",
            )
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="keyed vs unkeyed, back to back")
    parser.add_argument("--duration-s", type=int, default=60)
    parser.add_argument("--only", action="append", choices=list(POINTS))
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = json.loads(OUT.read_text()) if OUT.exists() else {"points": {}}
    for point in args.only or list(POINTS):
        users, rate, alternations, procs = POINTS[point]
        runs = []
        for i in range(1, alternations + 1):
            for keyed in (True, False):
                name = f"{point}-{i}-{'key' if keyed else 'nokey'}"
                r = one_run(name, users, rate, keyed, args.duration_s, procs)
                print(
                    f"{name}: {r['ok_rps']} rps, p50 {r['p50_ms']} ms, p95 {r['p95_ms']} ms, "
                    f"chassis {r['chassis_vcpu_mean']} vCPU = {r['chassis_cpu_ms_per_call']} "
                    f"ms/call, valkey {r['valkey_commands_per_call']} cmd/call, "
                    f"failures {r['failures']}/{r['requests']}",
                    flush=True,
                )
                runs.append(r)
        report["points"][point] = {
            "users": users,
            "rate_per_user": rate or "none",
            "duration_s": args.duration_s,
            "runs": runs,
            "summary": summary(runs),
        }
        report["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
        OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
