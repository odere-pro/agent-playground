"""014 H-7, the live sampling box: at `TRACE_SAMPLE_RATE=0` the OTel Collector keeps every trace
with an error span and drops the rest (`tail_sampling`; decided by the user, 2026-11-13).

Starts the PoC-7 stack (`deploy/compose/poc07/docker-compose.yaml`: Langfuse v4, the Collector,
Jaeger) fresh under a Compose project of its own, with the percent made by the one conversion
that `stack.sh` runs (`deploy/compose/trace_sample_percent.py`). A chassis in this process (the
`fake` profile, the `otel` telemetry adapter, the `inprocess` lane) exports to the Collector with
its bearer token and sends three runs, one after the other:

- one that ends `end` (`echo_wire`): its trace must never reach Jaeger;
- one that ends `error` at once (`chassis_contracts.lane:error_handle`): found in Jaeger;
- one that spends the whole default 30 s `timeout_ms` and then ends `error` (`a2a.timeout`):
  found in Jaeger, since `decision_wait` (45 s) outlasts it.

The ok run goes first, so its sampling decision is made before either error run. The absence
check runs only after both error traces are found, plus a margin: the error traces are the
control that the path from the chassis to Jaeger works.

`network`; skipped unless `TRACE_SAMPLING_LIVE=1`. Needs Docker, and 127.0.0.1 ports 3000, 4318,
13133, and 16686 free. By hand:

    TRACE_SAMPLING_LIVE=1 uv run pytest -m network -v -s \\
        pocs/poc-07-cross-cutting-decisions/tests/test_poc07_sampling_live.py

The fixture tears the stack down (`down -v`, this project only) at the end. The credentials are
made into a temporary env file (mode 600) and never printed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from chassis.core.events import End, Event, Start
from chassis.server import ChassisConfig, create_app

pytestmark = [pytest.mark.network, pytest.mark.requires_env("TRACE_SAMPLING_LIVE")]

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_DIR = ROOT / "deploy/compose"
COMPOSE_FILE = COMPOSE_DIR / "poc07/docker-compose.yaml"
CONVERTER = COMPOSE_DIR / "trace_sample_percent.py"
PROJECT = "poc07-h7-sampling"
"""suggested: a Compose project of its own, so no other stack is touched."""
RATE = "0"
JAEGER = "http://127.0.0.1:16686"
COLLECTOR = "http://127.0.0.1:4318"
COLLECTOR_HEALTH = "http://127.0.0.1:13133/"
READY_URLS = ("http://127.0.0.1:3000/api/public/health", f"{JAEGER}/", COLLECTOR_HEALTH)
READY_WAIT_S = float(os.environ.get("POC07_WAIT_S", "600"))
SPAN_WAIT_S = float(os.environ.get("POC07_SPAN_WAIT_S", "180"))
"""suggested: how long a read waits for a kept trace (`decision_wait` 45 s, then the export)."""
ABSENT_MARGIN_S = 20.0
"""suggested: how long past the last kept trace the ok trace must still be absent."""
POLL_S = 2.0
SLOW_S = 60.0
"""How long `slow_handle` sleeps after `start`: well past the default 30 s run budget."""
DEFAULT_TIMEOUT_S = 30.0
RUN_SPAN = "chassis.run"
ECHO = "chassis.core.handle:echo_wire"
QUICK_ERROR = "chassis_contracts.lane:error_handle"
SLOW_ERROR = f"{__name__}:slow_handle"
_OTEL_VARS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "OTEL_TRACES_SAMPLER",
    "OTEL_TRACES_SAMPLER_ARG",
)


async def slow_handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Event]:
    """`start`, then a sleep past the default budget: only the run deadline ends it, as an error."""
    yield Start(request_id=ctx["request_id"])
    await asyncio.sleep(SLOW_S)
    yield End(status="ok")


# --- The stack: fresh, at rate 0, under its own project ---


def _env_file(folder: Path) -> tuple[Path, str]:
    """Every credential the compose file needs, made fresh as `stack.sh` makes them, in a mode 600
    file. Returns the path and the chassis OTLP token."""
    public = f"pk-lf-{secrets.token_hex(16)}"
    secret = f"sk-lf-{secrets.token_hex(16)}"
    token = secrets.token_hex(24)
    values = {
        "POSTGRES_PASSWORD": secrets.token_hex(16),
        "CLICKHOUSE_PASSWORD": secrets.token_hex(16),
        "MINIO_ROOT_PASSWORD": secrets.token_hex(16),
        "REDIS_AUTH": secrets.token_hex(16),
        "NEXTAUTH_SECRET": secrets.token_hex(32),
        "LANGFUSE_SALT": secrets.token_hex(16),
        "LANGFUSE_ENCRYPTION_KEY": secrets.token_hex(32),
        "LANGFUSE_PUBLIC_KEY": public,
        "LANGFUSE_SECRET_KEY": secret,
        "LANGFUSE_AUTH": base64.b64encode(f"{public}:{secret}".encode()).decode(),
        "OTLP_CHASSIS_TOKEN": token,
    }
    path = folder / ".env.poc07-sampling"
    path.touch(mode=0o600)
    path.write_text("".join(f"{name}={value}\n" for name, value in values.items()))
    return path, token


def _percent(rate: str) -> str:
    """The Collector percent for `rate`, by the one conversion `stack.sh` runs."""
    env = {"PATH": os.environ.get("PATH", ""), "TRACE_SAMPLE_RATE": rate}
    made = subprocess.run(
        [sys.executable, "-I", str(CONVERTER)], env=env, capture_output=True, text=True, check=True
    )
    return made.stdout.strip()


def _compose(env_file: Path, percent: str, *args: str) -> subprocess.CompletedProcess[str]:
    """`docker compose` on this project only. Output is kept, never printed whole."""
    env = {**os.environ, "TRACE_SAMPLE_PERCENT": percent}
    command = ["docker", "compose", "-p", PROJECT, "--env-file", str(env_file)]
    return subprocess.run(
        [*command, "-f", str(COMPOSE_FILE), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _answers(url: str) -> bool:
    try:
        return httpx.get(url, timeout=5, trust_env=False).is_success
    except httpx.HTTPError:
        return False


def _wait_ready() -> None:
    deadline = time.monotonic() + READY_WAIT_S
    for url in READY_URLS:
        while not _answers(url):
            if time.monotonic() > deadline:
                pytest.fail(f"{url} did not answer in {READY_WAIT_S:.0f} s", pytrace=False)
            time.sleep(3)


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The PoC-7 stack at rate 0, started fresh; yields the chassis OTLP token. `down -v` after."""
    if _answers(f"{JAEGER}/") or _answers(COLLECTOR_HEALTH):
        pytest.fail("a Jaeger or a Collector already answers: another stack is up", pytrace=False)
    percent = _percent(RATE)
    assert percent == "0"
    env_file, token = _env_file(tmp_path_factory.mktemp("sampling"))
    try:
        up = _compose(env_file, percent, "up", "-d", "--wait", "--quiet-pull")
        if up.returncode != 0:
            pytest.fail(f"compose up failed: {up.stderr[-2000:]}", pytrace=False)
        _wait_ready()
        yield token
    finally:
        down = _compose(env_file, percent, "down", "-v", "--remove-orphans")
        print(f"compose down -v -p {PROJECT}: exit {down.returncode}")


# --- The runs and the trace store ---


@dataclass(frozen=True)
class _Run:
    trace_id: str
    status: int
    body: str
    elapsed_s: float


def _config(handle: str) -> dict[str, Any]:
    return {
        "version": "cfg-1",
        "profile": "fake",
        "agent": {"name": "echo", "version": "0.0.1"},
        "spec": {
            "adapters": {"telemetry": "otel"},
            "engine": {"connector": "inprocess", "handle": handle},
        },
    }


async def _run(handle: str) -> _Run:
    """One `/v1/run` on a chassis serving `handle`, in a new W3C trace; the spans flushed after."""
    app = create_app(ChassisConfig.model_validate(_config(handle)))
    trace_id = uuid.uuid4().hex
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://chassis", timeout=SLOW_S + 30
        ) as client:
            started = time.monotonic()
            answer = await client.post(
                "/v1/run", json={"input": {"text": "hello"}, "trace_id": trace_id}
            )
            elapsed_s = time.monotonic() - started
        provider = getattr(app.state.ports.telemetry, "tracer_provider", None)
        if provider is not None:
            provider.force_flush()
    return _Run(trace_id, answer.status_code, answer.text, elapsed_s)


def _in_jaeger(trace_id: str) -> set[str]:
    """The span names Jaeger holds for `trace_id`; empty when it has none."""
    answer = httpx.get(f"{JAEGER}/api/traces/{trace_id}", timeout=10, trust_env=False)
    if answer.status_code == 404:
        return set()
    answer.raise_for_status()
    return {
        span["operationName"]
        for trace in answer.json().get("data") or []
        for span in trace["spans"]
    }


def _found(trace_id: str, deadline: float) -> set[str]:
    """`_in_jaeger` until it holds the run span, or the last answer at `deadline`."""
    while True:
        names = _in_jaeger(trace_id)
        if RUN_SPAN in names or time.monotonic() > deadline:
            return names
        time.sleep(POLL_S)


def _ended_ok(run: Any) -> bool:
    """HTTP 200 with the envelope's `status` "ok"; an error run is anything else."""
    if run.status != 200:
        return False
    try:
        return bool(json.loads(run.body).get("status") == "ok")
    except ValueError:
        return False


def _export_to_the_collector(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    for name in _OTEL_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", COLLECTOR)
    # The SDK URL-decodes header values, so the space is %20.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", f"Authorization=Bearer%20{token}")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "poc07-sampling-live")


def test_at_rate_zero_both_error_traces_are_kept_and_the_ok_trace_is_not(
    stack: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """014 H-7: at rate 0, a quick error run and a 30 s error run are kept; an ok run is not."""
    _export_to_the_collector(monkeypatch, stack)

    ok = asyncio.run(_run(ECHO))
    quick = asyncio.run(_run(QUICK_ERROR))
    slow = asyncio.run(_run(SLOW_ERROR))
    for label, run in (("ok", ok), ("quick error", quick), ("30 s error", slow)):
        body = run.body[:160].replace("\n", " ")
        print(f"{label}: trace {run.trace_id} http {run.status} {run.elapsed_s:.1f} s {body}")

    assert _ended_ok(ok), ok.body
    assert not _ended_ok(quick), quick.body
    assert not _ended_ok(slow), slow.body
    assert slow.elapsed_s >= DEFAULT_TIMEOUT_S - 1

    deadline = time.monotonic() + SPAN_WAIT_S
    quick_spans = _found(quick.trace_id, deadline)
    slow_spans = _found(slow.trace_id, deadline)
    print(f"jaeger quick error: {sorted(quick_spans)}")
    print(f"jaeger 30 s error: {sorted(slow_spans)}")
    assert RUN_SPAN in quick_spans
    assert RUN_SPAN in slow_spans

    time.sleep(ABSENT_MARGIN_S)
    ok_spans = _in_jaeger(ok.trace_id)
    print(f"jaeger ok: {sorted(ok_spans)}")
    assert ok_spans == set()
