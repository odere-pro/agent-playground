"""The golden set: the PoC-7 20 cases (`cases.yaml`) and the PoC-8 gate set of 50 (package data).

`load_cases()` reads and validates the file: a JSON Schema check (`CASES_SCHEMA`), then the rules
a schema cannot say (unique ids, unique inputs, unique references, the case count). The same file
feeds the fake model server's script, the scripted judge, the promptfoo tests, the DeepEval test
file, and the Langfuse dataset, so every tool and every engine sees the same cases.

PoC-8: `load_gate_cases()` is the CI eval gate's set, `cases.yaml` then `cases-gate.yaml` (30
more), 50 cases. `cases.yaml` is not changed, so the PoC-7 trial keeps its 20. `gate_data()` is the
same 50 as one mapping, for the eval tool that reads one file (`GOLDEN_CASES`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import jsonschema
import yaml

__all__ = [
    "CASES_FILE",
    "CASES_SCHEMA",
    "GATE_FILE",
    "GATE_SIZE",
    "GOLDEN_SIZE",
    "Case",
    "ToolCallSpec",
    "gate_data",
    "load_cases",
    "load_gate_cases",
    "parse_cases",
]

GOLDEN_SIZE = 20
"""The planning doc's number: "the same 20 golden cases on two engines"."""
CASES_FILE = Path(str(files("bakeoff.golden").joinpath("cases.yaml")))
GATE_SIZE = 50
"""The planning doc's number: "a small golden set (about 50 cases per agent)" (PoC-8)."""
GATE_FILE = Path(str(files("bakeoff.golden").joinpath("cases-gate.yaml")))

Task = Literal["simplifier", "lookup"]
Label = Literal["pass", "fail"]

_TOOL_CALL = {
    "type": "object",
    "required": ["name", "arguments"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "arguments": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}
_STRINGS = {"type": "array", "items": {"type": "string", "minLength": 1}}

CASES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["version", "cases"],
    "additionalProperties": False,
    "properties": {
        "version": {"const": 1},
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "task", "input", "expect", "rubric", "reference", "label"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^g[0-9]{2}$"},
                    "task": {"enum": ["simplifier", "lookup"]},
                    "input": {"type": "string", "pattern": "^(simplify|lookup): .+"},
                    "expect": {
                        "type": "object",
                        "required": ["contains", "tool_calls"],
                        "additionalProperties": False,
                        "properties": {
                            "contains": {**_STRINGS, "minItems": 1},
                            "banned": _STRINGS,
                            "tool_calls": {"type": "array", "items": _TOOL_CALL},
                        },
                    },
                    "model": {
                        "type": "object",
                        "required": ["calls"],
                        "additionalProperties": False,
                        "properties": {"calls": {"type": "array", "items": _TOOL_CALL}},
                    },
                    "rubric": {"type": "string", "minLength": 1},
                    "reference": {"type": "string", "minLength": 1, "pattern": '^[^"\\\\\n]+$'},
                    "label": {"enum": ["pass", "fail"]},
                    "note": {"type": "string"},
                    "judge": {
                        "type": "object",
                        "required": ["verdict", "reason"],
                        "additionalProperties": False,
                        "properties": {
                            "verdict": {"enum": ["pass", "fail"]},
                            "reason": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
        },
    },
}
"""The file's shape. A reference holds no quote, backslash, or newline, so it reaches the judge
unchanged inside any tool's prompt template (the judge script matches it as a substring)."""


@dataclass(frozen=True)
class ToolCallSpec:
    name: str
    arguments: Mapping[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class Case:
    id: str
    task: Task
    input: str
    contains: tuple[str, ...]
    banned: tuple[str, ...]
    tool_calls: tuple[ToolCallSpec, ...]
    """What a good answer calls, in order. Empty: no tool call."""
    rubric: str
    reference: str
    label: Label
    """The human label of `reference`."""
    model_calls: tuple[ToolCallSpec, ...]
    """What the scripted model asks for: `expect.tool_calls` unless `model.calls` says otherwise."""
    judge_verdict: Label
    judge_reason: str
    note: str = ""


def _calls(rows: Sequence[Mapping[str, Any]]) -> tuple[ToolCallSpec, ...]:
    return tuple(ToolCallSpec(str(r["name"]), dict(r["arguments"])) for r in rows)


def _case(row: Mapping[str, Any]) -> Case:
    expect = row["expect"]
    calls = _calls(expect["tool_calls"])
    model = row.get("model")
    judge = row.get("judge")
    label: Label = row["label"]
    default_reason = (
        "The answer meets the rubric." if label == "pass" else "The answer misses the rubric."
    )
    return Case(
        id=row["id"],
        task=row["task"],
        input=row["input"],
        contains=tuple(expect["contains"]),
        banned=tuple(expect.get("banned") or ()),
        tool_calls=calls,
        rubric=row["rubric"],
        reference=row["reference"],
        label=label,
        model_calls=_calls(model["calls"]) if model else calls,
        judge_verdict=judge["verdict"] if judge else label,
        judge_reason=judge["reason"] if judge else default_reason,
        note=str(row.get("note") or ""),
    )


def parse_cases(data: Any, *, size: int | None = GOLDEN_SIZE) -> list[Case]:
    """Validate `data` (the parsed YAML) and return the cases. Raises `ValueError`."""
    try:
        jsonschema.validate(data, CASES_SCHEMA)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        raise ValueError(f"golden set: {where}: {exc.message}") from exc
    cases = [_case(row) for row in data["cases"]]
    for attr in ("id", "input", "reference"):
        values = [getattr(c, attr) for c in cases]
        dupes = sorted({v for v in values if values.count(v) > 1})
        if dupes:
            raise ValueError(f"golden set: duplicate {attr}: {dupes}")
    for case in cases:
        if case.task == "simplifier" and case.tool_calls:
            raise ValueError(f"golden set: {case.id}: a simplifier case calls no tool")
        if case.task == "lookup" and not case.tool_calls:
            raise ValueError(f"golden set: {case.id}: a lookup case expects a tool call")
    if size is not None and len(cases) != size:
        raise ValueError(f"golden set: {len(cases)} cases, expected {size}")
    return cases


def load_cases(path: Path | None = None) -> list[Case]:
    """The golden set from `path`, by default the packaged `cases.yaml`."""
    return parse_cases(yaml.safe_load((path or CASES_FILE).read_text(encoding="utf-8")))


def _read(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def gate_data() -> dict[str, Any]:
    """The 50 gate cases as one parsed YAML mapping: the PoC-7 cases, then the PoC-8 ones."""
    first, extra = _read(CASES_FILE), _read(GATE_FILE)
    return {"version": first["version"], "cases": [*first["cases"], *extra["cases"]]}


def load_gate_cases() -> list[Case]:
    """The PoC-8 gate set: 50 cases, validated like `load_cases` (ids, inputs, references unique
    across both files)."""
    return parse_cases(gate_data(), size=GATE_SIZE)
