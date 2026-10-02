"""PoC-5 public bind, offline (exit criterion 1, H13; security review 2026-10-02, item 10).

The chassis's public port binds the pod IP; the manifests set `--host`, and the CLI has no rule
for it. `chassis serve --host 0.0.0.0` parses today, so the public app would also answer on
loopback, beside the proxies. The xfail below is strict: it turns into a failure the day the CLI
refuses a wildcard public host, so the marker must go then. The paired control: a specific
host still parses.
"""

from __future__ import annotations

import pytest
from chassis.server.cli import parse_args

_BASE = ["serve", "--config", "chassis.yaml"]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="026 CH-4: the CLI does not refuse a wildcard public --host yet (security review 10)",
)
def test_poc05_serve_refuses_a_wildcard_public_host() -> None:
    try:
        parse_args([*_BASE, "--host", "0.0.0.0"])
    except SystemExit:
        return
    raise AssertionError("chassis serve accepted --host 0.0.0.0")


def test_poc05_serve_accepts_a_specific_public_host() -> None:
    """Control: a pod IP style host parses."""
    args = parse_args([*_BASE, "--host", "10.244.0.7"])
    assert args.host == "10.244.0.7"
