"""LangSmith is one more Collector exporter, behind a switch (129 CH-10, ADR-007 item 10).

The default PoC-7 stack (`deploy/compose/poc07`) runs without LangSmith and needs no LangSmith
variable. `stack.sh <verb> --langsmith` adds two files: a Compose override that hands the
Collector a second `--config` and the LangSmith variables, and an overlay Collector config with
the `otlphttp/langsmith` exporter. The Collector merges its configs: maps merge, a list is
replaced. So the overlay restates the whole traces exporter list, and it adds no processor, so
redaction still runs before every exporter. `stack.sh` sends the trace key only to an endpoint on
its allow-list, and refuses any other before a docker call.

All checks are static, except `stack.sh` runs with a stub `docker`, and `docker compose config`
renders. That command parses and
interpolates only: it needs no daemon, no network, and no key. It skips when Docker is absent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "deploy/compose/poc07"
COLLECTOR = STACK / "otel-collector.yaml"
COLLECTOR_OVERLAY = STACK / "otel-collector.langsmith.yaml"
COMPOSE = STACK / "docker-compose.yaml"
COMPOSE_OVERLAY = STACK / "docker-compose.langsmith.yaml"
STACK_SH = STACK / "stack.sh"

KEY_VAR = "LANGSMITH_API_KEY"  # pragma: allowlist secret
EXPORTER = "otlphttp/langsmith"
BASE_EXPORTERS = ["otlphttp/langfuse", "otlp/jaeger"]
DUMMY_KEY = "test-only-not-a-key"  # pragma: allowlist secret
_VAR = re.compile(r"\$\{([^}]*)\}")


def _yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), path
    return data


def _merge(base: Any, overlay: Any) -> Any:
    """The Collector's default config merge: maps merge key by key; anything else, lists
    included, is replaced by the overlay's value."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        return {k: _merge(base[k], overlay[k]) if k in base else overlay[k] for k in overlay} | {
            k: v for k, v in base.items() if k not in overlay
        }
    return overlay


def _switched() -> dict[str, Any]:
    return dict(_merge(_yaml(COLLECTOR), _yaml(COLLECTOR_OVERLAY)))


def test_default_collector_config_has_no_langsmith_exporter_or_variable() -> None:
    config = _yaml(COLLECTOR)

    assert EXPORTER not in config["exporters"]
    assert config["service"]["pipelines"]["traces"]["exporters"] == BASE_EXPORTERS
    assert "langsmith" not in COLLECTOR.read_text().lower()


def test_default_compose_file_names_no_langsmith_variable() -> None:
    assert "langsmith" not in COMPOSE.read_text().lower()


def test_switch_sends_traces_to_langfuse_jaeger_and_langsmith() -> None:
    base = _yaml(COLLECTOR)
    switched = _switched()

    assert switched["service"]["pipelines"]["traces"]["exporters"] == [*BASE_EXPORTERS, EXPORTER]
    for name in BASE_EXPORTERS:
        assert switched["exporters"][name] == base["exporters"][name]
    assert switched["service"]["pipelines"]["logs"] == base["service"]["pipelines"]["logs"]


def test_switch_keeps_redaction_before_every_exporter() -> None:
    overlay = _yaml(COLLECTOR_OVERLAY)

    assert "processors" not in overlay
    assert set(overlay["service"]["pipelines"]) == {"traces"}
    assert set(overlay["service"]["pipelines"]["traces"]) == {"exporters"}
    traces = _switched()["service"]["pipelines"]["traces"]
    assert traces["processors"] == _yaml(COLLECTOR)["service"]["pipelines"]["traces"]["processors"]
    assert "transform/redact_spans" in traces["processors"]


def test_langsmith_exporter_reads_its_key_from_the_environment_only() -> None:
    exporter = _yaml(COLLECTOR_OVERLAY)["exporters"][EXPORTER]

    assert exporter["headers"]["x-api-key"] == f"${{env:{KEY_VAR}}}"
    assert exporter["headers"]["Langsmith-Project"] == "${env:LANGSMITH_PROJECT}"
    assert exporter["endpoint"] == "${env:LANGSMITH_OTLP_ENDPOINT}"


@pytest.mark.parametrize("path", [COLLECTOR_OVERLAY, COMPOSE_OVERLAY], ids=lambda p: p.name)
def test_switch_files_hold_no_key_literal(path: Path) -> None:
    text = path.read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))

    assert not re.search(r"lsv2_|ls__|(pk|sk)-lf-|Bearer |Basic ", code)
    for line in code.splitlines():
        if "x-api-key" in line.lower() or KEY_VAR in line:
            assert _VAR.search(line), line


def test_compose_override_gives_the_key_to_the_collector_only() -> None:
    services = _yaml(COMPOSE_OVERLAY)["services"]

    assert set(services) == {"otel-collector"}
    collector = services["otel-collector"]
    env = collector["environment"]
    assert env[KEY_VAR] == f"${{{KEY_VAR}:?}}"
    assert all(re.fullmatch(r"[A-Z0-9_]+:\?", v) for v in _VAR.findall(COMPOSE_OVERLAY.read_text()))
    assert collector["command"] == [
        "--config=/etc/otelcol/config.yaml",
        "--config=/etc/otelcol/langsmith.yaml",
    ]
    assert "./otel-collector.langsmith.yaml:/etc/otelcol/langsmith.yaml:ro" in collector["volumes"]
    # No image, port, or capability change: the base service keeps its pin and hardening.
    assert not {"image", "ports", "cap_drop", "cap_add", "privileged"} & set(collector)


def test_stack_sh_adds_the_switch_files_only_with_the_flag() -> None:
    script = STACK_SH.read_text()
    default = re.search(r"^compose=\((.*)\)$", script, re.MULTILINE)

    assert default and "langsmith" not in default.group(1)
    assert re.search(
        r"--langsmith\)\s*\n(.*\n){0,6}?"
        r"\s*compose\+=\(-f \"\$here/docker-compose\.langsmith\.yaml\"\)",
        script,
    )
    # The flag path fails closed without the key, and never prints it.
    assert f"${{{KEY_VAR}:?" in script
    assert not re.search(rf"echo[^\n]*\$\{{?{KEY_VAR}", script)


@pytest.mark.parametrize(
    "key",
    [
        "lsv2_pt_0123456789abcdef_0123456789",  # pragma: allowlist secret
        "ls__0123456789abcdef0123456789abcdef",  # pragma: allowlist secret
    ],
    ids=["lsv2", "ls__"],
)
def test_stack_sh_logs_mask_the_langsmith_key(key: str) -> None:
    script = STACK_SH.read_text()
    redact = script.partition("redact() {")[2].partition("\n}\n")[0]
    shapes = re.search(r'shapes = re\.compile\(r"(.*)"\)', redact)

    assert f'os.environ.get("{KEY_VAR}")' in redact
    assert shapes
    assert re.sub(shapes.group(1), "***", f"exporter header x-api-key={key} sent") == (
        "exporter header x-api-key=*** sent"
    )


ALLOWED_ENDPOINTS = [
    "https://api.smith.langchain.com/otel",
    "https://eu.api.smith.langchain.com/otel",
    "http://fake-langsmith:8000/otel",
]


def _run_stack_sh(tmp_path: Path, verb: str, endpoint: str) -> tuple[int, str, bool]:
    """`stack.sh <verb> --langsmith` with a stub `docker` first on PATH. Returns the exit code,
    stderr, and whether the stub was called."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    called = tmp_path / "docker-called"
    stub = stub_dir / "docker"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{called}"\n')
    stub.chmod(0o755)
    env = {
        "PATH": f"{stub_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        KEY_VAR: DUMMY_KEY,
        "LANGSMITH_OTLP_ENDPOINT": endpoint,
    }
    result = subprocess.run(
        ["bash", str(STACK_SH), verb, "--langsmith"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=60,
    )
    return result.returncode, result.stderr, called.exists()


def test_stack_sh_refuses_an_endpoint_off_the_allow_list_before_any_docker_call(
    tmp_path: Path,
) -> None:
    code, stderr, docker_called = _run_stack_sh(tmp_path, "up", "http://evil/otel")

    assert code != 0
    assert not docker_called
    assert "LANGSMITH_OTLP_ENDPOINT" in stderr
    assert "evil" not in stderr


@pytest.mark.parametrize("endpoint", ALLOWED_ENDPOINTS)
def test_stack_sh_accepts_an_endpoint_on_the_allow_list(tmp_path: Path, endpoint: str) -> None:
    # `env` only prints the env file's path: no docker call, no file written.
    code, stderr, docker_called = _run_stack_sh(tmp_path, "env", endpoint)

    assert code == 0, stderr
    assert not docker_called


def _compose_config(
    env_file: Path, *overlays: Path, key: str | None
) -> subprocess.CompletedProcess[str]:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker is not installed")
    # The probe runs with the same env as the real call, so a plugin found only under the real
    # HOME (~/.docker/cli-plugins) makes the test skip, not fail.
    env = {"PATH": str(Path(docker).parent), "HOME": str(env_file.parent)}
    probe = subprocess.run(
        [docker, "compose", "version"], capture_output=True, check=False, env=env, timeout=60
    )
    if probe.returncode:
        pytest.skip("docker compose is not available")
    files = [arg for path in (COMPOSE, *overlays) for arg in ("-f", str(path))]
    if key is not None:
        env[KEY_VAR] = key
    # `config` parses and interpolates only; it needs no daemon and no network.
    return subprocess.run(
        [
            docker,
            "compose",
            "-p",
            "poc07",
            "--env-file",
            str(env_file),
            *files,
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=60,
    )


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    """Stand-in values for every variable the default stack reads, and no LangSmith one."""
    names = sorted({v.removesuffix(":?") for v in _VAR.findall(COMPOSE.read_text())})
    path = tmp_path / ".env.poc07"
    path.write_text("".join(f"{name}=stand-in\n" for name in names))
    return path


def test_docker_compose_renders_the_default_stack_with_no_langsmith_key(env_file: Path) -> None:
    result = _compose_config(env_file, key=None)

    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    assert "langsmith" not in json.dumps(services).lower()


def test_docker_compose_refuses_the_switch_without_the_key(env_file: Path) -> None:
    # Set the overlay's other variables, so the key is the only one missing. Compose names
    # only the first missing variable, and which one comes first differs between versions.
    env_file.write_text(
        env_file.read_text() + "LANGSMITH_PROJECT=poc07\nLANGSMITH_OTLP_ENDPOINT=http://x/otel\n"
    )

    result = _compose_config(env_file, COMPOSE_OVERLAY, key=None)

    assert result.returncode != 0
    assert KEY_VAR in result.stderr


def test_docker_compose_renders_the_switch_with_the_key_in_the_collector_only(
    env_file: Path,
) -> None:
    extra = (
        env_file.read_text() + "LANGSMITH_PROJECT=poc07\nLANGSMITH_OTLP_ENDPOINT=http://x/otel\n"
    )
    env_file.write_text(extra)

    result = _compose_config(env_file, COMPOSE_OVERLAY, key=DUMMY_KEY)

    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    holders = {n for n, s in services.items() if KEY_VAR in (s.get("environment") or {})}
    assert holders == {"otel-collector"}
    collector = services["otel-collector"]
    assert collector["command"] == [
        "--config=/etc/otelcol/config.yaml",
        "--config=/etc/otelcol/langsmith.yaml",
    ]
    assert {v["target"] for v in collector["volumes"]} == {
        "/etc/otelcol/config.yaml",
        "/etc/otelcol/langsmith.yaml",
    }
    assert collector["cap_drop"] == ["ALL"]
