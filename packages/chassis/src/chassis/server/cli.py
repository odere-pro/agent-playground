"""`chassis serve --config configs/fake.yaml --port 8080 --proxy-port 8090`.

Two listeners in one event loop: the public app (`/health`, `/ready`, `/v1/run`) on `--host` and
`--port`, and the proxy app (`/v1/chat/completions`) on `--proxy-host` and `--proxy-port`
(suggested: 8090). Only the public app runs a lifespan; the proxy app shares its state. The proxy
listener binds loopback only unless `--allow-any-proxy-host` is given (`deploy/CLAUDE.md`: the
proxies are localhost-only). That flag is for the loopback listener's tests; it is not the
`remote` lane. After the config loads, the flag is refused (status 2) in profile `cloud` and
when `spec.trust` is `untrusted` (R5a-22): that listener has no authentication.

The `remote` lane (PoC-5 plan, section 2.3; ADR-001 item 8) adds a third listener:
`--remote-proxy-host <pod IP>` and `--remote-proxy-port` (suggested: 8091) serve the remote proxy
app (`chassis.server.remote_auth`): the model proxy and the tool endpoint behind a bearer token
and a run in flight. The flag is refused unless `spec.engine.connector` is `remote` and the
listener's own token variable (`spec.engine.auth.proxy_token_env`, required; 055 CH-6) is set,
with 32 characters or more (a set previous token too; `remote_auth.MIN_TOKEN_LENGTH`); the host
must be one IP address, neither loopback nor a wildcard (`0.0.0.0`, `::`).

Bind order (H14): the proxy listeners bind first. A taken proxy port exits with status 3 and a
message naming the address, before the public listener binds and before anything reaches the
workload. The public lifespan then runs in the background (`DeferredStartup`): `/health` answers
at once and `/ready` is 503 `starting` while the workload is not reachable, for up to
`--startup-wait-s` (suggested: 120). Past that, or on any other startup error, exit 3.

The chassis handles SIGTERM and SIGINT itself (`chassis.server.lifecycle`): `/ready` goes 503 at
once, the public listener closes after `--drain-delay-s` (suggested: 5), in-flight runs get up to
`--drain-timeout-s` (suggested: 30), and the proxy listeners stop last. A second signal exits at
once.

Logs (PoC-8): one JSON object per line on stderr, at `spec.observability.log_level`
(`chassis.logs.configure_logging`). uvicorn gets no log config of its own (`log_config=None`), so
its lines take the same format.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import uvicorn

from chassis.adapters.a2a.loopback import is_loopback_host
from chassis.logs import configure_logging
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
        help="let --proxy-host be off loopback, with no authentication (tests only; refused in "
        "profile cloud and for spec.trust untrusted); this is not the remote lane: use "
        "--remote-proxy-host for that",
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
    serve.add_argument(
        "--ready-marker",
        type=Path,
        default=None,
        help="an absolute path: written once every listener has bound, removed at start and "
        "exit, for a startup probe the workload cannot answer (ADR-012; off by default)",
    )
    return parser


def _check_any_proxy_host(parser: argparse.ArgumentParser, config: ChassisConfig) -> None:
    """R5a-22: `--allow-any-proxy-host` puts the model proxy and `/mcp` off loopback with no
    authentication, so it is refused in profile `cloud` and when `spec.trust` is `untrusted`.
    Each refusal exits with status 2 and says why."""
    if config.profile == "cloud":
        parser.error(
            "--allow-any-proxy-host is refused in profile 'cloud': the model proxy spends the "
            "chassis key with no authentication. Use the remote lane (--remote-proxy-host)"
        )
    if config.spec.trust == "untrusted":
        parser.error(
            "--allow-any-proxy-host is refused when spec.trust is 'untrusted': untrusted code "
            "must reach the proxies through the remote listener (--remote-proxy-host)"
        )


def _check_remote(
    parser: argparse.ArgumentParser, args: argparse.Namespace, config: ChassisConfig
) -> None:
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
    connector = config.spec.engine.connector
    if connector != "remote":
        parser.error(
            f"--remote-proxy-host is for the remote lane, but spec.engine.connector is "
            f"{connector!r}"
        )
    auth = config.spec.engine.auth
    try:
        remote_tokens(auth.model_dump() if auth is not None else None)
    except (LookupError, ValueError) as exc:
        parser.error(f"--remote-proxy-host: {exc}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse and check the flags; a bad combination exits with status 2 and says why."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        if not is_loopback_host(args.proxy_host) and not args.allow_any_proxy_host:
            parser.error(
                f"--proxy-host {args.proxy_host} is not loopback. The model proxy spends with "
                "the chassis key, so it listens on localhost only (deploy/CLAUDE.md). Pass "
                "--allow-any-proxy-host to override."
            )
        for flag in ("drain_delay_s", "drain_timeout_s", "startup_wait_s"):
            if getattr(args, flag) < 0:
                parser.error(f"--{flag.replace('_', '-')} must be 0 or more")
        if args.ready_marker is not None and not args.ready_marker.is_absolute():
            parser.error("--ready-marker must be an absolute path")
        if args.proxy_port == args.port:
            parser.error("--proxy-port must differ from --port: the proxies never share it")
        if args.allow_any_proxy_host or args.remote_proxy_host is not None:
            config = load_config(args.config)  # once, for the checks that need it
            if args.allow_any_proxy_host:
                _check_any_proxy_host(parser, config)
            if args.remote_proxy_host is not None:
                _check_remote(parser, args, config)
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
    configure_logging(agent=config.agent.name, level=config.spec.observability.log_level)
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
                log_config=None,
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
            log_config=None,
        )
    )
    startup.server = public
    proxy = QuietServer(
        uvicorn.Config(
            proxy_app, host=args.proxy_host, port=args.proxy_port, lifespan="off", log_config=None
        )
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
    ready_marker: Path | None = None,
) -> None:
    """Every server in one loop, with the chassis's own signal handling (`Drain`): the proxies
    bind first, then the public server starts. When any stops, so do the others; the proxies
    stop after the public one. Raises `ProxyBindFailed` when a proxy cannot bind.
    """
    state = getattr(public.config.app, "state", None)
    await Drain(
        public, proxy, settings, state=state, remote=remote, ready_marker=ready_marker
    ).serve()


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "serve":
        servers = build_all(args)
        settings = DrainSettings(delay_s=args.drain_delay_s, timeout_s=args.drain_timeout_s)
        try:
            asyncio.run(
                serve(servers.public, servers.proxy, settings, servers.remote, args.ready_marker)
            )
        except ProxyBindFailed as exc:
            print(f"chassis: {exc}", file=sys.stderr)
            sys.exit(STARTUP_FAILURE)
        if not servers.public.started or servers.startup.failed is not None:
            sys.exit(STARTUP_FAILURE)


if __name__ == "__main__":
    main()
