"""The names and helpers of the 022 H-6 two-chassis rate-limit drill (`deploy/compose/poc08`).

Shared by the live drill (`test_poc08_stack_ratelimit.py`, `network`) and its offline checks
(`test_poc08_ratelimit_static.py`). Not a test file.
"""

from __future__ import annotations

from pathlib import Path

from chassis.server.rate_limit import bucket_key
from poc_harness.metrics_stack import STACK

DRILL = "test_poc08_stack_ratelimit.py"
CONFIG = "ratelimit.yaml"
AGENT = "echo-ratelimit"
"""The agent name in `chassis/ratelimit.yaml`: its own bucket, never spent by the other drills."""
CHASSIS_A = "http://127.0.0.1:18091"
CHASSIS_B = "http://127.0.0.1:18092"
SERVICES = {"chassis-rl-a": CHASSIS_A, "chassis-rl-b": CHASSIS_B}
CALLER = "poc08-ratelimit"
"""The subject of `POC08_RUN_TOKEN` on both chassis: the caller that spends its bucket."""
OTHER = "poc08-ratelimit-other"
"""The subject of `POC08_OTHER_TOKEN` on both chassis: the caller that must still get 200."""
OTHER_TOKEN_KEY = "POC08_OTHER_TOKEN"
SPEND_ON_A = 4
"""suggested: the caller's calls on chassis A. With B's they make the burst of ratelimit.yaml."""
SPEND_ON_B = 2
"""suggested: the caller's calls on chassis B. B refuses the next one, after only these two."""
WINDOW_S = 30.0
"""suggested: the longest the drill may take from the reset to the last counted call. At one
token a minute the bucket refills half a token in it, so no refill changes the count."""
RUN_TIMEOUT_S = 30.0
EXEC_TIMEOUT_S = 60.0


def bucket_keys() -> dict[str, str]:
    """The Valkey key of each caller's bucket for `AGENT`, as the chassis names it."""
    return {subject: bucket_key(AGENT, subject) for subject in (CALLER, OTHER)}


def valkey_cli_argv(env_file: str | Path, *args: str) -> list[str]:
    """`valkey-cli <args>` inside the stack's Valkey container. The password stays there: the
    container's own `VALKEY_PASSWORD` goes to `REDISCLI_AUTH`, as in the Valkey healthcheck."""
    return [
        "docker",
        "compose",
        "-p",
        "poc08",
        "--env-file",
        str(env_file),
        "-f",
        str(STACK / "docker-compose.yaml"),
        "exec",
        "-T",
        "valkey",
        "sh",
        "-c",
        'REDISCLI_AUTH="$VALKEY_PASSWORD" exec valkey-cli "$@"',
        "valkey-cli",
        *args,
    ]
