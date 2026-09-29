"""The chassis JSON event schema, version 0.

A workload yields these events from `handle`. They travel over A2A in the `sidecar`
and `remote` lanes and in memory in `inprocess`. The published JSON Schema lives in
`packages/chassis/schemas/events.v0.json` and is generated from these models.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

SCHEMA_VERSION = "0"
SUPPORTED_SCHEMA_VERSIONS: tuple[str, ...] = ("0",)
"""Versions the chassis accepts: the current one and, once there is one, the previous major."""


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["0"] = "0"


class Start(_EventBase):
    type: Literal["start"] = "start"
    request_id: str


class Delta(_EventBase):
    type: Literal["delta"] = "delta"
    text: str


class ToolCall(_EventBase):
    type: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None


class Metrics(_EventBase):
    type: Literal["metrics"] = "metrics"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    model_route: str | None = None
    latency_ms: int | None = None
    attempt: int = 1


class End(_EventBase):
    type: Literal["end"] = "end"
    status: Literal["ok", "retry", "fallback"] = "ok"
    output: dict[str, Any] | None = None


class Error(_EventBase):
    type: Literal["error"] = "error"
    code: str
    message: str
    retryable: bool = False


Event = Annotated[Start | Delta | ToolCall | Metrics | End | Error, Field(discriminator="type")]
"""One event in the chassis event stream."""

_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


class UnsupportedSchemaVersion(ValueError):
    """The event names a schema version this chassis does not accept."""


def parse_event(data: dict[str, Any]) -> Event:
    """Validate one event from the wire. Refuses an unknown schema version with a clear error."""
    version = data.get("schema_version", SCHEMA_VERSION)
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise UnsupportedSchemaVersion(
            f"event schema version {version!r} is not supported; "
            f"this chassis accepts {', '.join(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    return _ADAPTER.validate_python(data)


def event_json_schema() -> dict[str, Any]:
    """The JSON Schema of the event union, draft 2020-12."""
    return _ADAPTER.json_schema(mode="serialization")
