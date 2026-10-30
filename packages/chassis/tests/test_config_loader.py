"""`ConfigReloader` (PoC-4 plan, section 3): the first config, a reload on a `ConfigPort` callback,
and a refused document that leaves the last good config in place.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest
from chassis.fakes import InMemoryConfig, InMemoryState, InMemoryTelemetry
from chassis.ports.config import LoadedConfig
from chassis.schemas import SCHEMA_DIR
from chassis.server.app import close_ports
from chassis.server.config import ChassisConfig, IdempotencySpec, content_hash, load_config
from chassis.server.config_loader import (
    ConfigRejected,
    ConfigReloader,
    restart_required,
    uses_store,
)
from chassis.server.idempotency import Idempotency
from chassis.server.pipeline import RunPipeline
from chassis.server.readiness import not_ready_reason
from starlette.datastructures import State

DOC: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "adapters": {"config": "memory"},
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "fake-route"},
        "prompt": {"version": "p1"},
    },
}


def doc(**changes: Any) -> dict[str, Any]:
    """`DOC` with dotted paths set, e.g. `doc(**{"spec.limits.body_bytes_max": 10})`."""
    out = copy.deepcopy(DOC)
    for path, value in changes.items():
        *parents, leaf = path.split(".")
        node = out
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return out


def reloader(
    bootstrap: dict[str, Any] = DOC, *, from_store: bool | None = None, store: Any = None
) -> tuple[ConfigReloader, State, InMemoryConfig, InMemoryTelemetry]:
    port = store if store is not None else InMemoryConfig()
    telemetry = InMemoryTelemetry()
    state = State({"config": None, "config_loaded": False})
    loader = ConfigReloader(
        state, port, load_config(bootstrap), telemetry=telemetry, from_store=from_store
    )
    return loader, state, port, telemetry


def test_memory_adapter_means_the_bootstrap_is_the_config() -> None:
    assert not uses_store(load_config(DOC))
    assert uses_store(load_config(doc(**{"spec.adapters": {"config": "minio"}})))
    assert uses_store(load_config(doc(profile="local", **{"spec.adapters": {}})))


async def test_start_without_a_store_uses_the_bootstrap() -> None:
    loader, state, _, _ = reloader()
    await loader.start()
    assert state.config == load_config(DOC)
    assert state.config_loaded is True
    await loader.stop()


async def test_a_reloadable_change_swaps_the_config() -> None:
    loader, state, port, telemetry = reloader()
    await loader.start()
    await port.put(
        "echo",
        doc(
            version="cfg-2",
            **{
                "spec.limits": {"body_bytes_max": 10, "max_tokens_max": 5},
                "spec.model.route": "other-route",
                "spec.prompt.version": "p2",
                "spec.idempotency": {"ttl_s": 60},
            },
        ),
    )
    config: ChassisConfig = state.config
    assert config.version is not None
    assert config.version.startswith("cfg-2+") and len(config.version) == len("cfg-2+") + 12
    assert config.spec.limits.body_bytes_max == 10
    assert config.spec.model.route == "other-route"
    assert config.spec.prompt.version == "p2"
    assert config.spec.idempotency.ttl_s == 60
    assert telemetry.counter_value("chassis.config.reloaded") == 1
    assert loader.reloaded == 1
    await loader.stop()


async def test_pipeline_idempotency_uses_the_new_ttl_when_a_reload_changes_it() -> None:
    """R4-16: the pipeline's `Idempotency` takes the reloaded spec through `reload`."""
    # Arrange
    loader, state, port, telemetry = reloader()
    state.ports = SimpleNamespace(state=InMemoryState(), telemetry=telemetry)
    pipeline = RunPipeline(state)
    await loader.start()
    before = pipeline.idempotency
    # Act
    await port.put("echo", doc(**{"spec.idempotency": {"ttl_s": 60}}))
    # Assert
    assert pipeline.idempotency is before
    assert before.spec.ttl_s == 60
    await loader.stop()


def test_idempotency_reload_swaps_the_spec_when_called() -> None:
    """R4-16: `Idempotency.reload` is the typed way to give it a new `spec.idempotency`."""
    # Arrange
    idem = Idempotency(InMemoryState(), IdempotencySpec(), InMemoryTelemetry())
    # Act
    idem.reload(IdempotencySpec(ttl_s=60))
    # Assert
    assert idem.spec.ttl_s == 60


async def test_version_is_the_content_hash_when_the_document_names_none() -> None:
    loader, state, port, _ = reloader()
    await loader.start()
    data = doc(**{"spec.model.route": "r2"})
    del data["version"]
    await port.put("echo", data)
    assert state.config.version == content_hash(data)


@pytest.mark.parametrize(
    "bad",
    [
        doc(**{"spec.limits.body_bytes_max": 0}),
        doc(**{"spec.limits.typo": 1}),
        {k: v for k, v in DOC.items() if k != "agent"},
        doc(**{"spec.limits.max_tokens_max": "lots"}),
    ],
)
async def test_an_invalid_document_is_refused_and_the_last_good_stays(bad: dict[str, Any]) -> None:
    loader, state, port, telemetry = reloader()
    await loader.start()
    good = state.config
    await port.put("echo", bad)
    assert state.config is good
    assert telemetry.counter_value("chassis.config.rejected", reason="invalid") == 1
    assert loader.rejected == 1
    # The published schema refuses it too.
    schema = json.loads((SCHEMA_DIR / "chassis-config.v0.json").read_text())
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("profile", "local"),
        ("agent.version", "0.0.2"),
        ("agent.name", "other"),
        ("spec.adapters", {"config": "memory", "model": "litellm"}),
        ("spec.engine.handle", "other:handle"),
        ("spec.interfaces", {"openai": False}),
        ("spec.events", {"result_events": True}),
        ("spec.idempotency", {"enabled": False}),
        ("spec.idempotency", {"lease_s": 9}),
        ("spec.idempotency", {"wait_poll_ms": 7}),
    ],
)
async def test_a_restart_only_change_is_refused(path: str, value: Any) -> None:
    loader, state, port, telemetry = reloader()
    await loader.start()
    good = state.config
    await port.put("echo", doc(**{path: value, "spec.limits.body_bytes_max": 10}))
    assert state.config is good
    assert telemetry.counter_value("chassis.config.rejected", reason="restart_required") == 1
    logged = [e for e in telemetry.logs if e["message"] == "chassis.config.rejected"]
    assert logged and any(p.startswith(path.split(".")[0]) for p in logged[-1]["paths"])


def test_restart_required_lists_paths_outside_reloadable() -> None:
    old = load_config(DOC)
    new = load_config(doc(version="x", **{"agent.version": "2", "spec.limits.messages_max": 3}))
    assert restart_required(old, new) == ["agent.version"]


async def test_start_from_the_store_loads_and_checks_the_document() -> None:
    store = InMemoryConfig({"echo": doc(version="from-store", **{"spec.model.route": "r9"})})
    loader, state, _, _ = reloader(from_store=True, store=store)
    await loader.start()
    assert state.config.version.startswith("from-store+")
    assert state.config.spec.model.route == "r9"


async def test_start_fails_without_the_document() -> None:
    loader, state, _, _ = reloader(from_store=True)
    with pytest.raises(KeyError):
        await loader.start()
    assert state.config_loaded is False


async def test_start_fails_on_an_invalid_document_and_names_the_field_not_the_value() -> None:
    store = InMemoryConfig({"echo": doc(**{"spec.limits.body_bytes_max": "s3cr3t-value"})})
    loader, state, _, _ = reloader(from_store=True, store=store)
    with pytest.raises(ConfigRejected) as caught:
        await loader.start()
    assert "spec.limits.body_bytes_max" in str(caught.value)
    assert "s3cr3t-value" not in str(caught.value)
    assert state.config_loaded is False


async def test_start_fails_when_the_document_differs_on_a_restart_only_field() -> None:
    store = InMemoryConfig({"echo": doc(**{"agent.version": "9.9.9"})})
    loader, _, _, _ = reloader(from_store=True, store=store)
    with pytest.raises(ConfigRejected, match=r"agent\.version"):
        await loader.start()


async def test_stop_unsubscribes() -> None:
    loader, state, port, _ = reloader()
    await loader.start()
    await loader.stop()
    await port.put("echo", doc(**{"spec.model.route": "late"}))
    assert state.config.spec.model.route == "fake-route"


def test_ready_waits_for_the_first_config() -> None:
    state = SimpleNamespace(ready=True, draining=False, readiness=None, config_loaded=False)
    assert not_ready_reason(state) == "starting"
    state.config_loaded = True
    assert not_ready_reason(state) is None
    state.draining = True
    state.config_loaded = False
    assert not_ready_reason(state) == "draining"


async def test_a_failed_store_poll_is_counted_in_telemetry() -> None:
    from chassis.adapters.s3.config import S3Config

    s3 = S3Config(object())  # type: ignore[arg-type]  # no call reaches the client here
    assert s3.on_poll_failed is None
    loader, _, _, telemetry = reloader(store=s3, from_store=False)
    await loader.start()
    assert s3.on_poll_failed is not None
    s3._failed("echo", ConnectionError("store down"))
    assert telemetry.counter_value("chassis.config.poll_failed", reason="ConnectionError") == 1
    assert s3.poll_failures == 1
    await loader.stop()


async def test_the_version_names_the_content_even_when_the_document_is_not_bumped() -> None:
    """Two documents with the same `version` and other content report two versions, so two
    replicas on different content never report the same `versions.config`."""
    loader, state, port, _ = reloader()
    await loader.start()
    first = doc(version="cfg-2", **{"spec.model.route": "r1"})
    await port.put("echo", first)
    one = state.config.version
    assert one == f"cfg-2+{content_hash(first)}"
    second = doc(version="cfg-2", **{"spec.model.route": "r2"})
    await port.put("echo", second)
    assert state.config.version == f"cfg-2+{content_hash(second)}" != one
    await loader.stop()


class _Closing:
    def __init__(self, name: str, log: list[str], fail: bool = False) -> None:
        self.name, self.log, self.fail = name, log, fail

    async def aclose(self) -> None:
        self.log.append(self.name)
        if self.fail:
            raise RuntimeError(self.name)

    async def close(self) -> None:
        await self.aclose()


async def test_close_ports_closes_the_config_port_too() -> None:
    """S3Config holds a client: `close_ports` closes it, and each closer runs even when another
    raises."""
    log: list[str] = []
    bundle = SimpleNamespace(
        engine=_Closing("engine", log, fail=True),
        model=_Closing("model", log),
        state=_Closing("state", log),
        events=_Closing("events", log),
        config=_Closing("config", log, fail=True),
    )
    with pytest.raises(RuntimeError):
        await close_ports(bundle)  # type: ignore[arg-type]
    assert log == ["engine", "model", "state", "events", "config"]


@pytest.mark.parametrize(
    "idempotency",
    [{"ttl_s": float("inf")}, {"ttl_s": float("nan")}, {"ttl_s": 2_592_001}],
)
async def test_reloader_refuses_ttl_s_as_invalid_when_it_is_not_finite_or_past_the_cap(
    idempotency: dict[str, float],
) -> None:
    """R4-08: `spec.idempotency.ttl_s: .inf` (or past 30 days) is `invalid`; the last good
    config stays."""
    # Arrange
    loader, state, port, telemetry = reloader()
    await loader.start()
    good = state.config
    # Act
    await port.put("echo", doc(**{"spec.idempotency": idempotency}))
    # Assert
    assert state.config is good
    assert telemetry.counter_value("chassis.config.rejected", reason="invalid") == 1
    logged = [e for e in telemetry.logs if e["message"] == "chassis.config.rejected"]
    assert logged[-1]["paths"] == ["spec.idempotency.ttl_s"]
    await loader.stop()


@pytest.mark.parametrize("lease_s", [float("inf"), float("nan"), 3_601])
def test_idempotency_spec_refuses_lease_s_when_it_is_not_finite_or_past_the_cap(
    lease_s: float,
) -> None:
    """R4-08: `lease_s` has the same guard as `ttl_s`."""
    # Arrange
    data = {"lease_s": lease_s}
    # Act / Assert
    with pytest.raises(ValueError, match="lease_s"):
        IdempotencySpec.model_validate(data)


def test_idempotency_spec_accepts_the_caps_when_they_are_exactly_reached() -> None:
    """R4-08: the caps themselves are allowed."""
    # Arrange / Act
    spec = IdempotencySpec(ttl_s=2_592_000, lease_s=3_600)
    # Assert
    assert (spec.ttl_s, spec.lease_s) == (2_592_000, 3_600)


def _mixed_top_level() -> LoadedConfig:
    data: dict[Any, Any] = {**doc(), 1: "x", "foo": "y"}
    return LoadedConfig.model_construct(name="echo", version="2", data=data)


def _mixed_nested() -> LoadedConfig:
    data = doc()
    data["spec"][1] = "x"
    return LoadedConfig(name="echo", version="2", data=data)


def _too_deep() -> LoadedConfig:
    data = doc()
    node: dict[str, Any] = data["spec"]
    for _ in range(50_000):  # past the C recursion limit of `json.dumps`
        node["deep"] = {}
        node = node["deep"]
    return LoadedConfig.model_construct(name="echo", version="2", data=data)


@pytest.mark.parametrize("loaded", [_mixed_top_level, _mixed_nested, _too_deep])
async def test_reloader_rejects_as_invalid_when_the_document_cannot_be_hashed(
    loaded: Any,
) -> None:
    """R4-28: mixed key types (`1:` next to `foo:`) or a too-deep document count
    `chassis.config.rejected{reason=invalid}` and keep the active config."""
    # Arrange
    loader, state, _, telemetry = reloader()
    await loader.start()
    good = state.config
    # Act
    await loader.on_change(loaded())
    # Assert
    assert state.config is good
    assert telemetry.counter_value("chassis.config.rejected", reason="invalid") == 1
    await loader.stop()


async def test_reloader_refuses_restart_required_when_a_field_outside_reloadable_changes() -> None:
    """R4-20: a path in no list (`spec.idempotency.max_entry_bytes`) is restart-only."""
    # Arrange
    loader, state, port, telemetry = reloader()
    await loader.start()
    good = state.config
    # Act
    await port.put("echo", doc(**{"spec.idempotency": {"max_entry_bytes": 7}}))
    # Assert
    assert state.config is good
    assert telemetry.counter_value("chassis.config.rejected", reason="restart_required") == 1
    await loader.stop()


async def test_reloader_leaves_the_port_alone_when_it_does_not_poll() -> None:
    """R4-16: only a port that polls (`PollingConfig`) gets the poll-failed hook."""
    # Arrange
    loader, _, port, _ = reloader()
    # Act
    await loader.start()
    # Assert
    assert not hasattr(port, "on_poll_failed")
    await loader.stop()
