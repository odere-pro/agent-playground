"""Offline checks of the PoC-8 LiteLLM drill: `deploy/compose/poc08-litellm`, its workflow
`.github/workflows/poc08-litellm.yml`, and the gate on `test_poc08_stack_litellm.py`.

- Images: every third-party image is pinned by digest, the LiteLLM one is PoC-5's, and the only
  exceptions are `PENDING` (exact version tags, until the first CI run prints their digests).
- LiteLLM's config names the `presidio` guardrail under the name the chassis calls, every
  guardrail is on for every call, and the `otel` callback exports to the Collector.
- Keys: no key literal in any file; the master key reaches LiteLLM only, the virtual key the
  chassis only, both from git-ignored files that `stack.sh` makes.
- The workflow is read-only, pinned by SHA, uses no secret, and uploads its artifacts.
- The drill is collected only with `POC08_LITELLM=1`, and then every test in it is `network`.

Parses files and runs `pytest --collect-only`; no Docker, no socket.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from chassis.adapters.litellm.guardrails import StageNames
from chassis.server import ChassisConfig
from chassis_contracts.redteam import load_cases

ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "deploy/compose/poc08-litellm"
COMPOSE = STACK / "docker-compose.yaml"
WORKFLOW = ROOT / ".github/workflows/poc08-litellm.yml"
DRILL = "pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py"
POC05_LITELLM = ROOT / "deploy/kind/poc05/platform/litellm.yaml"
DIGEST = re.compile(r"^[^\s@]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")
EXACT_TAG = re.compile(r":v?\d+\.\d+\.\d+$")
FULL_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
KEY_SHAPE = re.compile(r"sk-[A-Za-z0-9_-]{8,}|Bearer [A-Za-z0-9._-]{8,}")
PENDING = {
    "docker.io/jaegertracing/jaeger:2.21.0",
    "docker.io/otel/opentelemetry-collector-contrib:0.163.2",
    "mcr.microsoft.com/presidio-analyzer:2.2.358",
    "mcr.microsoft.com/presidio-anonymizer:2.2.358",
}
"""Images on an exact version tag until `stack.sh digests` in the first CI run gives their
digests. Pin each in docker-compose.yaml, then remove it here."""
OURS = "agent-platform/"


def _code(path: Path) -> str:
    """The file without its comment lines: the prose may name what the code must not do."""
    return "\n".join(x for x in path.read_text().splitlines() if not x.lstrip().startswith("#"))


def _yaml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    return data


def _services() -> dict[str, dict[str, Any]]:
    services: dict[str, dict[str, Any]] = _yaml(COMPOSE)["services"]
    return services


def _env(service: str) -> dict[str, str]:
    return {k: str(v) for k, v in (_services()[service].get("environment") or {}).items()}


# --- images ---


def test_every_third_party_image_is_pinned_by_digest_or_pending() -> None:
    images = {s["image"] for s in _services().values()}
    third_party = {i for i in images if not i.startswith(OURS)}
    unpinned = {i for i in third_party if not DIGEST.match(i)}
    assert unpinned == PENDING, "pin by digest, or list it in PENDING with an exact tag"
    assert all(EXACT_TAG.search(i) for i in PENDING), "a pending image names an exact version"
    header = COMPOSE.read_text().split("\nname:", 1)[0]
    for image in PENDING:
        assert image.removeprefix("docker.io/") in header, f"the header lists {image}"


def test_our_images_are_built_here() -> None:
    for name, service in _services().items():
        if service["image"].startswith(OURS):
            assert "build" in service, f"{name} is built from this repo, never pulled"


def test_dockerfile_bases_are_pinned_by_digest() -> None:
    text = (STACK / "guardrail-server.Dockerfile").read_text()
    refs = re.findall(r"^ARG PYTHON_IMAGE=(\S+)", text, re.M)
    refs += [r for r in re.findall(r"^COPY --from=(\S+)", text, re.M) if r != "build"]
    assert len(refs) == 2
    assert all(DIGEST.match(r) for r in refs), refs
    assert '--classifier", "pattern"' in text, "the stand-in, never Prompt Guard 2 here"


def test_litellm_is_the_poc05_image() -> None:
    poc05 = re.search(r"image: (ghcr\.io/berriai/litellm\S+)", POC05_LITELLM.read_text())
    assert poc05 is not None
    assert _services()["litellm"]["image"] == poc05.group(1)


# --- LiteLLM config: the presidio guardrail and the OTel callback ---


def _litellm_config() -> dict[str, Any]:
    return _yaml(STACK / "litellm.yaml")


def _guardrails() -> dict[str, dict[str, Any]]:
    return {g["guardrail_name"]: g["litellm_params"] for g in _litellm_config()["guardrails"]}


def test_pii_mask_is_litellms_presidio_hook_on_every_call() -> None:
    pii = _guardrails()["pii-mask"]
    assert pii["guardrail"] == "presidio"
    assert pii["mode"] == "pre_call"
    assert pii["default_on"] is True
    for side in ("analyzer", "anonymizer"):
        var = f"PRESIDIO_{side.upper()}_API_BASE"
        assert pii[f"presidio_{side}_api_base"] == f"os.environ/{var}"
        assert _env("litellm")[var] == f"http://presidio-{side}:3000"


def test_every_name_the_chassis_calls_is_a_guardrail_on_every_call() -> None:
    names = StageNames()
    called = {*names.input, *names.output, *names.tool_output}
    guardrails = _guardrails()
    assert called <= set(guardrails)
    assert all(g["default_on"] is True for g in guardrails.values())
    for name in ("prompt-guard", "secret-guard"):
        assert guardrails[name]["guardrail"] == "generic_guardrail_api"
        assert guardrails[name]["api_base"] == "http://guardrail-server:8095"


def test_litellm_exports_its_spans_to_the_collector() -> None:
    assert "otel" in _litellm_config()["litellm_settings"]["callbacks"]
    env = _env("litellm")
    assert env["OTEL_EXPORTER"] == "otlp_http"
    assert env["OTEL_ENDPOINT"] == "http://otel-collector:4318/v1/traces"
    assert env["OTEL_SERVICE_NAME"] == "litellm"
    collector = _yaml(STACK / "otel-collector.yaml")
    assert collector["service"]["pipelines"]["traces"]["exporters"] == ["otlp/jaeger"]


def test_the_chassis_config_runs_litellm_for_model_and_guardrails_with_otel() -> None:
    config = ChassisConfig.model_validate(_yaml(STACK / "chassis.yaml"))
    adapters = config.spec.adapters
    assert adapters is not None
    assert (adapters.model, adapters.guardrails, adapters.telemetry) == ("litellm",) * 2 + ("otel",)
    assert config.spec.model.route in {m["model_name"] for m in _litellm_config()["model_list"]}
    env = _env("chassis")
    assert env["LITELLM_BASE_URL"] == "http://litellm:4000/v1"
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-collector:4318"


# --- keys ---


def _stack_files() -> list[Path]:
    files = [p for p in STACK.iterdir() if p.is_file() and not p.name.startswith(".env")]
    return [*files, WORKFLOW]


def test_no_key_literal_in_any_file() -> None:
    for path in _stack_files():
        found = KEY_SHAPE.findall(path.read_text())
        assert not found, f"{path.name} holds a key-shaped literal"
    assert _litellm_config()["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"
    # The one literal `api_key` is the fake model server's placeholder; it takes no key.
    assert [m["litellm_params"]["api_key"] for m in _litellm_config()["model_list"]] == ["fake"]


def test_every_compose_secret_comes_from_the_env_file_and_fails_closed() -> None:
    for name, service in _services().items():
        for var, value in (service.get("environment") or {}).items():
            if re.search(r"KEY|PASSWORD|SECRET|TOKEN", var):
                assert re.fullmatch(r"\$\{[A-Z_]+:\?\}", str(value)), f"{name}.{var}"


def test_the_master_key_reaches_litellm_only_and_the_virtual_key_the_chassis_only() -> None:
    services = _services()
    holders = [
        n for n, s in services.items() if "LITELLM_MASTER_KEY" in (s.get("environment") or {})
    ]
    assert holders == ["litellm"]
    with_key_file = [n for n, s in services.items() if s.get("env_file")]
    assert with_key_file == ["chassis"]
    assert services["chassis"]["env_file"] == ["./.run/chassis.env"]
    assert (STACK / ".gitignore").read_text().split() == [".run/"]
    script = _code(STACK / "stack.sh")
    assert 'env_file="$here/.env.poc08-litellm"' in script  # `.env.*` in the root .gitignore
    assert "umask 077" in script and "openssl rand" in script
    assert "-H @-" in script, "the master key goes to curl on stdin, never in argv"
    assert "set -x" not in script


def test_the_virtual_key_may_call_the_model_and_the_guardrail_route_only() -> None:
    """LiteLLM keeps `/guardrails/apply_guardrail` for admins unless the key's `allowed_routes`
    names it (v1.103.0, `route_checks.py`); the chassis's guard stages call it."""
    script = _code(STACK / "stack.sh")
    assert '"allowed_routes":["openai_routes","/guardrails/apply_guardrail"]' in script


def test_the_chassis_reaches_litellm_and_the_collector_only() -> None:
    services = _services()
    assert services["chassis"]["networks"] == ["edge"]
    platform = {"postgres", "fake-model-server", "guardrail-server"}
    platform |= {"presidio-analyzer", "presidio-anonymizer"}
    for name in platform:
        assert services[name]["networks"] == ["platform"], name
    assert _yaml(COMPOSE)["networks"]["platform"]["internal"] is True
    for name, service in services.items():
        for port in service.get("ports") or []:
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port}"


# --- the workflow ---


def _workflow() -> dict[Any, Any]:
    return _yaml(WORKFLOW)


def _steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = _workflow()["jobs"]["poc08-litellm"]["steps"]
    return steps


def test_the_workflow_is_read_only_pinned_and_uses_no_secret() -> None:
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as true
    assert isinstance(triggers, dict)
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in workflow["jobs"]["poc08-litellm"]
    text = _code(WORKFLOW)
    assert "secrets." not in text and "pull_request_target" not in text
    uses = [s["uses"].split()[0] for s in _steps() if "uses" in s]
    assert uses and all(FULL_SHA.match(u) for u in uses), uses


def test_the_workflow_runs_the_drill_uploads_artifacts_and_tears_down() -> None:
    steps = {s.get("name", s.get("uses", "")): s for s in _steps()}
    runs = " ".join(str(s.get("run", "")) for s in _steps())
    for verb in ("up", "wait", "digests", "env"):
        assert f"deploy/compose/poc08-litellm/stack.sh {verb}" in runs
    assert f"POC08_LITELLM=1 uv run pytest -m network -v -s \\\n            {DRILL}" in (
        WORKFLOW.read_text()
    )
    upload = steps["Artifacts"]
    assert upload["if"] == "always()" and "poc08-litellm" in upload["with"]["path"]
    assert steps["Down"]["if"] == "always()"
    assert steps["Logs"]["run"].strip().splitlines()[1].endswith("stack.sh logs")


# --- the fake model's script and the red-team selection ---


def test_the_model_script_checks_raw_values_before_the_mask() -> None:
    spec = importlib.util.spec_from_file_location("poc08_model_script", STACK / "model_script.py")
    assert spec is not None and spec.loader is not None
    model_script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model_script)
    rules = model_script.pii_rules()
    raw = [r["match"] for r in rules if r["reply"] == model_script.RAW_REPLY]
    assert raw == list(model_script.PII_VALUES)
    assert all(v in model_script.PII_TEXT for v in raw)
    masked = next(i for i, r in enumerate(rules) if r["reply"] == model_script.MASKED_REPLY)
    assert masked == len(raw), "every raw rule comes first: the first match wins"
    assert not any(v in model_script.CONTROL_TEXT for v in raw)
    cases = {c.id: c for c in load_cases()}
    for case_id in model_script.REDTEAM_CASES:
        assert cases[case_id].category in {"exfiltration", "secret_read"}
        assert cases[case_id].kind in {"input", "output", "output_stream"}


# --- the drill is collected only with its variable ---


def _collect(env_value: str | None, target: str) -> dict[str, list[str]]:
    env = {k: v for k, v in os.environ.items() if k != "POC08_LITELLM"}
    if env_value is not None:
        env["POC08_LITELLM"] = env_value
    code = (
        "import json, sys, pytest\n"
        "class Seen:\n"
        "    items = {}\n"
        "    def pytest_collection_finish(self, session):\n"
        "        for item in session.items:\n"
        "            self.items[item.nodeid] = sorted({m.name for m in item.iter_markers()})\n"
        "seen = Seen()\n"
        "pytest.main(sys.argv[1:], plugins=[seen])\n"
        "print('@@COLLECTED@@' + json.dumps(seen.items))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, "--collect-only", "-q", "-p", "no:cacheprovider", target],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    [line] = [x for x in done.stdout.splitlines() if x.startswith("@@COLLECTED@@")]
    items: dict[str, list[str]] = json.loads(line.removeprefix("@@COLLECTED@@"))
    return items


@pytest.mark.parametrize("value", [None, "0"])
def test_without_its_variable_the_drill_is_not_collected(value: str | None) -> None:
    # The folder, as `make test` collects it: `collect_ignore` acts on discovery only.
    items = _collect(value, str(Path(DRILL).parent))
    assert any("test_poc08_litellm_static" in node for node in items), "the folder collects"
    assert not [node for node in items if "test_poc08_stack_litellm" in node]


def test_with_its_variable_every_drill_test_is_collected_and_network() -> None:
    items = _collect("1", DRILL)
    assert len(items) == 4, sorted(items)
    assert all("network" in marks for marks in items.values())
