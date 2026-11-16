"""022 H-6, "A caller over its rate limit gets 429, and the limit holds across two pods": two
chassis processes on one Valkey keep one bucket per caller. `network`; collected only with
`COMPOSE_STACK=1` (`conftest.py`), and skipped without it when named by path. By hand:

    deploy/compose/poc08/stack.sh up && deploy/compose/poc08/stack.sh wait
    export "$(deploy/compose/poc08/stack.sh env)"
    COMPOSE_STACK=1 uv run pytest -m network -v \\
        pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_ratelimit.py
    deploy/compose/poc08/stack.sh down

The stack: `chassis-rl-a` (18091) and `chassis-rl-b` (18092) run the same
`deploy/compose/poc08/chassis/ratelimit.yaml` (agent `echo-ratelimit`, a burst of 6, one token a
minute, `fail_closed`) with one caller map and one `VALKEY_URL`.

The drill, once per module (`scenario`): delete both callers' bucket keys in Valkey (a fresh
bucket on every run); the caller spends `SPEND_ON_A` runs on A and `SPEND_ON_B` on B, all 200;
the next run on B and the next on A are 429 `rate_limited` with `Retry-After`; the other caller
then gets 200 on both. Last, Valkey is read inside its container (`valkey-cli`, the password
never leaves it).

The control is paired with the 429 on B: B refuses after only `SPEND_ON_B` of its own runs, below
the burst, so a bucket per process would have answered 200 there. And Valkey holds exactly one
bucket key per caller for the agent, not one per chassis, the caller's with under one token.

Credentials come from the env file named by `POC08_ENV_FILE` (made by `stack.sh`), never printed.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field

import httpx
import pytest
from poc08_ratelimit import (
    AGENT,
    CALLER,
    CHASSIS_A,
    CHASSIS_B,
    EXEC_TIMEOUT_S,
    OTHER,
    OTHER_TOKEN_KEY,
    RUN_TIMEOUT_S,
    SPEND_ON_A,
    SPEND_ON_B,
    WINDOW_S,
    bucket_keys,
    valkey_cli_argv,
)
from poc_harness.drills import gate_is_on
from poc_harness.metrics_stack import (
    ENV_FILE_VAR,
    PLAIN_INPUT,
    STACK_VAR,
    TOKEN_KEY,
    bearer,
    read_env_file,
)

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        not gate_is_on(STACK_VAR),
        reason=f"a PoC-8 stack drill: needs deploy/compose/poc08 up and {STACK_VAR}=1",
    ),
]


@dataclass
class Scenario:
    on_a: list[int] = field(default_factory=list)
    on_b: list[int] = field(default_factory=list)
    next_on_b: httpx.Response | None = None
    next_on_a: httpx.Response | None = None
    other_on_a: int = 0
    other_on_b: int = 0
    elapsed_s: float = 0.0
    keys: set[str] = field(default_factory=set)
    caller_bucket: str = ""


def _valkey(env_file: str, *args: str) -> str:
    done = subprocess.run(
        valkey_cli_argv(env_file, *args),
        capture_output=True,
        text=True,
        timeout=EXEC_TIMEOUT_S,
        check=False,
    )
    assert done.returncode == 0, (args[:1], done.stderr[-1000:])
    return done.stdout.strip()


def _post(client: httpx.Client, token: str) -> httpx.Response:
    return client.post("/v1/run", json={"input": {"text": PLAIN_INPUT}}, headers=bearer(token))


@pytest.fixture(scope="module")
def scenario() -> Scenario:
    env_file = os.environ.get(ENV_FILE_VAR, "")
    assert env_file, f'{ENV_FILE_VAR} is not set: export "$(stack.sh env)"'
    values = read_env_file(env_file)
    missing = [k for k in (TOKEN_KEY, OTHER_TOKEN_KEY) if not values.get(k)]
    assert not missing, f"the env file has no {missing}: run stack.sh up, which adds missing keys"
    caller, other = values[TOKEN_KEY], values[OTHER_TOKEN_KEY]
    keys = bucket_keys()
    _valkey(env_file, "DEL", *keys.values())
    seen = Scenario()
    started = time.monotonic()
    with (
        httpx.Client(base_url=CHASSIS_A, timeout=RUN_TIMEOUT_S) as a,
        httpx.Client(base_url=CHASSIS_B, timeout=RUN_TIMEOUT_S) as b,
    ):
        seen.on_a = [_post(a, caller).status_code for _ in range(SPEND_ON_A)]
        seen.on_b = [_post(b, caller).status_code for _ in range(SPEND_ON_B)]
        seen.next_on_b = _post(b, caller)
        seen.next_on_a = _post(a, caller)
        seen.elapsed_s = time.monotonic() - started
        seen.other_on_a = _post(a, other).status_code
        seen.other_on_b = _post(b, other).status_code
    seen.keys = set(_valkey(env_file, "--scan", "--pattern", f"ratelimit:{AGENT}:*").split())
    seen.caller_bucket = _valkey(env_file, "GET", keys[CALLER])
    print(
        f"on A {seen.on_a}, on B {seen.on_b}, next on B {seen.next_on_b.status_code} "
        f"(Retry-After {seen.next_on_b.headers.get('Retry-After')}), next on A "
        f"{seen.next_on_a.status_code}, other caller A {seen.other_on_a} B {seen.other_on_b}, "
        f"{len(seen.keys)} bucket keys, caller tokens left "
        f"{seen.caller_bucket.split(':', 1)[0]}, {seen.elapsed_s:.2f} s"
    )
    return seen


def _assert_rate_limited(response: httpx.Response | None) -> None:
    assert response is not None
    assert response.status_code == 429, (response.status_code, response.text[:300])
    assert "rate_limited" in response.text
    assert 1 <= int(response.headers["Retry-After"]) <= 60


def test_the_caller_spends_one_bucket_across_two_chassis(scenario: Scenario) -> None:
    assert scenario.elapsed_s < WINDOW_S, "the bucket may have refilled; rerun"
    assert scenario.on_a == [200] * SPEND_ON_A
    assert scenario.on_b == [200] * SPEND_ON_B
    # The paired control: B has served only SPEND_ON_B runs, below the burst. A bucket per
    # process would answer 200 here; one bucket in Valkey, already spent on A, answers 429.
    _assert_rate_limited(scenario.next_on_b)
    _assert_rate_limited(scenario.next_on_a)


def test_another_caller_still_runs_on_both_chassis(scenario: Scenario) -> None:
    assert (scenario.other_on_a, scenario.other_on_b) == (200, 200)


def test_valkey_holds_one_bucket_per_caller_not_one_per_chassis(scenario: Scenario) -> None:
    keys = bucket_keys()
    assert scenario.keys == {keys[CALLER], keys[OTHER]}
    tokens = float(scenario.caller_bucket.split(":", 1)[0])
    assert tokens < 1, f"the caller's bucket holds {tokens} tokens"
