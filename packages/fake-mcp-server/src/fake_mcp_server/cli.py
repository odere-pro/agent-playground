"""`fake-mcp-server --port 8082 [--allow TOKEN=tool,tool]`"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn

from fake_mcp_server.server import ANY_CALLER, TOOL_NAMES, FakeMcpState, create_app


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scripted MCP server with four harmless tools")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback)")
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="TOKEN=tool,tool",
        help="show and allow only these tools to this bearer token; repeatable. "
        "Without 'TOKEN=', the list covers any caller. Unset: every tool for everyone.",
    )
    parser.add_argument(
        "--expose-calls", action="store_true", help="serve GET /calls (offline tests only)"
    )
    return parser.parse_args(argv)


def parse_allow(items: Sequence[str]) -> dict[str, frozenset[str]] | None:
    if not items:
        return None
    allow: dict[str, frozenset[str]] = {}
    for item in items:
        token, sep, names = item.partition("=")
        if not sep:
            token, names = ANY_CALLER, item
        tools = frozenset(n.strip() for n in names.split(",") if n.strip())
        unknown = sorted(tools - set(TOOL_NAMES))
        if unknown:
            raise SystemExit(f"--allow: unknown tool {unknown}; known: {list(TOOL_NAMES)}")
        allow[token.strip() or ANY_CALLER] = allow.get(token, frozenset()) | tools
    return allow


def main() -> None:
    args = parse_args()
    state = FakeMcpState(allow=parse_allow(args.allow))
    app = create_app(state, expose_calls=args.expose_calls)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
