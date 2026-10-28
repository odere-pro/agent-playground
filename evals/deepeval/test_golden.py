"""DeepEval on the PoC-7 golden set, through the chassis's `POST /v1/run` (a black box over HTTP).

Nothing here names an engine: the launcher sets the variables per engine.
  CHASSIS_URL   the chassis under test, for example http://127.0.0.1:8080
  JUDGE_URL     the OpenAI-compatible judge, ends in /v1 (the fake model server in the trial)
  GOLDEN_CASES  the golden set (default: the packaged cases.yaml)

Run: uv run python -m bakeoff.evaltrial --tool deepeval --engine echo-python --out DIR
It runs `deepeval test run` in an isolated `uv run --no-project --with deepeval==4.3.2`, so this
file imports only deepeval, httpx, pytest, and yaml. Not collected by `make test` (testpaths).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from deepeval import assert_test
from deepeval.metrics import BaseMetric, GEval
from deepeval.metrics.g_eval import Rubric
from deepeval.models import LocalModel
from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = Path(
    os.environ.get("GOLDEN_CASES", ROOT / "packages/bakeoff/src/bakeoff/golden/cases.yaml")
)
CASES: list[dict[str, Any]] = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))["cases"]
CHASSIS_URL = os.environ.get("CHASSIS_URL", "http://127.0.0.1:8080").rstrip("/")
JUDGE_URL = os.environ.get("JUDGE_URL", "http://127.0.0.1:8081/v1")


class _Deterministic(BaseMetric):
    """A metric with no model: score 1 or 0, threshold 1."""

    label = "deterministic"

    def __init__(self) -> None:
        self.threshold = 1.0
        self.async_mode = False
        self.include_reason = True

    def check(self, test_case: LLMTestCase) -> str:
        raise NotImplementedError

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        problem = self.check(test_case)
        self.score = 0.0 if problem else 1.0
        self.reason = problem or "ok"
        self.success = self.score >= self.threshold
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)

    @property
    def __name__(self) -> str:  # type: ignore[override]
        return self.label


class Contains(_Deterministic):
    def __init__(self, needle: str) -> None:
        super().__init__()
        self.needle = needle
        self.label = f"contains:{needle}"

    def check(self, test_case: LLMTestCase) -> str:
        found = self.needle.casefold() in (test_case.actual_output or "").casefold()
        return "" if found else f"output lacks {self.needle!r}"


class Excludes(_Deterministic):
    def __init__(self, word: str) -> None:
        super().__init__()
        self.word = word
        self.label = f"banned:{word}"

    def check(self, test_case: LLMTestCase) -> str:
        found = self.word.casefold() in (test_case.actual_output or "").casefold()
        return f"output holds banned {self.word!r}" if found else ""


class ToolCallsInOrder(_Deterministic):
    label = "tools"

    def __init__(self, expected: list[dict[str, Any]]) -> None:
        super().__init__()
        self.expected = expected

    def check(self, test_case: LLMTestCase) -> str:
        got = [
            {"name": c.name, "arguments": c.input_parameters or {}}
            for c in test_case.tools_called or []
        ]
        return "" if got == self.expected else f"tool calls {got}, expected {self.expected}"


def judge() -> LocalModel:
    return LocalModel(model="fake-judge", base_url=JUDGE_URL, api_key="not-a-key")


def rubric_metric(case: dict[str, Any]) -> GEval:
    return GEval(
        name="rubric",
        evaluation_params=[SingleTurnParams.ACTUAL_OUTPUT],
        evaluation_steps=[case["rubric"]],
        rubric=[
            Rubric(score_range=(0, 0), expected_outcome="The answer misses the rubric."),
            Rubric(score_range=(1, 1), expected_outcome="The answer meets the rubric."),
        ],
        model=judge(),
        threshold=0.5,
        async_mode=False,
    )


def run(text: str) -> dict[str, Any]:
    response = httpx.post(
        f"{CHASSIS_URL}/v1/run",
        json={"input": {"text": text}, "stream": False},
        timeout=120.0,
        trust_env=False,
    )
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    return data


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden(case: dict[str, Any]) -> None:
    data = run(case["input"])
    output = data.get("output") or {}
    calls = [
        ToolCall(name=c["name"], input_parameters=c.get("arguments") or {})
        for c in output.get("tool_calls") or []
    ]
    test_case = LLMTestCase(
        name=case["id"],
        input=case["input"],
        actual_output=str(output.get("text") or ""),
        tools_called=calls,
        additional_metadata={"case_id": case["id"], "trace_id": data.get("trace_id")},
    )
    expect = case["expect"]
    metrics: list[BaseMetric] = [Contains(n) for n in expect["contains"]]
    metrics += [Excludes(w) for w in expect.get("banned") or []]
    metrics.append(ToolCallsInOrder(expect["tool_calls"]))
    metrics.append(rubric_metric(case))
    assert_test(test_case, metrics, run_async=False)
