"""`python -m bakeoff smoke | run`."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from bakeoff.config import ROUTES, Hosted, Route
from bakeoff.procs import Cleanup, install_signal_handlers
from bakeoff.registry import ENGINES, LANES, Engine, check_hosted, engine_names, get_engine
from bakeoff.report import render_json, render_markdown
from bakeoff.runner import Cell, plan, run_cell, run_target, smoke_line
from bakeoff.tasks import TASKS, get_tasks

__all__ = ["USAGE", "main"]

USAGE = """usage: python -m bakeoff smoke [--engine A,B] [--lane L,M]
       python -m bakeoff run [--tasks smoke,simplifier,lookup] [--repeat N] [--out DIR]
                             [--engine A,B] [--lane L,M]
       python -m bakeoff run --target ENGINE=URL [ENGINE=URL ...] [--model-log FILE]
                             [--tasks ...] [--repeat N] [--out DIR]
       python -m bakeoff run --model-url URL --model-key-env NAME [--route big-default|local-small]
                             [--tasks ...] [--repeat N] [--out DIR] [--engine A,B] [--lane L,M]

smoke  starts the processes of every engine x lane the trust rule allows and runs `simplify:
       Hello.`; prints PASS, FAIL <reason>, or SKIP <reason> per line; exits 1 on any FAIL.
run    the same matrix with the tasks; writes results.json and results.md to --out.
       With --target it measures chassis instances that already run, by URL.
       With --model-url (hosted mode) the chassis processes call that LiteLLM URL with the key in
       the variable NAME, on the given route, instead of the fake model server. Only trusted
       engines run that way; the default is every trusted engine in the inprocess and sidecar
       lanes. There is no prompt-bytes column in this mode.
"""

MODEL_FAKE = "fake model server (scripted)"
MODEL_TARGET = "whatever the targets are configured with"
HOSTED_LANES = ["inprocess", "sidecar"]
RUN_KEY_ENV = "POC06_LITELLM_KEY"
"""The one variable hosted mode reads: the per-run LiteLLM key. A provider key never comes here."""
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _hosted_label(hosted: Hosted) -> str:
    """Where the model is, without any user info or query a URL might carry."""
    parts = urlsplit(hosted.url)
    port = f":{parts.port}" if parts.port else ""
    return f"hosted via LiteLLM at {parts.scheme}://{parts.hostname}{port}, route {hosted.route}"


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
            p.add_argument("--model-url", help="hosted mode: the LiteLLM base URL, ends in /v1")
            p.add_argument(
                "--model-key-env", help="hosted mode: the variable that holds the LiteLLM key"
            )
            p.add_argument("--route", choices=ROUTES, help="hosted mode: spec.model.route")
    return parser


def _hosted(args: argparse.Namespace) -> Hosted | None:
    """The hosted-mode settings, or None. The key's value is read here and never printed."""
    url, key_env, route = args.model_url, args.model_key_env, args.route
    if not url:
        if key_env or route:
            raise ValueError("--model-key-env and --route need --model-url")
        return None
    if args.target:
        raise ValueError("--model-url and --target cannot be used together")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("--model-url must be an http or https URL")
    if parts.hostname not in LOOPBACK_HOSTS:
        raise ValueError("--model-url must point at this host (127.0.0.1, localhost, or ::1)")
    if "remote" in (args.lane or ()):
        raise ValueError("hosted mode does not run the remote lane (it needs a sandbox on kind)")
    if not key_env:
        raise ValueError("--model-url needs --model-key-env (the variable that holds the key)")
    if key_env != RUN_KEY_ENV:
        raise ValueError(f"--model-key-env must be {RUN_KEY_ENV}, the per-run LiteLLM key")
    key = os.environ.get(key_env)
    if not key:
        raise ValueError(f"the variable {key_env} is not set")
    chosen: Route = route or "big-default"
    return Hosted(url.rstrip("/"), key, chosen)


def _engines(names: Sequence[str] | None, hosted: Hosted | None = None) -> list[Engine]:
    """The engines to run. In hosted mode an untrusted one is refused, and the default is the
    trusted ones."""
    if names:
        engines = [get_engine(n) for n in names]
        if hosted is not None:
            for engine in engines:
                check_hosted(engine)
        return engines
    return [e for e in ENGINES if e.trusted] if hosted is not None else list(ENGINES)


def _lanes(lanes: Sequence[str] | None, hosted: Hosted | None) -> list[str] | None:
    if lanes:
        return list(lanes)
    return list(HOSTED_LANES) if hosted is not None else None


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
    hosted = _hosted(args)
    if args.target:
        for spec in args.target:
            engine, sep, url = spec.partition("=")
            if not sep or not engine or not url:
                raise ValueError(f"--target wants ENGINE=URL, got {spec!r}")
            cells.append(run_target(engine, url, tasks, args.repeat, args.model_log, _say))
        model = MODEL_TARGET
    else:
        engines = _engines(args.engine, hosted)
        for item in plan(engines, _lanes(args.lane, hosted)):
            _say(f"{item.engine.name} {item.lane}")
            cells.append(run_cell(item, tasks, args.repeat, cleanup, _say, hosted))
        model = MODEL_FAKE if hosted is None else _hosted_label(hosted)
    meta = {
        "tasks": [t.name for t in tasks],
        "repeat": args.repeat,
        "model": model,
        "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target_mode": bool(args.target),
        "hosted": hosted is not None,
        "route": hosted.route if hosted else "big-default",
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
