"""The native request and response envelope (epic G.1) and the `Context` a `handle` receives."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["ok", "retry", "fallback", "error"]


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
    """What the chassis gives a `handle` next to the input. Never holds a key."""

    model_config = ConfigDict(extra="forbid")
    request_id: str
    trace_id: str
    idempotency_key: str
    agent: str
    agent_version: str
    budget: Budget = Field(default_factory=Budget)
    versions: Versions
    model_route: str | None = None
    traceparent: str | None = None
    """The run's W3C `traceparent`, set by the connector per run; `handle` forwards it as the
    `traceparent` header on its model proxy and MCP calls. Not a credential."""


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    trace_id: str
    idempotency_key: str
    agent: str
    agent_version: str
    input: TaskInput
    context_ref: str | None = None
    stream: bool = False
    budget: Budget = Field(default_factory=Budget)


class Response(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    trace_id: str
    idempotency_key: str
    agent: str
    agent_version: str
    output: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    status: Status
    versions: Versions
    context_ref: str | None = None
