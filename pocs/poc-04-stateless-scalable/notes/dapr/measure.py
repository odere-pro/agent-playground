"""P18 measurement: one event path on the scale stack at 1 pair, echo-python, a fixed 10 RPS.

    uv run python pocs/poc-04-stateless-scalable/notes/dapr/measure.py --events kafka --rep 1

Steps: `scale.sh up echo-python 1 --events <none|kafka|dapr>`, wait for `/ready`, 5 idle
`docker stats` samples 2 s apart, a 10 s warm-up, then 60 s of Locust at 10 users x 1 call/s
(the `rate` scenario of run_matrix.py) while `docker stats` samples project poc04 every 2 s.
With events on, it reads the topic's end offsets before and after the measured window and then
counts every message on `agents.task.completed.v1` and `agents.task.failed.v1` with Kafka's
console consumer inside the Kafka container. Raw output goes to notes/dapr/raw/. `--keep`
leaves the stack up; otherwise it runs `scale.sh down`. Only project poc04 is touched.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "load"))

import run_matrix as rm  # noqa: E402

RAW = HERE / "raw"
KAFKA = "poc04-kafka-1"
TOPICS = ("agents.task.completed.v1", "agents.task.failed.v1")
BIN = "/opt/kafka/bin"


def kafka(cmd: str) -> str:
    out = subprocess.run(
        ["docker", "exec", KAFKA, "sh", "-c", cmd],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    return out.stdout


def end_offset(topic: str) -> int:
    """The sum of the topic's end offsets over its partitions (0 when it does not exist)."""
    text = kafka(f"{BIN}/kafka-get-offsets.sh --bootstrap-server localhost:9092 --topic {topic}")
    return sum(int(line.rsplit(":", 1)[1]) for line in text.splitlines() if line.count(":") == 2)


def consume_count(topic: str) -> int:
    text = kafka(
        f"{BIN}/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic {topic} "
        "--from-beginning --timeout-ms 15000 2>/dev/null"
    )
    return sum(1 for line in text.splitlines() if line.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", choices=("none", "kafka", "dapr"), required=True)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--duration-s", type=int, default=60)
    parser.add_argument("--warmup-s", type=int, default=10)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    name = f"{args.events}-rep{args.rep}"
    RAW.mkdir(parents=True, exist_ok=True)
    stats_csv = RAW / f"{name}-docker-stats.csv"
    stats_csv.unlink(missing_ok=True)
    scenario = rm.Scenario(
        name, "rate", "echo-python", 1, rm.RATE_USERS, {"LOAD_RATE_PER_USER": rm.RATE_PER_USER}
    )
    up = [str(rm.SCALE_SH), "up", "echo-python", "1"]
    if args.events != "none":
        up += ["--events", args.events]
    rm.run(up)
    result: dict[str, Any] = {"name": name, "events": args.events}
    try:
        rm.wait_ready()
        names = rm.project_containers()
        idle: list[dict[str, Any]] = []
        for _ in range(5):
            idle += rm.sample_stats(names)
            time.sleep(2)
        rm.write_samples(stats_csv, idle, "idle")
        rm.run(rm.locust_cmd(scenario, args.warmup_s, RAW / f"{name}-warmup", 4), scenario.env)
        time.sleep(3)
        before = {t: end_offset(t) for t in TOPICS} if args.events != "none" else {}
        sampler = rm.Sampler(names, rm.SAMPLE_EVERY_S)
        sampler.start()
        try:
            rm.run(rm.locust_cmd(scenario, args.duration_s, RAW / name, 4), scenario.env)
        finally:
            rows = sampler.stop()
            rm.write_samples(stats_csv, rows, "load")
        time.sleep(5)
        if args.events != "none":
            after = {t: end_offset(t) for t in TOPICS}
            result["offsets_before"], result["offsets_after"] = before, after
            result["events_in_window"] = {t: after[t] - before[t] for t in TOPICS}
            result["consumed_total"] = {t: consume_count(t) for t in TOPICS}
    finally:
        if not args.keep:
            rm.run([str(rm.SCALE_SH), "down"])
    result["warmup"] = rm.read_locust(RAW / f"{name}-warmup")
    result["load"] = rm.read_locust(RAW / name)
    roles = rm.per_role(rows)
    idle_roles = rm.per_role(idle)
    for role, numbers in roles.items():
        numbers["idle_vcpu"] = idle_roles.get(role, {}).get("vcpu_mean", 0.0)
        numbers["idle_mib"] = idle_roles.get(role, {}).get("mib_mean", 0.0)
    result["containers"] = roles
    result["host"] = rm.host_info()
    (RAW / f"{name}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
