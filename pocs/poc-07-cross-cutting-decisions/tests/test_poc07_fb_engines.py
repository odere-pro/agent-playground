"""Exit criterion 5 (feedback part): feedback works on at least two engines with no
engine-specific code. Each engine runs in the `sidecar` lane: the template A2A server on a Unix
socket, its model and tool calls to the PoC-6a fake backends on Unix sockets. After a `/v1/run`,
`POST /v1/feedback` with only the `request_id` lands in the fake Langfuse on THAT run's trace:
the same trace id the chassis answered and the `traceparent` the workload sent on its model call.

The test body is the same for every engine; only the registry entry differs. Exit criterion 6
(feedback part): what each engine must expose for feedback is nothing, since the chassis keeps
`request_id -> trace_id` itself.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.core.trace import parse_traceparent, trace_id_hex
from poc06_harness import ENGINES, SMOKE
from poc06a_harness import Backends, serve_backends, wire_python_engine
from poc07_fb_harness import chassis, client, fake_langfuse, run
from poc_harness.servers import a2a_on_unix_socket

FEEDBACK_ENGINES = ("echo-python", "echo-langgraph")


@pytest.fixture(scope="module")
def backends() -> Iterator[Backends]:
    with serve_backends() as found:
        yield found


@pytest.mark.parametrize("engine_name", FEEDBACK_ENGINES)
async def test_poc07_fb_feedback_lands_on_the_runs_trace_in_the_sidecar_lane(
    engine_name: str, backends: Backends, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 5: "Each concern works on at least two engines": feedback."""
    backends.reset()
    engine = ENGINES[engine_name]
    wire_python_engine(monkeypatch, engine, backends)
    fake = fake_langfuse()
    with a2a_on_unix_socket(engine.handle) as (url, uds):
        spec = {"connector": "sidecar", "url": url, "uds": uds}
        app = chassis(fake, engine=SidecarConnector(), engine_spec=spec)
        async with client(app) as http:
            answer = await run(http, SMOKE.text)
            assert answer["status"] == "ok", answer
            feedback = await http.post(
                "/v1/feedback",
                json={"request_id": answer["request_id"], "score": 1, "label": "helpful"},
            )
    assert feedback.status_code == 202, feedback.text
    trace = trace_id_hex(answer["trace_id"])
    assert feedback.json()["trace_id"] == trace
    (score,) = fake.scores.values()
    assert score["traceId"] == trace
    assert score["metadata"]["request_id"] == answer["request_id"]
    model_traces = {parse_traceparent(s.traceparent) for s in backends.model_calls()}
    assert model_traces == {trace}, "the workload's model call is on the same trace"
