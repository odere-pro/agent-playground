"""PoC-8 (014 H-7): `GET /metrics` on the public app, in the Prometheus text format.

The counters are the ones the chassis already counts through `TelemetryPort`; the run latency and
the errors by code come from the `chassis.run` span. Tokens and cost come from the model proxy's
usage. The evaluator gate counts gated runs next to its fallbacks. Offline: httpx over ASGI.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import pytest
from chassis.adapters.otel import OtelTelemetry
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Event, Start
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel, ScriptRule
from chassis.fakes.auth import StaticTokens
from chassis.fakes.evaluator import ScoreRule, ScriptedEvaluator
from chassis.metrics import CONTENT_TYPE, MetricsRegistry
from chassis.ports.auth import Principal
from chassis.ports.bundle import PortBundle
from chassis.ports.telemetry import TelemetryPort
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from fastapi import FastAPI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from promtext import families, samples, value

CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
CHAT = {"model": "big-default", "messages": [{"role": "user", "content": "simplify: a b"}]}


def _config(**spec: Any) -> ChassisConfig:
    return ChassisConfig.model_validate({**CONFIG, "spec": {**CONFIG["spec"], **spec}})


def _ports(engine: FakeEngine | None = None, **extra: Any) -> PortBundle:
    return PortBundle(
        model=ScriptedModel([ScriptRule(match="simplify", reply="Plain words.")]),
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=extra.pop("telemetry", None) or InMemoryTelemetry(),
        **extra,
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


async def _scrape(app: FastAPI, *calls: dict[str, Any]) -> str:
    async with _client(app) as client, app.router.lifespan_context(app):
        for body in calls:
            await client.post("/v1/run", json=body)
        res = await client.get("/metrics")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == CONTENT_TYPE
    return res.text


# --- The registry and its text format ---


def test_the_registry_renders_counters_and_histograms_in_the_text_format() -> None:
    registry = MetricsRegistry()
    registry.count("chassis.requests", 2, {"interface": "native"})
    registry.span_ended("chassis.run", {"interface": "native"}, 0.2, "ok")
    text = registry.render({"agent": "echo"})
    kinds = families(text)
    assert kinds["chassis_requests_total"] == "counter"
    assert kinds["chassis_run_duration_seconds"] == "histogram"
    assert value(text, "chassis_requests_total", agent="echo", interface="native") == 2
    count = value(text, "chassis_run_duration_seconds_count", interface="native", status="ok")
    assert count == 1
    assert value(text, "chassis_run_duration_seconds_bucket", le="+Inf", status="ok") == 1
    assert value(text, "chassis_run_duration_seconds_bucket", le="0.1", status="ok") == 0
    assert value(text, "chassis_run_duration_seconds_sum", status="ok") == pytest.approx(0.2)


def test_label_values_are_escaped_and_names_are_sanitized() -> None:
    registry = MetricsRegistry()
    registry.count("chassis.odd-name", 1, {"reason": 'a "quoted"\nline\\x'})
    text = registry.render({})
    assert 'chassis_odd_name_total{reason="a \\"quoted\\"\\nline\\\\x"} 1' in text
    samples(text)  # every line parses


def test_the_dashboard_families_are_declared_before_anything_is_counted() -> None:
    kinds = families(MetricsRegistry().render({"agent": "echo"}))
    for family in (
        "chassis_requests_total",
        "chassis_run_duration_seconds",
        "chassis_run_errors_total",
        "chassis_model_tokens_total",
        "chassis_model_cost_microusd_total",
        "chassis_gate_runs_total",
        "chassis_gate_fallbacks_total",
    ):
        assert family in kinds, family


def _otel() -> OtelTelemetry:
    return OtelTelemetry(span_exporters=[InMemorySpanExporter()], log_sink=lambda _: None)


@pytest.mark.parametrize("make", [InMemoryTelemetry, _otel], ids=["memory", "otel"])
def test_both_telemetry_adapters_feed_their_registry(make: Any) -> None:
    telemetry = make()
    port: TelemetryPort = telemetry
    port.counter("chassis.requests", agent="echo", interface="native")
    with port.span("chassis.run", interface="openai", request_id="r1") as span:
        span.attributes["error.code"] = "engine_error"
    text = telemetry.metrics.render({"agent": "echo"})
    assert value(text, "chassis_requests_total", interface="native") == 1
    assert value(text, "chassis_run_duration_seconds_count", interface="openai") == 1
    assert value(text, "chassis_run_errors_total", interface="openai", code="engine_error") == 1


# --- `GET /metrics` on the public app ---


async def test_the_fake_profile_serves_metrics_with_no_network_or_key() -> None:
    app = create_app(_config())  # the ports come from the `fake` profile
    text = await _scrape(app, {"input": {"text": "hello"}})
    assert value(text, "chassis_requests_total", agent="echo", interface="native") == 1
    assert (
        value(
            text,
            "chassis_run_duration_seconds_count",
            agent="echo",
            interface="native",
            status="ok",
        )
        == 1
    )


async def test_a_run_is_counted_by_interface_and_status_with_its_latency() -> None:
    app = create_app(_config(), _ports())
    text = await _scrape(app, {"input": {"text": "a"}}, {"input": {"text": "b"}, "stream": True})
    assert value(text, "chassis_requests_total", interface="native") == 2
    assert value(text, "chassis_run_duration_seconds_count", interface="native", status="ok") == 2
    assert value(text, "chassis_run_duration_seconds_bucket", le="+Inf", status="ok") == 2


async def _fails(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    yield Start(request_id=ctx.request_id)
    raise RuntimeError("boom")


async def test_a_run_error_is_counted_by_code() -> None:
    app = create_app(_config(), _ports(FakeEngine(handle=_fails)))
    text = await _scrape(app, {"input": {"text": "x"}})
    assert value(text, "chassis_run_errors_total", interface="native", code="engine_error") == 1
    assert value(text, "chassis_run_duration_seconds_count", status="error") == 1
    assert value(text, "chassis_run_duration_seconds_count", status="ok") == 0


async def test_model_tokens_and_cost_come_from_the_proxy_usage() -> None:
    """The scripted model reports 10 input and 5 output tokens per call. suggested: prices are USD
    per million tokens, so the cost in micro-USD is tokens times the price."""
    prices = {"big-default": {"input_per_mtok": 1.5, "output_per_mtok": 4.0}}
    app = create_app(_config(observability={"prices": prices}), _ports())
    async with (
        _client(create_proxy_app(app)) as proxy,
        _client(app) as client,
        app.router.lifespan_context(app),
    ):
        assert (await proxy.post("/v1/chat/completions", json=CHAT)).status_code == 200
        streamed = await proxy.post("/v1/chat/completions", json={**CHAT, "stream": True})
        assert streamed.status_code == 200
        unpriced = await proxy.post("/v1/chat/completions", json={**CHAT, "model": "other"})
        assert unpriced.status_code == 200
        text = (await client.get("/metrics")).text
    tokens = "chassis_model_tokens_total"
    assert value(text, tokens, route="big-default", direction="input") == 20
    assert value(text, tokens, route="big-default", direction="output") == 10
    assert value(text, tokens, route="other", direction="input") == 10
    cost = "chassis_model_cost_microusd_total"
    assert value(text, cost, route="big-default") == 2 * (10 * 1.5 + 5 * 4.0)
    assert value(text, cost, route="other") == 0, "a route with no price has no cost"


# --- The evaluator gate's fallback rate ---

BAD, GOOD, FALLBACK = "The report was signed.", "Dana signed it in May.", "fallback-route"


class _RouteEngine(FakeEngine):
    """BAD on any route but the fallback, GOOD on it."""

    def __init__(self) -> None:
        super().__init__(handle=self._answer)

    async def _answer(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        yield Delta(text=GOOD if ctx.model_route == FALLBACK else BAD)
        yield End()


async def test_gated_runs_and_fallbacks_are_both_counted() -> None:
    evaluator = ScriptedEvaluator([ScoreRule(match=BAD, score=0.2)], default=0.9)
    config = _config(
        model={"route": "big-default", "fallback_route": FALLBACK},
        harness={"evaluator_gate": True, "retries": 0, "threshold": 0.7},
    )
    app = create_app(config, _ports(_RouteEngine(), evaluator=evaluator))
    text = await _scrape(app, {"input": {"text": "simplify: x"}})
    assert value(text, "chassis_gate_runs_total", mode="sync") == 1
    assert value(text, "chassis_gate_fallbacks_total") == 1


# --- Who may read `/metrics` ---

TOKENS = {
    "tok-run": Principal("user-run", frozenset({"agent:run"})),
    "tok-metrics": Principal("prometheus", frozenset({"metrics:read"})),
}


def _auth_app(**observability: Any) -> FastAPI:
    config = _config(observability=observability) if observability else _config()
    return create_app(config, _ports(auth=StaticTokens(TOKENS)))


async def _get(app: FastAPI, token: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with _client(app) as client, app.router.lifespan_context(app):
        return await client.get("/metrics", headers=headers)


async def test_metrics_need_no_token_by_default_even_with_auth_on() -> None:
    """suggested: `spec.observability.metrics_scope` is unset, so a scraper needs no token."""
    res = await _get(_auth_app())
    assert res.status_code == 200
    assert "chassis_requests_total" in res.text


@pytest.mark.parametrize(
    ("token", "status"), [(None, 401), ("tok-forged", 401), ("tok-run", 403), ("tok-metrics", 200)]
)
async def test_a_metrics_scope_when_set_is_required(token: str | None, status: int) -> None:
    res = await _get(_auth_app(metrics_scope="metrics:read"), token)
    assert res.status_code == status, res.text


async def test_metrics_are_not_in_the_openapi_document() -> None:
    app = create_app(_config(), _ports())
    async with _client(app) as client, app.router.lifespan_context(app):
        paths: Sequence[str] = (await client.get("/openapi.json")).json()["paths"]
    assert "/metrics" not in paths
