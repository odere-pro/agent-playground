"""The container dashboard (046 A-5): the key metrics of `docs/specs/metrics.md`, in three rows.

One row per group: the north star, its inputs, and the counter metrics. Each row is one function
that returns its panels, so a later row (cost, SLOs) is one more function in `ROW_GROUPS`. Every
query sums over agents or splits by `agent`: the board is the whole container, not one agent.

A metric with no Prometheus source (it comes from a load run, a rollout record, the golden set, or
a join across traces) is a text panel that names where the number comes from. Every PromQL query
names a family in `chassis.metrics.DECLARED`; `tests/test_dashboards.py` checks it. suggested:
the panels, the windows, and the layout.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from chassis.dashboards._grafana import (
    DATASOURCE,
    DATASOURCE_VARIABLE,
    FALLBACK_TARGET,
    WINDOW,
    target_line,
)

__all__ = [
    "CONTAINER",
    "COUNTERS_ROW",
    "INPUTS_ROW",
    "NORTH_STAR",
    "NORTH_STAR_ROW",
    "ROW_GROUPS",
    "container_dashboard",
    "good_tasks",
]

CONTAINER = "container"
"""The file stem: `deploy/dashboards/container.json`. No agent may take the name."""
NORTH_STAR_ROW = "North star"
INPUTS_ROW = "Input metrics"
COUNTERS_ROW = "Counter metrics"
NORTH_STAR = (
    "count(count by (agent) "
    '(increase(chassis_run_duration_seconds_count{status="ok", environment="prod"}[1w]) > 0))'
)
"""The formula in `docs/specs/metrics.md`, as written there."""
_WEEK = "1w"
"""suggested: the "per week" window; metrics.md says an ISO week in UTC, which PromQL cannot
align to, so the board shows the trailing seven days."""
_LOWER_BOUND = (
    "a lower bound: each failed scoring is subtracted, including retries that later passed."
)
"""046 A-5 review: `evaluator_calls{outcome="fail"}` counts scorings, not runs."""


def good_tasks(by: str = "") -> str:
    """Ok runs minus failed scorings over the week, summed `by` (for example `by (agent)`).

    `(failed or ok * 0)` keeps a group with no failed scoring: it reads 0 there instead of
    dropping out, which `or vector(0)` does only for one summed number."""
    group = f" {by} " if by else ""
    ok = f'sum{group}(increase(chassis_run_duration_seconds_count{{status="ok"}}[{_WEEK}]))'
    failed = f'sum{group}(increase(chassis_evaluator_calls_total{{outcome="fail"}}[{_WEEK}]))'
    return f"({ok} - ({failed} or {ok} * 0))"


@dataclass(frozen=True)
class Panel:
    """One panel: a query panel when `queries` is set, else a text panel with `content`."""

    title: str
    description: str = ""
    queries: Sequence[tuple[str, str]] = ()
    """(PromQL, legend) per target."""
    unit: str = "short"
    kind: str = "timeseries"
    content: str = ""
    defaults: dict[str, Any] = field(default_factory=dict)


def _text(title: str, formula: str, source: str, target: str = "none yet") -> Panel:
    content = (
        f"**Formula:** {formula}\n\n**Source:** {source}\n\n**Target on record:** {target}\n\n"
        "The chassis exports no family for it, so it is a weekly number in the "
        "`record-measurement` note, not a query."
    )
    return Panel(title=title, kind="text", content=content)


def north_star_row() -> list[Panel]:
    return [
        Panel(
            title="Agents in production on the chassis",
            kind="stat",
            queries=[(NORTH_STAR, "agents")],
            description=(
                "Agents with at least one ok run in production in the last seven days "
                '(docs/specs/metrics.md). The query needs `environment="prod"`, and the chassis '
                "sets no environment label: the production scrape adds it (suggested: the name "
                "and value). Until a scrape adds it, this panel shows no data. No target until "
                "the first real week sets the baseline."
            ),
        )
    ]


def input_rows() -> list[Panel]:
    w = WINDOW
    return [
        Panel(
            title="Good tasks per week",
            queries=[(good_tasks(), "good tasks")],
            description=(
                "Ok runs minus failed evaluator scorings, over the last seven days, summed over "
                f"agents. It is {_LOWER_BOUND}"
            ),
        ),
        Panel(
            title="First-attempt pass rate",
            unit="percentunit",
            queries=[
                (
                    f"sum by (agent, mode) "
                    f"(rate(chassis_gate_first_attempt_passes_total[{w}])) "
                    f"/ sum by (agent, mode) (rate(chassis_gate_runs_total[{w}]))",
                    "{{agent}} {{mode}}",
                )
            ],
            description="Gated runs that passed on attempt 1 over gated runs, per agent, exact.",
        ),
        Panel(
            title="Task success rate",
            unit="percentunit",
            queries=[
                (
                    f"sum by (agent, interface) "
                    f'(rate(chassis_run_duration_seconds_count{{status="ok"}}[{w}])) '
                    f"/ sum by (agent, interface) (rate(chassis_requests_total[{w}]))",
                    "{{agent}} {{interface}}",
                )
            ],
            description="Ok runs over runs started, per agent and interface.",
        ),
        Panel(
            title="p95 run latency, and the chassis's share of it",
            unit="s",
            queries=[
                (
                    f"histogram_quantile(0.95, sum by (le, agent) (rate({family}_bucket[{w}])))",
                    f"{{{{agent}}}} {legend}",
                )
                for family, legend in (
                    ("chassis_run_duration_seconds", "run p95"),
                    ("chassis_engine_run_duration_seconds", "engine p95"),
                )
            ],
            description=(
                "The run p95 and the engine call p95 per agent. The gap between them is roughly "
                "the chassis's share. suggested: a stage budget of 25 ms p50 and 50 ms p95 "
                "(022 H-6), recorded, not enforced."
            ),
        ),
        _text(
            "Time to first demo",
            "hours from `agentctl new` to a passing demo, by someone new",
            "a timed run of `agentctl new` (`packages/agentctl`) by a new user",
            "under a day",
        ),
        _text(
            "Chassis release lead time",
            "hours from a chassis tag to the last ring",
            "the ring rollout record of each chassis release",
        ),
    ]


def counter_rows() -> list[Panel]:
    w = WINDOW
    sync_runs = f'sum by (agent) (rate(chassis_gate_runs_total{{mode="sync"}}[{w}]))'
    gate_runs = f"sum by (agent) (rate(chassis_gate_runs_total[{w}]))"
    return [
        _text(
            "Escapes: passed the gate, then bad feedback",
            'runs with `evaluator_calls{outcome="pass"}` and later '
            '`chassis_feedback_total{label="bad"}` (or a score below 0.5), per week',
            "the gate scores and user feedback on each run's trace in the trace store "
            "(`FeedbackPort`, the score named `spec.harness.rubric`); a per-run join "
            "PromQL cannot do",
            "if online scores fall more than 10 points under offline, review the golden set",
        ),
        Panel(
            title="Cost per good task",
            unit="currencyUSD",
            queries=[
                (
                    f"sum(increase(chassis_model_cost_microusd_total[{_WEEK}])) / 1e6 "
                    f"/ {good_tasks()}",
                    "USD per good task",
                )
            ],
            description=(
                "Model cost over the last seven days, every route and the judge included, over "
                "good tasks. Only routes in `spec.observability.prices` have a cost. Target: "
                "the G-5 break-even. Good tasks here are "
                f"{_LOWER_BOUND} So this cost reads high by the same bias."
            ),
        ),
        _text(
            "Guardrail false blocks",
            "blocks on golden inputs / golden inputs",
            "an eval run of the golden set (`packages/eval-kit/golden/`) through the guardrail",
            "below 1% false positives (ADR-008)",
        ),
        Panel(
            title="Guardrail unavailable rate",
            unit="percentunit",
            queries=[
                (
                    f"sum by (agent) (rate(chassis_guardrail_unavailable_total[{w}])) "
                    f"/ sum by (agent) (rate(chassis_requests_total[{w}]))",
                    "{{agent}}",
                )
            ],
            description="The other half of guardrail false blocks: checks the guardrail could "
            "not answer, over runs started.",
        ),
        _text(
            "Secret or credential leaks",
            "stream secret guard hits that reached a client; red-team misses",
            "`python -m eval_kit.redteam` (the misses); a hit that reached a client has no "
            "counter: the chassis counts a block, not a leak",
            "zero; red team 18 of 18",
        ),
        Panel(
            title="Judge agreement and stored gate scores (138 CH-18)",
            unit="percentunit",
            queries=[
                (
                    f"sum by (agent, pair) "
                    f'(rate(chassis_evaluator_agreement_total{{agree="true"}}[{w}])) '
                    f"/ sum by (agent, pair) (rate(chassis_evaluator_agreement_total[{w}]))",
                    "{{agent}} {{pair}}",
                )
            ],
            description=(
                "How often the main and the shadow judge agree, when "
                "`spec.harness.shadow_evaluator` is set. The scores the store missed are the "
                "next panel."
            ),
        ),
        Panel(
            title="Gate scores not stored",
            queries=[
                (
                    "sum by (agent, code) (increase(chassis_gate_feedback_failed_total[1h]))",
                    "{{agent}} {{code}}",
                )
            ],
            description="Gate scores `FeedbackPort` did not store, per hour. The escapes join "
            "needs every one.",
        ),
        Panel(
            title="Gate fallback rate and fail-open rate",
            unit="percentunit",
            queries=[
                (
                    f"sum by (agent) (rate(chassis_gate_fallbacks_total[{w}])) / {sync_runs}",
                    "{{agent}} fallback",
                ),
                (
                    f"sum by (agent) (rate(chassis_gate_failed_open_total[{w}])) / {gate_runs}",
                    "{{agent}} fail-open",
                ),
            ],
            defaults=target_line(FALLBACK_TARGET),
            description=(
                "Fallbacks over sync gate runs (target below 10%, the line), and fail-opens "
                "over gate runs (alert above suggested: 1% over 15 minutes, 117 X-3)."
            ),
        ),
        Panel(
            title="Gate reruns stopped by the budget",
            queries=[
                (
                    "sum by (agent, reason) (increase(chassis_gate_stopped_total[1h]))",
                    "{{agent}} {{reason}}",
                )
            ],
            description="Reruns the run's one budget stopped, per hour, by `timeout` or "
            "`budget` (017 H-4).",
        ),
        _text(
            "Sidecar cost per replica",
            "CPU and RSS per chassis container",
            "the load run (`python -m load_tools.run_matrix`); the chassis does not export its "
            "own CPU or memory",
            "0.05 to 0.1 vCPU, 128 to 256 MiB (ADR-001)",
        ),
        _text(
            "Rollbacks per chassis release",
            "releases pinned back by any service / releases",
            "the ring rollout record of each chassis release",
        ),
    ]


ROW_GROUPS: tuple[tuple[str, Callable[[], list[Panel]]], ...] = (
    (NORTH_STAR_ROW, north_star_row),
    (INPUTS_ROW, input_rows),
    (COUNTERS_ROW, counter_rows),
)
"""The rows, top to bottom. A later row group is one more entry."""
_WIDTH = 12
_HEIGHT = 8


def _grafana_panel(panel: Panel, panel_id: int, x: int, y: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": panel_id,
        "type": panel.kind,
        "title": panel.title,
        "datasource": DATASOURCE,
        "gridPos": {"h": _HEIGHT, "w": _WIDTH, "x": x, "y": y},
    }
    if panel.description:
        out["description"] = panel.description
    if panel.kind == "text":
        out["options"] = {"mode": "markdown", "content": panel.content}
        return out
    out["fieldConfig"] = {"defaults": {"unit": panel.unit, **panel.defaults}, "overrides": []}
    out["targets"] = [
        {"refId": chr(ord("A") + i), "expr": expr, "legendFormat": legend}
        for i, (expr, legend) in enumerate(panel.queries)
    ]
    return out


def _layout() -> list[dict[str, Any]]:
    """Each row header, then its panels two to a line."""
    panels: list[dict[str, Any]] = []
    y = 0
    for title, group in ROW_GROUPS:
        panels.append(
            {
                "id": len(panels) + 1,
                "type": "row",
                "title": title,
                "collapsed": False,
                "datasource": DATASOURCE,
                "gridPos": {"h": 1, "w": 2 * _WIDTH, "x": 0, "y": y},
                "panels": [],
            }
        )
        y += 1
        members = group()
        for index, panel in enumerate(members):
            x, line = (index % 2) * _WIDTH, index // 2
            panels.append(_grafana_panel(panel, len(panels) + 1, x, y + line * _HEIGHT))
        y += ((len(members) + 1) // 2) * _HEIGHT
    return panels


def container_dashboard() -> dict[str, Any]:
    """The container dashboard: the north star, its inputs, and the counter metrics."""
    return {
        "uid": f"chassis-{CONTAINER}",
        "title": "Chassis container: key metrics",
        "tags": ["chassis", "container"],
        "timezone": "utc",
        "schemaVersion": 39,
        "refresh": "1m",
        "time": {"from": "now-7d", "to": "now"},
        "templating": {"list": [DATASOURCE_VARIABLE]},
        "panels": _layout(),
    }
