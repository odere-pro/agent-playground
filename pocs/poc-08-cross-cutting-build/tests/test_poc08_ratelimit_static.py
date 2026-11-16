"""022 H-6, the rate limit across two pods, offline half of the stack drill: the two chassis of
`deploy/compose/poc08` that share one bucket are shaped right before a runner starts Docker.

- `chassis-rl-a` and `chassis-rl-b` run the same `chassis/ratelimit.yaml` (one agent name, one
  small limit, Valkey, `fail_closed`), with the same caller map and the same `VALKEY_URL`, so
  one caller has one bucket key on both. Ports on 127.0.0.1 only.
- `stack.sh` makes the second caller's token and waits for both chassis.
- The drill's plan fits the config: A's and B's calls add up to the burst, B alone is refused
  before its own count reaches the burst, and the refill over the drill's window is under one
  token.
- The drill is collected only with `COMPOSE_STACK=1`, and is `network`.
- The Valkey command keeps the password inside the container.

The live half is `test_poc08_stack_ratelimit.py` (`network`).
"""

from __future__ import annotations

import importlib.util
import json
import re
import runpy
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from chassis.profiles import REGISTRY
from chassis.server.config import load_config
from chassis.server.rate_limit import bucket_key
from poc08_ratelimit import (
    AGENT,
    CALLER,
    CONFIG,
    DRILL,
    OTHER,
    OTHER_TOKEN_KEY,
    SERVICES,
    SPEND_ON_A,
    SPEND_ON_B,
    WINDOW_S,
    bucket_keys,
    valkey_cli_argv,
)
from poc_harness.metrics_stack import STACK, STACK_VAR, TOKEN_KEY

HERE = Path(__file__).resolve().parent
COMPOSE = STACK / "docker-compose.yaml"
STACK_SH = STACK / "stack.sh"
ENV_KEYS = (TOKEN_KEY, OTHER_TOKEN_KEY, "VALKEY_PASSWORD", "GRAFANA_ADMIN_PASSWORD")
"""Every key `stack.sh` makes; the Compose file needs each one, `down` included."""


def _services() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(COMPOSE.read_text())
    assert isinstance(data, dict)
    services: dict[str, dict[str, Any]] = data["services"]
    return services


def _callers(service: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = service["environment"]["CHASSIS_STATIC_TOKENS"]
    callers: dict[str, dict[str, Any]] = json.loads(raw)
    return callers


def test_both_chassis_serve_the_ratelimit_config_on_localhost() -> None:
    services = _services()
    for name, url in SERVICES.items():
        spec = services[name]
        command = [str(c) for c in spec["command"]]
        assert command[command.index("--config") + 1] == f"/etc/chassis/{CONFIG}", name
        assert any(str(v).startswith("./chassis:/etc/chassis") for v in spec["volumes"]), name
        [port] = [str(p) for p in spec["ports"]]
        assert port == f"127.0.0.1:{url.rsplit(':', 1)[1]}:8080", name
        assert spec["image"] == services["chassis"]["image"], "the one chassis image"
        assert spec["cap_drop"] == ["ALL"], name


def test_the_two_chassis_share_the_store_and_the_caller_map() -> None:
    a, b = (_services()[name] for name in SERVICES)
    assert a["environment"] == b["environment"], "one env: one store, one caller map"
    assert a["environment"]["VALKEY_URL"] == _services()["chassis"]["environment"]["VALKEY_URL"]
    callers = _callers(a)
    assert {entry["subject"] for entry in callers.values()} == {CALLER, OTHER}
    assert all("agent:run" in entry["scopes"] for entry in callers.values())
    tokens = set(callers)
    assert tokens == {
        f"${{{TOKEN_KEY}:?set by stack.sh}}",
        f"${{{OTHER_TOKEN_KEY}:?set by stack.sh}}",
    }, "both tokens come from stack.sh, no literal"


def test_the_ratelimit_config_is_a_small_valkey_limit_that_fails_closed() -> None:
    config = load_config(STACK / "chassis" / CONFIG)
    assert config.agent.name == AGENT
    adapters = config.spec.adapters
    assert adapters is not None
    for port, adapter in adapters.model_dump(exclude_none=True).items():
        assert adapter in REGISTRY[port], f"{port}: {adapter}"
    assert (adapters.state, adapters.auth) == ("valkey", "static")
    limits = config.spec.limits
    assert limits.rate_limit and limits.rate_limit_on_unavailable == "fail_closed"
    agents = {
        load_config(STACK / "chassis" / name).agent.name
        for name in ("drill.yaml", "stages.yaml", "plain.yaml")
    }
    assert AGENT not in agents, "its own bucket: the other drills never spend it"


def test_the_drill_plan_fits_the_limit() -> None:
    limits = load_config(STACK / "chassis" / CONFIG).spec.limits
    assert SPEND_ON_A >= 1 and SPEND_ON_B >= 1
    assert limits.request_burst == SPEND_ON_A + SPEND_ON_B, "A and B spend the whole bucket"
    assert limits.request_burst >= SPEND_ON_B + 1, "B alone would still have a token"
    refill = limits.requests_per_minute / 60.0 * WINDOW_S
    assert refill < 1, f"the bucket refills {refill:.2f} tokens inside the drill's window"


def test_bucket_keys_are_one_per_caller_for_the_agent() -> None:
    keys = bucket_keys()
    assert keys == {CALLER: bucket_key(AGENT, CALLER), OTHER: bucket_key(AGENT, OTHER)}
    assert len(set(keys.values())) == 2
    assert all(key.startswith(f"ratelimit:{AGENT}:") for key in keys.values())
    assert not any(CALLER in key.split(":", 2)[2] for key in keys.values()), "subject hashed"


def test_stack_sh_makes_the_other_token_and_waits_for_both_chassis() -> None:
    text = STACK_SH.read_text()
    assert f'echo "{OTHER_TOKEN_KEY}=' in text
    for url in SERVICES.values():
        assert f"{url}/ready" in text


def _run_make_env(env_file: Path) -> None:
    """Runs only `make_env` of stack.sh (cut out of the script) on `env_file`. No Docker."""
    found = re.search(r"^make_env\(\) \{\n.*?^\}\n", STACK_SH.read_text(), re.M | re.S)
    assert found, "stack.sh has no make_env function"
    script = f'set -euo pipefail\nenv_file="$1"\n{found.group(0)}make_env\n'
    done = subprocess.run(
        ["bash", "-c", script, "make_env", str(env_file)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-500:]


def _keys(env_file: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line)


def test_make_env_adds_the_missing_key_to_an_old_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.poc08"
    old = {  # the three keys of an env file from before POC08_OTHER_TOKEN
        TOKEN_KEY: "poc08-old0run0token",  # pragma: allowlist secret
        "VALKEY_PASSWORD": "old0valkey0pw",  # pragma: allowlist secret
        "GRAFANA_ADMIN_PASSWORD": "old0grafana0pw",  # pragma: allowlist secret
    }
    env_file.write_text("".join(f"{k}={v}\n" for k, v in old.items()))
    _run_make_env(env_file)
    keys = _keys(env_file)
    assert set(keys) == set(ENV_KEYS), "all four keys, each once"
    assert len(env_file.read_text().splitlines()) == len(ENV_KEYS)
    assert {k: keys[k] for k in old} == old, "the old values are kept"
    assert keys[OTHER_TOKEN_KEY].startswith("poc08-") and len(keys[OTHER_TOKEN_KEY]) > 40
    _run_make_env(env_file)
    assert _keys(env_file) == keys, "a second run changes nothing"


def test_make_env_writes_a_new_file_with_every_key_and_mode_600(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.poc08"
    _run_make_env(env_file)
    keys = _keys(env_file)
    assert set(keys) == set(ENV_KEYS)
    assert len(set(keys.values())) == len(ENV_KEYS), "each value is its own random"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_the_valkey_command_keeps_the_password_in_the_container(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.poc08"
    env_file.write_text("VALKEY_PASSWORD=pw0123456789\n")  # pragma: allowlist secret
    argv = valkey_cli_argv(env_file, "--scan", "--pattern", f"ratelimit:{AGENT}:*")
    assert argv[:4] == ["docker", "compose", "-p", "poc08"]
    assert str(env_file) in argv and str(COMPOSE) in argv
    assert argv[argv.index("exec") : argv.index("exec") + 3] == ["exec", "-T", "valkey"]
    assert argv[-3:] == ["--scan", "--pattern", f"ratelimit:{AGENT}:*"]
    assert "pw0123456789" not in " ".join(argv)
    assert any('REDISCLI_AUTH="$VALKEY_PASSWORD"' in part for part in argv)


def test_the_drill_is_gated_and_marked_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(STACK_VAR, raising=False)
    assert DRILL in runpy.run_path(str(HERE / "conftest.py"))["collect_ignore"]
    monkeypatch.setenv(STACK_VAR, "1")
    assert DRILL not in runpy.run_path(str(HERE / "conftest.py"))["collect_ignore"]
    monkeypatch.delenv(STACK_VAR, raising=False)
    spec = importlib.util.spec_from_file_location("poc08_ratelimit_drill_probe", HERE / DRILL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    marks = {mark.name: mark for mark in module.pytestmark}
    assert "network" in marks
    assert marks["skipif"].args == (True,)
