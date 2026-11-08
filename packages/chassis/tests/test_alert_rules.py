"""The Prometheus alert rules in `deploy/prometheus/rules/chassis.yaml` (CH-16 adds the first:
the service's model budget refusal; 117 X-3 adds the SLO rules next to it). Every rule names a
metric the chassis exports, waits a `for`, carries a severity, and links a runbook. Static: the
file is parsed; `/metrics` is scraped in process, no socket.
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
RULES = ROOT / "deploy/prometheus/rules/chassis.yaml"
RUNBOOKS = ROOT / "docs/guides/poc-05-runbooks.md"
_METRIC = re.compile(r"\bchassis_[a-z0-9_]+")
CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}


def _rules() -> list[dict[str, Any]]:
    data = yaml.safe_load(RULES.read_text())
    return [r for g in data["groups"] for r in g["rules"]]


def _alerts() -> dict[str, dict[str, Any]]:
    return {r["alert"]: r for r in _rules() if "alert" in r}


async def _exported() -> dict[str, str]:
    app = create_app(ChassisConfig.model_validate(CONFIG))
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        app.router.lifespan_context(app),
    ):
        return families((await client.get("/metrics")).text)


def _family(name: str, exported: dict[str, str]) -> bool:
    if name in exported:
        return True
    return any(name.startswith(f"{family}_") for family in exported)


def test_the_budget_refusal_has_an_alert_on_its_counter() -> None:
    """CH-16: `ChassisModelBudgetExhausted` fires on `chassis_model_budget_exhausted_total`, per
    agent and route, so an exhausted service budget reaches a person and not only a 429."""
    alert = _alerts()["ChassisModelBudgetExhausted"]
    assert "chassis_model_budget_exhausted_total" in alert["expr"]
    assert "increase(" in alert["expr"] or "rate(" in alert["expr"]
    assert alert["labels"]["severity"] in ("warning", "critical")
    assert alert["for"]
    assert "{{ $labels.agent }}" in alert["annotations"]["summary"]
    assert "{{ $labels.route }}" in alert["annotations"]["summary"]


async def test_every_rule_names_a_metric_the_chassis_exports() -> None:
    exported = await _exported()
    for rule in _rules():
        names = _METRIC.findall(rule["expr"])
        assert names, f"no chassis metric in {rule['expr']!r}"
        for name in names:
            assert _family(name, exported), f"{name} in {rule['expr']!r} is not exported"


def test_every_alert_has_a_severity_a_wait_and_a_runbook() -> None:
    for name, alert in _alerts().items():
        assert alert["labels"]["severity"], name
        assert alert["for"], f"{name} has no `for`"
        runbook = alert["annotations"]["runbook_url"]
        anchor = runbook.rsplit("#", 1)
        assert anchor[0].endswith("docs/guides/poc-05-runbooks.md"), name
        assert RUNBOOKS.is_file()
