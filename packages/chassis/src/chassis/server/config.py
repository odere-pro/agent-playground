"""`ChassisConfig`: what `chassis serve` reads. Loaded from a YAML file or a dict."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from chassis.ports.engine import Lane
from chassis.profiles import DEFAULT_LANE, AdapterSpec, Profile, check_lane


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str


Trust = Literal["trusted", "untrusted"]
"""`spec.trust` (PoC-5, plan section 2.5): `untrusted` code runs only in the `remote` lane."""
DEFAULT_TRUST: Trust = "trusted"
"""suggested: the default in the `fake` and `local` profiles. The `cloud` profile has none."""
ENV_NAME = r"^[A-Z_][A-Z0-9_]*$"
"""An environment variable name: the config names the variable, never holds the token."""


class EngineAuthSpec(BaseModel):
    """`spec.engine.auth` (PoC-5, plan section 2.2): the per-remote credential, by reference.

    The token is read from `os.environ[token_env]` at connector setup; the config never holds it.
    `previous_token_env` is the token being rotated out: the chassis's remote listener accepts
    either, the connector sends `token_env`. suggested: every name; `scheme` leaves room for
    `sigv4` and `google` (PoC-6), only `bearer` now.
    """

    model_config = ConfigDict(extra="forbid")
    scheme: Literal["bearer"] = "bearer"
    token_env: str = Field(pattern=ENV_NAME)
    previous_token_env: str | None = Field(default=None, pattern=ENV_NAME)


Protocol = Literal["chassis", "a2a"]
"""`spec.engine.protocol` (contract v5 draft, B.4): how the `remote` connector reads the agent."""


class PlainA2ASpec(BaseModel):
    """`spec.engine.a2a`: options of the plain-A2A mode. suggested: every name and default."""

    model_config = ConfigDict(extra="forbid")
    usage_key: str | None = None
    """The metadata key that holds token usage; a flat lookup. Null: usage is not read."""
    context_id: Literal["omit", "trace_id"] = "omit"
    """`omit`: the request has no `context_id`. `trace_id`: the run's trace id."""


class EngineSpec(BaseModel):
    """`spec.engine`: the lane and whatever the connector needs, passed as is to `setup`.

    `connector` is the lane, named once (contract v1, decision 2); it defaults to `sidecar`
    (ADR-001 item 4), so an `inprocess` config names it. Restart-only.
    """

    model_config = ConfigDict(extra="allow")
    connector: Lane = DEFAULT_LANE
    handle: str | None = None
    """`inprocess`: the workload's `handle` as `module:attribute`."""
    url: str | None = None
    """`sidecar`: the workload's A2A server, `http://127.0.0.1:<port>` (loopback only).
    `remote` (required): `http(s)://host:port[/path]`, no user info, query, or fragment;
    `https` in the `cloud` profile."""
    uds: str | None = None
    """`sidecar` or `remote`: a Unix socket path to reach that server over instead of TCP.
    Refused for `remote` in the `cloud` profile."""
    auth: EngineAuthSpec | None = None
    """`remote` (required): the bearer token by variable name. Refused in the other lanes."""
    probe_timeout_s: float = Field(default=2.0, gt=0)
    """`remote`: the readiness probe's timeout; the probe crosses the network. suggested: 2.0."""
    protocol: Protocol = "chassis"
    """`remote` only for `a2a`: read the agent's own A2A stream, which has no `chassis.event`
    metadata (a third-party agent). suggested: the name. Restart-only."""
    a2a: PlainA2ASpec | None = None
    """Options of `protocol: a2a`. Refused while `protocol` is `chassis`."""

    @model_validator(mode="after")
    def _remote_fields(self) -> EngineSpec:
        if self.protocol == "a2a" and self.connector != "remote":
            raise ValueError(
                f"spec.engine.protocol: a2a is for spec.engine.connector: remote only "
                f"(got {self.connector!r})"
            )
        if self.a2a is not None and self.protocol != "a2a":
            raise ValueError("spec.engine.a2a needs spec.engine.protocol: a2a")
        if self.connector != "remote":
            if self.auth is not None:
                raise ValueError(
                    f"spec.engine.auth is for spec.engine.connector: remote only; "
                    f"remove it for {self.connector!r}"
                )
            return self
        if self.auth is None:
            raise ValueError(
                "spec.engine.connector: remote needs spec.engine.auth "
                "({scheme: bearer, token_env: <NAME>})"
            )
        if not self.url:
            raise ValueError("spec.engine.connector: remote needs spec.engine.url")
        check_remote_url(self.url)
        return self

    def as_mapping(self) -> dict[str, Any]:
        """What the connector's `setup` reads. `probe_timeout_s` only for `remote`, so the other
        lanes see the same mapping as before PoC-5. `protocol` and `a2a` likewise."""
        mapping = self.model_dump(exclude_none=True)
        if self.connector != "remote":
            mapping.pop("probe_timeout_s", None)
            mapping.pop("protocol", None)
            mapping.pop("a2a", None)
        return mapping


def check_remote_url(url: str) -> None:
    """`spec.engine.url` for `remote`: `http` or `https`, a host, a port, an optional path; no
    user info, query, or fragment. The message names the field, never the URL's parts beyond it.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"spec.engine.url is not a valid URL: {exc}") from exc
    problems = []
    if parts.scheme not in ("http", "https"):
        problems.append("the scheme must be http or https")
    if not parts.hostname:
        problems.append("it needs a host")
    if port is None:
        problems.append("it needs a port")
    if parts.username is not None or parts.password is not None:
        problems.append("it must not hold user info")
    if parts.query or "?" in url:
        problems.append("it must not have a query")
    if parts.fragment or "#" in url:
        problems.append("it must not have a fragment")
    if problems:
        raise ValueError(
            "spec.engine.url for spec.engine.connector: remote must be http(s)://host:port[/path]: "
            + "; ".join(problems)
        )


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: str = "fake-route"


class PromptSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str | None = None


class InterfacesSpec(BaseModel):
    """`spec.interfaces`: which public interfaces are mounted besides native `/v1/run`, which is
    always on. suggested: each on by default (PoC-3 open note, section 8). One that is off is not
    mounted and not listed.
    """

    model_config = ConfigDict(extra="forbid")
    openai: bool = True
    anthropic: bool = True
    mcp: bool = True


class LimitsSpec(BaseModel):
    """`spec.limits`: what one call on a public interface may ask for. The interfaces have no auth
    yet (PoC-8), so the caller sets the run budget; these bound it. A value above a ceiling
    is refused (400 `limit_exceeded` in the format's shape), never clamped; a budget below 1 is
    refused earlier (422 on native, 400 `invalid_body` on the chat formats); a body over
    `body_bytes_max` is 413 before it is read (`chassis.server.interfaces.limits`).

    suggested: every name and default.
    - `max_tokens_max: 8000`: the most `budget.max_tokens` (OpenAI `max_completion_tokens` or
      `max_tokens`, Anthropic `max_tokens`) one run may ask for.
    - `timeout_ms_max: 120000`: the most `budget.timeout_ms`.
    - `body_bytes_max: 1048576`: the largest request body, 1 MiB.
    - `messages_max: 256`: the most chat `messages`, and on native the most turns
      (`input.data.history` plus the input), per call.
    - `uncorrelated_tokens_per_minute: 20000` (PoC-5, plan section 2.12, H16): the per-replica
      token bucket for model calls on the loopback model proxy that name no run in flight; past
      it, 429 `budget_exhausted`. `0` refuses every uncorrelated call. Reloadable.
    """

    model_config = ConfigDict(extra="forbid")
    max_tokens_max: int = Field(default=8000, ge=1)
    timeout_ms_max: int = Field(default=120_000, ge=1)
    body_bytes_max: int = Field(default=1_048_576, ge=1)
    messages_max: int = Field(default=256, ge=1)
    uncorrelated_tokens_per_minute: int = Field(default=20_000, ge=0)


class IdempotencySpec(BaseModel):
    """`spec.idempotency` (PoC-4, 018 H-18): the cache of a finished run under a key the client
    sent, in `StatePort`. suggested: every name and default.
    - `enabled: true`. Restart-only.
    - `ttl_s: 86400`: a cached result lives one day. Reloadable.
    - `lease_s: 5`: a claim expires 5 s after its owner stops renewing it. Restart-only.
    - `wait_poll_ms: 100`: how often a duplicate in flight checks for the first result.
    - `max_entry_bytes: 1048576`: a bigger result is not cached.
    """

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    ttl_s: float = Field(default=86_400, gt=0)
    lease_s: float = Field(default=5, gt=0)
    wait_poll_ms: int = Field(default=100, ge=1)
    max_entry_bytes: int = Field(default=1_048_576, ge=1)


class EventsConsumeSpec(BaseModel):
    """`spec.events.consume`: event-triggered runs (PoC-4 P9, optional). suggested: the names."""

    model_config = ConfigDict(extra="forbid")
    topic: str = "agents.task.requested.v1"
    group: str | None = None
    """The consumer group. `None` is the agent name (suggested)."""


class EventsSpec(BaseModel):
    """`spec.events` (PoC-4, 019 H-17). Restart-only. The adapter is `spec.adapters.events`.
    - `result_events: false`: publish `agents.task.completed.v1` or `agents.task.failed.v1` after
      each run.
    - `consume`: run each event from a topic. Not built yet (PoC-4 P9 was optional and was not
      done): a non-null value is refused, never ignored. The type stays so the published schema
      does not change when the consumer arrives (019 H-17).
    """

    model_config = ConfigDict(extra="forbid")
    result_events: bool = False
    consume: EventsConsumeSpec | None = None

    @model_validator(mode="after")
    def _refuse_consume(self) -> EventsSpec:
        if self.consume is not None:
            raise ValueError(
                "spec.events.consume is set, but event-triggered runs are not built yet "
                "(backlog 019 H-17); remove spec.events.consume or set it to null"
            )
        return self


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trust: Trust = DEFAULT_TRUST
    """PoC-5: `trusted` or `untrusted`. `untrusted` needs `spec.engine.connector: remote`.
    Defaults to `trusted` in `fake` and `local`; the `cloud` profile must name it. Restart-only."""
    adapters: AdapterSpec | None = None
    """Merged over the profile defaults per field. No `engine`: that is `spec.engine.connector`."""
    engine: EngineSpec = Field(default_factory=EngineSpec)
    model: ModelSpec = Field(default_factory=ModelSpec)
    prompt: PromptSpec = Field(default_factory=PromptSpec)
    interfaces: InterfacesSpec = Field(default_factory=InterfacesSpec)
    limits: LimitsSpec = Field(default_factory=LimitsSpec)
    idempotency: IdempotencySpec = Field(default_factory=IdempotencySpec)
    events: EventsSpec = Field(default_factory=EventsSpec)


class ChassisConfig(BaseModel):
    """The whole config. `version` is the file's content hash when the file does not name one."""

    model_config = ConfigDict(extra="forbid")
    version: str | None = None
    profile: Profile
    agent: AgentSpec
    spec: Spec = Field(default_factory=Spec)

    @model_validator(mode="after")
    def _lane_fits_profile(self) -> ChassisConfig:
        engine = self.spec.engine
        check_lane(engine.connector, self.profile)
        if self.profile == "cloud" and "trust" not in self.spec.model_fields_set:
            # PoC-5, plan section 2.5: the `cloud` profile has no `spec.trust` default.
            raise ValueError(
                "spec.trust is required in profile 'cloud': set it to trusted or untrusted"
            )
        if self.spec.trust == "untrusted" and engine.connector != "remote":
            raise ValueError(
                "spec.trust: untrusted needs spec.engine.connector: remote "
                f"(got {engine.connector!r})"
            )
        if self.profile == "cloud" and engine.connector == "remote":
            if engine.url and urlsplit(engine.url).scheme != "https":
                raise ValueError(
                    "spec.engine.url for spec.engine.connector: remote must use https in "
                    "profile 'cloud'"
                )
            if engine.uds is not None:
                raise ValueError(
                    "spec.engine.uds is refused for spec.engine.connector: remote in profile "
                    "'cloud'"
                )
        return self


RELOADABLE: tuple[str, ...] = (
    "version",
    "spec.limits",  # includes PoC-5's `spec.limits.uncorrelated_tokens_per_minute`
    "spec.model.route",
    "spec.prompt",
    "spec.idempotency.ttl_s",
)
"""The field paths a config reload may change (PoC-4 plan, section 3). A change anywhere else is
refused with `restart_required` and the last good config stays.
"""
RESTART_ONLY: tuple[str, ...] = (
    "profile",
    "agent",
    "spec.trust",
    "spec.adapters",
    "spec.engine",
    "spec.interfaces",
    "spec.events",
    "spec.idempotency.enabled",
    "spec.idempotency.lease_s",
)
"""The field paths the plan names as restart-only. Not the complete list: any path outside
`RELOADABLE` (for example `spec.idempotency.wait_poll_ms`) is refused too.
"""


def content_hash(data: Mapping[str, Any] | str | bytes) -> str:
    """A short, stable hash of the config content."""
    if isinstance(data, Mapping):
        raw = json.dumps(data, sort_keys=True, default=str).encode()
    elif isinstance(data, str):
        raw = data.encode()
    else:
        raw = data
    return hashlib.sha256(raw).hexdigest()[:12]


def load_config(source: str | Path | Mapping[str, Any]) -> ChassisConfig:
    """Load from a YAML path or a dict. Fills `version` with the content hash when missing."""
    if isinstance(source, Mapping):
        data = dict(source)
        raw: str | Mapping[str, Any] = data
    else:
        text = Path(source).read_text()
        data = yaml.safe_load(text) or {}
        raw = text
    if not isinstance(data, dict):
        raise ValueError("config must be a mapping")
    data.setdefault("version", content_hash(raw))
    return ChassisConfig.model_validate(data)
