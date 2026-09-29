"""Core: the envelope, the events, the collector, and the `handle` contract. No network, no product
SDK.
"""

from chassis.core.collector import collect
from chassis.core.envelope import Budget, Context, Request, Response, Status, TaskInput, Versions
from chassis.core.events import (
    SCHEMA_VERSION,
    Delta,
    End,
    Error,
    Event,
    Metrics,
    Start,
    ToolCall,
    parse_event,
)
from chassis.core.handle import Handle, echo

__all__ = [
    "SCHEMA_VERSION",
    "Budget",
    "Context",
    "Delta",
    "End",
    "Error",
    "Event",
    "Handle",
    "Metrics",
    "Request",
    "Response",
    "Start",
    "Status",
    "TaskInput",
    "ToolCall",
    "Versions",
    "collect",
    "echo",
    "parse_event",
]
