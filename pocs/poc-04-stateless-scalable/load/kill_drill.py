"""The PoC-4 replica drills on the Compose scale stack (plan, sections 6 and 10; criteria 3 and 6).

    uv run python pocs/poc-04-stateless-scalable/load/kill_drill.py --mode kill --pair 2
    uv run python pocs/poc-04-stateless-scalable/load/kill_drill.py --mode graceful --pair 2
    uv run python pocs/poc-04-stateless-scalable/load/kill_drill.py --mode kill --dry-run

The stack must already run with at least `--pair` pairs (`deploy/compose/scale.sh up <engine> 2`),
or pass `--up <engine>` to bring it up with `--pairs` pairs first and down at the end.

- `--mode kill` (criterion 3): steady load with retries on the same key. After `--act-after-s`,
  `docker kill` (SIGKILL) `poc04-chassis-<pair>-1` and `poc04-workload-<pair>-1`. Then every
  call that needed a retry, and a sample of the rest, is sent once more with the same key and
  input: the answer must be the same envelope (same `request_id` and `output`), marked
  `Idempotent-Replayed: true`. Pass: 0 lost calls and 0 replay mismatches.
- `--mode graceful` (criterion 6): steady load with no retry. `docker kill -s TERM` the chassis,
  wait for it to exit, then `docker stop` the workload (the chassis first: plan, section 6).
  Pass: 0 failed calls.

Only containers named `poc04-*` are touched. The report is printed as JSON and, with `--out`,
written to a file (evidence for `notes/`). Exit code 1 when the drill fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
from steady_client import (
    DEFAULT_URL,
    REPLAYED_HEADER,
    REQUEST_TIMEOUT_S,
    Call,
    Summary,
    body_for,
    print_summary,
    run_steady,
)

ROOT = Path(__file__).resolve().parents[3]
SCALE_SH = ROOT / "deploy" / "compose" / "scale.sh"
PROJECT = "poc04"
# suggested: how many calls that needed no retry are replayed too, as a control.
CONTROL_SAMPLE = 20
# suggested: the chassis drains in `--drain-delay-s 3` plus up to `--drain-timeout-s 30`.
CHASSIS_EXIT_TIMEOUT_S = 45
# suggested: a docker kill or stop on Docker Desktop answered in over 30 s on 2026-10-01.
DOCKER_TIMEOUT_S = 90


def container(service: str, pair: int) -> str:
    name = f"{PROJECT}-{service}-{pair}-1"
    if not name.startswith(f"{PROJECT}-"):  # never touch another project's container
        raise ValueError(name)
    return name


def plan(mode: str, pair: int) -> list[list[str]]:
    """The Docker commands the drill runs, in order."""
    chassis, workload = container("chassis", pair), container("workload", pair)
    if mode == "kill":
        return [["docker", "kill", chassis, workload]]
    return [
        ["docker", "kill", "-s", "TERM", chassis],
        ["docker", "wait", chassis],
        ["docker", "stop", workload],
    ]


def docker(args: list[str], timeout_s: float) -> str:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        # Docker Desktop can take over 30 s to report a kill of a pair whose workload shares the
        # chassis's network namespace (seen 2026-10-01). Record it; the replay check still runs.
        line = f"$ {' '.join(args)} -> no answer after {timeout_s:.0f} s"
        print(line, flush=True)
        return line
    line = f"$ {' '.join(args)} -> exit {done.returncode}"
    if done.returncode != 0:
        line += f": {done.stderr.strip()}"
    print(line, flush=True)
    return line


async def act(mode: str, pair: int, delay_s: float) -> list[str]:
    """Wait `delay_s`, then run the plan's commands off the event loop."""
    await asyncio.sleep(delay_s)
    log = []
    for args in plan(mode, pair):
        timeout = CHASSIS_EXIT_TIMEOUT_S if args[1] == "wait" else DOCKER_TIMEOUT_S
        started = time.monotonic()
        line = await asyncio.to_thread(docker, args, timeout)
        log.append(f"{line} ({time.monotonic() - started:.1f} s)")
    return log


async def replay_check(url: str, calls: Sequence[Call]) -> dict[str, Any]:
    """Send each call again with its key and input; compare with the first answer."""
    same = differs = unmarked = failed = 0
    mismatches: list[str] = []
    async with httpx.AsyncClient(base_url=url, timeout=REQUEST_TIMEOUT_S) as client:
        for call in calls:
            try:
                response = await client.post(
                    "/v1/run", json=body_for(call.text), headers={"Idempotency-Key": call.key}
                )
                again = response.json() if response.status_code == 200 else None
            except (httpx.HTTPError, ValueError) as exc:
                failed += 1
                mismatches.append(f"{call.key}: {type(exc).__name__}")
                continue
            if again is None:
                failed += 1
                mismatches.append(f"{call.key}: HTTP {response.status_code}")
                continue
            first = call.response or {}
            if (again.get("request_id"), again.get("output")) == (
                first.get("request_id"),
                first.get("output"),
            ):
                same += 1
            else:
                differs += 1
                mismatches.append(f"{call.key}: request_id or output differs")
            if response.headers.get(REPLAYED_HEADER, "").lower() != "true":
                unmarked += 1
    return {
        "checked": len(calls),
        "same": same,
        "differs": differs,
        "not_marked_replayed": unmarked,
        "failed": failed,
        "mismatches": mismatches[:20],
    }


async def drill(args: argparse.Namespace) -> dict[str, Any]:
    retry = args.mode == "kill"
    load = asyncio.create_task(
        run_steady(
            args.url,
            concurrency=args.concurrency,
            duration_s=args.duration_s,
            retry=retry,
            max_attempts=args.max_attempts,
            retry_delay_s=args.retry_delay_s,
        )
    )
    actions = await act(args.mode, args.pair, args.act_after_s)
    summary: Summary = await load
    print_summary(summary)
    report: dict[str, Any] = {
        "mode": args.mode,
        "pair": args.pair,
        "actions": actions,
        "load": summary.as_dict(),
    }
    passed = summary.failed == 0 and summary.sent > 0
    if retry:
        ok = [c for c in summary.calls if c.ok]
        retried = [c for c in ok if c.attempts > 1]
        rest = [c for c in ok if c.attempts == 1]
        control = random.sample(rest, min(CONTROL_SAMPLE, len(rest)))
        report["replay_retried"] = await replay_check(args.url, retried)
        report["replay_control"] = await replay_check(args.url, control)
        for key in ("replay_retried", "replay_control"):
            check = report[key]
            passed = passed and check["differs"] == 0 and check["failed"] == 0
            passed = passed and check["not_marked_replayed"] == 0
        report["lost"] = summary.failed
    report["passed"] = passed
    return report


def scale(*argv: str) -> None:
    print(f"$ {SCALE_SH} {' '.join(argv)}", flush=True)
    subprocess.run([str(SCALE_SH), *argv], check=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--mode", choices=("kill", "graceful"), required=True)
    parser.add_argument("--pair", type=int, default=2, help="the pair to kill or stop")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration-s", type=float, default=40.0, help="suggested")
    parser.add_argument("--act-after-s", type=float, default=10.0, help="suggested")
    parser.add_argument("--max-attempts", type=int, default=10)
    parser.add_argument("--retry-delay-s", type=float, default=0.5)
    parser.add_argument("--up", metavar="ENGINE", help="scale.sh up ENGINE --pairs first")
    parser.add_argument("--pairs", type=int, choices=(2, 4), default=2, help="with --up")
    parser.add_argument("--out", type=Path, help="also write the JSON report here")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    args = parser.parse_args(argv)
    if args.pair < 1 or args.pair > 4:
        parser.error("--pair must be 1 to 4")
    if args.up and args.pair > args.pairs:
        parser.error("--pair must be one of the --pairs that --up starts")
    if args.act_after_s >= args.duration_s:
        parser.error("--act-after-s must be less than --duration-s")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        print(f"kill_drill {args.mode}: pair {args.pair}, {args.concurrency} workers,")
        print(f"  {args.duration_s:g} s of load on {args.url}, retry {args.mode == 'kill'}")
        if args.up:
            print(f"  first: {SCALE_SH} up {args.up} {args.pairs}")
        for cmd in plan(args.mode, args.pair):
            print(f"  at {args.act_after_s:g} s: {' '.join(cmd)}")
        if args.mode == "kill":
            print("  then: replay every retried key and a control sample; compare envelopes")
        if args.up:
            print(f"  last: {SCALE_SH} down")
        return 0
    if args.up:
        scale("up", args.up, str(args.pairs))
    try:
        report = asyncio.run(drill(args))
    finally:
        if args.up:
            scale("down")
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
