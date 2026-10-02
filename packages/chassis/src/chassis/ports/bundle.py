"""The set of ports the chassis runs with. Built by `chassis.profiles.build_ports`."""

from __future__ import annotations

from dataclasses import dataclass, field

from chassis.ports.config import ConfigPort
from chassis.ports.engine import EngineConnector
from chassis.ports.events import EventPort, NoEvents
from chassis.ports.model import ModelPort
from chassis.ports.state import InMemoryState, StatePort
from chassis.ports.telemetry import TelemetryPort
from chassis.ports.tool import NoTools, ToolPort


@dataclass(frozen=True)
class PortBundle:
    model: ModelPort
    engine: EngineConnector
    config: ConfigPort
    telemetry: TelemetryPort
    tools: ToolPort = field(default_factory=NoTools)
    """Defaults to no tools, so bundles built before `ToolPort` existed still build."""
    state: StatePort = field(default_factory=InMemoryState)
    """Defaults to one in-memory store per bundle, so bundles built before PoC-4 still build."""
    events: EventPort = field(default_factory=NoEvents)
    """Defaults to no events (`spec.adapters.events: none`)."""
