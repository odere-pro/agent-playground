"""A fake of the three Langfuse public API routes the chassis uses, as one Starlette app.

The shapes follow the `langfuse` Python SDK 5.0.4's generated API client (`langfuse/api/scores`,
`langfuse/api/opentelemetry`):

- `POST /api/public/scores`: the body is a JSON list of scores (`CreateScoresRequest =
  List[CreateScoreRequest]`). All accepted is 202 `{"message": "Accepted"}`; some refused is 207
  `{"accepted", "rejected", "errors": [{"message"}]}`. A score with an `id` that exists replaces
  it (an upsert); one with no `id` gets a fresh one.
- `GET /api/public/v3/scores?id=a,b&fields=details,subject`: `{"data": [...], "meta": {...}}`,
  each score with `subject: {"kind": "trace", "id": <traceId>}` (`ScoreSubjectV3_Trace`).
- `GET /api/public/scores/{id}`: one stored score as it was sent, plus `createdAt`, or 404. A
  convenience for tests; SDK 5.x reads through the v3 list instead.
- `POST /api/public/otel/v1/traces`: OTLP over HTTP. The body is recorded as sent (protobuf or
  JSON), never parsed; the answer is an empty 200.
- `GET /api/public/health`: no auth.

Every route but health needs HTTP Basic auth with the project's public key and secret key; a wrong
pair is 401. `unavailable = True` makes every authed route answer 503 and store nothing.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

__all__ = ["DATA_TYPES", "FakeLangfuse", "Received", "create_app"]

DATA_TYPES = frozenset({"NUMERIC", "BOOLEAN", "CATEGORICAL", "CORRECTION", "TEXT"})
"""`ScoreDataType` in SDK 5.0.4."""


@dataclass(frozen=True)
class Received:
    """One OTLP export: the content type and the raw body."""

    content_type: str
    body: bytes


@dataclass
class FakeLangfuse:
    """The fake's state: the project keys, the stored scores by id, and the OTLP bodies."""

    public_key: str
    secret_key: str
    scores: dict[str, dict[str, Any]] = field(default_factory=dict)
    writes: dict[str, int] = field(default_factory=dict)
    """How many times each score id was written."""
    traces: list[Received] = field(default_factory=list)
    unavailable: bool = False

    def authorized(self, header: str | None) -> bool:
        if not header or not header.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(header[len("Basic ") :], validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            return False
        public, _, secret = raw.partition(":")
        return (public, secret) == (self.public_key, self.secret_key)

    def store(self, score: dict[str, Any]) -> str | None:
        """Store one score; the reason it is refused, or None."""
        problem = _problem(score)
        if problem is not None:
            return problem
        score_id = str(score.get("id") or uuid.uuid4().hex)
        now = datetime.now(UTC).isoformat()
        stored = dict(score, id=score_id)  # as sent: no `timestamp` is added
        stored.setdefault("dataType", "NUMERIC")
        stored["createdAt"] = self.scores.get(score_id, {}).get("createdAt", now)
        stored["updatedAt"] = now
        self.scores[score_id] = stored
        self.writes[score_id] = self.writes.get(score_id, 0) + 1
        return None


def _problem(score: Any) -> str | None:
    if not isinstance(score, dict):
        return "a score must be an object"
    if not isinstance(score.get("name"), str) or not score["name"]:
        return "name is required"
    data_type = score.get("dataType", "NUMERIC")
    if data_type not in DATA_TYPES:
        return f"unknown dataType {data_type!r}"
    value = score.get("value")
    numeric = isinstance(value, int | float) and not isinstance(value, bool)
    if data_type in {"NUMERIC", "BOOLEAN"} and not numeric:
        return f"value must be a number for {data_type}"
    if data_type in {"CATEGORICAL", "TEXT"} and not isinstance(value, str):
        return f"value must be a string for {data_type}"
    if data_type == "BOOLEAN" and value not in (0, 1):
        return "a BOOLEAN value is 0 or 1"
    if score.get("metadata") is not None and not isinstance(score["metadata"], dict):
        return "metadata must be an object"
    return None


def _v3(score: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in score.items() if k not in {"traceId", "observationId", "sessionId"}}
    out.setdefault("timestamp", score["createdAt"])  # the store picks the time when none was sent
    out.setdefault("source", "API")
    out.setdefault("environment", "default")
    if score.get("traceId"):
        out["subject"] = {"kind": "trace", "id": score["traceId"]}
    return out


def create_app(state: FakeLangfuse) -> Starlette:
    """The fake API over `state`."""

    def refused(request: Request) -> Response | None:
        if not state.authorized(request.headers.get("authorization")):
            return JSONResponse({"message": "Unauthorized"}, status_code=401)
        if state.unavailable:
            return JSONResponse({"message": "Service Unavailable"}, status_code=503)
        return None

    async def create_scores(request: Request) -> Response:
        if (answer := refused(request)) is not None:
            return answer
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"message": "invalid JSON"}, status_code=400)
        if not isinstance(body, list):
            return JSONResponse({"message": "the body must be a list of scores"}, status_code=400)
        errors = [problem for score in body if (problem := state.store(score)) is not None]
        if errors:
            accepted = len(body) - len(errors)
            payload = {
                "accepted": accepted,
                "rejected": len(errors),
                "errors": [{"message": e} for e in errors],
            }
            return JSONResponse(payload, status_code=207)
        return JSONResponse({"message": "Accepted"}, status_code=202)

    async def get_score(request: Request) -> Response:
        if (answer := refused(request)) is not None:
            return answer
        score = state.scores.get(request.path_params["score_id"])
        if score is None:
            return JSONResponse({"message": "Score not found"}, status_code=404)
        return JSONResponse(score)

    async def list_scores_v3(request: Request) -> Response:
        if (answer := refused(request)) is not None:
            return answer
        ids = [i for i in request.query_params.get("id", "").split(",") if i]
        trace_id = request.query_params.get("traceId")
        found = [
            _v3(s)
            for s in state.scores.values()
            if (not ids or s["id"] in ids) and (trace_id is None or s.get("traceId") == trace_id)
        ]
        return JSONResponse({"data": found, "meta": {"limit": 50, "cursor": None}})

    async def otel_traces(request: Request) -> Response:
        if (answer := refused(request)) is not None:
            return answer
        content_type = request.headers.get("content-type", "")
        state.traces.append(Received(content_type, await request.body()))
        return Response(b"", status_code=200, media_type="application/x-protobuf")

    async def health(request: Request) -> Response:
        return JSONResponse({"status": "OK"})

    return Starlette(
        routes=[
            Route("/api/public/scores", create_scores, methods=["POST"]),
            Route("/api/public/scores/{score_id}", get_score, methods=["GET"]),
            Route("/api/public/v3/scores", list_scores_v3, methods=["GET"]),
            Route("/api/public/otel/v1/traces", otel_traces, methods=["POST"]),
            Route("/api/public/health", health, methods=["GET"]),
        ]
    )
