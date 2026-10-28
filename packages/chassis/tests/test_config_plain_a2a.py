"""`spec.engine.protocol` and `spec.engine.a2a` (contract v5 draft, part B.4): the plain-A2A mode
is opt-in, for `remote` only, restart-only, and absent from the other lanes' mapping.
"""

from __future__ import annotations

from typing import Any

import pytest
from chassis.server import ChassisConfig
from pydantic import ValidationError


def _config(engine: dict[str, Any]) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-plain",
            "profile": "fake",
            "agent": {"name": "simplifier", "version": "0.0.1"},
            "spec": {"adapters": {"model": "fake"}, "engine": engine},
        }
    )


REMOTE: dict[str, Any] = {
    "connector": "remote",
    "url": "http://remote.test:8080",
    "auth": {"scheme": "bearer", "token_env": "REMOTE_TOKEN"},
}


def test_protocol_defaults_to_chassis_and_a2a_is_unset() -> None:
    engine = _config(REMOTE).spec.engine
    assert engine.protocol == "chassis" and engine.a2a is None
    assert engine.as_mapping()["protocol"] == "chassis"
    assert "a2a" not in engine.as_mapping()


def test_the_a2a_block_has_its_defaults() -> None:
    engine = _config({**REMOTE, "protocol": "a2a", "a2a": {}}).spec.engine
    assert engine.a2a is not None
    assert engine.a2a.usage_key is None and engine.a2a.context_id == "omit"


def test_a_full_plain_mapping_reaches_setup() -> None:
    a2a = {"usage_key": "kagent.dev/a2a/usage", "context_id": "trace_id"}
    mapping = _config({**REMOTE, "protocol": "a2a", "a2a": a2a}).spec.engine.as_mapping()
    assert mapping["protocol"] == "a2a" and mapping["a2a"] == a2a


@pytest.mark.parametrize("lane", ["sidecar", "inprocess"])
def test_protocol_a2a_is_refused_outside_remote(lane: str) -> None:
    engine: dict[str, Any] = {"connector": lane, "protocol": "a2a"}
    if lane == "inprocess":
        engine["handle"] = "chassis.core.handle:echo_wire"
    with pytest.raises(ValidationError) as caught:
        _config(engine)
    assert (
        f"spec.engine.protocol: a2a is for spec.engine.connector: remote only (got '{lane}')"
        in (str(caught.value))
    )


def test_the_a2a_block_needs_protocol_a2a() -> None:
    with pytest.raises(
        ValidationError, match=r"spec\.engine\.a2a needs spec\.engine\.protocol: a2a"
    ):
        _config({**REMOTE, "a2a": {"usage_key": "x"}})


def test_an_unknown_protocol_is_refused() -> None:
    with pytest.raises(ValidationError, match="protocol"):
        _config({**REMOTE, "protocol": "grpc"})


def test_the_a2a_block_forbids_extra_keys_and_bad_values() -> None:
    with pytest.raises(ValidationError, match="extra"):
        _config({**REMOTE, "protocol": "a2a", "a2a": {"nope": 1}})
    with pytest.raises(ValidationError, match="context_id"):
        _config({**REMOTE, "protocol": "a2a", "a2a": {"context_id": "random"}})


def test_other_lanes_see_the_v4_mapping() -> None:
    mapping = _config({"connector": "sidecar", "url": "http://127.0.0.1:9100"}).spec.engine
    assert "protocol" not in mapping.as_mapping() and "a2a" not in mapping.as_mapping()
