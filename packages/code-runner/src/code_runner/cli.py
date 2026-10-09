"""Two modes, one image.

`code-runner --host 0.0.0.0 --port 8000`: serve `run_python` over streamable HTTP at `/mcp`.
Run it only inside the sandbox pod (gVisor, no egress, PID and memory limits, memory-backed
`/tmp`). The pod is the security boundary; this process is not.

`code-runner dispatch --pool code-runner --namespace poc05-tools`: the dispatcher. It serves
the same tool and runs each call in a fresh sandbox claimed from the pool. It runs no code.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

DEFAULT_PORT = 8000  # suggested


def serve_parser() -> argparse.ArgumentParser:
    # Imported here so the dispatcher's parser needs nothing from the server side.
    from code_runner.server import CACHE_SIZE

    parser = argparse.ArgumentParser(description="The code-execution MCP server (run_python)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--tmp-root", default=None, help="where each call's fresh directory goes (default: TMPDIR)"
    )
    parser.add_argument("--cache-size", type=int, default=CACHE_SIZE)
    return parser


def dispatch_parser() -> argparse.ArgumentParser:
    from code_runner.dispatch import CA_FILE, TOKEN_FILE
    from code_runner.server import CACHE_SIZE

    parser = argparse.ArgumentParser(
        prog="code-runner dispatch", description="Run each run_python call in a fresh sandbox"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--pool", required=True, help="the SandboxWarmPool to claim from")
    parser.add_argument("--namespace", required=True, help="where the pool and claims live")
    parser.add_argument("--token-file", default=TOKEN_FILE, help="the projected token")
    parser.add_argument("--ca-file", default=CA_FILE, help="the cluster CA")
    parser.add_argument("--cache-size", type=int, default=CACHE_SIZE)
    return parser


def _serve(argv: list[str]) -> None:
    from code_runner.isolation import harden_server
    from code_runner.server import create_server

    args = serve_parser().parse_args(argv)
    # Linux: subreaper and not dumpable before any call; it fails here, not on the first call.
    harden_server()
    server = create_server(tmp_root=args.tmp_root, cache_size=args.cache_size)
    app = server.http_app(path="/mcp", stateless_http=True)
    uvicorn.run(app, host=args.host, port=args.port)


def _dispatch(argv: list[str]) -> None:
    from code_runner.dispatch import (
        DispatchConfig,
        Dispatcher,
        api_client_from_env,
        create_dispatch_server,
        sandbox_client,
    )

    args = dispatch_parser().parse_args(argv)
    api = api_client_from_env(os.environ, token_file=args.token_file, ca_file=args.ca_file)
    config = DispatchConfig(namespace=args.namespace, pool=args.pool)
    dispatcher = Dispatcher(api, sandbox_client(), config, cache_size=args.cache_size)
    app = create_dispatch_server(dispatcher).http_app(path="/mcp", stateless_http=True)
    uvicorn.run(app, host=args.host, port=args.port)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args[:1] == ["dispatch"]:
        _dispatch(args[1:])
    else:
        _serve(args)


if __name__ == "__main__":
    main()
