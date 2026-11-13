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
from chassis import CHASSIS_VERSION
from chassis.server import ChassisConfig, create_app
from poc_harness.promtext import families, samples

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


async def _scrape(config: dict[str, Any] = CONFIG) -> str:
    app = create_app(ChassisConfig.model_validate(config))
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        app.router.lifespan_context(app),
    ):
        return (await client.get("/metrics")).text


async def _exported() -> dict[str, str]:
    return families(await _scrape())


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


async def test_the_budget_alert_has_a_zero_to_rise_from_on_every_declared_route() -> None:
    """`increase()` needs a sample before the first refusal. A series born at 1 never rises (CI
    run 42634108264), so the chassis exports the counter at 0 for each `spec.model` route."""
    assert (
        "increase(chassis_model_budget_exhausted_total["
        in _alerts()["ChassisModelBudgetExhausted"]["expr"]
    )
    text = await _scrape()
    assert 'chassis_model_budget_exhausted_total{agent="echo",route="fake-route"} 0' in (
        text.splitlines()
    ), text


_RISE = re.compile(r"\b(?:increase|rate)\((chassis_[a-z0-9_]+_total)\b")
GATED: dict[str, Any] = {
    **CONFIG,
    "spec": {
        **CONFIG["spec"],
        "adapters": {"evaluator": "fake"},
        "harness": {"evaluator_gate": True},
    },
}
"""The config the rise check scrapes: the gate on, so its counters are seeded too."""


async def test_every_counter_a_rule_watches_rise_on_has_a_zero_before_traffic() -> None:
    """117 X-3: a counter an `increase()` or `rate()` rule reads must be scraped at 0 before its
    first count, or the rule misses that count (CI run 42634108264). A new rule on a counter the
    chassis does not seed (`chassis_run_errors_total`, `chassis_tool_calls_total`) fails here."""
    names = {name for rule in _rules() for name in _RISE.findall(rule["expr"])}
    assert names, "no rule reads a counter with increase() or rate()"
    seen = {name for name, _ in samples(await _scrape(GATED))}
    assert names <= seen, sorted(names - seen)


async def test_build_info_carries_the_chassis_version_for_an_alert_to_join() -> None:
    """117 X-3: `chassis_build_info{version}` is 1, so a rule can carry the chassis version with
    `* on (agent) group_left (version) chassis_build_info`."""
    found = [
        dict(labels)
        for (name, labels), number in samples(await _scrape()).items()
        if name == "chassis_build_info" and number == 1
    ]
    assert found == [{"agent": "echo", "version": CHASSIS_VERSION, "agent_version": "0.0.1"}]


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
