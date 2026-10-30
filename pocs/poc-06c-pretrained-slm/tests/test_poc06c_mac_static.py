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
    """Each new Compose or LiteLLM file exists and parses to a mapping."""
    exists = path.is_file()

    data = load(path)

    assert exists
    assert data


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: p.name)
def test_the_yaml_text_names_no_key_value(path: Path) -> None:
    """The file's text has no `sk-` literal, no env file, no host network, no privilege, no
    Docker socket."""
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


KEY_VARS = [
    (path, name, var, value)
    for path in (HOSTED_COMPOSE, SCALE_OVERLAY)
    for name, service in load(path)["services"].items()
    for var, value in _env_of(service).items()
    if KEY_NAME.search(var)
]


@pytest.mark.parametrize(
    ("path", "name", "var", "value"),
    KEY_VARS,
    ids=[f"{p.name}-{n}-{v}" for p, n, v, _ in KEY_VARS],
)
def test_every_secret_variable_is_an_interpolation_or_a_placeholder(
    path: Path, name: str, var: str, value: str
) -> None:
    """A variable named like a key holds `${...}` or the placeholder `unused`, never a value."""
    is_interpolation = INTERPOLATION.match(value)

    assert is_interpolation or value == "unused", (path.name, name, var)


def test_the_hosted_compose_file_is_litellm_alone_and_pinned() -> None:
    """The hosted file runs LiteLLM alone, on the PoC-1 pinned image, on loopback, with no caps."""
    compose = load(HOSTED_COMPOSE)
    base = load(COMPOSE / "docker-compose.yaml")["services"]["litellm"]["image"]

    litellm = compose["services"]["litellm"]
    env = _env_of(litellm)

    assert compose["name"] == "poc06mac"
    assert list(compose["services"]) == ["litellm"]
    assert re.search(r"@sha256:[0-9a-f]{64}$", litellm["image"])
    assert litellm["image"] == base, "the same pinned image as the PoC-1 stack"
    assert litellm["ports"] == ["127.0.0.1:${POC06_LITELLM_PORT:-14000}:4000"]
    assert env["LITELLM_MASTER_KEY"].startswith("${POC06_LITELLM_KEY:?")
    assert env["POC06_HOSTED_API_KEY"] == "${POC06_HOSTED_API_KEY:-unused}"
    assert litellm["cap_drop"] == ["ALL"]
    assert load(HOSTED_COMPOSE)["networks"]["default"]["name"] == "poc06mac"


def test_the_litellm_config_routes_big_default_hosted_and_local_small_to_the_host() -> None:
    """`big-default` goes to the hosted model, `local-small` to the host's llama-server."""
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


def test_the_litellm_config_caps_the_proxy_spend_with_a_budget() -> None:
    """The master key has no scope, so a proxy-wide budget bounds the provider spend."""
    config = load(LITELLM_CONFIG)

    budget = config["litellm_settings"].get("max_budget")

    assert isinstance(budget, int | float) and budget > 0


@pytest.mark.parametrize("path", [HOSTED_COMPOSE, SCALE_OVERLAY], ids=lambda p: p.name)
def test_litellm_reaches_the_llama_server_through_host_docker_internal(path: Path) -> None:
    """LiteLLM's llama base is host.docker.internal, and the config is mounted read-only."""
    litellm = load(path)["services"]["litellm"]

    env = _env_of(litellm)
    mounts = "\n".join(litellm["volumes"])

    assert env["POC06_LLAMA_BASE"].startswith("http://host.docker.internal:")
    assert env["POC06_LLAMA_BASE"].endswith("/v1")
    assert "litellm.config.yaml:/etc/litellm/config.yaml:ro" in mounts


OVERLAY_OTHERS = sorted(n for n in load(SCALE_OVERLAY)["services"] if n != "litellm")


def test_the_provider_key_reaches_only_the_litellm_service() -> None:
    """In the hosted file only LiteLLM gets the provider key; the scale run's LiteLLM gets the
    placeholder."""
    hosted = load(HOSTED_COMPOSE)["services"]
    overlay = load(SCALE_OVERLAY)["services"]

    holders = [n for n, s in hosted.items() if "POC06_HOSTED_API_KEY" in _env_of(s)]
    placeholder = _env_of(overlay["litellm"])["POC06_HOSTED_API_KEY"]  # pragma: allowlist secret

    assert holders == ["litellm"]
    assert placeholder == "unused", "the scale run has no provider key"


@pytest.mark.parametrize("name", OVERLAY_OTHERS)
def test_the_scale_service_sees_no_hosted_variable_when_it_is_not_litellm(name: str) -> None:
    """No other scale-overlay service gets a HOSTED variable."""
    env = _env_of(load(SCALE_OVERLAY)["services"][name])

    hosted = [v for v in env if "HOSTED" in v]

    assert not hosted, name


def test_the_scale_chassis_reach_litellm_with_the_run_key() -> None:
    """The scale chassis anchor points at LiteLLM and takes the run key by interpolation."""
    chassis = load(SCALE_OVERLAY)["x-slm-chassis"]["environment"]

    base_url, key = chassis["LITELLM_BASE_URL"], chassis["LITELLM_API_KEY"]

    assert base_url == "http://litellm:4000/v1"
    assert key.startswith("${POC06_LITELLM_KEY:?")


SCALE_PAIRS = ("chassis-1", "chassis-2", "chassis-3", "chassis-4")


def test_the_scale_overlay_changes_only_the_pairs_litellm_and_the_seed() -> None:
    """The overlay touches the four pairs, adds LiteLLM, and remounts the seed's configs."""
    services = load(SCALE_OVERLAY)["services"]

    base = load(COMPOSE / "docker-compose.scale.yaml")["services"]

    assert set(services) == {"litellm", *SCALE_PAIRS, "minio-init"}
    assert set(services) <= set(base) | {"litellm"}
    assert "litellm" not in base, "the PoC-4 stack has no LiteLLM"
    assert services["minio-init"]["volumes"] == ["./poc06/chassis-configs:/seed/configs:ro"]


@pytest.mark.parametrize("name", SCALE_PAIRS)
def test_the_scale_chassis_mounts_the_poc06_configs_and_publishes_no_port(name: str) -> None:
    """Each scale chassis publishes no port and mounts the PoC-6 configs read-only."""
    service = load(SCALE_OVERLAY)["services"][name]

    volumes = service["volumes"]

    assert "ports" not in service
    assert volumes == ["./poc06/chassis-configs:/etc/chassis:ro"]


def test_the_scale_chassis_config_differs_from_poc04_only_in_the_route() -> None:
    """The SLM scale config is PoC-4's with the route switched to `local-small`."""
    slm = load(SCALE_SLM_CONFIG)
    base = load(ROOT / "packages" / "chassis" / "configs" / "scale.yaml")
    base_route = base["spec"]["model"]["route"]

    base["spec"]["model"]["route"] = "local-small"

    assert base_route == "big-default"
    assert slm["spec"]["model"]["route"] == "local-small"
    assert slm == base
    assert load_config(slm).spec.model.route == "local-small"


# ---- the script -------------------------------------------------------------------------------


def pin_state() -> bytes | None:
    """model.sha256's bytes, or None when it does not exist."""
    pin = POC06 / "model.sha256"
    return pin.read_bytes() if pin.exists() else None


DRY_RUN_NEEDLES = (
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
)
STEPS = ("hosted", "slm", "scale", "load", "kind")


def test_the_dry_run_exits_0_and_prints_every_step(tmp_path: Path) -> None:
    """`--dry-run` exits 0, prints every step and file, and leaves model.sha256 as it was."""
    pin_before = pin_state()

    done = run_script(tmp_path, "--dry-run")
    missing = [needle for needle in DRY_RUN_NEEDLES if needle not in done.stdout]

    assert done.returncode == 0, done.stdout + done.stderr
    assert not missing, missing
    assert "MISSING" not in done.stdout
    assert pin_state() == pin_before, "a dry run neither creates nor changes model.sha256"


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def test_the_dry_run_runs_and_writes_nothing(tmp_path: Path) -> None:
    """A dry run with `--push` only prints the push, and writes nothing in git or HOME."""
    before = _git_status()

    done = run_script(tmp_path, "--dry-run", "--push")
    after = _git_status()

    assert done.returncode == 0, done.stdout + done.stderr
    assert "git push" in done.stdout
    assert after == before
    assert list(tmp_path.iterdir()) == [], "no cache, no temp file in HOME"


def test_the_dry_run_never_reads_or_prints_a_key(tmp_path: Path) -> None:
    """A key in the environment never reaches the dry run's output."""
    secret = "dry-run-secret-value-xyz"  # pragma: allowlist secret

    done = run_script(tmp_path, "--dry-run", OPENAI_API_KEY=secret, POC06_LITELLM_KEY=secret)

    assert done.returncode == 0
    assert secret not in done.stdout + done.stderr
    assert "value is never printed" in done.stdout


@pytest.mark.parametrize("only", STEPS)
def test_each_step_can_be_dry_run_alone(tmp_path: Path, only: str) -> None:
    """`--only <step>` plans that step and no other."""
    done = run_script(tmp_path, "--dry-run", "--only", only)

    others = [s for s in STEPS if s != only and f"== step {s}" in done.stdout]

    assert done.returncode == 0, done.stdout + done.stderr
    assert f"== step {only}" in done.stdout
    assert not others, others


def test_an_unknown_step_or_option_is_a_usage_error(tmp_path: Path) -> None:
    """An unknown step or option exits 2."""
    step = run_script(tmp_path, "--dry-run", "--only", "bogus")

    option = run_script(tmp_path, "--bogus")

    assert step.returncode == 2
    assert option.returncode == 2


def _hosted_mode_args(engine: str) -> list[tuple[str, ...]]:
    return [
        ("--dry-run", "--engine", engine),
        ("--dry-run", "--only", "hosted", "--engine", f"echo-python,{engine}"),
        ("--dry-run", "--only", "slm", "--engine", engine),
    ]


HOSTED_MODE_CASES = [(e, a) for e in UNTRUSTED for a in _hosted_mode_args(e)]


@pytest.mark.parametrize(
    ("engine", "args"),
    HOSTED_MODE_CASES,
    ids=[f"{e}-{' '.join(a[1:])}" for e, a in HOSTED_MODE_CASES],
)
def test_the_script_refuses_an_untrusted_engine_in_hosted_mode(
    tmp_path: Path, engine: str, args: tuple[str, ...]
) -> None:
    """An untrusted engine, alone or in a list, is refused before any step is planned."""
    done = run_script(tmp_path, *args)

    assert done.returncode == 2, (args, done.stdout)
    assert f"refusing {engine}" in done.stderr
    assert "DRY RUN" not in done.stdout, "refused before any step is planned"


@pytest.mark.parametrize("variable", ["POC06_SCALE_ENGINES", "POC06_LOAD_ENGINES"])
def test_the_script_refuses_an_untrusted_engine_in_the_scale_and_load_lists(
    tmp_path: Path, variable: str
) -> None:
    """An untrusted engine in the scale or load list is refused."""
    done = run_script(tmp_path, "--dry-run", **{variable: "echo-python echo-smolagents"})

    assert done.returncode == 2
    assert "refusing echo-smolagents" in done.stderr


def test_the_script_refuses_an_engine_it_does_not_know(tmp_path: Path) -> None:
    """An engine on neither list is refused."""
    done = run_script(tmp_path, "--dry-run", "--engine", "echo-new-framework")

    assert done.returncode == 2
    assert "not on the trusted list" in done.stderr


def test_the_script_text_lists_the_registrys_trusted_engines() -> None:
    """The script's TRUSTED_ENGINES and UNTRUSTED_ENGINES lines are the registry's split."""
    text = SCRIPT.read_text()

    trusted = re.search(r'^TRUSTED_ENGINES="([^"]*)"', text, re.M)
    untrusted = re.search(r'^UNTRUSTED_ENGINES="([^"]*)"', text, re.M)

    assert trusted and untrusted
    assert sorted(trusted.group(1).split()) == TRUSTED
    assert sorted(untrusted.group(1).split()) == UNTRUSTED


STDIN_USE = re.compile(
    r"""^\s*if printf '%s' "\$HOSTED_KEY" \| POC06_HOSTED_MODEL=\S+ run_logged """
)
SAYS = re.compile(r"\b(echo|printf|say|warn|die|tee)\b")


def test_the_script_text_keeps_the_key_rules() -> None:
    """The script's text: strict, no xtrace, no forced push, the key never exported or printed
    (one stdin pipe to hosted.sh), one prefix hand-off, and `--push` stages exact paths off main."""
    code = [line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")]
    text = "\n".join(code)

    stdin_lines = [line for line in code if STDIN_USE.match(line)]
    printed = [
        line
        for line in code
        if not STDIN_USE.match(line)
        and "HOSTED_KEY" in line.replace("HOSTED_KEY_ENV", "")
        and SAYS.search(line)
        and "redact" not in line
    ]

    assert "set -euo pipefail" in text
    assert "set -x" not in text and "xtrace" not in text
    assert "--no-verify" not in text and "--force" not in text and "push -f" not in text
    assert "export HOSTED_KEY" not in text and "declare -x HOSTED_KEY" not in text
    # The one other use of the key: the kind step hands it to hosted.sh on stdin, through a pipe.
    assert len(stdin_lines) == 1
    assert '"$KIND_HOSTED" up; then' in stdin_lines[0]
    assert not printed, printed
    # the key is handed to one command only, as a prefix assignment
    assert text.count('POC06_HOSTED_API_KEY="$key"') == 1
    # --push stages exact paths, on a branch that is not main
    assert 'git -C "$ROOT" add -- "${NOTE_FILES[@]}"' in text
    assert re.search(r"git [^\n]*\badd (-A|\.|--all|-u)", text) is None
    assert "main | master" in text


@pytest.mark.parametrize("stem", ["hosted-run", "slm-run", "scale-run", "load-run", "kind-run"])
def test_the_script_text_writes_the_note_the_task_lists(stem: str) -> None:
    """The script's text writes each note the task names."""
    text = SCRIPT.read_text()

    assert f"write_note {stem}" in text


def test_the_script_text_names_the_three_notes_folders() -> None:
    """The script's text puts the notes in the 6a, 6b, and 6c notes folders."""
    text = SCRIPT.read_text()

    assert 'NOTES_A="pocs/poc-06a-bake-off-sidecar-lane/notes"' in text
    assert 'NOTES_C="pocs/poc-06c-pretrained-slm/notes"' in text
    assert 'NOTES_B="pocs/poc-06b-bake-off-remote-lane/notes"' in text


# ---- scale-slm.sh and the driver --------------------------------------------------------------


@pytest.mark.parametrize("engine", [*UNTRUSTED, "echo-new-framework"])
def test_scale_slm_refuses_an_untrusted_or_unknown_engine_before_touching_docker(
    tmp_path: Path, engine: str
) -> None:
    """scale-slm.sh exits 2 on an untrusted or unknown engine, with no Docker on PATH needed."""
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
    """A model mode other than fake or slm is a usage error."""
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
    """The driver's dry run plans one `up` per pair count and raw files under `--out`."""
    argv = [
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
    ]

    done = subprocess.run(
        argv, env=clean_env(tmp_path), capture_output=True, text=True, timeout=120, check=False
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
    """`bakeoff run` exits 2 for an untrusted engine on a real model URL, writes nothing, and
    does not print the key."""
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

    code = cli.main(argv)
    err = capsys.readouterr().err

    assert code == 2
    assert "untrusted" in err
    assert "unit-test-value" not in err
    assert list(tmp_path.iterdir()) == []


def test_the_script_text_redacts_what_it_writes_to_the_notes(tmp_path: Path) -> None:
    """The writer redacts; the check here is that the script's redaction pattern is in place."""
    text = SCRIPT.read_text()

    assert "redact <" in text
    assert "[redacted]" in text


# ---- security review fixes --------------------------------------------------------------------


def _code() -> str:
    lines = SCRIPT.read_text().splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


PREFIX_HANDOFF = re.compile(r"\s*(POC06_SCALE_MODEL=\w+ )?POC06_LITELLM_KEY=\"\$LITELLM_KEY\"")


def test_the_run_key_is_never_exported(tmp_path: Path) -> None:
    """The run key is never exported, reaches a command only as a prefix, and never prints."""
    code = _code()

    handoffs = [line for line in code.splitlines() if "POC06_LITELLM_KEY=" in line]
    loose = [line for line in handoffs if not PREFIX_HANDOFF.match(line)]
    done = run_script(tmp_path, "--dry-run", POC06_LITELLM_KEY="outside-value")

    assert not re.search(r"\bexport\s+POC06_LITELLM_KEY", code)
    assert not re.search(r"\b(export|declare -x)\b[^\n]*(LITELLM_KEY|HOSTED_KEY)", code)
    # it reaches a command only as a prefix assignment
    assert not loose, loose
    assert done.returncode == 0
    assert "outside-value" not in done.stdout + done.stderr


def test_the_script_text_gives_the_provider_key_to_the_hosted_litellm_only() -> None:
    """The script's text starts LiteLLM twice, once with `hosted`, and hands it the key."""
    code = _code()

    assert code.count('start_litellm "$log" hosted') == 1
    assert code.count('start_litellm "$log"') == 2
    assert 'POC06_HOSTED_API_KEY="$key"' in code
    assert "key=$HOSTED_KEY" in code


def test_the_slm_dry_run_never_looks_at_the_env_file(tmp_path: Path) -> None:
    """The slm step never names the env file, and the code reads it only for hosted or kind."""
    code = _code()
    gate = "if want hosted || want kind; then"

    done = run_script(tmp_path, "--dry-run", "--only", "slm")
    body = code[code.index("preflight_real()") : code.index("ensure_gguf()")]

    assert done.returncode == 0
    assert "env file" not in done.stdout and "deploy/compose/.env" not in done.stdout
    assert "presence of" not in done.stdout
    # slm needs no provider key in the code path either
    assert gate in body
    assert body.index("ENV_FILE") > body.index(gate)


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


REDACT_INPUT = (
    "a provider-secret-9 b sk-proj-****abcd c Bearer abcdefghijkl1234 d sk-poc06-abc "
    "e sk-abcdefghijklmnop f /Users/someone/.cache/poc06/x.gguf"
)


@pytest.mark.parametrize(
    "leaked", ["provider-secret-9", "abcd c", "abcdefghijkl1234", "ijklmnop", "/Users/someone"]
)
def test_redact_scrubs_keys_masked_keys_and_the_home_path(tmp_path: Path, leaked: str) -> None:
    """`redact` removes the exact keys, masked keys, Bearer tokens, `sk-` shapes, and HOME."""
    out = _redact(REDACT_INPUT, tmp_path)

    assert leaked not in out, out


def test_redact_rewrites_the_home_path_as_a_tilde(tmp_path: Path) -> None:
    """`redact` writes HOME as `~`."""
    out = _redact(REDACT_INPUT, tmp_path)

    assert "~/.cache/poc06/x.gguf" in out


BAD_GGUF = (
    ("POC06_GGUF_FILE", "../evil.gguf"),
    ("POC06_GGUF_FILE", "a b.gguf"),
    ("POC06_GGUF_REPO", "no-slash"),
    ("POC06_GGUF_REV", "main;rm"),
)


def test_the_model_download_is_https_only_and_the_names_are_validated(tmp_path: Path) -> None:
    """The download is HTTPS only at a pinned revision; a bad file, repo, or rev exits 2."""
    code = _code()

    accepted = [
        (var, value)
        for var, value in BAD_GGUF
        if run_script(tmp_path, "--dry-run", **{var: value}).returncode != 2
    ]
    done = run_script(tmp_path, "--dry-run", POC06_GGUF_REV="abc123")

    assert "--proto =https --proto-redir =https" in code
    assert "GGUF_REV" in code
    assert not accepted, accepted
    assert done.returncode == 0
    assert "resolve/abc123/" in done.stdout


@pytest.mark.parametrize(("host", "want"), [(None, "127.0.0.1"), ("0.0.0.0", "0.0.0.0")])
def test_the_llama_server_binds_loopback_unless_told_0_0_0_0(
    tmp_path: Path, host: str | None, want: str
) -> None:
    """Loopback by default (Docker Desktop reaches it through host.docker.internal). A Linux host,
    such as the poc06-slm.yml CI job, sets POC06_LLAMA_HOST=0.0.0.0."""
    env = {} if host is None else {"POC06_LLAMA_HOST": host}

    done = run_script(tmp_path, "--dry-run", "--only", "slm", **env)

    assert done.returncode == 0, done.stderr
    assert f"--host {want} --port 8089" in done.stdout


@pytest.mark.parametrize("value", ["10.0.0.5", "localhost", "0.0.0.0;rm"])
def test_the_llama_server_host_is_refused_when_it_is_not_loopback_or_0_0_0_0(
    tmp_path: Path, value: str
) -> None:
    """No other POC06_LLAMA_HOST value is taken."""
    done = run_script(tmp_path, "--dry-run", POC06_LLAMA_HOST=value)

    assert done.returncode == 2, value


@pytest.mark.parametrize("path", [HOSTED_COMPOSE, SCALE_OVERLAY], ids=lambda p: p.name)
def test_host_docker_internal_resolves_on_linux_docker_too(path: Path) -> None:
    """Docker Desktop resolves host.docker.internal itself; Linux Docker needs host-gateway."""
    litellm = load(path)["services"]["litellm"]

    assert litellm["extra_hosts"] == ["host.docker.internal:host-gateway"]


def test_the_script_text_refuses_other_unpushed_commits_and_ignores_teardown_signals() -> None:
    """The script's text checks `@{u}..HEAD` before a push and ignores signals in teardown."""
    code = _code()

    assert "@{u}..HEAD" in code
    assert "would leave with the notes" in code
    assert "trap '' INT TERM HUP" in code
    assert "trap - EXIT INT TERM HUP" not in code


def test_the_security_md_and_debt_note_text_record_the_poc06_exception() -> None:
    """SECURITY.md's text names the PoC-6 exception and the budget; the debt note's text names
    the per-key fix and holds no `sk-` literal."""
    text = (COMPOSE / "SECURITY.md").read_text()
    debt = ROOT / "pocs" / "poc-06c-pretrained-slm" / "notes" / "2026-10-09-mac-command-debt.md"

    body = debt.read_text()

    assert "PoC-6 exception" in text
    assert "provider budget" in text
    assert "/key/generate" in body and "026" in body
    assert not re.search(r"\bsk-[A-Za-z0-9]", body)
