"""ModelPort: how the chassis calls a model. The default real adapter is LiteLLM over OpenAI-
compatible HTTP.

Failure (CH-16). A `ModelError` carries a `code`. Three codes are the port's own, and every
adapter uses them: `SERVICE_BUDGET_EXHAUSTED` when the model service refuses for the service's
budget (LiteLLM's `budget_exceeded`), `TIMEOUT` when a call passed its `timeout_s` or a transport
timeout, and `RUN_ENDED` when the run a call belongs to ended first (set by the proxy, never by an
adapter). Any other upstream status is `http_<status>`. An adapter never retries: the retries and
the fallback routes are the model router's (`num_retries` and `fallbacks` per route in the LiteLLM
config), so a chassis retry would multiply them.

`timeout_s` is the wall clock of the whole call, in seconds: the proxy passes what the run has
left of `budget.timeout_ms`. Past it the adapter raises `TIMEOUT` and ends the upstream call, so a
run that is over keeps paying for nothing. `None` is the adapter's own default.

Request metadata (012 H-3). An adapter sends the trace and request IDs of the call's run, read
from `CALL_IDS`, and nothing that picks whose spend it is: the service's virtual key binds spend.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterator, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolCallRequest(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelMessage(BaseModel):
    """One chat message on the port, tool loop included. Workloads never see it: they send the
    OpenAI shape to the proxy, which maps it here (`docs/contracts/contract-v0.md`, "Changes
    decided for v1", item 3).

    - `content` is `None` only on an assistant message that has `tool_calls`.
    - `tool_calls` only on `assistant`; an empty list is read as `None`.
    - `tool_call_id` only on `tool`, and required there, with `content`.
    - `name` on any role.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_calls: list[ToolCallRequest] | None = None
    tool_call_id: str | None = None

    @field_validator("tool_calls", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == [] else value

    @model_validator(mode="after")
    def _check_role(self) -> Self:
        if self.tool_calls is not None and self.role != "assistant":
            raise ValueError(f"tool_calls is only allowed on an assistant message, not {self.role}")
        if self.tool_call_id is not None and self.role != "tool":
            raise ValueError(f"tool_call_id is only allowed on a tool message, not {self.role}")
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("a tool message needs tool_call_id")
        if self.content is None and not (self.role == "assistant" and self.tool_calls):
            raise ValueError(
                "content may be null only on an assistant message with tool_calls, "
                f"not on {self.role}"
            )
        return self


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ModelResult(BaseModel):
    text: str
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str = ""


class ModelChunk(BaseModel):
    """One piece of a streamed answer. The last chunk has `finish=True` and carries `usage`."""

    text: str = ""
    tool_call: ToolCallRequest | None = None
    usage: Usage | None = None
    finish: bool = False


SERVICE_BUDGET_EXHAUSTED = "service_budget_exhausted"
"""The model service refused the call for the service's budget. Not retryable: the budget resets
on its own period, not on a retry."""
TIMEOUT = "timeout"
"""The call passed its `timeout_s`, or a transport timeout. Retryable."""
RUN_ENDED = "run_ended"
"""The run this call belongs to ended before the model answered. Not retryable."""


@dataclass(frozen=True)
class CallIds:
    """The run a model call belongs to: what an adapter puts in the request metadata."""

    trace_id: str
    request_id: str

    def metadata(self) -> dict[str, str]:
        return {"trace_id": self.trace_id, "request_id": self.request_id}


CALL_IDS: ContextVar[CallIds | None] = ContextVar("chassis_model_call_ids", default=None)
"""The IDs of the run the current model call belongs to; None for an uncorrelated call."""


@contextlib.contextmanager
def call_ids(trace_id: str, request_id: str) -> Iterator[None]:
    """Set `CALL_IDS` for the calls made (and the tasks created) inside the block."""
    token = CALL_IDS.set(CallIds(trace_id=trace_id, request_id=request_id))
    try:
        yield
    finally:
        CALL_IDS.reset(token)


class ModelError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class ModelPort(Protocol):
    name: str

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> ModelResult: ...

    def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> AsyncIterator[ModelChunk]: ...
