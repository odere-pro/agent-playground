"""The PoC-8 offline eval gate: block a merge when the golden set regresses.

    uv run python -m eval_kit.evalgate --report eval-reports/            # check, the CI step
    uv run python -m eval_kit.evalgate --report eval-reports/ --engine echo-python
    uv run python -m eval_kit.evalgate --report eval-reports/ --record   # rewrite the baseline

It reads `eval-report.v0` files (`eval_kit.evalreport`; `meta.lane` names the lane) and the
committed baseline `evals/gate/baseline.json` (`eval-baseline.v0`):

    {"schema": "eval-baseline.v0", "dataset": [case files], "cases": 50,
     "agents": {"simplifier": {"must_pass": [ids], "must_fail": [ids],
                               "engines": {"echo-python": {"inprocess": 0.78, ...}, ...}}},
     "skipped": {"echo-smolagents": "why it does not run in CI"}}

A target is one agent, engine, and lane. It passes when all of these hold:

- its report exists and covers the baseline's number of cases;
- its pass rate is at or above the recorded one (suggested: no tolerance; the model is scripted,
  so the rate is exact);
- every `must_pass` case passes: the cases that passed on every target when the baseline was
  recorded;
- every `must_fail` case fails: the cases labelled `fail` (wrong answers on purpose). One that
  starts to pass means a check stopped checking, so the gate fails even though the rate went up.

Exit 0 when every target passes, 1 when one regresses or is missing, 2 on a bad file or flag. A
report for an engine or lane the baseline does not list fails too: record it first. `--record`
writes the baseline from the reports, keeps `skipped`, and refuses a report in which a case
labelled `fail` passes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
from bakeoff.registry import LANES, ROOT

from eval_kit.evalreport import SCHEMA_ID, validate_report
from eval_kit.golden import Case, dataset_data, dataset_paths, parse_cases

__all__ = [
    "BASELINE",
    "BASELINE_SCHEMA",
    "DEFAULT_AGENT",
    "GATE_DATASET",
    "GateError",
    "Result",
    "check",
    "dataset_cases",
    "load_baseline",
    "load_reports",
    "main",
    "record",
]

BASELINE = ROOT / "evals/gate/baseline.json"
BASELINE_ID = "eval-baseline.v0"
DEFAULT_AGENT = "simplifier"
"""Every echo engine runs the simplifier agent: one prompt, one golden set."""
GATE_DATASET = (
    "packages/eval-kit/src/eval_kit/golden/cases.yaml",
    "packages/eval-kit/src/eval_kit/golden/cases-gate.yaml",
)
"""The packaged gate set's case files, relative to the repo root, as the committed baseline names
them. A baseline that names none reads the packaged files themselves (`dataset_paths`)."""

_IDS = {"type": "array", "items": {"type": "string"}, "uniqueItems": True}
BASELINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema", "cases", "agents", "skipped"],
    "additionalProperties": False,
    "properties": {
        "schema": {"const": BASELINE_ID},
        "dataset": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
        },
        "cases": {"type": "integer", "minimum": 1},
        "agents": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["must_pass", "must_fail", "engines"],
                "additionalProperties": False,
                "properties": {
                    "must_pass": _IDS,
                    "must_fail": _IDS,
                    "engines": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "propertyNames": {"enum": list(LANES)},
                            "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                    },
                },
            },
        },
        "skipped": {"type": "object", "additionalProperties": {"type": "string", "minLength": 1}},
    },
}


class GateError(ValueError):
    """A bad baseline, report, or flag: the gate cannot decide (exit 2)."""


@dataclass
class Result:
    agent: str
    engine: str
    lane: str
    passed: int = 0
    total: int = 0
    baseline_rate: float | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def rate(self) -> float:
        return round(self.passed / self.total, 3) if self.total else 0.0

    def line(self) -> str:
        head = f"{'PASS' if self.ok else 'FAIL'} {self.agent} {self.engine} {self.lane}"
        base = "none" if self.baseline_rate is None else f"{self.baseline_rate:.3f}"
        line = f"{head} {self.passed}/{self.total} {self.rate:.3f} (baseline {base})"
        return line if self.ok else f"{line}: {'; '.join(self.problems)}"


def load_baseline(path: Path) -> dict[str, Any]:
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.validate(data, BASELINE_SCHEMA)
    except (OSError, ValueError, jsonschema.ValidationError) as exc:
        raise GateError(f"baseline {path}: {getattr(exc, 'message', exc)}") from exc
    return data


def dataset_cases(baseline: dict[str, Any]) -> list[Case]:
    """The cases of the files the baseline's `dataset` names (repo-relative or absolute paths),
    joined in order; the packaged gate set when it names none (`dataset_paths`)."""
    paths = dataset_paths(baseline.get("dataset"), ROOT)
    try:
        return parse_cases(dataset_data(paths), size=None)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GateError(f"dataset {', '.join(map(str, paths))}: {exc}") from exc


def _check_dataset(baseline: dict[str, Any], cases: Sequence[Case]) -> None:
    if len(cases) != baseline["cases"]:
        raise GateError(f"the dataset has {len(cases)} cases, the baseline {baseline['cases']}")
    ids = {c.id for c in cases}
    for agent, row in baseline["agents"].items():
        unknown = sorted((set(row["must_pass"]) | set(row["must_fail"])) - ids)
        if unknown:
            raise GateError(f"{agent}: cases not in the dataset: {', '.join(unknown)}")


def _report_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(p for p in path.glob("*.json") if not p.name.endswith(".raw.json"))
    return [path] if path.is_file() else []


def load_reports(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Every `eval-report.v0` in `paths` (files, or folders of `*.json`; `*.raw.json` skipped)."""
    reports = []
    for path in paths:
        for file in _report_files(path):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise GateError(f"{file}: not JSON: {exc}") from exc
            if not isinstance(data, dict) or data.get("schema") != SCHEMA_ID:
                continue
            validate_report(data)
            if not isinstance(data.get("meta"), dict) or data["meta"].get("lane") not in LANES:
                raise GateError(f"{file}: meta.lane is missing or unknown")
            reports.append(data)
    if not reports:
        raise GateError(f"no eval-report.v0 in {', '.join(str(p) for p in paths)}")
    return reports


def _targets(baseline: dict[str, Any]) -> dict[tuple[str, str], tuple[str, float]]:
    """(engine, lane) -> (agent, recorded pass rate)."""
    out: dict[tuple[str, str], tuple[str, float]] = {}
    for agent, row in baseline["agents"].items():
        for engine, lanes in row["engines"].items():
            for lane, rate in lanes.items():
                out[(engine, lane)] = (agent, float(rate))
    return out


def _judge(
    result: Result, report: dict[str, Any], baseline: dict[str, Any], ids: frozenset[str]
) -> None:
    agent = baseline["agents"][result.agent]
    verdicts = {c["id"]: bool(c["pass"]) for c in report["cases"]}
    result.passed = sum(verdicts.values())
    result.total = len(verdicts)
    known = set(agent["must_pass"]) | set(agent["must_fail"])
    if result.total != baseline["cases"] or not known <= verdicts.keys():
        result.problems.append(
            f"the report has {result.total} cases, the baseline {baseline['cases']}"
        )
    elif verdicts.keys() != ids:
        extra = sorted(verdicts.keys() - ids)
        result.problems.append(f"the report's case ids are not the dataset's: {', '.join(extra)}")
    assert result.baseline_rate is not None
    if result.rate < result.baseline_rate:
        result.problems.append(f"pass rate {result.rate:.3f} < {result.baseline_rate:.3f}")
    failed = [i for i in agent["must_pass"] if verdicts.get(i) is False]
    if failed:
        result.problems.append(f"must-pass cases failed: {', '.join(failed)}")
    passing = [i for i in agent["must_fail"] if verdicts.get(i) is True]
    if passing:
        result.problems.append(
            f"must-fail cases passed (a check stopped checking): {', '.join(passing)}"
        )


def check(
    baseline: dict[str, Any],
    reports: Sequence[dict[str, Any]],
    *,
    engines: set[str] | None = None,
    lanes: set[str] | None = None,
) -> list[Result]:
    """One `Result` per baseline target (in baseline order), then one per unknown report.
    `engines` and `lanes`, when given, limit both to those."""

    def wanted(engine: str, lane: str) -> bool:
        return (engines is None or engine in engines) and (lanes is None or lane in lanes)

    cases = dataset_cases(baseline)
    _check_dataset(baseline, cases)
    ids = frozenset(c.id for c in cases)
    targets = _targets(baseline)
    by_target: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for report in reports:
        key = (report["engine"], report["meta"]["lane"])
        by_target.setdefault(key, []).append(report)
    results = []
    for (engine, lane), (agent, rate) in targets.items():
        if not wanted(engine, lane):
            continue
        result = Result(agent, engine, lane, baseline_rate=rate)
        found = by_target.get((engine, lane), [])
        if not found:
            result.problems.append("no report for this target")
        elif len(found) > 1:
            result.problems.append(f"{len(found)} reports for one target")
        else:
            _judge(result, found[0], baseline, ids)
        results.append(result)
    for (engine, lane), found in by_target.items():
        if (engine, lane) not in targets and wanted(engine, lane):
            result = Result(
                "?", engine, lane, found[0]["summary"]["passed"], len(found[0]["cases"])
            )
            result.problems.append(f"no baseline for {engine}/{lane}; record one (--record)")
            results.append(result)
    return results


def record(
    reports: Sequence[dict[str, Any]],
    *,
    agent: str,
    skipped: dict[str, str],
    dataset: Sequence[str] | None = None,
) -> dict[str, Any]:
    """A baseline from `reports`, with the labels of `dataset` (default the gate set). Raises
    `GateError` when they disagree on the case ids, or when a case labelled `fail` passes in one
    of them."""
    if not reports:
        raise GateError("no report to record")
    ids = [c["id"] for c in reports[0]["cases"]]
    named = {"dataset": list(dataset)} if dataset else {}
    labels = {c.id: c.label for c in dataset_cases(named)}
    engines: dict[str, dict[str, float]] = {}
    passing_everywhere = set(ids)
    for report in reports:
        verdicts = {c["id"]: bool(c["pass"]) for c in report["cases"]}
        if sorted(verdicts) != sorted(ids):
            raise GateError(f"{report['engine']}: its case ids differ from the first report's")
        bad = sorted(i for i, ok in verdicts.items() if ok and labels.get(i) == "fail")
        if bad:
            raise GateError(
                f"{report['engine']} {report['meta']['lane']}: cases labelled fail pass: "
                f"{', '.join(bad)}; fix the eval before recording"
            )
        passing_everywhere &= {i for i, ok in verdicts.items() if ok}
        lane = report["meta"]["lane"]
        engines.setdefault(report["engine"], {})[lane] = round(sum(verdicts.values()) / len(ids), 3)
    ordered = {
        engine: {lane: lanes[lane] for lane in LANES if lane in lanes}
        for engine, lanes in sorted(engines.items())
    }
    return {
        "schema": BASELINE_ID,
        **named,
        "cases": len(ids),
        "agents": {
            agent: {
                "must_pass": sorted(passing_everywhere),
                "must_fail": sorted(i for i in ids if labels.get(i) == "fail"),
                "engines": ordered,
            }
        },
        "skipped": dict(sorted(skipped.items())),
    }


def _csv(value: str) -> set[str]:
    return {part.strip() for part in value.split(",") if part.strip()}


def _run(args: argparse.Namespace) -> int:
    reports = load_reports(args.report)
    if args.record:
        old = load_baseline(args.baseline) if args.baseline.is_file() else {}
        data = record(
            reports,
            agent=args.agent,
            skipped=old.get("skipped", {}),
            dataset=old.get("dataset"),
        )
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.baseline}")
        return 0
    baseline = load_baseline(args.baseline)
    results = check(baseline, reports, engines=args.engine, lanes=args.lane)
    if not results:
        raise GateError("no baseline target matches --engine and --lane")
    for result in results:
        print(result.line())
    for engine, reason in baseline["skipped"].items():
        if args.engine is None or engine in args.engine:
            print(f"SKIP {engine}: {reason}")
    failed = sum(not r.ok for r in results)
    print(f"eval gate: {len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval_kit.evalgate")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--report", type=Path, nargs="+", required=True, help="files or folders")
    parser.add_argument("--engine", type=_csv, default=None, help="only these engines")
    parser.add_argument("--lane", type=_csv, default=None, help="only these lanes")
    parser.add_argument("--agent", default=DEFAULT_AGENT, help="with --record: the agent")
    parser.add_argument("--record", action="store_true", help="write the baseline instead")
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except GateError as exc:
        print(f"eval gate: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
