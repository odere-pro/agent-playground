"""The native interface: `POST /v1/run`, the chassis envelope as is (contract v1), over `serve`.

`RunRequest` is the native `Request` with the ids and the agent optional. A body that names
another agent or agent version than this chassis serves is 400 `{"detail": str}`. The ids follow
`ids.resolve_ids`: the body's, else the `traceparent` and `Idempotency-Key` headers (the
`Idempotency-Key` header wins over the body's key), else minted. A body `trace_id` an in-flight
run holds is 409 `{"detail": {"code": "trace_id_in_use", "message"}}` (suggested); a header's is
re-minted. Not ready is 503 `{"detail": str}`. An engine error is a 200 envelope with `status:
error`, never an HTTP error; a body FastAPI cannot validate stays its 422.

The body is validated strictly at this boundary: no value of the wrong JSON type is coerced
(`true` or `"5"` for an integer budget, `0` for `stream`), and `budget.max_tokens` and
`budget.timeout_ms` must be positive (suggested: the minimum, the same on every interface). Both
are 422. The envelope models (`Budget`, `TaskInput`) and their published schemas are unchanged.
The ceilings (`spec.limits`) are `serve`'s: 400 `{"detail": str}` (`limits.enforce_limits`); a
body over `body_bytes_max` is 413 `{"detail": str}` (`limits.BodyLimit`).

The stream is one SSE frame per event (`event: <type>`, `data: <event>`), then one `response`
frame with the collected envelope. The two headers are read raw, not declared as header
parameters, so the operation's inputs are the body alone (the MCP tool's arguments, section 7).

`x-chassis-interface: mcp`, also read raw, marks the MCP tool's call (`interfaces.mcp`): `stream`
is read as false, telemetry is labeled `mcp`, and a body `trace_id` in use is re-minted, not 409.
It grants nothing; any other value is native.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Annotated, Any

from fastapi import APIRouter
from fastapi import Request as HTTPRequest
from pydantic import BaseModel, ConfigDict, Field, Strict, field_validator

from chassis.core.envelope import Budget, Request, Response, TaskInput
from chassis.core.events import Event
from chassis.core.inbound import (
    Ids,
    Interface,
    Refused,
    Reply,
    ReplyMeta,
    Served,
    StreamReply,
    status_for,
)
from chassis.server.config import LimitsSpec
from chassis.server.interfaces.errors import INTERFACE_KEY
from chassis.server.interfaces.ids import resolve_ids
from chassis.server.interfaces.serve import event_stream_content, serve
from chassis.server.pipeline import RunPipeline

__all__ = [
    "DetailCode",
    "DetailText",
    "NativeInbound",
    "RunRequest",
    "add_run_route",
    "describe_run",
    "native_router",
]


class RunRequest(BaseModel):
    """The `/v1/run` body: the native `Request` with the ids and the agent optional."""

    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str | None = None
    trace_id: str | None = None
    idempotency_key: str | None = None
    agent: str | None = None
    agent_version: str | None = None
    input: TaskInput
    context_ref: str | None = None
    stream: Annotated[bool, Strict()] = False
    budget: Budget = Field(default_factory=Budget)

    @field_validator("input", mode="before")
    @classmethod
    def _strict_input(cls, value: Any) -> Any:
        """The nested envelope model, validated strictly (a model's own `strict` does not reach
        a nested model)."""
        return TaskInput.model_validate(value, strict=True) if isinstance(value, dict) else value

    @field_validator("budget", mode="before")
    @classmethod
    def _strict_positive_budget(cls, value: Any) -> Any:
        """`Budget`, strictly, with each limit positive. suggested: the minimum of 1."""
        if not isinstance(value, dict):
            return value
        budget = Budget.model_validate(value, strict=True)
        for field in ("max_tokens", "timeout_ms"):
            if getattr(budget, field) <= 0:
                raise ValueError(f"budget.{field} must be a positive integer")
        return budget

    def agent_mismatch(self, served: Served) -> str | None:
        """Why the body names another agent than this chassis serves, or None."""
        for field, expected in (
            ("agent", served.agent),
            ("agent_version", served.agent_version),
        ):
            given = getattr(self, field)
            if given is not None and given != expected:
                return f"{field} {given!r} does not match the served {field} {expected!r}"
        return None


class DetailText(BaseModel):
    """400 (another agent) and 503 (not ready) on `/v1/run`."""

    detail: str


class CodeMessage(BaseModel):
    code: str
    message: str


class DetailCode(BaseModel):
    """409 `trace_id_in_use` and `idempotency_in_progress`, and 422 `idempotency_conflict`, on
    `/v1/run`."""

    detail: CodeMessage


class ValidationIssue(BaseModel):
    """One item of FastAPI's validation error, as its own `ValidationError` schema."""

    loc: list[str | int]
    msg: str
    type: str


class DetailIssues(BaseModel):
    """422 for a body that fails validation (FastAPI's `HTTPValidationError` shape)."""

    detail: list[ValidationIssue]


def _frame(name: str, payload: BaseModel) -> str:
    return f"event: {name}\ndata: {payload.model_dump_json()}\n\n"


DETAIL_CODES = frozenset({"trace_id_in_use", "idempotency_conflict", "idempotency_in_progress"})
"""The native refusals answered as `{"detail": {code, message}}`."""


class NativeInbound:
    """`InboundAdapter[RunRequest]` for the native envelope. `interface="mcp"` when the MCP tool
    calls `/v1/run` (the telemetry label and the re-mint rule; the wire is the same).
    """

    def __init__(self, interface: Interface = "native") -> None:
        self.interface: Interface = interface

    def to_request(
        self, body: RunRequest, headers: Mapping[str, str], *, ids: Ids, served: Served
    ) -> Request:
        mismatch = body.agent_mismatch(served)
        if mismatch is not None:
            raise Refused(400, {}, {"detail": mismatch})
        return Request(
            request_id=ids.request_id,
            trace_id=ids.trace_id,
            idempotency_key=ids.idempotency_key,
            agent=served.agent,
            agent_version=served.agent_version,
            input=body.input,
            context_ref=body.context_ref,
            stream=body.stream,
            budget=body.budget,
        )

    def complete(self, response: Response, meta: ReplyMeta) -> Reply:
        return Reply(200, {}, response.model_dump(mode="json"))

    def stream(self, events: AsyncIterator[Event], meta: ReplyMeta) -> StreamReply:
        async def frames() -> AsyncIterator[str]:
            seen: list[Event] = []
            async for event in events:
                seen.append(event)
                yield _frame(event.type, event)
            yield _frame("response", await meta.collect(seen))

        return StreamReply(frames())

    def error(self, code: str, message: str, retryable: bool, meta: ReplyMeta) -> Reply:
        """Only the refusals before a run reach here: `trace_id_in_use` (409),
        `idempotency_conflict` (422), and `idempotency_in_progress` (409) are `{"detail": {code,
        message}}`; any other code is `status_for` with `{"detail": message}` (`not_ready` and
        `state_unavailable`: 503). No `x-should-retry`: the native wire is contract v1's.
        """
        if code in DETAIL_CODES:
            status = 409 if code == "trace_id_in_use" else status_for(code, retryable)
            return Reply(status, {}, {"detail": {"code": code, "message": message}})
        return Reply(status_for(code, retryable), {}, {"detail": message})


RUN_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The response envelope; with `stream: true`, one SSE frame per event "
        "and a last `response` frame",
        "content": event_stream_content(),
    },
    400: {
        "model": DetailText,
        "description": "The body names another agent or version, or `limit_exceeded`: a budget "
        "or a turn count outside `spec.limits`",
    },
    422: {
        "model": DetailIssues | DetailCode,
        "description": "The body fails validation (`detail` is a list), or "
        "idempotency_conflict: this Idempotency-Key was used with another input "
        "(`detail` is `{code, message}`)",
    },
    409: {
        "model": DetailCode,
        "description": "trace_id_in_use: the body's trace_id is held; idempotency_in_progress: "
        "a run with this Idempotency-Key is still in flight",
    },
    413: {"model": DetailText, "description": "The body is over `spec.limits.body_bytes_max`"},
    503: {
        "model": DetailText,
        "description": "The engine is not ready, or state_unavailable: the state store failed "
        "for a call with an Idempotency-Key",
    },
}


def describe_run(agent: str, version: str, limits: LimitsSpec | None = None) -> tuple[str, str]:
    """The `/v1/run` operation's `summary` and `description`, built from the served agent. The
    MCP tool is generated from this operation and carries the description, so an MCP client has
    something to choose it by with no hand-written tool definition. With `limits`, it states the
    ceilings, so an MCP client's model does not ask for more.
    """
    summary = f"Run the {agent} agent"
    description = (
        f"Run the agent {agent!r}, version {version}, once, and return its response envelope "
        "(output, status, metrics, versions). Input: `input.text`, the text to work on, and the "
        "optional `input.data` object for structured input; `input.data.system` (a system "
        "prompt) and `input.data.history` (earlier turns, `[{role, text}]`) are carried as is. "
        "Optional: `budget.max_tokens` and `budget.timeout_ms` bound the whole run; "
        "`stream: true` answers server-sent events (native callers only)."
    )
    if limits is not None:
        description += (
            f" Limits: `budget.max_tokens` at most {limits.max_tokens_max}, "
            f"`budget.timeout_ms` at most {limits.timeout_ms_max}, both at least 1; at most "
            f"{limits.messages_max} turns (`input.data.history` plus the input); a body of at "
            f"most {limits.body_bytes_max} bytes. A call over a limit is refused, not clamped."
        )
    return summary, description


def native_router(
    pipeline: RunPipeline, *, summary: str | None = None, description: str | None = None
) -> APIRouter:
    """`POST /v1/run` over `pipeline`. `summary` and `description` go on the operation
    (`describe_run`); without them FastAPI's default summary applies.
    """
    router = APIRouter()
    add_run_route(router, pipeline, summary=summary, description=description)
    return router


def add_run_route(
    router: APIRouter,
    pipeline: RunPipeline,
    *,
    summary: str | None = None,
    description: str | None = None,
) -> None:
    """Add `POST /v1/run` over `pipeline` to `router`: a router of its own (`native_router`) or
    the app's (`mount_interfaces`, so FastAPI analyzes the route once, not again on include).
    """
    inbound = NativeInbound()
    mcp_inbound = NativeInbound("mcp")

    @router.post(
        "/v1/run",
        operation_id="run",
        summary=summary,
        description=description,
        response_model=Response,
        responses=RUN_RESPONSES,
        openapi_extra={INTERFACE_KEY: "native"},
    )
    async def run(body: RunRequest, http: HTTPRequest) -> Any:
        ids = resolve_ids(
            http.headers,
            request_id=body.request_id,
            trace_id=body.trace_id,
            idempotency_key=body.idempotency_key,
        )
        adapter = inbound
        if http.headers.get(INTERFACE_KEY) == "mcp":
            # The MCP tool's call (section 7): complete only, labeled `mcp`, and a body trace_id
            # in use is re-minted. No trust: a direct caller that sends it only loses streaming.
            adapter, body = mcp_inbound, body.model_copy(update={"stream": False})
        # MCP's inner hop never sees its client leave; only a direct call is watched.
        watch = http.is_disconnected if adapter is inbound else None
        return await serve(
            adapter,
            body,
            http.headers,
            pipeline=pipeline,
            ids=ids,
            errors_as_http=False,
            disconnected=watch,
        )
