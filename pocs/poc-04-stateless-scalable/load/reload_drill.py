"""Config reload drill on the Compose scale stack (exit criterion 7, live; plan, section 3).

Needs `deploy/compose/scale.sh up <engine> <pairs>` first. Steps:
1. A native run with `budget.max_tokens: 5000` answers 200 (default limit 8000); note the config
   version a run reports (`versions.config`, `<version>+<hash12>` or `<hash12>`).
2. Upload the seed config with `spec.limits.max_tokens_max: 4000` to MinIO; within a few polls the
   same run is refused (400 `limit_exceeded`) and a run within budget reports a new version.
3. Upload an invalid document; the chassis logs `config refused, keeping version=...` and the
   4000 limit and the version stay.
4. No chassis restarted (`RestartCount` 0, `StartedAt` unchanged).

`mc` runs in a throwaway container on network `poc04`; the MinIO root password is read from
`deploy/compose/.env.poc04` by Docker and never printed. Exit 1 on any failed step.

    uv run python pocs/poc-04-stateless-scalable/load/reload_drill.py
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
SEED = ROOT / "packages" / "chassis" / "configs" / "scale.yaml"
SECRETS = ROOT / "deploy" / "compose" / ".env.poc04"
MC_IMAGE = (
    "pgsty/mc:RELEASE.2026-09-16T00-00-00Z"
    "@sha256:cfc83108c3abb371f8fb84d99c1fdc88f8c237e022409b0081fb7c0a3be634dd"
)
URL = "http://127.0.0.1:18080"
# suggested: the chassis polls every 5 s; wait up to 4 polls.
WAIT_S = 20.0


def upload(text: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "echo.yaml").write_text(text)
        script = (
            'export MC_HOST_local="http://poc04-admin:${MINIO_ROOT_PASSWORD}@minio:9000"; '
            "mc cp -q /drill/echo.yaml local/agent-configs/agents/echo.yaml >/dev/null"
        )
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "poc04",
                "--env-file",
                str(SECRETS),
                "-e",
                "MC_CONFIG_DIR=/tmp/mc",
                "-v",
                f"{tmp}:/drill:ro",
                "--entrypoint",
                "sh",
                MC_IMAGE,
                "-c",
                script,
            ],
            check=True,
            timeout=60,
        )


def run(client: httpx.Client, max_tokens: int) -> tuple[int, str, str]:
    """(status, config version or '', error message or '')."""
    body = {"input": {"text": "simplify: hi"}, "budget": {"max_tokens": max_tokens}}
    response = client.post("/v1/run", json=body)
    data = response.json()
    version = str((data.get("versions") or {}).get("config") or "")
    message = "" if response.status_code == 200 else response.text[:200]
    return response.status_code, version, message


def chassis_names() -> list[str]:
    out = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "label=com.docker.compose.project=poc04",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return sorted(n for n in out if n.startswith("poc04-chassis-"))


def started(names: list[str]) -> dict[str, str]:
    fmt = "{{.State.StartedAt}} {{.RestartCount}}"
    return {
        n: subprocess.run(
            ["docker", "inspect", "-f", fmt, n], capture_output=True, text=True, check=True
        ).stdout.strip()
        for n in names
    }


def until(client: httpx.Client, check: object, what: str) -> tuple[int, str, str]:
    deadline = time.monotonic() + WAIT_S
    while True:
        got = run(client, 5000)
        if callable(check) and check(got):
            return got
        if time.monotonic() > deadline:
            print(f"FAIL: {what} not seen in {WAIT_S:.0f} s; last {got}")
            raise SystemExit(1)
        time.sleep(1)


def main() -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    names = chassis_names()
    before = started(names)
    seed = SEED.read_text()
    good = seed + "  limits:\n    max_tokens_max: 4000\n"
    bad = seed + "  limits:\n    max_tokens_max: -1\n    not_a_field: true\n"
    ok = True
    with httpx.Client(base_url=URL, timeout=30) as client:
        status, seed_version, _ = run(client, 5000)
        print(f"seed:      max_tokens 5000 -> {status}, config {seed_version}")
        ok &= status == 200
        upload(good)
        print("uploaded:  spec.limits.max_tokens_max 4000")
        status, _, message = until(client, lambda g: g[0] == 400, "the 4000 limit")
        print(f"after good: max_tokens 5000 -> {status}: {message}")
        _, good_version, _ = run(client, 100)
        print(f"after good: max_tokens 100 -> config {good_version}")
        ok &= bool(good_version) and good_version != seed_version
        upload(bad)
        print("uploaded:  max_tokens_max -1 and an unknown field")
        time.sleep(WAIT_S / 2)
        status, _, message = run(client, 5000)
        _, kept_version, _ = run(client, 100)
        print(f"after bad: max_tokens 5000 -> {status}: {message}")
        print(f"after bad: max_tokens 100 -> config {kept_version}")
        ok &= status == 400 and kept_version == good_version
    for name in names:
        lines = subprocess.run(["docker", "logs", name], capture_output=True, text=True).stderr
        refused = [ln for ln in lines.splitlines() if "config refused" in ln]
        print(f"{name}: {refused[-1] if refused else 'no config refused line'}")
        ok &= bool(refused)
    after = started(names)
    for name in names:
        print(f"{name}: started/restarts {before[name]} -> {after[name]}")
    ok &= after == before and all(v.endswith(" 0") for v in after.values())
    upload(seed)
    print("restored:  the seed document")
    print("reload drill:", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
