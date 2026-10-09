"""`Manifest` v0: what this chassis serves, for `GET /manifest` (PoC-3 open note, section 8).

suggested: the whole shape. Published as `schemas/manifest.v0.json` (`make schemas`). No network,
no product SDK, no web framework: `chassis.server.manifest` fills it per request from the config,
the OpenAPI spec, and the agent MCP server's tools.

`interfaces` lists native first, then each other interface that is mounted and switched on, in
the spec's order, then MCP. An HTTP interface is one OpenAPI operation; `model` is the value a
chat format's `model` must hold (the agent's name), None for native. `ManifestAgent.trust` is the
agent's `spec.trust` (PoC-5). Image digests, scopes, events, governance, class, kind, and task
arrive later (051 H-13).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from chassis.core.envelope import Versions

__all__ = [
    "MANIFEST_VERSION",
    "HttpInterface",
    "Manifest",
    "ManifestAgent",
    "McpInterface",
    "OpenAPIRef",
]

MANIFEST_VERSION = "0"


class ManifestAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    trust: Literal["trusted", "untrusted"] = "trusted"
    """`spec.trust` (PoC-5). Shown to callers; not a security control by itself."""


class HttpInterface(BaseModel):
    """One interface operation in the OpenAPI spec (`x-chassis-interface`)."""

    model_config = ConfigDict(extra="forbid")
    name: str
    method: str
    path: str
    operation_id: str
    streaming: bool
    """The operation declares a `text/event-stream` 200."""
    model: str | None = None
    """What the body's `model` must be (the agent name); None when the format has no `model`."""


class McpInterface(BaseModel):
    """The agent as MCP tools; not in the OpenAPI spec."""

    model_config = ConfigDict(extra="forbid")
    name: Literal["mcp"] = "mcp"
    path: str
    transport: Literal["streamable-http"] = "streamable-http"
    streaming: Literal[False] = False
    """A tool call answers once (section 7)."""
    tools: list[str]


class OpenAPIRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None
    """Where the app serves its spec; None when it does not."""
    version: str
    sha256: str
    """Over the spec as JSON with sorted keys and no spaces (`separators=(",", ":")`)."""


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest_version: Literal["0"] = "0"
    agent: ManifestAgent
    versions: Versions
    lane: str
    """`spec.engine.connector`."""
    event_schema_versions: list[str]
    """The event `schema_version`s this chassis accepts."""
    interfaces: list[HttpInterface | McpInterface]
    openapi: OpenAPIRef
