"""PoC-7 exit criterion 2 (evaluator part): each new real evaluator adapter passes the same
contract suite as its fake (`EvaluatorPortContract`; the fake binds it in
`packages/chassis/tests/test_contracts_poc07.py`).

- `JudgeEvaluator` over the chassis's real `LiteLLMModel`, against the fake model server on a
  Unix socket with a scripted judge. The scores are scripted: this proves the wiring, the prompt,
  the strict parse, and the cost report, not the judge's quality.
- `EncoderEvaluator` against an in-process stub of the TEI `/predict` API. No encoder model runs
  here (Hugging Face answers 403 in this environment); the Mac run is in the gate cost note.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest
from chassis.adapters.evaluators import EncoderEvaluator, JudgeEvaluator
from chassis.adapters.litellm import LiteLLMModel
from chassis_contracts import EvaluatorPortContract
from poc07_evals_harness import JUDGE_ROUTE, serve_gate_backends, tei_stub
from poc_harness.sidecar_lane import Backends


class Outage(httpx.AsyncBaseTransport):
    """A transport that can be switched to fail like a dead upstream."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.down = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("scripted outage", request=request)
        return await self.inner.handle_async_request(request)


@pytest.fixture(scope="module")
def backends() -> Iterator[Backends]:
    with serve_gate_backends() as served:
        yield served


class TestPoc07EvalsJudgeEvaluator(EvaluatorPortContract):
    @pytest.fixture
    def outage(self, backends: Backends) -> Outage:
        return Outage(httpx.AsyncHTTPTransport(uds=backends.model_uds))

    @pytest.fixture
    def evaluator_port(self, outage: Outage) -> JudgeEvaluator:
        model = LiteLLMModel("http://model.invalid/v1", transport=outage)
        return JudgeEvaluator(
            model, route=JUDGE_ROUTE, price_in_per_mtok=0.15, price_out_per_mtok=0.6
        )

    @pytest.fixture
    def make_unavailable(self, outage: Outage) -> Callable[[], None]:
        return lambda: setattr(outage, "down", True)


class TestPoc07EvalsEncoderEvaluator(EvaluatorPortContract):
    @pytest.fixture
    def outage(self) -> Outage:
        return Outage(httpx.ASGITransport(app=tei_stub()))

    @pytest.fixture
    def evaluator_port(self, outage: Outage) -> EncoderEvaluator:
        return EncoderEvaluator("http://encoder.invalid", transport=outage)

    @pytest.fixture
    def make_unavailable(self, outage: Outage) -> Callable[[], None]:
        return lambda: setattr(outage, "down", True)


async def test_poc07_evals_both_evaluators_are_picked_by_spec_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 2: `spec.adapters.evaluator: judge | encoder` builds these classes from the
    environment, through `REGISTRY`; a swap is a config change."""
    from chassis.profiles import resolve

    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm.invalid/v1")
    monkeypatch.setenv("CHASSIS_ENCODER_URL", "http://encoder.invalid")
    judge = resolve("evaluator", "judge")
    encoder = resolve("evaluator", "encoder")
    assert isinstance(judge, JudgeEvaluator) and judge.route == "judge"
    assert isinstance(encoder, EncoderEvaluator)
    await judge.aclose()
    await encoder.aclose()
