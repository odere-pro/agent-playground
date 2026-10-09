"""Static checks of the PoC-6 Mac command (T-MAC part 1). Offline: no Docker, no key, no llama.cpp.

What they pin: the new Compose files parse and hold no key value; the LiteLLM config routes
`big-default` to the hosted model and `local-small` to the host's llama-server; the script's
`--dry-run` exits 0 here; and neither the script nor `bakeoff` runs an untrusted engine against a
real model.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from bakeoff import cli
from bakeoff.registry import ENGINES
from chassis.server.config import load_config

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "deploy" / "compose"
POC06 = COMPOSE / "poc06"
SCRIPT = ROOT / "scripts" / "poc06_mac.sh"
SCALE_SLM = POC06 / "scale-slm.sh"
DRIVER = POC06 / "load_driver.py"

HOSTED_COMPOSE = POC06 / "litellm-hosted.yaml"
SCALE_OVERLAY = POC06 / "scale-litellm-slm.yaml"
LITELLM_CONFIG = POC06 / "litellm.config.yaml"
SCALE_SLM_CONFIG = POC06 / "chassis-configs" / "scale-slm.yaml"
YAML_FILES = [HOSTED_COMPOSE, SCALE_OVERLAY, LITELLM_CONFIG, SCALE_SLM_CONFIG]

UNTRUSTED = sorted(e.name for e in ENGINES if not e.trusted)
TRUSTED = sorted(e.name for e in ENGINES if e.trusted)
KEY_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)$")
INTERPOLATION = re.compile(r"^\$\{[A-Z0-9_]+(:[-?][^}]*)?\}$")


def load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    return data


def clean_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """PATH, a temp HOME, and `extra`: no key of this session reaches the script."""
    return {"PATH": os.environ["PATH"], "HOME": str(tmp_path), **extra}


def run_script(tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=clean_env(tmp_path, **env),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


# ---- Compose and LiteLLM files ----------------------------------------------------------------


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: p.name)
def test_the_new_yaml_files_parse(path: Path) -> None:
    assert path.is_file()
    assert load(path)


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: p.name)
def test_no_file_names_a_key_value(path: Path) -> None:
    text = path.read_text()
    assert not re.search(r"\bsk-[A-Za-z0-9]", text), "a literal that starts with sk-"
    assert "env_file" not in text
    assert "network_mode: host" not in text
    assert "privileged" not in text
    assert "docker.sock" not in text


def _env_of(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment", {})
    assert isinstance(env, dict)
    return {k: str(v) for k, v in env.items()}


def test_every_secret_variable_is_an_interpolation_or_a_placeholder() -> None:
    for path in (HOSTED_COMPOSE, SCALE_OVERLAY):
        services = load(path)["services"]
        for name, service in services.items():
            for var, value in _env_of(service).items():
                if KEY_NAME.search(var):
                    assert INTERPOLATION.match(value) or value == "unused", (path.name, name, var)


def test_the_hosted_compose_file_is_litellm_alone_and_pinned() -> None:
    compose = load(HOSTED_COMPOSE)
    assert compose["name"] == "poc06mac"
    assert list(compose["services"]) == ["litellm"]
    litellm = compose["services"]["litellm"]
    assert re.search(r"@sha256:[0-9a-f]{64}$", litellm["image"])
    base = load(COMPOSE / "docker-compose.yaml")["services"]["litellm"]["image"]
    assert litellm["image"] == base, "the same pinned image as the PoC-1 stack"
    assert litellm["ports"] == ["127.0.0.1:${POC06_LITELLM_PORT:-14000}:4000"]
    env = _env_of(litellm)
    assert env["LITELLM_MASTER_KEY"].startswith("${POC06_LITELLM_KEY:?")
    assert env["POC06_HOSTED_API_KEY"] == "${POC06_HOSTED_API_KEY:-unused}"
    assert litellm["cap_drop"] == ["ALL"]
    assert load(HOSTED_COMPOSE)["networks"]["default"]["name"] == "poc06mac"


def test_the_litellm_config_routes_big_default_hosted_and_local_small_to_the_host() -> None:
    config = load(LITELLM_CONFIG)
    routes = {m["model_name"]: m["litellm_params"] for m in config["model_list"]}
    assert set(routes) == {"big-default", "local-small"}
    assert routes["big-default"] == {
        "model": "os.environ/POC06_HOSTED_MODEL",
        "api_key": "os.environ/POC06_HOSTED_API_KEY",  # pragma: allowlist secret
    }
    assert routes["local-small"]["api_base"] == "os.environ/POC06_LLAMA_BASE"
    assert routes["local-small"]["api_key"] == "fake"  # pragma: allowlist secret
    assert routes["local-small"]["model"] == "openai/local-small"
    assert config["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"
    assert config["litellm_settings"]["turn_off_message_logging"] is True


@pytest.mark.parametrize("path", [HOSTED_COMPOSE, SCALE_OVERLAY], ids=lambda p: p.name)
def test_litellm_reaches_the_llama_server_through_host_docker_internal(path: Path) -> None:
    litellm = load(path)["services"]["litellm"]
    env = _env_of(litellm)
    assert env["POC06_LLAMA_BASE"].startswith("http://host.docker.internal:")
    assert env["POC06_LLAMA_BASE"].endswith("/v1")
    mounts = "\n".join(litellm["volumes"])
    assert "litellm.config.yaml:/etc/litellm/config.yaml:ro" in mounts


def test_the_provider_key_reaches_only_the_litellm_service() -> None:
    hosted = load(HOSTED_COMPOSE)["services"]
    assert [n for n, s in hosted.items() if "POC06_HOSTED_API_KEY" in _env_of(s)] == ["litellm"]
    overlay = load(SCALE_OVERLAY)
    for name, service in overlay["services"].items():
        env = _env_of(service)
        if name == "litellm":
            placeholder = env["POC06_HOSTED_API_KEY"]  # pragma: allowlist secret
            assert placeholder == "unused", "the scale run has no provider key"
        else:
            assert not [v for v in env if "HOSTED" in v], name
    chassis = overlay["x-slm-chassis"]["environment"]
    assert chassis["LITELLM_BASE_URL"] == "http://litellm:4000/v1"
    assert chassis["LITELLM_API_KEY"].startswith("${POC06_LITELLM_KEY:?")


def test_the_scale_overlay_changes_only_the_pairs_litellm_and_the_seed() -> None:
    services = load(SCALE_OVERLAY)["services"]
    assert set(services) == {
        "litellm",
        "chassis-1",
        "chassis-2",
        "chassis-3",
        "chassis-4",
        "minio-init",
    }
    base = load(COMPOSE / "docker-compose.scale.yaml")["services"]
    assert set(services) <= set(base) | {"litellm"}
    assert "litellm" not in base, "the PoC-4 stack has no LiteLLM"
    for name in ("chassis-1", "chassis-2", "chassis-3", "chassis-4"):
        assert "ports" not in services[name]
        assert services[name]["volumes"] == ["./poc06/chassis-configs:/etc/chassis:ro"]
    assert services["minio-init"]["volumes"] == ["./poc06/chassis-configs:/seed/configs:ro"]


def test_the_scale_chassis_config_differs_from_poc04_only_in_the_route() -> None:
    slm = load(SCALE_SLM_CONFIG)
    base = load(ROOT / "packages" / "chassis" / "configs" / "scale.yaml")
    assert base["spec"]["model"]["route"] == "big-default"
    assert slm["spec"]["model"]["route"] == "local-small"
    base["spec"]["model"]["route"] = "local-small"
    assert slm == base
    assert load_config(slm).spec.model.route == "local-small"


# ---- the script -------------------------------------------------------------------------------


def test_the_dry_run_exits_0_and_prints_every_step(tmp_path: Path) -> None:
    done = run_script(tmp_path, "--dry-run")
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout
    for needle in (
        "DRY RUN",
        "step hosted",
        "step slm",
        "step scale",
        "step load",
        "step kind",
        "== files this run needs",
        "--route big-default",
        "--route local-small",
        "--jinja --chat-template-kwargs",
        "enable_thinking",
        "scale-slm.sh build",
        "load_driver.py",
        "Qwen3-1.7B-Q8_0.gguf",
        "model.sha256",
        "-hosted-run.md",
    ):
        assert needle in out, needle
    assert "MISSING" not in out
    assert not (POC06 / "model.sha256").exists(), "a dry run records nothing"


def test_the_dry_run_runs_and_writes_nothing(tmp_path: Path) -> None:
    before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    done = run_script(tmp_path, "--dry-run", "--push")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "git push" in done.stdout
    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    assert after == before
    assert list(tmp_path.iterdir()) == [], "no cache, no temp file in HOME"


def test_the_dry_run_never_reads_or_prints_a_key(tmp_path: Path) -> None:
    secret = "dry-run-secret-value-xyz"  # pragma: allowlist secret
    done = run_script(tmp_path, "--dry-run", OPENAI_API_KEY=secret, POC06_LITELLM_KEY=secret)
    assert done.returncode == 0
    assert secret not in done.stdout + done.stderr
    assert "value is never printed" in done.stdout


@pytest.mark.parametrize("only", ["hosted", "slm", "scale", "load", "kind"])
def test_each_step_can_be_dry_run_alone(tmp_path: Path, only: str) -> None:
    done = run_script(tmp_path, "--dry-run", "--only", only)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"== step {only}" in done.stdout
    others = {"hosted", "slm", "scale", "load", "kind"} - {only}
    for other in others:
        assert f"== step {other}" not in done.stdout


def test_an_unknown_step_or_option_is_a_usage_error(tmp_path: Path) -> None:
    assert run_script(tmp_path, "--dry-run", "--only", "bogus").returncode == 2
    assert run_script(tmp_path, "--bogus").returncode == 2


@pytest.mark.parametrize("engine", UNTRUSTED)
def test_the_script_refuses_an_untrusted_engine_in_hosted_mode(tmp_path: Path, engine: str) -> None:
    for args in (
        ("--dry-run", "--engine", engine),
        ("--dry-run", "--only", "hosted", "--engine", f"echo-python,{engine}"),
        ("--dry-run", "--only", "slm", "--engine", engine),
    ):
        done = run_script(tmp_path, *args)
        assert done.returncode == 2, (args, done.stdout)
        assert f"refusing {engine}" in done.stderr
        assert "DRY RUN" not in done.stdout, "refused before any step is planned"


@pytest.mark.parametrize("variable", ["POC06_SCALE_ENGINES", "POC06_LOAD_ENGINES"])
def test_the_script_refuses_an_untrusted_engine_in_the_scale_and_load_lists(
    tmp_path: Path, variable: str
) -> None:
    done = run_script(tmp_path, "--dry-run", **{variable: "echo-python echo-smolagents"})
    assert done.returncode == 2
    assert "refusing echo-smolagents" in done.stderr


def test_the_script_refuses_an_engine_it_does_not_know(tmp_path: Path) -> None:
    done = run_script(tmp_path, "--dry-run", "--engine", "echo-new-framework")
    assert done.returncode == 2
    assert "not on the trusted list" in done.stderr


def test_the_scripts_trusted_list_is_the_registrys() -> None:
    text = SCRIPT.read_text()
    trusted = re.search(r'^TRUSTED_ENGINES="([^"]*)"', text, re.M)
    untrusted = re.search(r'^UNTRUSTED_ENGINES="([^"]*)"', text, re.M)
    assert trusted and untrusted
    assert sorted(trusted.group(1).split()) == TRUSTED
    assert sorted(untrusted.group(1).split()) == UNTRUSTED


def test_the_script_keeps_the_key_rules() -> None:
    lines = SCRIPT.read_text().splitlines()
    code = [line for line in lines if not line.lstrip().startswith("#")]
    text = "\n".join(code)
    assert "set -euo pipefail" in text
    assert "set -x" not in text and "xtrace" not in text
    assert "--no-verify" not in text and "--force" not in text and "push -f" not in text
    assert "export HOSTED_KEY" not in text and "declare -x HOSTED_KEY" not in text
    # The one other use of the key: the kind step hands it to hosted.sh on stdin, through a pipe.
    stdin_use = re.compile(
        r"""^\s*if printf '%s' "\$HOSTED_KEY" \| POC06_HOSTED_MODEL=\S+ run_logged """
    )
    assert sum(1 for line in code if stdin_use.match(line)) == 1
    for line in code:
        if stdin_use.match(line):
            assert '"$KIND_HOSTED" up; then' in line
            continue
        if "HOSTED_KEY" in line.replace("HOSTED_KEY_ENV", ""):
            assert not re.search(r"\b(echo|printf|say|warn|die|tee)\b", line) or "redact" in line
    # the key is handed to one command only, as a prefix assignment
    assert text.count('POC06_HOSTED_API_KEY="$key"') == 1
    # --push stages exact paths, on a branch that is not main
    assert 'git -C "$ROOT" add -- "${NOTE_FILES[@]}"' in text
    assert re.search(r"git [^\n]*\badd (-A|\.|--all|-u)", text) is None
    assert "main | master" in text


def test_the_script_names_only_the_notes_the_task_lists() -> None:
    text = SCRIPT.read_text()
    for stem in ("hosted-run", "slm-run", "scale-run", "load-run", "kind-run"):
        assert f"write_note {stem}" in text
    assert 'NOTES_A="pocs/poc-06a-bake-off-sidecar-lane/notes"' in text
    assert 'NOTES_C="pocs/poc-06c-pretrained-slm/notes"' in text
    assert 'NOTES_B="pocs/poc-06b-bake-off-remote-lane/notes"' in text


# ---- scale-slm.sh and the driver --------------------------------------------------------------


def test_scale_slm_refuses_an_untrusted_or_unknown_engine_before_touching_docker(
    tmp_path: Path,
) -> None:
    for engine in (*UNTRUSTED, "echo-new-framework"):
        done = subprocess.run(
            ["bash", str(SCALE_SLM), "up", engine, "1"],
            env=clean_env(tmp_path),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert done.returncode == 2, engine
        assert "engine:" in done.stderr


def test_scale_slm_rejects_an_unknown_model_mode(tmp_path: Path) -> None:
    done = subprocess.run(
        ["bash", str(SCALE_SLM), "up", "echo-python", "1"],
        env=clean_env(tmp_path, POC06_SCALE_MODEL="bogus"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 2
    assert "fake or slm" in done.stderr


def test_the_load_driver_plans_the_poc04_matrix_through_scale_slm(tmp_path: Path) -> None:
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            str(DRIVER),
            "--out",
            str(tmp_path / "out"),
            "--engine",
            "echo-openai-agents",
            "--pairs",
            "1",
            "--pairs",
            "2",
            "--only",
            "main",
            "--dry-run",
        ],
        env=clean_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.count("scale-slm.sh up echo-openai-agents") == 2
    assert "scale-slm.sh down" in done.stdout
    assert str(tmp_path / "out") in done.stdout, "raw files go to --out, not to the PoC-4 notes"
    assert "poc-04-stateless-scalable/notes" not in done.stdout
    assert not (tmp_path / "out" / "results.md").exists()


# ---- bakeoff ----------------------------------------------------------------------------------


@pytest.mark.parametrize("engine", UNTRUSTED)
def test_bakeoff_refuses_an_untrusted_engine_with_a_non_fake_model_url(
    engine: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("POC06_LITELLM_KEY", "unit-test-value")
    argv = [
        "run",
        "--engine",
        engine,
        "--model-url",
        "http://127.0.0.1:14000/v1",
        "--model-key-env",
        "POC06_LITELLM_KEY",
        "--out",
        str(tmp_path),
    ]
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert "untrusted" in err
    assert "unit-test-value" not in err
    assert list(tmp_path.iterdir()) == []


def test_the_notes_hold_no_key(tmp_path: Path) -> None:
    """The writer redacts; the check here is that the script's redaction pattern is in place."""
    text = SCRIPT.read_text()
    assert "redact <" in text
    assert "[redacted]" in text


# ---- security review fixes --------------------------------------------------------------------


def _code() -> str:
    lines = SCRIPT.read_text().splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def test_the_run_key_is_never_exported(tmp_path: Path) -> None:
    code = _code()
    assert not re.search(r"\bexport\s+POC06_LITELLM_KEY", code)
    assert not re.search(r"\b(export|declare -x)\b[^\n]*(LITELLM_KEY|HOSTED_KEY)", code)
    # it reaches a command only as a prefix assignment
    for line in code.splitlines():
        if "POC06_LITELLM_KEY=" in line:
            assert re.match(
                r"\s*(POC06_SCALE_MODEL=\w+ )?POC06_LITELLM_KEY=\"\$LITELLM_KEY\"", line
            )
    done = run_script(tmp_path, "--dry-run", POC06_LITELLM_KEY="outside-value")
    assert done.returncode == 0
    assert "outside-value" not in done.stdout + done.stderr


def test_only_the_hosted_steps_litellm_gets_the_provider_key() -> None:
    code = _code()
    assert code.count('start_litellm "$log" hosted') == 1
    assert code.count('start_litellm "$log"') == 2
    assert 'POC06_HOSTED_API_KEY="$key"' in code
    assert "key=$HOSTED_KEY" in code


def test_the_slm_dry_run_never_looks_at_the_env_file(tmp_path: Path) -> None:
    done = run_script(tmp_path, "--dry-run", "--only", "slm")
    assert done.returncode == 0
    assert "env file" not in done.stdout and "deploy/compose/.env" not in done.stdout
    assert "presence of" not in done.stdout
    # slm needs no provider key in the code path either
    start = _code().index("preflight_real()")
    body = _code()[start : _code().index("ensure_gguf()")]
    assert "if want hosted || want kind; then" in body
    assert body.index("ENV_FILE") > body.index("if want hosted || want kind; then")


def _redact(text: str, tmp_path: Path) -> str:
    source = SCRIPT.read_text()
    func = source[source.index("redact() {") : source.index("# Run a command, show it")]
    done = subprocess.run(
        ["bash", "-c", f"{func}\nHOSTED_KEY=provider-secret-9\nLITELLM_KEY=sk-poc06-abc\nredact"],
        input=text,
        env={"PATH": os.environ["PATH"], "HOME": "/Users/someone"},
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def test_redact_scrubs_keys_masked_keys_and_the_home_path(tmp_path: Path) -> None:
    text = (
        "a provider-secret-9 b sk-proj-****abcd c Bearer abcdefghijkl1234 d sk-poc06-abc "
        "e sk-abcdefghijklmnop f /Users/someone/.cache/poc06/x.gguf"
    )
    out = _redact(text, tmp_path)
    for leaked in ("provider-secret-9", "abcd c", "abcdefghijkl1234", "ijklmnop", "/Users/someone"):
        assert leaked not in out, out
    assert "~/.cache/poc06/x.gguf" in out


def test_the_model_download_is_https_only_and_the_names_are_validated(tmp_path: Path) -> None:
    code = _code()
    assert "--proto =https --proto-redir =https" in code
    assert "GGUF_REV" in code
    for var, value in (
        ("POC06_GGUF_FILE", "../evil.gguf"),
        ("POC06_GGUF_FILE", "a b.gguf"),
        ("POC06_GGUF_REPO", "no-slash"),
        ("POC06_GGUF_REV", "main;rm"),
    ):
        done = run_script(tmp_path, "--dry-run", **{var: value})
        assert done.returncode == 2, (var, value)
    done = run_script(tmp_path, "--dry-run", POC06_GGUF_REV="abc123")
    assert done.returncode == 0
    assert "resolve/abc123/" in done.stdout


def test_push_refuses_other_unpushed_commits_and_the_teardown_ignores_signals() -> None:
    code = _code()
    assert "@{u}..HEAD" in code
    assert "would leave with the notes" in code
    assert "trap '' INT TERM HUP" in code
    assert "trap - EXIT INT TERM HUP" not in code


def test_security_md_records_the_poc06_exception_and_the_debt_note_exists() -> None:
    text = (COMPOSE / "SECURITY.md").read_text()
    assert "PoC-6 exception" in text
    assert "provider budget" in text
    debt = ROOT / "pocs" / "poc-06c-pretrained-slm" / "notes" / "2026-10-09-mac-command-debt.md"
    body = debt.read_text()
    assert "/key/generate" in body and "026" in body
    assert not re.search(r"\bsk-[A-Za-z0-9]", body)
