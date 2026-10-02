"""The OpenAI interface: `POST /v1/chat/completions` on the public port, where a client calls the
agent and `model` is the agent's name (PoC-3 open note, section 5). Not the model proxy: that is
`POST /v1/chat/completions` on the proxy port (`chassis.server.model_proxy`), where a workload
calls a model and `model` is the LiteLLM route.

The body is the SDK's `CompletionCreateParams`, validated by FastAPI; the mapping is
`chassis.adapters.openai_compat.inbound.OpenAIInbound`, reached through
`chassis.adapters.openai_compat.types`, so this module never imports `openai` (`make lint`). The
call goes through `serve` with `errors_as_http=True`: an `error` before the first `delta` is an
HTTP error the SDK raises, and a body FastAPI cannot validate is 400 in OpenAI's shape. The ids
follow `ids.resolve_ids` (a `traceparent` header, else minted; a trace id in use is re-minted, so
an SDK client never sees a 409 for it). Idempotency can still refuse a keyed call: 409
`idempotency_in_progress`, 422 `idempotency_conflict`, 503 `state_unavailable`, all declared in
`CHAT_COMPLETIONS_RESPONSES` (contract v3, "Refusals").
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi import Request as HTTPRequest

from chassis.adapters.openai_compat.inbound import OpenAIInbound
from chassis.adapters.openai_compat.types import (
    ChatCompletion,
    CompletionCreateParams,
    OpenAIErrorResponse,
)
from chassis.server.config import ChassisConfig
from chassis.server.interfaces.errors import INTERFACE_KEY, register_validation_format
from chassis.server.interfaces.ids import resolve_ids
from chassis.server.interfaces.serve import event_stream_content, serve
from chassis.server.pipeline import RunPipeline

__all__ = ["CHAT_COMPLETIONS_RESPONSES", "mount_openai", "openai_router"]

_ID_HEADERS: dict[str, Any] = {
    "x-request-id": {"description": "The run's request id", "schema": {"type": "string"}},
    "x-trace-id": {"description": "The run's trace id", "schema": {"type": "string"}},
}

CHAT_COMPLETIONS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "A `ChatCompletion`; with `stream: true`, `ChatCompletionChunk` frames "
        "and `data: [DONE]`",
        "headers": _ID_HEADERS,
        "content": event_stream_content(),
    },
    400: {"model": OpenAIErrorResponse, "description": "A refused or invalid body"},
    404: {"model": OpenAIErrorResponse, "description": "model_not_found: not this agent"},
    409: {
        "model": OpenAIErrorResponse,
        "description": "server_error / idempotency_in_progress: the same call is still running",
    },
    413: {"model": OpenAIErrorResponse, "description": "limit_exceeded: the body is too large"},
    422: {
        "model": OpenAIErrorResponse,
        "description": "invalid_request_error / idempotency_conflict: the key holds another call",
    },
    500: {"model": OpenAIErrorResponse, "description": "engine_error"},
    502: {"model": OpenAIErrorResponse, "description": "The workload failed (not retryable)"},
    503: {
        "model": OpenAIErrorResponse,
        "description": "Not ready, state_unavailable, or a retryable failure",
    },
    504: {"model": OpenAIErrorResponse, "description": "a2a.timeout"},
}


def openai_router(pipeline: RunPipeline, inbound: OpenAIInbound | None = None) -> APIRouter:
    """The OpenAI interface, `POST /v1/chat/completions` on the public port, over `pipeline`."""
    router = APIRouter()
    _add_route(router, pipeline, inbound or OpenAIInbound())
    return router


def _add_route(
    router: APIRouter, pipeline: RunPipeline, adapter: OpenAIInbound, *, live_limits: bool = False
) -> None:
    """The route over `adapter`. With `live_limits`, each request first sets
    `adapter.messages_max` from the live config (`pipeline.config`), so a reloaded
    `spec.limits.messages_max` applies to the next request. The set and the read in `to_request`
    run with no await between, so concurrent requests cannot see a mixed value."""

    @router.post(
        "/v1/chat/completions",
        operation_id="chat_completions",
        tags=["openai"],
        response_model=ChatCompletion,
        responses=CHAT_COMPLETIONS_RESPONSES,
        openapi_extra={INTERFACE_KEY: "openai"},
    )
    async def chat_completions(body: CompletionCreateParams, http: HTTPRequest) -> Any:
        if live_limits:
            adapter.messages_max = pipeline.config.spec.limits.messages_max
        return await serve(
            adapter,
            body,
            http.headers,
            pipeline=pipeline,
            ids=resolve_ids(http.headers),
            errors_as_http=True,
            disconnected=http.is_disconnected,
            options=adapter.options(body),
            ignored=adapter.ignored(body, http.headers),
        )


def _messages_max(app: FastAPI) -> int | None:
    """`spec.limits.messages_max` of the app's config, or None with no config."""
    config: ChassisConfig | None = getattr(app.state, "config", None)
    return None if config is None else config.spec.limits.messages_max


def mount_openai(app: FastAPI) -> None:
    """Add the route to `app` itself (its `state.pipeline`) and answer its validation errors in
    OpenAI's shape. On the app's own router, not included: FastAPI 0.141 analyzes an included
    route a second time, and the SDK body type makes each analysis cost (`create_app`'s start-up).
    """
    adapter = OpenAIInbound(messages_max=_messages_max(app))
    _add_route(app.router, app.state.pipeline, adapter, live_limits=True)
    register_validation_format(app, adapter)
