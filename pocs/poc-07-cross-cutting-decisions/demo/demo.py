"""PoC-7 demo helper, called by `demo.sh`. Not a test. It reuses the PoC test harnesses.

    python demo.py trace --engine echo-langgraph   # one /v1/run: span tree, then feedback on it
    python demo.py evals DIR                       # eval-report.v0 summaries in DIR, side by side
    python demo.py gate --engine echo-langgraph    # sync gate: judge fails, retry, fallback

Offline: Unix sockets and ASGI transports only, no key. The fake Langfuse key pair is the
harness's literal test pair and is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
POCS = HERE.parents[1]
for _tests in (
    "poc-02-two-engines-one-contract",
    "poc-03-one-interface-every-client",
    "poc-04-stateless-scalable",
    "poc-05-sandboxed",
    "poc-06a-bake-off-sidecar-lane",
    "poc-07-cross-cutting-decisions",
):
    sys.path.insert(0, str(POCS / _tests / "tests"))

import pytest  # noqa: E402  (MonkeyPatch only, for the harnesses' hooks)

CALLER_TRACE = "0af7651916cd43dd8448eb211c80319c"  # pragma: allowlist secret
CALLER_SPAN = "b7ad6b7169203331"  # pragma: allowlist secret
SIDECAR_URL = "http://127.0.0.1:9100"
SESSION = "sess-poc07-demo"
ENGINES = {
    "echo-langgraph": "echo_langgraph.handle:handle",
    "echo-openai-agents": "echo_openai_agents.handle:handle",
}


# --- 1 and 2: one trace, then feedback on it ----------------------------------------------------


def _ids(span: Any) -> tuple[str, str, str | None]:
    trace = f"{span.context.trace_id:032x}"
    own = f"{span.context.span_id:016x}"
    parent = f"{span.parent.span_id:016x}" if span.parent is not None else None
    return trace, own, parent


def _print_tree(spans: list[Any]) -> None:
    children: dict[str | None, list[Any]] = {}
    for span in spans:
        children.setdefault(_ids(span)[2], []).append(span)
    for kids in children.values():
        kids.sort(key=lambda s: s.start_time or 0)
    known = {_ids(s)[1] for s in spans}
    roots = [s for s in spans if _ids(s)[2] not in known]
    print(f"{'span':64} {'span id':16}  {'parent':16}  service / scope")

    def show(span: Any, depth: int) -> None:
        _, own, parent = _ids(span)
        service = span.resource.attributes.get("service.name", "?")
        scope = span.instrumentation_scope.name if span.instrumentation_scope else ""
        label = ("  " * depth + span.name)[:64]
        print(f"{label:64} {own}  {parent or '-':16}  {service} / {scope}")
        for kid in children.get(own, []):
            show(kid, depth + 1)

    for root in sorted(roots, key=lambda s: s.start_time or 0):
        show(root, 0)


async def _trace_and_feedback(engine: str, mp: pytest.MonkeyPatch) -> int:
    from chassis.adapters.a2a import SidecarConnector
    from chassis.adapters.otel import OtelTelemetry
    from chassis.core.trace import trace_id_hex
    from chassis.fakes import InMemoryConfig
    from chassis.fakes.events import InMemoryBus
    from chassis.fakes.tool import default_tools
    from chassis.ports.bundle import PortBundle
    from chassis.server import ChassisConfig, create_app
    from chassis.server.feedback import FEEDBACK_RECEIVED, FeedbackEvents
    from chassis_contracts.events import assert_valid_cloudevent
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from poc07_fb_harness import fake_langfuse, langfuse_port
    from poc_harness.apps import client_for, running
    from poc_harness.outbound import route_outbound
    from poc_harness.scenario import AFTER_TOOL, ROUTE, TIMEOUT_S, TOOL_PROMPT
    from poc_harness.scripted_model import ToolLoopModel
    from test_poc07_obs_trace_join import workload_on_uds
    from workload_a2a import telemetry as workload_telemetry

    exporter = InMemorySpanExporter()
    workload_telemetry.configure(span_exporters=[exporter], service_name="workload")
    langfuse, bus = fake_langfuse(), InMemoryBus()
    try:
        async with workload_on_uds(ENGINES[engine]) as uds:
            ports = PortBundle(
                model=ToolLoopModel(),
                engine=SidecarConnector(),
                config=InMemoryConfig(),
                telemetry=OtelTelemetry(span_exporters=[exporter], service_name="chassis"),
                tools=default_tools(),
                events=bus,
                feedback=langfuse_port(langfuse),
            )
            config = ChassisConfig.model_validate(
                {
                    "version": "cfg-poc07-demo",
                    "profile": "fake",
                    "agent": {"name": "simplifier", "version": "0.0.1"},
                    "spec": {
                        "adapters": {"model": "fake", "feedback": "langfuse"},
                        "engine": {"connector": "sidecar", "url": SIDECAR_URL, "uds": uds},
                        "model": {"route": ROUTE},
                        "prompt": {"version": "simplifier-v1"},
                        "feedback": {"events": True},
                    },
                }
            )
            app = create_app(config, ports)
            route_outbound(mp, app)
            headers = {
                "traceparent": f"00-{CALLER_TRACE}-{CALLER_SPAN}-01",
                "x-session-id": SESSION,
            }
            async with asyncio.timeout(TIMEOUT_S), running(app), client_for(app) as http:
                res = await http.post(
                    "/v1/run", json={"input": {"text": TOOL_PROMPT}}, headers=headers
                )
                answer = res.json()
                spans = list(exporter.get_finished_spans())
                feedback = await http.post(
                    "/v1/feedback",
                    json={
                        "request_id": answer["request_id"],
                        "score": 1,
                        "label": "helpful",
                        "comment": "right answer; call me on +44 20 7946 0958",
                    },
                )
                events = app.state.feedback_events
                assert isinstance(events, FeedbackEvents)
                assert await events.wait(5) == 0
    finally:
        workload_telemetry.shutdown()

    print(f"POST /v1/run  engine={engine} lane=sidecar  input={TOOL_PROMPT!r}")
    print(
        f"  http {res.status_code}  status={answer['status']}  output={answer['output']['text']!r}"
    )
    print(f"  request_id={answer['request_id']}  trace_id={trace_id_hex(answer['trace_id'])}")
    print(f"  caller traceparent: 00-{CALLER_TRACE}-{CALLER_SPAN}-01")
    print()
    _print_tree(spans)
    traces = sorted({_ids(s)[0] for s in spans})
    names = [s.name for s in spans]
    framework = [
        s
        for s in spans
        if s.instrumentation_scope is not None
        and (s.instrumentation_scope.name or "").startswith("openinf")
    ]
    print()
    print(f"spans: {len(spans)}  trace ids: {traces}")
    counts = {
        name: names.count(name) for name in ("chassis.run", "chassis.engine.run", "workload.handle")
    }
    counts["OpenInference"] = len(framework)
    for name in ("chassis.model.call", "chassis.tool.call"):
        counts[name] = names.count(name)
    print("  ".join(f"{name}: {n}" for name, n in counts.items()))

    print()
    print("### Feedback on that request id")
    print()
    print(f"POST /v1/feedback request_id={answer['request_id']} score=1 label=helpful")
    print(f"  http {feedback.status_code}  {json.dumps(feedback.json())}")
    (score,) = langfuse.scores.values()
    print("fake Langfuse score:")
    shown: dict[str, Any] = {
        k: score[k] for k in ("id", "traceId", "name", "value", "dataType") if k in score
    }
    shown["metadata.request_id"] = score["metadata"]["request_id"]
    print(f"  {json.dumps(shown)}")
    (event,) = [e for t, e in bus.published if t == FEEDBACK_RECEIVED]
    wire = assert_valid_cloudevent(event)
    print(f"published {FEEDBACK_RECEIVED} (valid CloudEvent; data redacted):")
    keep = ("specversion", "type", "source", "dataschema", "data")
    print(
        "  " + json.dumps({k: wire[k] for k in keep if k in wire}, indent=2).replace("\n", "\n  ")
    )

    checks = {
        "http 200 and status ok": res.status_code == 200 and answer["status"] == "ok",
        "answer used the tool": answer["output"]["text"] == AFTER_TOOL,
        "every span in the caller's trace": traces == [CALLER_TRACE],
        "OpenInference spans present": bool(framework),
        ">= 2 model calls and >= 1 tool call": names.count("chassis.model.call") >= 2
        and names.count("chassis.tool.call") >= 1,
        "feedback 202": feedback.status_code == 202,
        "score on the run's trace": score["traceId"] == CALLER_TRACE,
        "event on the run's trace": wire["data"]["trace_id"] == CALLER_TRACE,
        "event comment redacted": "7946" not in json.dumps(wire["data"]),
    }
    print()
    for name, ok in checks.items():
        print(f"check: {name}: {'ok' if ok else 'FAILED'}")
    return 0 if all(checks.values()) else 1


def trace(engine: str) -> int:
    with pytest.MonkeyPatch.context() as mp:
        return asyncio.run(_trace_and_feedback(engine, mp))


# --- 3: eval summaries ---------------------------------------------------------------------------


def evals(folder: Path, tool: str, engines: list[str]) -> int:
    from bakeoff.evalreport import validate_report

    reports = {}
    for engine in engines:
        report = json.loads((folder / f"{tool}-{engine}.json").read_text(encoding="utf-8"))
        validate_report(report)
        reports[engine] = report
        meta = report.get("meta", {})
        print(
            f"{report['schema']}  tool={report['tool']}  engine={report['engine']}  "
            f"summary={json.dumps(report['summary'])}  lane={meta.get('lane')}  "
            f"offline={meta.get('offline')}"
        )
    print()
    print(f"{'case':6} " + " ".join(f"{e:20}" for e in engines) + " failed assertions")
    ids = [c["id"] for c in reports[engines[0]]["cases"]]
    same = True
    for i, case_id in enumerate(ids):
        rows = [reports[e]["cases"][i] for e in engines]
        same &= len({r["pass"] for r in rows}) == 1 and all(r["id"] == case_id for r in rows)
        failed = sorted({a["kind"] for a in rows[0]["assertions"] if not a["pass"]})
        cells = " ".join(f"{'pass' if r['pass'] else 'FAIL':20}" for r in rows)
        print(f"{case_id:6} {cells} {', '.join(failed) or '-'}")
    print()
    print(f"same verdict on every case for both engines: {'yes' if same else 'NO'}")
    return 0 if same else 1


# --- 5: the evaluator gate -----------------------------------------------------------------------


async def _gate(engine: str, mp: pytest.MonkeyPatch) -> int:
    from poc06_harness import ROUTE
    from poc07_evals_harness import BAD, FALLBACK_ROUTE, GATE_INPUT, GOOD, serve_gate_backends
    from test_poc07_evals_gate import gated_run, routes

    with serve_gate_backends() as backends:
        body, telemetry, calls = await gated_run(engine, backends, mp)
    from chassis.server.evaluator_gate import EVALUATOR_CALLS, GATE_FALLBACKS, GATE_RETRIES

    print(f"POST /v1/run  engine={engine} lane=sidecar  harness: evaluator_gate=true sync")
    print("  retries=1 threshold=0.7, judge=JudgeEvaluator (scripted scores)")
    print(f"  input={GATE_INPUT!r}")
    print(f"  status={body['status']}  output={body['output']['text']!r}")
    m = body["metrics"]
    print(f"  metrics: attempts={m['attempts']} model_route={m['model_route']}")
    print(f"model calls in order: {routes(calls)}")
    print(
        f"counters: {GATE_RETRIES}={telemetry.counter_value(GATE_RETRIES)}  "
        f"{GATE_FALLBACKS}={telemetry.counter_value(GATE_FALLBACKS)}  "
        f"{EVALUATOR_CALLS}{{mode=sync,outcome=fail}}="
        + str(
            telemetry.counter_value(EVALUATOR_CALLS, evaluator="judge", mode="sync", outcome="fail")
        )
    )
    ok = (
        body["status"] == "fallback"
        and body["output"]["text"] == GOOD
        and BAD not in body["output"]["text"]
        and m["model_route"] == FALLBACK_ROUTE
        and routes(calls)[0] == ROUTE
    )
    print(
        f"check: judge failed twice, fell back, the bad answer never left: {'ok' if ok else 'NO'}"
    )
    return 0 if ok else 1


class _ShutdownRace(logging.Filter):
    """Drops one line: a harness server thread's `Future.set_result` on a future its shutdown
    already cancelled (`InvalidStateError`). The run's result does not depend on it."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not (
            threading.current_thread() is not threading.main_thread()
            and record.getMessage().startswith("Exception in callback Future.set_result")
        )


def gate(engine: str) -> int:
    logging.getLogger("asyncio").addFilter(_ShutdownRace())
    with pytest.MonkeyPatch.context() as mp:
        return asyncio.run(_gate(engine, mp))


def main() -> int:
    parser = argparse.ArgumentParser(prog="demo.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_trace = sub.add_parser("trace")
    p_trace.add_argument("--engine", choices=list(ENGINES), required=True)
    p_evals = sub.add_parser("evals")
    p_evals.add_argument("folder", type=Path)
    p_evals.add_argument("--tool", default="deepeval")
    p_evals.add_argument("--engine", default="echo-python,echo-langgraph")
    p_gate = sub.add_parser("gate")
    p_gate.add_argument("--engine", default="echo-langgraph")
    args = parser.parse_args()
    if args.cmd == "trace":
        return trace(args.engine)
    if args.cmd == "evals":
        return evals(args.folder, args.tool, args.engine.split(","))
    return gate(args.engine)


if __name__ == "__main__":
    raise SystemExit(main())
