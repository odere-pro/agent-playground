"""PoC-5 on kind, exit criterion 2: the `remote` lane gives the same events and the same envelope as
the `sidecar` lane for the same requests (T22; plan sections 2.1 to 2.4; threat model H17, H28).

Offline, `test_poc05_remote_lane_contract.py` binds `chassis_contracts.lane.LaneContract` over
`inprocess`, `sidecar`, and `remote` with the suite's own case handles. On kind the pods run one
fixed handle, echo-python, so the case handles cannot be loaded there. This file sends the same
requests to both deployed lanes instead and compares what comes back with the suite's own
`assert_same`:

- `sidecar`: `agent-echo`, the chassis and echo-python in one pod (runc).
- `remote`: `chassis-echo-remote`, whose workload is the Sandbox `remote-echo` on gVisor. Its
  model and tool calls go back through the chassis's remote listener on 8091 with the remote's
  own token (H17's allowed control) inside the run (`run_required`).

Each request goes to `POST /v1/run` with `stream: true`, from inside the chassis container to its
pod IP (the public port binds the pod IP only), with the image's own Python. The texts drive the
fake model's script: the default reply, a tool call through the MCP gateway, a reply with usage,
and a scripted model failure. Per-run values are masked: the request id, the tool call id, the
trace id and idempotency key, and the names that differ per deployment (agent name, config hash).
Every event is also parsed with `chassis.core.events.parse_event`.

A kind test: marked `network`, skipped unless `POC05_KIND=1` (`poc05_conftest.py`). Run:
`POC05_KIND=1 uv run pytest -m network <this file>`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml
from chassis.core.events import parse_event
from chassis_contracts.lane import assert_same
from poc05_kind import AGENTS_NS, CHASSIS, REMOTE_NS, get_json, kubectl, pod

SIDECAR = "agent-echo"
REMOTE = "chassis-echo-remote"
REMOTE_WORKLOAD = "remote-echo"
# The fake model's script (packages/fake-model-server/scripts/example.yaml): text in, path run.
TEXTS = {
    "hello": ("start", "delta", "metrics", "end"),
    "glossary": ("start", "tool_call", *["delta"] * 8, "metrics", "end"),
    "simplify": ("start", *["delta"] * 6, "metrics", "end"),
    "fail": ("start", "error"),
}
MASK = "<per run>"
PER_DEPLOYMENT = ("request_id", "trace_id", "idempotency_key", "agent", "agent_version")

# POST /v1/run with stream: true for each text; one JSON line out per text: the status, every
# SSE frame's event name and data. Nothing secret is in the command or the output.
STREAM_PY = r"""
import json, os, sys, urllib.error, urllib.request
out = []
for text in json.loads(sys.argv[1]):
    body = json.dumps({"input": {"text": text}, "stream": True}).encode()
    url = "http://%s:8080/v1/run" % os.environ["POD_IP"]
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            code, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read()
    frames = []
    for block in raw.decode(errors="replace").split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.splitlines() if ": " in l)
        if "event" in lines:
            frames.append([lines["event"], json.loads(lines.get("data", "null"))])
    out.append({"status": code, "frames": frames})
print(json.dumps(out))
"""


def run_texts(app: str) -> dict[str, dict[str, Any]]:
    got = kubectl(
        "exec", "-n", AGENTS_NS, f"deploy/{app}", "-c", CHASSIS, "--",
        "python", "-c", STREAM_PY, json.dumps(list(TEXTS)),
    )  # fmt: skip
    assert got.returncode == 0, f"exec in {app} failed: {got.stderr[-400:]}"
    results: list[dict[str, Any]] = json.loads(got.stdout.strip().splitlines()[-1])
    return dict(zip(TEXTS, results, strict=True))


def masked_events(frames: list[list[Any]]) -> list[dict[str, Any]]:
    events = []
    for name, data in frames:
        if name == "response":
            continue
        dump = parse_event(data).model_dump()
        assert dump["type"] == name, (name, dump)
        for key in ("request_id", "call_id"):
            if key in dump:
                dump[key] = MASK
        events.append(dump)
    return events


def masked_envelope(frames: list[list[Any]]) -> dict[str, Any]:
    (envelope,) = [data for name, data in frames if name == "response"]
    out: dict[str, Any] = {k: (MASK if k in PER_DEPLOYMENT else v) for k, v in envelope.items()}
    out["versions"] = {**out["versions"], "config": MASK}
    for call in (out.get("output") or {}).get("tool_calls") or []:
        call["call_id"] = MASK
    return out


def chassis_config(app: str) -> dict[str, Any]:
    deployment = get_json("deployment", app, "-n", AGENTS_NS)
    volumes = deployment["spec"]["template"]["spec"]["volumes"]
    (name,) = [v["configMap"]["name"] for v in volumes if "configMap" in v]
    data = get_json("configmap", name, "-n", AGENTS_NS)["data"]
    config: dict[str, Any] = yaml.safe_load(data["config.yaml"])
    return config


@pytest.fixture(scope="module")
def lanes() -> dict[str, dict[str, dict[str, Any]]]:
    return {"sidecar": run_texts(SIDECAR), "remote": run_texts(REMOTE)}


def test_the_two_deployments_are_the_two_lanes() -> None:
    """Criterion 2 precondition: `agent-echo` runs the `sidecar` connector and
    `chassis-echo-remote` the `remote` connector, whose workload pod runs on gVisor (H28) and
    holds no chassis; otherwise the comparison below would compare a lane with itself."""
    sidecar, remote = chassis_config(SIDECAR), chassis_config(REMOTE)
    assert sidecar["spec"]["engine"].get("connector", "sidecar") == "sidecar", sidecar["spec"]
    assert remote["spec"]["engine"]["connector"] == "remote", remote["spec"]
    assert "remote-echo" in remote["spec"]["engine"]["url"], remote["spec"]["engine"]

    workload = pod(REMOTE_NS, REMOTE_WORKLOAD)
    assert workload["spec"]["runtimeClassName"] == "gvisor"
    assert [c["name"] for c in workload["spec"]["containers"]] == ["workload"]
    sidecar_pod = pod(AGENTS_NS, SIDECAR)
    assert sidecar_pod["spec"].get("runtimeClassName") is None


@pytest.mark.parametrize("text", list(TEXTS))
def test_remote_gives_the_sidecar_events_for_the_same_request(
    lanes: dict[str, dict[str, dict[str, Any]]], text: str
) -> None:
    """Criterion 2: the same request to the `sidecar` and the `remote` lane gives the same event
    list, type for type and field for field (per-run ids masked), and the same envelope. Each
    stream also has the event types the fake model's script makes, so two equal but empty or
    failed streams do not pass."""
    sidecar, remote = lanes["sidecar"][text], lanes["remote"][text]
    assert sidecar["status"] == remote["status"] == 200, (sidecar["status"], remote["status"])

    sidecar_events, remote_events = (
        masked_events(sidecar["frames"]),
        masked_events(remote["frames"]),
    )
    assert tuple(e["type"] for e in remote_events) == TEXTS[text], remote_events
    assert_same(sidecar_events, remote_events, f"{text}: sidecar vs remote events")
    assert_same(
        masked_envelope(sidecar["frames"]),
        masked_envelope(remote["frames"]),
        f"{text}: sidecar vs remote envelope",
    )
