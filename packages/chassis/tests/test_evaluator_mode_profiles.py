"""137 CH-17: the effective `spec.harness.evaluator_mode` in each profile and bundled config.

With the gate on and no mode named, every profile loads `async`. A named `sync` is kept (opt-in).
No bundled config in `packages/chassis/configs/` loads to `sync`.

Driven in process, no socket.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args

import pytest
from chassis.profiles import Profile
from chassis.server.config import load_config

PROFILES: tuple[Profile, ...] = get_args(Profile)
BUNDLED = Path(__file__).resolve().parents[1] / "configs"


def _document(profile: Profile, harness: dict[str, Any] | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "engine": (
            {"connector": "sidecar"}
            if profile == "cloud"
            else {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}
        ),
        "model": {"route": "big-default"},
    }
    if profile == "cloud":
        spec["trust"] = "trusted"
    if harness is not None:
        spec["harness"] = harness
    return {"profile": profile, "agent": {"name": "echo", "version": "0.0.1"}, "spec": spec}


def test_every_profile_is_covered() -> None:
    assert set(PROFILES) == {"fake", "local", "cloud"}


@pytest.mark.parametrize("profile", PROFILES)
def test_the_gate_on_with_no_mode_loads_async(profile: Profile) -> None:
    document = _document(profile, {"evaluator_gate": True})

    harness = load_config(document).spec.harness

    assert harness.evaluator_mode == "async"


@pytest.mark.parametrize("profile", PROFILES)
def test_a_named_sync_mode_is_kept(profile: Profile) -> None:
    document = _document(profile, {"evaluator_gate": True, "evaluator_mode": "sync"})

    harness = load_config(document).spec.harness

    assert harness.evaluator_mode == "sync"


@pytest.mark.parametrize("path", sorted(BUNDLED.glob("*.yaml")), ids=lambda p: p.name)
def test_no_bundled_config_loads_sync(path: Path) -> None:
    config = load_config(path)

    mode = config.spec.harness.evaluator_mode

    assert mode != "sync", path.name
