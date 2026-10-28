"""The matrix: which engine runs in which lane, and one cell's run."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

import httpx

from bakeoff.config import Hosted
from bakeoff.lines import count_files
from bakeoff.measure import CallSource, JsonlCalls, TaskResult, measure_task
from bakeoff.procs import Cleanup
from bakeoff.registry import ROOT, Engine, Lane, get_engine, lanes_for
from bakeoff.stack import StackError, running_stack
from bakeoff.tasks import Task

__all__ = ["Cell", "Planned", "plan", "run_cell", "run_target", "smoke_line"]

Status = Literal["ok", "fail", "skip"]
Log = Callable[[str], None]


@dataclass
class Planned:
    engine: Engine
    lane: Lane
    skip: str | None = None


@dataclass
class Cell:
    engine: str
    lane: str
    status: Status
    reason: str = ""
    code_lines: int | None = None
    mapping_files: list[str] = field(default_factory=list)
    tasks: dict[str, TaskResult] = field(default_factory=dict)


def plan(engines: Sequence[Engine], lanes: Sequence[str] | None = None) -> list[Planned]:
    """Every engine x lane the trust rule allows, with the reason when one cannot run.

    An engine that cannot run at all (kagent-adk, a missing build) is SKIP in each of its lanes.
    """
    rows: list[Planned] = []
    for engine in engines:
        reason = engine.skip_reason()
        rows.extend(
            Planned(engine, lane, reason)
            for lane in lanes_for(engine)
            if lanes is None or lane in lanes
        )
    return rows


def _lines(engine: Engine) -> tuple[int | None, list[str]]:
    counts = count_files(ROOT, engine.mapping_files)
    if not counts:
        return None, []
    return sum(c.code for c in counts), [c.path for c in counts]


def run_cell(
    item: Planned,
    tasks: Sequence[Task],
    repeat: int,
    cleanup: Cleanup,
    log: Log,
    hosted: Hosted | None = None,
) -> Cell:
    """Start the stack for `item`, run `tasks` `repeat` times each, and stop the stack."""
    lines, files = _lines(item.engine)
    cell = Cell(item.engine.name, item.lane, "ok", code_lines=lines, mapping_files=files)
    if item.skip:
        cell.status, cell.reason = "skip", item.skip
        return cell
    try:
        with (
            running_stack(item.engine, item.lane, cleanup, hosted) as stack,
            httpx.Client(base_url=stack.url, trust_env=False, timeout=120.0) as client,
        ):
            measure_task(client, tasks[0], 1, None)  # warm-up: not counted
            for task in tasks:
                log(f"  {item.engine.name} {item.lane}: {task.name} x{repeat}")
                cell.tasks[task.name] = measure_task(client, task, repeat, stack.fake, log)
    except StackError as exc:
        cell.status, cell.reason = "fail", str(exc)
    return cell


def run_target(
    engine_name: str,
    url: str,
    tasks: Sequence[Task],
    repeat: int,
    model_log: str | None,
    log: Log,
) -> Cell:
    """The tasks against a chassis that is already running at `url`. The lane is `target`."""
    try:
        lines, files = _lines(get_engine(engine_name))
    except KeyError:
        lines, files = None, []
    cell = Cell(engine_name, "target", "ok", code_lines=lines, mapping_files=files)
    source: CallSource | None = None
    if model_log:
        from pathlib import Path

        source = JsonlCalls(Path(model_log))
    try:
        with httpx.Client(base_url=url.rstrip("/"), trust_env=False, timeout=300.0) as client:
            measure_task(client, tasks[0], 1, None)
            for task in tasks:
                log(f"  {engine_name} target: {task.name} x{repeat}")
                cell.tasks[task.name] = measure_task(client, task, repeat, source, log)
    except httpx.HTTPError as exc:
        cell.status, cell.reason = "fail", f"{type(exc).__name__} reaching the target"
    return cell


def smoke_line(cell: Cell) -> str:
    """`PASS`, `FAIL <reason>`, or `SKIP <reason>`, then the engine and the lane."""
    if cell.status == "ok":
        result = cell.tasks.get("smoke")
        if result is None or result.passed != result.runs or result.runs == 0:
            reason = result.failures[0] if result and result.failures else "smoke did not pass"
            cell.status, cell.reason = "fail", reason
    head = {"ok": "PASS", "fail": "FAIL", "skip": "SKIP"}[cell.status]
    tail = f"  {cell.reason}" if cell.reason else ""
    return f"{head:<5}{cell.engine:<20}{cell.lane:<10}{tail}".rstrip()
