"""The chassis JSON event schema, version 0.

A workload yields these events from `handle`. They travel over A2A in the `sidecar`
and `remote` lanes and in memory in `inprocess`. The published JSON Schema lives in
`packages/chassis/schemas/events.v0.json` and is generated from these models.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from chassis.core.envelope import ID_PATTERN

SCHEMA_VERSION = "0"
SUPPORTED_SCHEMA_VERSIONS: tuple[str, ...] = ("0",)
"""Versions the chassis accepts: the current one and, once there is one, the previous major."""

# Bounds on the v0 events (contract v8, R1-09). Each is far above what an honest producer emits,
# so it refuses only values no conforming producer writes, and `schema_version` stays "0".
EVENT_TEXT_MAX = 1_048_576
"""suggested: the most characters in one `delta`."""

TOOL_CALL_ID_MAX = 256
"""suggested: the most characters in a `tool_call.call_id`."""

TOOL_NAME_MAX = 128
"""suggested: the most characters in a `tool_call.name` (MCP's own limit; a metric label)."""

COUNT_MAX = 2**53
"""suggested: the largest token count or latency, as `adapters.a2a.plain.MAX_COUNT`."""

MODEL_ROUTE_MAX = 256
"""suggested: the most characters in `metrics.model_route`, as the model proxy's `model` cap."""

ERROR_CODE_PATTERN = r"^[A-Za-z0-9._:-]{1,64}$"
"""suggested: an `error.code`, 1 to 64 characters of the id alphabet (a metric label)."""

ERROR_MESSAGE_MAX = 8192
"""suggested: the most characters in an `error.message`. A writer that builds the message from
exception text cuts it to this, or its own error event fails validation."""


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["0"] = "0"


class Start(_EventBase):
    type: Literal["start"] = "start"
    request_id: str = Field(pattern=ID_PATTERN)


class Delta(_EventBase):
    type: Literal["delta"] = "delta"
    text: str = Field(max_length=EVENT_TEXT_MAX)


class ToolCall(_EventBase):
    type: Literal["tool_call"] = "tool_call"
    call_id: str = Field(min_length=1, max_length=TOOL_CALL_ID_MAX)
    name: str = Field(min_length=1, max_length=TOOL_NAME_MAX)
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None


class Metrics(_EventBase):
    type: Literal["metrics"] = "metrics"
    input_tokens: int = Field(default=0, ge=0, le=COUNT_MAX)
    output_tokens: int = Field(default=0, ge=0, le=COUNT_MAX)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    model_route: str | None = Field(default=None, max_length=MODEL_ROUTE_MAX)
    latency_ms: int | None = Field(default=None, ge=0, le=COUNT_MAX)
    attempt: int = Field(default=1, ge=1)


class End(_EventBase):
    type: Literal["end"] = "end"
    status: Literal["ok", "retry", "fallback"] = "ok"
    output: dict[str, Any] | None = None


class Error(_EventBase):
    type: Literal["error"] = "error"
    code: str = Field(pattern=ERROR_CODE_PATTERN)
    message: str = Field(max_length=ERROR_MESSAGE_MAX)
    retryable: bool = False


Event = Annotated[Start | Delta | ToolCall | Metrics | End | Error, Field(discriminator="type")]
"""One event in the chassis event stream."""

_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


_VERSION_TEXT_CAP = 40
"""suggested: the most characters of an unknown `schema_version` that an error message repeats."""


class UnsupportedSchemaVersion(ValueError):
    """The event names a schema version this chassis does not accept."""


def parse_event(data: dict[str, Any]) -> Event:
    """Validate one event from the wire. Refuses an unknown schema version with a clear error.

    A value that is not a JSON object (a mapping) raises `TypeError`, never `AttributeError`; the
    idempotency replay and the A2A server already treat `TypeError` as a bad event. The version in
    the error text is cut to `_VERSION_TEXT_CAP` characters.
    """
    if not isinstance(data, Mapping):
        raise TypeError(f"event must be a JSON object, not {type(data).__name__}")
    version = data.get("schema_version", SCHEMA_VERSION)
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise UnsupportedSchemaVersion(
            f"event schema version {repr(version)[:_VERSION_TEXT_CAP]} is not supported; "
            f"this chassis accepts {', '.join(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    return _ADAPTER.validate_python(data)


def event_json_schema() -> dict[str, Any]:
    """The JSON Schema of the event union, draft 2020-12."""
    return _ADAPTER.json_schema(mode="serialization")
