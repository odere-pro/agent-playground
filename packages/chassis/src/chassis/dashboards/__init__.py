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

__all__ = ["AGENTS_FILE", "DASHBOARD_DIR", "agents_in", "dashboard", "main", "panel_exprs"]

DASHBOARD_DIR = Path("deploy/dashboards")
AGENTS_FILE = "agents.txt"
"""In the output folder: the agents to generate, one per line, `#` for a comment. The list is
deploy config, not chassis code (no agent or engine name lives in the chassis)."""
_AGENT = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}")
_DATASOURCE = {"type": "prometheus", "uid": "${datasource}"}
WINDOW = "5m"
"""suggested: the rate window."""


def _panels(agent: str) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """(title, unit, [(PromQL, legend)]) per panel."""
    a = f'agent="{agent}"'
    w = WINDOW
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
        (
            "Evaluator gate fallback rate",
            "percentunit",
            [
                (
                    f"sum(rate(chassis_gate_fallbacks_total{{{a}}}[{w}])) / "
                    f'sum(rate(chassis_gate_runs_total{{{a},mode="sync"}}[{w}]))',
                    "fallback rate",
                )
            ],
        ),
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
                "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
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
                {"name": "datasource", "type": "datasource", "query": "prometheus", "label": "Data"}
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
