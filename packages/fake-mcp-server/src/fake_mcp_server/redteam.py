"""The red-team tool server (PoC-8): the MCP server behind the chassis in `bakeoff.redteam`.

One read-only tool, `fetch_page(page, text)`. It returns the scripted payload for `page` (an
injection, a key, an exfiltration request: the red-team set's tool outputs), or `stored`, and
records every call so the runner can tell whether a tool ran and with which arguments. The pages
come from the caller (`chassis_contracts.redteam.live.tool_pages`); this module knows no case.

Not served by the agent-scan launcher (`deploy/agent-scan/stdio_server.py` builds
`create_server`); its one description is plain text, and its outputs are hostile on purpose.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastmcp import FastMCP

__all__ = ["REDTEAM_TOOL", "create_redteam_server"]

REDTEAM_TOOL = "fetch_page"


def create_redteam_server(pages: Mapping[str, str]) -> tuple[FastMCP, list[dict[str, str]]]:
    """The server, and the list it appends each call's arguments to."""
    calls: list[dict[str, str]] = []
    server = FastMCP("redteam-tools")

    @server.tool(
        name=REDTEAM_TOOL,
        description="Fetch a page by name, or store a note in `text`.",
        annotations={"readOnlyHint": True},
    )
    def fetch_page(page: str = "", text: str = "") -> str:
        calls.append({"page": page, "text": text})
        return pages.get(page, "stored")

    return server, calls
