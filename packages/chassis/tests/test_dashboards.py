"""PoC-8 (014 H-7, G.2): one Grafana dashboard per agent, made from one template, and (046 A-5)
one container dashboard with the key metrics of `docs/specs/metrics.md`.

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
    CONTAINER,
    COUNTERS_ROW,
    DASHBOARD_DIR,
    FALLBACK,
    FALLBACK_TARGET,
    FIRST_TRY,
    INPUTS_ROW,
    NORTH_STAR_ROW,
    RETRY,
    agents_in,
    container_dashboard,
    dashboard,
    main,
    panel_exprs,
)
from chassis.dashboards.container import good_tasks
from chassis.metrics import DECLARED
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
    assert sorted(p.stem for p in folder.glob("*.json")) == sorted([*agents, CONTAINER])
    for agent in agents:
        on_disk = json.loads((folder / f"{agent}.json").read_text())
        assert on_disk == dashboard(agent), f"run: uv run python -m chassis.dashboards ({agent})"
    on_disk = json.loads((folder / f"{CONTAINER}.json").read_text())
    assert on_disk == container_dashboard(), "run: uv run python -m chassis.dashboards"


def test_the_cli_writes_one_file_per_agent_and_the_container_board(tmp_path: Path) -> None:
    main(["--out", str(tmp_path), "alpha", "beta"])
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["alpha.json", "beta.json", "container.json"]
    assert json.loads((tmp_path / "alpha.json").read_text()) == dashboard("alpha")
    assert json.loads((tmp_path / "container.json").read_text()) == container_dashboard()


def test_no_agent_takes_the_container_boards_file_name() -> None:
    with pytest.raises(ValueError, match="container"):
        dashboard(CONTAINER)


# --- 046 A-5: the container board, from docs/specs/metrics.md ---

METRICS_SPEC = ROOT / "docs/specs/metrics.md"


def _section(heading: str) -> str:
    text = METRICS_SPEC.read_text()
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else None]


def _table_metrics(heading: str) -> list[str]:
    """The first cell of each body row of the one table under `## <heading>`."""
    rows = [line for line in _section(heading).splitlines() if line.startswith("| ")]
    return [row.split("|")[1].strip() for row in rows[2:]]  # the header, then the rule


def _north_star() -> tuple[str, str]:
    """(the north star's name, its formula) from metrics.md."""
    text = METRICS_SPEC.read_text()
    name = re.search(r"^## North star: (.+)$", text, re.M)
    formula = re.search(r"^- \*\*Formula:\*\* suggested: `([^`]+)`", text, re.M)
    assert name and formula
    return name.group(1), formula.group(1)


def _rows(board: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Each row's title, with the panels under it, in board order."""
    rows: dict[str, list[dict[str, Any]]] = {}
    current: list[dict[str, Any]] | None = None
    for panel in board["panels"]:
        if panel["type"] == "row":
            current = rows.setdefault(panel["title"], [])
        else:
            assert current is not None, f"{panel['title']} is above every row"
            current.append(panel)
    return rows


def _titled(panels: list[dict[str, Any]], title: str) -> dict[str, Any]:
    matches = [p for p in panels if p["title"].lower() == title.lower()]
    assert len(matches) == 1, f"{title!r}: {[p['title'] for p in panels]}"
    return matches[0]


def test_the_container_board_has_a_row_for_the_north_star_its_inputs_and_the_counters() -> None:
    inputs, counters = _table_metrics("Input metrics"), _table_metrics("Counter metrics")
    assert len(inputs) == 6 and len(counters) == 8, (inputs, counters)
    rows = _rows(container_dashboard())
    assert list(rows) == [NORTH_STAR_ROW, INPUTS_ROW, COUNTERS_ROW]
    name, _ = _north_star()
    _titled(rows[NORTH_STAR_ROW], name)
    for metric in inputs:
        _titled(rows[INPUTS_ROW], metric)
    for metric in counters:
        _titled(rows[COUNTERS_ROW], metric)


async def test_every_container_query_names_a_declared_metric_the_chassis_exports() -> None:
    exported = await _exported()
    exprs = panel_exprs(container_dashboard())
    assert exprs, "the container board has queries"
    for expr in exprs:
        names = _METRIC.findall(expr)
        assert names, f"no chassis metric in {expr!r}"
        for name in names:
            family = _family(name, exported)
            assert family, f"{name} in {expr!r} is not exported"
            assert family in DECLARED, f"{family} in {expr!r} is not declared"


def test_the_north_star_query_is_the_spec_formula_and_says_the_label_is_missing() -> None:
    name, formula = _north_star()
    panel = _titled(_rows(container_dashboard())[NORTH_STAR_ROW], name)
    assert panel_exprs({"panels": [panel]}) == [formula]
    assert 'environment="prod"' in formula
    assert 'environment="prod"' in panel["description"]
    assert "sets no environment label" in panel["description"]


NO_PROMETHEUS_SOURCE = (
    "Time to first demo",
    "Chassis release lead time",
    "Escapes: passed the gate, then bad feedback",
    "Guardrail false blocks",
    "Secret or credential leaks",
    "Sidecar cost per replica",
    "Rollbacks per chassis release",
)


def test_a_metric_with_no_prometheus_source_is_a_text_panel_that_names_its_source() -> None:
    board = container_dashboard()
    panels = [p for row in _rows(board).values() for p in row]
    for title in NO_PROMETHEUS_SOURCE:
        panel = _titled(panels, title)
        assert panel["type"] == "text", title
        assert "targets" not in panel, title
        assert "**Source:**" in panel["options"]["content"], title
    for panel in panels:
        if panel["type"] != "text":
            assert panel["targets"], panel["title"]


def test_good_tasks_is_a_lower_bound_and_keeps_agents_with_no_failed_scoring() -> None:
    """046 A-5 review: failed scorings are subtracted, retries that later passed included, so
    the count is a lower bound, and cost per good task carries the same bias. Per agent, an agent
    with no failed scoring must read its ok runs, not drop out: `(failed or ok * 0)`."""
    ok = 'sum by (agent) (increase(chassis_run_duration_seconds_count{status="ok"}[1w]))'
    failed = 'sum by (agent) (increase(chassis_evaluator_calls_total{outcome="fail"}[1w]))'
    assert good_tasks("by (agent)") == f"({ok} - ({failed} or {ok} * 0))"
    assert "vector(0)" not in good_tasks() and "vector(0)" not in good_tasks("by (agent)")
    rows = _rows(container_dashboard())
    bound = "a lower bound: each failed scoring is subtracted, including retries that later passed"
    good = _titled(rows[INPUTS_ROW], "Good tasks per week")
    cost = _titled(rows[COUNTERS_ROW], "Cost per good task")
    assert panel_exprs({"panels": [good]}) == [good_tasks()]
    assert good_tasks() in panel_exprs({"panels": [cost]})[0]
    assert bound in good["description"] and bound in cost["description"]


def test_a_text_panel_says_the_chassis_exports_no_family_for_it() -> None:
    panels = [p for row in _rows(container_dashboard()).values() for p in row]
    for panel in (p for p in panels if p["type"] == "text"):
        assert "The chassis exports no family for it" in panel["options"]["content"]


def test_the_container_gate_fallback_panel_draws_a_target_line_at_ten_percent() -> None:
    counters = _rows(container_dashboard())[COUNTERS_ROW]
    panel = _titled(counters, "Gate fallback rate and fail-open rate")
    defaults = panel["fieldConfig"]["defaults"]
    assert [s["value"] for s in defaults["thresholds"]["steps"]] == [None, FALLBACK_TARGET]
    assert defaults["custom"]["thresholdsStyle"]["mode"] == "line"


def _panel(board: dict[str, Any], title: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = [p for p in board["panels"] if p["title"] == title]
    [panel] = matches
    return panel


_TERM = re.compile(r"sum\(rate\((chassis_[a-z_]+)\{([^}]*)\}\[5m\]\)\)")
_ARITHMETIC = re.compile(r"[0-9.+\-*/() ]+")


def _value(expr: str, counters: dict[str, float]) -> float:
    """`expr` with each `sum(rate(...))` term set to its counter in `counters`, worked out."""

    def term(match: re.Match[str]) -> str:
        name, labels = match.groups()
        assert 'agent="echo"' in labels, expr
        if name in ("chassis_gate_runs_total", "chassis_gate_first_attempt_passes_total"):
            assert 'mode="sync"' in labels, expr
        return repr(counters[name])

    arithmetic = _TERM.sub(term, expr)
    assert _ARITHMETIC.fullmatch(arithmetic), arithmetic
    return float(eval(arithmetic))  # digits and operators only, checked above


def test_the_gate_panels_work_out_from_fixed_counter_values() -> None:
    """046 A-5: per agent, the first-try pass rate is the run-level counter over sync gate runs,
    exact; the retry rate is retried attempts per sync run. 10 sync runs: 8 passed on attempt 1,
    1 passed on its retry, and 1 retried twice and went to the fallback (3 retried attempts).
    The old estimate `1 - (retries + fallbacks) / runs` would read 0.6, not 0.8."""
    counters = {
        "chassis_gate_runs_total": 10.0,
        "chassis_gate_first_attempt_passes_total": 8.0,
        "chassis_gate_retries_total": 3.0,
        "chassis_gate_fallbacks_total": 1.0,
    }
    board = dashboard("echo")
    expected = {FIRST_TRY: 0.8, RETRY: 0.3, FALLBACK: 0.1}
    for title, value in expected.items():
        panel = _panel(board, title)
        [expr] = panel_exprs({"panels": [panel]})
        assert _value(expr, counters) == pytest.approx(value), title
        assert panel["fieldConfig"]["defaults"]["unit"] == "percentunit"
    [first_try] = panel_exprs({"panels": [_panel(board, FIRST_TRY)]})
    assert "chassis_gate_first_attempt_passes_total" in first_try
    assert "retries" not in first_try and "fallbacks" not in first_try
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
