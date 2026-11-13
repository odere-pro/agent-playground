"""CH-20 (issue 140): `spec.memory` and the auth-off refusal in the `cloud` profile."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from chassis.server.config import ChassisConfig, MemorySpec
from pydantic import ValidationError

LOCAL: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "local",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "fake-route"},
        "prompt": {"version": "p1"},
    },
}

CLOUD: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "cloud",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "trust": "trusted",
        "engine": {"connector": "sidecar"},
        "model": {"route": "fake-route"},
        "prompt": {"version": "p1"},
    },
}


def _with(base: dict[str, Any], **spec: Any) -> dict[str, Any]:
    doc = copy.deepcopy(base)
    doc["spec"].update(spec)
    return doc


def test_memory_defaults_are_off_one_day_and_256_kib() -> None:
    memory = MemorySpec()
    assert (memory.enabled, memory.ttl_s, memory.max_bytes) == (False, 86_400, 262_144)


def test_cloud_with_auth_off_refuses_memory_enabled_at_config_load() -> None:
    doc = _with(CLOUD, adapters={"auth": "none"}, memory={"enabled": True})
    with pytest.raises(ValidationError) as caught:
        ChassisConfig.model_validate(doc)
    assert "spec.memory.enabled" in str(caught.value)
    assert "auth" in str(caught.value)


def test_cloud_with_auth_off_and_memory_off_is_not_refused_for_memory() -> None:
    doc = _with(CLOUD, adapters={"auth": "none"})
    assert ChassisConfig.model_validate(doc).spec.memory.enabled is False


def test_cloud_with_a_real_authenticator_allows_memory() -> None:
    doc = _with(CLOUD, memory={"enabled": True})
    assert ChassisConfig.model_validate(doc).spec.memory.enabled is True


@pytest.mark.parametrize("profile", ["local", "fake"])
def test_local_and_fake_with_auth_off_allow_memory(profile: str) -> None:
    doc = _with(LOCAL, adapters={"auth": "none"}, memory={"enabled": True})
    doc["profile"] = profile
    config = ChassisConfig.model_validate(doc)
    assert config.spec.memory.enabled is True


@pytest.mark.parametrize("field", [{"ttl_s": 0}, {"max_bytes": 0}, {"unknown": 1}])
def test_a_bad_memory_value_is_refused(field: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ChassisConfig.model_validate(_with(LOCAL, memory=field))
