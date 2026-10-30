"""PoC-8 scope item "Observability", the part that waited: Prometheus and Grafana load the
generated dashboards (`deploy/dashboards/`) against a real chassis. `network`; collected only
with `POC08_STACK=1` (`conftest.py`), and skipped without it when named by path. CI runs it in
`.github/workflows/poc08-stack.yml`; by hand:

    deploy/compose/poc08/stack.sh up && deploy/compose/poc08/stack.sh wait
    export "$(deploy/compose/poc08/stack.sh env)"
    POC08_STACK=1 uv run pytest -m network -v \\
        pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py

The load, in each of two rounds (`ROUNDS`), on the `echo` chassis with every PoC-8 stage on
(deploy/compose/poc08/chassis/drill.yaml): two plain runs, one run with a tool call
(`glossary_lookup`), one run the evaluator gate falls back on (the judge scores the main route's
answer 0.1, one retry, then `fallback-route`), one run that ends in a model error, a thumbs-down
on one plain run and a thumbs-up on the other. After each round the drill waits until Prometheus
has scraped it, so every counter has two samples that differ inside the panels' windows.

Then: Prometheus scrapes the three chassis at `/metrics`; EVERY panel query of
`deploy/dashboards/echo.json` returns a non-empty result with a number in it (windows as the
dashboard has them: `[5m]` and `[1h]`); the `User feedback by label` panel shows `thumbs-down`;
Grafana has every dashboard file loaded, `chassis-echo` with all its panels, and the dashboards'
`${datasource}` variable resolves to the provisioned Prometheus, which answers a panel query
through Grafana. Last, a burst meets 429 `rate_limited`: the rate limit runs, and with
`rate_limit_on_unavailable: fail_closed` every run before it proves Valkey answered.

Credentials come from the env file named by `POC08_ENV_FILE` (made by `stack.sh`), never printed.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from poc_harness.metrics_stack import (
    AGENT,
    CHASSIS,
    DASHBOARD_UID,
    FAIL_INPUT,
    FALLBACK_ANSWER,
    FALLBACK_ROUTE,
    GATE_BAD,
    GATE_INPUT,
    GRAFANA,
    GRAFANA_PASSWORD_KEY,
    GRAFANA_USER,
    PLAIN_INPUT,
    PROMETHEUS,
    RUN_TIMEOUT_S,
    SCRAPE_JOB,
    STACK_VAR,
    THUMBS_DOWN,
    THUMBS_UP,
    TOKEN_KEY,
    TOOL_INPUT,
    dashboard_file,
    dashboard_files,
    feedback,
    frame_has_values,
    grafana_query,
    panel_queries,
    prom_query,
    prom_value,
    read_env_file,
    run,
    wait_until,
)

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get(STACK_VAR) != "1",
        reason=f"a PoC-8 stack drill: needs deploy/compose/poc08 up and {STACK_VAR}=1",
    ),
]

ROUNDS = 2
ROUND = (PLAIN_INPUT, PLAIN_INPUT, TOOL_INPUT, GATE_INPUT, FAIL_INPUT)
SCRAPE_WAIT_S = float(os.environ.get("POC08_SCRAPE_WAIT_S", "90"))
"""suggested: how long the drill waits for Prometheus to scrape a round (scrape interval 5 s)."""
GRAFANA_WAIT_S = float(os.environ.get("POC08_GRAFANA_WAIT_S", "90"))
"""suggested: how long the drill waits for Grafana to provision the dashboards."""
BURST = 60
"""suggested: drill.yaml allows a burst of 30 and 60 a minute, so 60 quick runs meet a 429."""
FEEDBACK_PANEL = "User feedback by label"
A = f'agent="{AGENT}"'


@dataclass
class Drill:
    token: str
    runs: list[tuple[str, int, dict[str, Any]]] = field(default_factory=list)
    feedback: list[httpx.Response] = field(default_factory=list)


@pytest.fixture(scope="module")
def secrets() -> dict[str, str]:
    values = read_env_file()
    missing = [k for k in (TOKEN_KEY, GRAFANA_PASSWORD_KEY) if not values.get(k)]
    assert not missing, f"the env file has no {missing}"
    return values


@pytest.fixture(scope="module")
def prom() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=PROMETHEUS, timeout=10.0) as client:
        yield client


@pytest.fixture(scope="module")
def grafana(secrets: dict[str, str]) -> Iterator[httpx.Client]:
    auth = (GRAFANA_USER, secrets[GRAFANA_PASSWORD_KEY])
    with httpx.Client(base_url=GRAFANA, timeout=10.0, auth=auth) as client:
        yield client


def _total(prom: httpx.Client, expr: str) -> float:
    result = prom_query(prom, expr)
    return prom_value(result[0]) if result else 0.0


def _wait_for_scrape(prom: httpx.Client, runs: int, thumbs_down: int) -> None:
    """Until Prometheus holds every run and every thumbs-down sent so far."""

    def scraped() -> bool:
        requests = _total(prom, f"sum(chassis_requests_total{{{A}}})")
        down = _total(prom, f'sum(chassis_feedback_total{{{A},label="{THUMBS_DOWN}"}})')
        return requests >= runs and down >= thumbs_down

    wait_until(scraped, timeout_s=SCRAPE_WAIT_S, poll_s=2.0, what=f"a scrape of {runs} runs")


@pytest.fixture(scope="module")
def drill(secrets: dict[str, str], prom: httpx.Client) -> Drill:
    """The load: `ROUNDS` rounds of `ROUND` and two feedbacks, each round scraped."""
    record = Drill(token=secrets[TOKEN_KEY])
    with httpx.Client(base_url=CHASSIS, timeout=RUN_TIMEOUT_S) as chassis:
        for done in range(1, ROUNDS + 1):
            plain_ids: list[str] = []
            for text in ROUND:
                code, body = run(chassis, text, record.token)
                record.runs.append((text, code, body))
                if text == PLAIN_INPUT and body.get("request_id"):
                    plain_ids.append(str(body["request_id"]))
            assert len(plain_ids) == 2, record.runs[-len(ROUND) :]
            for request_id, score, label in (
                (plain_ids[0], 0.0, THUMBS_DOWN),
                (plain_ids[1], 1.0, THUMBS_UP),
            ):
                record.feedback.append(
                    feedback(chassis, request_id, record.token, score=score, label=label)
                )
            _wait_for_scrape(prom, runs=done * len(ROUND), thumbs_down=done)
    return record


def _runs(drill: Drill, text: str) -> list[tuple[int, dict[str, Any]]]:
    return [(code, body) for t, code, body in drill.runs if t == text]


def test_prometheus_scrapes_every_chassis_at_metrics(prom: httpx.Client) -> None:
    def all_up() -> bool:
        body = prom.get("/api/v1/targets", params={"state": "active"}).json()
        mine = [t for t in body["data"]["activeTargets"] if t["labels"]["job"] == SCRAPE_JOB]
        return len(mine) == 3 and all(t["health"] == "up" for t in mine)

    wait_until(all_up, timeout_s=SCRAPE_WAIT_S, poll_s=2.0, what="three chassis targets up")
    body = prom.get("/api/v1/targets", params={"state": "active"}).json()
    mine = [t for t in body["data"]["activeTargets"] if t["labels"]["job"] == SCRAPE_JOB]
    for target in mine:
        assert target["scrapeUrl"].endswith(":8080/metrics"), target["scrapeUrl"]


def test_the_drill_runs_end_as_planned(drill: Drill) -> None:
    """The load did what the panels need: ok runs, a tool call, a fallback, an error, feedback."""
    for code, body in _runs(drill, PLAIN_INPUT):
        assert code == 200 and body["status"] == "ok", body
    for code, body in _runs(drill, TOOL_INPUT):
        assert code == 200 and body["status"] == "ok", body
        assert "glossary_lookup" in json.dumps(body["output"].get("tool_calls")), body
    for code, body in _runs(drill, GATE_INPUT):
        assert code == 200 and body["status"] == "fallback", body
        assert body["output"]["text"] == FALLBACK_ANSWER
        assert GATE_BAD not in json.dumps(body)
        assert body["metrics"]["model_route"] == FALLBACK_ROUTE
    for _, body in _runs(drill, FAIL_INPUT):
        assert body["status"] == "error", body
    for answer in drill.feedback:
        assert answer.status_code == 202, answer.text


@pytest.mark.parametrize(
    ("title", "ref", "expr"),
    panel_queries(dashboard_file()),
    ids=[f"{title}/{ref}" for title, ref, _ in panel_queries(dashboard_file())],
)
def test_every_panel_query_has_data(
    drill: Drill, prom: httpx.Client, title: str, ref: str, expr: str
) -> None:
    """Each query of the agent's dashboard, as the file has it, on the real Prometheus."""
    result = prom_query(prom, expr)
    assert result, f"panel {title!r} ({ref}) has no series: {expr}"
    values = [prom_value(sample) for sample in result]
    assert any(math.isfinite(v) for v in values), f"panel {title!r} ({ref}): {values}"


def test_the_feedback_panel_shows_thumbs_down(drill: Drill, prom: httpx.Client) -> None:
    [expr] = [e for title, _, e in panel_queries(dashboard_file()) if title == FEEDBACK_PANEL]
    by_label = {s["metric"].get("label"): prom_value(s) for s in prom_query(prom, expr)}
    assert by_label.get(THUMBS_DOWN, 0.0) > 0, by_label
    assert THUMBS_UP in by_label, by_label


def test_grafana_loaded_every_dashboard_file(grafana: httpx.Client) -> None:
    expected = {json.loads(path.read_text())["uid"] for path in dashboard_files()}
    assert expected

    def loaded() -> bool:
        found = grafana.get("/api/search", params={"type": "dash-db", "limit": 5000}).json()
        return expected <= {d["uid"] for d in found}

    wait_until(loaded, timeout_s=GRAFANA_WAIT_S, poll_s=2.0, what="Grafana to load dashboards")


def test_grafana_has_the_agent_dashboard_with_every_panel(grafana: httpx.Client) -> None:
    response = grafana.get(f"/api/dashboards/uid/{DASHBOARD_UID}")
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert body["meta"].get("provisioned") is True
    board = body["dashboard"]
    on_disk = dashboard_file()
    assert [p["title"] for p in board["panels"]] == [p["title"] for p in on_disk["panels"]]
    assert FEEDBACK_PANEL in {p["title"] for p in board["panels"]}
    file_exprs = {p["id"]: [t["expr"] for t in p["targets"]] for p in on_disk["panels"]}
    for panel in board["panels"]:
        assert panel["datasource"]["uid"] == "${datasource}", panel["title"]
        assert [t["expr"] for t in panel["targets"]] == file_exprs[panel["id"]], panel["title"]


def test_the_dashboard_datasource_resolves_to_prometheus(
    drill: Drill, grafana: httpx.Client
) -> None:
    """The `datasource` variable (type `datasource`, query `prometheus`) has no saved value, so
    Grafana takes the default datasource of that type. It must exist, be healthy, and answer the
    feedback panel's query with data."""
    [variable] = [v for v in dashboard_file()["templating"]["list"] if v["name"] == "datasource"]
    kind = variable["query"]
    sources = grafana.get("/api/datasources").json()
    candidates = [s for s in sources if s["type"] == kind]
    assert candidates, f"no {kind} datasource in Grafana"
    [chosen] = [s for s in candidates if s.get("isDefault")] or candidates[:1]
    health = grafana.get(f"/api/datasources/uid/{chosen['uid']}/health")
    assert health.status_code == 200 and health.json().get("status") == "OK", health.text[:500]
    [expr] = [e for title, _, e in panel_queries(dashboard_file()) if title == FEEDBACK_PANEL]
    frames = grafana_query(grafana, chosen["uid"], expr)
    assert frames and any(frame_has_values(f) for f in frames), frames


def test_a_burst_meets_the_rate_limit_kept_in_valkey(drill: Drill) -> None:
    """Last: it empties the drill caller's bucket. 429 `rate_limited` with `Retry-After`."""
    refused: httpx.Response | None = None
    with httpx.Client(base_url=CHASSIS, timeout=RUN_TIMEOUT_S) as chassis:
        for _ in range(BURST):
            response = chassis.post(
                "/v1/run",
                json={"input": {"text": PLAIN_INPUT}},
                headers={"Authorization": f"Bearer {drill.token}"},
            )
            assert response.status_code in (200, 429), (response.status_code, response.text[:300])
            if response.status_code == 429:
                refused = response
                break
    assert refused is not None, f"no 429 in {BURST} runs"
    assert "rate_limited" in refused.text
    assert int(refused.headers["Retry-After"]) >= 1
