"""The PoC-5 harness pieces that later PoCs share: the socket wrappers, the pod probe runner, the
manifest readers, the workflow readers, and the drill's required variables. The kind suites use
them against a real cluster, so these checks are their only offline cover.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from poc_harness import kind
from poc_harness.drills import required_env
from poc_harness.kind import exec_probe, probe, tcp_check
from poc_harness.kind_controls import traceparent_headers
from poc_harness.lane_tokens import traceparent
from poc_harness.manifests import (
    container_secret_names,
    containers_of,
    load_manifests,
    secret_names_of,
)
from poc_harness.sockets import on_unix_socket, on_unix_socket_on_this_loop, socket_path
from poc_harness.workflows import (
    load_workflow,
    workflow_runs,
    workflow_steps,
    workflow_triggers,
)

MAX_SOCKET_PATH = 104
"""macOS caps a Unix socket path at 104 bytes."""


async def _hello(scope: dict[str, Any], receive: Any, send: Any) -> None:
    assert scope["type"] == "http"
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"hello"})


def _answer(uds: str) -> str:
    with httpx.Client(transport=httpx.HTTPTransport(uds=uds), base_url="http://app") as client:
        return client.get("/").text


def test_socket_path_is_short_and_its_folder_is_removed_on_exit() -> None:
    with socket_path("x.sock") as uds:
        folder = Path(uds).parent
        assert len(uds) < MAX_SOCKET_PATH
        assert folder.is_dir()
    assert not folder.exists()


def test_the_sync_wrapper_serves_the_app_and_removes_the_folder() -> None:
    with on_unix_socket(_hello, "a.sock") as uds:
        folder = Path(uds).parent
        assert len(uds) < MAX_SOCKET_PATH
        assert _answer(uds) == "hello"
    assert not folder.exists()


async def test_the_async_wrapper_serves_the_app_and_removes_the_folder() -> None:
    async with on_unix_socket_on_this_loop(_hello, "a.sock") as uds:
        folder = Path(uds).parent
        assert len(uds) < MAX_SOCKET_PATH
        transport = httpx.AsyncHTTPTransport(uds=uds)
        async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
            assert (await client.get("/")).text == "hello"
    assert not folder.exists()


def _fake_kubectl(
    monkeypatch: pytest.MonkeyPatch, *, stdout: str = "", stderr: str = "", code: int = 0
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def fake(*args: str, timeout: int = 0) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, code, stdout, stderr)

    monkeypatch.setattr(kind, "kubectl", fake)
    return calls


def test_exec_probe_runs_any_interpreter_and_gives_one_result_per_check_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks = [{"kind": "a"}, {"kind": "b"}]
    results = [{"seen": "a"}, {"seen": "b"}]
    calls = _fake_kubectl(monkeypatch, stdout=f"noise\n{json.dumps(results)}\n")

    got = exec_probe("ns", "pod-1", "box", ["node", "-e", "script"], checks)

    assert got == results
    assert calls == [
        ("exec", "-n", "ns", "pod-1", "-c", "box", "--", "node", "-e", "script", json.dumps(checks))
    ]


def test_exec_probe_fails_with_the_stderr_tail_on_a_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_kubectl(monkeypatch, stderr="x" * 1000 + "the tail", code=1)

    with pytest.raises(AssertionError, match="the tail") as failed:
        exec_probe("ns", "pod-1", "box", ["python"], [])

    assert "x" * 500 not in str(failed.value)


def test_exec_probe_fails_when_a_check_has_no_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_kubectl(monkeypatch, stdout="[]\n")

    with pytest.raises(AssertionError):
        exec_probe("ns", "pod-1", "box", ["python"], [{"kind": "a"}])


def test_probe_runs_the_shared_script_with_the_image_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_kubectl(monkeypatch, stdout='[{"exists": true}]\n')

    assert probe("ns", "pod-1", "box", [{"kind": "path"}]) == [{"exists": True}]

    assert calls[0][7:9] == ("python", "-c")


def test_tcp_check_builds_a_check_and_connects_nowhere() -> None:
    assert tcp_check("10.0.0.1", 80) == {"kind": "tcp", "host": "10.0.0.1", "port": 80}


def test_the_two_traceparent_helpers_differ_by_name_and_by_type() -> None:
    trace = "0" * 32
    value = traceparent(trace)
    assert value.startswith(f"00-{trace}-")
    assert traceparent_headers(trace)["traceparent"].startswith(f"00-{trace}-")


def test_load_manifests_keeps_mappings_only(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_text("kind: A\n---\n---\n- a list\n---\nkind: B\n")

    assert [d["kind"] for d in load_manifests(path)] == ["A", "B"]


SPEC: dict[str, Any] = {
    "initContainers": [{"name": "i", "envFrom": [{"secretRef": {"name": "from-init"}}]}],
    "containers": [
        {"name": "c", "env": [{"name": "E", "valueFrom": {"secretKeyRef": {"name": "from-env"}}}]}
    ],
    "ephemeralContainers": [{"name": "e"}],
    "volumes": [
        {"name": "v", "secret": {"secretName": "from-volume"}},  # pragma: allowlist secret
        {"name": "p", "projected": {"sources": [{"secret": {"name": "from-projected"}}]}},
    ],
}


def test_containers_of_lists_every_kind_of_container() -> None:
    assert [c["name"] for c in containers_of(SPEC)] == ["i", "c", "e"]


def test_secret_names_of_covers_env_env_from_and_volumes() -> None:
    assert set(secret_names_of(SPEC)) == {
        "from-init",
        "from-env",
        "from-volume",
        "from-projected",
    }


def test_container_secret_names_reads_one_container_only() -> None:
    assert list(container_secret_names(SPEC["containers"][0])) == ["from-env"]


def test_load_workflow_restores_the_on_key_and_the_readers_walk_every_job(tmp_path: Path) -> None:
    path = tmp_path / "w.yml"
    path.write_text(
        "on:\n  push:\n  pull_request:\n"
        "jobs:\n  a:\n    steps:\n      - run: one\n  b:\n    steps:\n      - run: two\n"
    )

    doc = load_workflow(path)

    assert workflow_triggers(doc) == {"push", "pull_request"}
    assert len(workflow_steps(doc)) == 2
    assert workflow_runs(doc) == "one\ntwo"


def test_required_env_returns_the_values_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRILL_A", "1")
    monkeypatch.setenv("DRILL_B", "2")

    assert required_env("DRILL_A", "DRILL_B") == ["1", "2"]


def test_required_env_fails_naming_each_missing_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRILL_A", "1")
    monkeypatch.delenv("DRILL_B", raising=False)
    monkeypatch.setenv("DRILL_C", "")

    with pytest.raises(pytest.fail.Exception, match="DRILL_B, DRILL_C") as failed:
        required_env("DRILL_A", "DRILL_B", "DRILL_C")

    assert "DRILL_A" not in str(failed.value)
