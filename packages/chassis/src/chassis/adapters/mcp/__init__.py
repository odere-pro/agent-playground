"""MCP with FastMCP; `fastmcp` and `mcp` are imported only here. `server`: `ToolPort` served to
workloads (`/mcp` on the proxy port). `agent`: the agent as one tool for clients (`/v1/mcp` on the
public port), generated from the OpenAPI spec.
"""

from chassis.adapters.mcp.agent import build_agent_mcp
from chassis.adapters.mcp.server import PortTool, build_mcp_server

__all__ = ["PortTool", "build_agent_mcp", "build_mcp_server"]
