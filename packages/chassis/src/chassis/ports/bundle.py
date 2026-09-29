"""The set of ports the chassis runs with. Built by `chassis.profiles.build_ports`."""

from __future__ import annotations

from dataclasses import dataclass

from chassis.ports.config import ConfigPort
from chassis.ports.engine import EngineConnector
from chassis.ports.model import ModelPort
from chassis.ports.telemetry import TelemetryPort


@dataclass(frozen=True)
class PortBundle:
    model: ModelPort
    engine: EngineConnector
    config: ConfigPort
    telemetry: TelemetryPort
