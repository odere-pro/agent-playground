"""FeedbackPort: a user's score for one call, written onto the call's trace (PoC-7). The real
adapter is `LangfuseFeedback` (`chassis.adapters.langfuse`), Langfuse's scores API.

`submit(feedback)` is idempotent per `feedback_id`: the same id twice is one record. The chassis
serves it as `POST /v1/feedback` (`chassis.server.feedback`) and finds the trace from the
`request_id`.

`FeedbackReceived` is the payload of the CloudEvent `agents.feedback.received.v1`, published on
`EventPort` when `spec.feedback.events` is on; its schema is `schemas/feedback.v0.json`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

COMMENT_MAX = 2000
"""suggested: the longest comment."""


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    feedback_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """The W3C trace id of the call (`chassis.core.trace.trace_id_hex`)."""
    score: float = Field(ge=0, le=1)
    """suggested: 0 is bad, 1 is good; a thumbs down is 0, a thumbs up is 1."""
    label: str | None = Field(default=None, max_length=64)
    comment: str | None = Field(default=None, max_length=COMMENT_MAX)
    subject: str | None = None
    """Who gave it: the caller's `Principal.subject`, never a value from the body."""
    timestamp: datetime | None = None
    """When this `feedback_id` was first given, timezone-aware. The chassis keeps it per id, so a
    repeat sends the same value: Langfuse updates a score only for the same `id` and `timestamp`.
    None lets the store pick the time."""


FEEDBACK_SCHEMA_VERSION = "0"
"""`feedback.v0.json`: the payload of `agents.feedback.received.v1`."""


class FeedbackReceived(BaseModel):
    """The payload of `agents.feedback.received.v1` (PoC-7, suggested). The comment is redacted
    (`chassis.redaction.redact`) before it is put here."""

    model_config = ConfigDict(extra="forbid")
    feedback_id: str
    request_id: str
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    agent: str
    score: float = Field(ge=0, le=1)
    label: str | None = None
    comment: str | None = None
    """Redacted: emails, phone numbers, and ids are `<ENTITY>`."""
    subject: str | None = None
    received_at: datetime


class FeedbackError(Exception):
    """The port could not store the feedback. `code`:

    - `feedback_unavailable`: the store did not answer or failed with a 5xx (`retryable=True`).
    - `feedback_rejected`: the store refused it (a 4xx), not retryable.
    - `feedback_not_configured`: `NoFeedback`.
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class FeedbackPort(Protocol):
    name: str

    async def submit(self, feedback: Feedback) -> None: ...


class NoFeedback:
    """No feedback store: every call raises `feedback_not_configured`. The `PortBundle` default."""

    name = "none"

    async def submit(self, feedback: Feedback) -> None:
        raise FeedbackError("feedback_not_configured", "spec.adapters.feedback is none")
