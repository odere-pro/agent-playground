"""`GET /manifest` v0 (PoC-3 open note, section 8): what this chassis serves, built on each request.

- From the config: the agent, the versions, the lane (`spec.engine.connector`), and which
  interfaces `spec.interfaces` switches on.
- From the OpenAPI spec (`app.openapi()["paths"]`, not `app.routes`: FastAPI 0.141 keeps an
  included router as one lazy entry): one entry per operation carrying `x-chassis-interface`.
  An operation is listed only when its interface is native or switched on; `streaming` is a
  declared `text/event-stream` 200. The spec's sha256 is over its JSON with sorted keys.
- From the agent MCP server (`app.state.agent_mcp`, set by `interfaces.mcp`): the MCP entry and
  its tool names, when MCP is mounted and on.

`mount_manifest(app)` runs in `create_app` before `mount_interfaces`. It needs no lifespan: it
answers before ready. The first request builds the whole spec (about 0.25 s; FastAPI caches it),
which start-up never does.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import FastAPI

from chassis.core.events import SUPPORTED_SCHEMA_VERSIONS
from chassis.core.manifest import (
    HttpInterface,
    Manifest,
    ManifestAgent,
    McpInterface,
    OpenAPIRef,
)
from chassis.server.config import ChassisConfig
from chassis.server.interfaces.errors import INTERFACE_KEY
from chassis.server.interfaces.mcp import MCP_PATH, STATE_KEY
from chassis.server.pipeline import versions_for

__all__ = ["MANIFEST_PATH", "build_manifest", "mount_manifest", "spec_sha256"]

MANIFEST_PATH = "/manifest"
_NATIVE = "native"
_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


def spec_sha256(spec: dict[str, Any]) -> str:
    """sha256 of the spec as JSON, sorted keys, no spaces."""
    raw = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _streams(operation: dict[str, Any]) -> bool:
    ok = operation.get("responses", {}).get("200", {})
    return "text/event-stream" in ok.get("content", {})


def _http_interfaces(spec: dict[str, Any], config: ChassisConfig) -> list[HttpInterface]:
    switched = config.spec.interfaces.model_dump()
    found: list[HttpInterface] = []
    for path, item in spec.get("paths", {}).items():
        for method, operation in item.items():
            if method not in _METHODS or not isinstance(operation, dict):
                continue
            name = operation.get(INTERFACE_KEY)
            if not isinstance(name, str) or not switched.get(name, True):
                continue
            found.append(
                HttpInterface(
                    name=name,
                    method=method.upper(),
                    path=path,
                    operation_id=operation.get("operationId", ""),
                    streaming=_streams(operation),
                    model=None if name == _NATIVE else config.agent.name,
                )
            )
    return sorted(found, key=lambda i: i.name != _NATIVE)  # stable: native first, then spec order


async def build_manifest(app: FastAPI) -> Manifest:
    """The manifest of `app` now. `app.state.config` must be set."""
    config: ChassisConfig = app.state.config
    spec = app.openapi()
    interfaces: list[HttpInterface | McpInterface] = [*_http_interfaces(spec, config)]
    server = getattr(app.state, STATE_KEY, None)
    if server is not None and config.spec.interfaces.mcp:
        tools = [tool.name for tool in await server.list_tools()]
        interfaces.append(McpInterface(path=MCP_PATH, tools=tools))
    return Manifest(
        agent=ManifestAgent(
            name=config.agent.name, version=config.agent.version, trust=config.spec.trust
        ),
        versions=versions_for(config),
        lane=config.spec.engine.connector,
        event_schema_versions=list(SUPPORTED_SCHEMA_VERSIONS),
        interfaces=interfaces,
        openapi=OpenAPIRef(
            path=app.openapi_url, version=str(spec["openapi"]), sha256=spec_sha256(spec)
        ),
    )


def mount_manifest(app: FastAPI) -> None:
    """`GET /manifest` on `app`."""

    @app.get(MANIFEST_PATH, operation_id="manifest", response_model=Manifest)
    async def manifest() -> Manifest:
        return await build_manifest(app)
