"""The official `openai` SDK (3.22) types the OpenAI interface speaks, re-exported so
`chassis.server` reaches them without importing the SDK itself (`make lint`; PoC-3 open note,
section 1).

`OpenAIError` and `OpenAIErrorResponse` are the error body: the SDK's `ErrorObject` plus the
`retryable` key the model proxy's error body also carries (section 3). They are declared on the
OpenAI interface's error statuses in the OpenAPI spec.
"""

from __future__ import annotations

from openai.types.chat import ChatCompletion, ChatCompletionChunk, CompletionCreateParams
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice
from openai.types.chat.chat_completion_chunk import ChoiceDelta
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.completion_usage import CompletionUsage
from openai.types.shared import ErrorObject
from pydantic import BaseModel

__all__ = [
    "ChatCompletion",
    "ChatCompletionChunk",
    "ChatCompletionMessage",
    "Choice",
    "ChoiceDelta",
    "ChunkChoice",
    "CompletionCreateParams",
    "CompletionUsage",
    "ErrorObject",
    "OpenAIError",
    "OpenAIErrorResponse",
]


class OpenAIError(ErrorObject):
    """`ErrorObject` with `code` set to the chassis code and `retryable` added."""

    retryable: bool | None = None


class OpenAIErrorResponse(BaseModel):
    """`{"error": {...}}`: every OpenAI interface error, before or in the stream."""

    error: OpenAIError
