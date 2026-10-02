"""`DaprEvents`: `EventPort` over the daprd sidecar, with httpx (no Dapr SDK).

Publish: `POST {DAPR_HTTP_ENDPOINT}/v1.0/publish/{DAPR_PUBSUB_NAME}/{topic}` with the structured
CloudEvent as is (`Content-Type: application/cloudevents+json`, so daprd does not wrap it again),
`?metadata.partitionKey=<key>`, and the header `dapr-api-token`. A 204 means the broker has it.

Subscribe is programmatic: the adapter keeps a route table and serves it with `inbound_routes()`,
which the chassis mounts on the proxy app (localhost only, `chassis.server.proxy_app`). daprd reads
`GET /dapr/subscribe` once, when it starts, and calls `POST /dapr/events/{topic}` per event with
the header `dapr-api-token: $APP_API_TOKEN`; any other token is 401. The answer is `SUCCESS` when
the handler returns, `RETRY` when it raises, and `DROP` for a body that is not a CloudEvent or a
topic with no subscription. So a subscription made after daprd started is not seen until daprd
restarts: subscribe in the lifespan, before the proxy listener is ready.

Retries and the dead-letter topic are Dapr's: the subscription names `deadLetterTopic`, and the
resiliency policy (`deploy/compose/dapr/resiliency.yaml`) sets `maxRetries`. `max_attempts` is
checked against `DAPR_MAX_RETRIES` (suggested; optional) and a mismatch is logged, not enforced.
The consumer group is daprd's app id, so one app has one group per topic.

The code is cut into the same marked sections as `chassis.adapters.kafka.events` (CloudEvents,
retries, dead-letter topic) so the PoC-4 Dapr comparison can count the lines each concern costs.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from chassis.ports.events import (
    CONTENT_TYPE,
    DLQ_SUFFIX,
    CloudEvent,
    EventHandler,
    PublishFailed,
)

log = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "http://127.0.0.1:3500"
DEFAULT_PUBSUB = "pubsub"
PUBLISH_BACKOFF_S = (0.1, 0.5)
"""suggested: 3 publish attempts, waiting 0.1 s then 0.5 s between them, as the Kafka adapter."""
TOKEN_HEADER = "dapr-api-token"
"""daprd's header both ways: the chassis sends `DAPR_API_TOKEN`, daprd sends `APP_API_TOKEN`."""
EVENTS_PATH = "/dapr/events/"


# --- CloudEvents wire form ------------------------------------------------------------------

DAPR_ATTRIBUTES = frozenset({"pubsubname", "topic", "traceid", "tracestate"})
"""Attributes daprd adds to a passed-through CloudEvent on delivery. They are not ours."""
EMPTY_TRACEPARENT = "00-00000000000000000000000000000000-0000000000000000-00"
"""What daprd writes into `traceparent` when the publish carried no trace context."""


def _headers(event: CloudEvent, api_token: str) -> dict[str, str]:
    """daprd overwrites the event's `traceparent` with the publish request's trace context, so
    the event's own goes in the W3C header too."""
    headers = {"content-type": CONTENT_TYPE, TOKEN_HEADER: api_token}
    if event.traceparent:
        headers["traceparent"] = event.traceparent
    return headers


def _decode(body: bytes) -> CloudEvent | None:
    """The event daprd delivered, or `None` when the body is not a valid CloudEvent."""
    try:
        raw = json.loads(body)
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("traceparent") == EMPTY_TRACEPARENT:
        del raw["traceparent"]
    try:
        return CloudEvent.model_validate(
            {name: value for name, value in raw.items() if name not in DAPR_ATTRIBUTES}
        )
    except ValidationError:
        return None


# --- end CloudEvents wire form --------------------------------------------------------------


@dataclass
class _Entry:
    topic: str
    group: str
    handler: EventHandler


class _DaprSubscription:
    def __init__(self, owner: DaprEvents, entry: _Entry) -> None:
        self._owner = owner
        self._entry = entry

    async def close(self) -> None:
        if self._owner._routes.get(self._entry.topic) is self._entry:
            del self._owner._routes[self._entry.topic]


class DaprEvents:
    """`EventPort` on daprd. Build it with `from_env()`; mount `inbound_routes()` on the proxy."""

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        *,
        pubsub: str = DEFAULT_PUBSUB,
        api_token: str,
        app_token: str,
        publish_backoff_s: tuple[float, ...] = PUBLISH_BACKOFF_S,
        dapr_max_retries: int | None = None,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.pubsub = pubsub
        self.publish_backoff_s = publish_backoff_s
        self.dapr_max_retries = dapr_max_retries
        self._api_token = api_token
        self._app_token = app_token
        self._routes: dict[str, _Entry] = {}
        self._client = httpx.AsyncClient(timeout=timeout_s, transport=transport, trust_env=False)

    def __repr__(self) -> str:
        return f"DaprEvents(endpoint={self.endpoint!r}, pubsub={self.pubsub!r})"

    @classmethod
    def from_env(cls) -> DaprEvents:
        """`DAPR_HTTP_ENDPOINT` (default `http://127.0.0.1:3500`), `DAPR_PUBSUB_NAME` (default
        `pubsub`), `DAPR_API_TOKEN` and `APP_API_TOKEN` (both required), `DAPR_MAX_RETRIES`
        (optional, the resiliency policy's `maxRetries`)."""
        tokens = {}
        for name in ("DAPR_API_TOKEN", "APP_API_TOKEN"):
            value = os.environ.get(name)
            if not value:
                raise LookupError(f"{name} is not set")
            tokens[name] = value
        retries = os.environ.get("DAPR_MAX_RETRIES")
        return cls(
            os.environ.get("DAPR_HTTP_ENDPOINT") or DEFAULT_ENDPOINT,
            pubsub=os.environ.get("DAPR_PUBSUB_NAME") or DEFAULT_PUBSUB,
            api_token=tokens["DAPR_API_TOKEN"],
            app_token=tokens["APP_API_TOKEN"],
            dapr_max_retries=int(retries) if retries else None,
        )

    # --- retries: publish -------------------------------------------------------------------

    async def publish(self, topic: str, event: CloudEvent) -> None:
        """Return on daprd's 204. Retry a transport error or a 5xx; raise `PublishFailed` after
        `len(publish_backoff_s) + 1` attempts, or at once on any other answer."""
        url = f"{self.endpoint}/v1.0/publish/{quote(self.pubsub, safe='')}/{quote(topic, safe='')}"
        key = event.partitionkey or event.idempotencykey
        params = {"metadata.partitionKey": key} if key else None
        headers = _headers(event, self._api_token)
        body = event.to_wire()
        attempts = len(self.publish_backoff_s) + 1
        why = ""
        for attempt in range(1, attempts + 1):
            try:
                answer = await self._client.post(url, content=body, params=params, headers=headers)
            except httpx.TransportError as exc:
                why = type(exc).__name__
            else:
                if answer.status_code in (200, 204):
                    return
                why = f"daprd answered {answer.status_code}"
                if answer.status_code < 500:
                    break
            if attempt < attempts:
                await asyncio.sleep(self.publish_backoff_s[attempt - 1])
        raise PublishFailed(f"publish to {topic} failed: {why}")

    # --- end retries: publish ---------------------------------------------------------------

    async def subscribe(
        self, topic: str, handler: EventHandler, *, group: str, max_attempts: int = 3
    ) -> _DaprSubscription:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        held = self._routes.get(topic)
        if held is not None and held.group != group:
            raise ValueError(
                f"dapr: one group per topic per app (the group is daprd's app id); "
                f"{topic} is held by {held.group}"
            )
        if self.dapr_max_retries is not None and max_attempts != self.dapr_max_retries + 1:
            log.warning(
                "max_attempts does not match the dapr resiliency policy",
                extra={"topic": topic, "max_attempts": max_attempts},
            )
        entry = _Entry(topic, group, handler)
        self._routes[topic] = entry
        return _DaprSubscription(self, entry)

    async def aclose(self) -> None:
        self._routes.clear()
        await self._client.aclose()

    def inbound_routes(self) -> list[Route]:
        """The routes daprd calls back. Mount them on the proxy app only, never the public one."""
        return [
            Route("/dapr/subscribe", self._list, methods=["GET"]),
            Route(EVENTS_PATH + "{topic:path}", self._receive, methods=["POST"]),
        ]

    def _authorized(self, request: Request) -> bool:
        sent = request.headers.get(TOKEN_HEADER, "")
        return hmac.compare_digest(sent.encode(), self._app_token.encode())

    # --- dead-letter topic ------------------------------------------------------------------

    async def _list(self, request: Request) -> Response:
        """The subscription list daprd reads at start; each topic names `<topic>.dlq`."""
        if not self._authorized(request):
            return Response(status_code=401)
        listed: list[dict[str, Any]] = []
        for topic in self._routes:
            item: dict[str, Any] = {
                "pubsubname": self.pubsub,
                "topic": topic,
                "route": EVENTS_PATH + topic,
            }
            if not topic.endswith(DLQ_SUFFIX):
                item["deadLetterTopic"] = topic + DLQ_SUFFIX
            listed.append(item)
        return JSONResponse(listed)

    # --- end dead-letter topic --------------------------------------------------------------

    # --- retries: delivery ------------------------------------------------------------------

    async def _receive(self, request: Request) -> Response:
        """One delivery: SUCCESS, RETRY (daprd's resiliency policy redelivers), or DROP."""
        if not self._authorized(request):
            return Response(status_code=401)
        topic = request.path_params["topic"]
        entry = self._routes.get(topic)
        event = _decode(await request.body())
        if entry is None or event is None:
            log.warning(
                "dropped a delivery",
                extra={"topic": topic, "reason": "no subscription" if entry is None else "body"},
            )
            return JSONResponse({"status": "DROP"})
        try:
            await entry.handler(event)
        except Exception as exc:
            log.warning(
                "event handler failed, asking daprd to retry",
                extra={"topic": topic, "event_id": event.id, "reason": type(exc).__name__},
            )
            return JSONResponse({"status": "RETRY"})
        return JSONResponse({"status": "SUCCESS"})

    # --- end retries: delivery --------------------------------------------------------------
