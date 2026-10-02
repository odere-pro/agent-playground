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
from chassis.fakes import InMemoryConfig, InMemoryTelemetry
from chassis.schemas import SCHEMA_DIR
from chassis.server.app import close_ports
from chassis.server.config import ChassisConfig, content_hash, load_config
from chassis.server.config_loader import (
    ConfigRejected,
    ConfigReloader,
    restart_required,
    uses_store,
)
from chassis.server.readiness import not_ready_reason

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
) -> tuple[ConfigReloader, SimpleNamespace, InMemoryConfig, InMemoryTelemetry]:
    port = store if store is not None else InMemoryConfig()
    telemetry = InMemoryTelemetry()
    state = SimpleNamespace(config=None, config_loaded=False)
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


async def test_the_idempotency_layer_gets_the_new_spec() -> None:
    loader, state, port, _ = reloader()
    state.idempotency = SimpleNamespace(spec=None)
    await loader.start()
    await port.put("echo", doc(**{"spec.idempotency": {"ttl_s": 60}}))
    assert state.idempotency.spec.ttl_s == 60


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
