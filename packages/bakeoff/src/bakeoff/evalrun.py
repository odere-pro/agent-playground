"""The PoC-8 eval run: DeepEval on the 50 gate cases, on every engine and every lane it may use.

    uv run python -m bakeoff.evalrun --out eval-reports                       # all, normal logic
    uv run python -m bakeoff.evalrun --engine echo-python --lane sidecar --logic drop-facts --out x
    uv run python -m bakeoff.evalgate --report eval-reports                   # then the gate

Per engine and lane it starts the bake-off stack with the gate model script for `--logic`
(`golden-gate.yaml` or `golden-gate-drop-facts.yaml`, in the engine's dialect) and the gate judge,
and runs DeepEval through the chassis's `/v1/run` (`bakeoff.evaltrial.run_one`). It writes
`deepeval-<engine>-<lane>[-<logic>].json` (`eval-report.v0`, `meta.lane`, `meta.logic`), the raw
report, and the log. An engine that cannot run here is printed as `SKIP` with its reason; the
lanes follow the trust rule (`registry.lanes_for`).

Exit 1 when a stack does not start or DeepEval writes no report; a failing case is a result, and
`bakeoff.evalgate` decides. No key: the model and the judge are the fake model server.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml

from bakeoff.evaltrial import Golden, Tool, run_one
from bakeoff.golden import gate_data
from bakeoff.golden.generate import GATE_JUDGE_SCRIPT, GATE_SCRIPTS
from bakeoff.golden.logic import LOGICS
from bakeoff.procs import Cleanup, install_signal_handlers
from bakeoff.registry import LANES, engine_names, get_engine, lanes_for
from bakeoff.stack import StackError

__all__ = ["gate_golden", "main", "plan", "report_stem"]

TOOL: Tool = "deepeval"


def plan(
    engines: Sequence[str], lanes: Sequence[str]
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """The (engine, lane) runs, in registry order, and the engines skipped here with why."""
    names = engine_names() if "all" in engines else list(engines)
    wanted = set(LANES) if "all" in lanes else set(lanes)
    targets: list[tuple[str, str]] = []
    skipped: dict[str, str] = {}
    for name in names:
        engine = get_engine(name)
        reason = engine.skip_reason()
        if reason:
            skipped[name] = reason
            continue
        targets += [(name, lane) for lane in lanes_for(engine) if lane in wanted]
    return targets, skipped


def gate_golden(logic: str, work: Path) -> Golden:
    """The gate files for `logic`; the 50 cases are written to `work` as the one file the tool
    reads (`GOLDEN_CASES`)."""
    cases_file = work / "gate-cases.yaml"
    cases_file.write_text(yaml.safe_dump(gate_data(), sort_keys=False), encoding="utf-8")
    return Golden(
        cases_file=cases_file,
        model_script=GATE_SCRIPTS[logic],
        judge_script=GATE_JUDGE_SCRIPT,
        meta=(("logic", logic), ("golden", "gate")),
    )


def report_stem(engine: str, lane: str, logic: str) -> str:
    suffix = "" if logic == "normal" else f"-{logic}"
    return f"{TOOL}-{engine}-{lane}{suffix}"


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bakeoff.evalrun")
    parser.add_argument("--engine", type=_csv, default=["all"])
    parser.add_argument("--lane", type=_csv, default=["all"])
    parser.add_argument("--logic", choices=LOGICS, default="normal")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--offline", action="store_true", help="no proxy, warm caches only")
    args = parser.parse_args(argv)
    install_signal_handlers()
    cleanup = Cleanup()
    targets, skipped = plan(args.engine, args.lane)
    for name, reason in skipped.items():
        print(f"SKIP {name}: {reason}", flush=True)
    golden = gate_golden(args.logic, cleanup.tempdir())
    failed = False
    for engine, lane in targets:
        stem = report_stem(engine, lane, args.logic)
        try:
            row = run_one(
                TOOL,
                engine,
                lane,
                args.out,
                offline=args.offline,
                cleanup=cleanup,
                golden=golden,
                stem=stem,
            )
        except (StackError, subprocess.TimeoutExpired, ValueError) as exc:
            print(f"FAIL {engine} {lane}: {exc}", file=sys.stderr, flush=True)
            failed = True
            continue
        print(json.dumps(row), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
