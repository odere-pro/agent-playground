"""`spec.adapters` and the three profiles: `fake`, `local`, `cloud`.

The `fake` profile resolves fully. `model: litellm` builds `LiteLLMModel` from the environment.
`engine: inprocess` builds the A2A `InProcessConnector`; the server's lifespan sets it up with
`spec.engine`. Every other adapter raises `AdapterNotAvailable` and names the PoC that adds it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from chassis.adapters.a2a import InProcessConnector
from chassis.adapters.litellm import LiteLLMModel
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


Factory = Callable[..., object]


def _scripted_model(agent: str | None = None) -> object:
    return ScriptedModel()


def _litellm_from_env(agent: str | None = None) -> object:
    """`LITELLM_BASE_URL` picks the router; `LITELLM_API_KEY` is optional at build time.
    `agent` becomes the `agent:<name>` tag on every router call.
    """
    try:
        return LiteLLMModel.from_env(agent=agent)
    except LookupError as exc:
        raise AdapterNotAvailable(str(exc)) from exc


# port -> adapter -> factory, or the PoC that adds it. Factories take no arguments, except the
# `model` ones, which accept `agent=` (see `resolve`).
REGISTRY: dict[str, dict[str, Factory | str]] = {
    "model": {
        "fake": _scripted_model,
        "litellm": _litellm_from_env,
        "vllm": "backlog 012 H-3",
    },
    "engine": {
        "fake": lambda: FakeEngine(handle=echo),
        "inprocess": InProcessConnector,
        "sidecar": "PoC-2",
        "remote": "PoC-5",
    },
    "config": {"memory": InMemoryConfig, "minio": "PoC-4", "s3": "PoC-4"},
    "telemetry": {"memory": InMemoryTelemetry, "otel": "PoC-7"},
}


def resolve(port: str, adapter: str, **kw: object) -> object:
    """Build one adapter. `kw` goes to the factory as is; only the `model` factories take any."""
    try:
        entry = REGISTRY[port][adapter]
    except KeyError as exc:
        raise AdapterNotAvailable(f"{port}: no adapter named {adapter!r}") from exc
    if isinstance(entry, str):
        raise AdapterNotAvailable(f"{port}: adapter {adapter!r} arrives in {entry}")
    return entry(**kw)


def check_lane(lane: Lane, profile: Profile) -> None:
    if lane not in LANES:
        raise LaneNotAllowed(f"unknown lane {lane!r}; one of {', '.join(LANES)}")
    if lane == "inprocess" and profile not in ("fake", "local"):
        raise LaneNotAllowed(
            f"lane 'inprocess' is for the chassis's own tests and local runs; "
            f"profile {profile!r} must use 'sidecar' or 'remote'"
        )


def build_ports(
    profile: Profile, overrides: AdapterSpec | None = None, *, agent: str | None = None
) -> PortBundle:
    """Build the ports for a profile. `overrides` is the agent's own `spec.adapters`, if any.
    `agent` is the agent name the model adapter tags its calls with.
    """
    if profile not in PROFILE_DEFAULTS:
        raise AdapterNotAvailable(f"unknown profile {profile!r}; one of {', '.join(PROFILES)}")
    spec = overrides or PROFILE_DEFAULTS[profile]
    return PortBundle(
        model=resolve("model", spec.model, agent=agent),  # type: ignore[arg-type]
        engine=resolve("engine", spec.engine),  # type: ignore[arg-type]
        config=resolve("config", spec.config),  # type: ignore[arg-type]
        telemetry=resolve("telemetry", spec.telemetry),  # type: ignore[arg-type]
    )
