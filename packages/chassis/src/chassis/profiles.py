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

The `cloud` profile also refuses, before any adapter is built (so nothing connects), each with
`AdapterNotAllowed` that names the variable and never its value:
- `auth: none`, which would run `/v1/run` as the anonymous principal (review R2 note);
- `CONFIG_S3_SECURE` off when `config` is `s3` or `minio`, and a `VALKEY_URL` that is not
  `rediss://`, `valkeys://`, or `unix://`, or whose query turns off the certificate or host
  name check, when `state` is `valkey` (review R4-30, MEDIUM 9), unless
  `CHASSIS_ALLOW_PLAINTEXT_STORES=1` (suggested: the name and the value), which logs a warning;
- a URL that is not `https`: `LITELLM_MCP_URL` when `tools` is `mcp` (review R5a-25),
  `LITELLM_BASE_URL` when `model` or `guardrails` is `litellm` (HIGH 3), and `OIDC_JWKS_URL`
  when `auth` is `oidc` (MEDIUM 9). No loopback or in-cluster name and no override passes;
  outside `cloud` the adapters' own checks apply. A URL that does not parse is refused too.

The lanes and the LiteLLM model are lazy too: `inprocess` and `sidecar` are `lazy` entries, and
`_litellm_from_env` imports `chassis.adapters.litellm` inside the call so its error text stays
its own. Importing `chassis.profiles` loads no A2A or LiteLLM adapter module.

Adapters with an SDK are lazy (`lazy`): the factory imports its module only when the adapter is
named, so an unused SDK (valkey, aiokafka, minio) is never loaded. A missing module or SDK is
"adapter X is unavailable (no module Y)"; an adapter whose `from_env` raises `LookupError` is
"not configured".

`engine: remote` is
`chassis.adapters.a2a.remote:RemoteConnector` (built with no arguments, set up with
`spec.engine` like the other lanes), `tools: mcp` is
`chassis.adapters.mcp.gateway:McpGatewayTools.from_env`.

PoC-7 adds four ports, each with `none` (off, the `fake` and `local` default) and a fake: `auth`
(`static`, `oidc`), `guardrails` (`fake`, `litellm`), `evaluator` (`fake`, `judge`, `encoder`),
`feedback` (`memory`, `langfuse`). `telemetry: otel` is `OtelTelemetry.from_env`. The real ones
are lazy.
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import SplitResult, parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, model_validator

from chassis.fakes.config import InMemoryConfig
from chassis.fakes.evaluator import ScriptedEvaluator
from chassis.fakes.feedback import InMemoryFeedback
from chassis.fakes.guardrail import KeywordGuardrails
from chassis.fakes.model import ScriptedModel
from chassis.fakes.state import InMemoryState
from chassis.fakes.telemetry import InMemoryTelemetry
from chassis.fakes.tool import default_tools
from chassis.ports.auth import NoAuth
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import LANES, Lane
from chassis.ports.evaluator import NoEvaluator
from chassis.ports.events import NoEvents
from chassis.ports.feedback import NoFeedback
from chassis.ports.guardrail import NoGuardrails

log = logging.getLogger(__name__)

Profile = Literal["fake", "local", "cloud"]
PROFILES: tuple[Profile, ...] = ("fake", "local", "cloud")
PORTS: tuple[str, ...] = (
    "model",
    "config",
    "telemetry",
    "tools",
    "state",
    "events",
    "auth",
    "guardrails",
    "evaluator",
    "feedback",
)
"""The ports `spec.adapters` names. The engine is not one of them: `spec.engine.connector`."""
DEFAULT_LANE: Lane = "sidecar"
"""ADR-001 item 4. `spec.engine.connector` defaults to it too (`chassis.server.config`)."""
IN_MEMORY = frozenset({"fake", "memory", "static"})
"""suggested: the adapter names the `cloud` profile refuses."""

# The adapters import their SDK (minio, valkey, FastMCP, httpx, pyjwt) at module level, so
# these names are copies, not imports. `test_profiles_cloud_transport.py` checks they match.
S3_SECURE_VAR = "CONFIG_S3_SECURE"
"""Read by `chassis.adapters.s3.config.S3Config.from_env` (`config: s3` and `config: minio`)."""
VALKEY_URL_VAR = "VALKEY_URL"
"""`chassis.adapters.valkey.state.URL_VAR`."""
LITELLM_MCP_URL_VAR = "LITELLM_MCP_URL"
"""`chassis.adapters.mcp.gateway.URL_VAR`."""
LITELLM_BASE_URL_VAR = "LITELLM_BASE_URL"
"""`chassis.adapters.litellm.client.BASE_URL_VAR`, read by the model and guardrails adapters."""
OIDC_JWKS_URL_VAR = "OIDC_JWKS_URL"
"""`chassis.adapters.oidc.JWKS_URL_VAR`."""
ALLOW_PLAINTEXT_STORES_VAR = "CHASSIS_ALLOW_PLAINTEXT_STORES"
"""suggested: set to `1` to let the `cloud` profile reach S3 and Valkey without TLS."""
S3_CONFIGS = frozenset({"s3", "minio"})
"""The `config` adapters that read `CONFIG_S3_SECURE`."""
_S3_OFF = frozenset({"0", "false", "no", "off"})
"""The values `S3Config.from_env` reads as `secure=False` (its `_flag`)."""
VALKEY_TLS_SCHEMES = frozenset({"rediss", "valkeys", "unix"})
"""TLS, or a local socket that never crosses a network."""
_VALKEY_FALSE = frozenset({"0", "f", "false", "n", "no"})
"""The query values valkey-py reads as false (its `to_bool`)."""


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
    auth: str | None = None
    """PoC-7: `none`, `static` (fake), or `oidc`."""
    guardrails: str | None = None
    """PoC-7: `none`, `fake`, or `litellm`."""
    evaluator: str | None = None
    """PoC-7: `none`, `fake`, `judge`, or `encoder`."""
    feedback: str | None = None
    """PoC-7: `none`, `memory`, or `langfuse`."""

    @model_validator(mode="before")
    @classmethod
    def _no_engine(cls, data: Any) -> Any:
        if isinstance(data, dict) and "engine" in data:
            raise ValueError(
                "spec.adapters.engine is removed: the lane is named once, in "
                "spec.engine.connector (contract v1, decision 2)"
            )
        return data


_OPTIONAL_OFF = {"auth": "none", "guardrails": "none", "evaluator": "none", "feedback": "none"}
"""suggested: the four optional ports (auth, guardrails, evaluator, feedback) are off in `fake`
and `local` until an agent names them."""

PROFILE_DEFAULTS: dict[Profile, AdapterSpec] = {
    "fake": AdapterSpec(
        model="fake",
        config="memory",
        telemetry="memory",
        tools="fake",
        state="memory",
        events="none",
        **_OPTIONAL_OFF,
    ),
    "local": AdapterSpec(
        model="litellm",
        config="minio",
        telemetry="otel",
        tools="mcp",
        state="valkey",
        events="none",
        **_OPTIONAL_OFF,
    ),
    "cloud": AdapterSpec(
        model="litellm",
        config="s3",
        telemetry="otel",
        tools="mcp",
        state="valkey",
        events="none",
        auth="oidc",
        guardrails="litellm",
        evaluator="none",
        feedback="langfuse",
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
    from chassis.adapters.litellm import LiteLLMModel  # lazy: loaded only when it is named

    try:
        return LiteLLMModel.from_env(agent=agent)
    except LookupError as exc:
        raise AdapterNotAvailable(str(exc)) from exc


def lazy(port: str, adapter: str, target: str) -> Factory:
    """A factory that imports `target` (`module:attr`, `attr` may be dotted, as in
    `Class.from_env`) only when called, then calls it with the factory's arguments.

    A `ModuleNotFoundError` (the adapter module or its SDK is not there) becomes
    `AdapterNotAvailable("<port>: adapter '<name>' is unavailable (no module '<module>')")`.
    A `LookupError` from the target (a required environment variable is unset) becomes
    `AdapterNotAvailable("<port>: adapter '<name>' is not configured: <message>")`.
    """
    module_name, _, attr = target.partition(":")

    def factory(*args: object, **kw: object) -> object:
        try:
            found: Any = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            raise AdapterNotAvailable(
                f"{port}: adapter {adapter!r} is unavailable (no module {exc.name!r})"
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
        "inprocess": lazy(
            "engine",
            "inprocess",
            "chassis.adapters.a2a:InProcessConnector",
        ),
        "sidecar": lazy("engine", "sidecar", "chassis.adapters.a2a:SidecarConnector"),
        "remote": lazy("engine", "remote", "chassis.adapters.a2a.remote:RemoteConnector"),
    },
    "config": {
        "memory": InMemoryConfig,
        "minio": lazy("config", "minio", "chassis.adapters.s3.config:S3Config.from_env"),
        "s3": lazy("config", "s3", "chassis.adapters.s3.config:S3Config.from_env"),
    },
    "telemetry": {
        "memory": InMemoryTelemetry,
        "otel": lazy("telemetry", "otel", "chassis.adapters.otel:OtelTelemetry.from_env"),
    },
    "tools": {
        "fake": default_tools,
        "mcp": lazy(
            "tools",
            "mcp",
            "chassis.adapters.mcp.gateway:McpGatewayTools.from_env",
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
    "auth": {
        "none": NoAuth,
        "static": lazy("auth", "static", "chassis.fakes.auth:StaticTokens.from_env"),
        "oidc": lazy("auth", "oidc", "chassis.adapters.oidc:OidcAuth.from_env"),
    },
    "guardrails": {
        "none": NoGuardrails,
        "fake": KeywordGuardrails,
        "litellm": lazy(
            "guardrails",
            "litellm",
            "chassis.adapters.litellm.guardrails:LiteLLMGuardrails.from_env",
        ),
    },
    "evaluator": {
        "none": NoEvaluator,
        "fake": ScriptedEvaluator,
        "judge": lazy(
            "evaluator",
            "judge",
            "chassis.adapters.evaluators:JudgeEvaluator.from_env",
        ),
        "encoder": lazy(
            "evaluator",
            "encoder",
            "chassis.adapters.evaluators:EncoderEvaluator.from_env",
        ),
    },
    "feedback": {
        "none": NoFeedback,
        "memory": InMemoryFeedback,
        "langfuse": lazy(
            "feedback",
            "langfuse",
            "chassis.adapters.langfuse:LangfuseFeedback.from_env",
        ),
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
    _check_cloud_auth(spec)
    _check_store_tls(spec)
    _check_https("tools", LITELLM_MCP_URL_VAR, spec.tools == "mcp")
    _check_https("model", LITELLM_BASE_URL_VAR, spec.model == "litellm")
    _check_https("guardrails", LITELLM_BASE_URL_VAR, spec.guardrails == "litellm")
    _check_https("auth", OIDC_JWKS_URL_VAR, spec.auth == "oidc")


def _check_cloud_auth(spec: AdapterSpec) -> None:
    if spec.auth == "none":
        raise AdapterNotAllowed(
            "auth: 'none' runs every call as the anonymous principal; profile 'cloud' needs a "
            "real authenticator"
        )


def _url_parts(var: str, url: str) -> SplitResult:
    """`urlsplit(url)`. Its `ValueError` echoes part of the URL, so it becomes an
    `AdapterNotAllowed` that names only `var` (review LOW 10)."""
    try:
        parts = urlsplit(url)
        _ = parts.hostname
    except ValueError:
        raise AdapterNotAllowed(f"{var} is not a valid URL") from None
    return parts


def _valkey_without_tls(url: str) -> bool:
    """Not `rediss`, `valkeys`, or `unix`; or a query that turns off the certificate check
    (`ssl_cert_reqs` other than `required`) or the host name check (`ssl_check_hostname`
    false, as valkey-py reads it). Names and values are compared case-insensitively."""
    parts = _url_parts(VALKEY_URL_VAR, url)
    if parts.scheme not in VALKEY_TLS_SCHEMES:
        return True
    query = {k.lower(): v.strip().lower() for k, v in parse_qsl(parts.query)}
    if query.get("ssl_cert_reqs", "required") != "required":
        return True
    return query.get("ssl_check_hostname", "true") in _VALKEY_FALSE


def _plaintext_stores(spec: AdapterSpec) -> list[str]:
    """The variables that send a store's traffic without TLS. Names only, never values."""
    found: list[str] = []
    secure = os.environ.get(S3_SECURE_VAR, "true").strip().lower()
    if spec.config in S3_CONFIGS and secure in _S3_OFF:
        found.append(S3_SECURE_VAR)
    url = os.environ.get(VALKEY_URL_VAR)  # unset: the adapter names it as "not configured"
    if spec.state == "valkey" and url and _valkey_without_tls(url):
        found.append(VALKEY_URL_VAR)
    return found


def _check_store_tls(spec: AdapterSpec) -> None:
    """S3 and Valkey without TLS are refused, unless the override is set; then each is logged
    as a warning (review LOW 12)."""
    found = _plaintext_stores(spec)
    if not found:
        return
    names = ", ".join(found)
    if os.environ.get(ALLOW_PLAINTEXT_STORES_VAR) == "1":
        log.warning(
            "profile 'cloud': %s=1 lets a store run without TLS (%s)",
            ALLOW_PLAINTEXT_STORES_VAR,
            names,
        )
        return
    raise AdapterNotAllowed(
        f"{names} turns TLS off; profile 'cloud' needs TLS to its stores "
        f"(set {ALLOW_PLAINTEXT_STORES_VAR}=1 to allow it)"
    )


def _check_https(port: str, var: str, applies: bool) -> None:
    """`var` carries a credential (the LiteLLM key) or the keys that check one (the JWKS), so
    `cloud` needs `https` with no loopback, in-cluster, or override exemption."""
    url = os.environ.get(var)  # unset: the adapter names it as "not configured"
    if applies and url and _url_parts(var, url).scheme != "https":
        raise AdapterNotAllowed(f"{port}: profile 'cloud' needs an https {var}")


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
        auth=resolve("auth", _named(spec, "auth")),  # type: ignore[arg-type]
        guardrails=resolve("guardrails", _named(spec, "guardrails")),  # type: ignore[arg-type]
        evaluator=resolve("evaluator", _named(spec, "evaluator")),  # type: ignore[arg-type]
        feedback=resolve("feedback", _named(spec, "feedback")),  # type: ignore[arg-type]
    )
