"""Exit criteria 1 and 2 against the real services of `deploy/compose/poc07` (Langfuse v4, the
OTel Collector, Jaeger). `network`; skipped unless `POC07_STACK=1`. CI runs it in
`.github/workflows/poc07-stack.yml`; by hand:

    deploy/compose/poc07/stack.sh up && deploy/compose/poc07/stack.sh wait
    export "$(deploy/compose/poc07/stack.sh env)"
    POC07_STACK=1 uv run pytest -m network -v \\
        pocs/poc-07-cross-cutting-decisions/tests/test_poc07_stack.py

Credentials come from the env file named by `POC07_ENV_FILE` (made by `stack.sh`). They are
read into the process environment through `monkeypatch` and never printed or put in a message.

The read paths are the ones the `langfuse` 5.0.4 generated client calls (`langfuse/api/`):

- spans: `GET /api/public/v2/observations?traceId=` (`observations/raw_client.py`). There is no
  `GET /api/public/traces/{id}` in that client (only DELETE), and Observations API v2 is one of
  Langfuse v4's real-time read paths (`opentelemetry/raw_client.py`).
- scores: `GET /api/public/v3/scores?id=` or `traceId=`, `fields=details,subject`
  (`scores/raw_client.py`). There is no `GET /api/public/scores/{id}` (only DELETE).
- OTLP in: `POST /api/public/otel/v1/traces` with `x-langfuse-ingestion-version: 4`, which v4
  needs for real-time processing (`opentelemetry/raw_client.py`).

Jaeger v2: `GET /api/traces/{traceId}` on 16686 (the query service's HTTP API).

Ingestion is asynchronous on both, so every read polls with a timeout.

Depends on adapters merged from other work packages: `chassis.adapters.otel:OtelTelemetry` (W1)
and `chassis.adapters.langfuse:LangfuseFeedback`, `feedback_from_score`, and `POST /v1/feedback`
(W3). They are imported inside the tests; with the stack up and an adapter missing, the test
fails and says which one.
"""

from __future__ import annotations

import importlib
import os
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from chassis.ports.feedback import Feedback
from chassis_contracts import FeedbackPortContract

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("POC07_STACK") != "1",
        reason="a PoC-7 stack drill: needs deploy/compose/poc07 up and POC07_STACK=1",
    ),
]

LANGFUSE = "http://127.0.0.1:3000"
JAEGER = "http://127.0.0.1:16686"
COLLECTOR = "http://127.0.0.1:4318"
SPAN_WAIT_S = float(os.environ.get("POC07_SPAN_WAIT_S", "120"))
"""suggested: how long a read waits for a span to be ingested."""
SCORE_WAIT_S = float(os.environ.get("POC07_SCORE_WAIT_S", "180"))
"""suggested: how long a read waits for a score to be ingested."""
POLL_S = 2.0
RUN_SPAN = "chassis.run"
CHILD_SPAN = "poc07.child"

_OTEL_VARS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
)


def _require(module: str, name: str) -> Any:
    """`module.name`, or a failure naming the work package that has not been merged yet."""
    try:
        return getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError):
        pytest.fail(f"{module}:{name} is not here yet (a PoC-7 adapter not merged)", pytrace=False)


class _Keys:
    """The Langfuse project keys. `repr` never shows them."""

    def __init__(self, values: dict[str, str]) -> None:
        self.public = values["LANGFUSE_PUBLIC_KEY"]
        self.secret = values["LANGFUSE_SECRET_KEY"]
        self.basic = values["LANGFUSE_AUTH"]
        """base64 of `public:secret`."""

    def __repr__(self) -> str:
        return "_Keys(***)"


@pytest.fixture(scope="module")
def keys() -> _Keys:
    path = os.environ.get("POC07_ENV_FILE")
    if not path or not Path(path).is_file():
        pytest.fail("POC07_ENV_FILE does not name the stack's env file", pytrace=False)
    values: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep:
            values[name.strip()] = value.strip()
    missing = [
        n
        for n in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_AUTH")
        if n not in values
    ]
    if missing:
        pytest.fail(f"the env file lacks {missing}", pytrace=False)
    return _Keys(values)


@pytest.fixture
def langfuse_env(keys: _Keys, monkeypatch: pytest.MonkeyPatch) -> _Keys:
    """`LangfuseFeedback.from_env()` reads these."""
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    monkeypatch.setenv("LANGFUSE_HOST", LANGFUSE)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", keys.public)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", keys.secret)
    return keys


@pytest.fixture
def langfuse(keys: _Keys) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=LANGFUSE, auth=(keys.public, keys.secret), timeout=10) as client:
        yield client


def _export_to(monkeypatch: pytest.MonkeyPatch, endpoint: str, headers: str | None = None) -> None:
    """The exporter config: the only thing that differs between the two runs of the swap."""
    for name in _OTEL_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
    if headers is not None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", headers)
    # Neither Langfuse nor the Collector's pipelines take metrics; the same in both runs.
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "poc07-stack-test")


def _poll(read: Callable[[], set[str]], want: set[str], wait_s: float) -> set[str]:
    """`read()` until it holds every name in `want`, or the last answer after `wait_s`."""
    deadline = time.monotonic() + wait_s
    while True:
        got = read()
        if want <= got or time.monotonic() > deadline:
            return got
        time.sleep(POLL_S)


def _langfuse_names(client: httpx.Client, trace_id: str) -> set[str]:
    answer = client.get(
        "/api/public/v2/observations",
        params={"traceId": trace_id, "fields": "core,basic", "limit": 100},
    )
    answer.raise_for_status()
    return {o["name"] for o in answer.json()["data"] if o.get("name")}


def _jaeger_names(trace_id: str) -> set[str]:
    answer = httpx.get(f"{JAEGER}/api/traces/{trace_id}", timeout=10)
    if answer.status_code == 404:
        return set()
    answer.raise_for_status()
    return {
        span["operationName"]
        for trace in answer.json().get("data") or []
        for span in trace["spans"]
    }


def _emit_run(trace_id: str) -> None:
    """One `chassis.run` span with a nested child, in the W3C trace `trace_id`, exported by
    `OtelTelemetry.from_env()`. The same code for both runs of the swap."""
    otel_telemetry = _require("chassis.adapters.otel", "OtelTelemetry")
    telemetry = otel_telemetry.from_env()
    with (
        telemetry.span(RUN_SPAN, trace_id=trace_id, request_id=f"req-{trace_id[:8]}"),
        telemetry.span(CHILD_SPAN, step="one"),
    ):
        pass
    telemetry.shutdown()  # flushes the batch processor


# --- (a) criterion 1: the same spans reach Langfuse and Jaeger; the swap is config only ---


def test_same_spans_reach_langfuse_and_jaeger_through_the_collector(
    langfuse: httpx.Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 1: "the same spans reach Langfuse and a second OpenTelemetry backend"."""
    _export_to(monkeypatch, COLLECTOR)
    trace_id = uuid.uuid4().hex
    _emit_run(trace_id)

    want = {RUN_SPAN, CHILD_SPAN}
    in_langfuse = _poll(lambda: _langfuse_names(langfuse, trace_id), want, SPAN_WAIT_S)
    in_jaeger = _poll(lambda: _jaeger_names(trace_id), want, SPAN_WAIT_S)
    assert want <= in_langfuse
    assert want <= in_jaeger
    assert in_langfuse == in_jaeger


def test_swap_to_direct_langfuse_by_exporter_config_only(
    langfuse: httpx.Client, keys: _Keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 1: "Telemetry swap shown": straight to Langfuse "by exporter config only"."""
    # Only OTEL_EXPORTER_OTLP_* change: straight to Langfuse's OTLP endpoint, Basic auth and the
    # v4 ingestion header (the Python SDK URL-decodes header values, so the space is %20).
    _export_to(
        monkeypatch,
        f"{LANGFUSE}/api/public/otel",
        f"Authorization=Basic%20{keys.basic},x-langfuse-ingestion-version=4",
    )
    trace_id = uuid.uuid4().hex
    _emit_run(trace_id)

    want = {RUN_SPAN, CHILD_SPAN}
    assert want <= _poll(lambda: _langfuse_names(langfuse, trace_id), want, SPAN_WAIT_S)
    # The Collector is out of the path now, so Jaeger never sees this trace.
    assert _jaeger_names(trace_id) == set()


# --- (b) criterion 2: LangfuseFeedback passes the FeedbackPort suite against the real Langfuse --


def _scores(client: httpx.Client, wait_s: float, **query: str) -> list[dict[str, Any]]:
    """`GET /api/public/v3/scores`, polled until it returns any score or `wait_s` runs out."""
    deadline = time.monotonic() + wait_s
    while True:
        answer = client.get(
            "/api/public/v3/scores", params={**query, "fields": "details,subject", "limit": 50}
        )
        answer.raise_for_status()
        data: list[dict[str, Any]] = answer.json()["data"]
        if data or time.monotonic() > deadline:
            return data
        time.sleep(POLL_S)


class _Recording:
    """The real adapter, remembering what each `feedback_id` was sent with: Langfuse sets a
    `timestamp` when none was sent, and the suite compares with what was sent."""

    name = "langfuse"

    def __init__(self, port: Any) -> None:
        self.port = port
        self.sent: dict[str, Feedback] = {}

    async def submit(self, feedback: Feedback) -> None:
        await self.port.submit(feedback)
        self.sent[feedback.feedback_id] = feedback


class TestLangfuseFeedbackOnTheStack(FeedbackPortContract):
    """No `make_unavailable`: a real service cannot be made to fail on cue (the suite skips it)."""

    @pytest.fixture
    def feedback_port(self, langfuse_env: _Keys) -> _Recording:
        port = _require("chassis.adapters.langfuse", "LangfuseFeedback")
        return _Recording(port.from_env())

    @pytest.fixture
    def read_feedback(
        self, feedback_port: _Recording, langfuse: httpx.Client
    ) -> Callable[[str], Feedback | None]:
        from_score = _require("chassis.adapters.langfuse", "feedback_from_score")

        def read(feedback_id: str) -> Feedback | None:
            found = _scores(langfuse, SCORE_WAIT_S, id=feedback_id)
            if not found:
                return None
            feedback: Feedback = from_score(found[0])
            sent = feedback_port.sent.get(feedback_id)
            if sent is not None and getattr(sent, "timestamp", "absent") is None:
                feedback = feedback.model_copy(update={"timestamp": None})
            return feedback

        return read

    @pytest.fixture
    def count_feedback(self, langfuse: httpx.Client) -> Callable[[str], int]:
        return lambda feedback_id: len(_scores(langfuse, SCORE_WAIT_S, id=feedback_id))


# --- (c) a chassis call: its spans and its feedback land on one Langfuse trace ---


async def test_chassis_feedback_lands_on_the_run_trace(
    langfuse: httpx.Client, langfuse_env: _Keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Feedback scope: "onto the call's trace", on a real Langfuse."""
    from chassis.profiles import REGISTRY
    from chassis.server import ChassisConfig, create_app

    for port, adapter in (("telemetry", "otel"), ("feedback", "langfuse")):
        if adapter not in REGISTRY[port]:
            pytest.fail(f"REGISTRY[{port!r}] has no {adapter!r} yet (not merged)", pytrace=False)
    _export_to(monkeypatch, COLLECTOR)
    config = ChassisConfig.model_validate(
        {
            "version": "cfg-1",
            "profile": "fake",
            "agent": {"name": "echo", "version": "0.0.1"},
            "spec": {
                "adapters": {"telemetry": "otel", "feedback": "langfuse"},
                "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
            },
        }
    )
    app = create_app(config)
    trace_id = uuid.uuid4().hex
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chassis") as client:
            run = await client.post(
                "/v1/run", json={"input": {"text": "hello"}, "trace_id": trace_id}
            )
            assert run.status_code == 200, run.text
            assert run.json()["trace_id"] == trace_id
            if "/v1/feedback" not in {getattr(r, "path", None) for r in app.routes}:
                pytest.skip("POST /v1/feedback is not mounted on this branch")
            given = await client.post(
                "/v1/feedback",
                json={"request_id": run.json()["request_id"], "score": 1.0, "label": "helpful"},
            )
            assert given.status_code == 202, given.text
            feedback_id = given.json()["feedback_id"]
            assert given.json()["trace_id"] == trace_id
        provider = getattr(app.state.ports.telemetry, "tracer_provider", None)
        if provider is not None:
            provider.force_flush()

    names = _poll(lambda: _langfuse_names(langfuse, trace_id), {RUN_SPAN}, SPAN_WAIT_S)
    assert RUN_SPAN in names
    scores = _scores(langfuse, SCORE_WAIT_S, traceId=trace_id)
    ours = [s for s in scores if s["id"] == feedback_id]
    assert ours, "the feedback score is on the run's trace"
    assert ours[0]["value"] == 1.0
    assert (ours[0].get("subject") or {}).get("id") == trace_id
