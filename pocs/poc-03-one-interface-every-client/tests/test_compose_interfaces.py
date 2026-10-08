"""PoC-3 Compose demo: one agent, every client, LiteLLM's MCP gateway in front of `/v1/mcp`.

Covered (docs/planning/poc/003-PoC-3-one-interface-every-client.md):

- Demo: "The same agent is called from the OpenAI Python SDK, the Anthropic Python SDK, and an
  MCP client ..., once on each engine. Every call streams, and every call shows up in the router."
- Exit criteria: "The OpenAI and Anthropic SDKs work with only a base URL change." and "An MCP
  client lists the agent's tool and runs it."
- Scope item: MCP tools "mounted in the app, with LiteLLM's MCP gateway in front".

The offline part reads the Compose files, `litellm/config.yaml`, the demo script, and the client
script, and checks the security review's conditions for the gateway: LiteLLM reaches
`http://chassis:8080/v1/mcp` over the Compose network only (never the proxy port 8090), no new
published port and none beyond `127.0.0.1`, no key in the config and no static auth header on the
`mcp_servers` entry, no LiteLLM key in a workload, and the LiteLLM image pinned by digest. The
live part runs `deploy/compose/demo-interfaces.sh`; it needs Docker and sockets, so it is marked
`network` and `slow`.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_DIR = ROOT / "deploy/compose"
COMPOSE_FILES = sorted(COMPOSE_DIR.glob("docker-compose*.yaml"))
LITELLM_CONFIG = COMPOSE_DIR / "litellm/config.yaml"
DEMO = COMPOSE_DIR / "demo-interfaces.sh"
CLIENTS = ROOT / "pocs/poc-03-one-interface-every-client/demo/clients.py"
DEMO_DIR = ROOT / "pocs/poc-03-one-interface-every-client/demo"

PUBLISHED = {
    "chassis": "127.0.0.1:8080:8080",
    "litellm": "127.0.0.1:4000:4000",
    "traefik": "127.0.0.1:18080:8000",
}
"""Every published port in every Compose file, and the service that owns it (SECURITY.md, 3).
`traefik` is the PoC-4 scale stack's one loopback port (SECURITY.md, 7)."""
GATEWAY_TARGET = "http://chassis:8080/v1/mcp"
GATEWAY_ENTRY_KEYS = {"url", "transport", "description", "allow_all_keys"}
"""The keys the `mcp_servers` entry may carry: no `headers`, `auth_type`, or credential field."""
WORKLOADS = ("python", "pydanticai", "langgraph", "typescript")
KEY_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{path.relative_to(ROOT)} is not a mapping"
    return data


def _services(path: Path) -> dict[str, dict[str, Any]]:
    services = _load(path).get("services") or {}
    assert isinstance(services, dict)
    return services


def _env_names(service: dict[str, Any]) -> list[str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        return [str(item).split("=", 1)[0] for item in env]
    return [str(k) for k in env]


def _gateway_entry() -> dict[str, Any]:
    servers = _load(LITELLM_CONFIG).get("mcp_servers")
    assert isinstance(servers, dict) and len(servers) == 1, servers
    entry = next(iter(servers.values()))
    assert isinstance(entry, dict)
    return entry


def test_no_compose_file_publishes_a_new_port() -> None:
    """Security review: the gateway adds no published port, and nothing is published beyond
    `127.0.0.1`. Across every Compose file, only the chassis's 8080, LiteLLM's 4000, and the
    PoC-4 scale stack's Traefik on 18080 are published, all on the host's loopback.
    """
    assert COMPOSE_FILES, "no Compose file found"
    for path in COMPOSE_FILES:
        for name, service in _services(path).items():
            assert "expose" not in service, f"{path.name}: {name}"
            assert service.get("network_mode") != "host", f"{path.name}: {name}"
            for port in service.get("ports", []):
                assert str(port) == PUBLISHED.get(name), f"{path.name}: {name} publishes {port}"


def test_gateway_points_at_the_public_mcp_endpoint_on_the_compose_network() -> None:
    """Scope item: LiteLLM's MCP gateway in front of `/v1/mcp`. The entry reaches the chassis by
    its Compose service name on the public port 8080, never the proxy port 8090 (the workload's
    `/mcp`), and over plain HTTP inside the network, not through a host address.
    """
    entry = _gateway_entry()
    assert entry["url"] == GATEWAY_TARGET, entry["url"]
    url = urlsplit(entry["url"])
    assert (url.hostname, url.port, url.path) == ("chassis", 8080, "/v1/mcp"), url
    assert entry.get("transport") == "http", "streamable HTTP is LiteLLM's `http` transport"


def test_gateway_entry_carries_no_auth_header_and_the_config_no_key() -> None:
    """Security review: no key in `config.yaml` (an `os.environ/` reference only), and the
    `mcp_servers` entry carries no static auth header with the chassis or provider key.
    """
    entry = _gateway_entry()
    assert set(entry) <= GATEWAY_ENTRY_KEYS, sorted(entry)
    text = LITELLM_CONFIG.read_text()
    assert "sk-" not in text
    assert not re.search(r"(?im)^\s*(authorization|x-api-key|bearer)\b", text)
    for model in _load(LITELLM_CONFIG)["model_list"]:
        key = model["litellm_params"].get("api_key")
        assert key in (None, "fake") or str(key).startswith("os.environ/"), key
    assert "master_key" not in text.split("# No `general_settings.master_key`")[0]


def test_litellm_image_stays_pinned_by_digest() -> None:
    """Security review: the LiteLLM image stays pinned by digest."""
    image = _services(COMPOSE_DIR / "docker-compose.yaml")["litellm"]["image"]
    assert image.startswith("ghcr.io/berriai/litellm:") and DIGEST.search(image), image


def test_workloads_get_no_litellm_key() -> None:
    """Security review: workload containers get no LiteLLM key. No workload service in any
    Compose file names a key-like variable or loads an `env_file`.
    """
    for path in COMPOSE_FILES:
        for name, service in _services(path).items():
            if not name.startswith("workload-"):
                continue
            assert "env_file" not in service, f"{path.name}: {name}"
            assert [k for k in _env_names(service) if KEY_NAME.search(k)] == [], name


def test_clients_use_only_the_sdks_a_base_url_and_a_placeholder_key() -> None:
    """Exit criterion: the OpenAI and Anthropic SDKs work with only a base URL change; an MCP
    client lists the agent's tool and runs it. The client script imports the two SDKs and
    `fastmcp.Client`, nothing from the chassis, and holds no key-like literal.
    """
    tree = ast.parse(CLIENTS.read_text())
    imported = {
        node.module.split(".")[0] if isinstance(node, ast.ImportFrom) and node.module else ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert {"openai", "anthropic", "fastmcp"} <= imported, imported
    assert not imported & {"chassis", "httpx", "httpx2", "requests"}, imported
    text = CLIENTS.read_text()
    assert "sk-" not in text and "os.environ" not in text
    for needle in ("stream=True", "messages.stream(", "list_tools()", "call_tool(", "/manifest"):
        assert needle in text, needle


def test_demo_script_calls_every_client_on_every_engine() -> None:
    """Demo: once on each engine, every client, through the chassis's published port and the
    gateway on LiteLLM's published port, with the router's lines after each call.
    """
    text = DEMO.read_text()
    assert text.startswith("#!/usr/bin/env bash\n")
    assert os.access(DEMO, os.X_OK)
    for profile in WORKLOADS:
        assert re.search(rf"\b{profile}\b", text), profile
    for needle in (
        "docker-compose.sidecar.yaml",
        "CHASSIS=http://127.0.0.1:8080",
        "GATEWAY=http://127.0.0.1:4000/mcp/",
        "--client openai",
        "--client anthropic",
        "--client mcp",
        "--client manifest",
        "token_log",
        "pocs/poc-03-one-interface-every-client/demo",
        " down",
    ):
        assert needle in text, needle
    assert "8090" not in text, "the demo never touches the proxy port"
    assert DEMO_DIR.is_dir()


@pytest.mark.network
@pytest.mark.slow
def test_demo_runs_every_client_on_every_engine(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    """Demo: the same agent is called from the OpenAI SDK, the Anthropic SDK, and an MCP client
    (directly and through LiteLLM's MCP gateway), once on each engine, and every call shows up in
    the router. Needs Docker and sockets, so it is out of `make test`.
    """
    if request.config.getoption("--disable-socket", default=False):
        pytest.skip("sockets are disabled; run without --disable-socket to cover Compose")
    for tool in ("docker", "uv"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not on PATH")
    info = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
    if info.returncode != 0:
        pytest.skip(f"docker info failed, the daemon is not running: {info.stderr.strip()[:200]}")
    out = tmp_path / "demo.md"
    result = subprocess.run(
        ["bash", str(DEMO)],
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
        env={"PATH": os.environ["PATH"], "HOME": str(Path.home()), "DEMO_OUT": str(out)},
    )
    record = out.read_text() if out.exists() else result.stdout
    assert result.returncode == 0, (result.stderr + record)[-4000:]
    calls = len(WORKLOADS) * 4
    assert record.count("openai: ok") == len(WORKLOADS), record[-4000:]
    assert record.count("anthropic: ok") == len(WORKLOADS), record[-4000:]
    assert record.count("mcp: ok") == 2 * len(WORKLOADS), record[-4000:]
    assert record.count("chassis_agent-echo") >= len(WORKLOADS), "the gateway listed the tool"
    assert "FAILED" not in record and "(no token_log line)" not in record, record[-4000:]
    assert record.count("router (LiteLLM token_log)") == calls
    assert record.count("key-like variables in the workload: none") == len(WORKLOADS)
