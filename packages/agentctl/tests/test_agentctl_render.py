"""The rendered agent: the right files per framework, Python that parses, YAML and TOML that load,
no chassis dependency, and example business logic equal to the echo workload it came from.
"""

from __future__ import annotations

import ast
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml
from agentctl import rules
from agentctl.scaffold import TEMPLATE, Answers, render

ROOT = Path(__file__).resolve().parents[3]
LEFTOVER = re.compile(r"(?<!\$)\{\{-?\s*[A-Za-z_]|\{%")
COMBOS = [
    (framework, connector)
    for framework in rules.FRAMEWORKS
    for connector in rules.CONNECTORS
    if rules.trust_of(framework) == "trusted" or connector == "remote"
]
CHART_TEMPLATES = ("configmap", "deployment", "hpa", "networkpolicy", "service", "serviceaccount")
COMMON = {
    ".copier-answers.yml",
    ".editorconfig",
    ".gitignore",
    "CLAUDE.md",
    "Dockerfile",
    "Makefile",
    "README.md",
    "config/agent.yaml",
    "config/fake.yaml",
    "pyproject.toml",
    "tests/conftest.py",
    "tests/test_contract.py",
    "tests/test_handle.py",
    "tests/suites/test_eval.py",
    "tests/suites/test_hostile.py",
    "tests/suites/test_load_smoke.py",
    "tests/suites/test_redteam.py",
    "evals/cases.yaml",
    # Compose profiles with a debugger per container, and the template's own CI.
    ".github/workflows/ci.yml",
    ".gitlab-ci.yml",
    ".vscode/launch.json",
    "deploy/compose/README.md",
    "deploy/compose/debug/debugpy.Dockerfile",
    "deploy/compose/debug/requirements.txt",
    "deploy/compose/debug/sitecustomize.py",
    "deploy/compose/docker-compose.yaml",
    # The chart on the platform's library chart agent-lib (025 H-10), in both lanes.
    "deploy/helm/Chart.yaml",
    "deploy/helm/values.yaml",
    *(f"deploy/helm/templates/{name}.tpl" for name in CHART_TEMPLATES),
    # The business-logic guide and the decisions it rests on.
    "docs/decisions.md",
    "docs/how-to-add-business-logic.md",
}
SRC_FILES = {
    "python": {"handle.py", "tools.py"},
    "pydanticai": {"handle.py", "mapping.py"},
    "langgraph": {"handle.py", "mapping.py", "telemetry.py", "tools.py"},
    "langchain": {"handle.py", "mapping.py", "telemetry.py", "tools.py"},
    "openai-agents": {"handle.py", "mapping.py", "telemetry.py", "tools.py"},
    "smolagents": {"agent.py", "handle.py", "mapping.py", "tools.py"},
    "claude-agent": {"handle.py", "mapping.py"},
}
ECHO = {"langchain": "echo-langgraph"}
"""A variant with no echo workload of its own, and the workload its shared files come from."""
NO_ECHO = {"langchain": {"handle.py"}}
"""Files with no echo copy: `create_agent`'s handle (ADR-006 decision 1)."""
SDK = {
    "python": "mcp==",
    "pydanticai": "pydantic-ai-slim[openai,mcp]==",
    "langgraph": "langgraph==",
    "langchain": "langchain==",
    "openai-agents": "openai-agents==",
    "smolagents": "smolagents[mcp]==",
    "claude-agent": "claude-agent-sdk==",
}


def _render(tmp_path: Path, framework: str, connector: str, name: str = "my-agent") -> Path:
    answers = Answers(
        agent_name=name,
        kind="transformer",
        framework=framework,
        connector=connector,
        platform_path=ROOT,
    )
    return render(answers, tmp_path)


def _sidecar_only(path: str) -> bool:
    return path.startswith(("deploy/compose/", ".vscode/"))


def _files(path: Path) -> set[str]:
    return {str(p.relative_to(path)) for p in path.rglob("*") if p.is_file()}


@pytest.mark.parametrize(("framework", "connector"), COMBOS)
def test_the_tree_holds_the_framework_files_and_nothing_else(
    tmp_path: Path, framework: str, connector: str
) -> None:
    agent = _render(tmp_path, framework, connector)
    src = {f"src/my_agent/{name}" for name in {*SRC_FILES[framework], "__init__.py", "py.typed"}}
    common = COMMON if connector == "sidecar" else {f for f in COMMON if not _sidecar_only(f)}
    if connector == "remote":
        common = common | {"tests/test_remote_lane.py"}
    if framework != "claude-agent":
        common = common | {"tests/test_stateless.py"}  # 057 H-19, in process
    assert _files(agent) == common | src


@pytest.mark.parametrize(("framework", "connector"), COMBOS)
def test_every_rendered_file_is_final(tmp_path: Path, framework: str, connector: str) -> None:
    agent = _render(tmp_path, framework, connector)
    for path in agent.rglob("*"):
        if path.is_file():
            text = path.read_text()
            # A Jinja leftover is `{{ name` or `{%`. Not leftovers: `{{.Field}}` (a Go
            # template, docker inspect) and `${{ github.sha }}` (a GitHub Actions expression).
            # The chart's `templates/*.tpl` are Helm (Go) templates, copied as they are.
            if path.suffix != ".tpl":
                assert not LEFTOVER.search(text), path
            else:
                assert text == f'{{{{ include "agent-lib.{path.stem}" . }}}}\n', path
            assert not path.name.endswith(".jinja"), path
            if path.suffix == ".py":
                ast.parse(text, filename=str(path))
            if path.suffix in (".yaml", ".yml"):
                assert isinstance(yaml.safe_load(text), dict), path


@pytest.mark.parametrize(("framework", "connector"), COMBOS)
def test_pyproject_names_the_kit_and_never_the_chassis(
    tmp_path: Path, framework: str, connector: str
) -> None:
    agent = _render(tmp_path, framework, connector)
    project = tomllib.loads((agent / "pyproject.toml").read_text())
    deps = project["project"]["dependencies"]
    dev = project["dependency-groups"]["dev"]
    assert any(d.startswith(SDK[framework]) for d in deps), deps
    assert any(d.startswith("workload-a2a") for d in deps)
    assert "agent-testkit" in dev
    names = [d.split("[")[0].split("=")[0] for d in [*deps, *dev]]
    assert not [n for n in names if n.startswith("chassis")], names
    # Every pin from PyPI is exact; platform packages come from path sources.
    sources = project["tool"]["uv"]["sources"]
    for dep in [*deps, *dev]:
        name = dep.split("[")[0].split("=")[0]
        assert name in sources or "==" in dep, dep
    for name, source in sources.items():
        assert source["path"] == f".platform/packages/{name}", name
    assert project["tool"]["agent-testkit"] == {"handle": "my_agent:handle", "name": "my-agent"}
    assert project["tool"]["uv"]["build-backend"]["module-name"] == "my_agent"


def _imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize(("framework", "connector"), COMBOS)
def test_the_rendered_tree_holds_no_chassis_code(
    tmp_path: Path, framework: str, connector: str
) -> None:
    """059 H-15 box 5: no chassis package in the tree and no Python file imports one (ADR-001)."""
    agent = _render(tmp_path, framework, connector)
    assert not [p for p in agent.rglob("chassis*") if p.is_dir()]
    for path in agent.rglob("*.py"):
        found = _imports(ast.parse(path.read_text(), filename=str(path)))
        assert not {n for n in found if n.startswith("chassis")}, path


@pytest.mark.parametrize(("framework", "connector"), COMBOS)
def test_answers_file_lets_copier_update_later(
    tmp_path: Path, framework: str, connector: str
) -> None:
    agent = _render(tmp_path, framework, connector)
    answers = yaml.safe_load((agent / ".copier-answers.yml").read_text())
    assert answers["_src_path"] == str(TEMPLATE)
    assert {k: answers[k] for k in ("agent_name", "kind", "framework", "connector")} == {
        "agent_name": "my-agent",
        "kind": "transformer",
        "framework": framework,
        "connector": connector,
    }
    assert answers["platform_path"] == str(ROOT)


@pytest.mark.parametrize(("framework", "connector"), COMBOS)
def test_tests_bind_the_kit_contract(tmp_path: Path, framework: str, connector: str) -> None:
    agent = _render(tmp_path, framework, connector)
    conftest = ast.parse((agent / "tests/conftest.py").read_text())
    plugins = [
        node.value
        for node in conftest.body
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "pytest_plugins"
    ]
    assert [ast.literal_eval(p) for p in plugins] == [["agent_testkit.plugin"]]
    contract = (agent / "tests/test_contract.py").read_text()
    assert "from agent_testkit.contract import WorkloadContract" in contract
    assert "class TestWorkloadContract(WorkloadContract):" in contract


NO_INPUT_AS_DATA = {"smolagents"}
"""Engines whose offline output does not carry the fake model's reply (025 H-10)."""


@pytest.mark.parametrize(("framework", "connector"), COMBOS)
def test_contract_turns_off_input_as_data_only_where_the_reply_never_reaches_output(
    tmp_path: Path, framework: str, connector: str
) -> None:
    agent = _render(tmp_path, framework, connector)
    contract = (agent / "tests/test_contract.py").read_text()
    is_off = "checks_input_as_data = False" in contract
    assert is_off == (framework in NO_INPUT_AS_DATA), contract


@pytest.mark.parametrize(("framework", "connector"), COMBOS)
def test_makefile_runs_tests_offline_and_dockerfile_is_hardened(
    tmp_path: Path, framework: str, connector: str
) -> None:
    agent = _render(tmp_path, framework, connector)
    makefile = (agent / "Makefile").read_text()
    for target in ("setup:", "test:", "lint:", "run:"):
        assert f"\n{target}" in makefile, target
    assert "--disable-socket --allow-unix-socket" in makefile
    assert "_API_KEY" in makefile
    dockerfile = (agent / "Dockerfile").read_text()
    assert "python:3.12-slim@sha256:" in dockerfile and "uv:0.11.23@sha256:" in dockerfile
    assert "USER 10002:10002" in dockerfile and "HEALTHCHECK" in dockerfile
    assert "HOST=127.0.0.1" in dockerfile
    assert "--handle my_agent:handle --name my-agent" in dockerfile
    assert "API_KEY" not in dockerfile and "COPY packages/" not in dockerfile


@pytest.mark.parametrize("framework", rules.FRAMEWORKS)
def test_the_example_logic_is_the_echo_workload(tmp_path: Path, framework: str) -> None:
    """Drift check: rendered as `echo-<framework>`, each source file equals the echo workload's
    (ADR-002's copy rule). On a failure, copy the echo file into `_frameworks/<framework>/` with
    the module name replaced by `{{ package_name }}`. `langchain` has no workload of its own: its
    shared files are echo-langgraph's, and its `handle.py` is checked by its rendered unit tests."""
    name = ECHO.get(framework, f"echo-{framework}")
    connector = "remote" if framework in rules.UNTRUSTED else "sidecar"
    agent = _render(tmp_path, framework, connector, name=name)
    module = name.replace("-", "_")
    echo = ROOT / "packages/workloads" / name / "src" / module
    for file in sorted(SRC_FILES[framework] - NO_ECHO.get(framework, set())):
        rendered = (agent / "src" / module / file).read_text()
        assert rendered == (echo / file).read_text(), f"{file} drifted from {echo / file}"
    partials = {
        p.name.removesuffix(".jinja") for p in (TEMPLATE / "_frameworks" / framework).iterdir()
    }
    assert partials == SRC_FILES[framework]


CI_TARGETS = ("lint", "test", "load-smoke", "hostile", "redteam", "eval", "image", "sign")
"""What the template's CI calls (`.github/workflows/ci.yml`, `.gitlab-ci.yml`)."""


def test_every_target_the_ci_calls_is_in_the_makefile(tmp_path: Path) -> None:
    agent = _render(tmp_path, "pydanticai", "sidecar")
    ci = (agent / ".github/workflows/ci.yml").read_text() + (agent / ".gitlab-ci.yml").read_text()
    for target in CI_TARGETS:
        assert re.search(rf"make \"?{target}\b|- {target}\b", ci), target
        done = subprocess.run(
            ["make", "-n", target, "IMAGE_REF=example@sha256:0"],
            cwd=agent,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, (target, done.stderr)


def test_the_langchain_variant_is_create_agent_on_the_langgraph_pieces(tmp_path: Path) -> None:
    """ADR-006 decision 1: a `create_agent` variant next to the `StateGraph` one. It builds the
    graph with `create_agent` and shares the LangGraph variant's mapping, tools, and tracing."""
    chain = _render(tmp_path / "chain", "langchain", "sidecar") / "src/my_agent"
    graph = _render(tmp_path / "graph", "langgraph", "sidecar") / "src/my_agent"

    handle = (chain / "handle.py").read_text()
    assert "create_agent(model, tools, system_prompt=SYSTEM_PROMPT)" in handle
    assert "StateGraph(" not in handle
    for shared in ("mapping.py", "tools.py", "telemetry.py"):
        assert (chain / shared).read_text() == (graph / shared).read_text(), shared


def test_the_langchain_pin_is_agentctls_dev_pin(tmp_path: Path) -> None:
    """The template's `langchain==` pin and agentctl's dev pin move together: the rendered agent's
    tests run on the monorepo venv, which has the dev pin."""
    agent = _render(tmp_path, "langchain", "sidecar")
    rendered = re.findall(r'"langchain==([^"]+)"', (agent / "pyproject.toml").read_text())
    agentctl = (ROOT / "packages/agentctl/pyproject.toml").read_text()
    dev = re.findall(r'"langchain==([^"]+)"', agentctl)
    assert rendered == dev and len(dev) == 1


def _model_routes(model: dict[str, object]) -> set[str]:
    """Every route a `spec.model` block names: route, fallback_route, judge_route, routes."""
    named = {model.get(key) for key in ("route", "fallback_route", "judge_route")}
    routes = model.get("routes") or {}
    assert isinstance(routes, dict)
    return {str(r) for r in (named | set(routes.values())) if r}


@pytest.mark.parametrize(
    ("framework", "connector"), [("langgraph", "sidecar"), ("smolagents", "remote")]
)
def test_the_generated_routes_are_the_ones_agentctl_checks(
    tmp_path: Path, framework: str, connector: str
) -> None:
    """059 H-15 box 7: `check_routes` runs on `rules.GENERATED_ROUTES` before rendering, so they
    must be every route the keyed configs name. The `fake` profile calls no key: it is left out."""
    agent = _render(tmp_path, framework, connector)
    config = yaml.safe_load((agent / "config/agent.yaml").read_text())
    chart = yaml.safe_load((agent / "deploy/helm/values.yaml").read_text())
    assert config["spec"]["adapters"]["model"] == "litellm"
    assert chart["chassis"]["config"]["spec"]["adapters"]["model"] == "litellm"
    named = _model_routes(config["spec"]["model"])
    named |= _model_routes(chart["chassis"]["config"]["spec"]["model"])
    assert named == set(rules.GENERATED_ROUTES)
