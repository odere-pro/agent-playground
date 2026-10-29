"""PoC-8 exit criterion 3, the workload side of "logs with `trace_id`" (014 H-7).

The chassis's lines are checked in `test_poc08_c3_trace.py`. Here: after one run through the full
stack, the workload's own log lines are JSON with the chassis's field names, and the lines it
wrote inside the request carry the chassis run's `request_id` and 32-hex `trace_id`. Cells: the
`sidecar` lane for the four trusted Python engines, the `remote` lane for `echo-python`.

The harness runs the workload server in this process (a thread, on a Unix socket), so its log
records reach this process's root logger next to the chassis's. They are captured at the
formatter level: a root handler with the workload's own `workload_a2a.logs.JsonFormatter`, the
handler `workload-a2a serve` installs at start. The workload's `request_context` is a contextvar
of the workload server's thread, so a chassis record never picks up the workload's ids.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from chassis.core.trace import trace_id_hex
from poc08_harness import PLAIN, TRUSTED_ENGINES, full_stack
from workload_a2a.logs import JsonFormatter

WORKLOAD_AGENT = "workload"
SERVER_LOGGER = "workload_a2a.server"
FIELDS = ("time", "level", "logger", "message", "agent")
CELLS = [
    *(pytest.param(e, "sidecar", id=f"{e}-sidecar") for e in TRUSTED_ENGINES),
    pytest.param("echo-python", "remote", id="echo-python-remote"),
]


@pytest.fixture
def workload_lines() -> Iterator[Callable[[], list[str]]]:
    """The raw lines written so far by a root handler with the workload's `JsonFormatter`."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(agent=WORKLOAD_AGENT))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield lambda: stream.getvalue().splitlines()
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


@pytest.mark.parametrize(("engine", "lane"), CELLS)
async def test_the_workload_logs_json_with_the_chassis_runs_trace_id(
    engine: str,
    lane: str,
    monkeypatch: pytest.MonkeyPatch,
    workload_lines: Callable[[], list[str]],
) -> None:
    """Every line is one JSON object with the chassis's field names; the workload's `handle done`
    line for the run carries the run's `request_id` and `trace_id`; no line carries another."""
    with full_stack(engine, lane, monkeypatch) as stack:
        response = await stack.run("simplify: Hello.")
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["status"] == "ok", body
    assert body["output"]["text"] == PLAIN
    trace, request_id = trace_id_hex(body["trace_id"]), body["request_id"]

    lines: list[dict[str, Any]] = [json.loads(line) for line in workload_lines()]
    assert lines
    for line in lines:
        assert all(field in line for field in FIELDS), line
    (done,) = [x for x in lines if x["logger"] == SERVER_LOGGER and x["message"] == "handle done"]
    assert (done.get("trace_id"), done.get("request_id")) == (trace, request_id), done
    assert len(done["trace_id"]) == 32
    carried = {(x["trace_id"], x.get("request_id")) for x in lines if "trace_id" in x}
    assert carried == {(trace, request_id)}, f"{engine}/{lane}: {carried}"
