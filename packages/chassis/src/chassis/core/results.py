"""`TaskResult`: the payload of a result event (`agents.task.completed.v1` and
`agents.task.failed.v1`, 019 H-17), published as `schemas/task-result.v1.json`. Pure: no network.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
    input: TaskInput
    output: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    """The run's token counts, as the `metrics` event carries them."""
    versions: Versions
