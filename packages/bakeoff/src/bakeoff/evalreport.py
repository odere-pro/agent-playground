"""`eval-report.v0`: one report shape for every eval tool and every engine.

    {"schema": "eval-report.v0", "engine": str, "tool": str,
     "cases": [{"id", "pass", "trace_id", "assertions": [{"kind", "value", "pass", "reason"}]}],
     "summary": {"total", "passed", "failed"}}

`kind` is one of `contains`, `banned`, `tools`, `rubric`: the golden set's four checks, named the
same way by both tool configs (promptfoo `metric`, DeepEval metric name `kind:value`). Cases are
sorted by id. The launcher adds `meta` (timings, exit code); it is not part of the comparison.
"""

from __future__ import annotations

from typing import Any

import jsonschema

__all__ = ["KINDS", "REPORT_SCHEMA", "SCHEMA_ID", "normalize", "trim_raw", "validate_report"]

SCHEMA_ID = "eval-report.v0"
KINDS = ("contains", "banned", "tools", "rubric")

_ASSERTION = {
    "type": "object",
    "required": ["kind", "value", "pass", "reason"],
    "additionalProperties": False,
    "properties": {
        "kind": {"enum": list(KINDS)},
        "value": {"type": "string"},
        "pass": {"type": "boolean"},
        "reason": {"type": "string"},
    },
}
REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema", "engine", "tool", "cases", "summary"],
    "additionalProperties": False,
    "properties": {
        "schema": {"const": SCHEMA_ID},
        "engine": {"type": "string"},
        "tool": {"enum": ["promptfoo", "deepeval"]},
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "pass", "trace_id", "assertions"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "pass": {"type": "boolean"},
                    "trace_id": {"type": ["string", "null"]},
                    "assertions": {"type": "array", "items": _ASSERTION, "minItems": 1},
                },
            },
        },
        "summary": {
            "type": "object",
            "required": ["total", "passed", "failed"],
            "additionalProperties": False,
            "properties": {k: {"type": "integer"} for k in ("total", "passed", "failed")},
        },
        "meta": {"type": "object"},
    },
}


def _promptfoo(raw: dict[str, Any]) -> list[dict[str, Any]]:
    cases = []
    for row in raw["results"]["results"]:
        grading = row.get("gradingResult") or {}
        assertions = []
        for part in grading.get("componentResults") or []:
            spec = part.get("assertion") or {}
            kind = str(spec.get("metric"))
            value = "" if kind in ("tools", "rubric") else str(spec.get("value"))
            assertions.append(
                {
                    "kind": kind,
                    "value": value,
                    "pass": bool(part.get("pass")),
                    "reason": str(part.get("reason") or ""),
                }
            )
        response = row.get("response") or {}
        meta = response.get("metadata") or {}
        cases.append(
            {
                "id": str(row["testCase"]["metadata"]["case_id"]),
                "pass": bool(row.get("success")),
                "trace_id": meta.get("trace_id"),
                "assertions": assertions,
            }
        )
    return cases


def _deepeval(raw: dict[str, Any]) -> list[dict[str, Any]]:
    cases = []
    for row in raw["testCases"]:
        assertions = []
        for metric in row.get("metricsData") or []:
            name = str(metric["name"])
            if name.startswith("rubric"):
                kind, value = "rubric", ""
            else:
                kind, _, value = name.partition(":")
            assertions.append(
                {
                    "kind": kind,
                    "value": value,
                    "pass": bool(metric.get("success")),
                    "reason": str(metric.get("reason") or metric.get("error") or ""),
                }
            )
        meta = row.get("metadata") or {}
        cases.append(
            {
                "id": str(row["name"]),
                "pass": bool(row.get("success")),
                "trace_id": meta.get("trace_id"),
                "assertions": assertions,
            }
        )
    return cases


def normalize(tool: str, engine: str, raw: dict[str, Any]) -> dict[str, Any]:
    """The tool's own JSON report as `eval-report.v0`. Raises `ValueError` on an unknown tool."""
    if tool == "promptfoo":
        cases = _promptfoo(raw)
    elif tool == "deepeval":
        cases = _deepeval(raw)
    else:
        raise ValueError(f"unknown eval tool {tool!r}")
    cases.sort(key=lambda c: c["id"])
    passed = sum(1 for c in cases if c["pass"])
    report = {
        "schema": SCHEMA_ID,
        "engine": engine,
        "tool": tool,
        "cases": cases,
        "summary": {"total": len(cases), "passed": passed, "failed": len(cases) - passed},
    }
    validate_report(report)
    return report


def validate_report(report: dict[str, Any]) -> None:
    """Raise `ValueError` when `report` is not `eval-report.v0`."""
    try:
        jsonschema.validate(report, REPORT_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"{SCHEMA_ID}: {exc.message}") from exc


def trim_raw(tool: str, raw: dict[str, Any], root: str) -> dict[str, Any]:
    """The tool's report, smaller and with no host path, to commit as evidence.

    promptfoo: drops `config.tests` (a copy of tests.yaml), `config.outputPath`, and each judge
    call's `renderedGradingPrompt`. DeepEval: drops `verboseLogs`; `testFile` relative to `root`.
    Pass/fail, scores, reasons, outputs, and the tool's own keys stay.
    """
    if tool == "promptfoo":
        config = raw.get("config") or {}
        config.pop("tests", None)
        config.pop("outputPath", None)
        base = str(config.get("basePath") or "")
        config["basePath"] = base.removeprefix(root.rstrip("/") + "/")
        for row in raw.get("results", {}).get("results", []):
            for part in (row.get("gradingResult") or {}).get("componentResults") or []:
                (part.get("metadata") or {}).pop("renderedGradingPrompt", None)
    elif tool == "deepeval":
        test_file = str(raw.get("testFile") or "")
        raw["testFile"] = test_file.removeprefix(root.rstrip("/") + "/")
        for row in raw.get("testCases") or []:
            for metric in row.get("metricsData") or []:
                metric.pop("verboseLogs", None)
    return raw
