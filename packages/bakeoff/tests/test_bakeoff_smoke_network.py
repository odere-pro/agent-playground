"""`bakeoff smoke` on real localhost processes. Needs sockets, so it is marked `network`."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from bakeoff.envs import build_env
from bakeoff.registry import ROOT

pytestmark = pytest.mark.network


def test_smoke_passes_for_echo_python_inprocess_and_sidecar(tmp_path: Path) -> None:
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "bakeoff",
            "smoke",
            "--engine",
            "echo-python",
            "--lane",
            "inprocess,sidecar",
        ],
        env=build_env(tmp_path),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    lines = [line.split() for line in done.stdout.splitlines()]
    assert lines == [["PASS", "echo-python", "inprocess"], ["PASS", "echo-python", "sidecar"]]


HOSTED_SCENARIO = """
import json, sys
from bakeoff.cli import main
from bakeoff.procs import free_port
from bakeoff.registry import BAKEOFF_SCRIPT, ROOT
from bakeoff.stack import FakeModel

fake = FakeModel(ROOT / BAKEOFF_SCRIPT, free_port())
fake.start()
try:
    rc = main([
        "run", "--engine", "echo-python", "--lane", "sidecar", "--tasks", "smoke", "--repeat", "1",
        "--out", sys.argv[1], "--route", "local-small",
        "--model-url", f"http://127.0.0.1:{fake.port}/v1", "--model-key-env", "BAKEOFF_TEST_KEY",
    ])
    seen = list(fake.app.state.calls)
    print(json.dumps({"rc": rc, "calls": len(seen), "models": sorted({c["model"] for c in seen})}))
finally:
    fake.stop()
"""


def test_hosted_mode_reaches_the_model_url_on_the_chosen_route(tmp_path: Path) -> None:
    """A fake model server stands in for LiteLLM: the chassis must call it, with that route.

    The whole scenario runs in a child process, because this test process may not open sockets.
    """
    done = subprocess.run(
        [sys.executable, "-c", HOSTED_SCENARIO, str(tmp_path / "out")],
        env=build_env(tmp_path, {"BAKEOFF_TEST_KEY": "hosted-test-value"}),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "hosted-test-value" not in done.stdout + done.stderr
    seen = json.loads(done.stdout.splitlines()[-1])
    assert seen["rc"] == 0
    assert seen["calls"] >= 1
    assert seen["models"] == ["local-small"]
    results = (tmp_path / "out" / "results.json").read_text()
    assert "hosted-test-value" not in results
    assert '"passed": 1' in results
    assert '"model_calls": []' in results
