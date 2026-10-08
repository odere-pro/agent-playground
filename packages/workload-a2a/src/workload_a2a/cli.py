"""`workload-a2a serve --handle module:attribute --port N`: serve a workload's `handle` over A2A.

Binds `127.0.0.1` by default and refuses a host that is not loopback unless `--allow-any-host` is
passed (ADR-001: the sidecar serves localhost only). `--uds PATH` serves on a Unix socket instead
of TCP, for tests and local runs. On SIGTERM it finishes in-flight `handle` calls for up to
`--drain-timeout-s` seconds (suggested default 30), then exits. `--require-token-env NAME` puts a
bearer check on every request and is the only way to bind a host that is not loopback without
`--allow-any-host` (PoC-5 section 2.4). `--previous-token-env NAME` (only with
`--require-token-env`) also accepts the token in `$NAME` while a rotation runs; an unset or empty
variable is ignored.
"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Sequence
from typing import cast

import uvicorn

from workload_a2a.auth import BearerTokenMiddleware, read_previous_token, read_token
from workload_a2a.server import (
    DEFAULT_DRAIN_TIMEOUT_S,
    WireHandle,
    build_agent_card,
    build_server,
    is_loopback,
)


def load_handle(path: str) -> WireHandle:
    """Import `module:attribute`, the way an entry point is loaded."""
    module_name, sep, attr = path.partition(":")
    if not sep or not module_name or not attr:
        raise ValueError(f"--handle must be 'module:attribute', got {path!r}")
    module = importlib.import_module(module_name)
    try:
        handle = getattr(module, attr)
    except AttributeError as exc:
        raise ValueError(f"{module_name} has no attribute {attr!r}") from exc
    if not callable(handle):
        raise ValueError(f"{path} is not callable")
    return cast(WireHandle, handle)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workload-a2a")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve handle(input, ctx) over A2A")
    serve.add_argument("--handle", required=True, help="module:attribute, e.g. echo_python:handle")
    serve.add_argument("--port", type=int, help="TCP port; required unless --uds is given")
    serve.add_argument("--host", default="127.0.0.1", help="loopback only by default")
    serve.add_argument("--uds", help="serve on this Unix socket path instead of TCP")
    serve.add_argument("--name", help="agent card name; default: the --handle path")
    serve.add_argument("--version", default="0.0.0", help="agent card version")
    serve.add_argument("--description", default="", help="agent card description")
    serve.add_argument(
        "--allow-any-host", action="store_true", help="bind a host that is not loopback"
    )
    serve.add_argument(
        "--drain-timeout-s",
        type=int,
        default=DEFAULT_DRAIN_TIMEOUT_S,
        help="on SIGTERM, wait this long for in-flight calls (suggested default: 30)",
    )
    serve.add_argument(
        "--require-token-env",
        metavar="NAME",
        help="require 'Authorization: Bearer <value of $NAME>' on every request; "
        "a host that is not loopback is allowed only with this flag",
    )
    serve.add_argument(
        "--previous-token-env",
        metavar="NAME",
        help="during a token rotation, also accept the value of $NAME; unset or empty is "
        "ignored; needs --require-token-env",
    )
    serve.add_argument("--log-level", default="info")
    return parser


def _card_url(host: str, port: int | None) -> str:
    """The URL the card names: the bound loopback host, or 127.0.0.1 for a wildcard bind."""
    if not is_loopback(host):
        host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}" + (f":{port}" if port else "")


def build(argv: Sequence[str] | None = None) -> uvicorn.Server:
    """Parse the command line and build the server. Exits with the reason on a bad argument."""
    args = _parser().parse_args(argv)
    if args.port is None and not args.uds:
        raise SystemExit("workload-a2a: --port is required unless --uds is given")
    if args.drain_timeout_s < 0:
        raise SystemExit("workload-a2a: --drain-timeout-s must be 0 or more")
    if args.previous_token_env and not args.require_token_env:
        raise SystemExit("workload-a2a: --previous-token-env needs --require-token-env")
    try:
        token = read_token(args.require_token_env) if args.require_token_env else None
        handle = load_handle(args.handle)
        card = build_agent_card(
            name=args.name or args.handle,
            version=args.version,
            description=args.description,
            url=_card_url(args.host, args.port),
        )
        previous = read_previous_token(args.previous_token_env) if args.previous_token_env else None
        server = build_server(
            handle,
            card,
            host=args.host,
            port=args.port or 0,
            uds=args.uds,
            allow_any_host=args.allow_any_host or token is not None,
            log_level=args.log_level,
            drain_timeout_s=args.drain_timeout_s,
        )
        if token is not None:
            server.config.app = BearerTokenMiddleware(server.config.app, token, previous)
        return server
    except (ValueError, ImportError) as exc:
        raise SystemExit(f"workload-a2a: {exc}") from exc


def main(argv: Sequence[str] | None = None) -> None:
    build(argv).run()
