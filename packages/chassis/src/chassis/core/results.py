"""`TaskResult`: the payload of a result event (`agents.task.completed.v1` and
`agents.task.failed.v1`, 019 H-17), published as `schemas/task-result.v1.json`. Pure: no network.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from chassis.core.envelope import Status, TaskInput, Versions

TASK_COMPLETED = "agents.task.completed.v1"
"""After a run that ended with an `end` event."""
TASK_FAILED = "agents.task.failed.v1"
"""After a run that ended with an `error` event."""


class TaskResult(BaseModel):
    """One finished run, as the chassis reports it outside the request."""

    model_config = ConfigDict(extra="forbid")
    agent: str
    agent_version: str
    request_id: str
    idempotency_key: str
    """The sha256 hex of the caller's key, never the raw key (`chassis.server.results`)."""
    status: Status
    input: TaskInput | SkipJsonSchema[None] = None
    """The caller's input, redacted. Left out of the event unless `spec.events.include_input` is
    true: caller data stays off a shared broker until topic ACLs per service are in (020 X-8).
    On the wire it is absent or a `TaskInput`, never null."""
    output: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    """The run's token counts, as the `metrics` event carries them."""
    versions: Versions
    original_model: str | None = Field(
        default=None,
        description=(
            "The model that produced the original text the agent worked on, for example the text "
            "a simplifier was given. Provider terms can restrict training on it. Null when "
            "unknown. Not the route that served this run: that is `versions.model_route`."
        ),
    )
    """The model-source field (019 H-17). Filled for the simplifier by 043 A-3; null until then."""
