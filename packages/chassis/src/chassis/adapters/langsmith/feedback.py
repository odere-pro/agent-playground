"""`LangSmithFeedback`: the `FeedbackPort` over LangSmith's feedback API (ADR-009 item 3a,
130 CH-11). Plain `httpx`, no SDK (ADR-009 F6).

suggested: `POST /api/v1/feedback`, `x-api-key` header auth. Check the LangSmith API reference
before pinning; the live binding needs a key and a network run.

One feedback is one LangSmith feedback record:

- `id`: `langsmith_feedback_id(feedback_id)`, a uuid5, so a repeat is one record (suggested).
- `run_id`, `trace_id`: `trace_id` as a UUID. suggested: LangSmith keys the root run of an OTLP
  trace by that trace id (ADR-009 item 3a).
- `key`: `spec.feedback.score_name` (default `user-feedback`).
- `score`, `comment` (omitted when None), `created_at` (`timestamp` when set).
- `feedback_source`: `{type: api, metadata: {feedback_id, request_id, label, subject}}`, None
  values omitted.

Errors: a transport error, a 5xx, or a 429 is `feedback_unavailable` and retryable; any other 4xx
is `feedback_rejected`. No retry here. The key is never logged, put in an error, or shown in
`repr`; only the chassis holds it.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from chassis.ports.feedback import Feedback, FeedbackError

__all__ = [
    "DEFAULT_SCORE_NAME",
    "DEFAULT_TIMEOUT_S",
    "FEEDBACK_PATH",
    "LangSmithFeedback",
    "feedback_body",
    "feedback_from_record",
    "langsmith_feedback_id",
]

FEEDBACK_PATH = "/api/v1/feedback"
"""suggested; check the LangSmith API reference before pinning."""
DEFAULT_SCORE_NAME = "user-feedback"
"""suggested; `spec.feedback.score_name` sets it."""
DEFAULT_TIMEOUT_S = 5.0
"""suggested: one feedback write, connect and read."""
_ID_NAMESPACE = uuid.UUID("6f1c2a47-6a2b-5c39-9b0e-2f7d1f0c8a11")
"""suggested: a fixed namespace for `langsmith_feedback_id`. Changing it splits old records."""


def langsmith_feedback_id(feedback_id: str) -> str:
    """The LangSmith record id for a `feedback_id`. LangSmith ids are UUIDs and a `feedback_id`
    is any string the caller sent, so it is hashed (uuid5): the same id is the same record."""
    return str(uuid.uuid5(_ID_NAMESPACE, feedback_id))


def _iso(at: datetime) -> str:
    aware = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


def feedback_body(feedback: Feedback, score_name: str = DEFAULT_SCORE_NAME) -> dict[str, Any]:
    """The one LangSmith feedback record for `feedback`, keyed by API field names."""
    run = str(uuid.UUID(feedback.trace_id))
    metadata = {
        key: value
        for key, value in (
            ("feedback_id", feedback.feedback_id),
            ("request_id", feedback.request_id),
            ("label", feedback.label),
            ("subject", feedback.subject),
        )
        if value is not None
    }
    body: dict[str, Any] = {
        "id": langsmith_feedback_id(feedback.feedback_id),
        "run_id": run,
        "trace_id": run,
        "key": score_name,
        "score": feedback.score,
        "feedback_source": {"type": "api", "metadata": metadata},
    }
    if feedback.comment is not None:
        body["comment"] = feedback.comment
    if feedback.timestamp is not None:
        body["created_at"] = _iso(feedback.timestamp)
    return body


def feedback_from_record(record: Mapping[str, Any]) -> Feedback:
    """The reverse of `feedback_body`, for contract bindings that read back through the store."""
    metadata = record["feedback_source"]["metadata"]
    created = record.get("created_at")
    return Feedback(
        feedback_id=metadata["feedback_id"],
        request_id=metadata["request_id"],
        trace_id=uuid.UUID(record["trace_id"]).hex,
        score=record["score"],
        label=metadata.get("label"),
        comment=record.get("comment"),
        subject=metadata.get("subject"),
        timestamp=None if created is None else datetime.fromisoformat(created),
    )


class LangSmithFeedback:
    """`FeedbackPort` over LangSmith's feedback API. `transport` is for tests; production builds
    it with `from_env()`."""

    name = "langsmith"

    def __init__(
        self,
        host: str,
        api_key: str,
        *,
        score_name: str = DEFAULT_SCORE_NAME,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.score_name = score_name
        """Set from `spec.feedback.score_name` by the chassis at start."""
        self._client = httpx.AsyncClient(
            base_url=self.host,
            headers={"x-api-key": api_key},
            timeout=timeout_s,
            transport=transport,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LangSmithFeedback:
        """From `LANGSMITH_ENDPOINT` and `LANGSMITH_API_KEY`. `LookupError` names the first unset
        one. No default host: a default would send data to a cloud the platform did not pick."""
        source = os.environ if env is None else env
        for var in ("LANGSMITH_ENDPOINT", "LANGSMITH_API_KEY"):
            if not source.get(var):
                raise LookupError(f"{var} is not set")
        return cls(source["LANGSMITH_ENDPOINT"], source["LANGSMITH_API_KEY"])

    def __repr__(self) -> str:
        return f"LangSmithFeedback(host={self.host!r}, score_name={self.score_name!r})"

    async def submit(self, feedback: Feedback) -> None:
        body = feedback_body(feedback, self.score_name)
        try:
            response = await self._client.post(FEEDBACK_PATH, json=body)
        except httpx.HTTPError as exc:
            raise FeedbackError(
                "feedback_unavailable", f"transport: {type(exc).__name__}", retryable=True
            ) from None
        status = response.status_code
        if status >= 500 or status == 429:
            raise FeedbackError("feedback_unavailable", f"HTTP {status}", retryable=True)
        if status >= 400:
            raise FeedbackError("feedback_rejected", f"HTTP {status}")

    async def aclose(self) -> None:
        await self._client.aclose()
