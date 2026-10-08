"""The Compose scale stack, live (plan, sections 8a and 10). Every test here is a `stack` drill:
it needs Docker, the images of `deploy/compose/scale.sh build`, and POC04_STACK=1. Each test
brings up project `poc04` and takes it down again. Evidence of the recorded run:
`notes/2026-10-01-drills.md`.

    POC04_STACK=1 uv run pytest pocs/poc-04-stateless-scalable/tests/test_compose_scale.py -v
"""

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
    """Criterion 1 (smoke): one `POST /v1/run` through Traefik answers 200, and the same key
    again is a replay of the same envelope."""
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
    """Criteria 2 and 3: SIGKILL one of 2 pairs under retried load; no request is lost, and each
    retried key sent again returns the same envelope with `Idempotent-Replayed: true` from the
    surviving replica."""
    report = run_drill("kill", tmp_path)
    assert report["passed"] is True
    assert report["lost"] == 0
    retried = report["replay_retried"]
    assert isinstance(retried, dict)
    assert retried["same"] == retried["checked"]
    assert retried["not_marked_replayed"] == 0


def test_graceful_drill_fails_nothing_without_retry(tmp_path: Path) -> None:
    """Criterion 6: SIGTERM one of 2 chassis under load with no client retry; zero failures."""
    report = run_drill("graceful", tmp_path)
    assert report["passed"] is True
    load = report["load"]
    assert isinstance(load, dict)
    assert load["failed"] == 0
    assert load["retried"] == 0
