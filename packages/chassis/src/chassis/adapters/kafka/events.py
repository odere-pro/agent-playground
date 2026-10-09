"""`KafkaEvents`: `EventPort` over Kafka with aiokafka (pure Python, asyncio, no librdkafka).

The wire form is the structured JSON CloudEvent (`CloudEvent.to_wire()`): record value = those
bytes, record key = the partition key (`partitionkey`, else `idempotencykey`), header
`content-type: application/cloudevents+json`. Delivery is at least once: the consumer commits
only after the handler returns or after the dead-letter publish. Retries happen in place, so
order per key holds; after `max_attempts` deliveries the event goes to `<topic>.dlq`.

The code is cut into marked sections (CloudEvents, retries, dead-letter topic) so the PoC-4 Dapr
comparison can count the lines each concern costs here.

Topics: the PoC-4 Compose broker creates them (`auto.create.topics.enable=true`, suggested: 3
partitions); the PoC-5 kind broker does not, `seed.sh kafka` does.

SASL (PoC-5, H11): `KAFKA_SECURITY_PROTOCOL=SASL_PLAINTEXT` with a SCRAM user from
`KAFKA_SASL_USERNAME` and `KAFKA_SASL_PASSWORD_FILE` (a mounted Secret file; or
`KAFKA_SASL_PASSWORD`). The password is never logged and never in `repr`. TLS, ACLs per service,
and the production broker are 020 X-8.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaError
from pydantic import ValidationError

from chassis.ports.events import (
    CONTENT_TYPE,
    DLQ_SUFFIX,
    CloudEvent,
    EventHandler,
    PublishFailed,
)

log = logging.getLogger(__name__)

PUBLISH_BACKOFF_S = (0.1, 0.5)
"""suggested: 3 publish attempts, waiting 0.1 s then 0.5 s between them."""
DELIVERY_BACKOFF_S = (0.2, 1.0)
"""suggested: the wait before the 2nd and later deliveries of a failing event."""
METADATA_MAX_AGE_MS = 1_000
"""suggested: a consumer sees a topic the broker just created within a second."""

PROTOCOL_VAR: Final = "KAFKA_SECURITY_PROTOCOL"
MECHANISM_VAR: Final = "KAFKA_SASL_MECHANISM"
USERNAME_VAR: Final = "KAFKA_SASL_USERNAME"
PASSWORD_VAR: Final = "KAFKA_SASL_PASSWORD"
PASSWORD_FILE_VAR: Final = "KAFKA_SASL_PASSWORD_FILE"
SASL_MECHANISMS: Final = ("SCRAM-SHA-512", "SCRAM-SHA-256")
# No `ssl_context` is passed yet, so a TLS protocol would fail late inside aiokafka. TLS is 020 X-8.
PROTOCOLS: Final = ("PLAINTEXT", "SASL_PLAINTEXT")
"""The first is the default (suggested)."""


# --- CloudEvents wire form ------------------------------------------------------------------


def _encode(event: CloudEvent) -> tuple[bytes | None, bytes, list[tuple[str, bytes]]]:
    """Record key, value, and headers for one event."""
    key = event.partitionkey or event.idempotencykey
    return (
        key.encode() if key else None,
        event.to_wire(),
        [("content-type", CONTENT_TYPE.encode())],
    )


def _decode(value: bytes | None) -> CloudEvent | None:
    """The event in a record value, or `None` when it is not a valid CloudEvent."""
    if value is None:
        return None
    try:
        return CloudEvent.model_validate_json(value)
    except ValidationError:
        return None


# --- end CloudEvents wire form --------------------------------------------------------------


# --- SASL settings --------------------------------------------------------------------------


@dataclass(frozen=True)
class SaslCredentials:
    """A SCRAM user. The password is left out of `repr`, so the object is safe to log."""

    mechanism: str
    username: str
    password: str = field(repr=False)


def _password_from_env() -> str:
    """`KAFKA_SASL_PASSWORD_FILE` (the file's text, one trailing newline dropped) or
    `KAFKA_SASL_PASSWORD`. Exactly one must be set."""
    path = os.environ.get(PASSWORD_FILE_VAR)
    value = os.environ.get(PASSWORD_VAR)
    if path and value:
        raise ValueError(f"events: kafka takes one of {PASSWORD_FILE_VAR} or {PASSWORD_VAR}")
    if path:
        try:
            value = Path(path).read_text().removesuffix("\n")
        except OSError as exc:
            raise LookupError(f"events: kafka cannot read {PASSWORD_FILE_VAR}") from exc
        if not value:
            raise LookupError(f"events: kafka {PASSWORD_FILE_VAR} names an empty file")
    if not value:
        raise LookupError(f"events: kafka needs {PASSWORD_FILE_VAR} or {PASSWORD_VAR} for SASL")
    return value


def sasl_from_env(protocol: str) -> SaslCredentials | None:
    """The SCRAM user for a `SASL_*` protocol, else `None`. A `SASL_*` protocol with no user or
    no password raises `LookupError`; SASL variables with any other protocol raise `ValueError`,
    so a credential is never set and then silently unused. A protocol outside `PROTOCOLS`
    (`SSL`, `SASL_SSL`) raises `ValueError` until 020 X-8 adds TLS."""
    if protocol not in PROTOCOLS:
        raise ValueError(f"events: kafka {PROTOCOL_VAR} must be one of {PROTOCOLS}; TLS is 020 X-8")
    if not protocol.startswith("SASL_"):
        named = [v for v in (USERNAME_VAR, PASSWORD_VAR, PASSWORD_FILE_VAR) if os.environ.get(v)]
        if named:
            raise ValueError(f"events: kafka {', '.join(named)} set but {PROTOCOL_VAR}={protocol}")
        return None
    mechanism = os.environ.get(MECHANISM_VAR) or SASL_MECHANISMS[0]
    if mechanism not in SASL_MECHANISMS:
        raise ValueError(f"events: kafka {MECHANISM_VAR} must be one of {SASL_MECHANISMS}")
    username = os.environ.get(USERNAME_VAR)
    if not username:
        raise LookupError(f"events: kafka needs {USERNAME_VAR} for {protocol}")
    return SaslCredentials(mechanism, username, _password_from_env())


# --- end SASL settings ----------------------------------------------------------------------


class _KafkaSubscription:
    def __init__(
        self, owner: KafkaEvents, consumer: AIOKafkaConsumer, task: asyncio.Task[None]
    ) -> None:
        self._owner = owner
        self._consumer = consumer
        self._task = task

    async def close(self) -> None:
        self._owner._subs.discard(self)
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        await self._consumer.stop()


class KafkaEvents:
    """`EventPort` on Kafka. Build it with `from_env()`; the producer starts on first publish."""

    def __init__(
        self,
        bootstrap_servers: str,
        *,
        security_protocol: str = "PLAINTEXT",
        sasl: SaslCredentials | None = None,
        publish_backoff_s: tuple[float, ...] = PUBLISH_BACKOFF_S,
        delivery_backoff_s: tuple[float, ...] = DELIVERY_BACKOFF_S,
        request_timeout_ms: int = 10_000,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.security_protocol = security_protocol
        self.sasl = sasl
        self.publish_backoff_s = publish_backoff_s
        self.delivery_backoff_s = delivery_backoff_s
        self.request_timeout_ms = request_timeout_ms
        self._producer: AIOKafkaProducer | None = None
        self._producer_lock = asyncio.Lock()
        self._subs: set[_KafkaSubscription] = set()

    def __repr__(self) -> str:
        user = self.sasl.username if self.sasl else None
        return (
            f"KafkaEvents(bootstrap_servers={self.bootstrap_servers!r}, "
            f"security_protocol={self.security_protocol!r}, sasl_username={user!r})"
        )

    @classmethod
    def from_env(cls) -> KafkaEvents:
        """`KAFKA_BOOTSTRAP_SERVERS` (required), `KAFKA_SECURITY_PROTOCOL` (default PLAINTEXT),
        and for `SASL_*` the SCRAM user (`sasl_from_env`)."""
        servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
        if not servers:
            raise LookupError("KAFKA_BOOTSTRAP_SERVERS is not set")
        protocol = os.environ.get(PROTOCOL_VAR) or "PLAINTEXT"
        return cls(servers, security_protocol=protocol, sasl=sasl_from_env(protocol))

    def _client_options(self) -> dict[str, Any]:
        """What the producer and every consumer share: the address, the timeout, and SASL."""
        options: dict[str, Any] = {
            "bootstrap_servers": self.bootstrap_servers,
            "security_protocol": self.security_protocol,
            "request_timeout_ms": self.request_timeout_ms,
        }
        if self.sasl is not None:
            options |= {
                "sasl_mechanism": self.sasl.mechanism,
                "sasl_plain_username": self.sasl.username,
                "sasl_plain_password": self.sasl.password,
            }
        return options

    async def _started_producer(self) -> AIOKafkaProducer:
        async with self._producer_lock:
            if self._producer is None:
                producer = AIOKafkaProducer(
                    acks="all",
                    enable_idempotence=True,
                    linger_ms=5,  # suggested
                    **self._client_options(),
                )
                try:
                    await producer.start()
                except BaseException:
                    await producer.stop()
                    raise
                self._producer = producer
            return self._producer

    # --- retries: publish -------------------------------------------------------------------

    async def publish(self, topic: str, event: CloudEvent) -> None:
        """Return once every in-sync replica has the event (`acks=all`). Raise `PublishFailed`
        after `len(publish_backoff_s) + 1` attempts."""
        key, value, headers = _encode(event)
        attempts = len(self.publish_backoff_s) + 1
        for attempt in range(1, attempts + 1):
            try:
                producer = await self._started_producer()
                await producer.send_and_wait(topic, value=value, key=key, headers=headers)
                return
            except (KafkaError, OSError) as exc:
                log.warning(
                    "kafka publish failed",
                    extra={
                        "topic": topic,
                        "event_id": event.id,
                        "attempt": attempt,
                        "error": type(exc).__name__,
                    },
                )
                if attempt == attempts:
                    raise PublishFailed(
                        f"publish to {topic} failed after {attempts} attempts: {type(exc).__name__}"
                    ) from None
                await asyncio.sleep(self.publish_backoff_s[attempt - 1])

    # --- end retries: publish ---------------------------------------------------------------

    async def subscribe(
        self, topic: str, handler: EventHandler, *, group: str, max_attempts: int = 3
    ) -> _KafkaSubscription:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        consumer = AIOKafkaConsumer(
            topic,
            group_id=group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",  # suggested
            metadata_max_age_ms=METADATA_MAX_AGE_MS,
            **self._client_options(),
        )
        await consumer.start()
        task = asyncio.create_task(self._consume(consumer, topic, handler, max_attempts))
        sub = _KafkaSubscription(self, consumer, task)
        self._subs.add(sub)
        return sub

    async def aclose(self) -> None:
        for sub in list(self._subs):
            await sub.close()
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def _consume(
        self, consumer: AIOKafkaConsumer, topic: str, handler: EventHandler, max_attempts: int
    ) -> None:
        async for record in consumer:
            event = _decode(record.value)
            if event is None:
                log.warning("dropped a record that is not a CloudEvent", extra={"topic": topic})
            else:
                await self._deliver(topic, event, handler, max_attempts)
            try:
                await consumer.commit()
            except KafkaError as exc:  # a rebalance: the event comes again, at least once
                log.warning("kafka commit failed", extra={"error": type(exc).__name__})

    # --- retries: delivery ------------------------------------------------------------------

    async def _deliver(
        self, topic: str, event: CloudEvent, handler: EventHandler, max_attempts: int
    ) -> None:
        for attempt in range(1, max_attempts + 1):
            try:
                await handler(event)
                return
            except Exception as exc:
                if attempt == max_attempts:
                    await self._dead_letter(topic, event, exc, max_attempts)
                    return
                backoff = self.delivery_backoff_s
                await asyncio.sleep(backoff[min(attempt, len(backoff)) - 1])

    # --- end retries: delivery --------------------------------------------------------------

    # --- dead-letter topic ------------------------------------------------------------------

    async def _dead_letter(
        self, topic: str, event: CloudEvent, exc: Exception, attempts: int
    ) -> None:
        """Publish to `<topic>.dlq` with the exception class name only, never its message."""
        dead = event.model_copy(
            update={"deadletterreason": type(exc).__name__, "deadletterattempts": attempts}
        )
        log.warning(
            "event dead-lettered",
            extra={"topic": topic, "event_id": event.id, "reason": type(exc).__name__},
        )
        await self.publish(topic + DLQ_SUFFIX, dead)

    # --- end dead-letter topic --------------------------------------------------------------
