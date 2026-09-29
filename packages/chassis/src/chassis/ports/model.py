"""ModelPort: how the chassis calls a model. The default real adapter is LiteLLM over OpenAI-
compatible HTTP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol, TypedDict

from pydantic import BaseModel, Field


class ModelMessage(TypedDict):
    role: str
    content: str


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolCallRequest(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


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
