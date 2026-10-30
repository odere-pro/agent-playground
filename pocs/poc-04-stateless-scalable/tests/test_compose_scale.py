"""The Compose scale stack, live (plan, sections 8a and 10). Every test here is a `stack` drill:
it needs Docker, the images of `deploy/compose/scale.sh build`, and POC04_STACK=1. Each test
brings up project `poc04` and takes it down again. Evidence of the recorded run:
`notes/2026-10-01-drills.md`.

The README's exit criteria the drills here give evidence for:

- 2: "A repeated call with the same key returns the same result on any replica."
- 3: "A killed replica loses no request that the client retries."
- 6: "Stopping a pair under load fails no request in flight: the workload finishes its `handle` calls before it exits."

    POC04_STACK=1 uv run pytest pocs/poc-04-stateless-scalable/tests/test_compose_scale.py -v
"""  # noqa: E501

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
SCALE = ROOT / "deploy" / "compose" / "scale.sh"
DRILL = ROOT / "pocs" / "poc-04-stateless-scalable" / "load" / "kill_drill.py"
URL = "http://127.0.0.1:18080"
NETWORK = "poc04"
"""The project network's real name (`networks.default.name` in `docker-compose.scale.yaml`)."""
CHASSIS_1 = "poc04-chassis-1-1"
"""Compose's container name for service `chassis-1` in project `poc04`."""
EGRESS_PROBE = """
import json, socket
def tcp(host, port):
    with socket.create_connection((host, port), timeout=3):
        return "reached"
def probe(fn, *args):
    try:
        return fn(*args)
    except OSError as exc:
        return "refused: " + type(exc).__name__
print(json.dumps({
    "internet": probe(tcp, "1.1.1.1", 443),
    "dns": probe(lambda: socket.gethostbyname("example.com") and "reached"),
    "host": probe(tcp, "host.docker.internal", 80),
}))
"""
"""Run in chassis-1: each way out prints `reached` or the error that stopped it."""

pytestmark = [pytest.mark.stack, pytest.mark.network]


def scale(*args: str, timeout_s: float = 400) -> None:
    subprocess.run([str(SCALE), *args], check=True, timeout=timeout_s, capture_output=True)


@pytest.fixture
def one_pair() -> Iterator[None]:
    scale("up", "echo-python", "1")
    try:
        yield
    finally:
        scale("down")


def wait_ready(client: httpx.Client, timeout_s: float = 60) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if client.get("/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    pytest.fail(f"{URL}/ready not 200 in {timeout_s} s")


@pytest.mark.usefixtures("one_pair")
def test_smoke_one_run_through_traefik_and_its_replay() -> None:
    """Exit criterion 2, on one pair through Traefik (the smoke run): one `POST /v1/run` answers
    200, and the same key again is a replay of the same envelope."""
    with httpx.Client(base_url=URL, timeout=30) as client:
        wait_ready(client)
        key = str(uuid.uuid4())
        body = {"input": {"text": "simplify: hello world"}}
        first = client.post("/v1/run", json=body, headers={"Idempotency-Key": key})
        again = client.post("/v1/run", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "ok"
    assert again.status_code == 200, again.text
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert again.json()["request_id"] == first.json()["request_id"]


def run_drill(mode: str, tmp_path: Path) -> dict[str, object]:
    out = tmp_path / f"drill-{mode}.json"
    done = subprocess.run(
        [
            sys.executable,
            str(DRILL),
            "--mode",
            mode,
            "--pair",
            "2",
            "--up",
            "echo-python",
            "--pairs",
            "2",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert out.exists(), done.stdout[-2000:] + done.stderr[-2000:]
    report: dict[str, object] = json.loads(out.read_text())
    assert done.returncode == 0, json.dumps(report, indent=2)
    return report


def test_kill_drill_loses_nothing_and_replays_retried_keys(tmp_path: Path) -> None:
    """Exit criteria 2 and 3: SIGKILL one of 2 pairs under retried load; no request is lost, and
    each retried key sent again returns the same envelope with `Idempotent-Replayed: true` from
    the surviving replica."""
    report = run_drill("kill", tmp_path)
    assert report["passed"] is True
    assert report["lost"] == 0
    retried = report["replay_retried"]
    assert isinstance(retried, dict)
    assert retried["same"] == retried["checked"]
    assert retried["not_marked_replayed"] == 0


def test_graceful_drill_fails_nothing_without_retry(tmp_path: Path) -> None:
    """Exit criterion 6, live: SIGTERM one of 2 chassis under load, no client retry; none fail."""
    report = run_drill("graceful", tmp_path)
    assert report["passed"] is True
    load = report["load"]
    assert isinstance(load, dict)
    assert load["failed"] == 0
    assert load["retried"] == 0


def docker(*args: str) -> str:
    done = subprocess.run(["docker", *args], check=True, capture_output=True, text=True, timeout=60)
    return done.stdout.strip()


@pytest.mark.usefixtures("one_pair")
def test_chassis_reaches_nothing_outside_when_poc04_is_internal() -> None:
    """`poc04` is internal: chassis-1 reaches no internet, DNS, or host; Traefik still answers."""
    # Arrange
    with httpx.Client(base_url=URL, timeout=30) as client:
        wait_ready(client)

        # Act
        internal = docker("network", "inspect", NETWORK, "-f", "{{.Internal}}")
        probes = json.loads(docker("exec", CHASSIS_1, "python", "-c", EGRESS_PROBE))
        ready = client.get("/ready").status_code

    # Assert
    assert internal == "true"
    assert {k: v.startswith("refused: ") for k, v in probes.items()} == {
        "internet": True,
        "dns": True,
        "host": True,
    }, probes
    assert ready == 200
