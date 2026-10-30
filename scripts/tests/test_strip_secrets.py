"""check_offline.sh must strip credential variables before pytest starts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow  # starts a nested pytest run

ROOT = Path(__file__).resolve().parents[2]

STRIPPED = (
    "OPENAI_API_KEY",
    "GITHUB_TOKEN",
    "APP_SECRET",
    "DB_PASSWORD",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LITELLM_MASTER_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "POC06_PROVIDER_KEY",
    "POC06_VIRTUAL_KEY",
    "POC06_LITELLM_KEY",
    "CHASSIS_S3_SECRET_KEY",
)
KEPT = "R0_06_PLAIN_SETTING"

PROBE = """
import os

def test_env_probe():
    leaked = [n for n in {names!r} if n in os.environ]
    assert leaked == [], leaked
    assert os.environ.get({kept!r}) == "kept"
"""


def _run_check_offline(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    probe = tmp_path / "test_env_probe.py"
    probe.write_text(PROBE.format(names=STRIPPED, kept=KEPT))
    env = {**os.environ, **dict.fromkeys(STRIPPED, "fake-value"), KEPT: "kept", "UV_NO_SYNC": "1"}
    return subprocess.run(
        [
            str(ROOT / "scripts" / "check_offline.sh"),
            "-q",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(tmp_path),
            str(probe),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_check_offline_strips_every_credential_suffix(tmp_path: Path) -> None:
    """check_offline.sh unsets every credential suffix and keeps other variables."""
    result = _run_check_offline(tmp_path)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
