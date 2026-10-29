"""`chassis_contracts.redteam.live`: the red-team set against a running chassis with a real engine
(PoC-8). The script, the tool pages, the stop rules, and the baseline. Offline: no socket."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from chassis_contracts.redteam import load_cases
from chassis_contracts.redteam.live import (
    BLOCKED,
    TOOL,
    Evidence,
    LiveOutcome,
    check_baseline,
    model_script,
    record_baseline,
    request_body,
    stopped,
    tool_pages,
    trigger,
)

CASES = {c.id: c for c in load_cases()}


def _rule(script: dict[str, Any], match: str) -> dict[str, Any]:
    return next(r for r in script["rules"] if r.get("match") == match)


def test_redteam_live_script_answers_each_case_through_the_engine() -> None:
    script = model_script(list(CASES.values()))
    for case in CASES.values():
        if case.kind in ("input", "workload_env"):
            assert all(r.get("match") != trigger(case) for r in script["rules"])
            continue
        rule = _rule(script, trigger(case))
        if case.kind in ("output", "output_stream"):
            assert rule["reply"] == case.payload
        elif case.kind == "tool_output":
            assert rule["tool_call"] == {"name": TOOL, "arguments": {"page": case.id}}
        else:
            assert rule["tool_call"] == {"name": TOOL, "arguments": case.tool_arguments}
    assert not any(r.get("after_tool") for r in script["rules"])


def test_redteam_live_request_body_sends_the_payload_or_the_trigger() -> None:
    case = CASES["input-01-ignore-previous"]
    assert request_body(case) == {"input": {"text": case.payload}, "stream": False}
    stream = CASES["secret-04-streamed-answer-has-key"]
    assert request_body(stream) == {"input": {"text": trigger(stream)}, "stream": True}


def test_redteam_live_tool_pages_hold_each_tool_output_payload() -> None:
    pages = tool_pages(list(CASES.values()))
    tool_cases = [c for c in CASES.values() if c.kind == "tool_output"]
    assert pages == {c.id: c.payload for c in tool_cases}
    assert len(pages["tool-02-past-512-tokens"].split()) > 900, "the filler is in the page"


def _ev(**kw: Any) -> Evidence:
    return Evidence(**{"text": "", "error_code": None, "model_bodies": [], "tool_calls": [], **kw})


def test_redteam_live_stop_rules() -> None:
    inp = CASES["input-01-ignore-previous"]
    assert stopped(inp, _ev(error_code=BLOCKED))
    assert not stopped(inp, _ev(text="done"))

    out = CASES["secret-03-answer-has-key"]
    assert stopped(out, _ev(text="I cannot share that."))
    assert not stopped(out, _ev(text=out.payload))

    tool = CASES["tool-01-ignore-in-page"]
    called = [{"page": tool.id, "text": ""}]
    leaked = [{"messages": [{"role": "tool", "content": json.dumps({"text": tool.payload})}]}]
    assert stopped(tool, _ev(tool_calls=called, error_code="tool_error"))
    assert not stopped(tool, _ev(tool_calls=called, model_bodies=leaked))
    assert not stopped(tool, _ev()), "a tool that never ran proves nothing: not stopped"

    args = CASES["exfil-03-write-tool-gets-secret"]
    assert stopped(args, _ev())
    assert not stopped(args, _ev(tool_calls=[{"page": "", **args.tool_arguments}]))


def _outcomes(engine: str, lane: str, missed: set[str]) -> list[LiveOutcome]:
    return [
        LiveOutcome(c.id, c.category, c.kind, "litellm", engine, lane, c.id not in missed)
        for c in CASES.values()
    ]


def test_redteam_live_baseline_records_and_checks_each_target() -> None:
    first = _outcomes("echo-python", "sidecar", {"input-05-french"})
    baseline = record_baseline(first, skipped={"kagent-adk": "kind only"})
    target = baseline["configs"]["litellm"]["echo-python/sidecar"]
    assert target["stopped"] == len(CASES) - 1
    assert target["rate"] == round((len(CASES) - 1) / len(CASES), 3)
    assert "input-05-french" not in target["must_stop"]
    assert check_baseline(baseline, first) == [("litellm echo-python/sidecar", [])]

    worse = _outcomes("echo-python", "sidecar", {"input-05-french", "input-01-ignore-previous"})
    [(_, problems)] = check_baseline(baseline, worse)
    assert any("block rate" in p for p in problems)
    assert any("input-01-ignore-previous" in p for p in problems)

    [(_, missing)] = check_baseline(baseline, [])
    assert missing == ["no result for this target"]
    stranger = [replace(o, engine="echo-new") for o in first]
    results = dict(check_baseline(baseline, first + stranger))
    assert results["litellm echo-new/sidecar"] == ["no baseline for this target; record one"]


def test_redteam_live_check_filters_by_engine_and_lane() -> None:
    both = _outcomes("echo-python", "sidecar", set()) + _outcomes("echo-python", "remote", set())
    baseline = record_baseline(both, skipped={})
    only = check_baseline(baseline, both[: len(CASES)], engines={"echo-python"}, lanes={"sidecar"})
    assert only == [("litellm echo-python/sidecar", [])]


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_redteam_live_every_case_kind_has_a_stop_rule(case_id: str) -> None:
    stopped(CASES[case_id], _ev())
