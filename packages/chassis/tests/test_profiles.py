from __future__ import annotations

import pytest
from chassis.adapters.litellm import LiteLLMModel
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.profiles import (
    PROFILES,
    AdapterNotAvailable,
    AdapterSpec,
    LaneNotAllowed,
    build_ports,
    check_lane,
)


def test_fake_profile_builds_all_four_ports() -> None:
    ports = build_ports("fake")
    assert isinstance(ports.model, ScriptedModel)
    assert isinstance(ports.engine, FakeEngine)
    assert isinstance(ports.config, InMemoryConfig)
    assert isinstance(ports.telemetry, InMemoryTelemetry)


@pytest.mark.parametrize("profile", [p for p in PROFILES if p != "fake"])
def test_other_profiles_name_the_poc_that_adds_their_adapter(
    profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://router:4000/v1")
    with pytest.raises(AdapterNotAvailable, match="arrives in PoC"):
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


def test_overrides_pick_the_adapter_per_port() -> None:
    with pytest.raises(AdapterNotAvailable, match="config: adapter 'minio' arrives in PoC-4"):
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
