"""The task checks, percentiles, and line counts. Offline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from bakeoff.lines import count_source
from bakeoff.measure import JsonlCalls, call_summary, view_from_events
from bakeoff.stats import mean, percentile
from bakeoff.tasks import RunView, check_task, get_tasks, tool_calls_pass

GOOD_CALLS = (
    {"name": "glossary_lookup", "arguments": {"term": "SLM"}},
    {"name": "acronym_expand", "arguments": {"acronym": "RAG"}},
)
ANSWER = "SLM means a small language model. RAG stands for retrieval-augmented generation."


def test_smoke_needs_a_delta_before_a_good_end() -> None:
    assert check_task("smoke", RunView("ok", "Hello.", deltas=1)).ok
    assert not check_task("smoke", RunView("ok", "", deltas=0)).ok
    assert not check_task("smoke", RunView("error", "Hello.", deltas=1)).ok
    assert check_task("smoke", RunView("ok", "Hello.")).ok
    assert not check_task("smoke", RunView("ok", "")).ok


def test_simplifier_keeps_every_fact() -> None:
    text = "Acme put out the SLM in 2026. It cut costs by 30 percent."
    assert check_task("simplifier", RunView("ok", text)).ok
    check = check_task("simplifier", RunView("ok", "Acme cut costs in 2026."))
    assert not check.ok
    assert "30" in check.reason


def test_lookup_wants_both_tools_in_order_and_both_expansions() -> None:
    assert check_task("lookup", RunView("ok", ANSWER, GOOD_CALLS)).ok
    assert check_task("lookup", RunView("ok", ANSWER, GOOD_CALLS)).tools_ok is True
    assert check_task("lookup", RunView("ok", ANSWER, GOOD_CALLS[::-1])).tools_ok is False
    assert check_task("lookup", RunView("ok", ANSWER, GOOD_CALLS[:1])).tools_ok is False
    no_expansion = check_task("lookup", RunView("ok", "SLM is small.", GOOD_CALLS))
    assert not no_expansion.ok
    assert no_expansion.tools_ok is True


def test_tool_arguments_must_match_the_schema_and_the_task() -> None:
    wrong_key = (
        {"name": "glossary_lookup", "arguments": {"word": "SLM"}},
        GOOD_CALLS[1],
    )
    extra_key = (
        {"name": "glossary_lookup", "arguments": {"term": "SLM", "x": 1}},
        GOOD_CALLS[1],
    )
    other_value = (
        {"name": "glossary_lookup", "arguments": {"term": "LLM"}},
        GOOD_CALLS[1],
    )
    not_object = ({"name": "glossary_lookup", "arguments": "SLM"}, GOOD_CALLS[1])
    assert tool_calls_pass(GOOD_CALLS) == (True, "")
    for calls in (wrong_key, extra_key, other_value, not_object):
        assert not tool_calls_pass(calls)[0]


def test_unknown_task_names_are_refused() -> None:
    assert [t.name for t in get_tasks(["smoke", "lookup"])] == ["smoke", "lookup"]
    with pytest.raises(ValueError, match="unknown task"):
        get_tasks(["nope"])
    with pytest.raises(ValueError, match="unknown task"):
        check_task("nope", RunView("ok", ""))


def test_percentiles_interpolate() -> None:
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([5, 1, 4, 2, 3], 95) == pytest.approx(4.8)
    assert percentile([7], 95) == 7
    assert percentile([], 50) is None
    assert percentile([1, 2], 50) == 1.5
    assert mean([1, 3]) == 2
    assert mean([]) is None
    with pytest.raises(ValueError, match="between 0 and 100"):
        percentile([1], 101)


def test_a_stream_folds_into_a_view() -> None:
    events: list[dict[str, Any]] = [
        {"type": "start"},
        {"type": "tool_call", "name": "glossary_lookup", "arguments": {"term": "SLM"}},
        {"type": "delta", "text": "Hel"},
        {"type": "delta", "text": "lo."},
        {"type": "metrics", "input_tokens": 1},
        {"type": "end", "status": "ok"},
    ]
    view = view_from_events(events)
    assert (view.status, view.text, view.deltas, len(view.tool_calls)) == ("ok", "Hello.", 2, 1)
    assert view_from_events([{"type": "start"}, {"type": "error", "code": "x"}]).status == "error"
    assert view_from_events([{"type": "delta", "text": "a"}]).status == "error"  # no end


def test_a_call_is_summed_by_message_bytes_and_body_keys() -> None:
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    summary = call_summary(body)
    assert summary["body_keys"] == ["messages", "model", "stream"]
    assert summary["prompt_bytes"] == len(b'[{"role":"user","content":"hi"}]')


def test_a_model_log_is_read_by_line(tmp_path: Path) -> None:
    log = tmp_path / "model.jsonl"
    source = JsonlCalls(log)
    assert source.mark() == 0
    log.write_text('{"model": "a", "messages": []}\n{"body": {"model": "b"}}\n')
    assert source.mark() == 2
    assert [b["model"] for b in source.since(1)] == ["b"]


def test_code_lines_skip_blanks_comments_and_docstrings() -> None:
    python = '"""doc\nmore"""\n\n# note\nx = 1\n\ndef f():\n    """d"""\n    return x\n'
    count = count_source("a.py", python)
    assert (count.total, count.blank, count.comment, count.code) == (9, 2, 4, 3)
    ts = "// c\n/* a\n b */\nconst x = 1;\n\nexport {};\n"
    count = count_source("a.ts", ts)
    assert (count.comment, count.code) == (3, 2)
