"""`LangfuseFeedback`: the `FeedbackPort` over Langfuse's scores API (`POST /api/public/scores`).

The wire shape is the one the `langfuse` SDK 5.0.4 generated client sends
(`langfuse/api/scores/raw_client.py`, `types/create_score_request.py`): the body is a JSON list of
scores, HTTP Basic auth is the project's public key and secret key, all taken is 202, some
refused is 207 `{accepted, rejected, errors: [{message}]}`.

One feedback is one score:

| Score field | From |
| ----------- | ---- |
| `id` | `feedback_id`: Langfuse upserts by id, so a repeat is one score |
| `traceId` | `trace_id`, 32 hex: Langfuse's trace id is the OTel trace id it ingested |
| `name` | `spec.feedback.score_name` (default `user-feedback`) |
| `value`, `dataType` | `score`, `NUMERIC` |
| `comment` | `comment`, as the user wrote it (omitted when None) |
| `metadata` | `{request_id, label, subject}` (None values omitted) |
| `timestamp` | `timestamp` when set: Langfuse updates a score only for the same id and timestamp |

The label is not written as a second CATEGORICAL score (suggested): two scores per feedback
would break "one record per `feedback_id`" and make a 207 half-written. It is in `metadata` and in
the `agents.feedback.received.v1` event.

Errors: a transport error, a 5xx, or a 429 is `FeedbackError("feedback_unavailable",
retryable=True)`; any other 4xx, or a 207 with a refused score, is `feedback_rejected`. No retry
here (suggested): the caller decides. The secret key is never logged, put in an error, or shown in
`repr`; an error message holds the status and Langfuse's own short message only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from chassis.ports.feedback import Feedback, FeedbackError

__all__ = [
    "DEFAULT_SCORE_NAME",
    "DEFAULT_TIMEOUT_S",
    "SCORES_PATH",
    "LangfuseFeedback",
    "feedback_from_score",
    "score_body",
]

SCORES_PATH = "/api/public/scores"
DEFAULT_SCORE_NAME = "user-feedback"
"""suggested; `spec.feedback.score_name` sets it."""
DEFAULT_TIMEOUT_S = 5.0
"""suggested: one score write, connect and read."""
_MESSAGE_CAP = 200
_HOST_VARS = ("LANGFUSE_BASE_URL", "LANGFUSE_HOST")
"""SDK 5.x reads `LANGFUSE_BASE_URL` and falls back to the deprecated `LANGFUSE_HOST`."""


def _iso(at: datetime) -> str:
    aware = at if at.tzinfo is not None else at.replace(tzinfo=UTC)  # naive is UTC, as the SDK
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


def score_body(feedback: Feedback, score_name: str = DEFAULT_SCORE_NAME) -> dict[str, Any]:
    """The one Langfuse score for `feedback`, keyed by API field names."""
    metadata = {
        key: value
        for key, value in (
            ("request_id", feedback.request_id),
            ("label", feedback.label),
            ("subject", feedback.subject),
        )
        if value is not None
    }
    body: dict[str, Any] = {
        "id": feedback.feedback_id,
        "traceId": feedback.trace_id,
        "name": score_name,
        "value": feedback.score,
        "dataType": "NUMERIC",
        "metadata": metadata,
    }
    if feedback.comment is not None:
        body["comment"] = feedback.comment
    if feedback.timestamp is not None:
        body["timestamp"] = _iso(feedback.timestamp)
    return body


def feedback_from_score(score: Mapping[str, Any]) -> Feedback:
    """The reverse of `score_body`: a stored score (as sent, or a v3 read with `subject`) as a
    `Feedback`. For contract bindings that read back through the store."""
    metadata = score.get("metadata") or {}
    trace_id = score.get("traceId")
    subject = score.get("subject")
    if trace_id is None and isinstance(subject, Mapping):
        trace_id = subject.get("id")
    timestamp = score.get("timestamp")
    return Feedback(
        feedback_id=score["id"],
        request_id=metadata["request_id"],
        trace_id=trace_id,
        score=score["value"],
        label=metadata.get("label"),
        comment=score.get("comment"),
        subject=metadata.get("subject"),
        timestamp=None if timestamp is None else datetime.fromisoformat(timestamp),
    )


def _short(text: str) -> str:
    return " ".join(text.split())[:_MESSAGE_CAP]


def _refusals(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "a 207 that is not JSON"
    if not isinstance(body, dict) or not body.get("rejected"):
        return ""
    errors = body.get("errors") or []
    first = errors[0].get("message", "") if errors and isinstance(errors[0], dict) else ""
    return _short(f"{body['rejected']} refused: {first}")


class LangfuseFeedback:
    """`FeedbackPort` over Langfuse's scores API. `transport` is for tests (an ASGI transport to
    the fake, a mock); production builds it with `from_env()`."""

    name = "langfuse"

    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
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
            auth=httpx.BasicAuth(public_key, secret_key),
            timeout=timeout_s,
            transport=transport,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LangfuseFeedback:
        """From `LANGFUSE_BASE_URL` (or the older `LANGFUSE_HOST`), `LANGFUSE_PUBLIC_KEY`, and
        `LANGFUSE_SECRET_KEY`. `LookupError` names the first unset one; there is no default host
        (a default would send data to a cloud the platform did not pick)."""
        source = os.environ if env is None else env
        host = next((source[name] for name in _HOST_VARS if source.get(name)), None)
        if host is None:
            raise LookupError("LANGFUSE_HOST (or LANGFUSE_BASE_URL) is not set")
        for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
            if not source.get(name):
                raise LookupError(f"{name} is not set")
        return cls(host, source["LANGFUSE_PUBLIC_KEY"], source["LANGFUSE_SECRET_KEY"])

    def __repr__(self) -> str:
        return f"LangfuseFeedback(host={self.host!r}, score_name={self.score_name!r})"

    async def submit(self, feedback: Feedback) -> None:
        body = [score_body(feedback, self.score_name)]
        try:
            response = await self._client.post(SCORES_PATH, json=body)
        except httpx.HTTPError as exc:
            raise FeedbackError(
                "feedback_unavailable", f"transport: {type(exc).__name__}", retryable=True
            ) from None
        status = response.status_code
        if status >= 500 or status == 429:
            raise FeedbackError("feedback_unavailable", f"HTTP {status}", retryable=True)
        if status >= 400:
            raise FeedbackError("feedback_rejected", f"HTTP {status}")
        if status == 207 and (refused := _refusals(response)):
            raise FeedbackError("feedback_rejected", f"HTTP 207: {refused}")

    async def aclose(self) -> None:
        await self._client.aclose()
