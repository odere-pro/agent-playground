"""PoC-8 drill: a real LiteLLM proxy in the trace, with its real Presidio hook, against
`deploy/compose/poc08-litellm`. `network`; collected only with `POC08_LITELLM=1` (conftest.py),
so `make test` never sees it. CI runs it in `.github/workflows/poc08-litellm.yml`; by hand:

    deploy/compose/poc08-litellm/stack.sh up && deploy/compose/poc08-litellm/stack.sh wait
    export "$(deploy/compose/poc08-litellm/stack.sh env)"
    POC08_LITELLM=1 uv run pytest -m network -v -s \\
        pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py

What it shows:

(a) One `POST /v1/run` gives one trace in Jaeger holding `chassis.run`, `chassis.model.route`,
    the model proxy's `chassis.model.call`, and a span of service `litellm` that descends from
    `chassis.model.call`, all on the run's trace id.
(b) A PII input (email, phone, card) reaches the fake model masked by LiteLLM's `presidio` hook.
    The fake model's script answers by what it got (`model_script.py`), so its answer says
    whether the raw values arrived. The same holds for a call straight to LiteLLM, so the masking
    is LiteLLM's, not the chassis's. The control: plain text reaches the model unchanged.
(c) The red-team cases on PII and secrets that pass LiteLLM's guardrail chain on this stack
    (`model_script.REDTEAM_CASES`) run through the chassis. The drill prints, per case, whether it
    was stopped (`chassis_contracts.redteam.live.stopped`) and whether `pii-mask` alone changes
    its text. It asserts that each case ran, not a rate: the numbers are recorded, not gated.

Artifacts go to `POC08_LITELLM_OUT` (CI uploads it): the Jaeger trace JSON and the results.
The chassis's virtual key comes from the file `POC08_LITELLM_KEY_FILE` names (`stack.sh env`);
it is used for the direct LiteLLM calls only and never printed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from chassis_contracts.redteam import load_cases
from chassis_contracts.redteam.live import Evidence, request_body, stopped

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("POC08_LITELLM") != "1",
        reason="a PoC-8 drill: needs deploy/compose/poc08-litellm up and POC08_LITELLM=1",
    ),
]

ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "deploy/compose/poc08-litellm"
CHASSIS = "http://127.0.0.1:8080"
LITELLM = "http://127.0.0.1:4000"
JAEGER = "http://127.0.0.1:16686"
SPAN_WAIT_S = float(os.environ.get("POC08_LITELLM_SPAN_WAIT_S", "120"))
"""suggested: how long the drill waits for both exporters' batches to reach Jaeger."""
POLL_S = 2.0
RUN_TIMEOUT_S = 120.0
CHASSIS_SPANS = ("chassis.run", "chassis.model.route", "chassis.model.call")
PROXY_SPAN = "chassis.model.call"
LITELLM_SERVICE = "litellm"
GUARDRAIL = "pii-mask"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "poc08_litellm_model_script", STACK / "model_script.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script_module()


class _Key:
    """The chassis's LiteLLM virtual key. `repr` never shows it."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __repr__(self) -> str:
        return "_Key(***)"


@pytest.fixture(scope="module")
def key() -> _Key:
    path = os.environ.get("POC08_LITELLM_KEY_FILE")
    if not path or not Path(path).is_file():
        pytest.fail("POC08_LITELLM_KEY_FILE does not name the stack's key file", pytrace=False)
    for line in Path(path).read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == "LITELLM_API_KEY" and value.strip():
            return _Key(value.strip())
    pytest.fail("the key file holds no LITELLM_API_KEY (did stack.sh up mint it?)", pytrace=False)


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    raw = os.environ.get("POC08_LITELLM_OUT")
    path = Path(raw) if raw else tmp_path_factory.mktemp("poc08-litellm")
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def chassis() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=CHASSIS, timeout=RUN_TIMEOUT_S, trust_env=False) as client:
        yield client


@pytest.fixture
def litellm(key: _Key) -> Iterator[httpx.Client]:
    headers = {"Authorization": f"Bearer {key.value}"}
    with httpx.Client(
        base_url=LITELLM, headers=headers, timeout=RUN_TIMEOUT_S, trust_env=False
    ) as client:
        yield client


def _write(out_dir: Path, name: str, data: Any) -> None:
    (out_dir / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _run(chassis: httpx.Client, body: dict[str, Any]) -> httpx.Response:
    return chassis.post("/v1/run", json=body)


def _answer(response: httpx.Response) -> str:
    assert response.status_code == 200, response.text[:500]
    return str((response.json().get("output") or {}).get("text") or "")


def _apply(litellm: httpx.Client, text: str, input_type: str = "request") -> str:
    """`pii-mask` on `text` through LiteLLM's `POST /guardrails/apply_guardrail`."""
    body = {"guardrail_name": GUARDRAIL, "text": text, "input_type": input_type}
    answer = litellm.post("/guardrails/apply_guardrail", json=body)
    assert answer.status_code == 200, f"apply_guardrail: HTTP {answer.status_code}"
    return str(answer.json()["response_text"])


# --- (a) one trace: chassis.run, chassis.model.route, chassis.model.call, and LiteLLM ---


def _jaeger_trace(trace_id: str) -> dict[str, Any] | None:
    answer = httpx.get(f"{JAEGER}/api/traces/{trace_id}", timeout=10, trust_env=False)
    if answer.status_code == 404:
        return None
    answer.raise_for_status()
    data = answer.json().get("data") or []
    return data[0] if data else None


def _service(trace: dict[str, Any], span: dict[str, Any]) -> str:
    return str(trace["processes"][span["processID"]]["serviceName"])


def _parent(span: dict[str, Any]) -> str | None:
    for ref in span.get("references") or []:
        if ref.get("refType") == "CHILD_OF":
            return str(ref["spanID"])
    return None


def _ancestors(trace: dict[str, Any], span: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {s["spanID"]: s for s in trace["spans"]}
    chain: list[dict[str, Any]] = []
    parent = _parent(span)
    while parent is not None and parent in by_id and len(chain) < len(by_id):
        chain.append(by_id[parent])
        parent = _parent(by_id[parent])
    return chain


def _litellm_under_proxy(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """The LiteLLM spans with `chassis.model.call` among their ancestors."""
    return [
        s
        for s in trace["spans"]
        if _service(trace, s) == LITELLM_SERVICE
        and any(a["operationName"] == PROXY_SPAN for a in _ancestors(trace, s))
    ]


def _complete(trace: dict[str, Any] | None) -> bool:
    if trace is None:
        return False
    names = {s["operationName"] for s in trace["spans"]}
    return set(CHASSIS_SPANS) <= names and bool(_litellm_under_proxy(trace))


def _poll(read: Callable[[], dict[str, Any] | None], wait_s: float) -> dict[str, Any] | None:
    deadline = time.monotonic() + wait_s
    while True:
        got = read()
        if _complete(got) or time.monotonic() > deadline:
            return got
        time.sleep(POLL_S)


def test_one_run_is_one_trace_with_a_litellm_span_under_the_model_proxy(
    chassis: httpx.Client, out_dir: Path
) -> None:
    trace_id = uuid.uuid4().hex
    run = _run(chassis, {"input": {"text": SCRIPT.CONTROL_TEXT}, "trace_id": trace_id})
    assert _answer(run) == SCRIPT.CONTROL_REPLY
    assert run.json()["trace_id"] == trace_id

    trace = _poll(lambda: _jaeger_trace(trace_id), SPAN_WAIT_S)
    _write(out_dir, f"trace-{trace_id}.json", trace)
    assert trace is not None, f"Jaeger has no trace {trace_id}"
    spans = trace["spans"]
    shape = sorted({(_service(trace, s), s["operationName"]) for s in spans})
    print(f"\ntrace {trace_id}: {len(spans)} spans: {shape}")
    assert {s["traceID"] for s in spans} == {trace_id}
    names = {s["operationName"] for s in spans}
    assert set(CHASSIS_SPANS) <= names, f"missing chassis spans: {set(CHASSIS_SPANS) - names}"
    services = {_service(trace, s) for s in spans}
    assert LITELLM_SERVICE in services, f"no LiteLLM span in the run's trace: {services}"
    under = _litellm_under_proxy(trace)
    assert under, "no LiteLLM span descends from chassis.model.call"
    print(f"LiteLLM spans under {PROXY_SPAN}: {sorted({s['operationName'] for s in under})}")


# --- (b) PII reaches the model masked by LiteLLM's Presidio hook; plain text unchanged ---


def test_pii_reaches_the_model_masked_by_litellms_presidio_hook(
    chassis: httpx.Client, litellm: httpx.Client, out_dir: Path
) -> None:
    through_chassis = _answer(_run(chassis, {"input": {"text": SCRIPT.PII_TEXT}}))
    direct = litellm.post(
        "/v1/chat/completions",
        json={
            "model": "big-default",
            "messages": [{"role": "user", "content": SCRIPT.PII_TEXT}],
        },
    )
    assert direct.status_code == 200, f"LiteLLM chat: HTTP {direct.status_code}"
    direct_answer = direct.json()["choices"][0]["message"]["content"]
    masked = _apply(litellm, SCRIPT.PII_TEXT)
    record = {
        "through_chassis": through_chassis,
        "direct_to_litellm": direct_answer,
        "applied_guardrails_header": direct.headers.get("x-litellm-applied-guardrails"),
        "pii_mask_text": masked,
    }
    _write(out_dir, "pii.json", record)
    print(f"\n{json.dumps(record, indent=2)}")

    assert through_chassis == SCRIPT.MASKED_REPLY, "the model got raw PII through the chassis"
    assert direct_answer == SCRIPT.MASKED_REPLY, "the model got raw PII straight from LiteLLM"
    assert not [v for v in SCRIPT.PII_VALUES if v in masked]
    assert "<EMAIL_ADDRESS>" in masked and "<CREDIT_CARD>" in masked


def test_plain_text_reaches_the_model_unchanged(
    chassis: httpx.Client, litellm: httpx.Client
) -> None:
    assert _answer(_run(chassis, {"input": {"text": SCRIPT.CONTROL_TEXT}})) == (
        SCRIPT.CONTROL_REPLY
    )
    assert _apply(litellm, SCRIPT.CONTROL_TEXT) == SCRIPT.CONTROL_TEXT


# --- (c) the PII and secret red-team cases on this stack: numbers, printed ---


def _error_code(response: httpx.Response) -> str | None:
    try:
        error = (response.json().get("output") or {}).get("error") or {}
    except ValueError:
        return None
    code = error.get("code") if isinstance(error, dict) else None
    return str(code) if code else None


def test_the_pii_and_secret_redteam_cases_report_their_numbers(
    chassis: httpx.Client, litellm: httpx.Client, out_dir: Path
) -> None:
    cases = [c for c in load_cases() if c.id in SCRIPT.REDTEAM_CASES]
    assert sorted(c.id for c in cases) == sorted(SCRIPT.REDTEAM_CASES)
    rows: list[dict[str, Any]] = []
    for case in cases:
        response = _run(chassis, request_body(case))
        evidence = Evidence(response.text, _error_code(response))
        input_type = "request" if case.kind == "input" else "response"
        rows.append(
            {
                "case": case.id,
                "category": case.category,
                "kind": case.kind,
                "http": response.status_code,
                "error_code": evidence.error_code,
                "stopped": stopped(case, evidence),
                "pii_mask_changes_it": _apply(litellm, case.payload, input_type) != case.payload,
            }
        )
    done = sum(r["stopped"] for r in rows)
    summary = {
        "stack": "poc08-litellm: LiteLLM v1.103.0, Presidio, guardrail-server pattern stand-in",
        "cases": len(rows),
        "stopped": done,
        "rate": round(done / len(rows), 3),
        "pii_mask_changes": sum(r["pii_mask_changes_it"] for r in rows),
        "rows": rows,
    }
    _write(out_dir, "redteam.json", summary)
    print(f"\nred team on the LiteLLM stack: {done}/{len(rows)} stopped")
    for r in rows:
        print(
            f"  {r['case']:40} {r['kind']:13} stopped={r['stopped']!s:5} "
            f"pii_mask_changes_it={r['pii_mask_changes_it']!s:5} code={r['error_code']}"
        )
    assert len(rows) == len(SCRIPT.REDTEAM_CASES)
    # A 5xx is the stack failing, not a case stopped: the run proved nothing.
    assert not [r["case"] for r in rows if r["http"] >= 500], rows
