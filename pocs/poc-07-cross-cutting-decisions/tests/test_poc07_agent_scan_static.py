"""PoC-7 scope, Security line: "Snyk Agent Scan in CI, run in a sandbox" (plan W5).

Static checks, offline. They support the evidence for exit criteria 5 and 7 (each concern on
every engine with no engine-specific code; the red-team set): the tool descriptions every engine
sees come from the repo's MCP servers, and CI scans those descriptions. What they pin:

- `.github/workflows/poc07-agent-scan.yml`: the triggers, never `pull_request_target`, read-only
  `permissions`, every action pinned by a full commit SHA, the scan run through `scan.sh`, the
  token only in the scan step's env, a skip notice when it is missing, and the report uploaded.
- `deploy/agent-scan/scan.sh`: every `docker run` is sandboxed (read-only root, no capabilities,
  no new privileges, uid 65534, the repo mounted read-only); the inspect phase, which starts the
  repo's MCP servers, has no network and no token.
- `deploy/agent-scan/mcp.json`: every MCP server package in the repo is in the scan config, and
  the stdio launcher serves each one's real tool list (in memory, no socket).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import shlex
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/poc07-agent-scan.yml"
SCAN_DIR = ROOT / "deploy/agent-scan"
SCAN_SH = SCAN_DIR / "scan.sh"
CONFIG = SCAN_DIR / "mcp.json"
LAUNCHER = SCAN_DIR / "stdio_server.py"
TOKEN = "SNYK_TOKEN"
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")

# Packages that build a FastMCP server but are not scanned on their own, with the reason.
NOT_SCANNED = {
    # The chassis re-serves the ToolPort list (adapters/mcp/server.py). Those descriptions come
    # from the upstream servers, which are scanned here.
    "chassis": "re-serves upstream tool descriptions",
}


def _workflow() -> dict[Any, Any]:
    data = yaml.safe_load(WORKFLOW.read_text())
    assert isinstance(data, dict)
    return data


def _steps() -> list[dict[str, Any]]:
    return [step for job in _workflow()["jobs"].values() for step in job["steps"]]


def _docker_runs() -> list[list[str]]:
    """Each `docker run` in scan.sh as tokens, line continuations joined, comments dropped."""
    text = SCAN_SH.read_text()
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    joined = "\n".join(lines).replace("\\\n", " ")
    return [
        shlex.split(line[line.index("docker run") :], comments=True)
        for line in joined.splitlines()
        if re.search(r"\bdocker run\b", line)
    ]


def _flag_values(tokens: list[str], flag: str) -> list[str]:
    values = []
    for i, tok in enumerate(tokens):
        if tok == flag and i + 1 < len(tokens):
            values.append(tokens[i + 1])
        elif tok.startswith(flag + "="):
            values.append(tok.split("=", 1)[1])
    return values


def _mcp_server_packages() -> set[str]:
    """Every package under packages/ whose source (not tests) builds a FastMCP server."""
    found = set()
    for pyproject in ROOT.glob("packages/**/pyproject.toml"):
        if ".venv" in pyproject.parts or "node_modules" in pyproject.parts:
            continue
        src = pyproject.parent / "src"
        if src.is_dir() and any("FastMCP(" in f.read_text() for f in src.rglob("*.py")):
            found.add(tomllib.loads(pyproject.read_text())["project"]["name"])
    return found


def _launcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_scan_stdio_server", LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the workflow -------------------------------------------------------------------------


def test_workflow_triggers_and_never_pull_request_target() -> None:
    code = [ln for ln in WORKFLOW.read_text().splitlines() if not ln.lstrip().startswith("#")]
    assert not any("pull_request_target" in ln for ln in code)
    # PyYAML reads the bare key `on` as True.
    triggers = _workflow()[True]
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch"}


def test_workflow_permissions_are_read_only() -> None:
    wf = _workflow()
    assert wf["permissions"] in ({}, {"contents": "read"})
    for job in wf["jobs"].values():
        assert job.get("permissions", {}) in ({}, {"contents": "read"})


def test_every_action_is_pinned_by_a_full_commit_sha() -> None:
    uses = [step["uses"] for step in _steps() if "uses" in step]
    assert uses, "the workflow uses no action"
    assert [u for u in uses if not PINNED.match(u)] == []


def test_checkout_keeps_no_token() -> None:
    checkout = [s for s in _steps() if s.get("uses", "").startswith("actions/checkout@")]
    assert checkout and all(s["with"]["persist-credentials"] is False for s in checkout)


def test_the_scan_runs_through_scan_sh_and_the_token_is_only_in_its_env() -> None:
    steps = _steps()
    scan = [s for s in steps if "deploy/agent-scan/scan.sh" in s.get("run", "")]
    assert len(scan) == 1
    assert f"secrets.{TOKEN}" in scan[0]["env"][TOKEN]
    others = [s for s in steps if s is not scan[0]]
    assert all(f"secrets.{TOKEN}" not in json.dumps(s) for s in others)
    # The secret never goes to an action's inputs.
    assert all(f"secrets.{TOKEN}" not in json.dumps(s.get("with", {})) for s in steps)


def test_a_missing_token_is_a_clear_skip_not_a_failure() -> None:
    text = SCAN_SH.read_text()
    assert re.search(rf'-z "\$\{{{TOKEN}:-\}}"', text), "scan.sh does not test for a missing token"
    assert "::notice" in text and "skip" in text.lower()


def test_the_report_is_uploaded_as_an_artifact() -> None:
    upload = [s for s in _steps() if s.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(upload) == 1
    assert upload[0]["if"] == "always()"
    assert "agent-scan" in upload[0]["with"]["path"]


# --- the sandbox ----------------------------------------------------------------------------


def test_every_docker_run_is_sandboxed() -> None:
    runs = _docker_runs()
    assert len(runs) >= 2, "expected an inspect run and a scan run"
    for tokens in runs:
        assert "--rm" in tokens
        assert "--read-only" in tokens
        assert "ALL" in _flag_values(tokens, "--cap-drop")
        assert "no-new-privileges" in _flag_values(tokens, "--security-opt")
        assert _flag_values(tokens, "--user") == ["65534:65534"]
        assert _flag_values(tokens, "--network"), "every run names its network"
        mounts = _flag_values(tokens, "-v") + _flag_values(tokens, "--volume")
        repo = [m for m in mounts if m.split(":")[1:2] == ["/repo"]]
        assert repo and all(m.endswith(":ro") for m in repo)
        # Only the token is passed in, by name; no env file, no other variable.
        assert _flag_values(tokens, "--env-file") == []
        assert set(_flag_values(tokens, "-e") + _flag_values(tokens, "--env")) <= {TOKEN}
        assert "--privileged" not in tokens


def test_the_inspect_phase_has_no_network_and_no_token() -> None:
    inspect = [t for t in _docker_runs() if "inspect" in t]
    assert len(inspect) == 1
    assert _flag_values(inspect[0], "--network") == ["none"]
    assert TOKEN not in " ".join(inspect[0])


# --- the scan config ------------------------------------------------------------------------


def test_every_mcp_server_package_is_in_the_scan_config() -> None:
    servers = json.loads(CONFIG.read_text())["mcpServers"]
    packages = _mcp_server_packages()
    assert {"fake-mcp-server", "code-runner"} <= packages
    assert packages - set(NOT_SCANNED) <= set(servers)
    for name, server in servers.items():
        assert server["type"] == "stdio"
        assert server["args"] == ["deploy/agent-scan/stdio_server.py", name]
        assert "env" not in server


@pytest.mark.parametrize(
    ("name", "tools"),
    [
        (
            "fake-mcp-server",
            {"glossary_lookup", "acronym_expand", "note_write", "unlisted_probe"},
        ),
        ("code-runner", {"run_python"}),
    ],
)
def test_the_launcher_serves_each_servers_real_tools(name: str, tools: set[str]) -> None:
    from fastmcp import Client

    async def listed() -> dict[str, str]:
        async with Client(_launcher().build(name)) as client:
            return {t.name: t.description or "" for t in await client.list_tools()}

    got = asyncio.run(listed())
    assert set(got) == tools
    assert all(got.values()), "a tool with no description gives the scan nothing to score"


def test_the_launcher_refuses_an_unknown_server() -> None:
    with pytest.raises(SystemExit):
        _launcher().build("not-a-server")
