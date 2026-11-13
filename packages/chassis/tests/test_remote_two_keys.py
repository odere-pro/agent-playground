"""055 CH-6: the workload token Secret holds two keys, one per direction, by default.

- chassis -> workload: `spec.engine.auth.token_env`. The connector presents it on every A2A
  request; the workload's server (`--require-token-env`) requires it.
- workload -> chassis: `spec.engine.auth.proxy_token_env`. The workload sends it on every model
  and MCP call; the chassis's remote listener accepts only it.

So a remote that echoes or replays the key it was sent cannot call the listener, and the key the
workload sends cannot call the workload. One key both ways is refused at start with a clear
message (no fall back), and so is the same key value under two names.

The whole lane runs in one process over Unix sockets (`poc_harness.remote_lane`), no TCP, with
random per-test tokens.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from chassis.server import cli
from chassis.server.config import EngineAuthSpec
from chassis.server.remote_auth import remote_tokens
from poc_harness.lane_tokens import new_token
from poc_harness.remote_lane import REPLY, RemoteLane, remote_lane
from poc_harness.scenario import ROUTE
from pydantic import ValidationError

BODY: dict[str, Any] = {
    # The run's own route: any other is 403 `model_route_denied` (012 H-3).
    "model": ROUTE,
    "messages": [{"role": "user", "content": "simplify: x"}],
    "max_tokens": 16,
}
ONE_WAY = {"scheme": "bearer", "token_env": "REMOTE_TOKEN"}
TWO_WAY = {**ONE_WAY, "proxy_token_env": "REMOTE_PROXY_TOKEN"}


async def _listener(lane: RemoteLane, token: str, trace: str) -> int:
    async with lane.remote_proxy_client(token) as client:
        response = await client.post(
            "/v1/chat/completions", json=BODY, headers=lane.run_headers(trace)
        )
    return response.status_code


async def _workload(lane: RemoteLane, token: str) -> int:
    async with lane.workload_client(token) as client:
        return (await client.get(AGENT_CARD_WELL_KNOWN_PATH)).status_code


async def test_the_chassis_presents_one_key_and_its_listener_accepts_only_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lane's default tokens are two distinct keys. The workload's server takes only the
    chassis-to-workload key and a run ends `ok`, so the chassis presented that key. Inside the
    same run the listener refuses that key with 401 `remote_unauthenticated` (the replay) and
    takes the workload-to-chassis key (the control); the workload's server refuses the
    workload-to-chassis key. Each key is refused in the other direction."""
    async with remote_lane(monkeypatch) as lane:
        to_workload, to_chassis = lane.tokens.chassis, lane.tokens.proxy
        assert to_chassis is not None
        async with lane.held_run() as run:
            replayed = await _listener(lane, to_workload, run.traceparent)
            own = await _listener(lane, to_chassis, run.traceparent)
        server_own = await _workload(lane, to_workload)
        server_other = await _workload(lane, to_chassis)
    assert to_workload != to_chassis
    assert lane.tokens.workload == to_workload and lane.tokens.sends == to_chassis
    assert run.response is not None, "the run never answered"
    out = run.response.json()
    assert out["status"] == "ok" and out["output"]["text"] == REPLY, out
    assert replayed == 401, "the chassis-to-workload key was let in on the listener"
    assert own == 200
    assert (server_own, server_other) == (200, 401)


def test_one_key_both_ways_fails_start_with_a_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No `proxy_token_env`: the listener does not fall back to `token_env`. `remote_tokens`
    raises, and `chassis serve --remote-proxy-host` exits 2 with the reason, naming no value."""
    token = new_token()
    monkeypatch.setenv("REMOTE_TOKEN", token)
    with pytest.raises(LookupError, match="proxy_token_env") as exc:
        remote_tokens(ONE_WAY)
    assert "one key per direction" in str(exc.value) and token not in str(exc.value)
    config = tmp_path / "remote.yaml"
    document = {
        "profile": "fake",
        "agent": {"name": "simplifier", "version": "0.0.1"},
        "spec": {
            "trust": "untrusted",
            "engine": {"connector": "remote", "url": "http://echo-remote:9000", "auth": ONE_WAY},
        },
    }
    config.write_text(yaml.safe_dump(document))
    argv = ["serve", "--config", str(config), "--remote-proxy-host", "10.0.0.5"]
    with pytest.raises(SystemExit) as refused:
        cli.parse_args(argv)
    err = capsys.readouterr().err
    assert refused.value.code == 2
    assert "proxy_token_env" in err and "one key per direction" in err and token not in err


def test_the_same_key_in_both_directions_fails_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two names but one value is one key both ways: refused, naming the variables only. One
    variable named for both directions is refused when the config loads."""
    token = new_token()
    monkeypatch.setenv("REMOTE_TOKEN", token)
    monkeypatch.setenv("REMOTE_PROXY_TOKEN", token)
    with pytest.raises(ValueError, match="REMOTE_PROXY_TOKEN") as exc:
        remote_tokens(TWO_WAY)
    assert "REMOTE_TOKEN" in str(exc.value) and token not in str(exc.value)
    monkeypatch.setenv("REMOTE_PROXY_TOKEN", new_token())
    assert remote_tokens(TWO_WAY) != (token,)
    outbound = {**TWO_WAY, "previous_token_env": "REMOTE_OLD"}
    for shared in ("REMOTE_TOKEN", "REMOTE_OLD"):
        with pytest.raises(ValidationError, match="one key per direction"):
            EngineAuthSpec.model_validate({**outbound, "proxy_token_env": shared})
