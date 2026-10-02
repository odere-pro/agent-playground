"""Print every P18 run side by side: `uv run python .../notes/dapr/summarize.py`."""

import json
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"


def cell(role: dict[str, float] | None) -> str:
    if not role:
        return "-"
    return (
        f"idle {role['idle_vcpu']:.3f} vCPU {role['idle_mib']:.0f} MiB; "
        f"10 RPS {role['vcpu_mean']:.3f} (max {role['vcpu_max']:.2f}) vCPU "
        f"{role['mib_mean']:.0f} (max {role['mib_max']:.0f}) MiB"
    )


for path in sorted(RAW.glob("*-rep*.json")):
    d = json.loads(path.read_text())
    c, load = d["containers"], d["load"]
    sent = d["warmup"]["requests"] + load["requests"]
    ev = d.get("events_in_window", {}).get("agents.task.completed.v1")
    tot = d.get("consumed_total", {}).get("agents.task.completed.v1")
    failed = d.get("consumed_total", {}).get("agents.task.failed.v1")
    print(f"## {d['name']}")
    print(
        f"  load: {load['requests']} req, {load['failures']} failed, {load['rps']} rps, "
        f"p50 {load['p50_ms']} ms, p95 {load['p95_ms']} ms; warm-up {d['warmup']['requests']}"
    )
    if ev is not None:
        print(
            f"  events: window {ev} of {load['requests']}; consumed {tot} of {sent} sent; "
            f"failed topic {failed}"
        )
    for role in ("chassis", "daprd-1", "workload", "kafka"):
        if role in c:
            print(f"  {role:9} {cell(c[role])}")
