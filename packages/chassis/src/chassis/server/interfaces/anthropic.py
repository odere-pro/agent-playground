"""The Anthropic interface: `POST /v1/messages` on the public port, the Anthropic Messages format
over `serve` (PoC-3 open note, sections 2, 3, and 11). The mapping is
`chassis.adapters.anthropic_compat.inbound.AnthropicInbound`; this module never imports
`anthropic` (it reaches the SDK types through `chassis.adapters.anthropic_compat.types`).

Not to be confused with the model proxy (`POST /v1/chat/completions` on the proxy port), which is
how a workload calls a model; this is how a client calls the agent.

- The body is the SDK's `MessageCreateParams`, so the OpenAPI 3.1 spec carries Anthropic's own
  request schema. FastAPI drops top-level keys the SDK type does not list; the router merges them
  back from the raw JSON, so `mcp_servers` is still refused and `temperature`, `top_p`, and
  `top_k` (not in the 1.11 type) are still counted as ignored.
- A body FastAPI cannot validate is 400 `invalid_request_error` (`register_validation_format`),
  and so is a `ValidationError` raised while the lazy `messages` iterator is read (the adapter
  reads it), not 422 or 500. A 422 here is only idempotency's `idempotency_conflict`.
- `serve(..., errors_as_http=True)`: the hold rule for streams, the run's errors as HTTP in the
  Anthropic error shape with `x-should-retry`, and re-minting a trace id in use (no 409 for it).
  Idempotency can still refuse a keyed call: 409 `idempotency_in_progress` (`api_error`), 422
  `idempotency_conflict`, 503 `state_unavailable`, all declared in `MESSAGES_RESPONSES`.
- `anthropic-version`, `anthropic-beta`, and `x-api-key` are accepted and ignored: never read,
  logged, or forwarded. They are not declared as parameters.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi import Request as HTTPRequest

from chassis.adapters.anthropic_compat.inbound import AnthropicInbound
from chassis.adapters.anthropic_compat.types import ErrorResponse, Message, MessageCreateParams
from chassis.server.config import ChassisConfig
from chassis.server.interfaces.errors import INTERFACE_KEY, register_validation_format
from chassis.server.interfaces.ids import resolve_ids
from chassis.server.interfaces.serve import event_stream_content, serve
from chassis.server.pipeline import RunPipeline

__all__ = ["MESSAGES_RESPONSES", "anthropic_router", "mount_anthropic"]

_ID_HEADERS: dict[str, Any] = {
    "request-id": {
        "description": "The run's request id, where the Anthropic SDK reads it",
        "schema": {"type": "string"},
    },
    "x-request-id": {"description": "The run's request id", "schema": {"type": "string"}},
    "x-trace-id": {"description": "The run's trace id", "schema": {"type": "string"}},
}

MESSAGES_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "A `Message`; with `stream: true`, Anthropic's server-sent events "
        "(`message_start` ... `message_stop`, or `error` mid-stream)",
        "content": event_stream_content(),
        "headers": _ID_HEADERS,
    },
    400: {"model": ErrorResponse, "description": "invalid_request_error: refused or invalid body"},
    404: {"model": ErrorResponse, "description": "not_found_error: `model` is not this agent"},
    409: {
        "model": ErrorResponse,
        "description": "api_error: idempotency_in_progress, the same call is still running",
    },
    413: {"model": ErrorResponse, "description": "invalid_request_error: the body is too large"},
    422: {
        "model": ErrorResponse,
        "description": "invalid_request_error: idempotency_conflict, the key holds another call",
    },
    500: {"model": ErrorResponse, "description": "api_error: engine_error"},
    502: {"model": ErrorResponse, "description": "api_error: the workload failed"},
    503: {
        "model": ErrorResponse,
        "description": "overloaded_error: not ready, state_unavailable, or retryable",
    },
    504: {"model": ErrorResponse, "description": "timeout_error: a2a.timeout"},
}


def anthropic_router(pipeline: RunPipeline, inbound: AnthropicInbound | None = None) -> APIRouter:
    """`POST /v1/messages` over `pipeline`, on a router of its own."""
    router = APIRouter()
    _add_route(router, pipeline, inbound or AnthropicInbound())
    return router


def _add_route(
    router: APIRouter,
    pipeline: RunPipeline,
    adapter: AnthropicInbound,
    *,
    live_limits: bool = False,
) -> None:
    """The route over `adapter`. With `live_limits`, each request first sets
    `adapter.messages_max` from the live config (`pipeline.config`), so a reloaded
    `spec.limits.messages_max` applies to the next request. The set and the read in `to_request`
    run with no await between, so concurrent requests cannot see a mixed value."""

    @router.post(
        "/v1/messages",
        operation_id="messages",
        response_model=Message,
        responses=MESSAGES_RESPONSES,
        openapi_extra={INTERFACE_KEY: "anthropic"},
    )
    async def messages(body: MessageCreateParams, http: HTTPRequest) -> Any:
        raw = await http.json()  # cached: FastAPI already parsed it
        if live_limits:
            adapter.messages_max = pipeline.config.spec.limits.messages_max
        merged: dict[str, Any] = dict(body)
        if isinstance(raw, dict):
            merged = {**{k: v for k, v in raw.items() if k not in merged}, **merged}
        return await serve(
            adapter,
            merged,
            http.headers,
            pipeline=pipeline,
            ids=resolve_ids(http.headers),
            errors_as_http=True,
            disconnected=http.is_disconnected,
            ignored=adapter.ignored(merged),
        )


def mount_anthropic(app: FastAPI) -> None:
    """Add the route to `app` itself, over `app.state.pipeline`, and answer its validation errors
    in the Anthropic shape. On the app's own router, not included: FastAPI 0.141 analyzes an
    included route a second time, and the SDK body type makes each analysis cost.
    """
    config: ChassisConfig | None = getattr(app.state, "config", None)
    inbound = AnthropicInbound(
        messages_max=None if config is None else config.spec.limits.messages_max
    )
    _add_route(app.router, app.state.pipeline, inbound, live_limits=True)
    register_validation_format(app, inbound)
