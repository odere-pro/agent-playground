"""The fake MCP server (PoC-5, plan section 2.7). Never imports `chassis`."""

from fake_mcp_server.server import (
    GLOSSARY,
    PROBE_MARKER,
    TOOL_NAMES,
    FakeMcpState,
    create_app,
    create_server,
)

__all__ = [
    "GLOSSARY",
    "PROBE_MARKER",
    "TOOL_NAMES",
    "FakeMcpState",
    "create_app",
    "create_server",
]
