"""`GET /manifest` v0 (PoC-3 open note, section 8): built per request from the config and the
OpenAPI spec, plus the MCP tools the agent MCP server lists.

The OpenAI and Anthropic routers are built elsewhere, so these tests drive the interface list from
the spec: a stub route carrying `x-chassis-interface` stands in for them.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import jsonschema
from chassis import CHASSIS_VERSION
from chassis.core.handle import echo
from chassis.core.manifest import MANIFEST_VERSION, Manifest
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.schemas import SCHEMA_DIR, generate, render
from chassis.server import ChassisConfig, create_app
from chassis.server.interfaces.errors import INTERFACE_KEY
from chassis.server.interfaces.mcp import mount_agent_mcp
from chassis.server.interfaces.native import native_router
from chassis.server.manifest import mount_manifest
from chassis.server.pipeline import RunPipeline
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "fake-route"},
        "prompt": {"version": "simplifier-v1"},
    },
}


def _config(**interfaces: bool) -> ChassisConfig:
    spec = {**CONFIG["spec"], "interfaces": interfaces}
    return ChassisConfig.model_validate({**CONFIG, "spec": spec})


def _ports() -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://c") as client:
        return await client.get(path)


async def _manifest(app: FastAPI) -> dict[str, Any]:
    response = await _get(app, "/manifest")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


class _Chat(BaseModel):
    model: str


def _stub_router(name: str, path: str, *, streaming: bool) -> APIRouter:
    """A route that looks like an interface in the spec, without its code."""
    router = APIRouter()
    responses: dict[int | str, dict[str, Any]] = {}
    if streaming:
        responses[200] = {"content": {"text/event-stream": {"schema": {"type": "string"}}}}

    @router.post(
        path, operation_id=f"stub_{name}", responses=responses, openapi_extra={INTERFACE_KEY: name}
    )
    async def stub(body: _Chat) -> dict[str, str]:
        return {}

    return router


def _stub_app(config: ChassisConfig) -> FastAPI:
    """Native, two stub interfaces, `/manifest`, then the agent MCP when on: the order
    `create_app` uses, without the OpenAI and Anthropic routers.
    """
    app = FastAPI()
    app.state.config = config
    app.state.pipeline = RunPipeline(app.state)
    mount_manifest(app)
    app.include_router(native_router(app.state.pipeline))
    flags = config.spec.interfaces
    if flags.openai:
        app.include_router(_stub_router("openai", "/v1/chat/completions", streaming=True))
    if flags.anthropic:
        app.include_router(_stub_router("anthropic", "/v1/messages", streaming=False))
    if flags.mcp:
        mount_agent_mcp(app, config)
    return app


def _spec_interfaces(spec: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """(name, method, path, operation_id) of every operation carrying `x-chassis-interface`."""
    out = []
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if isinstance(op, dict) and INTERFACE_KEY in op:
                out.append((op[INTERFACE_KEY], method.upper(), path, op["operationId"]))
    return out


# --- shape -------------------------------------------------------------------------------------


async def test_manifest_matches_the_config_and_the_openapi_spec() -> None:
    app = create_app(_config(), _ports())
    manifest = await _manifest(app)
    spec = (await _get(app, "/openapi.json")).json()
    assert manifest["manifest_version"] == MANIFEST_VERSION == "0"
    assert manifest["agent"] == {"name": "echo", "version": "0.0.1", "trust": "trusted"}
    assert manifest["versions"] == {
        "chassis": CHASSIS_VERSION,
        "config": "cfg-1",
        "prompt": "simplifier-v1",
        "model_route": "fake-route",
    }
    assert manifest["lane"] == "inprocess"
    assert manifest["event_schema_versions"] == ["0"]
    http = [i for i in manifest["interfaces"] if i["name"] != "mcp"]
    listed = [(i["name"], i["method"], i["path"], i["operation_id"]) for i in http]
    assert listed == _spec_interfaces(spec)
    assert listed[0] == ("native", "POST", "/v1/run", "run")
    assert http[0]["streaming"] is True and http[0]["model"] is None
    for entry in http[1:]:
        assert entry["model"] == "echo", "a chat format's `model` names the agent"
    (mcp,) = [i for i in manifest["interfaces"] if i["name"] == "mcp"]
    assert mcp == {
        "name": "mcp",
        "path": "/v1/mcp",
        "transport": "streamable-http",
        "streaming": False,
        "tools": ["echo"],
    }
    digest = hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert manifest["openapi"] == {"path": "/openapi.json", "version": "3.1.0", "sha256": digest}


async def test_manifest_validates_against_the_published_schema() -> None:
    manifest = await _manifest(create_app(_config(), _ports()))
    schema = json.loads((SCHEMA_DIR / "manifest.v0.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(manifest)
    Manifest.model_validate(manifest)


def test_manifest_route_is_in_the_spec_with_its_model() -> None:
    spec = create_app(_config(), _ports()).openapi()
    op = spec["paths"]["/manifest"]["get"]
    assert op["operationId"] == "manifest"
    assert INTERFACE_KEY not in op, "the manifest is not an interface"
    ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref == "#/components/schemas/Manifest"


async def test_manifest_is_not_an_mcp_tool() -> None:
    app = create_app(_config(), _ports())
    tools = await app.state.agent_mcp.list_tools()
    assert [t.name for t in tools] == ["echo"]


async def test_manifest_needs_no_lifespan() -> None:
    """Built from the config and the spec, so it answers before the engine is ready."""
    response = await _get(create_app(_config(), _ports()), "/manifest")
    assert response.status_code == 200


# --- interfaces from the spec ------------------------------------------------------------------


async def test_lists_the_interfaces_the_spec_carries() -> None:
    manifest = await _manifest(_stub_app(_config()))
    assert manifest["interfaces"] == [
        {
            "name": "native",
            "method": "POST",
            "path": "/v1/run",
            "operation_id": "run",
            "streaming": True,
            "model": None,
        },
        {
            "name": "openai",
            "method": "POST",
            "path": "/v1/chat/completions",
            "operation_id": "stub_openai",
            "streaming": True,
            "model": "echo",
        },
        {
            "name": "anthropic",
            "method": "POST",
            "path": "/v1/messages",
            "operation_id": "stub_anthropic",
            "streaming": False,
            "model": "echo",
        },
        {
            "name": "mcp",
            "path": "/v1/mcp",
            "transport": "streamable-http",
            "streaming": False,
            "tools": ["echo"],
        },
    ]


async def test_an_interface_switched_off_is_not_listed() -> None:
    manifest = await _manifest(_stub_app(_config(openai=False, mcp=False)))
    assert [i["name"] for i in manifest["interfaces"]] == ["native", "anthropic"]


async def test_a_route_in_the_spec_for_an_interface_switched_off_is_not_listed() -> None:
    """The spec is the source and the config confirms it: a stray route does not list."""
    config = _config(anthropic=False, mcp=False)  # MCP off: the spec is not read until asked
    app = _stub_app(config)
    app.include_router(_stub_router("anthropic", "/stray", streaming=False))
    assert "/stray" in app.openapi()["paths"]
    manifest = await _manifest(app)
    assert [i["name"] for i in manifest["interfaces"]] == ["native", "openai"]


async def test_every_interface_off_leaves_native_only() -> None:
    config = _config(openai=False, anthropic=False, mcp=False)
    manifest = await _manifest(create_app(config, _ports()))
    assert [i["name"] for i in manifest["interfaces"]] == ["native"]


async def test_the_sha256_follows_the_spec() -> None:
    on = await _manifest(_stub_app(_config()))
    off = await _manifest(_stub_app(_config(openai=False)))
    assert on["openapi"]["sha256"] != off["openapi"]["sha256"]
    again = await _manifest(_stub_app(_config()))
    assert on["openapi"]["sha256"] == again["openapi"]["sha256"]


# --- schema ------------------------------------------------------------------------------------


def test_manifest_schema_file_is_published_and_matches() -> None:
    generated = generate()
    assert "manifest.v0.json" in generated
    assert (SCHEMA_DIR / "manifest.v0.json").read_text() == render(generated["manifest.v0.json"])
    assert generated["manifest.v0.json"] == Manifest.model_json_schema()


async def test_manifest_agent_trust_defaults_to_trusted() -> None:
    manifest = await _manifest(_stub_app(_config()))
    assert manifest["agent"]["trust"] == "trusted"


async def test_manifest_agent_trust_shows_untrusted_with_the_remote_lane() -> None:
    engine = {
        "connector": "remote",
        "url": "http://workload:8080",
        "auth": {"scheme": "bearer", "token_env": "REMOTE_TOKEN"},
    }
    spec = {**CONFIG["spec"], "engine": engine, "trust": "untrusted"}
    config = ChassisConfig.model_validate({**CONFIG, "spec": spec})
    manifest = await _manifest(_stub_app(config))
    assert manifest["agent"]["trust"] == "untrusted"
    assert manifest["lane"] == "remote"
