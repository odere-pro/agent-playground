from __future__ import annotations

import subprocess
import sys

import pytest
from chassis.adapters.a2a import InProcessConnector, SidecarConnector
from chassis.adapters.litellm import LiteLLMModel
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.state import InMemoryState
from chassis.fakes.tool import InMemoryTools
from chassis.ports.events import NoEvents
from chassis.profiles import (
    PROFILE_DEFAULTS,
    PROFILES,
    REGISTRY,
    AdapterNotAllowed,
    AdapterNotAvailable,
    AdapterSpec,
    LaneNotAllowed,
    build_ports,
    check_lane,
    lazy,
    merge_adapters,
)
from pydantic import ValidationError

NOT_HERE = "arrives in PoC|is not configured"
"""What a named adapter says offline: not built yet (its PoC), or built but missing its env."""
STORE_ENV = ("VALKEY_URL", "KAFKA_BOOTSTRAP_SERVERS", "CONFIG_S3_ENDPOINT", "DAPR_API_TOKEN")


def _no_store_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in STORE_ENV:
        monkeypatch.delenv(name, raising=False)


def test_fake_profile_builds_all_four_ports() -> None:
    ports = build_ports("fake", connector="inprocess")
    assert isinstance(ports.model, ScriptedModel)
    assert isinstance(ports.engine, InProcessConnector)
    assert isinstance(ports.config, InMemoryConfig)
    assert isinstance(ports.telemetry, InMemoryTelemetry)


def test_the_engine_is_spec_engine_connector_and_defaults_to_sidecar() -> None:
    """Contract v1 decision 2: the lane is named once, and `build_ports` builds it from
    `REGISTRY["engine"][connector]`. The default is `sidecar` (ADR-001 item 4).
    """
    assert isinstance(build_ports("fake").engine, SidecarConnector)
    assert isinstance(build_ports("fake", connector="inprocess").engine, InProcessConnector)


def test_fake_engine_is_not_a_registered_lane() -> None:
    assert "fake" not in REGISTRY["engine"]
    assert set(REGISTRY["engine"]) == {"inprocess", "sidecar", "remote"}


def test_adapters_engine_is_refused_and_names_the_one_field() -> None:
    with pytest.raises(ValidationError, match=r"spec\.engine\.connector"):
        AdapterSpec.model_validate({"model": "fake", "engine": "inprocess"})


def test_every_profile_default_names_every_port() -> None:
    for profile in PROFILES:
        spec = PROFILE_DEFAULTS[profile]
        assert None not in (
            spec.model,
            spec.config,
            spec.telemetry,
            spec.tools,
            spec.state,
            spec.events,
        ), profile


def test_adapters_merge_over_the_profile_defaults_per_field() -> None:
    merged = merge_adapters("local", AdapterSpec(model="fake", config="memory"))
    assert (merged.model, merged.config) == ("fake", "memory")
    assert (merged.telemetry, merged.tools) == ("otel", "mcp"), "unset fields keep the defaults"
    assert merge_adapters("local", AdapterSpec()) == PROFILE_DEFAULTS["local"]
    assert merge_adapters("local", None) == PROFILE_DEFAULTS["local"]


def test_an_explicit_null_keeps_the_default() -> None:
    merged = merge_adapters("fake", AdapterSpec.model_validate({"model": None}))
    assert merged.model == "fake"


def test_empty_adapters_in_cloud_does_not_build_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The review probe: `adapters: {}` in `cloud` built every fake. Now it is the `cloud`
    defaults, which name adapters that arrive in later PoCs.
    """
    monkeypatch.setenv("LITELLM_BASE_URL", "http://router:4000/v1")
    _no_store_env(monkeypatch)
    with pytest.raises(AdapterNotAvailable, match=NOT_HERE):
        build_ports("cloud", AdapterSpec())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model", "fake"),
        ("config", "memory"),
        ("telemetry", "memory"),
        ("tools", "fake"),
        ("state", "memory"),
        ("events", "memory"),
    ],
)
def test_cloud_refuses_a_fake_or_memory_adapter(field: str, value: str) -> None:
    with pytest.raises(AdapterNotAllowed, match=f"{field}: {value!r}"):
        build_ports("cloud", AdapterSpec.model_validate({field: value}))


def test_local_allows_any_mix() -> None:
    spec = AdapterSpec(
        model="fake", config="memory", telemetry="memory", tools="fake", state="memory"
    )
    ports = build_ports("local", spec, connector="inprocess")
    assert isinstance(ports.model, ScriptedModel) and isinstance(ports.tools, InMemoryTools)


def test_build_ports_refuses_inprocess_in_cloud() -> None:
    with pytest.raises(LaneNotAllowed, match="'inprocess'"):
        build_ports("cloud", connector="inprocess")


@pytest.mark.parametrize("profile", [p for p in PROFILES if p != "fake"])
def test_other_profiles_name_the_poc_that_adds_their_adapter(
    profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://router:4000/v1")
    _no_store_env(monkeypatch)
    with pytest.raises(AdapterNotAvailable, match=NOT_HERE):
        build_ports(profile)  # type: ignore[arg-type]


def test_litellm_needs_its_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    with pytest.raises(AdapterNotAvailable, match="model: litellm needs LITELLM_BASE_URL"):
        build_ports("fake", AdapterSpec(model="litellm"))


def test_litellm_carries_the_agent_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry hands the agent name to the model adapter, so the router call is tagged."""
    monkeypatch.setenv("LITELLM_BASE_URL", "http://router:4000/v1")
    ports = build_ports("fake", AdapterSpec(model="litellm"), agent="echo")
    assert isinstance(ports.model, LiteLLMModel)
    assert ports.model.agent == "echo"


def test_overrides_pick_the_adapter_per_port(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_store_env(monkeypatch)
    with pytest.raises(AdapterNotAvailable, match=f"config: adapter 'minio' ({NOT_HERE})"):
        build_ports("fake", AdapterSpec(config="minio"))


def test_unknown_adapter_is_a_clear_error() -> None:
    with pytest.raises(AdapterNotAvailable, match="no adapter named 'gpt'"):
        build_ports("fake", AdapterSpec(model="gpt"))


@pytest.mark.parametrize("profile", ["fake", "local"])
def test_inprocess_allowed_in_fake_and_local(profile: str) -> None:
    check_lane("inprocess", profile)  # type: ignore[arg-type]


def test_inprocess_refused_in_cloud() -> None:
    with pytest.raises(LaneNotAllowed, match="'inprocess' is for the chassis's own tests"):
        check_lane("inprocess", "cloud")


@pytest.mark.parametrize("lane", ["sidecar", "remote"])
def test_other_lanes_allowed_everywhere(lane: str) -> None:
    for profile in PROFILES:
        check_lane(lane, profile)  # type: ignore[arg-type]


def test_fake_profile_builds_in_memory_state_and_no_events() -> None:
    ports = build_ports("fake", connector="inprocess")
    assert isinstance(ports.state, InMemoryState)
    assert isinstance(ports.events, NoEvents)


def test_poc04_defaults_state_valkey_off_fake_and_events_none_everywhere() -> None:
    assert [PROFILE_DEFAULTS[p].state for p in PROFILES] == ["memory", "valkey", "valkey"]
    assert [PROFILE_DEFAULTS[p].events for p in PROFILES] == ["none", "none", "none"]


def test_poc04_registry_names_every_adapter() -> None:
    assert set(REGISTRY["state"]) == {"memory", "valkey"}
    assert set(REGISTRY["events"]) == {"none", "memory", "kafka", "dapr"}
    assert set(REGISTRY["config"]) == {"memory", "minio", "s3"}


def test_cloud_allows_no_events() -> None:
    """`none` is not an in-memory double, so `cloud` runs it."""
    spec = merge_adapters("cloud", AdapterSpec(events="none"))
    assert spec.events == "none"


@pytest.mark.parametrize(
    ("field", "value"),
    [("state", "valkey"), ("events", "memory"), ("events", "kafka"), ("events", "dapr")],
)
def test_a_poc04_adapter_offline_is_not_available(
    field: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before its wave-1 package lands it names PoC-4; after, it needs its env, which is unset."""
    _no_store_env(monkeypatch)
    try:
        ports = build_ports("fake", AdapterSpec.model_validate({field: value}))
    except AdapterNotAvailable as exc:
        assert f"{field}: adapter {value!r}" in str(exc)
    else:  # only the in-memory bus builds with no env, once it exists
        assert (field, value) == ("events", "memory"), ports


def test_a_lazy_adapter_whose_module_is_missing_names_poc_4() -> None:
    factory = lazy("state", "nope", "chassis.adapters.nope.state:Nope.from_env")
    with pytest.raises(AdapterNotAvailable, match="state: adapter 'nope' arrives in PoC-4"):
        factory()


def test_a_lazy_adapter_missing_its_env_is_not_configured() -> None:
    factory = lazy("state", "env", "os:environ.__getitem__")
    with pytest.raises(AdapterNotAvailable, match="state: adapter 'env' is not configured"):
        factory("CHASSIS_TEST_UNSET_VARIABLE")


def test_importing_profiles_loads_no_store_sdk() -> None:
    """The lazy factories import an adapter only when it is named, so an unused SDK costs no
    memory in a replica.
    """
    code = (
        "import sys, chassis.profiles, chassis.server.app\n"
        "loaded = {'valkey', 'aiokafka', 'minio'} & set(sys.modules)\n"
        "assert not loaded, loaded\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
