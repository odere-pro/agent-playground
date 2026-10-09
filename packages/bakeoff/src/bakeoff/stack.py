"""One engine in one lane as real localhost processes.

    fake model server (a uvicorn thread of this process, so `app.state.calls` can be read)
      <- chassis serve (a process; the `local` profile's `litellm` adapter, fake tools on /mcp)
      <- workload (a process, unless the lane is `inprocess`)

Every child gets an environment built from scratch (`bakeoff.envs`) and runs in its own process
group, killed on exit (`bakeoff.procs`).
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fake_model_server import Script, create_app

from bakeoff.config import (
    chassis_argv,
    chassis_config,
    chassis_env_vars,
    dump_config,
    workload_argv,
    workload_env_vars,
)
from bakeoff.envs import build_env
from bakeoff.procs import Cleanup, Spawned, free_port, non_loopback_ip
from bakeoff.registry import ROOT, TS_MAIN, Engine, Lane, check_lane

__all__ = ["FakeModel", "Stack", "StackError", "running_stack"]

START_TIMEOUT_S = 90.0


class StackError(RuntimeError):
    """A process did not start or did not become ready. The message is safe to print."""


class FakeModel:
    """The fake model server in a uvicorn thread of this process."""

    def __init__(self, script: Path, port: int):
        self.port = port
        self.app = create_app(Script.from_yaml(script))
        self._server = uvicorn.Server(
            uvicorn.Config(
                self.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"
            )
        )
        self._thread = threading.Thread(target=self._server.run, name="fake-model", daemon=True)

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise StackError("the fake model server did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)

    @property
    def calls_total(self) -> int:
        total: int = self.app.state.calls_total
        return total

    def mark(self) -> int:
        return self.calls_total

    def since(self, mark: int) -> list[dict[str, Any]]:
        """The request bodies recorded after `mark` (a former `calls_total`)."""
        new = self.calls_total - mark
        if new <= 0:
            return []
        recorded: list[dict[str, Any]] = list(self.app.state.calls)
        return recorded[-new:]


@dataclass
class Stack:
    url: str
    """The chassis's public listener."""
    fake: FakeModel


def _wait_ready(url: str, procs: list[Spawned], secrets_: list[str]) -> None:
    deadline = time.monotonic() + START_TIMEOUT_S
    with httpx.Client(trust_env=False, timeout=2.0) as client:
        while time.monotonic() < deadline:
            for child in procs:
                if not child.alive():
                    raise StackError(
                        f"{child.name} exited ({child.proc.returncode}): {child.tail(secrets_)}"
                    )
            try:
                if client.get(f"{url}/ready").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise StackError("the chassis was not ready in time (/ready)")


@contextmanager
def running_stack(engine: Engine, lane: Lane, cleanup: Cleanup) -> Iterator[Stack]:
    """Start the processes for `engine` in `lane`; stop and remove everything on exit."""
    check_lane(engine, lane)
    remote_ip: str | None = None
    if lane == "remote":
        remote_ip = non_loopback_ip()
        if remote_ip is None:
            raise StackError("no non-loopback IP address for the remote proxy listener")
    work = cleanup.tempdir()
    home = work / "home"
    home.mkdir()
    token = secrets.token_hex(16)
    fake = FakeModel(ROOT / engine.script, free_port())
    children: list[Spawned] = []
    try:
        fake.start()
        port, proxy_port, remote_port, workload_port = (free_port() for _ in range(4))
        if lane == "remote":
            model_url = f"http://{remote_ip}:{remote_port}/v1"
            tool_url = f"http://{remote_ip}:{remote_port}/mcp"
        else:
            model_url = f"http://127.0.0.1:{proxy_port}/v1"
            tool_url = f"http://127.0.0.1:{proxy_port}/mcp"
        if lane != "inprocess":
            main_js = str(ROOT / TS_MAIN)
            workload = cleanup.spawn(
                engine.name,
                workload_argv(engine, lane, port=workload_port, main_js=main_js),
                build_env(
                    home,
                    workload_env_vars(
                        engine,
                        lane,
                        port=workload_port,
                        model_url=model_url,
                        tool_url=tool_url,
                        token=token,
                    ),
                ),
                ROOT / TS_MAIN.parents[2] if engine.runner == "node" else ROOT,
                work / "workload.log",
            )
            children.append(workload)
        config = chassis_config(
            engine, lane, workload_port=None if lane == "inprocess" else workload_port
        )
        config_path = work / "chassis.yaml"
        config_path.write_text(dump_config(config))
        chassis = cleanup.spawn(
            "chassis",
            chassis_argv(
                str(config_path),
                port=port,
                proxy_port=proxy_port,
                remote_ip=remote_ip,
                remote_port=remote_port if remote_ip else None,
            ),
            build_env(
                home,
                chassis_env_vars(lane, fake_port=fake.port, proxy_port=proxy_port, token=token),
            ),
            ROOT,
            work / "chassis.log",
        )
        children.append(chassis)
        _wait_ready(f"http://127.0.0.1:{port}", children, [token])
        yield Stack(f"http://127.0.0.1:{port}", fake)
    finally:
        for child in reversed(children):
            cleanup.release(child)
        fake.stop()
        cleanup.remove_dir(work)
