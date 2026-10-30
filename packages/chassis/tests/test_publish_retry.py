"""One publish retry for the Dapr and the Kafka adapter (review R4-19): three attempts, waits of
0.1 s then 0.5 s, the same `PublishFailed` text as before. No broker, no daprd, no real sleep.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from aiokafka.errors import KafkaError
from chassis.adapters import publish_retry
from chassis.adapters.dapr import DaprEvents
from chassis.adapters.kafka import KafkaEvents
from chassis.adapters.kafka import events as kafka_events
from chassis.ports.events import EventPort, PublishFailed
from chassis_contracts.events import make_event

TOPIC = "agents.task.v1"
Adapter = tuple[EventPort, Callable[[], int]]


def _dapr(failures: int) -> Adapter:
    """daprd answers 503 `failures` times, then 204."""
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503 if len(seen) <= failures else 204)

    port = DaprEvents(
        "http://127.0.0.1:3500",
        api_token="t",  # pragma: allowlist secret (a test value)
        app_token="a",  # pragma: allowlist secret (a test value)
        transport=httpx.MockTransport(answer),
    )
    return port, lambda: len(seen)


def _kafka(failures: int, monkeypatch: pytest.MonkeyPatch) -> Adapter:
    """The broker refuses `failures` sends, then takes the record."""
    sent: list[str] = []

    class Producer:
        def __init__(self, **_kwargs: Any) -> None:
            return None

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        async def send_and_wait(self, topic: str, **_kwargs: Any) -> None:
            sent.append(topic)
            if len(sent) <= failures:
                raise KafkaError()

    monkeypatch.setattr(kafka_events, "AIOKafkaProducer", Producer)
    return KafkaEvents("kafka:9092"), lambda: len(sent)


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(publish_retry, "sleep", sleep)
    return recorded


def _adapter(name: str, failures: int, monkeypatch: pytest.MonkeyPatch) -> Adapter:
    return _dapr(failures) if name == "dapr" else _kafka(failures, monkeypatch)


@pytest.mark.parametrize("name", ["dapr", "kafka"])
async def test_publish_makes_three_attempts_with_the_same_waits_when_the_broker_fails_twice(
    name: str, waits: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two failures then a success: three attempts, waiting 0.1 s then 0.5 s."""
    port, attempts = _adapter(name, 2, monkeypatch)

    await port.publish(TOPIC, make_event(TOPIC))

    assert attempts() == 3
    assert waits == [0.1, 0.5]
    await port.aclose()


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("dapr", f"publish to {TOPIC} failed: daprd answered 503"),
        ("kafka", f"publish to {TOPIC} failed after 3 attempts: KafkaError"),
    ],
)
async def test_publish_raises_the_same_publish_failed_when_every_attempt_fails(
    name: str, message: str, waits: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three failures raise `PublishFailed` with each adapter's own text and no cause."""
    port, attempts = _adapter(name, 3, monkeypatch)

    with pytest.raises(PublishFailed) as caught:
        await port.publish(TOPIC, make_event(TOPIC))

    assert str(caught.value) == message
    assert caught.value.__cause__ is None
    assert attempts() == 3 and waits == [0.1, 0.5]
    await port.aclose()


def test_publish_backoff_is_defined_once_when_both_adapters_use_it() -> None:
    """Both adapters default to the one `PUBLISH_BACKOFF_S` of `publish_retry`."""
    defaults = {
        cls.__name__: inspect.signature(cls).parameters["publish_backoff_s"].default
        for cls in (DaprEvents, KafkaEvents)
    }

    assert publish_retry.PUBLISH_BACKOFF_S == (0.1, 0.5)
    assert all(d is publish_retry.PUBLISH_BACKOFF_S for d in defaults.values()), defaults
