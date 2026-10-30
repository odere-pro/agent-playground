"""`POST /v1/feedback` (PoC-7): a user's score for one call, written onto that call's trace through
`FeedbackPort` (`spec.adapters.feedback`; `LangfuseFeedback` in the cloud profile).

**On or off.** `feedback_on(config)`: `spec.feedback.enabled` and an adapter other than `none`.
Then `create_app` mounts the route and the lifespan adds a `FeedbackLookup` to
`pipeline.on_finished`. Off (the `fake` and `local` default, `adapters.feedback: none`), there is
no route (404) and no lookup write, so the public OpenAPI is unchanged. With an injected
`NoFeedback` the route answers 501 `feedback_not_configured`.

**Request id to trace id.** The engine exposes nothing for this: the chassis owns both ids. After
every run whose engine stream ended (`Run.done`), `FeedbackLookup` writes
`chassis:feedback:v1:req:<agent>:<sha256(request_id)>` -> `{"trace_id", "agent"}` into `StatePort`
with `ttl = spec.feedback.lookup_ttl_s`. `trace_id` is `trace_id_hex(request.trace_id)`, the W3C
trace id the chassis spans and the workload's `traceparent` carry, which Langfuse uses as its
trace id. The write is awaited inside `on_finished`, so in complete mode it is there before the
answer goes out; a state failure is counted (`chassis.feedback.lookup_failed`) and logged, never
sent to the client. Shared state, no module global: any replica can take the feedback.

**The call.** Body `{request_id, score, label?, comment?, feedback_id?}`, strict, no other field.
`subject` is `request.state.principal.subject` when an auth middleware set one, never a body
field. `feedback_id` defaults to `default_feedback_id(request_id, subject)`, so a repeat from the
same caller is the same record. The first time a `feedback_id` is seen, its time and request id
go to `chassis:feedback:v1:id:<agent>:<sha256(feedback_id)>` (`set_if_absent`, same TTL); a
repeat sends that first time again (Langfuse updates a score only for the same id and
timestamp), and a `feedback_id` already used for another request is 409 `feedback_id_conflict`.

| Answer | When |
| ------ | ---- |
| 202 `{feedback_id, trace_id}` | stored |
| 404 `unknown_request` | no lookup: unknown, unfinished, expired, or another agent's |
| 409 `feedback_id_conflict` | the `feedback_id` belongs to another request |
| 422 | the body fails validation (FastAPI's shape) |
| 501 `feedback_not_configured` | `NoFeedback` |
| 502 `feedback_rejected` | the store refused it (its 4xx) |
| 503 `feedback_unavailable`, `state_unavailable`, `not_ready` | retryable, `Retry-After: 1` |

Errors are `{"detail": {"code", "message"}}` with a fixed message, as `/v1/run`'s refusals.

**Telemetry.** One `chassis.feedback` span (`request_id`, `feedback_id`, `trace_id`), the counter
`chassis.feedback{label}` per stored feedback (`label` is `none` when unset), and
`chassis.feedback_failed{code}` otherwise. The comment is never logged or put on a span.

**The comment** goes to the store as written: it is content the user gave on purpose to be read
with the trace. The event carries it through `chassis.redaction.redact`.

**The event** (`spec.feedback.events: true`, suggested off): `agents.feedback.received.v1` on
`EventPort` after a 202, with payload `FeedbackReceived` (`schemas/feedback.v0.json`). Like the
result events (`server.results`) it is published in a background task: a broker failure is
counted and logged and never changes the 202; shutdown waits for pending publishes, bounded. Its
`id` is a uuid5 of the feedback's content, so a repeat of the same feedback has the same id and a
changed score a new one.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi import Request as HTTPRequest
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import State

from chassis.core.trace import trace_id_hex
from chassis.ports.auth import ANY_SCOPE
from chassis.ports.bundle import PortBundle
from chassis.ports.events import CloudEvent, PublishFailed
from chassis.ports.feedback import (
    COMMENT_MAX,
    Feedback,
    FeedbackError,
    FeedbackPort,
    FeedbackReceived,
)
from chassis.ports.state import StateUnavailable
from chassis.profiles import merge_adapters
from chassis.redaction import redact
from chassis.server.config import ChassisConfig
from chassis.server.pipeline import Run
from chassis.server.results import PUBLISH_ABANDONED, PUBLISH_FAILED, PUBLISHED, SHUTDOWN_WAIT_S

__all__ = [
    "COUNTER",
    "DATASCHEMA",
    "FAILED",
    "FEEDBACK_PATH",
    "FEEDBACK_RECEIVED",
    "LOOKUP_FAILED",
    "SPAN",
    "FeedbackAccepted",
    "FeedbackEvents",
    "FeedbackLookup",
    "FeedbackRequest",
    "bind_score_name",
    "default_feedback_id",
    "feedback_event",
    "feedback_on",
    "id_key",
    "lookup_key",
    "mount_feedback",
]

FEEDBACK_PATH = "/v1/feedback"
FEEDBACK_RECEIVED = "agents.feedback.received.v1"
"""suggested: the event type and topic. Not in the epic's event catalog yet."""
DATASCHEMA = "feedback.v0.json"
SPAN = "chassis.feedback"
COUNTER = "chassis.feedback"
"""suggested: a stored feedback, labeled `label`."""
FAILED = "chassis.feedback_failed"
"""suggested: a feedback not stored, labeled `code`."""
LOOKUP_FAILED = "chassis.feedback.lookup_failed"
"""suggested: a finished run whose lookup could not be written."""
KEY_PREFIX = "chassis:feedback:v1:"
FEEDBACK_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,128}$"
"""suggested: what a client-chosen `feedback_id` may hold."""
REQUEST_ID_MAX = 256
"""suggested."""
RETRY_AFTER_S = "1"
"""suggested: the `Retry-After` of a 503."""
EVENT_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:chassis:agents.feedback")
"""suggested: the uuid5 namespace of feedback event ids."""

MESSAGES: dict[str, str] = {
    "unknown_request": "no finished run with this request_id; it may have expired",
    "feedback_id_conflict": "this feedback_id was already used for another request",
    "feedback_not_configured": "feedback is not configured for this agent",
    "feedback_rejected": "the feedback store refused the feedback",
    "feedback_unavailable": "the feedback store cannot be reached; retry later",
    "state_unavailable": "the chassis cannot reach its state store; retry later",
    "not_ready": "the chassis is not ready; retry later",
}
"""The fixed texts; the store's own message stays in the log."""
STATUS: dict[str, int] = {
    "unknown_request": 404,
    "feedback_id_conflict": 409,
    "feedback_not_configured": 501,
    "feedback_rejected": 502,
    "feedback_unavailable": 503,
    "state_unavailable": 503,
    "not_ready": 503,
}


class FeedbackRequest(BaseModel):
    """The `/v1/feedback` body. `subject` and `trace_id` are not fields: the chassis sets them."""

    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=REQUEST_ID_MAX)
    score: float = Field(ge=0, le=1)
    """0 is bad, 1 is good (suggested); a thumbs down is 0, a thumbs up is 1."""
    label: str | None = Field(default=None, min_length=1, max_length=64)
    comment: str | None = Field(default=None, max_length=COMMENT_MAX)
    feedback_id: str | None = Field(default=None, pattern=FEEDBACK_ID_PATTERN)
    """Default: `default_feedback_id(request_id, subject)`."""


class FeedbackAccepted(BaseModel):
    """202."""

    feedback_id: str
    trace_id: str


class _CodeMessage(BaseModel):
    code: str
    message: str


class FeedbackRefused(BaseModel):
    """404, 409, 501, 502, 503."""

    detail: _CodeMessage


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def lookup_key(agent: str, request_id: str) -> str:
    """The `StatePort` key of a finished run's lookup: scoped by agent, the id hashed."""
    return f"{KEY_PREFIX}req:{agent}:{_digest(request_id)}"


def id_key(agent: str, feedback_id: str) -> str:
    """The `StatePort` key that keeps a `feedback_id`'s first time and request id."""
    return f"{KEY_PREFIX}id:{agent}:{_digest(feedback_id)}"


def default_feedback_id(request_id: str, subject: str | None) -> str:
    """32 hex of sha256(request_id, subject): one record per caller and call."""
    return _digest(f"{request_id}\x00{subject or ''}")[:32]


def feedback_on(config: ChassisConfig) -> bool:
    """`spec.feedback.enabled` and `spec.adapters.feedback` (merged over the profile) not
    `none`."""
    adapters = merge_adapters(config.profile, config.spec.adapters)
    return config.spec.feedback.enabled and adapters.feedback not in (None, "none")


def bind_score_name(port: FeedbackPort, score_name: str) -> None:
    """Give an adapter that names its score (`LangfuseFeedback.score_name`) the spec's name. The
    registry builds adapters with no arguments, so the lifespan does this once."""
    if hasattr(port, "score_name"):
        port.score_name = score_name


class FeedbackLookup:
    """An `on_finished` callback: writes the finished run's lookup into `StatePort`."""

    def __init__(self, state: State) -> None:
        self._state = state

    async def __call__(self, run: Run) -> None:
        if not run.done:
            return  # the client never got an answer to rate
        ports: PortBundle = self._state.ports
        config: ChassisConfig = self._state.config
        request = run.request
        value = {"trace_id": trace_id_hex(request.trace_id), "agent": request.agent}
        try:
            await ports.state.set(
                lookup_key(request.agent, request.request_id),
                json.dumps(value, separators=(",", ":")).encode(),
                ttl_s=config.spec.feedback.lookup_ttl_s,
            )
        except StateUnavailable as exc:
            ports.telemetry.counter(LOOKUP_FAILED)
            ports.telemetry.log(
                "warning",
                "feedback lookup not written",
                request_id=request.request_id,
                error=type(exc).__name__,
            )


def feedback_event(feedback: Feedback, agent: str, received_at: datetime) -> CloudEvent:
    """The `agents.feedback.received.v1` CloudEvent for a stored feedback. The comment is
    redacted."""
    data = FeedbackReceived(
        feedback_id=feedback.feedback_id,
        request_id=feedback.request_id,
        trace_id=feedback.trace_id,
        agent=agent,
        score=feedback.score,
        label=feedback.label,
        comment=None if feedback.comment is None else redact(feedback.comment),
        subject=feedback.subject,
        received_at=received_at,
    )
    content = f"{feedback.feedback_id}:{feedback.score}:{feedback.label}:{feedback.comment}"
    return CloudEvent(
        id=uuid.uuid5(EVENT_ID_NAMESPACE, content).hex,
        source=f"/agents/{agent}",
        type=FEEDBACK_RECEIVED,
        subject=feedback.request_id,
        time=received_at,
        dataschema=DATASCHEMA,
        data=data.model_dump(mode="json"),
        traceparent=f"00-{feedback.trace_id}-{uuid.uuid4().hex[:16]}-01",
        partitionkey=_digest(feedback.request_id),
    )


class FeedbackEvents:
    """Publishes `agents.feedback.received.v1` in background tasks, as `ResultPublisher` does."""

    def __init__(self, state: State) -> None:
        self._state = state
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def ports(self) -> PortBundle:
        ports: PortBundle = self._state.ports
        return ports

    def publish(self, event: CloudEvent) -> None:
        task = asyncio.create_task(self._publish(event), name=f"feedback-event:{event.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _publish(self, event: CloudEvent) -> None:
        telemetry = self.ports.telemetry
        try:
            await self.ports.events.publish(FEEDBACK_RECEIVED, event)
        except Exception as exc:  # never raised into a request
            telemetry.counter(PUBLISH_FAILED, topic=FEEDBACK_RECEIVED)
            telemetry.log(
                "warning",
                "feedback event not published",
                topic=FEEDBACK_RECEIVED,
                event_id=event.id,
                request_id=event.subject,
                error=type(exc).__name__,
                after_retries=isinstance(exc, PublishFailed),
            )
            return
        telemetry.counter(PUBLISHED, topic=FEEDBACK_RECEIVED)

    async def wait(self, timeout_s: float) -> int:
        """Wait for pending publishes up to `timeout_s`; how many are still pending."""
        tasks = set(self._tasks)
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_s)
        return sum(1 for task in tasks if not task.done())

    async def aclose(self, timeout_s: float | None = None) -> None:
        """Wait up to `timeout_s` (default `SHUTDOWN_WAIT_S`), then cancel and count the rest."""
        await self.wait(SHUTDOWN_WAIT_S if timeout_s is None else timeout_s)
        left = [task for task in self._tasks if not task.done()]
        for task in left:
            task.cancel()
        if left:
            await asyncio.gather(*left, return_exceptions=True)
            self.ports.telemetry.counter(PUBLISH_ABANDONED, len(left))


class _Refusal(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _refused(code: str) -> JSONResponse:
    headers = {"Retry-After": RETRY_AFTER_S} if STATUS[code] == 503 else None
    body = {"detail": {"code": code, "message": MESSAGES[code]}}
    return JSONResponse(body, status_code=STATUS[code], headers=headers)


def _subject(http: HTTPRequest) -> str | None:
    """The authenticated caller, when the auth middleware set `request.state.principal`. Auth off
    (`NoAuth`'s anonymous principal, which holds every scope) is no subject."""
    principal = getattr(http.state, "principal", None)
    if ANY_SCOPE in getattr(principal, "scopes", ()):
        return None
    subject = getattr(principal, "subject", None)
    return subject if isinstance(subject, str) and subject else None


async def _lookup(ports: PortBundle, agent: str, request_id: str) -> str:
    """The run's trace id, or `_Refusal`."""
    raw = await ports.state.get(lookup_key(agent, request_id))
    if raw is None:
        raise _Refusal("unknown_request")
    found = json.loads(raw)
    if found.get("agent") != agent:
        raise _Refusal("unknown_request")
    trace_id: str = found["trace_id"]
    return trace_id


async def _first_seen(
    ports: PortBundle, agent: str, feedback_id: str, request_id: str, ttl_s: float
) -> datetime:
    """The time `feedback_id` was first given, kept in `StatePort`; `_Refusal` when it belongs
    to another request."""
    key = id_key(agent, feedback_id)
    now = datetime.now(UTC)
    mine = {"request_id": request_id, "timestamp": now.isoformat()}
    if await ports.state.set_if_absent(key, json.dumps(mine).encode(), ttl_s=ttl_s):
        return now
    raw = await ports.state.get(key)
    if raw is None:  # expired between the two calls
        return now
    seen = json.loads(raw)
    if seen.get("request_id") != request_id:
        raise _Refusal("feedback_id_conflict")
    return datetime.fromisoformat(seen["timestamp"])


async def _store(
    ports: PortBundle,
    config: ChassisConfig,
    body: FeedbackRequest,
    subject: str | None,
    *,
    span_attributes: dict[str, Any],
) -> Feedback:
    """Look the run up, keep the `feedback_id`'s first time, and submit the record. The trace id
    goes on the span once found. Raises `_Refusal`, `StateUnavailable`, or `FeedbackError`; a
    store error is logged first, its detail redacted, never the comment."""
    agent = config.agent.name
    feedback_id = body.feedback_id or default_feedback_id(body.request_id, subject)
    trace_id = await _lookup(ports, agent, body.request_id)
    span_attributes["trace_id"] = trace_id
    timestamp = await _first_seen(
        ports, agent, feedback_id, body.request_id, config.spec.feedback.lookup_ttl_s
    )
    record = Feedback(
        feedback_id=feedback_id,
        request_id=body.request_id,
        trace_id=trace_id,
        score=float(body.score),
        label=body.label,
        comment=body.comment,
        subject=subject,
        timestamp=timestamp,
    )
    try:
        await ports.feedback.submit(record)
    except FeedbackError as exc:
        ports.telemetry.log(
            "warning",
            "feedback not stored",
            request_id=body.request_id,
            feedback_id=feedback_id,
            code=exc.code,
            detail=redact(exc.message),
        )
        raise
    return record


def _failure_code(exc: StateUnavailable | _Refusal | FeedbackError) -> str:
    """The answer's code for a failure of `_store`. A store error keeps its own code only when
    the route knows it; else `feedback_rejected`."""
    if isinstance(exc, StateUnavailable):
        return "state_unavailable"
    if isinstance(exc, _Refusal):
        return exc.code
    return exc.code if exc.code in STATUS else "feedback_rejected"


def _accepted(state: State, record: Feedback, agent: str) -> JSONResponse:
    """Count the stored feedback, publish its event when events are on, and answer 202."""
    state.ports.telemetry.counter(COUNTER, label=record.label or "none")
    events: FeedbackEvents | None = state.feedback_events
    if events is not None:
        events.publish(feedback_event(record, agent, datetime.now(UTC)))
    accepted = FeedbackAccepted(feedback_id=record.feedback_id, trace_id=record.trace_id)
    return JSONResponse(accepted.model_dump(), status_code=202)


def mount_feedback(app: FastAPI) -> None:
    """Add `POST /v1/feedback` to `app`. `create_app` calls it when `feedback_on(config)`."""

    @app.post(
        FEEDBACK_PATH,
        operation_id="feedback",
        summary="Rate one finished call",
        description=(
            "A score from 0 (bad) to 1 (good) for a finished call, by its `request_id`, written "
            "onto the call's trace. A repeat with the same `feedback_id` updates one record."
        ),
        status_code=202,
        response_model=FeedbackAccepted,
        responses={
            404: {"model": FeedbackRefused, "description": "`unknown_request`"},
            409: {"model": FeedbackRefused, "description": "`feedback_id_conflict`"},
            501: {"model": FeedbackRefused, "description": "`feedback_not_configured`"},
            502: {"model": FeedbackRefused, "description": "`feedback_rejected`"},
            503: {
                "model": FeedbackRefused,
                "description": "`feedback_unavailable`, `state_unavailable`, `not_ready`",
            },
        },
    )
    async def feedback(body: FeedbackRequest, http: HTTPRequest) -> Any:
        state = app.state
        if not state.ready:
            return _refused("not_ready")
        ports: PortBundle = state.ports
        config: ChassisConfig = state.config
        subject = _subject(http)
        feedback_id = body.feedback_id or default_feedback_id(body.request_id, subject)
        with ports.telemetry.span(
            SPAN, request_id=body.request_id, feedback_id=feedback_id
        ) as span:
            try:
                record = await _store(ports, config, body, subject, span_attributes=span.attributes)
            except (StateUnavailable, _Refusal, FeedbackError) as exc:
                code = _failure_code(exc)
                span.attributes["error.code"] = code
                ports.telemetry.counter(FAILED, code=code)
                return _refused(code)
            return _accepted(state, record, config.agent.name)
