"""Shared by the PoC-8 stack drill (`test_poc08_stack_metrics.py`) and the latency script
(`poc08_stack_latency.py`). Opens no socket on import.

The stack is `deploy/compose/poc08` (`stack.sh up`). Its ports, the drill's inputs, and the
helpers that talk to the chassis, Prometheus, and Grafana live here, so the offline static test
(`test_poc08_stack_static.py`) checks the same values against the Compose file and the model
script, and runs the helpers on `httpx.MockTransport`.

Credentials come from the env file `stack.sh` writes; its path is `POC08_ENV_FILE`
(`stack.sh env`). They are read into memory and never printed or put in a message.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from poc_harness.paths import ROOT

STACK = ROOT / "deploy/compose/poc08"
DASHBOARDS = ROOT / "deploy/dashboards"
STACK_VAR = "POC08_STACK"
"""`1` turns the drill on: `conftest.py` collects `DRILL_FILE` only then."""
ENV_FILE_VAR = "POC08_ENV_FILE"
DRILL_FILE = "test_poc08_stack_metrics.py"

CHASSIS = "http://127.0.0.1:18088"
"""The drill's chassis, agent `echo`, every PoC-8 stage on (chassis/drill.yaml)."""
CHASSIS_STAGES = "http://127.0.0.1:18089"
"""Latency, stages on (chassis/stages.yaml)."""
CHASSIS_PLAIN = "http://127.0.0.1:18090"
"""Latency, stages off (chassis/plain.yaml)."""
PROMETHEUS = "http://127.0.0.1:19090"
GRAFANA = "http://127.0.0.1:13000"
GRAFANA_USER = "admin"
AGENT = "echo"
DASHBOARD_UID = f"chassis-{AGENT}"
SCRAPE_JOB = "chassis"

TOKEN_KEY = "POC08_RUN_TOKEN"
GRAFANA_PASSWORD_KEY = "GRAFANA_ADMIN_PASSWORD"

# The drill's inputs. deploy/compose/poc08/fake-model/poc08.yaml answers each one.
PLAIN_INPUT = "simplify: The meeting moved to Tuesday at 10 am."
TOOL_INPUT = "simplify: Use the glossary to say what SLM means."
GATE_INPUT = "simplify: gate-check The report was signed by Dana Ruiz on 4 May 2025."
FAIL_INPUT = "simplify: fail-model on purpose."
GATE_BAD = "GATE-BAD-ANSWER"
"""The main route's answer to `GATE_INPUT`; the judge scores it below the threshold."""
FALLBACK_ANSWER = "Dana Ruiz signed the report."
FALLBACK_ROUTE = "fallback-route"
JUDGE_ROUTE = "judge"
MAIN_ROUTE = "big-default"
THUMBS_DOWN = "thumbs-down"
THUMBS_UP = "thumbs-up"

RUN_TIMEOUT_S = 30.0
"""suggested: one run, the gate's retry and fallback included, on the fake model."""


def read_env_file(path: str | Path | None = None) -> dict[str, str]:
    """`KEY=VALUE` lines of the env file `stack.sh` wrote (`POC08_ENV_FILE` by default)."""
    where = path if path is not None else os.environ.get(ENV_FILE_VAR)
    if not where:
        raise LookupError(f'{ENV_FILE_VAR} is not set: export "$(stack.sh env)"')
    values: dict[str, str] = {}
    for line in Path(where).read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            values[key] = value
    return values


def bearer(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def run(client: httpx.Client, text: str, token: str | None) -> tuple[int, dict[str, Any]]:
    """One complete `POST /v1/run`: the status code and the JSON body."""
    response = client.post("/v1/run", json={"input": {"text": text}}, headers=bearer(token))
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:500]}
    return response.status_code, body if isinstance(body, dict) else {"raw": body}


def feedback(
    client: httpx.Client, request_id: str, token: str | None, *, score: float, label: str
) -> httpx.Response:
    body = {"request_id": request_id, "score": score, "label": label}
    return client.post("/v1/feedback", json=body, headers=bearer(token))


def prom_query(client: httpx.Client, expr: str) -> list[dict[str, Any]]:
    """An instant query on the Prometheus HTTP API; the `result` list. `AssertionError` when
    Prometheus does not answer `success`."""
    response = client.get("/api/v1/query", params={"query": expr})
    body = response.json()
    assert response.status_code == 200 and body.get("status") == "success", (expr, body)
    result: list[dict[str, Any]] = body["data"]["result"]
    return result


def prom_value(sample: Mapping[str, Any]) -> float:
    """The number of one instant-vector sample (`NaN` stays `NaN`)."""
    return float(sample["value"][1])


def wait_until(
    check: Callable[[], bool], *, timeout_s: float, poll_s: float = 1.0, what: str = ""
) -> None:
    """Poll `check` until it is true, or fail naming `what`."""
    deadline = time.monotonic() + timeout_s
    while not check():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout_s:.0f} s waiting for {what}")
        time.sleep(poll_s)


def dashboard_file(agent: str = AGENT) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((DASHBOARDS / f"{agent}.json").read_text())
    return data


def panel_queries(board: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """(panel title, refId, PromQL) for every target of every panel."""
    return [
        (str(p["title"]), str(t["refId"]), str(t["expr"]))
        for p in board["panels"]
        for t in p["targets"]
    ]


def dashboard_files() -> list[Path]:
    return sorted(DASHBOARDS.glob("*.json"))


def grafana_query(client: httpx.Client, datasource_uid: str, expr: str) -> list[dict[str, Any]]:
    """One instant PromQL query through Grafana's `/api/ds/query`, the way a panel runs it. The
    frames of refId `A`."""
    body = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "prometheus", "uid": datasource_uid},
                "expr": expr,
                "instant": True,
                "range": False,
                "intervalMs": 15000,
                "maxDataPoints": 100,
            }
        ],
        "from": "now-1h",
        "to": "now",
    }
    response = client.post("/api/ds/query", json=body)
    assert response.status_code == 200, (expr, response.status_code, response.text[:500])
    result = response.json()["results"]["A"]
    assert result.get("status", 200) == 200 and not result.get("error"), (expr, result)
    frames: list[dict[str, Any]] = result.get("frames") or []
    return frames


def frame_has_values(frame: Mapping[str, Any]) -> bool:
    values = frame.get("data", {}).get("values") or []
    return any(len(column) > 0 for column in values)


def percentile(values: Sequence[float], q: float) -> float:
    """The nearest-rank `q`th percentile (0 < q <= 100) of `values`."""
    if not values:
        raise ValueError("no values")
    if not 0 < q <= 100:
        raise ValueError(f"q must be in (0, 100], got {q}")
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(ms: Sequence[float]) -> dict[str, float | int]:
    """p50, p95, mean, min, max of latencies in milliseconds, rounded to 0.01 ms."""
    return {
        "count": len(ms),
        "p50_ms": round(percentile(ms, 50), 2),
        "p95_ms": round(percentile(ms, 95), 2),
        "mean_ms": round(sum(ms) / len(ms), 2),
        "min_ms": round(min(ms), 2),
        "max_ms": round(max(ms), 2),
    }
