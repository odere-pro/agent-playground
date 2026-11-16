"""What every generated board shares: the datasource, the rate window, the fallback target."""

from __future__ import annotations

from typing import Any

DATASOURCE: dict[str, str] = {"type": "prometheus", "uid": "${datasource}"}
"""The dashboard variable `datasource`, so one file fits any Prometheus."""
DATASOURCE_VARIABLE: dict[str, str] = {
    "name": "datasource",
    "type": "datasource",
    "query": "prometheus",
    "label": "Data",
}
WINDOW = "5m"
"""suggested: the rate window."""
FALLBACK_TARGET = 0.1
"""The gate fallback rate target from `docs/specs/metrics.md` (below 10%), drawn as a line."""


def target_line(value: float) -> dict[str, Any]:
    """Field defaults that draw a red line at `value` on a time series panel."""
    return {
        "thresholds": {
            "mode": "absolute",
            "steps": [{"color": "green", "value": None}, {"color": "red", "value": value}],
        },
        "custom": {"thresholdsStyle": {"mode": "line"}},
    }
