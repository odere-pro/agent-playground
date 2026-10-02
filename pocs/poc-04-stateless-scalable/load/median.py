"""Fold the load passes into one `notes/load/results.json`: per scenario, the pass with the median
RPS (plan, section 13). Single runs on the shared Docker Desktop VM varied up to 3x on
2026-10-01, so `run_passes.sh` runs the matrix more than once and this keeps every pass's value.

    uv run python pocs/poc-04-stateless-scalable/load/median.py

Each `engines.<engine>.<pairs>` is the full record of the median pass, plus `passes_rps` (every
pass, in pass order) and `median_pass`. `hop` and `idempotency` take the pass with the median
`hop_ms.p50_ms` and `cost_ms.p50_ms`, plus every pass's value.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOAD = Path(__file__).resolve().parents[1] / "notes" / "load"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_matrix import ok_rps, per_role, read_locust  # noqa: E402

ENGINES = ("echo-python", "echo-pydanticai", "echo-langgraph", "echo-typescript")
PAIRS = ("1", "2", "4")


def rebuild(raw: Path, engine: str, pairs: str) -> dict[str, Any] | None:
    """A main scenario that `run_matrix.py` dropped because Locust exited 1 on failed requests
    (before `--exit-code-on-error 0`), rebuilt from its raw CSVs with run_matrix's own code.
    None when the measured run never started (the warm-up failed, so there is no stats CSV)."""
    prefix = raw / f"{engine}-{pairs}p"
    if not Path(f"{prefix}_stats.csv").exists():
        return None
    result = read_locust(prefix)
    rows: dict[str, list[dict[str, Any]]] = {"idle": [], "load": []}
    with open(f"{prefix}-docker-stats.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows[row["phase"]].append(
                {"name": row["name"], "vcpu": float(row["vcpu"]), "mib": float(row["mib"])}
            )
    roles, idle = per_role(rows["load"]), per_role(rows["idle"])
    for name, numbers in roles.items():
        numbers["idle_vcpu"] = idle.get(name, {}).get("vcpu_mean", 0.0)
        numbers["idle_mib"] = idle.get(name, {}).get("mib_mean", 0.0)
    if "chassis" in roles and result["rps"] > 0:
        total = roles["chassis"]["vcpu_mean"] * roles["chassis"]["replicas"]
        roles["chassis"]["vcpu_per_100rps"] = round(total / result["rps"] * 100, 4)
    return {
        **result,
        "engine": engine,
        "pairs": int(pairs),
        "containers": roles,
        "rebuilt_from_raw": True,
    }


def median_index(values: list[float]) -> int:
    """The index of the median value (the lower one for an even count)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    return order[(len(order) - 1) // 2]


def main() -> int:
    passes: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(LOAD.glob("passes/[1-9]*/results.json")):
        data = json.loads(path.read_text())
        for engine in ENGINES:
            for pairs in PAIRS:
                by = data.setdefault("engines", {}).setdefault(engine, {})
                if pairs not in by:
                    rebuilt = rebuild(path.parent / "raw", engine, pairs)
                    if rebuilt is not None:
                        by[pairs] = rebuilt
                else:
                    r = by[pairs]
                    r.setdefault("ok_rps", ok_rps(r["requests"], r["failures"], r["rps"]))
        passes.append((path.parent.name, data))
    if not passes:
        raise SystemExit("median.py: no notes/load/passes/<n>/results.json")
    out: dict[str, Any] = {
        "version": 1,
        "updated": datetime.now(UTC).isoformat(timespec="seconds"),
        "method": f"median of {len(passes)} passes per scenario (load/run_passes.sh, median.py)",
        "host": passes[-1][1].get("host", {}),
        "engines": {},
    }
    keys = {(e, p) for _, d in passes for e, v in d.get("engines", {}).items() for p in v}
    for engine, pairs in sorted(keys):
        runs = [
            (n, d["engines"][engine][pairs])
            for n, d in passes
            if pairs in d.get("engines", {}).get(engine, {})
        ]
        rps = [float(r["ok_rps"]) for _, r in runs]
        i = median_index(rps)
        record = dict(runs[i][1])
        record["passes_ok_rps"] = {n: r["ok_rps"] for n, r in runs}
        record["passes_failures"] = {n: f"{r['failures']}/{r['requests']}" for n, r in runs}
        record["median_pass"] = runs[i][0]
        out["engines"].setdefault(engine, {})[pairs] = record
    for key, metric in (("hop", "hop_ms"), ("idempotency", "cost_ms")):
        runs2 = [(n, d[key]) for n, d in passes if d.get(key, {}).get(metric)]
        if not runs2:
            continue
        p50 = [float(r[metric]["p50_ms"]) for _, r in runs2]
        i = median_index(p50)
        record = dict(runs2[i][1])
        record["passes"] = {n: r[metric] for n, r in runs2}
        record["median_pass"] = runs2[i][0]
        record[f"{metric}_median_p50"] = statistics.median(p50)
        out[key] = record
    (LOAD / "results.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    for engine, by in out["engines"].items():
        line = ", ".join(
            f"{p}p {r['ok_rps']} ok rps {r['passes_ok_rps']} {r['passes_failures']}"
            for p, r in sorted(by.items())
        )
        print(f"{engine}: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
