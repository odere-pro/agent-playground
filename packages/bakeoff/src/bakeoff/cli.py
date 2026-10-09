"""`python -m bakeoff smoke | run`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from bakeoff.procs import Cleanup, install_signal_handlers
from bakeoff.registry import ENGINES, LANES, Engine, engine_names, get_engine
from bakeoff.report import render_json, render_markdown
from bakeoff.runner import Cell, plan, run_cell, run_target, smoke_line
from bakeoff.tasks import TASKS, get_tasks

__all__ = ["USAGE", "main"]

USAGE = """usage: python -m bakeoff smoke [--engine A,B] [--lane L,M]
       python -m bakeoff run [--tasks smoke,simplifier,lookup] [--repeat N] [--out DIR]
                             [--engine A,B] [--lane L,M]
       python -m bakeoff run --target ENGINE=URL [ENGINE=URL ...] [--model-log FILE]
                             [--tasks ...] [--repeat N] [--out DIR]

smoke  starts the processes of every engine x lane the trust rule allows and runs `simplify:
       Hello.`; prints PASS, FAIL <reason>, or SKIP <reason> per line; exits 1 on any FAIL.
run    the same matrix with the tasks; writes results.json and results.md to --out.
       With --target it measures chassis instances that already run, by URL.
"""

MODEL_FAKE = "fake model server (scripted)"
MODEL_TARGET = "whatever the targets are configured with"


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m bakeoff", usage=USAGE)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("smoke", "run"):
        p = sub.add_parser(name)
        p.add_argument("--engine", type=_csv, help=f"comma list of: {', '.join(engine_names())}")
        p.add_argument("--lane", type=_csv, help=f"comma list of: {', '.join(LANES)}")
        if name == "run":
            p.add_argument("--tasks", type=_csv, default=[t.name for t in TASKS])
            p.add_argument("--repeat", type=int, default=3)
            p.add_argument("--out", type=Path, default=Path("bakeoff-results"))
            p.add_argument("--target", nargs="+", metavar="ENGINE=URL")
            p.add_argument("--model-log", help="JSONL request log of the target's model")
    return parser


def _engines(names: Sequence[str] | None) -> list[Engine]:
    return [get_engine(n) for n in names] if names else list(ENGINES)


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _smoke(args: argparse.Namespace) -> int:
    cleanup = Cleanup()
    tasks = get_tasks(["smoke"])
    failed = False
    for item in plan(_engines(args.engine), args.lane):
        cell = run_cell(item, tasks, 1, cleanup, lambda _m: None)
        line = smoke_line(cell)
        print(line, flush=True)
        failed = failed or cell.status == "fail"
    return 1 if failed else 0


def _run(args: argparse.Namespace) -> int:
    if args.repeat < 1:
        raise ValueError("--repeat must be 1 or more")
    tasks = get_tasks(args.tasks)
    cleanup = Cleanup()
    cells: list[Cell] = []
    if args.target:
        for spec in args.target:
            engine, sep, url = spec.partition("=")
            if not sep or not engine or not url:
                raise ValueError(f"--target wants ENGINE=URL, got {spec!r}")
            cells.append(run_target(engine, url, tasks, args.repeat, args.model_log, _say))
        model = MODEL_TARGET
    else:
        for item in plan(_engines(args.engine), args.lane):
            _say(f"{item.engine.name} {item.lane}")
            cells.append(run_cell(item, tasks, args.repeat, cleanup, _say))
        model = MODEL_FAKE
    meta = {
        "tasks": [t.name for t in tasks],
        "repeat": args.repeat,
        "model": model,
        "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target_mode": bool(args.target),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(render_json(cells, meta))
    (args.out / "results.md").write_text(render_markdown(cells, meta))
    print(f"wrote {args.out / 'results.json'} and {args.out / 'results.md'}")
    return 1 if any(c.status == "fail" for c in cells) else 0


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(argv if argv is not None else sys.argv[1:])
    if not args_list:
        print(USAGE, end="")
        return 2
    install_signal_handlers()
    try:
        args = _parser().parse_args(args_list)
        return _smoke(args) if args.command == "smoke" else _run(args)
    except (ValueError, KeyError) as exc:
        print(f"bakeoff: {exc}", file=sys.stderr)
        return 2
