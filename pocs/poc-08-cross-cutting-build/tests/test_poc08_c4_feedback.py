"""PoC-8 exit criterion 4: "Feedback shows on the right trace for every engine."
(docs/planning/poc/008-PoC-8-build-cross-cutting.md)

Each cell runs one call through the chassis with every stage on, then `POST /v1/feedback` with
only the run's `request_id` (a thumbs-down). The feedback lands in `InMemoryFeedback` on THAT
run's trace: the trace id the chassis answered, which is also the trace id of every model proxy
span the workload's calls made. The engine exposes nothing for this; the chassis keeps
`request_id -> trace_id` itself. A second run's feedback lands on the second trace, not the first.
"""

from __future__ import annotations

import pytest
from chassis.core.trace import trace_id_hex
from poc08_harness import cell_params, full_stack


@pytest.mark.parametrize(("engine", "lane"), cell_params())
async def test_feedback_lands_on_the_runs_trace(
    engine: str, lane: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 4: two runs, feedback on each; each record names its own run's trace id, which
    is the trace the workload's model calls were on."""
    with full_stack(engine, lane, monkeypatch) as stack:
        first = (await stack.run("simplify: Hello.")).json()
        model_traces = {s.attributes["trace_id"] for s in stack.spans("chassis.model.call")}
        second = (await stack.run("simplify: Hello again.")).json()
        answers = [await stack.send_feedback(r["request_id"]) for r in (first, second)]
    assert first["status"] == "ok" and second["status"] == "ok", (first, second)
    for answer in answers:
        assert answer.status_code == 202, answer.text
    traces = [trace_id_hex(r["trace_id"]) for r in (first, second)]
    assert traces[0] != traces[1]
    assert model_traces == {traces[0]}, "the workload's model calls are on the run's trace"
    for answer, run, trace in zip(answers, (first, second), traces, strict=True):
        assert answer.json()["trace_id"] == trace
        record = stack.feedback.get(answer.json()["feedback_id"])
        assert record is not None
        assert record.trace_id == trace
        assert record.request_id == run["request_id"]
        assert record.score == 0
