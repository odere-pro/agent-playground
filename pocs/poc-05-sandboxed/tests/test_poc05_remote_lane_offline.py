"""PoC-5 exit criteria 1 and 6, the offline part: the remote lane's own credential, driven in
process through the whole lane (`poc05_harness.remote_lane`), no cluster and no TCP.

- Criterion 1 (offline part): the checks below run in `make test` on every commit.
- Criterion 6: the remote pod reaches only the chassis's model and tool proxies, and only with
  its own credential. Ids from `notes/2026-10-02-threat-model.md`: H17 (the remote listener and
  the workload's A2A server refuse a caller without the token) and H13 (the chassis CLI's bind
  rules for its listeners; the public port's bind argument in the manifests is checked by the
  static manifest tests).

Every refusal sits next to its allowed control in the same test: the same call with the right
token, inside a run, answers. The workload is `echo_python`; the callers are the test itself.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
import yaml
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from chassis.server.cli import parse_args
from chassis.server.remote_auth import AUTH_FAILED
from poc05_harness import (
    REPLY,
    ROUTE,
    TOKEN_ENV,
    RemoteLane,
    Tokens,
    new_token,
    new_trace_id,
    remote_lane,
    traceparent,
)

CHAT = "/v1/chat/completions"


def _chat_body() -> dict[str, Any]:
    return {"model": ROUTE, "messages": [{"role": "user", "content": "probe"}]}


async def _chat(lane: RemoteLane, token: str | None, trace: str | None) -> tuple[int, Any]:
    """One model call to the remote listener, as a remote pod makes it."""
    headers = {} if trace is None else {"traceparent": trace}
    async with lane.remote_proxy_client(token) as client:
        response = await client.post(CHAT, json=_chat_body(), headers=headers)
    return response.status_code, response.json()


def _code(body: Any) -> str:
    return str(body.get("error", {}).get("code")) if isinstance(body, dict) else ""


async def test_h17_remote_listener_needs_its_own_token_and_a_run_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit criteria 6 and 1 (offline), H17: the chassis's remote listener answers 401 to a call
    with no token and with another token, and the same call with the remote's own token inside a
    run answers 200 (the allowed control). With the right token and no run in flight (a trace id
    no run has, or a run that has ended) it answers 403 `run_required`.
    """
    async with remote_lane(monkeypatch) as lane:
        own = lane.tokens.sends
        async with lane.held_run() as run:
            no_token = await _chat(lane, None, run.traceparent)
            other = await _chat(lane, new_token(), run.traceparent)
            allowed = await _chat(lane, own, run.traceparent)
            no_run = await _chat(lane, own, traceparent(new_trace_id()))
            no_traceparent = await _chat(lane, own, None)
        assert run.response is not None and run.response.json()["status"] == "ok"
        after = await _chat(lane, own, run.traceparent)
        failed = lane.counter_total(AUTH_FAILED)
    assert allowed[0] == 200, allowed
    assert allowed[1]["choices"][0]["message"]["content"] == REPLY
    assert no_token[0] == 401 and _code(no_token[1]) == "remote_unauthenticated", no_token
    assert other[0] == 401 and other[1] == no_token[1], other
    assert failed == 2, failed
    for refused in (no_run, no_traceparent, after):
        assert refused[0] == 403 and _code(refused[1]) == "run_required", refused


async def test_h17_workload_server_refuses_a_caller_without_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit criteria 6 and 1 (offline), H17: the workload's A2A server (`workload-a2a serve
    --require-token-env`) answers 401 on the agent card and on the JSON-RPC endpoint to a caller
    with no token or another token. The control: the same card fetch with the token answers 200,
    the chassis's `RemoteConnector` probes it, and a run through it ends `ok`.
    """
    async with remote_lane(monkeypatch) as lane:
        refused: list[int] = []
        for token in (None, new_token()):
            async with lane.workload_client(token) as client:
                refused.append((await client.get(AGENT_CARD_WELL_KNOWN_PATH)).status_code)
                refused.append((await client.post("/", json={"jsonrpc": "2.0"})).status_code)
        async with lane.workload_client(lane.tokens.workload) as client:
            card = await client.get(AGENT_CARD_WELL_KNOWN_PATH)
        probed = await lane.connector.probe()
        async with lane.client() as client:
            out = (await client.post("/v1/run", json={"input": {"text": "hi"}})).json()
    assert refused == [401, 401, 401, 401], refused
    assert card.status_code == 200 and card.json()["name"] == "echo_python:handle", card.text
    assert probed is True
    assert out["status"] == "ok" and out["output"]["text"] == REPLY, out


async def test_a_request_through_the_remote_lane_is_charged_to_its_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit criteria 6 and 1 (offline), H17 control: a normal request goes chassis -> A2A with
    the token -> `echo_python` -> the remote listener with the token -> the model port, and its
    events come back. The model call is charged to the run: its `chassis.model.call` span is
    correlated to the run's request id and trace id, and no call is uncorrelated or refused.
    """
    trace_id = new_trace_id()
    body = {"trace_id": trace_id, "input": {"text": "simplify: the quick brown fox"}}
    async with remote_lane(monkeypatch) as lane, lane.client() as client:
        out = (await client.post("/v1/run", json=body)).json()
        uncorrelated = lane.counter_total("chassis.model_calls_uncorrelated")
        refused = lane.counter_total(AUTH_FAILED) + lane.counter_total(
            "chassis.remote.run_required"
        )
        spans = [s for s in lane.telemetry.spans if s.name == "chassis.model.call"]
        calls = len(lane.model.calls)
    assert out["status"] == "ok" and out["output"]["text"] == REPLY, out
    assert calls == 1, calls
    assert len(spans) == 1, spans
    attributes = spans[0].attributes
    assert attributes["correlated"] is True, attributes
    assert attributes["request_id"] == out["request_id"], (attributes, out["request_id"])
    assert attributes["trace_id"] == trace_id, attributes
    assert uncorrelated == 0 and refused == 0, (uncorrelated, refused)


def _leaks(tokens: set[str], texts: list[str]) -> list[str]:
    return [text[:200] for text in texts if any(t in text for t in tokens)]


async def test_the_token_is_in_no_log_span_or_event(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Exit criteria 6 and 1 (offline), H17: during a run through the remote lane (complete and
    streamed), and refused calls on both sides, no token value appears in any log record at
    DEBUG, any span name or attribute, any counter label, the telemetry fake's logs, the events,
    the envelope, or the connector's `repr`. The control: the capture did see the run (the
    model span, the refusal log, the streamed events), so an empty scan is not an empty capture.
    """
    caplog.set_level(logging.DEBUG)
    async with remote_lane(monkeypatch) as lane:
        tokens = lane.tokens.values()
        async with lane.client() as client:
            complete = await client.post("/v1/run", json={"input": {"text": "hi"}})
            streamed = await client.post("/v1/run", json={"input": {"text": "hi"}, "stream": True})
        async with lane.held_run() as run:
            await _chat(lane, new_token(), run.traceparent)
            await _chat(lane, lane.tokens.sends, run.traceparent)
        async with lane.workload_client(new_token()) as other:
            await other.get(AGENT_CARD_WELL_KNOWN_PATH)
        texts = [complete.text, streamed.text, repr(lane.connector)]
        texts += [run.response.text] if run.response is not None else []
        texts += [repr((s.name, s.attributes)) for s in lane.telemetry.spans]
        texts += [repr(key) for key in lane.telemetry.counters]
        texts += [json.dumps(entry, default=str) for entry in lane.telemetry.logs]
        span_names = {s.name for s in lane.telemetry.spans}
    for record in caplog.records:
        texts.append(record.getMessage())
        texts.append(repr(record.__dict__))
    assert "event: delta" in streamed.text and complete.json()["status"] == "ok"
    assert "chassis.model.call" in span_names, span_names
    assert any("remote proxy refused a call" in r.getMessage() for r in caplog.records)
    assert len(caplog.records) > 1
    assert _leaks(tokens, [f"x {sorted(tokens)[0]} x"]), "the scan cannot find a token"
    assert _leaks(tokens, texts) == []


ROTATIONS = {
    # The chassis swapped first: it sends the new token, the workload still sends the old one.
    "chassis-first": ("new", "old", "old", "new", "old"),
    # The workload swapped first: it sends the new token, the chassis still sends the old one.
    "workload-first": ("old", "new", "new", "old", "new"),
}
"""(chassis current, chassis previous, workload current, workload previous, workload sends)."""


@pytest.mark.parametrize("order", list(ROTATIONS))
async def test_h17_a_rotation_accepts_the_previous_token_on_both_sides(
    order: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit criteria 6 and 1 (offline), H17 during a token rotation (plan section 2.2): with one
    side swapped and the other not, a run through the lane ends `ok`; the remote listener (in a
    run) and the workload's server each accept both the old and the new token (the controls),
    and each refuses a third token with 401.
    """
    values = {"old": new_token(), "new": new_token()}
    chassis, chassis_prev, workload, workload_prev, sends = (values[k] for k in ROTATIONS[order])
    tokens = Tokens(
        chassis=chassis,
        chassis_previous=chassis_prev,
        workload=workload,
        workload_previous=workload_prev,
        sends=sends,
    )
    third = new_token()
    listener: dict[str, int] = {}
    server: dict[str, int] = {}
    async with remote_lane(monkeypatch, tokens=tokens) as lane:
        async with lane.held_run() as run:
            for name, token in (*values.items(), ("third", third)):
                listener[name] = (await _chat(lane, token, run.traceparent))[0]
        for name, token in (*values.items(), ("third", third)):
            async with lane.workload_client(token) as client:
                server[name] = (await client.get(AGENT_CARD_WELL_KNOWN_PATH)).status_code
    assert run.response is not None and run.response.json()["status"] == "ok", run.response
    assert listener == {"old": 200, "new": 200, "third": 401}, listener
    assert server == {"old": 200, "new": 200, "third": 401}, server


# --- H13: the chassis CLI's bind rules, through its argument checking ---


def _remote_config(tmp_path: Path) -> str:
    path = tmp_path / "remote.yaml"
    config = {
        "profile": "fake",
        "agent": {"name": "simplifier", "version": "0.0.1"},
        "spec": {
            "trust": "untrusted",
            "engine": {
                "connector": "remote",
                "url": "http://echo-remote:9000",
                "auth": {"scheme": "bearer", "token_env": TOKEN_ENV},
            },
        },
    }
    path.write_text(yaml.safe_dump(config))
    return str(path)


POD_IP = "10.244.0.7"
"""suggested: a pod IP in kind's default pod range."""


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "127.0.0.1", "::1", "localhost"])
def test_h13_cli_refuses_a_wildcard_or_loopback_remote_proxy_host(
    host: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit criteria 6 and 1 (offline), H13: `chassis serve --remote-proxy-host` refuses a
    wildcard (`0.0.0.0`, `::`) and loopback (`127.0.0.1`, `::1`, `localhost`) with status 2; the
    control, the pod IP with the same config and token, is accepted. Argument checking only;
    nothing binds.
    """
    monkeypatch.setenv(TOKEN_ENV, new_token())
    config = _remote_config(tmp_path)
    base = ["serve", "--config", config]
    allowed = parse_args([*base, "--remote-proxy-host", POD_IP])
    with pytest.raises(SystemExit) as refused:
        parse_args([*base, "--remote-proxy-host", host])
    assert allowed.remote_proxy_host == POD_IP
    assert refused.value.code == 2
    assert "--remote-proxy-host" in capsys.readouterr().err


def test_h13_cli_refuses_a_proxy_host_off_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit criteria 6 and 1 (offline), H13: the loopback proxy listener (`--proxy-host`, no
    auth) refuses `0.0.0.0` and the pod IP with status 2; the control, `127.0.0.1`, is accepted.
    The remote lane's listener also needs its token variable: unset, the pod IP is refused.
    """
    monkeypatch.setenv(TOKEN_ENV, new_token())
    config = _remote_config(tmp_path)
    base = ["serve", "--config", config]
    allowed = parse_args([*base, "--proxy-host", "127.0.0.1", "--remote-proxy-host", POD_IP])
    codes: list[object] = []
    for host in ("0.0.0.0", POD_IP):
        with pytest.raises(SystemExit) as refused:
            parse_args([*base, "--proxy-host", host])
        codes.append(refused.value.code)
    monkeypatch.delenv(TOKEN_ENV)
    with pytest.raises(SystemExit) as no_token:
        parse_args([*base, "--remote-proxy-host", POD_IP])
    assert allowed.proxy_host == "127.0.0.1" and allowed.remote_proxy_host == POD_IP
    assert codes == [2, 2], codes
    assert no_token.value.code == 2
    assert TOKEN_ENV in capsys.readouterr().err
