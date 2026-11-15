"""The ring promotion gate in `deploy/prometheus/rules/rings.yaml` (056 CH-7; the product plan,
phase G: "the X-3 error-rate alert as the promotion gate").

A ring is promoted only while its gate alerts are quiet. Each gate alert compares a ring that a
release has reached (`canary`, `ten-percent`) with `all`, the ring still on the previous tag, on
the chassis's own run metrics, grouped by the `ring` label Prometheus copies from the pod label
`agents.platform/chassis-ring`. Static: the file is parsed and `/metrics` is scraped in process,
no socket. Whether the alert stops a rollout on kind is the issue's live box.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import yaml
from chassis.server import ChassisConfig, create_app
from poc_harness.promtext import families

ROOT = Path(__file__).resolve().parents[3]
RULES = ROOT / "deploy/prometheus/rules/rings.yaml"
RINGS_README = ROOT / "deploy/rings/README.md"
_METRIC = re.compile(r"\bchassis_[a-z0-9_]+")
GATE = {"ChassisRingErrorRateAboveBaseline", "ChassisRingLatencyAboveBaseline"}
CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}


def _rules() -> list[dict[str, Any]]:
    data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    return [r for g in data["groups"] for r in g["rules"]]


def _alerts() -> dict[str, dict[str, Any]]:
    return {r["alert"]: r for r in _rules() if "alert" in r}


def _records() -> dict[str, str]:
    return {r["record"]: r["expr"] for r in _rules() if "record" in r}


async def _exported() -> dict[str, str]:
    app = create_app(ChassisConfig.model_validate(CONFIG))
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        app.router.lifespan_context(app),
    ):
        return families((await client.get("/metrics")).text)


def test_the_gate_is_an_error_rate_and_a_latency_alert() -> None:
    assert set(_alerts()) == GATE


async def test_every_ring_record_reads_a_chassis_metric_by_ring() -> None:
    exported = await _exported()
    records = _records()
    assert records, "no recording rule"
    for name, expr in records.items():
        assert name.startswith("chassis:ring_"), name
        assert "by (ring" in expr, f"{name} is not grouped by ring: {expr!r}"
        for metric in _METRIC.findall(expr):
            family = re.sub(r"_(bucket|count|sum)$", "", metric)
            assert family in exported, f"{metric} in {name} is not exported"


def test_each_gate_compares_a_reached_ring_with_the_previous_tag() -> None:
    """The baseline is `ring="all"`, the services the release has not reached; the alert reads
    only the rings it has (canary, ten-percent), so after the last promotion it has nothing to
    compare and stays quiet."""
    records = _records()
    for name in GATE:
        expr = _alerts()[name]["expr"]
        used = [r for r in records if r in expr]
        assert used, f"{name} reads no ring record"
        assert 'ring=~"canary|ten-percent"' in expr, name
        assert 'ring="all"' in expr, f"{name} has no baseline"
        assert "ignoring (ring)" in expr or "ignoring(ring)" in expr, name


def test_every_gate_alert_holds_for_a_while_names_the_ring_and_links_the_steps() -> None:
    for name, alert in _alerts().items():
        assert alert["labels"]["severity"] in ("warning", "critical"), name
        assert alert["labels"]["gate"] == "chassis-ring", name
        assert alert["for"], f"{name} has no `for`"
        assert "{{ $labels.ring }}" in alert["annotations"]["summary"], name
        page, _, anchor = alert["annotations"]["runbook_url"].partition("#")
        assert page.endswith("deploy/rings/README.md") and anchor, name
        assert f"## {anchor.replace('-', ' ')}".lower() in RINGS_README.read_text().lower(), name
