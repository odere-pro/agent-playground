"""PoC-2 boundaries: no framework in the chassis, no key in any workload.

Each test names the exit criterion in docs/planning/poc/002-PoC-2-two-engines-one-contract.md it
covers. The file checks read `pyproject.toml`, the Compose files, and the workload sources and
Dockerfiles; the runtime check plants fake keys in the environment and watches the workload's
outbound calls (routed into the chassis over ASGI, no network).
"""

from __future__ import annotations

import asyncio
import re
import shlex
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml
from poc02_harness import (
    MODEL_URL,
    ROOT,
    SIMPLIFY,
    TIMEOUT_S,
    chassis_app,
    engine_params,
    route_outbound,
    run_handle,
    running,
)

FRAMEWORK_RULE = "no agent framework anywhere in the chassis"
FRAMEWORK_DISTRIBUTIONS = ("pydantic-ai", "pydantic_ai", "langgraph", "langchain", "crewai")
"""Distribution names (and prefixes) that are an agent framework. `agents` is exact, below."""
FRAMEWORK_EXACT = ("agents", "openai-agents", "smolagents", "claude-agent-sdk")
COMPOSE_FILES = [
    ROOT / "deploy/compose/docker-compose.yaml",
    ROOT / "deploy/compose/docker-compose.local.yaml",
    ROOT / "deploy/compose/docker-compose.sidecar.yaml",
]
NOT_WORKLOADS = frozenset({"chassis", "litellm", "fake-model-server"})
KEY_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
"""A key-like variable name, the same rule as test_compose_sidecar.py. Names only, never values,
so `CHASSIS_MODEL_URL` and `CHASSIS_TOOL_URL` never match."""
ENV_READ = re.compile(
    r"""(?:
        \benviron\s*(?:\.\s*get\s*\(|\[)      # os.environ.get("X"), os.environ["X"]
      | \bgetenv\s*\(                         # os.getenv("X")
      | \bprocess\.env\s*(?:\?\.|\.|\[)       # process.env.X, process.env?.X, process.env["X"]
      | \bDeno\.env\s*\.\s*get\s*\(            # Deno.env.get("X")
    )\s*["'`]?(?P<name>[A-Za-z_]\w*)""",
    re.VERBOSE,
)
"""Reading one variable from the environment; `name` is the variable."""
WORKLOADS = ROOT / "packages/workloads"
PLANTED = "sk-poc02-planted-not-a-real-key"


def _requirement_name(spec: str) -> str:
    return re.split(r"[\s\[<>=!~;@]", spec.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _is_framework(name: str) -> bool:
    return name in FRAMEWORK_EXACT or name.startswith(
        tuple(d.replace("_", "-") for d in FRAMEWORK_DISTRIBUTIONS)
    )


@pytest.mark.slow
def test_import_lint_keeps_the_no_framework_rule() -> None:
    """Exit criterion: the chassis has no import of any framework (lint passes). Runs
    `lint-imports` (the console script; `python -m importlinter.cli` runs nothing) and needs the
    rule "no agent framework anywhere in the chassis" kept and no contract broken.
    """
    lint = Path(sys.executable).with_name("lint-imports")
    result = subprocess.run([str(lint)], cwd=ROOT, capture_output=True, text=True, check=False)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out[-4000:]
    assert f"{FRAMEWORK_RULE} KEPT" in out, out[-4000:]
    assert " 0 broken" in out, out[-4000:]


def _framework_rule() -> dict[str, Any]:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        contracts = tomllib.load(fh)["tool"]["importlinter"]["contracts"]
    rules = [c for c in contracts if c["name"] == FRAMEWORK_RULE]
    assert len(rules) == 1, f"one import-linter contract named {FRAMEWORK_RULE!r}"
    rule: dict[str, Any] = rules[0]
    return rule


def test_the_rule_covers_the_chassis_and_the_frameworks() -> None:
    """Exit criterion: the chassis has no import of any framework (lint passes). The rule is a
    `forbidden` contract over all of `chassis`, it counts indirect imports, and it forbids both
    PoC-2 frameworks and the other agent SDKs.
    """
    rule = _framework_rule()
    assert rule["type"] == "forbidden"
    assert rule["source_modules"] == ["chassis"]
    assert "allow_indirect_imports" not in rule
    assert {"pydantic_ai", "langgraph", "agents", "crewai"} <= set(rule["forbidden_modules"])


def test_the_rule_also_forbids_langchain() -> None:
    """Exit criterion: the chassis has no import of any framework (lint passes). LangGraph pulls
    in `langchain_core`; an import of it in the chassis is a framework import too.
    """
    assert {"langchain", "langchain_core"} <= set(_framework_rule()["forbidden_modules"])


def test_no_framework_is_a_chassis_dependency() -> None:
    """Exit criterion: the chassis has no import of any framework (lint passes). The chassis
    package does not depend on one either, so no framework is even installed with it.
    """
    with (ROOT / "packages/chassis/pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)["project"]
    specs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        specs.extend(extra)
    names = [_requirement_name(s) for s in specs]
    assert names, "the chassis names no dependencies; wrong file?"
    assert [n for n in names if _is_framework(n)] == []


# --- No key in any workload container ---


def _services(path: Path) -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(path.read_text()) or {}
    services = data.get("services") or {}
    assert isinstance(services, dict)
    return services


def _environment(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        pairs = [str(item).split("=", 1) for item in env]
        return {p[0]: (p[1] if len(p) > 1 else "") for p in pairs}
    return {str(k): "" if v is None else str(v) for k, v in env.items()}


def _workload_services() -> Iterator[tuple[str, str, dict[str, Any]]]:
    for path in COMPOSE_FILES:
        for name, service in _services(path).items():
            if name not in NOT_WORKLOADS:
                yield path.name, name, service


def _key_reads(text: str) -> list[str]:
    """Each environment read in `text` whose variable name is key-like."""
    return [m.group(0) for m in ENV_READ.finditer(text) if KEY_NAME.search(m.group("name"))]


@pytest.mark.parametrize(
    "source",
    [
        'os.environ.get("OPENAI_API_KEY")',
        "os.environ.get('LITELLM_API_KEY', '')",
        'os.environ["HF_TOKEN"]',
        "environ [ 'DB_PASSWORD' ]",
        'os.getenv("CLIENT_SECRET")',
        "getenv('api_key')",
        "process.env.OPENAI_API_KEY",
        "process.env?.GITHUB_TOKEN",
        'process.env["ANTHROPIC_API_KEY"]',
        "process.env['MasterKey']",
        'Deno.env.get("SERVICE_TOKEN")',
    ],
)
def test_key_read_catches(source: str) -> None:
    """Exit criterion: no workload container holds a key. The source check catches a read of any
    key-like name in every Python, Node, and Deno form.
    """
    assert len(_key_reads(source)) == 1, source


@pytest.mark.parametrize(
    "source",
    [
        'os.environ.get("CHASSIS_MODEL_URL", "http://127.0.0.1:8090/v1")',
        'os.environ["CHASSIS_TOOL_URL"]',
        'os.getenv("PORT")',
        'process.env["HOST"] ?? "127.0.0.1"',
        "process.env.ALLOW_ANY_HOST",
        'Deno.env.get("PORT")',
        'API_KEY_HEADER = "x-api-key"',
        "max_tokens = int(input_tokens)",
    ],
)
def test_key_read_ignores(source: str) -> None:
    """Exit criterion: no workload container holds a key. The source check matches names, not
    values: the proxy URLs, the bind, and a key-like word outside an environment read pass.
    """
    assert _key_reads(source) == [], source


def _assert_points_at_the_proxy(where: str, env: dict[str, str]) -> None:
    url = env.get("CHASSIS_MODEL_URL")
    if url is not None:
        assert urlparse(url).hostname == "127.0.0.1", f"{where}: CHASSIS_MODEL_URL={url}"


def test_no_workload_service_holds_a_key() -> None:
    """Exit criterion: no workload container holds a key. Each workload's model client points at
    the chassis model proxy. In every Compose file, every service that is not the chassis, the
    router, or the fake model server sets no key-like variable (`KEY`, `TOKEN`, `SECRET`,
    `PASSWORD`, any case), loads no
    `env_file`, and points `CHASSIS_MODEL_URL` (when set) at `127.0.0.1`. The workload riding in
    the chassis process (echo-python, `inprocess`) points at the chassis's own proxy.
    """
    for file, name, service in _workload_services():
        env = _environment(service)
        where = f"{file}: {name}"
        assert [k for k in env if KEY_NAME.search(k)] == [], f"{where} holds a key"
        assert "env_file" not in service, f"{where} loads an env_file"
        _assert_points_at_the_proxy(where, env)
    chassis = _environment(_services(COMPOSE_FILES[0])["chassis"])
    assert urlparse(chassis["CHASSIS_MODEL_URL"]).hostname == "127.0.0.1"


def _dockerfile_env(text: str) -> dict[str, str]:
    """The `ENV` and `ARG` names and values in a Dockerfile's text. A backslash at the end of a
    line continues the instruction on the next one; comment lines inside it are skipped.
    """
    logical: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if pending and line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        logical.append(pending + line)
        pending = ""
    if pending:
        logical.append(pending)
    env: dict[str, str] = {}
    for line in logical:
        parts = line.split(None, 1)
        if len(parts) < 2 or parts[0].upper() not in ("ENV", "ARG"):
            continue
        rest = parts[1]
        if "=" in rest.split(None, 1)[0]:
            for pair in shlex.split(rest):
                key, _, value = pair.partition("=")
                env[key] = value
        else:
            key, _, value = rest.partition(" ")
            env[key] = value.strip().strip("\"'")
    return env


INLINE_DOCKERFILE = """\
FROM python:3.12-slim
ARG BASE=python
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 \\
    HOST=127.0.0.1 PORT=9000 \\
    # a comment inside the instruction
    OPENAI_API_KEY="sk-not-a-real-key" \\
    CHASSIS_MODEL_URL=http://127.0.0.1:8090/v1
ENV LEGACY_TOKEN abc
USER workload
"""


def test_dockerfile_env_reads_continuation_lines() -> None:
    """Exit criterion: no workload container holds a key. The Dockerfile check sees every
    variable of a multi-line `ENV`, so a key on a continuation line is caught.
    """
    env = _dockerfile_env(INLINE_DOCKERFILE)
    assert env == {
        "BASE": "python",
        "PATH": "/app/.venv/bin:$PATH",
        "PYTHONUNBUFFERED": "1",
        "HOST": "127.0.0.1",
        "PORT": "9000",
        "OPENAI_API_KEY": "sk-not-a-real-key",
        "CHASSIS_MODEL_URL": "http://127.0.0.1:8090/v1",
        "LEGACY_TOKEN": "abc",
    }
    assert [k for k in env if KEY_NAME.search(k)] == ["OPENAI_API_KEY", "LEGACY_TOKEN"]


@pytest.mark.parametrize(
    ("name", "caught"),
    [
        ("OPENAI_API_KEY", True),
        ("hf_token", True),
        ("CLIENT_SECRET", True),
        ("DB_PASSWORD", True),
        ("MasterKey", True),
        ("CHASSIS_MODEL_URL", False),
        ("CHASSIS_TOOL_URL", False),
        ("HOST", False),
        ("PORT", False),
        ("PYDANTIC_AI_NO_BANNER", False),
    ],
)
def test_key_name(name: str, caught: bool) -> None:
    """Exit criterion: no workload container holds a key. The name rule is case-insensitive
    `KEY|TOKEN|SECRET|PASSWORD` and leaves the proxy URLs and the bind alone.
    """
    assert bool(KEY_NAME.search(name)) is caught


def test_no_workload_image_bakes_in_a_key() -> None:
    """Exit criterion: no workload container holds a key. Each workload's model client points at
    the chassis model proxy. Every `packages/workloads/*/Dockerfile` sets no key `ENV` or `ARG`,
    sets `HOST`, `PORT`, and `CHASSIS_MODEL_URL` (continuation lines included), binds loopback,
    and points `CHASSIS_MODEL_URL` at `127.0.0.1`.
    """
    dockerfiles = sorted(WORKLOADS.glob("*/Dockerfile"))
    assert dockerfiles, "no workload Dockerfile"
    for dockerfile in dockerfiles:
        env = _dockerfile_env(dockerfile.read_text())
        where = str(dockerfile.relative_to(ROOT))
        assert [k for k in env if KEY_NAME.search(k)] == [], f"{where} bakes in a key"
        assert {"HOST", "PORT", "CHASSIS_MODEL_URL"} <= set(env), f"{where}: {sorted(env)}"
        assert env["HOST"] == "127.0.0.1", f"{where}: HOST={env['HOST']}"
        assert env["PORT"] == "9000", f"{where}: PORT={env['PORT']}"
        _assert_points_at_the_proxy(where, env)


_REMOTE_TOKEN_READ = re.compile(r"""["']CHASSIS_API_TOKEN\b|\bTOKEN_VAR\b|\.CHASSIS_API_TOKEN\b""")
_TOKEN_VAR_DEF = re.compile(r"""^TOKEN_VAR\s*=\s*(?P<value>.+)$""", re.MULTILINE)


def _sources(folder: Path) -> list[Path]:
    return [
        p
        for p in sorted(folder.rglob("*"))
        if p.suffix in (".py", ".ts", ".js", ".mjs")
        and "node_modules" not in p.parts
        and "tests" not in p.parts
    ]


@pytest.mark.parametrize(
    "workload",
    sorted(
        p.name
        for p in WORKLOADS.iterdir()
        if (p / "package.json").exists() or (p / "pyproject.toml").exists()
    ),
)
def test_no_workload_reads_a_key(workload: str) -> None:
    """Exit criterion: no workload container holds a key. No workload's source reads a key-like
    variable (`KEY`, `TOKEN`, `SECRET`, `PASSWORD`, any case) from its environment.

    One name is allowed since PoC-5: `CHASSIS_API_TOKEN`, the remote lane's own per-remote
    credential (ADR-005). It opens only this remote's chassis proxies; it is not a provider key
    or an internal credential. It is unset in the `sidecar` lane.
    """
    offenders = [
        f"{p.relative_to(ROOT)}: {read}"
        for p in _sources(WORKLOADS / workload)
        for read in _key_reads(p.read_text())
        if not _REMOTE_TOKEN_READ.search(read)
    ]
    assert offenders == []
    # `TOKEN_VAR` may only ever name the remote token.
    for p in _sources(WORKLOADS / workload):
        for m in _TOKEN_VAR_DEF.finditer(p.read_text()):
            assert m.group("value").strip() == '"CHASSIS_API_TOKEN"', f"{p.relative_to(ROOT)}"


@pytest.mark.parametrize("workload", ["echo-python", "echo-pydanticai", "echo-langgraph"])
def test_python_workload_model_client_reads_the_proxy_url(workload: str) -> None:
    """Exit criterion: each workload's model client points at the chassis model proxy. The
    workload takes its model base URL from `CHASSIS_MODEL_URL`, and its default is `127.0.0.1`.
    """
    text = "\n".join(p.read_text() for p in _sources(WORKLOADS / workload / "src"))
    assert "CHASSIS_MODEL_URL" in text, f"{workload} does not read CHASSIS_MODEL_URL"
    defaults = re.findall(r"""["'](https?://[^"']+/v1)["']""", text)
    assert defaults and {urlparse(u).hostname for u in defaults} == {"127.0.0.1"}, defaults


@pytest.mark.parametrize("engine", engine_params())
async def test_python_engine_sends_no_key_and_calls_only_the_proxy(
    engine: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit criterion: no workload container holds a key. Each workload's model client points at
    the chassis model proxy. With fake provider keys planted in the environment, the engine's
    outbound calls go only to `CHASSIS_MODEL_URL` on `127.0.0.1` and carry none of them.
    """
    for var in ("OPENAI_API_KEY", "LITELLM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(var, PLANTED)
    app = chassis_app(engine)
    outbound = route_outbound(monkeypatch, app)
    async with asyncio.timeout(TIMEOUT_S), running(app):
        events = await run_handle(engine, SIMPLIFY)
    assert events[-1]["type"] == "end", events[-1]
    assert outbound.calls, "the engine made no outbound call"
    proxy = urlparse(MODEL_URL)
    assert outbound.to(f"{proxy.path}/chat/completions"), "no model call reached the proxy"
    for call in outbound.calls:
        assert call.url.host == proxy.hostname == "127.0.0.1", str(call.url)
        assert PLANTED not in str(list(call.headers.items())), f"{call.path} sent a planted key"
        assert PLANTED.encode() not in call.body
