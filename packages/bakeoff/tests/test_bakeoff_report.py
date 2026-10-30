"""The report rendering and the smoke line. Offline."""

from __future__ import annotations

import json

from bakeoff.measure import TaskResult
from bakeoff.report import render_json, render_markdown
from bakeoff.runner import Cell, smoke_line

META = {"tasks": ["smoke", "lookup"], "repeat": 2, "model": "fake", "generated": "now"}


def _result(task: str, passed: int, runs: int = 2) -> TaskResult:
    return TaskResult(
        task,
        runs=runs,
        passed=passed,
        tools_passed=passed,
        latencies_ms=[10.0, 20.0],
        ttft_ms=[5.0, 7.0],
        input_tokens=[10.0, 10.0],
        output_tokens=[5.0, 5.0],
        model_calls=[{"prompt_bytes": 100, "body_keys": ["messages", "model"]}],
        failures=[] if passed == runs else ["status error"],
    )


def _cells() -> list[Cell]:
    return [
        Cell(
            "echo-python",
            "sidecar",
            "ok",
            code_lines=243,
            mapping_files=["a/handle.py", "a/tools.py"],
            tasks={"smoke": _result("smoke", 2), "lookup": _result("lookup", 1)},
        ),
        Cell("kagent-adk", "remote", "skip", "runs only on kind (poc06-kind.yml)"),
    ]


def test_the_markdown_has_a_row_per_task_and_the_columns() -> None:
    text = render_markdown(_cells(), META)
    assert "| Engine | Lane | Task | Pass | Tool calls | p50 ms | p95 ms | TTFT p50 ms |" in text
    assert "| echo-python | sidecar | smoke | 2/2 | - | 15.0 | 19.5 | 6.0 | 10 | 5 | 100 |" in text
    assert "| echo-python | sidecar | lookup | 1/2 | 1/2 |" in text
    assert "| kagent-adk | remote | SKIP | runs only on kind (poc06-kind.yml) |" in text
    assert "| echo-python | 243 | handle.py, tools.py |" in text
    assert "echo-python sidecar lookup: status error" in text


def test_the_json_carries_the_numbers() -> None:
    data = json.loads(render_json(_cells(), META))
    cell = data["cells"][0]
    assert data["meta"]["repeat"] == 2
    assert cell["mapping_code_lines"] == 243
    smoke = cell["tasks"]["smoke"]
    assert smoke["pass_rate"] == 1.0
    assert smoke["latency_p50_ms"] == 15.0
    assert smoke["tool_call_pass_rate"] is None
    assert cell["tasks"]["lookup"]["tool_call_pass_rate"] == 0.5
    assert cell["tasks"]["lookup"]["model_calls"][0]["prompt_bytes"] == 100
    assert data["cells"][1]["status"] == "skip"


def test_the_smoke_line_is_pass_fail_or_skip() -> None:
    ok = Cell("echo-python", "inprocess", "ok", tasks={"smoke": _result("smoke", 2)})
    assert smoke_line(ok).split() == ["PASS", "echo-python", "inprocess"]
    bad = Cell("echo-python", "inprocess", "ok", tasks={"smoke": _result("smoke", 0)})
    assert smoke_line(bad).startswith("FAIL")
    assert "status error" in smoke_line(bad)
    assert bad.status == "fail"
    down = Cell("echo-python", "remote", "fail", "chassis exited (3)")
    assert smoke_line(down).startswith("FAIL")
    skip = smoke_line(_cells()[1])
    assert skip.split(maxsplit=3) == [
        "SKIP",
        "kagent-adk",
        "remote",
        "runs only on kind (poc06-kind.yml)",
    ]


GOLDEN_FULL = """\
# Bake-off results

Tasks: smoke, lookup. Repeat: 2. Model: fake. Generated: now.

| Engine | Lane | Task | Pass | Tool calls | p50 ms | p95 ms | TTFT p50 ms | In tok | Out tok \
| Prompt bytes per call | Request body keys |
| ------ | ---- | ---- | ---- | ---------- | ------ | ------ | ----------- | ------ | ------- \
| --------------------- | ----------------- |
| echo-python | sidecar | smoke | 2/2 | - | 15.0 | 19.5 | 6.0 | 10 | 5 | 100 | messages,model |
| echo-python | sidecar | lookup | 1/2 | 1/2 | 15.0 | 19.5 | 6.0 | 10 | 5 | 100 | messages,model |

## Not run

| Engine | Lane | Status | Reason |
| --- | --- | --- | --- |
| kagent-adk | remote | SKIP | runs only on kind (poc06-kind.yml) |
| echo-x | inprocess | FAIL | chassis exited (3) |

## Mapping size

| Engine | Code lines | Files |
| ------ | ---------- | ----- |
| echo-python | 243 | handle.py, tools.py |
| kagent-adk | - | - |
| echo-x | - | - |

## Failures

- echo-python sidecar lookup: status error
"""

GOLDEN_CLEAN = """\
# Bake-off results

Tasks: smoke, lookup. Repeat: 2. Model: fake. Generated: now.

| Engine | Lane | Task | Pass | Tool calls | p50 ms | p95 ms | TTFT p50 ms | In tok | Out tok \
| Prompt bytes per call | Request body keys |
| ------ | ---- | ---- | ---- | ---------- | ------ | ------ | ----------- | ------ | ------- \
| --------------------- | ----------------- |
| echo-python | inprocess | smoke | 1/1 | - | - | - | - | - | - | - | - |

## Mapping size

| Engine | Code lines | Files |
| ------ | ---------- | ----- |
| echo-python | - | - |
"""


def test_the_markdown_with_every_section_matches_the_golden_text() -> None:
    extra = [
        Cell("echo-python", "remote", "ok", code_lines=243, mapping_files=["a/handle.py"]),
        Cell("echo-x", "inprocess", "fail", "chassis exited (3)"),
    ]
    assert render_markdown([*_cells(), *extra], META) == GOLDEN_FULL


def test_the_markdown_of_a_clean_run_has_no_not_run_or_failures_section() -> None:
    empty = TaskResult(
        "smoke",
        runs=1,
        passed=1,
        tools_passed=1,
        latencies_ms=[],
        ttft_ms=[],
        input_tokens=[],
        output_tokens=[],
        model_calls=[],
        failures=[],
    )
    cell = Cell("echo-python", "inprocess", "ok", tasks={"smoke": empty})
    assert render_markdown([cell], META) == GOLDEN_CLEAN
