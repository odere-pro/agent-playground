"""`spec.adapters` and the three profiles: `fake`, `local`, `cloud`.

Contract v1 decision 2 (`docs/contracts/contract-v0.md`, "Changes decided for v1"): the lane is
named once, in `spec.engine.connector`. `build_ports` builds the connector from
`REGISTRY["engine"][connector]`: `inprocess` is the A2A `InProcessConnector`, `sidecar` the A2A
`SidecarConnector`; the server's lifespan sets either up with `spec.engine`. `spec.adapters` has
no `engine` field; a config that sets it is refused. `FakeEngine` is a test double, not a lane: a
test passes it in a `PortBundle` it builds itself.

`spec.adapters` merges over the profile defaults field by field (`merge_adapters`): only the
fields the agent sets change, so `adapters: {}` is the profile's defaults. `model: litellm`
builds `LiteLLMModel` from the environment. Every other adapter that is not built yet raises
`AdapterNotAvailable` and names the PoC that adds it. suggested: the `cloud` profile refuses a
`fake` or `memory` adapter (`AdapterNotAllowed`); `local` allows any mix.

PoC-4 adapters are lazy (`lazy`): the factory imports its module only when the adapter is named,
so an unused SDK (valkey, aiokafka, minio) is never loaded. A module that is not there yet names
PoC-4; an adapter whose `from_env` raises `LookupError` is "not configured".

PoC-5 entries are lazy too and name PoC-5 until their module lands: `engine: remote` is
`chassis.adapters.a2a.remote:RemoteConnector` (built with no arguments, set up with
`spec.engine` like the other lanes), `tools: mcp` is
`chassis.adapters.mcp.gateway:McpGatewayTools.from_env`.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from chassis.adapters.a2a import InProcessConnector, SidecarConnector
from chassis.adapters.litellm import LiteLLMModel
from chassis.fakes.config import InMemoryConfig
from chassis.fakes.model import ScriptedModel
from chassis.fakes.telemetry import InMemoryTelemetry
from chassis.fakes.tool import default_tools
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import LANES, Lane
from chassis.ports.events import NoEvents
from chassis.ports.state import InMemoryState

Profile = Literal["fake", "local", "cloud"]
PROFILES: tuple[Profile, ...] = ("fake", "local", "cloud")
PORTS: tuple[str, ...] = ("model", "config", "telemetry", "tools", "state", "events")
"""The ports `spec.adapters` names. The engine is not one of them: `spec.engine.connector`."""
DEFAULT_LANE: Lane = "sidecar"
"""ADR-001 item 4. `spec.engine.connector` defaults to it too (`chassis.server.config`)."""
IN_MEMORY = frozenset({"fake", "memory"})
"""suggested: the adapter names the `cloud` profile refuses."""


class AdapterSpec(BaseModel):
    """The `spec.adapters` block: the adapter per port. An unset field takes the profile default."""

    model_config = ConfigDict(extra="forbid")
    model: str | None = None
    config: str | None = None
    telemetry: str | None = None
    tools: str | None = None
    state: str | None = None
    """`memory` or `valkey` (PoC-4)."""
    events: str | None = None
    """`none`, `memory`, `kafka`, or `dapr` (PoC-4). suggested: `none` in every profile."""

    @model_validator(mode="before")
    @classmethod
    def _no_engine(cls, data: Any) -> Any:
        if isinstance(data, dict) and "engine" in data:
            raise ValueError(
                "spec.adapters.engine is removed: the lane is named once, in "
                "spec.engine.connector (contract v1, decision 2)"
            )
        return data


PROFILE_DEFAULTS: dict[Profile, AdapterSpec] = {
    "fake": AdapterSpec(
        model="fake",
        config="memory",
        telemetry="memory",
        tools="fake",
        state="memory",
        events="none",
    ),
    "local": AdapterSpec(
        model="litellm",
        config="minio",
        telemetry="otel",
        tools="mcp",
        state="valkey",
        events="none",
    ),
    "cloud": AdapterSpec(
        model="litellm", config="s3", telemetry="otel", tools="mcp", state="valkey", events="none"
    ),
}


class AdapterNotAvailable(LookupError):
    """The adapter is named in the profile but not built yet."""


class AdapterNotAllowed(ValueError):
    """suggested: the `cloud` profile runs no `fake` or `memory` adapter."""


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


def lazy(port: str, adapter: str, target: str, poc: str = "PoC-4") -> Factory:
    """A factory that imports `target` (`module:attr`, `attr` may be dotted, as in
    `Class.from_env`) only when called, then calls it with the factory's arguments.

    A `ModuleNotFoundError` (the adapter module or its SDK is not there) becomes
    `AdapterNotAvailable("<port>: adapter '<name>' arrives in <poc>")`. A `LookupError` from the
    target (a required environment variable is unset) becomes
    `AdapterNotAvailable("<port>: adapter '<name>' is not configured: <message>")`.
    """
    module_name, _, attr = target.partition(":")

    def factory(*args: object, **kw: object) -> object:
        try:
            found: Any = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            raise AdapterNotAvailable(
                f"{port}: adapter {adapter!r} arrives in {poc} (no module {exc.name!r})"
            ) from exc
        for part in attr.split("."):
            found = getattr(found, part)
        try:
            return found(*args, **kw)
        except LookupError as exc:
            raise AdapterNotAvailable(
                f"{port}: adapter {adapter!r} is not configured: {exc}"
            ) from exc

    factory.__qualname__ = factory.__name__ = f"lazy_{port}_{adapter}"
    return factory


# port -> adapter -> factory, or the PoC that adds it. Factories take no arguments, except the
# `model` ones, which accept `agent=` (see `resolve`). `engine` is keyed by lane only.
REGISTRY: dict[str, dict[str, Factory | str]] = {
    "model": {
        "fake": _scripted_model,
        "litellm": _litellm_from_env,
        "vllm": "backlog 012 H-3",
    },
    "engine": {
        "inprocess": InProcessConnector,
        "sidecar": SidecarConnector,
        "remote": lazy("engine", "remote", "chassis.adapters.a2a.remote:RemoteConnector", "PoC-5"),
    },
    "config": {
        "memory": InMemoryConfig,
        "minio": lazy("config", "minio", "chassis.adapters.s3.config:S3Config.from_env"),
        "s3": lazy("config", "s3", "chassis.adapters.s3.config:S3Config.from_env"),
    },
    "telemetry": {"memory": InMemoryTelemetry, "otel": "PoC-7"},
    "tools": {
        "fake": default_tools,
        "mcp": lazy(
            "tools", "mcp", "chassis.adapters.mcp.gateway:McpGatewayTools.from_env", "PoC-5"
        ),
    },
    "state": {
        "memory": InMemoryState,
        "valkey": lazy("state", "valkey", "chassis.adapters.valkey.state:ValkeyState.from_env"),
    },
    "events": {
        "none": NoEvents,
        "memory": lazy("events", "memory", "chassis.fakes.events:InMemoryBus"),
        "kafka": lazy("events", "kafka", "chassis.adapters.kafka.events:KafkaEvents.from_env"),
        "dapr": lazy("events", "dapr", "chassis.adapters.dapr.events:DaprEvents.from_env"),
    },
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


def merge_adapters(profile: Profile, overrides: AdapterSpec | None = None) -> AdapterSpec:
    """The profile defaults with the fields the agent set (`model_fields_set`) laid over them.
    A field set to null keeps the default.
    """
    if profile not in PROFILE_DEFAULTS:
        raise AdapterNotAvailable(f"unknown profile {profile!r}; one of {', '.join(PROFILES)}")
    defaults = PROFILE_DEFAULTS[profile]
    if overrides is None:
        return defaults
    update = {
        name: getattr(overrides, name)
        for name in overrides.model_fields_set
        if getattr(overrides, name) is not None
    }
    return defaults.model_copy(update=update)


def _check_cloud(profile: Profile, spec: AdapterSpec) -> None:
    if profile != "cloud":
        return
    for port in PORTS:
        adapter = getattr(spec, port)
        if adapter in IN_MEMORY:
            raise AdapterNotAllowed(
                f"{port}: {adapter!r} is an in-memory double; profile 'cloud' runs real "
                "adapters only (use profile 'local' for a mix)"
            )


def _named(spec: AdapterSpec, port: str) -> str:
    adapter = getattr(spec, port)
    if not adapter:  # every profile default names every port, so this is a defaults bug
        raise AdapterNotAvailable(f"{port}: no adapter named")
    return str(adapter)


def build_ports(
    profile: Profile,
    overrides: AdapterSpec | None = None,
    *,
    connector: Lane = DEFAULT_LANE,
    agent: str | None = None,
) -> PortBundle:
    """Build the ports for a profile. `overrides` is the agent's own `spec.adapters`, merged over
    the profile defaults per field. `connector` is `spec.engine.connector`, the lane. `agent` is
    the agent name the model adapter tags its calls with.
    """
    spec = merge_adapters(profile, overrides)
    check_lane(connector, profile)
    _check_cloud(profile, spec)
    return PortBundle(
        model=resolve("model", _named(spec, "model"), agent=agent),  # type: ignore[arg-type]
        engine=resolve("engine", connector),  # type: ignore[arg-type]
        config=resolve("config", _named(spec, "config")),  # type: ignore[arg-type]
        telemetry=resolve("telemetry", _named(spec, "telemetry")),  # type: ignore[arg-type]
        tools=resolve("tools", _named(spec, "tools")),  # type: ignore[arg-type]
        state=resolve("state", _named(spec, "state")),  # type: ignore[arg-type]
        events=resolve("events", _named(spec, "events")),  # type: ignore[arg-type]
    )
