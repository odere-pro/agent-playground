"""The native request and response envelope (epic G.1) and the `Context` a `handle` receives."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["ok", "retry", "fallback", "error"]

ID_MAX = 128
"""suggested: the most characters in a `request_id` or `trace_id` (contract v8, R1-09)."""

ID_PATTERN = rf"^[A-Za-z0-9._:-]{{1,{ID_MAX}}}$"
"""A `request_id` or `trace_id`: the alphabet of `feedback.FEEDBACK_ID_PATTERN`, 1 to `ID_MAX`."""

KEY_MAX = 255
"""suggested: the most characters in an `idempotency_key` (contract v8, R4-27)."""

KEY_PATTERN = rf"^[\x21-\x7E]{{1,{KEY_MAX}}}$"
"""An `idempotency_key`: visible ASCII, no space, 1 to `KEY_MAX`. Wider than `ID_PATTERN`, since
clients send `#` and `/` and the store hashes the key; ASCII only refuses a lone surrogate."""

Id = Annotated[str, Field(pattern=ID_PATTERN)]
"""A bounded `request_id` or `trace_id`, for every model that carries one."""

IdempotencyKey = Annotated[str, Field(pattern=KEY_PATTERN)]
"""A bounded `idempotency_key`, for every model that carries one."""


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_tokens: int = 2000
    timeout_ms: int = 30_000


class Versions(BaseModel):
    """Every response reports the versions it ran with (epic B.2, "pinned versions")."""

    model_config = ConfigDict(extra="forbid")
    chassis: str
    config: str | None = None
    prompt: str | None = None
    model_route: str | None = None


class TaskInput(BaseModel):
    """What the caller asks for. `text` for transformers; `data` for structured input."""

    model_config = ConfigDict(extra="forbid")
    text: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class Context(BaseModel):
    """What the chassis gives a `handle` next to the input.

    Never holds a product or platform credential; `run_secret` is a run-scoped capability for the
    chassis's own listener.
    """

    model_config = ConfigDict(extra="forbid")
    request_id: Id
    trace_id: Id
    idempotency_key: IdempotencyKey
    agent: str
    agent_version: str
    budget: Budget = Field(default_factory=Budget)
    versions: Versions
    model_route: str | None = None
    traceparent: str | None = None
    """The run's W3C `traceparent`, set by the connector per run; `handle` forwards it as the
    `traceparent` header on its model proxy and MCP calls. Not a credential."""
    run_secret: str | None = Field(default=None, repr=False)
    """The run's own secret, set by the chassis per run; `handle` forwards it as the
    `x-chassis-run-secret` header on its model proxy and MCP calls. It authorizes only what the run
    already permits, and only while the run is in flight. Never logged."""


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Id
    trace_id: Id
    idempotency_key: IdempotencyKey
    agent: str
    agent_version: str
    input: TaskInput
    context_ref: str | None = None
    stream: bool = False
    budget: Budget = Field(default_factory=Budget)


class Response(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Id
    trace_id: Id
    idempotency_key: IdempotencyKey
    agent: str
    agent_version: str
    output: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    status: Status
    versions: Versions
    context_ref: str | None = None
