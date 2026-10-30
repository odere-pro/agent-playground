"""PoC-8 demo helper, called by `demo.sh`. Not a test. It reuses `poc_harness.full_stack`.

    python poc08_demo.py live --engine echo-langgraph --lane sidecar

One live call through the chassis with every stage on (`poc_harness.full_stack`): the main
route's answer scores low, the gate retries, then falls back to `spec.model.fallback_route`. Then
the span list of that run, a thumbs-down `POST /v1/feedback` on its `request_id`, and the lines of
`GET /metrics` that show the gate and the feedback.

Offline: Unix sockets only, no TCP, no key. The judge is `ScriptedEvaluator`: it scores the
harness's `BAD` answer 0.1 and anything else 1.0. The tokens are the harness's literal test
tokens and are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest
from chassis.core.trace import trace_id_hex
from chassis.server.evaluator_gate import GATE_FALLBACKS, GATE_RETRIES
from poc_harness.full_stack import full_stack
from poc_harness.stage_model import BAD, FALLBACK_ROUTE, GATE_MARK, GOOD, ROUTE

RETRIES = 2
"""From the config (`spec.harness.retries`), not the default 1, so the count is visibly the
config's."""
SHOWN_ATTRS = ("attempt", "route", "route.requested", "route.forced", "tool")
METRIC_PREFIXES = (
    "chassis_requests_total",
    "chassis_model_calls_total",
    "chassis_gate_runs_total",
    "chassis_gate_retries_total",
    "chassis_gate_fallbacks_total",
    "chassis_evaluator_calls_total",
    "chassis_feedback_total",
)


def _print_spans(spans: list[Any]) -> None:
    """Every chassis span of the run in start order, with its own id and its parent's id. In the
    sidecar and remote lanes a model or tool proxy span's parent is the workload's span (from the
    `traceparent` the workload sent), which is not in this list."""
    print(f"{'span':26} {'id':8} {'parent':8} {'trace id':32}  attributes")
    for span in spans:
        attrs = span.attributes
        shown = {k: attrs[k] for k in SHOWN_ATTRS if k in attrs}
        parent = (span.parent_id or "-")[:8]
        print(
            f"{span.name:26} {span.span_id:8} {parent:8} {attrs.get('trace_id', '-'):32}  "
            f"{json.dumps(shown)}"
        )


def _metric_lines(text: str) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if line.startswith(METRIC_PREFIXES) and not line.startswith("#")
    ]


@dataclass(frozen=True)
class LiveRun:
    """What one live call left behind, read while the stack was still up."""

    task: str
    response: Any
    body: dict[str, Any]
    trace: str
    spans: list[Any]
    feedback: Any
    record: Any
    metrics: Any
    retries: float
    fallbacks: float
    routes: list[str]
    scored: int


async def _run_live(engine: str, lane: str, mp: pytest.MonkeyPatch) -> LiveRun:
    """The live call and the thumbs-down, with every stage on."""
    task = f"simplify: {GATE_MARK} The report was signed by Dana Ruiz on 4 May 2025."
    with full_stack(engine, lane, mp, retries=RETRIES) as stack:
        response = await stack.run(task)
        body = response.json()
        spans = list(stack.telemetry.spans)
        feedback = await stack.send_feedback(body["request_id"])
        async with stack.client() as client:
            metrics = await client.get("/metrics")
        return LiveRun(
            task=task,
            response=response,
            body=body,
            trace=trace_id_hex(body["trace_id"]),
            spans=spans,
            feedback=feedback,
            record=stack.feedback.get(feedback.json()["feedback_id"]),
            metrics=metrics,
            retries=stack.telemetry.counter_value(GATE_RETRIES),
            fallbacks=stack.telemetry.counter_value(GATE_FALLBACKS),
            routes=list(stack.model.routes),
            scored=len(stack.evaluator.cases),
        )


def _print_call(run: LiveRun, engine: str, lane: str) -> None:
    body = run.body
    print(f"POST /v1/run  engine={engine} lane={lane}  every stage on")
    print(
        f"  config: spec.harness.evaluator_gate=true retries={RETRIES} threshold=0.7, "
        f"spec.model.route={ROUTE} fallback_route={FALLBACK_ROUTE}"
    )
    print(f"  input={run.task!r}")
    print(f"  http {run.response.status_code}  status={body['status']}")
    print(f"  output={body['output']['text']!r}")
    print(
        f"  metrics: attempts={body['metrics']['attempts']} "
        f"model_route={body['metrics']['model_route']}"
    )
    print(f"  request_id={body['request_id']}  trace_id={run.trace}")
    print(f"model calls in order: {run.routes}")
    print(f"judge calls: {run.scored} (the fallback answer is not scored)")


def _print_feedback(run: LiveRun) -> None:
    print(f"POST /v1/feedback request_id={run.body['request_id']} score=0 label=thumbs-down")
    print(f"  http {run.feedback.status_code}  trace_id={run.feedback.json().get('trace_id')}")
    record = run.record
    if record is not None:
        print(
            f"  stored: request_id={record.request_id} trace_id={record.trace_id} "
            f"score={record.score} label={record.label}"
        )


def _print_metrics(run: LiveRun) -> None:
    print(f"http {run.metrics.status_code}")
    for line in _metric_lines(run.metrics.text):
        print(f"  {line}")


def _print_sections(run: LiveRun, engine: str, lane: str) -> None:
    _print_call(run, engine, lane)
    print()
    print("### The trace: every chassis span of the run")
    print()
    _print_spans(run.spans)
    print()
    print("### Thumbs-down feedback on the same request id")
    print()
    _print_feedback(run)
    print()
    print("### GET /metrics (no token by default), the gate and feedback lines")
    print()
    _print_metrics(run)


def _span_count(run: LiveRun, name: str) -> int:
    return len([s for s in run.spans if s.name == name])


def _checks(run: LiveRun) -> dict[str, bool]:
    """Each check's label and result, in the order the demo prints them."""
    response, body, routes, spans = run.response, run.body, run.routes, run.spans
    answer, record, trace = run.feedback.json(), run.record, run.trace
    shown_metrics = _metric_lines(run.metrics.text)
    run_traces = {s.attributes.get("trace_id") for s in spans if "trace_id" in s.attributes}
    return {
        "http 200, status fallback": response.status_code == 200 and body["status"] == "fallback",
        "the fallback answer, the bad one never left": body["output"]["text"] == GOOD
        and BAD not in response.text,
        f"{RETRIES + 1} scored attempts on the main route, then the fallback": routes
        == [ROUTE] * (RETRIES + 1) + [FALLBACK_ROUTE],
        "retries and fallbacks counted": run.retries == RETRIES and run.fallbacks == 1,
        "every span on the run's trace": run_traces == {trace},
        "a score span per scored attempt": _span_count(run, "chassis.evaluator.score")
        == RETRIES + 1,
        "a router span per model call": _span_count(run, "chassis.model.route") == len(routes),
        "feedback 202 on the run's trace": run.feedback.status_code == 202
        and answer.get("trace_id") == trace
        and record is not None
        and record.trace_id == trace,
        "/metrics shows the fallback": any(
            line.startswith("chassis_gate_fallbacks_total") and line.endswith(" 1")
            for line in shown_metrics
        ),
        "/metrics shows the thumbs-down": any(
            line.startswith("chassis_feedback_total") and 'label="thumbs-down"' in line
            for line in shown_metrics
        ),
    }


async def _live(engine: str, lane: str, mp: pytest.MonkeyPatch) -> int:
    run = await _run_live(engine, lane, mp)
    _print_sections(run, engine, lane)
    checks = _checks(run)
    print()
    for name, ok in checks.items():
        print(f"check: {name}: {'ok' if ok else 'FAILED'}")
    return 0 if all(checks.values()) else 1


def live(engine: str, lane: str) -> int:
    with pytest.MonkeyPatch.context() as mp:
        return asyncio.run(_live(engine, lane, mp))


def main() -> int:
    parser = argparse.ArgumentParser(prog="poc08_demo.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_live = sub.add_parser("live")
    p_live.add_argument("--engine", default="echo-langgraph")
    p_live.add_argument("--lane", default="sidecar", choices=("inprocess", "sidecar", "remote"))
    args = parser.parse_args()
    return live(args.engine, args.lane)


if __name__ == "__main__":
    raise SystemExit(main())
