"""`chassis serve --config configs/fake.yaml --port 8080 --proxy-port 8090`.

Two listeners in one event loop: the public app (`/health`, `/ready`, `/v1/run`) on `--host` and
`--port`, and the proxy app (`/v1/chat/completions`) on `--proxy-host` and `--proxy-port`
(suggested: 8090). Only the public app runs a lifespan; the proxy app shares its state. The proxy
listener binds loopback only unless `--allow-any-proxy-host` is given (`deploy/CLAUDE.md`: the
proxies are localhost-only). That flag is for the loopback listener's tests; it is not the
`remote` lane.

The `remote` lane (PoC-5 plan, section 2.3; ADR-001 item 8) adds a third listener:
`--remote-proxy-host <pod IP>` and `--remote-proxy-port` (suggested: 8091) serve the remote proxy
app (`chassis.server.remote_auth`): the model proxy and the tool endpoint behind a bearer token
and a run in flight. The flag is refused unless `spec.engine.connector` is `remote` and the
token variable named in `spec.engine.auth.token_env` is set; the host must be one IP address,
neither loopback nor a wildcard (`0.0.0.0`, `::`).

Bind order (H14): the proxy listeners bind first. A taken proxy port exits with status 3 and a
message naming the address, before the public listener binds and before anything reaches the
workload. The public lifespan then runs in the background (`DeferredStartup`): `/health` answers
at once and `/ready` is 503 `starting` while the workload is not reachable, for up to
`--startup-wait-s` (suggested: 120). Past that, or on any other startup error, exit 3.

The chassis handles SIGTERM and SIGINT itself (`chassis.server.lifecycle`): `/ready` goes 503 at
once, the public listener closes after `--drain-delay-s` (suggested: 5), in-flight runs get up to
`--drain-timeout-s` (suggested: 30), and the proxy listeners stop last. A second signal exits at
once.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import math
import sys
from collections.abc import Sequence
from typing import NamedTuple

import uvicorn

from chassis.server.app import create_app
from chassis.server.config import ChassisConfig, load_config
from chassis.server.lifecycle import (
    DeferredStartup,
    Drain,
    DrainSettings,
    ProxyBindFailed,
    QuietServer,
    StartupWait,
)
from chassis.server.proxy_app import create_proxy_app
from chassis.server.remote_auth import (
    DEFAULT_REMOTE_PROXY_PORT,
    create_remote_proxy_app,
    remote_tokens,
)

DEFAULT_PORT = 8080
DEFAULT_PROXY_PORT = 8090  # suggested: the epic gives no port for the proxies
DRAIN = DrainSettings()  # suggested: delay 5 s, timeout 30 s
WAIT = StartupWait()  # suggested: 120 s, one attempt a second
STARTUP_FAILURE = 3  # what `uvicorn.run` exits with when the lifespan fails


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chassis", description="The service chassis")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="start the chassis from a config file")
    serve.add_argument("--config", required=True, help="YAML config file")
    serve.add_argument("--host", default="127.0.0.1", help="the public listener")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument(
        "--proxy-host", default="127.0.0.1", help="the model proxy listener; loopback only"
    )
    serve.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    serve.add_argument(
        "--allow-any-proxy-host",
        action="store_true",
        help="let --proxy-host be off loopback, with no authentication (tests only); this is "
        "not the remote lane: use --remote-proxy-host for that",
    )
    serve.add_argument(
        "--remote-proxy-host",
        default=None,
        help="the remote lane's proxy listener: the pod IP (one address, not loopback, not "
        "0.0.0.0); needs spec.engine.connector remote and its token variable",
    )
    serve.add_argument("--remote-proxy-port", type=int, default=DEFAULT_REMOTE_PROXY_PORT)
    serve.add_argument(
        "--startup-wait-s",
        type=float,
        default=WAIT.timeout_s,
        help="seconds startup waits for an unreachable workload (/ready says starting) "
        "before the chassis exits",
    )
    serve.add_argument(
        "--drain-delay-s",
        type=float,
        default=DRAIN.delay_s,
        help="on SIGTERM, seconds /ready is 503 before the public listener closes",
    )
    serve.add_argument(
        "--drain-timeout-s",
        type=float,
        default=DRAIN.timeout_s,
        help="on SIGTERM, seconds in-flight runs may go on before they are cancelled",
    )
    return parser


def _check_remote(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """The `--remote-proxy-host` rules; each failure exits with status 2 and says why."""
    host = args.remote_proxy_host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        parser.error(f"--remote-proxy-host {host} must be one IP address: the pod IP")
    if address.is_loopback:
        parser.error(
            f"--remote-proxy-host {host} is loopback: that is the --proxy-host listener. The "
            "remote listener binds the pod IP"
        )
    if address.is_unspecified:
        parser.error(f"--remote-proxy-host {host} binds every address; give the pod IP only")
    if args.remote_proxy_port in (args.port, args.proxy_port):
        parser.error("--remote-proxy-port must differ from --port and --proxy-port")
    config: ChassisConfig = load_config(args.config)
    connector = config.spec.engine.connector
    if connector != "remote":
        parser.error(
            f"--remote-proxy-host is for the remote lane, but spec.engine.connector is "
            f"{connector!r}"
        )
    auth = config.spec.engine.auth
    try:
        remote_tokens(auth.model_dump() if auth is not None else None)
    except LookupError as exc:
        parser.error(f"--remote-proxy-host: {exc}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse and check the flags; a bad combination exits with status 2 and says why."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        if not _is_loopback(args.proxy_host) and not args.allow_any_proxy_host:
            parser.error(
                f"--proxy-host {args.proxy_host} is not loopback. The model proxy spends with "
                "the chassis key, so it listens on localhost only (deploy/CLAUDE.md). Pass "
                "--allow-any-proxy-host to override."
            )
        for flag in ("drain_delay_s", "drain_timeout_s", "startup_wait_s"):
            if getattr(args, flag) < 0:
                parser.error(f"--{flag.replace('_', '-')} must be 0 or more")
        if args.proxy_port == args.port:
            parser.error("--proxy-port must differ from --port: the proxies never share it")
        if args.remote_proxy_host is not None:
            _check_remote(parser, args)
    return args


class Servers(NamedTuple):
    public: uvicorn.Server
    proxy: uvicorn.Server
    remote: uvicorn.Server | None
    """The remote proxy listener, only with `--remote-proxy-host`."""
    startup: DeferredStartup
    """The public lifespan, entered in the background (H14)."""


def build_all(args: argparse.Namespace) -> Servers:
    """The public server (with the deferred lifespan), the proxy server, and the remote proxy
    server when `--remote-proxy-host` is given (neither proxy has a lifespan).
    """
    config = load_config(args.config)
    public_app = create_app(config)
    proxy_app = create_proxy_app(public_app)
    remote: uvicorn.Server | None = None
    if args.remote_proxy_host is not None:
        auth = config.spec.engine.auth
        tokens = remote_tokens(auth.model_dump() if auth is not None else None)
        remote_app = create_remote_proxy_app(public_app, tokens)
        remote = QuietServer(
            uvicorn.Config(
                remote_app,
                host=args.remote_proxy_host,
                port=args.remote_proxy_port,
                lifespan="off",
            )
        )
    startup = DeferredStartup(public_app, StartupWait(timeout_s=args.startup_wait_s))
    startup.install()  # last: the tool endpoints hooked into the lifespan above
    public = QuietServer(
        uvicorn.Config(
            public_app,
            host=args.host,
            port=args.port,
            lifespan="on",
            timeout_graceful_shutdown=math.ceil(args.drain_timeout_s),  # whole seconds
        )
    )
    startup.server = public
    proxy = QuietServer(
        uvicorn.Config(proxy_app, host=args.proxy_host, port=args.proxy_port, lifespan="off")
    )
    return Servers(public, proxy, remote, startup)


def build_servers(args: argparse.Namespace) -> tuple[uvicorn.Server, uvicorn.Server]:
    """The public server and the loopback proxy server (`build_all` without the rest)."""
    servers = build_all(args)
    return servers.public, servers.proxy


async def serve(
    public: uvicorn.Server,
    proxy: uvicorn.Server,
    settings: DrainSettings = DRAIN,
    remote: uvicorn.Server | None = None,
) -> None:
    """Every server in one loop, with the chassis's own signal handling (`Drain`): the proxies
    bind first, then the public server starts. When any stops, so do the others; the proxies
    stop after the public one. Raises `ProxyBindFailed` when a proxy cannot bind.
    """
    state = getattr(public.config.app, "state", None)
    await Drain(public, proxy, settings, state=state, remote=remote).serve()


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "serve":
        servers = build_all(args)
        settings = DrainSettings(delay_s=args.drain_delay_s, timeout_s=args.drain_timeout_s)
        try:
            asyncio.run(serve(servers.public, servers.proxy, settings, servers.remote))
        except ProxyBindFailed as exc:
            print(f"chassis: {exc}", file=sys.stderr)
            sys.exit(STARTUP_FAILURE)
        if not servers.public.started or servers.startup.failed is not None:
            sys.exit(STARTUP_FAILURE)


if __name__ == "__main__":
    main()
