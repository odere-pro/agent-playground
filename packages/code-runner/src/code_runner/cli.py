"""`code-runner --host 0.0.0.0 --port 8000`: serve `run_python` over streamable HTTP at `/mcp`.

Run it only inside the sandbox pod (gVisor, no egress, PID and memory limits, memory-backed
`/tmp`). The pod is the security boundary; this process is not.
"""

from __future__ import annotations

import argparse
import logging

import uvicorn

from code_runner.isolation import harden_server
from code_runner.server import CACHE_SIZE, create_server

DEFAULT_PORT = 8000  # suggested


def main() -> None:
    parser = argparse.ArgumentParser(description="The code-execution MCP server (run_python)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--tmp-root", default=None, help="where each call's fresh directory goes (default: TMPDIR)"
    )
    parser.add_argument("--cache-size", type=int, default=CACHE_SIZE)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Linux: subreaper and not dumpable before any call; it fails here, not on the first call.
    harden_server()
    server = create_server(tmp_root=args.tmp_root, cache_size=args.cache_size)
    app = server.http_app(path="/mcp", stateless_http=True)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
