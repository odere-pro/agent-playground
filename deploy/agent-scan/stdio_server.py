"""Serve one of the repo's MCP servers over stdio, for Agent Scan only.

`python deploy/agent-scan/stdio_server.py fake-mcp-server|code-runner`

Agent Scan starts this as a stdio subprocess and sends `initialize`, `tools/list`,
`prompts/list`, and `resources/list`. It never calls a tool. The server object is the one each
package builds for its HTTP entry point (`create_server`), so the descriptions scanned are the
descriptions served. It runs only inside the scan sandbox (`scan.sh`).

The scanner hands a stdio server only HOME, LOGNAME, PATH, SHELL, TERM, and USER, so no
PYTHONPATH arrives: the package sources are put on `sys.path` here, from this file's place in the
checkout. Their third-party deps come from the interpreter's venv (`/app/.venv` in the image).
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastmcp import FastMCP

SERVERS = ("fake-mcp-server", "code-runner")
REPO = Path(__file__).resolve().parents[2]
for _src in (REPO / "packages" / name / "src" for name in SERVERS):
    if str(_src) not in sys.path:
        sys.path.append(str(_src))


def build(name: str) -> FastMCP:
    if name == "fake-mcp-server":
        from fake_mcp_server.server import FakeMcpState
        from fake_mcp_server.server import create_server as fake_server

        return fake_server(FakeMcpState())
    if name == "code-runner":
        from code_runner.server import create_server as runner_server

        return runner_server()
    raise SystemExit(f"unknown server {name!r}; known: {', '.join(SERVERS)}")


def main(argv: list[str]) -> None:
    if len(argv) != 1:
        raise SystemExit(f"usage: stdio_server.py {'|'.join(SERVERS)}")
    build(argv[0]).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main(sys.argv[1:])
