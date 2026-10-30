"""The bake-off tables read trust and lane from `bakeoff.registry`, and the kind helpers keep
their promises. Offline: kubectl is a fake."""

from __future__ import annotations

import subprocess
from dataclasses import fields
from typing import Any

import pytest
from bakeoff.registry import get_engine, lanes_for
from poc_harness import bakeoff_kind, kind
from poc_harness.bakeoff_kind import (
    CONTAINER_ERRORS,
    KindEngine,
    container_error_of,
    kind_engine,
    missing_profile_manifest,
    probe_node,
    probe_python,
)
from poc_harness.bakeoff_kind import (
    ENGINES as KIND_ENGINES,
)
from poc_harness.bakeoff_tasks import ENGINES, Engine
from poc_harness.kind import AGENTS_NS, REMOTE_NS, ExecFailed


def _done(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, stderr)


@pytest.mark.parametrize("engine", ENGINES.values(), ids=lambda e: e.name)
def test_the_task_table_has_the_registrys_trust_and_lane(engine: Engine) -> None:
    entry = get_engine(engine.name)
    assert (engine.trust == "trusted") is entry.trusted
    assert engine.lane in lanes_for(entry)
    assert (engine.lane == "remote") is (not entry.trusted)


def test_the_task_table_has_no_unread_column() -> None:
    assert "hooks_extra" not in {f.name for f in fields(Engine)}


@pytest.mark.parametrize("engine", KIND_ENGINES, ids=lambda e: e.id)
def test_the_kind_table_has_the_registrys_trust_and_a_lane_it_allows(engine: KindEngine) -> None:
    entry = get_engine(engine.name)
    assert (engine.trust == "trusted") is entry.trusted
    assert engine.lane in lanes_for(entry)
    assert engine.namespace == (REMOTE_NS if engine.lane == "remote" else AGENTS_NS)


def test_an_untrusted_engine_cannot_be_listed_outside_the_remote_lane() -> None:
    with pytest.raises(ValueError, match="untrusted"):
        kind_engine("echo-smolagents", "chassis", "pod", lane="sidecar")


def test_a_trusted_engine_must_name_the_lane_it_is_deployed_in() -> None:
    with pytest.raises(ValueError, match="name the lane"):
        kind_engine("echo-typescript", "chassis", "pod")


def test_probe_node_returns_one_result_per_check(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, ...]] = []

    def fake(*args: str, **_: Any) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return _done(0, '[{"ok": true}]\n')

    monkeypatch.setattr(kind, "kubectl", fake)
    assert probe_node("ns", "pod-1", "c", [{"kind": "x"}]) == [{"ok": True}]
    assert seen[0][seen[0].index("--") + 1] == "node"


def test_probe_python_tries_the_next_interpreter_when_one_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter([_done(1, stderr='exec: "python": executable file not found'), _done(0, "[{}]")])
    monkeypatch.setattr(kind, "kubectl", lambda *_, **__: next(answers))
    assert probe_python("ns", "pod-1", "c", [{"kind": "x"}]) == [{}]


def test_probe_python_fails_on_any_other_exec_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kind, "kubectl", lambda *_, **__: _done(1, stderr="pod is gone"))
    with pytest.raises(ExecFailed, match="pod is gone"):
        probe_python("ns", "pod-1", "c", [{"kind": "x"}])


def test_the_missing_profile_manifest_names_a_localhost_profile_no_node_has() -> None:
    manifest = missing_profile_manifest()
    profile = manifest["spec"]["securityContext"]["seccompProfile"]
    assert profile["type"] == "Localhost"
    assert "does-not-exist" in profile["localhostProfile"]


def test_container_error_of_returns_the_waiting_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    waiting = {"state": {"waiting": {"reason": sorted(CONTAINER_ERRORS)[0]}}}
    states = iter([{"status": {"phase": "Pending"}}, {"status": {"containerStatuses": [waiting]}}])
    monkeypatch.setattr(bakeoff_kind, "get_json", lambda *_: next(states))
    monkeypatch.setattr(bakeoff_kind, "POLL_S", 0)
    assert container_error_of("ns", "p") == sorted(CONTAINER_ERRORS)[0]


def test_container_error_of_fails_when_the_pod_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bakeoff_kind, "get_json", lambda *_: {"status": {"phase": "Running"}})
    with pytest.raises(AssertionError, match="ran without its profile"):
        container_error_of("ns", "p")
