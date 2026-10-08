"""`chassis serve` in PoC-5 (plan, sections 2.3 and 2.12 H14).

- The remote proxy listener: `--remote-proxy-host` only in the `remote` lane with its token
  variable set, never loopback, never a wildcard address; it serves the remote proxy app on
  `--remote-proxy-port` (suggested 8091) and stops last, with the loopback proxy.
- H14, bind order: the loopback proxy binds before the public listener and before the chassis
  waits for the workload; a taken proxy port is a non-zero exit with a clear message. The wait
  for the workload is `/ready` 503 `starting` (the public port already answers `/health`), not
  a wait before the bind.

Real uvicorn servers on Unix sockets where a listener must run; no TCP socket.
"""

from __future__ import annotations

import asyncio
import errno
import os
import signal
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, cli, create_app
from chassis.server.lifecycle import DeferredStartup, Drain, DrainSettings, QuietServer, StartupWait
from chassis.server.proxy_app import create_proxy_app
from chassis.server.remote_auth import BearerAuth, create_remote_proxy_app
from fastapi import FastAPI

FAKE_YAML = str(Path(__file__).resolve().parents[1] / "configs/fake.yaml")
TOKEN = "t" * 64  # a test value, not a secret
REMOTE_YAML = """\
profile: fake
agent: {name: echo, version: 0.0.1}
spec:
  trust: untrusted
  engine:
    connector: remote
    url: http://remote-echo:9000
    auth: {token_env: REMOTE_TOKEN}
"""
CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}


@pytest.fixture
def remote_yaml(tmp_path: Path) -> str:
    path = tmp_path / "remote.yaml"
    path.write_text(REMOTE_YAML)
    return str(path)


# --- the remote proxy flags (section 2.3) -------------------------------------------------------


def test_remote_proxy_host_is_refused_outside_the_remote_lane(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMOTE_TOKEN", TOKEN)
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["serve", "--config", FAKE_YAML, "--remote-proxy-host", "10.0.0.5"])
    assert exc.value.code == 2
    assert "connector is 'inprocess'" in capsys.readouterr().err


def test_remote_proxy_host_is_refused_without_the_token_variable(
    remote_yaml: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REMOTE_TOKEN", raising=False)
    for value in (None, ""):
        if value is not None:
            monkeypatch.setenv("REMOTE_TOKEN", value)
        with pytest.raises(SystemExit) as exc:
            cli.parse_args(["serve", "--config", remote_yaml, "--remote-proxy-host", "10.0.0.5"])
        assert exc.value.code == 2
        assert "REMOTE_TOKEN is not set" in capsys.readouterr().err


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "::1", "localhost", "0.0.0.0", "::", "chassis.internal", "10.0.0.5/24"]
)
def test_remote_proxy_host_must_be_one_address_off_loopback(
    host: str,
    remote_yaml: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REMOTE_TOKEN", TOKEN)
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["serve", "--config", remote_yaml, "--remote-proxy-host", host])
    assert exc.value.code == 2
    assert "--remote-proxy-host" in capsys.readouterr().err


def test_remote_proxy_port_must_differ_from_the_other_two(
    remote_yaml: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMOTE_TOKEN", TOKEN)
    base = ["serve", "--config", remote_yaml, "--remote-proxy-host", "10.0.0.5"]
    for port in ("8080", "8090"):
        with pytest.raises(SystemExit):
            cli.parse_args([*base, "--remote-proxy-port", port])
        assert "--remote-proxy-port" in capsys.readouterr().err


def test_the_remote_flags_are_ignored_and_the_listener_absent_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = cli.parse_args(["serve", "--config", FAKE_YAML])
    assert args.remote_proxy_host is None and args.remote_proxy_port == 8091
    servers = cli.build_all(args)
    assert servers.remote is None


def test_serve_builds_the_remote_listener_on_the_pod_ip(
    remote_yaml: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMOTE_TOKEN", TOKEN)
    args = cli.parse_args(["serve", "--config", remote_yaml, "--remote-proxy-host", "10.0.0.5"])
    servers = cli.build_all(args)
    assert servers.remote is not None
    config = servers.remote.config
    assert (config.host, config.port, config.lifespan) == ("10.0.0.5", 8091, "off")
    assert isinstance(servers.remote, QuietServer)
    assert (servers.proxy.config.host, servers.proxy.config.port) == ("127.0.0.1", 8090)
    remote_app = config.app
    public_app = servers.public.config.app
    assert isinstance(remote_app, FastAPI) and isinstance(public_app, FastAPI)
    assert remote_app.state is public_app.state
    middleware: list[object] = [m.cls for m in remote_app.user_middleware]
    assert BearerAuth in middleware
    assert TOKEN not in repr(servers.remote.config.__dict__)


def test_allow_any_proxy_host_help_says_it_is_not_the_remote_lane(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["serve", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "not the remote lane" in out and "--remote-proxy-host" in out


# --- H14: the proxy binds first -----------------------------------------------------------------


def test_serve_exits_non_zero_when_the_proxy_port_is_taken(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """H14: the chassis never runs without its proxy. The public listener never starts and the
    ports are never built (no workload wait) when 8090 is taken.
    """
    calls: list[tuple[str, int]] = []
    original = asyncio.base_events.BaseEventLoop.create_server

    async def create_server(
        self: Any, factory: Any, host: Any = None, port: Any = None, **kw: Any
    ) -> Any:
        calls.append((host, port))
        if port == 8090:
            raise OSError(errno.EADDRINUSE, "address already in use")
        return await original(self, factory, host, port, **kw)

    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "create_server", create_server)
    built: list[str] = []

    def build_ports(*args: Any, **kwargs: Any) -> None:
        built.append("ports")

    monkeypatch.setattr("chassis.server.app.build_ports", build_ports)
    with pytest.raises(SystemExit) as exc:
        cli.main(["serve", "--config", FAKE_YAML, "--port", "8181"])
    assert exc.value.code not in (0, None)
    err = capsys.readouterr().err
    assert "127.0.0.1:8090" in err and "proxy" in err
    assert calls == [("127.0.0.1", 8090)], "the public listener never tried to bind"
    assert built == [], "no lifespan ran: the proxy bind comes before any wait for the workload"


class WaitingEngine(FakeEngine):
    """An engine whose `setup` fails like an unreachable sidecar until `up` is set. Records
    whether the proxy listener was up at each attempt.
    """

    def __init__(self, proxy: list[uvicorn.Server]) -> None:
        super().__init__(handle=echo)
        self.up = False
        self.proxy = proxy
        self.attempts: list[bool] = []

    async def setup(self, config: Mapping[str, Any], ports: PortBundle) -> None:
        self.attempts.append(self.proxy[0].started)
        if not self.up:
            try:
                raise ConnectionRefusedError(errno.ECONNREFUSED, "connection refused")
            except OSError as exc:
                raise RuntimeError("the sidecar at http://127.0.0.1:9000 is not reachable") from exc
        await super().setup(config, ports)


@contextmanager
def _sockets() -> Iterator[tuple[str, str, str]]:
    with tempfile.TemporaryDirectory(prefix="p5-") as folder:  # short: macOS caps the path
        yield tuple(str(Path(folder, f"{n}.sock")) for n in ("pub", "prx", "rem"))  # type: ignore[misc]


def _drain(
    engine: FakeEngine,
    socks: tuple[str, str, str],
    wait: StartupWait,
    *,
    remote: bool = False,
) -> tuple[Drain, DeferredStartup]:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    proxy_app = create_proxy_app(app)
    remote_server = None
    if remote:
        remote_app = create_remote_proxy_app(app, (TOKEN,))
        remote_server = QuietServer(uvicorn.Config(remote_app, uds=socks[2], lifespan="off"))
    deferred = DeferredStartup(app, wait)
    deferred.install()  # after every hook into the lifespan, as `cli.build_all` does
    public = QuietServer(uvicorn.Config(app, uds=socks[0], lifespan="on"))
    deferred.server = public
    proxy = QuietServer(uvicorn.Config(proxy_app, uds=socks[1], lifespan="off"))
    drain = Drain(
        public,
        proxy,
        DrainSettings(delay_s=0.0, timeout_s=5),
        state=app.state,
        remote=remote_server,
    )
    return drain, deferred


def _client(uds: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=uds), base_url="http://chassis", timeout=5
    )


async def _until(check: Any, what: str) -> None:
    for _ in range(500):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(what)


async def test_the_proxy_binds_before_the_wait_and_the_wait_is_readiness() -> None:
    """H14: the proxy is up at the first engine setup; while the workload is not reachable the
    public port answers `/health` 200 and `/ready` 503 `starting`; once it is, `/ready` is 200.
    """
    proxy_ref: list[uvicorn.Server] = []
    engine = WaitingEngine(proxy_ref)
    with _sockets() as socks:
        drain, deferred = _drain(engine, socks, StartupWait(timeout_s=10, interval_s=0.05))
        proxy_ref.append(drain.proxy)
        served = asyncio.create_task(drain.serve())
        await _until(lambda: len(engine.attempts) >= 2, "setup was not retried")
        assert engine.attempts[0] is True, "the proxy listener was bound before the first setup"
        async with _client(socks[0]) as client:
            assert (await client.get("/health")).status_code == 200
            ready = await client.get("/ready")
            assert (ready.status_code, ready.json()["reason"]) == (503, "starting")
            engine.up = True
            await _until(lambda: deferred.started, "startup never finished")
            assert (await client.get("/ready")).status_code == 200
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(served, 10)
    assert deferred.failed is None


async def test_a_startup_error_that_is_not_a_connection_fails_at_once() -> None:
    class Broken(FakeEngine):
        async def setup(self, config: Mapping[str, Any], ports: PortBundle) -> None:
            raise ValueError("engine.url is not a URL")

    engine = Broken(handle=echo)
    with _sockets() as socks:
        drain, deferred = _drain(engine, socks, StartupWait(timeout_s=10, interval_s=0.05))
        await asyncio.wait_for(drain.serve(), 5)
    assert isinstance(deferred.failed, ValueError)
    assert drain.order[-2:] == ["public stopped", "proxy stopped"]


async def test_the_workload_wait_gives_up_after_its_timeout() -> None:
    proxy_ref: list[uvicorn.Server] = []
    engine = WaitingEngine(proxy_ref)
    with _sockets() as socks:
        drain, deferred = _drain(engine, socks, StartupWait(timeout_s=0.3, interval_s=0.05))
        proxy_ref.append(drain.proxy)
        await asyncio.wait_for(drain.serve(), 5)
    assert isinstance(deferred.failed, RuntimeError)
    assert len(engine.attempts) >= 2


def test_main_exits_3_when_the_deferred_startup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_serve(self: uvicorn.Server, sockets: Any = None) -> None:
        self.started = True

    def failing(self: DeferredStartup) -> None:
        self.failed = RuntimeError("the sidecar is not reachable")

    monkeypatch.setattr(uvicorn.Server, "serve", fake_serve)
    monkeypatch.setattr(DeferredStartup, "install", failing)
    with pytest.raises(SystemExit) as exc:
        cli.main(["serve", "--config", FAKE_YAML])
    assert exc.value.code == cli.STARTUP_FAILURE


def test_the_startup_wait_flag(capsys: pytest.CaptureFixture[str]) -> None:
    args = cli.parse_args(["serve", "--config", FAKE_YAML])
    assert args.startup_wait_s == 120.0
    with pytest.raises(SystemExit):
        cli.parse_args(["serve", "--config", FAKE_YAML, "--startup-wait-s", "-1"])
    assert "--startup-wait-s" in capsys.readouterr().err


# --- the remote listener drains last ------------------------------------------------------------


async def test_the_remote_listener_stops_after_the_public_one_with_the_proxy() -> None:
    with _sockets() as socks:
        drain, deferred = _drain(
            FakeEngine(handle=echo), socks, StartupWait(timeout_s=5), remote=True
        )
        served = asyncio.create_task(drain.serve())
        assert drain.remote is not None
        remote = drain.remote
        await _until(
            lambda: drain.public.started and drain.proxy.started and remote.started,
            "the servers never started",
        )
        await _until(lambda: deferred.started, "startup never finished")
        async with _client(socks[2]) as client:
            refused = await client.post("/v1/chat/completions", json={})
            assert refused.status_code == 401
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(served, 10)
    order = drain.order
    assert order[:3] == ["draining", "public closing", "public stopped"]
    assert set(order[3:]) == {"proxy stopped", "remote proxy stopped"}
