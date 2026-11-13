"""One Grafana dashboard per agent, from one template (PoC-8, 014 H-7, epic G.2).

`dashboard(agent)` is the dashboard JSON: requests, latency, errors, tokens, cost, the
evaluator gate's fallback rate, and user feedback by label, each query filtered on
`agent="<agent>"`, the label every sample of `GET /metrics` carries. Every metric a query names
is one the chassis exports (`chassis.metrics`); `tests/test_dashboards.py` checks it against a
real scrape.

`python -m chassis.dashboards [--out DIR] [AGENT ...]` writes `<agent>.json` per agent into
`DASHBOARD_DIR` (run it from the repository root), by default for the agents listed in its
`agents.txt`. The files there are generated: edit this template, never them. The datasource is
the dashboard variable `datasource`, so one file fits any Prometheus. suggested: the panels, the
windows, and the layout.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

__all__ = [
    "AGENTS_FILE",
    "DASHBOARD_DIR",
    "FALLBACK",
    "NO_FALLBACK",
    "RETRY",
    "agents_in",
    "dashboard",
    "main",
    "panel_exprs",
]

DASHBOARD_DIR = Path("deploy/dashboards")
AGENTS_FILE = "agents.txt"
"""In the output folder: the agents to generate, one per line, `#` for a comment. The list is
deploy config, not chassis code (no agent or engine name lives in the chassis)."""
_AGENT = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}")
_DATASOURCE = {"type": "prometheus", "uid": "${datasource}"}
WINDOW = "5m"
"""suggested: the rate window."""
NO_FALLBACK = "Evaluator gate sync runs with no fallback"
"""`1 - fallbacks / sync runs`. Not a first-try pass rate: the gate counts each retried attempt
and, after the last retry, a fallback too, so one run can add to both counters and to retries more
than once. The counters cannot tell how many runs passed on attempt 1; this share is an upper
bound on it."""
RETRY = "Evaluator gate retry rate"
FALLBACK = "Evaluator gate fallback rate"
FALLBACK_TARGET = 0.1
"""The gate fallback rate target from `docs/specs/metrics.md` (below 10%), drawn as a line."""
_LINK_VARIABLES = (
    ("langfuse_url", "Langfuse URL"),
    ("langfuse_project", "Langfuse project id"),
    ("loki_uid", "Loki datasource uid"),
)
"""suggested: per-deploy textbox variables the panel links read, so no host is in the file.
suggested: the Loki stream label `agent`; nothing in `deploy/` provisions Loki or that label yet.
suggested: the Langfuse `search` query parameter on the project trace list."""
_FILTERS: dict[str, Any] = {
    "name": "filters",
    "type": "adhoc",
    "datasource": _DATASOURCE,
    "label": "Filters",
}
"""An ad hoc filter (006 G-6): Grafana adds each label pair to every query, so the token and cost
panels narrow by `route`. The query text stays plain PromQL. The agent is the dashboard itself.
`user` is not a metric label (004 G-2), so it is not a filter here."""


def _links(agent: str) -> list[dict[str, Any]]:
    """The Langfuse trace list and the Loki logs of `agent`, over the dashboard's time range."""
    left = quote(
        json.dumps(
            {
                "datasource": "${loki_uid}",
                "queries": [{"refId": "A", "expr": f'{{agent="{agent}"}}'}],  # suggested: label
                "range": {"from": "${__from}", "to": "${__to}"},
            },
            separators=(",", ":"),
        ),
        safe="${}:,",
    )
    return [
        {
            "title": "Langfuse traces",
            "url": f"${{langfuse_url}}/project/${{langfuse_project}}/traces?search={agent}",
            "targetBlank": True,
        },
        {"title": "Loki logs", "url": f"/explore?left={left}", "targetBlank": True},
    ]


def _field_defaults(title: str, unit: str) -> dict[str, Any]:
    defaults: dict[str, Any] = {"unit": unit}
    if title == FALLBACK:
        defaults["thresholds"] = {
            "mode": "absolute",
            "steps": [
                {"color": "green", "value": None},
                {"color": "red", "value": FALLBACK_TARGET},
            ],
        }
        defaults["custom"] = {"thresholdsStyle": {"mode": "line"}}
    return defaults


def _panels(agent: str) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """(title, unit, [(PromQL, legend)]) per panel."""
    a = f'agent="{agent}"'
    w = WINDOW
    sync_runs = f'sum(rate(chassis_gate_runs_total{{{a},mode="sync"}}[{w}]))'
    retries = f"sum(rate(chassis_gate_retries_total{{{a}}}[{w}]))"
    fallbacks = f"sum(rate(chassis_gate_fallbacks_total{{{a}}}[{w}]))"
    return [
        (
            "Requests by interface and status",
            "reqps",
            [
                (
                    f"sum by (interface, status) "
                    f"(rate(chassis_run_duration_seconds_count{{{a}}}[{w}]))",
                    "{{interface}} {{status}}",
                )
            ],
        ),
        (
            "Latency (p50, p95)",
            "s",
            [
                (
                    f"histogram_quantile({q}, sum by (le) "
                    f"(rate(chassis_run_duration_seconds_bucket{{{a}}}[{w}])))",
                    f"p{int(q * 100)}",
                )
                for q in (0.5, 0.95)
            ],
        ),
        (
            "Errors by code",
            "reqps",
            [(f"sum by (code) (rate(chassis_run_errors_total{{{a}}}[{w}]))", "{{code}}")],
        ),
        (
            "Model tokens by direction",
            "short",
            [
                (
                    f"sum by (direction) (rate(chassis_model_tokens_total{{{a}}}[{w}]))",
                    "{{direction}}",
                )
            ],
        ),
        (
            "Model cost (USD per hour)",
            "currencyUSD",
            [
                (
                    f"sum by (route) "
                    f"(increase(chassis_model_cost_microusd_total{{{a}}}[1h])) / 1e6",
                    "{{route}}",
                )
            ],
        ),
        (NO_FALLBACK, "percentunit", [(f"1 - {fallbacks} / {sync_runs}", "no fallback")]),
        (RETRY, "percentunit", [(f"{retries} / {sync_runs}", "retry rate")]),
        (FALLBACK, "percentunit", [(f"{fallbacks} / {sync_runs}", "fallback rate")]),
        (
            "User feedback by label",
            "short",
            [
                (
                    f"sum by (label) (increase(chassis_feedback_total{{{a}}}[1h]))",
                    "{{label}}",
                )
            ],
        ),
    ]


def dashboard(agent: str) -> dict[str, Any]:
    """The dashboard for `agent`. `ValueError` for a name that is not a plain agent name."""
    if not _AGENT.fullmatch(agent):
        raise ValueError(f"not an agent name: {agent!r}")
    panels = []
    for index, (title, unit, queries) in enumerate(_panels(agent)):
        panels.append(
            {
                "id": index + 1,
                "type": "timeseries",
                "title": title,
                "datasource": _DATASOURCE,
                "gridPos": {"h": 8, "w": 12, "x": (index % 2) * 12, "y": (index // 2) * 8},
                "fieldConfig": {"defaults": _field_defaults(title, unit), "overrides": []},
                "links": _links(agent),
                "targets": [
                    {"refId": chr(ord("A") + i), "expr": expr, "legendFormat": legend}
                    for i, (expr, legend) in enumerate(queries)
                ],
            }
        )
    return {
        "uid": f"chassis-{agent}"[:40],
        "title": f"Agent {agent}",
        "tags": ["chassis", "agent"],
        "timezone": "utc",
        "schemaVersion": 39,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "templating": {
            "list": [
                {
                    "name": "datasource",
                    "type": "datasource",
                    "query": "prometheus",
                    "label": "Data",
                },
                *(
                    {"name": name, "type": "textbox", "query": "", "label": label}
                    for name, label in _LINK_VARIABLES
                ),
                _FILTERS,
            ]
        },
        "panels": panels,
    }


def panel_exprs(board: dict[str, Any]) -> list[str]:
    """Every PromQL query in `board`."""
    return [t["expr"] for p in board["panels"] for t in p["targets"]]


def agents_in(folder: Path) -> list[str]:
    """The agents listed in `folder/agents.txt`."""
    lines = (folder / AGENTS_FILE).read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m chassis.dashboards", description=__doc__)
    parser.add_argument("agents", nargs="*", help=f"default: the agents in --out/{AGENTS_FILE}")
    parser.add_argument("--out", type=Path, default=DASHBOARD_DIR)
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    for agent in args.agents or agents_in(out):
        path = out / f"{agent}.json"
        path.write_text(json.dumps(dashboard(agent), indent=2) + "\n")
        print(path)
