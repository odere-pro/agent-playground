"""`DaprEvents` offline: the routes daprd calls back, the app token, SUCCESS, RETRY, and DROP, the
publish request, and the routes mounted on the proxy app only. No daprd: the inbound side runs over
`httpx.ASGITransport`, the publish side over `httpx.MockTransport`. The real daprd binding is
`tests/integration/test_dapr_events_contract.py`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from chassis.adapters.dapr import DaprEvents
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryBus, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.ports.events import CONTENT_TYPE, CloudEvent, EventPort, PublishFailed
from chassis.profiles import AdapterSpec, build_ports
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from fastapi import FastAPI
from starlette.applications import Starlette

API_TOKEN = "dapr-api-token-for-tests"
APP_TOKEN = "app-api-token-for-tests"
TOPIC = "agents.task.completed.v1"
CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}


def public_app(events: EventPort) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        events=events,
    )
    return create_app(ChassisConfig.model_validate(CONFIG), ports)


def event(**extra: Any) -> CloudEvent:
    return CloudEvent(
        id=uuid.uuid4().hex,
        source="/agents/echo",
        type=TOPIC,
        time=datetime.now(UTC),
        data={"n": 1},
        **extra,
    )


def dapr(transport: httpx.AsyncBaseTransport | None = None) -> DaprEvents:
    return DaprEvents(
        "http://127.0.0.1:3500",
        api_token=API_TOKEN,
        app_token=APP_TOKEN,
        publish_backoff_s=(0.0, 0.0),
        transport=transport,
    )


@pytest.fixture
async def port() -> AsyncIterator[DaprEvents]:
    adapter = dapr()
    yield adapter
    await adapter.aclose()


def inbound_client(adapter: DaprEvents, token: str | None = APP_TOKEN) -> httpx.AsyncClient:
    app = Starlette(routes=adapter.inbound_routes())
    headers = {} if token is None else {"dapr-api-token": token}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://app", headers=headers
    )


async def deliver(client: httpx.AsyncClient, topic: str, body: bytes) -> httpx.Response:
    return await client.post(
        f"/dapr/events/{topic}", content=body, headers={"content-type": CONTENT_TYPE}
    )


# --- from_env -------------------------------------------------------------------------------


def test_from_env_reads_the_endpoint_the_pubsub_and_both_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAPR_HTTP_ENDPOINT", "http://127.0.0.1:3999")
    monkeypatch.setenv("DAPR_PUBSUB_NAME", "events")
    monkeypatch.setenv("DAPR_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("APP_API_TOKEN", APP_TOKEN)
    adapter = DaprEvents.from_env()
    assert adapter.endpoint == "http://127.0.0.1:3999"
    assert adapter.pubsub == "events"
    assert API_TOKEN not in repr(adapter)


def test_from_env_defaults_the_endpoint_and_the_pubsub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAPR_HTTP_ENDPOINT", raising=False)
    monkeypatch.delenv("DAPR_PUBSUB_NAME", raising=False)
    monkeypatch.setenv("DAPR_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("APP_API_TOKEN", APP_TOKEN)
    adapter = DaprEvents.from_env()
    assert adapter.endpoint == "http://127.0.0.1:3500"
    assert adapter.pubsub == "pubsub"


@pytest.mark.parametrize("missing", ["DAPR_API_TOKEN", "APP_API_TOKEN"])
def test_from_env_needs_both_tokens(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.setenv("DAPR_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("APP_API_TOKEN", APP_TOKEN)
    monkeypatch.delenv(missing)
    with pytest.raises(LookupError, match=missing):
        DaprEvents.from_env()


def test_the_profile_builds_dapr_by_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAPR_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("APP_API_TOKEN", APP_TOKEN)
    bundle = build_ports("fake", AdapterSpec(events="dapr"))
    assert isinstance(bundle.events, DaprEvents)


# --- the subscription list daprd reads --------------------------------------------------------


async def test_dapr_subscribe_lists_each_topic_with_its_dead_letter_topic(
    port: DaprEvents,
) -> None:
    async def handler(_: CloudEvent) -> None:
        return None

    await port.subscribe(TOPIC, handler, group="echo")
    await port.subscribe(TOPIC + ".dlq", handler, group="echo")
    async with inbound_client(port) as client:
        listed = (await client.get("/dapr/subscribe")).json()
    assert listed == [
        {
            "pubsubname": "pubsub",
            "topic": TOPIC,
            "route": f"/dapr/events/{TOPIC}",
            "deadLetterTopic": TOPIC + ".dlq",
        },
        {"pubsubname": "pubsub", "topic": TOPIC + ".dlq", "route": f"/dapr/events/{TOPIC}.dlq"},
    ]


async def test_a_closed_subscription_leaves_the_list(port: DaprEvents) -> None:
    async def handler(_: CloudEvent) -> None:
        return None

    sub = await port.subscribe(TOPIC, handler, group="echo")
    await sub.close()
    async with inbound_client(port) as client:
        assert (await client.get("/dapr/subscribe")).json() == []


async def test_a_second_group_on_one_topic_is_refused(port: DaprEvents) -> None:
    async def handler(_: CloudEvent) -> None:
        return None

    await port.subscribe(TOPIC, handler, group="g1")
    with pytest.raises(ValueError, match="one group per topic"):
        await port.subscribe(TOPIC, handler, group="g2")


# --- the app token ----------------------------------------------------------------------------


@pytest.mark.parametrize("token", [None, "wrong-token"])
async def test_a_callback_without_the_app_token_is_401(port: DaprEvents, token: str | None) -> None:
    seen: list[CloudEvent] = []

    async def handler(got: CloudEvent) -> None:
        seen.append(got)

    await port.subscribe(TOPIC, handler, group="echo")
    async with inbound_client(port, token=token) as client:
        assert (await client.get("/dapr/subscribe")).status_code == 401
        assert (await deliver(client, TOPIC, event().to_wire())).status_code == 401
    assert seen == []


# --- SUCCESS, RETRY, DROP ---------------------------------------------------------------------


async def test_a_handler_that_returns_is_success(port: DaprEvents) -> None:
    seen: list[CloudEvent] = []

    async def handler(got: CloudEvent) -> None:
        seen.append(got)

    await port.subscribe(TOPIC, handler, group="echo")
    sent = event(partitionkey="k", traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01")
    # daprd adds its own attributes to a passed-through CloudEvent; they are not ours.
    body = json.loads(sent.to_wire()) | {
        "pubsubname": "pubsub",
        "topic": TOPIC,
        "traceid": "00-x",
        "tracestate": "",
    }
    async with inbound_client(port) as client:
        answer = await deliver(client, TOPIC, json.dumps(body).encode())
    assert answer.status_code == 200
    assert answer.json() == {"status": "SUCCESS"}
    assert seen == [sent]


async def test_a_handler_that_raises_is_retry_and_the_answer_holds_no_message(
    port: DaprEvents,
) -> None:
    async def handler(_: CloudEvent) -> None:
        raise RuntimeError("secret-detail-do-not-forward")

    await port.subscribe(TOPIC, handler, group="echo")
    async with inbound_client(port) as client:
        answer = await deliver(client, TOPIC, event().to_wire())
    assert answer.status_code == 200
    assert answer.json() == {"status": "RETRY"}
    assert "secret" not in answer.text


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", json.dumps({"specversion": "1.0", "id": "x"}).encode()],
    ids=["not-json", "not-an-object", "not-a-cloudevent"],
)
async def test_a_body_that_is_not_a_cloudevent_is_drop(port: DaprEvents, body: bytes) -> None:
    seen: list[CloudEvent] = []

    async def handler(got: CloudEvent) -> None:
        seen.append(got)

    await port.subscribe(TOPIC, handler, group="echo")
    async with inbound_client(port) as client:
        answer = await deliver(client, TOPIC, body)
    assert answer.json() == {"status": "DROP"}
    assert seen == []


async def test_an_event_for_a_closed_subscription_is_drop(port: DaprEvents) -> None:
    seen: list[CloudEvent] = []

    async def handler(got: CloudEvent) -> None:
        seen.append(got)

    sub = await port.subscribe(TOPIC, handler, group="echo")
    await sub.close()
    async with inbound_client(port) as client:
        answer = await deliver(client, TOPIC, event().to_wire())
    assert answer.json() == {"status": "DROP"}
    assert seen == []


# --- publish ----------------------------------------------------------------------------------


async def test_publish_posts_the_structured_cloudevent_with_the_api_token() -> None:
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    adapter = dapr(httpx.MockTransport(answer))
    sent = event(partitionkey="idem-1")
    await adapter.publish(TOPIC, sent)
    await adapter.aclose()
    [request] = requests
    assert request.method == "POST"
    assert request.url.path == f"/v1.0/publish/pubsub/{TOPIC}"
    assert request.url.params["metadata.partitionKey"] == "idem-1"
    assert request.headers["content-type"] == CONTENT_TYPE
    assert request.headers["dapr-api-token"] == API_TOKEN
    assert request.content == sent.to_wire()


async def test_publish_retries_a_server_error_then_succeeds() -> None:
    codes = iter([500, 503, 204])

    def answer(_: httpx.Request) -> httpx.Response:
        return httpx.Response(next(codes))

    adapter = dapr(httpx.MockTransport(answer))
    await adapter.publish(TOPIC, event())
    await adapter.aclose()


async def test_publish_raises_publish_failed_after_three_attempts_without_the_token() -> None:
    calls: list[int] = []

    def answer(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("daprd is down")

    adapter = dapr(httpx.MockTransport(answer))
    with pytest.raises(PublishFailed) as caught:
        await adapter.publish(TOPIC, event())
    await adapter.aclose()
    assert len(calls) == 3
    assert API_TOKEN not in str(caught.value)


async def test_a_refused_publish_is_not_retried() -> None:
    calls: list[int] = []

    def answer(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": "invalid api token"})

    adapter = dapr(httpx.MockTransport(answer))
    with pytest.raises(PublishFailed, match="401"):
        await adapter.publish(TOPIC, event())
    await adapter.aclose()
    assert len(calls) == 1


# --- mounted on the proxy app only ------------------------------------------------------------


async def test_the_proxy_app_serves_the_inbound_routes_and_the_public_app_does_not() -> None:
    adapter = dapr()
    public = public_app(adapter)
    proxy = create_proxy_app(public)

    async def handler(_: CloudEvent) -> None:
        return None

    await adapter.subscribe(TOPIC, handler, group="echo")
    async with public.router.lifespan_context(public):
        headers = {"dapr-api-token": APP_TOKEN}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=proxy), base_url="http://proxy", headers=headers
        ) as client:
            listed = (await client.get("/dapr/subscribe")).json()
            answer = await deliver(client, TOPIC, event().to_wire())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=public), base_url="http://public", headers=headers
        ) as client:
            assert (await client.get("/dapr/subscribe")).status_code == 404
    assert [entry["topic"] for entry in listed] == [TOPIC]
    assert answer.json() == {"status": "SUCCESS"}


async def test_the_proxy_app_has_no_dapr_routes_when_the_port_has_none() -> None:
    public = public_app(InMemoryBus())
    proxy = create_proxy_app(public)
    async with (
        public.router.lifespan_context(public),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=proxy), base_url="http://proxy"
        ) as client,
    ):
        assert (await client.get("/dapr/subscribe")).status_code == 404


async def test_publish_sends_the_events_traceparent_as_the_w3c_header() -> None:
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    adapter = dapr(httpx.MockTransport(answer))
    traceparent = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    await adapter.publish(TOPIC, event(traceparent=traceparent))
    await adapter.publish(TOPIC, event())
    await adapter.aclose()
    assert requests[0].headers["traceparent"] == traceparent
    assert "traceparent" not in requests[1].headers


async def test_daprds_empty_traceparent_is_not_passed_on(port: DaprEvents) -> None:
    seen: list[CloudEvent] = []

    async def handler(got: CloudEvent) -> None:
        seen.append(got)

    await port.subscribe(TOPIC, handler, group="echo")
    body = json.loads(event().to_wire()) | {
        "traceparent": "00-00000000000000000000000000000000-0000000000000000-00"
    }
    async with inbound_client(port) as client:
        await deliver(client, TOPIC, json.dumps(body).encode())
    assert seen[0].traceparent is None
