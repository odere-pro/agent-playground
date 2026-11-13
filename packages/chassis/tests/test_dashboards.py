"""PoC-8 (014 H-7, G.2): one Grafana dashboard per agent, made from one template.

Each panel's PromQL names only metrics the chassis exports at `GET /metrics`, and the files under
`deploy/dashboards/` are exactly what the generator makes. Offline.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from chassis.dashboards import (
    DASHBOARD_DIR,
    FALLBACK,
    FIRST_TRY,
    RETRY,
    agents_in,
    dashboard,
    main,
    panel_exprs,
)
from chassis.server import ChassisConfig, create_app
from poc_harness.promtext import families

ROOT = Path(__file__).resolve().parents[3]
CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
_METRIC = re.compile(r"\b(chassis_[a-z0-9_]+)\b")
_HISTOGRAM_PARTS = ("_bucket", "_count", "_sum")
TOPICS = ("requests", "latency", "errors", "tokens", "cost", "fallback", "feedback")


async def _exported() -> dict[str, str]:
    app = create_app(ChassisConfig.model_validate(CONFIG))
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        app.router.lifespan_context(app),
    ):
        await client.post("/v1/run", json={"input": {"text": "hello"}})
        return families((await client.get("/metrics")).text)


def _family(name: str, exported: dict[str, str]) -> str | None:
    if name in exported:
        return name
    for part in _HISTOGRAM_PARTS:
        base = name.removesuffix(part)
        if base != name and exported.get(base) == "histogram":
            return base
    return None


async def test_every_panel_names_a_metric_the_chassis_exports() -> None:
    exported = await _exported()
    exprs = panel_exprs(dashboard("echo"))
    assert exprs, "the dashboard has queries"
    for expr in exprs:
        names = _METRIC.findall(expr)
        assert names, f"no chassis metric in {expr!r}"
        for name in names:
            assert _family(name, exported), f"{name} in {expr!r} is not exported"


def test_the_dashboard_covers_every_topic_for_its_agent_only() -> None:
    board = dashboard("echo-openai-agents")
    titles = " ".join(p["title"].lower() for p in board["panels"])
    for topic in TOPICS:
        assert topic in titles, topic
    for expr in panel_exprs(board):
        assert 'agent="echo-openai-agents"' in expr, expr
    assert board["uid"] == "chassis-echo-openai-agents"
    assert board["title"] == "Agent echo-openai-agents"


def test_a_bad_agent_name_is_refused() -> None:
    with pytest.raises(ValueError, match="agent"):
        dashboard('echo"} or vector(1)')


def test_the_generated_files_are_up_to_date() -> None:
    folder = ROOT / DASHBOARD_DIR
    agents = agents_in(folder)
    assert "echo" in agents
    assert sorted(p.stem for p in folder.glob("*.json")) == sorted(agents)
    for agent in agents:
        on_disk = json.loads((folder / f"{agent}.json").read_text())
        assert on_disk == dashboard(agent), f"run: uv run python -m chassis.dashboards ({agent})"


def test_the_cli_writes_one_file_per_agent(tmp_path: Path) -> None:
    main(["--out", str(tmp_path), "alpha", "beta"])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["alpha.json", "beta.json"]
    assert json.loads((tmp_path / "alpha.json").read_text()) == dashboard("alpha")


def _panel(board: dict[str, Any], title: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = [p for p in board["panels"] if p["title"] == title]
    [panel] = matches
    return panel


_TERM = re.compile(r"sum\(rate\((chassis_[a-z_]+)\{([^}]*)\}\[5m\]\)\)")
_ARITHMETIC = re.compile(r"[0-9.+\-*/() ]+")


_LABEL = re.compile(r'(\w+)="([^"]*)"')
FIRST_ATTEMPTS = "chassis_gate_first_attempts_total"
Series = tuple[str, str, str]
"""(family, mode, outcome); "" for a label the family does not carry."""


def _value(expr: str, counters: dict[Series, float]) -> float:
    """`expr` with each `sum(rate(...))` term set to the sum of the series in `counters` its
    label selector matches, worked out."""

    def term(match: re.Match[str]) -> str:
        name, selector = match.groups()
        labels = dict(_LABEL.findall(selector))
        assert labels.pop("agent") == "echo", expr
        if name in ("chassis_gate_runs_total", FIRST_ATTEMPTS):
            assert labels.get("mode") in ("sync", "async"), expr
        total = sum(
            value
            for (family, mode, outcome), value in counters.items()
            if family == name
            and labels.get("mode", mode) == mode
            and labels.get("outcome", outcome) == outcome
        )
        return repr(total)

    arithmetic = _TERM.sub(term, expr)
    assert _ARITHMETIC.fullmatch(arithmetic), arithmetic
    return float(eval(arithmetic))  # digits and operators only, checked above


def test_the_gate_panels_work_out_from_fixed_counter_values() -> None:
    """046 A-5: per agent, the first-try pass rate is first attempts that passed over first
    attempts scored, one query per mode; the retry and fallback rates are over sync gate runs.
    10 sync runs: 8 scored attempts 1 passed, 2 failed (1 passed on its retry, 1 retried twice
    and went to the fallback: 3 retried attempts). Async: 3 passed, 1 failed. The old estimate
    `1 - (retries + fallbacks) / runs` would read 0.6, not 0.8."""
    counters: dict[Series, float] = {
        ("chassis_gate_runs_total", "sync", ""): 10.0,
        ("chassis_gate_runs_total", "async", ""): 4.0,
        (FIRST_ATTEMPTS, "sync", "pass"): 8.0,
        (FIRST_ATTEMPTS, "sync", "fail"): 2.0,
        (FIRST_ATTEMPTS, "async", "pass"): 3.0,
        (FIRST_ATTEMPTS, "async", "fail"): 1.0,
        ("chassis_gate_retries_total", "", ""): 3.0,
        ("chassis_gate_fallbacks_total", "", ""): 1.0,
    }
    board = dashboard("echo")
    expected = {FIRST_TRY: [0.8, 0.75], RETRY: [0.3], FALLBACK: [0.1]}
    for title, values in expected.items():
        panel = _panel(board, title)
        exprs = panel_exprs({"panels": [panel]})
        assert [_value(e, counters) for e in exprs] == pytest.approx(values), title
        assert panel["fieldConfig"]["defaults"]["unit"] == "percentunit"
    first_try = _panel(board, FIRST_TRY)
    legends = [t["legendFormat"] for t in first_try["targets"]]
    assert legends == ["first-try pass rate", "first-try pass rate (async)"]
    for expr in panel_exprs({"panels": [first_try]}):
        assert FIRST_ATTEMPTS in expr and "chassis_gate_runs_total" not in expr
        assert "retries" not in expr and "fallbacks" not in expr
    assert not [p for p in board["panels"] if "no fallback" in p["title"].lower()]


def test_the_fallback_panel_draws_a_target_line_at_ten_percent() -> None:
    defaults = _panel(dashboard("echo"), "Evaluator gate fallback rate")["fieldConfig"]["defaults"]
    assert [s["value"] for s in defaults["thresholds"]["steps"]] == [None, 0.1]
    assert defaults["custom"]["thresholdsStyle"]["mode"] == "line"


def test_every_panel_links_to_langfuse_traces_and_loki_logs() -> None:
    board = dashboard("echo")
    variables = {v["name"]: v["type"] for v in board["templating"]["list"]}
    assert variables["langfuse_url"] == variables["langfuse_project"] == "textbox"
    assert variables["loki_uid"] == "textbox"
    for panel in board["panels"]:
        links = {link["title"]: link["url"] for link in panel["links"]}
        assert set(links) == {"Langfuse traces", "Loki logs"}, panel["title"]
        langfuse = "${langfuse_url}/project/${langfuse_project}/traces?"
        assert links["Langfuse traces"].startswith(langfuse)
        assert "echo" in links["Langfuse traces"]
        assert links["Loki logs"].startswith("/explore?")
        assert "${loki_uid}" in links["Loki logs"] and "echo" in links["Loki logs"]


def test_token_and_cost_spend_can_be_filtered_by_route() -> None:
    """006 G-6: an ad hoc filter on the Prometheus datasource narrows every query by label, so
    the token and cost series (labeled `agent` and `route`, never `user`) filter by route. The
    agent is the dashboard itself: one file per agent. The query text stays plain PromQL, so the
    PoC-8 live check still sends it to Prometheus as the file has it."""
    board = dashboard("echo")
    [adhoc] = [v for v in board["templating"]["list"] if v["type"] == "adhoc"]
    assert adhoc["datasource"] == {"type": "prometheus", "uid": "${datasource}"}
    for title in ("Model tokens by direction", "Model cost (USD per hour)"):
        [expr] = panel_exprs({"panels": [_panel(board, title)]})
        assert 'agent="echo"' in expr, expr
        assert "$" not in expr, expr
