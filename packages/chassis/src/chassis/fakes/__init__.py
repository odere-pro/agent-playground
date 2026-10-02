"""In-memory fakes, one per port. Each passes the same contract suite as the real adapter."""

from chassis.fakes.config import InMemoryConfig
from chassis.fakes.engine import FakeEngine
from chassis.fakes.events import InMemoryBus
from chassis.fakes.model import ScriptedModel, ScriptRule
from chassis.fakes.state import InMemoryState
from chassis.fakes.telemetry import InMemoryTelemetry
from chassis.fakes.tool import InMemoryTools, default_tools

__all__ = [
    "FakeEngine",
    "InMemoryBus",
    "InMemoryConfig",
    "InMemoryState",
    "InMemoryTelemetry",
    "InMemoryTools",
    "ScriptRule",
    "ScriptedModel",
    "default_tools",
]
