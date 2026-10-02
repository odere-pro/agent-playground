"""ModelPort: how the chassis calls a model. The default real adapter is LiteLLM over OpenAI-
compatible HTTP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
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
    ) -> ModelResult: ...

    def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]: ...
