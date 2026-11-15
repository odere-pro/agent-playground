"""The dev hot reload, live (016 H-9): a saved change in `src/` answers on `/v1/run` with no rebuild
and no restart of the stack.

A drill: needs Docker and the chassis image `agent-platform/chassis:poc09` (the rendered
`deploy/compose/README.md`, "Images"), and builds the workload and the fake model server.
`DEV_RELOAD_LIVE=1` turns it on:

    DEV_RELOAD_LIVE=1 uv run pytest -s packages/agentctl/tests/test_agentctl_dev_reload_live.py

`agentctl new` renders a repo into `tmp_path`, never into this checkout. The stack gets its own
Compose project, agent name, and free host ports, and goes down with `down -v` in a `finally`.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from agentctl.scaffold import Answers, new_agent

ROOT = Path(__file__).resolve().parents[3]
CHANGE_DEADLINE_S = 20.0
"""suggested: 20 s from the save to the first `/v1/run` that shows the change."""
POLL_S = 0.5
UP_TIMEOUT_S = 900.0
CALL_TIMEOUT_S = 10.0
START = 'yield _event("start", request_id=str(ctx.get("request_id", "")))'
SITE = "/app/.venv/lib/python3.12/site-packages"

pytestmark = pytest.mark.requires_env(
    "DEV_RELOAD_LIVE", hint="needs Docker and the agent-platform/chassis:poc09 image"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Stack:
    """One `dev` profile stack of a rendered repo, under its own Compose project."""

    def __init__(self, repo: Path, project: str) -> None:
        self.repo, self.project = repo, project
        self.port = _free_port()
        self.env_ports = {
            "CHASSIS_PORT": str(self.port),
            "CHASSIS_DEBUG_PORT": str(_free_port()),
            "WORKLOAD_DEBUG_PORT": str(_free_port()),
        }

    def compose(self, *args: str, timeout: float = 120.0) -> str:
        command = ["docker", "compose", "-p", self.project]
        command += ["-f", "deploy/compose/docker-compose.yaml", "--profile", "dev", *args]
        env = {**os.environ, **self.env_ports}
        done = subprocess.run(
            command, cwd=self.repo, env=env, capture_output=True, text=True, timeout=timeout
        )
        assert done.returncode == 0, f"{' '.join(args)}: {done.stdout}\n{done.stderr}"
        return done.stdout

    def started_at(self, service: str) -> tuple[str, str]:
        """The container's id and `State.StartedAt`: both stay put unless it is recreated or
        restarted."""
        [container] = self.compose("ps", "-q", service).split()
        done = subprocess.run(
            ["docker", "inspect", "-f", "{{.Id}} {{.State.StartedAt}}", container],
            capture_output=True,
            text=True,
            check=True,
        )
        cid, started = done.stdout.split()
        return cid[:12], started

    def run(self, text: str) -> str:
        response = httpx.post(
            f"http://127.0.0.1:{self.port}/v1/run",
            json={"input": {"text": text}},
            timeout=CALL_TIMEOUT_S,
        )
        response.raise_for_status()
        return response.text


@pytest.fixture
def stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Stack]:
    monkeypatch.setenv("UV_OFFLINE", "1")
    suffix = uuid.uuid4().hex[:6]
    answers = Answers(
        agent_name=f"reload-live-{suffix}",
        kind="transformer",
        framework="python",
        connector="sidecar",
        platform_path=ROOT,
    )
    repo = new_agent(answers, tmp_path)
    live = Stack(repo, f"dev-reload-live-{suffix}")
    began = time.monotonic()
    try:
        live.compose("up", "-d", "--wait", "--build", timeout=UP_TIMEOUT_S)
        print(f"\nup --wait --build: {time.monotonic() - began:.1f} s")
        yield live
    finally:
        print(live.compose("logs", "--no-color", "--tail", "200", "workload-dev"))
        live.compose("down", "-v", "--remove-orphans", timeout=300.0)
        for image in (
            f"{answers.agent_name}:dev",
            f"{answers.agent_name}-watchfiles-1.3.0-debugpy:1.8.22",
        ):
            subprocess.run(["docker", "image", "rm", image], capture_output=True, check=False)


def _wait_for(stack: Stack, marker: str, deadline_s: float) -> float:
    """Seconds from now to the first `/v1/run` whose answer has `marker`. A call cut off while the
    child restarts, or a 503 from `/ready`, counts as not yet."""
    began = time.monotonic()
    while time.monotonic() - began < deadline_s:
        try:
            if marker in stack.run("hello"):
                return time.monotonic() - began
        except httpx.HTTPError:
            pass
        time.sleep(POLL_S)
    raise AssertionError(f"no answer with {marker!r} within {deadline_s} s")


def test_a_saved_change_in_src_is_live_with_no_rebuild_or_restart(stack: Stack) -> None:
    handle = stack.repo / "src" / stack.repo.name.replace("-", "_") / "handle.py"
    marker = f"reloaded-{uuid.uuid4().hex[:8]}"
    before = stack.run("hello")
    assert json.loads(before), "the original handle answers"
    assert marker not in before
    chassis, workload = stack.started_at("chassis-fake"), stack.started_at("workload-dev")

    source = handle.read_text()
    assert source.count(START) == 1
    handle.write_text(source.replace(START, f'{START}\n    yield _event("delta", text="{marker}")'))
    mounted = stack.compose(
        "exec", "-T", "workload-dev", "cat", f"{SITE}/{handle.parent.name}/handle.py"
    )
    assert marker in mounted, "the bind mount shows the saved file in the container"
    seen_after = _wait_for(stack, marker, CHANGE_DEADLINE_S)
    print(f"change seen after: {seen_after:.1f} s")

    assert stack.started_at("chassis-fake") == chassis, "the chassis was restarted"
    assert stack.started_at("workload-dev") == workload, "the workload container was restarted"
    assert marker in stack.run("hello"), "the change stays live"
    print(f"chassis-fake {chassis}, workload-dev {workload}: unchanged")
