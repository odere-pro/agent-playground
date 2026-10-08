"""Schemathesis property tests generated from the chassis's own OpenAPI spec (PoC-3 open note,
section 11). Exit criterion: "The interface contract suite and the Schemathesis tests run offline
in CI on every commit" (this file is the Schemathesis half; `test_exit_criteria.py` checks that the
gate runs it).

In process: `schemathesis.openapi.from_asgi` over the app built from `fake.yaml` with a fake engine
and every interface on. Schemathesis runs the app's lifespan once, on its own thread, for the life
of the process. No socket.

Every operation in the spec is tested: `/v1/run`, `/v1/chat/completions`, `/v1/messages`,
`/manifest`, `/health`, `/ready`. Deterministic (`derandomize`), no example database, 25 examples
per operation in the fuzzing phase, no stateful phase. The coverage phase is not bounded by
`max-examples`; on `/v1/messages` (the Anthropic `MessageCreateParams` union) it builds about
19 000 bodies, so a fixed one in four is sent there (`COVERAGE_SAMPLE`). A case sampled out is
never sent and never counted as passed: the run counts what it sent and what it dropped per route
(`COVERAGE_COUNTS`), and fails when fewer than `COVERAGE_SENT_MIN` were sent. Time budget: under
30 s for the file. Measured 2026-10-01 at load average 14.8: 19.4 s for the file, `/v1/messages`
8.2 s, `/v1/chat/completions` 3.7 s (`scripts/check_offline.sh <this file> --durations=4`).
Without the sample, measured again 2026-10-01 at load average 15.7: 29.3 s for the file,
`/v1/messages` 18.8 s, so the sample stays.

Every check named in section 11 runs on every request sent; none is filtered for any route. The
findings the run found are fixed; each is pinned below by its minimal request.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import schemathesis
import schemathesis.checks
from fastapi import FastAPI
from poc03_support import AGENT, fake_engine_app, operations
from schemathesis import Case, GenerationMode, HookContext
from schemathesis.generation.meta import TestPhase

APP: FastAPI = fake_engine_app()

CHECKS_ON = (
    "not_a_server_error",
    "status_code_conformance",
    "content_type_conformance",
    "response_headers_conformance",
    "response_schema_conformance",
    "negative_data_rejection",
    "unsupported_method",
    "missing_required_header",
)
"""Section 11, word for word."""

CHECKS_OFF = ("positive_data_acceptance",)
"""Off on purpose. It fails any 4xx on a body that fits the schema. Every body route accepts a
documented subset of a wider schema: `agent` (native) or `model` (OpenAI, Anthropic) must name the
served agent, else 400 or 404; and the SDK request types list features the adapters refuse with a
declared 400 (`n > 1`, client `tools`, images, `logprobs`, ...; open note, section 2). Those
answers are declared in the spec and are the contract. Measured at PoC-3 open: it fails both SDK
routes at once.
"""

MAX_EXAMPLES = 25  # suggested: section 11

CONFIG = schemathesis.Config.from_dict(
    {
        "generation": {"deterministic": True, "max-examples": MAX_EXAMPLES, "database": "none"},
        "phases": {"stateful": {"enabled": False}},
        "checks": {
            **{name: {"enabled": True} for name in CHECKS_ON},
            **{name: {"enabled": False} for name in CHECKS_OFF},
        },
    }
)

SCHEMA = schemathesis.openapi.from_asgi("/openapi.json", APP, config=CONFIG)


def _check(name: str) -> Any:
    """A built-in check by its registered name."""
    return getattr(schemathesis.checks, name)


CHECKS = [_check(name) for name in CHECKS_ON]

SDK_PATHS = ("/v1/chat/completions", "/v1/messages")
BODY_PATHS = ("/v1/run", *SDK_PATHS)
EXPECTED_OPERATIONS = {
    ("POST", "/v1/run"): ("run", "native"),
    ("POST", "/v1/chat/completions"): ("chat_completions", "openai"),
    ("POST", "/v1/messages"): ("messages", "anthropic"),
    ("GET", "/manifest"): ("manifest", None),
    ("GET", "/health"): ("health", None),
    ("GET", "/ready"): ("ready", None),
}
"""Section 11: every operation, its explicit `operation_id`, and its `x-chassis-interface`."""


@SCHEMA.hook("map_case")
def _name_the_served_agent(context: HookContext, case: Case[Any]) -> Case[Any]:
    """In a positive case only, name the served agent in `model` (or a set `agent`).

    Without it a random `model` makes every positive SDK case a 404, so the 200 answers and their
    schemas are never reached. Negative cases are left as generated; the 404 for a wrong `model` is
    still reached by them and by the coverage phase.
    """
    meta = case.meta
    if meta is None or meta.generation.mode != GenerationMode.POSITIVE:
        return case
    if isinstance(case.body, dict):
        if "model" in case.body:
            case.body["model"] = AGENT
        if case.body.get("agent") is not None:
            case.body["agent"] = AGENT
    return case


# --- the property run --------------------------------------------------------------------------


COVERAGE_SAMPLE = {"/v1/messages": 4}
"""The time budget. The coverage phase enumerates every edge of the schema and has no size knob;
on `/v1/messages` that is about 19 000 bodies, almost all answered by the same 400 handler. A
fixed one in four of them is kept (chosen by a hash of the case, so the run stays deterministic).
A request to an undeclared method is always kept. Every check runs on every request that is sent.
suggested: the ratio.
"""


COVERAGE_SENT_MIN = {"/v1/messages": 3800}
"""The fewest coverage cases a sampled route must send, so the sample cannot shrink silently.
Measured 2026-10-01: 4727 sent, 14 208 sampled out on `/v1/messages`; the floor is about four in
five of what was sent. suggested."""

COVERAGE_COUNTS: dict[str, dict[str, int]] = {}
"""Per sampled route, the coverage cases `sent` and `sampled_out` in this run."""


def _sampled_out(case: Case[Any]) -> bool:
    every = COVERAGE_SAMPLE.get(case.operation.path)
    meta = case.meta
    if every is None or meta is None or meta.phase.name != TestPhase.COVERAGE:
        return False
    if case.method.upper() != case.operation.method.upper():
        return False
    key = repr((case.body, sorted((case.headers or {}).items()), case.query)).encode()
    return hashlib.sha256(key).digest()[0] % every != 0


@SCHEMA.hook("filter_case")
def _sample_the_coverage_phase(context: HookContext, case: Case[Any]) -> bool:
    """Drop a sampled-out coverage case where it is generated, before it becomes a Hypothesis
    explicit example. Dropping it in the test body instead still paid Hypothesis's per-example
    cost for all 19 000, about three quarters of the route's time (cProfile, 2026-10-01).
    """
    dropped = _sampled_out(case)
    path = case.operation.path
    meta = case.meta
    if path in COVERAGE_SAMPLE and meta is not None and meta.phase.name == TestPhase.COVERAGE:
        counts = COVERAGE_COUNTS.setdefault(path, {"sent": 0, "sampled_out": 0})
        counts["sampled_out" if dropped else "sent"] += 1
    return not dropped


@pytest.fixture(scope="module")
def coverage_counts() -> Iterator[dict[str, dict[str, int]]]:
    """After the property run, each sampled route that ran sent at least its floor. Printed with
    `-s`; a failure here is an error on the module's last test."""
    yield COVERAGE_COUNTS
    for path, counts in COVERAGE_COUNTS.items():
        print(f"coverage phase, {path}: {counts}")
        assert counts["sent"] >= COVERAGE_SENT_MIN[path], (path, counts)


@SCHEMA.parametrize()
def test_every_operation_meets_its_spec(
    case: Case[Any], coverage_counts: dict[str, dict[str, int]]
) -> None:
    """Every operation in the spec, with the section 11 checks: no 5xx unless declared, only
    declared statuses, content types, headers, and schemas, invalid input refused, an undeclared
    method refused, and a missing required header refused.
    """
    response = case.call()
    case.validate_response(response, checks=CHECKS)


# --- the spec itself ---------------------------------------------------------------------------


def test_the_spec_is_openapi_3_1_with_every_operation_named_and_labeled() -> None:
    """The spec is OpenAPI 3.1; every operation has an explicit `operation_id` (not FastAPI's
    generated `<name>_<path>_<method>`); each interface route carries `x-chassis-interface`,
    and no other route does. `/v1/mcp` and the proxy app are not in it.
    """
    spec = APP.openapi()
    assert spec["openapi"].startswith("3.1."), spec["openapi"]
    found = {
        key: (op.get("operationId"), op.get("x-chassis-interface"))
        for key, op in operations(spec).items()
    }
    assert found == EXPECTED_OPERATIONS
    assert "/v1/mcp" not in spec["paths"]
    assert all("/mcp" not in path for path in spec["paths"])


def test_schemathesis_sees_every_operation_in_the_spec() -> None:
    """The property run covers each operation; none is filtered out or failed to load."""
    found = [result.ok() for result in SCHEMA.get_all_operations()]  # type: ignore[union-attr]
    loaded = {(op.method.upper(), op.path) for op in found}
    assert loaded == set(EXPECTED_OPERATIONS)


def test_the_checks_are_the_ones_section_11_names() -> None:
    """The run's checks are exactly the eight section 11 turns on; `positive_data_acceptance` is
    off in the config too, so no default set can bring it back.
    """
    assert [c.__name__ for c in CHECKS] == list(CHECKS_ON)
    configured = SCHEMA.config.checks_config_for()
    assert all(configured.get_by_name(name=name).enabled for name in CHECKS_ON)
    assert not any(configured.get_by_name(name=name).enabled for name in CHECKS_OFF)
    generation = SCHEMA.config.generation_for(phase="fuzzing")
    assert (generation.deterministic, generation.max_examples, generation.database) == (
        True,
        MAX_EXAMPLES,
        "none",
    )


# --- explicit, known-good calls: the 200 answers the property run rarely reaches ----------------

COMPLETE_BODIES: dict[str, dict[str, Any]] = {
    "/v1/run": {"input": {"text": "hello big world"}},
    "/v1/chat/completions": {"model": AGENT, "messages": [{"role": "user", "content": "hello"}]},
    "/v1/messages": {
        "model": AGENT,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hello"}],
    },
}


@pytest.mark.parametrize("path", BODY_PATHS)
def test_a_known_good_complete_call_meets_its_spec(path: str) -> None:
    """A plain one-turn call answers 200 and fits the declared `Response`, `ChatCompletion`, or
    `Message` schema and headers. The fuzzer almost never builds an OpenAI body the adapter
    accepts, so this pins the 200 schema check on every body route.
    """
    case = SCHEMA[path]["POST"].Case(body=COMPLETE_BODIES[path], media_type="application/json")
    response = case.call_and_validate(checks=CHECKS)
    assert response.status_code == 200, response.text


# --- fixed findings, each pinned by its minimal request ------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"budget": {"max_tokens": True}},
        {"budget": {"timeout_ms": False}},
        {"budget": {"max_tokens": "5"}},
        {"stream": 0},
    ],
    ids=["max_tokens-true", "timeout_ms-false", "max_tokens-string", "stream-zero"],
)
def test_finding_run_refuses_a_value_of_the_wrong_json_type(extra: dict[str, Any]) -> None:
    """Finding 1 (fixed in `RunRequest`: strict at the HTTP boundary). Minimal request:
    `POST /v1/run` `{"input": {"text": "x"}, "budget": {"max_tokens": true}}` (or `"stream": 0`).
    Was 200 `status: ok`; is 422, as declared for a body that does not fit the schema.
    """
    case = SCHEMA["/v1/run"]["POST"].Case(
        body={"input": {"text": "x"}, **extra}, media_type="application/json"
    )
    response = case.call()
    assert response.status_code in (400, 422), (response.status_code, response.text)


@pytest.mark.parametrize("path", SDK_PATHS)
async def test_finding_an_undecodable_body_gets_the_formats_error_shape(path: str) -> None:
    """Finding 2 (fixed in `interfaces.errors`: an `HTTPException` before the handler is in the
    route's format). Minimal request: `POST /v1/chat/completions` (or `/v1/messages`),
    `content-type: application/json`, body the single byte `0xff`. Was 400
    `{"detail": "There was an error parsing the body"}`; is 400 `OpenAIErrorResponse` or
    Anthropic `ErrorResponse`, as declared. Checked with Schemathesis's own response checks.
    """
    app = fake_engine_app()  # its own app: the module app's lifespan runs on Schemathesis's thread
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
    ):
        response = await client.post(
            path, content=b"\xff", headers={"content-type": "application/json"}
        )
    assert response.status_code == 400
    case = SCHEMA[path]["POST"].Case(body=COMPLETE_BODIES[path], media_type="application/json")
    case.validate_response(
        response,
        checks=[
            _check("status_code_conformance"),
            _check("content_type_conformance"),
            _check("response_schema_conformance"),
        ],
    )


STREAM_BODIES = {path: {**body, "stream": True} for path, body in COMPLETE_BODIES.items()}


@pytest.mark.parametrize("path", BODY_PATHS)
def test_finding_a_streamed_answer_fits_the_declared_event_stream_schema(path: str) -> None:
    """Finding 3 (fixed in the spec: `interfaces.serve.SSE_EVENT_SCHEMA`, one event object).
    Minimal request: `POST /v1/run` `{"input": {"text": "hello big world"}, "stream": true}` (and
    the same plain call, streamed, on the two SDK routes). Was: SSE event #0 is not of type string.
    """
    case = SCHEMA[path]["POST"].Case(body=STREAM_BODIES[path], media_type="application/json")
    case.call_and_validate(checks=[_check("response_schema_conformance")])
