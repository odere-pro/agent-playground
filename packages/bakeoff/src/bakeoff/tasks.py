"""The three bake-off tasks and their pass checks (docs/plans/2026-10-09-poc-06-bake-off.md).

| Task       | Pass when                                                                       |
| smoke      | `end{status: ok}` after at least one `delta`                                    |
| simplifier | the output holds `2026`, `Acme`, and `30`                                       |
| lookup     | `glossary_lookup{term: SLM}`, then `acronym_expand{acronym: RAG}`, each valid   |
|            | against the tool's schema; the answer holds both expansions                     |
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import jsonschema
from chassis.fakes.tool import ACRONYM_EXPAND, GLOSSARY_LOOKUP

__all__ = [
    "TASKS",
    "TOOL_SCHEMAS",
    "Check",
    "RunView",
    "Task",
    "check_task",
    "get_tasks",
    "tool_calls_pass",
]

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    GLOSSARY_LOOKUP.name: GLOSSARY_LOOKUP.parameters,
    ACRONYM_EXPAND.name: ACRONYM_EXPAND.parameters,
}
"""The chassis's own fake tools: the schema each call's arguments are checked against."""
EXPECTED_CALLS: tuple[tuple[str, dict[str, str]], ...] = (
    ("glossary_lookup", {"term": "SLM"}),
    ("acronym_expand", {"acronym": "RAG"}),
)


@dataclass(frozen=True)
class Task:
    name: str
    text: str


TASKS: tuple[Task, ...] = (
    Task("smoke", "simplify: Hello."),
    Task(
        "simplifier",
        "simplify: The SLM, released in 2026 by Acme, cut costs by 30 percent.",
    ),
    Task("lookup", "lookup: Define SLM and expand RAG."),
)


def get_tasks(names: Sequence[str]) -> list[Task]:
    by_name = {task.name: task for task in TASKS}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise ValueError(f"unknown task {', '.join(unknown)}; known: {', '.join(by_name)}")
    return [by_name[n] for n in names]


@dataclass(frozen=True)
class RunView:
    """What a check reads from one run: from a response envelope or from a stream of events."""

    status: str
    text: str
    tool_calls: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    deltas: int | None = None
    """Streamed `delta` events before `end`; None for a complete (non-stream) response."""


@dataclass(frozen=True)
class Check:
    ok: bool
    reason: str = ""
    tools_ok: bool | None = None
    """For `lookup`: the tool-call check alone."""


def tool_calls_pass(calls: Sequence[Mapping[str, Any]]) -> tuple[bool, str]:
    """The two tools, in order, with the expected arguments, each valid against its schema."""
    names = [str(call.get("name")) for call in calls]
    expected = [name for name, _ in EXPECTED_CALLS]
    if names != expected:
        return False, f"tool calls {names}, expected {expected}"
    for call, (name, arguments) in zip(calls, EXPECTED_CALLS, strict=True):
        given = call.get("arguments")
        if not isinstance(given, dict):
            return False, f"{name}: arguments are not an object"
        try:
            jsonschema.validate(given, TOOL_SCHEMAS[name])
        except jsonschema.ValidationError as exc:
            return False, f"{name}: arguments fail the schema ({exc.message})"
        if given != arguments:
            return False, f"{name}: arguments {given}, expected {arguments}"
    return True, ""


def _missing(text: str, needles: Sequence[str]) -> list[str]:
    folded = text.casefold()
    return [n for n in needles if n.casefold() not in folded]


def check_task(task: str, view: RunView) -> Check:
    """Apply the task's pass rule to `view`."""
    if view.status != "ok":
        return Check(False, f"status {view.status}")
    if task == "smoke":
        if view.deltas is None:
            if not view.text:
                return Check(False, "no output text")
        elif view.deltas < 1:
            return Check(False, "end before any delta")
        return Check(True)
    if task == "simplifier":
        missing = _missing(view.text, ("2026", "Acme", "30"))
        return Check(not missing, f"output lacks {missing}" if missing else "")
    if task == "lookup":
        tools_ok, why = tool_calls_pass(view.tool_calls)
        missing = _missing(view.text, ("small language model", "retrieval-augmented generation"))
        if not tools_ok:
            return Check(False, why, tools_ok=False)
        if missing:
            return Check(False, f"answer lacks {missing}", tools_ok=True)
        return Check(True, tools_ok=True)
    raise ValueError(f"unknown task {task!r}")
