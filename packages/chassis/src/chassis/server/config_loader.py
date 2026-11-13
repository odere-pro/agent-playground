"""The config loader: the agent config from the store, checked, and reloaded without a restart
(PoC-4 plan, section 3).

**Where the config lives.** The bootstrap file (`chassis serve --config`) is a whole
`ChassisConfig`: it names the profile, the agent, `spec.adapters`, and `spec.engine`, so the
chassis knows how to reach the store. With `spec.adapters.config: memory` (after the profile
defaults) there is no store document: the bootstrap is the config, and a reload comes from
`InMemoryConfig.put`. With any other config adapter, the store document `<agent.name>` (S3:
`agents/<agent.name>.yaml`) is a whole `ChassisConfig` too, and a missing one fails startup.

**One live config.** `state.config` is the active `ChassisConfig`. A reload swaps it in one
assignment, so every reader that reads `state.config` per request sees the new one on its next
request, and a run keeps the `Context` it opened with. The pipeline's `Idempotency` takes the
new `spec.idempotency` through `Idempotency.reload` on its next use (only `ttl_s` may differ).

**What may change.** Only the paths in `RELOADABLE` (`chassis.server.config`). A document that
changes any other leaf path is refused with `restart_required`. The bootstrap file's values at
`CEILINGS` (the per-call `spec.limits`, `spec.idempotency.ttl_s`) bound every store document: a
value above one is refused with `over_ceiling`, at start and on a reload.

**`start()`** loads, validates, and checks the store document against the bootstrap (or takes
the bootstrap), sets `state.config` and `state.config_loaded`, then subscribes. An invalid
document fails startup with `ConfigRejected`, which names the failing fields (Pydantic's `loc`),
never a value.

**`on_change(loaded)`** never raises. Accepted: `chassis.config.reloaded` and a log with the old
and new `version`. Refused: the active config stays, `chassis.config.rejected{reason}` (`invalid`,
`restart_required`, or `over_ceiling`), and a log with the field paths. suggested: the counter
and log names.

**`version`** of a document is its own `version` field when set, else `content_hash` of the parsed
document, so every replica that reads the same document reports the same `versions.config`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import ValidationError
from starlette.datastructures import State

from chassis.ports.config import ConfigPort, LoadedConfig, PollingConfig, Unsubscribe
from chassis.ports.telemetry import TelemetryPort
from chassis.profiles import merge_adapters
from chassis.server.config import RELOADABLE, ChassisConfig, content_hash, credential_paths

__all__ = [
    "CEILINGS",
    "REJECTED",
    "RELOADED",
    "ConfigRejected",
    "ConfigReloader",
    "changed_paths",
    "over_ceiling",
    "parse_document",
    "restart_required",
    "uses_store",
]

log = logging.getLogger("chassis.config")

RELOADED = "chassis.config.reloaded"
REJECTED = "chassis.config.rejected"

RejectReason = Literal["invalid", "restart_required", "over_ceiling"]

_REASON_TEXT: dict[str, str] = {
    "invalid": "is invalid at",
    "restart_required": "changes restart-only fields",
    "over_ceiling": "raises a bootstrap ceiling at",
}


POLL_FAILED = "chassis.config.poll_failed"
"""suggested: a failed poll of the config store, labeled with the error class."""


class ConfigRejected(ValueError):
    """A store document the chassis will not run with. The message names field paths only."""

    def __init__(self, reason: RejectReason, paths: list[str]) -> None:
        self.reason: RejectReason = reason
        self.paths = paths
        what = _REASON_TEXT[reason]
        super().__init__(f"config document {what}: {', '.join(paths) or '(document)'}")


def uses_store(bootstrap: ChassisConfig) -> bool:
    """True unless the config adapter (after the profile defaults) is `memory`."""
    return merge_adapters(bootstrap.profile, bootstrap.spec.adapters).config != "memory"


def _invalid_paths(exc: ValidationError) -> list[str]:
    paths = {".".join(str(part) for part in error["loc"]) for error in exc.errors()}
    return sorted(paths)


def parse_document(data: Mapping[str, Any]) -> ChassisConfig:
    """Validate a store document. The reported `version` always names the content: `<version>+
    <content hash>` when the document sets a `version`, the content hash (12 hex) alone when it
    does not. So a document whose content changed without a bump of its `version` still reports
    a new `versions.config`. Raises `ConfigRejected("invalid", paths)`; the paths are Pydantic's
    `loc`, never a value. A document the hash cannot read (mixed key types such as `1:` next to
    `foo:`, or nesting too deep) is `invalid` with no paths.
    """
    if not isinstance(data, Mapping):
        raise ConfigRejected("invalid", [])
    try:
        digest = content_hash(data)
        secret_paths = credential_paths(data)
        if secret_paths:
            # H-12: refused before validation, so the paths name the field, never the value.
            raise ConfigRejected("invalid", secret_paths)
        doc = dict(data)
        named = doc.get("version")
        doc["version"] = digest if named is None else f"{named}+{digest}"
        return ChassisConfig.model_validate(doc)
    except ValidationError as exc:
        raise ConfigRejected("invalid", _invalid_paths(exc)) from None
    except (TypeError, RecursionError):
        raise ConfigRejected("invalid", []) from None


def _leaves(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, Mapping) and value:
        out: dict[str, Any] = {}
        for key, item in value.items():
            out.update(_leaves(item, f"{prefix}.{key}" if prefix else str(key)))
        return out
    return {prefix: value}


def changed_paths(old: ChassisConfig, new: ChassisConfig) -> list[str]:
    """Every leaf path whose value differs between `old` and `new`."""
    a = _leaves(old.model_dump(mode="json"))
    b = _leaves(new.model_dump(mode="json"))
    return sorted(path for path in a.keys() | b.keys() if a.get(path) != b.get(path))


def _reloadable(path: str) -> bool:
    return any(path == root or path.startswith(f"{root}.") for root in RELOADABLE)


def restart_required(old: ChassisConfig, new: ChassisConfig) -> list[str]:
    """The changed leaf paths outside `RELOADABLE`."""
    return [path for path in changed_paths(old, new) if not _reloadable(path)]


CEILINGS: tuple[str, ...] = (
    "spec.limits.max_tokens_max",
    "spec.limits.timeout_ms_max",
    "spec.limits.body_bytes_max",
    "spec.limits.messages_max",
    "spec.idempotency.ttl_s",
)
"""suggested (H-12): the paths whose bootstrap values bound every store document: what one call
may ask for, and how long a cached result lives. A value may go down on a reload, never above
the bootstrap file's. Capacity settings (`max_in_flight`, the rate limits) are not ceilings."""


def over_ceiling(bootstrap: ChassisConfig, new: ChassisConfig) -> list[str]:
    """The `CEILINGS` paths where `new` is above the bootstrap value."""
    bound = _leaves(bootstrap.model_dump(mode="json"))
    value = _leaves(new.model_dump(mode="json"))
    return [path for path in CEILINGS if value[path] > bound[path]]


class ConfigReloader:
    """Holds `state.config` (module docstring). `from_store` defaults to `uses_store(bootstrap)`."""

    def __init__(
        self,
        state: State,
        port: ConfigPort,
        bootstrap: ChassisConfig,
        *,
        telemetry: TelemetryPort,
        from_store: bool | None = None,
    ) -> None:
        self._state = state
        self._port = port
        self.bootstrap = bootstrap
        self._telemetry = telemetry
        self.from_store = uses_store(bootstrap) if from_store is None else from_store
        self.reloaded = 0
        self.rejected = 0
        self._unsubscribe: Unsubscribe | None = None

    @property
    def name(self) -> str:
        return self.bootstrap.agent.name

    def _check(self, data: Mapping[str, Any], active: ChassisConfig) -> ChassisConfig:
        new = parse_document(data)
        paths = restart_required(active, new)
        if paths:
            raise ConfigRejected("restart_required", paths)
        ceilings = over_ceiling(self.bootstrap, new)
        if ceilings:
            raise ConfigRejected("over_ceiling", ceilings)
        return new

    def _activate(self, config: ChassisConfig) -> None:
        self._state.config = config  # one assignment: the swap

    async def start(self) -> None:
        """Set the first config, then subscribe. Raises `ConfigRejected`, `ConfigNotFound`, or
        `ConfigUnavailable`; on a raise `state.config_loaded` stays false.
        """
        if self.from_store:
            loaded = await self._port.load(self.name)
            first = self._check(loaded.data, self.bootstrap)
        else:
            first = self.bootstrap
        self._activate(first)
        self._state.config_loaded = True
        self._count_poll_failures()
        log.info("config loaded: version=%s", first.version)
        self._unsubscribe = self._port.subscribe(self.name, self.on_change)

    def _count_poll_failures(self) -> None:
        """A port that polls (`PollingConfig`, such as `S3Config`) and has no `on_poll_failed`
        hook yet gets one that counts `chassis.config.poll_failed` in telemetry. Other ports are
        left alone. `profiles.py` builds the port before telemetry exists, so the hook is set
        here, not at construction."""
        port = self._port
        if not isinstance(port, PollingConfig) or port.on_poll_failed is not None:
            return
        telemetry = self._telemetry

        def count(name: str, exc: BaseException) -> None:
            telemetry.counter(POLL_FAILED, reason=type(exc).__name__)

        port.on_poll_failed = count

    async def stop(self) -> None:
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        if unsubscribe is not None:
            unsubscribe()

    async def on_change(self, loaded: LoadedConfig) -> None:
        """Swap in the new document, or refuse it and keep the active one. Never raises."""
        active: ChassisConfig = self._state.config
        try:
            new = self._check(loaded.data, active)
        except ConfigRejected as exc:
            self.rejected += 1
            self._telemetry.counter(REJECTED, reason=exc.reason)
            self._telemetry.log(
                "warning",
                REJECTED,
                reason=exc.reason,
                paths=exc.paths,
                version=active.version,
                store_version=loaded.version,
            )
            log.warning(
                "config refused, keeping version=%s: reason=%s paths=%s",
                active.version,
                exc.reason,
                ",".join(exc.paths),
            )
            return
        self._activate(new)
        self.reloaded += 1
        self._telemetry.counter(RELOADED)
        self._telemetry.log("info", RELOADED, old=active.version, new=new.version)
        log.info("config reloaded: version %s -> %s", active.version, new.version)
