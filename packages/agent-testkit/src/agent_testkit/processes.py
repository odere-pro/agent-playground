"""The two processes the kit starts: the workload and the chassis, each its own process.

`start_workload(ref)` runs `python -m workload_a2a serve --handle <ref> --name <name> --uds <sock>`
with the interpreter running pytest (the workload's environment), and `PYTHONPATH` led by a guard
folder whose `chassis` and `chassis_contracts` packages raise `ImportError`: if anything in the
workload process imports the chassis, the workload fails to start and the fixture says so.

`start_chassis(workload, source)` writes a `fake`-profile config with `spec.engine.connector:
sidecar` and `spec.engine.uds` set to the workload's socket, and runs the chassis from its own
environment (`settings.ChassisSource`): `<source> -I data/chassis_serve.py --uds <sock> serve
--config <file> --port <p> --proxy-port <q>`. The public listener is on a Unix socket (the offline
gate refuses TCP in the test process); the model proxy is on loopback TCP, which only the workload
process calls (`CHASSIS_MODEL_URL`, `CHASSIS_TOOL_URL`).

Both are stopped with SIGTERM, then SIGKILL after `STOP_TIMEOUT_S`. Each process writes its output
to a log file next to its socket; a start that fails quotes the log tail.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from agent_testkit.settings import AgentRef, ChassisSource

DATA = Path(__file__).parent / "data"
CHASSIS_SERVE = DATA / "chassis_serve.py"
CHASSIS_CONFIG = DATA / "chassis-fake-sidecar.json"

BASE_URL = "http://127.0.0.1:9000"
"""What the chassis config and the clients name. Over a Unix socket it only fills `Host`."""
CARD_PATH = "/.well-known/agent-card.json"

WORKLOAD_START_S = 30.0
"""suggested: how long the workload may take to serve its agent card."""
CHASSIS_START_S = 120.0
"""suggested: how long the chassis may take to say ready; the first run builds its environment."""
STOP_TIMEOUT_S = 10.0
"""suggested: how long a process may take to exit after SIGTERM before it is killed."""
POLL_S = 0.1
LOG_TAIL = 4000
"""How many characters of a process log a failed start quotes."""
SECRET_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
"""Variables never passed to either process: the fake profile needs no key."""
UV_VARIABLES = ("UV_NO_SYNC", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
"""Variables of the caller's uv project that must not reach the chassis's own environment."""

_GUARD = """raise ImportError(
    "{name} is not importable here: the workload's environment never holds the chassis "
    "(ADR-002, agent-testkit)"
)
"""


class StartError(RuntimeError):
    """A process did not come up; the message quotes its log."""


@dataclass
class Process:
    """One started process, its log, and its scratch folder."""

    name: str
    popen: subprocess.Popen[bytes]
    log: Path

    def log_tail(self) -> str:
        try:
            return self.log.read_text(errors="replace")[-LOG_TAIL:]
        except OSError:
            return "<no log>"

    def stop(self) -> int | None:
        """SIGTERM, then SIGKILL after `STOP_TIMEOUT_S`. The exit code, or None if killed."""
        if self.popen.poll() is not None:
            return self.popen.returncode
        self.popen.send_signal(signal.SIGTERM)
        try:
            return self.popen.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self.popen.kill()
            self.popen.wait()
            return None


@dataclass
class Workload:
    """The workload's A2A server. `url` fills `Host`; requests go over `uds`."""

    ref: AgentRef
    url: str
    uds: str
    proxy_port: int
    """The port the chassis's model proxy must listen on: the workload was told it at start."""
    process: Process = field(repr=False)

    def client(self, **kw: Any) -> httpx.Client:
        return _client(self.uds, self.url, **kw)

    def async_client(self, **kw: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=self.uds),
            base_url=self.url,
            trust_env=False,
            **kw,
        )

    def agent_card(self) -> dict[str, Any]:
        with self.client() as http:
            response = http.get(CARD_PATH)
            response.raise_for_status()
            card: dict[str, Any] = response.json()
            return card


@dataclass
class Chassis:
    """The chassis in the `fake` profile, in front of a `Workload`."""

    url: str
    uds: str
    workload: Workload
    source: ChassisSource
    process: Process = field(repr=False)

    def client(self, **kw: Any) -> httpx.Client:
        return _client(self.uds, self.url, **kw)

    def post(self, path: str, body: Mapping[str, Any], **kw: Any) -> httpx.Response:
        with self.client(timeout=kw.pop("timeout", 60.0)) as http:
            return http.post(path, json=dict(body), **kw)

    def run(self, input: Mapping[str, Any], **kw: Any) -> dict[str, Any]:
        """POST `/v1/run` with `{"input": input, **kw}`; the response envelope. Any status other
        than 200 is an `AssertionError` that quotes the body."""
        response = self.post("/v1/run", {"input": dict(input), **kw})
        if response.status_code != 200:
            raise AssertionError(f"/v1/run answered {response.status_code}: {response.text}")
        envelope: dict[str, Any] = response.json()
        return envelope


def _client(uds: str, url: str, **kw: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.HTTPTransport(uds=uds), base_url=url, trust_env=False, **kw)


def free_ports(count: int) -> list[int]:
    """`count` free loopback TCP ports, asked of the OS in a child process: the offline gate
    refuses to open a TCP socket in the test process, even to bind one."""
    code = (
        "import socket\n"
        f"socks = [socket.socket() for _ in range({count})]\n"
        "for s in socks: s.bind(('127.0.0.1', 0))\n"
        "print(' '.join(str(s.getsockname()[1]) for s in socks))\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], check=True, capture_output=True, text=True
    )
    return [int(port) for port in out.stdout.split()]


def clean_env(base: Mapping[str, str], drop: tuple[str, ...] = ()) -> dict[str, str]:
    """`base` without secrets (`SECRET_SUFFIXES`) and without the names in `drop`."""
    return {k: v for k, v in base.items() if not k.endswith(SECRET_SUFFIXES) and k not in drop}


def _guard_dir(root: Path) -> Path:
    """A folder whose `chassis` and `chassis_contracts` raise `ImportError` on import."""
    guard = root / "guard"
    for name in ("chassis", "chassis_contracts"):
        package = guard / name
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text(_GUARD.format(name=name))
    return guard


def _spawn(name: str, argv: list[str], env: Mapping[str, str], log: Path) -> Process:
    with log.open("wb") as out:
        popen = subprocess.Popen(
            argv, env=dict(env), stdout=out, stderr=subprocess.STDOUT, start_new_session=True
        )
    return Process(name, popen, log)


def _wait_for(process: Process, uds: str, path: str, timeout_s: float) -> None:
    """Poll `GET path` over `uds` until it is 200; `StartError` on exit or timeout."""
    deadline = time.monotonic() + timeout_s
    last = "no answer yet"
    while time.monotonic() < deadline:
        if process.popen.poll() is not None:
            raise StartError(
                f"{process.name} exited with {process.popen.returncode} before {path} was 200.\n"
                f"--- {process.log} ---\n{process.log_tail()}"
            )
        if Path(uds).exists():
            try:
                with _client(uds, BASE_URL, timeout=2.0) as http:
                    response = http.get(path)
                if response.status_code == 200:
                    return
                last = f"{response.status_code} {response.text[:200]}"
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
        time.sleep(POLL_S)
    process.stop()
    raise StartError(
        f"{process.name}: {path} was not 200 within {timeout_s:.0f} s ({last}).\n"
        f"--- {process.log} ---\n{process.log_tail()}"
    )


@contextmanager
def _scratch() -> Iterator[Path]:
    """A short temporary folder: a Unix socket path must stay under about 100 bytes."""
    root = Path(tempfile.mkdtemp(prefix="atk-"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def workload_argv(ref: AgentRef, uds: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "workload_a2a",
        "serve",
        "--handle",
        ref.handle,
        "--name",
        ref.name,
        "--uds",
        uds,
        "--drain-timeout-s",
        "1",
        "--log-level",
        "warning",
    ]


@contextmanager
def start_workload(
    ref: AgentRef | str,
    *,
    env: Mapping[str, str] | None = None,
    block_chassis: bool = True,
    timeout_s: float = WORKLOAD_START_S,
) -> Iterator[Workload]:
    """Start the workload's A2A server and wait for its agent card. `env` adds variables."""
    agent = AgentRef.of(ref)
    with _scratch() as root:
        uds = str(root / "workload.sock")
        (proxy_port,) = free_ports(1)
        proxy = f"http://127.0.0.1:{proxy_port}"
        child = clean_env(os.environ)
        child.update(
            {
                "CHASSIS_MODEL_URL": f"{proxy}/v1",
                "CHASSIS_TOOL_URL": f"{proxy}/mcp",
                "PYTHONUNBUFFERED": "1",
                **(env or {}),
            }
        )
        if block_chassis:
            paths = [str(_guard_dir(root)), child.get("PYTHONPATH", "")]
            child["PYTHONPATH"] = os.pathsep.join(p for p in paths if p)
        process = _spawn(
            f"workload {agent.handle}", workload_argv(agent, uds), child, root / "workload.log"
        )
        try:
            _wait_for(process, uds, CARD_PATH, timeout_s)
            yield Workload(agent, BASE_URL, uds, proxy_port, process)
        finally:
            process.stop()


def chassis_config(workload: Workload) -> dict[str, Any]:
    """The `fake`-profile sidecar config for `workload`, from the template in `data/`."""
    config: dict[str, Any] = json.loads(CHASSIS_CONFIG.read_text())
    config["agent"]["name"] = workload.ref.name
    config["spec"]["engine"]["url"] = workload.url
    config["spec"]["engine"]["uds"] = workload.uds
    return config


def chassis_argv(source: ChassisSource, config: Path, uds: str, proxy_port: int) -> list[str]:
    (public_port,) = free_ports(1)  # never bound: the public listener is on `uds`
    return [
        *source.command,
        "-I",
        str(CHASSIS_SERVE),
        "--uds",
        uds,
        "serve",
        "--config",
        str(config),
        "--port",
        str(public_port),
        "--proxy-port",
        str(proxy_port),
        "--drain-delay-s",
        "0",
        "--drain-timeout-s",
        "1",
        "--startup-wait-s",
        "30",
    ]


@contextmanager
def start_chassis(
    workload: Workload, source: ChassisSource, *, timeout_s: float = CHASSIS_START_S
) -> Iterator[Chassis]:
    """Start the chassis in the `fake` profile in front of `workload`; wait for `/ready`."""
    with _scratch() as root:
        uds = str(root / "chassis.sock")
        config = root / "chassis.yaml"  # JSON is YAML
        config.write_text(json.dumps(chassis_config(workload), indent=2))
        env = clean_env(os.environ, drop=("PYTHONPATH", *UV_VARIABLES))
        env["PYTHONUNBUFFERED"] = "1"
        argv = chassis_argv(source, config, uds, workload.proxy_port)
        process = _spawn("chassis", argv, env, root / "chassis.log")
        try:
            _wait_for(process, uds, "/ready", timeout_s)
            yield Chassis(BASE_URL, uds, workload, source, process)
        finally:
            process.stop()
