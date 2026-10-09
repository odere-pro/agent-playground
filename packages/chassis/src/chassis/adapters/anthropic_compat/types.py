"""The official `anthropic` 1.11 types the Anthropic interface speaks, re-exported so that
`chassis.server.interfaces.anthropic` never imports `anthropic` itself (import-linter: the SDK is
imported only under `chassis.adapters`).

Import paths checked in `.venv` (anthropic 1.11.0): everything is re-exported by
`anthropic.types`, `ErrorResponse` from `anthropic.types.shared.error_response`, and the
`message_delta` body's `delta` model is `anthropic.types.raw_message_delta_event.Delta`.
"""

from __future__ import annotations

from anthropic.types import (
    ErrorResponse,
    InputJSONDelta,
    Message,
    MessageCreateParams,
    MessageDeltaUsage,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    RawMessageStreamEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
    Usage,
)
from anthropic.types.raw_message_delta_event import Delta as MessageDelta

__all__ = [
    "ErrorResponse",
    "InputJSONDelta",
    "Message",
    "MessageCreateParams",
    "MessageDelta",
    "MessageDeltaUsage",
    "RawContentBlockDeltaEvent",
    "RawContentBlockStartEvent",
    "RawContentBlockStopEvent",
    "RawMessageDeltaEvent",
    "RawMessageStartEvent",
    "RawMessageStopEvent",
    "RawMessageStreamEvent",
    "TextBlock",
    "TextDelta",
    "ToolUseBlock",
    "Usage",
]
