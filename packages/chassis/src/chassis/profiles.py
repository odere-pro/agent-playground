"""`spec.adapters` and the three profiles: `fake`, `local`, `cloud`.

Day 0 resolves only the `fake` profile. Every other adapter raises `AdapterNotAvailable`
and names the PoC that adds it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from chassis.core.handle import echo
from chassis.fakes.config import InMemoryConfig
from chassis.fakes.engine import FakeEngine
from chassis.fakes.model import ScriptedModel
from chassis.fakes.telemetry import InMemoryTelemetry
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import LANES, Lane

Profile = Literal["fake", "local", "cloud"]
PROFILES: tuple[Profile, ...] = ("fake", "local", "cloud")


class AdapterSpec(BaseModel):
    """The `spec.adapters` block: the adapter per port."""

    model_config = ConfigDict(extra="forbid")
    model: str = "fake"
    engine: str = "fake"
    config: str = "memory"
    telemetry: str = "memory"


PROFILE_DEFAULTS: dict[Profile, AdapterSpec] = {
    "fake": AdapterSpec(),
    "local": AdapterSpec(model="litellm", engine="sidecar", config="minio", telemetry="otel"),
    "cloud": AdapterSpec(model="litellm", engine="sidecar", config="s3", telemetry="otel"),
}


class AdapterNotAvailable(LookupError):
    """The adapter is named in the profile but not built yet."""


class LaneNotAllowed(ValueError):
    """`inprocess` is allowed only in the `fake` and `local` profiles (ADR-001 item 4)."""


Factory = Callable[[], object]

# port -> adapter -> factory, or the PoC that adds it.
REGISTRY: dict[str, dict[str, Factory | str]] = {
    "model": {
        "fake": ScriptedModel,
        "litellm": "PoC-1 walking skeleton",
        "vllm": "backlog 012 H-3",
    },
    "engine": {
        "fake": lambda: FakeEngine(handle=echo),
        "inprocess": "PoC-1 walking skeleton",
        "sidecar": "PoC-2",
        "remote": "PoC-5",
    },
    "config": {"memory": InMemoryConfig, "minio": "PoC-4", "s3": "PoC-4"},
    "telemetry": {"memory": InMemoryTelemetry, "otel": "PoC-7"},
}


def resolve(port: str, adapter: str) -> object:
    try:
        entry = REGISTRY[port][adapter]
    except KeyError as exc:
        raise AdapterNotAvailable(f"{port}: no adapter named {adapter!r}") from exc
    if isinstance(entry, str):
        raise AdapterNotAvailable(f"{port}: adapter {adapter!r} arrives in {entry}")
    return entry()


def check_lane(lane: Lane, profile: Profile) -> None:
    if lane not in LANES:
        raise LaneNotAllowed(f"unknown lane {lane!r}; one of {', '.join(LANES)}")
    if lane == "inprocess" and profile not in ("fake", "local"):
        raise LaneNotAllowed(
            f"lane 'inprocess' is for the chassis's own tests and local runs; "
            f"profile {profile!r} must use 'sidecar' or 'remote'"
        )


def build_ports(profile: Profile, overrides: AdapterSpec | None = None) -> PortBundle:
    """Build the ports for a profile. `overrides` is the agent's own `spec.adapters`, if any."""
    if profile not in PROFILE_DEFAULTS:
        raise AdapterNotAvailable(f"unknown profile {profile!r}; one of {', '.join(PROFILES)}")
    spec = overrides or PROFILE_DEFAULTS[profile]
    return PortBundle(
        model=resolve("model", spec.model),  # type: ignore[arg-type]
        engine=resolve("engine", spec.engine),  # type: ignore[arg-type]
        config=resolve("config", spec.config),  # type: ignore[arg-type]
        telemetry=resolve("telemetry", spec.telemetry),  # type: ignore[arg-type]
    )
