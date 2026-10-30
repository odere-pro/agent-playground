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
    # The business-logic guide and the decisions it rests on.
    "docs/decisions.md",
    "docs/how-to-add-business-logic.md",
}
SRC_FILES = {
    "python": {"handle.py", "tools.py"},
    "pydanticai": {"handle.py", "mapping.py"},
    "langgraph": {"handle.py", "mapping.py", "telemetry.py", "tools.py"},
    "openai-agents": {"handle.py", "mapping.py", "telemetry.py", "tools.py"},
    "smolagents": {"agent.py", "handle.py", "mapping.py", "tools.py"},
    "claude-agent": {"handle.py", "mapping.py"},
}
SDK = {
    "python": "mcp==",
    "pydanticai": "pydantic-ai-slim[openai,mcp]==",
    "langgraph": "langgraph==",
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
    return path.startswith(("deploy/", ".vscode/"))


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
    assert _files(agent) == common | src


@pytest.mark.parametrize(("framework", "connector"), COMBOS)
def test_every_rendered_file_is_final(tmp_path: Path, framework: str, connector: str) -> None:
    agent = _render(tmp_path, framework, connector)
    for path in agent.rglob("*"):
        if path.is_file():
            text = path.read_text()
            # A Jinja leftover is `{{ name` or `{%`. Not leftovers: `{{.Field}}` (a Go
            # template, docker inspect) and `${{ github.sha }}` (a GitHub Actions expression).
            assert not LEFTOVER.search(text), path
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
    the module name replaced by `{{ package_name }}`."""
    name = f"echo-{framework}"
    connector = "remote" if framework in rules.UNTRUSTED else "sidecar"
    agent = _render(tmp_path, framework, connector, name=name)
    module = name.replace("-", "_")
    echo = ROOT / "packages/workloads" / name / "src" / module
    for file in sorted(SRC_FILES[framework]):
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
