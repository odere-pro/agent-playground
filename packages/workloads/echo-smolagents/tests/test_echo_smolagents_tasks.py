"""The three PoC-6 tasks (docs/plans/2026-10-09-poc-06-bake-off.md, "Task spec") through `handle`,
against the fake model on `scripts/smolagents.yaml` and the two-tool MCP stub, over Unix sockets.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from echo_smolagents_support import (
    CTX,
    LOOKUP,
    RAG_EXPANSION,
    SIMPLIFIER,
    SLM_DEFINITION,
    SMOKE,
    Stubs,
    check_shape,
    handle_module,
    run,
    stub_servers,
    text_of,
    tools_module,
)


@pytest.fixture
async def stubs(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Stubs]:
    async with stub_servers(monkeypatch) as s:
        yield s


def _kinds(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


async def test_smoke_task_ends_ok_after_a_delta(stubs: Stubs) -> None:
    events = await run(SMOKE)
    check_shape(events)
    assert _kinds(events) == ["start", "delta", "metrics", "end"]
    assert events[0]["request_id"] == "req-1"
    assert text_of(events) == "Hello."
    assert events[-1] == {"schema_version": "0", "type": "end", "status": "ok"}


async def test_simplifier_task_keeps_the_facts(stubs: Stubs) -> None:
    events = await run(SIMPLIFIER)
    check_shape(events)
    answer = text_of(events)
    assert "2026" in answer and "Acme" in answer and "30" in answer
    assert events[-1]["status"] == "ok"
    assert not [e for e in events if e["type"] == "tool_call"], "no tool is needed"


async def test_lookup_task_calls_both_tools_in_order(stubs: Stubs) -> None:
    events = await run(LOOKUP)
    check_shape(events)
    assert _kinds(events) == ["start", "tool_call", "tool_call", "delta", "metrics", "end"]
    first, second = (e for e in events if e["type"] == "tool_call")
    assert (first["name"], first["arguments"]) == ("glossary_lookup", {"term": "SLM"})
    assert first["result"] == {"term": "SLM", "definition": SLM_DEFINITION}
    assert (second["name"], second["arguments"]) == ("acronym_expand", {"acronym": "RAG"})
    assert second["result"] == {"acronym": "RAG", "expansion": RAG_EXPANSION}
    assert first["call_id"] != second["call_id"]
    answer = text_of(events)
    assert "small language model" in answer and "retrieval-augmented generation" in answer
    assert stubs.tool_calls == [
        ("glossary_lookup", {"term": "SLM"}),
        ("acronym_expand", {"acronym": "RAG"}),
    ]


async def test_the_tool_arguments_are_valid_against_the_tool_schemas(stubs: Stubs) -> None:
    loaded = await tools_module.load_tools({}, 5.0, lambda *a: None, lambda e: None)
    by_name = {t.name: t for t in loaded}
    assert set(by_name) == {"glossary_lookup", "acronym_expand", "explode"}
    assert set(by_name["glossary_lookup"].inputs) == {"term"}
    assert by_name["glossary_lookup"].inputs["term"]["type"] == "string"
    assert set(by_name["acronym_expand"].inputs) == {"acronym"}
    events = await run(LOOKUP)
    for call in (e for e in events if e["type"] == "tool_call"):
        assert set(call["arguments"]) == set(by_name[call["name"]].inputs)


async def test_metrics_sum_the_tokens_of_every_model_call(stubs: Stubs) -> None:
    events = await run(LOOKUP)
    metrics = next(e for e in events if e["type"] == "metrics")
    calls = stubs.bodies()
    assert len(calls) == 3, "glossary call, acronym call, final answer"
    # The fake server reports 10 prompt and 5 completion tokens for each call.
    assert (metrics["input_tokens"], metrics["output_tokens"]) == (30, 15)
    assert metrics["model_route"] == "big-default" and metrics["attempt"] == 1


async def test_the_model_route_is_the_context_route(stubs: Stubs) -> None:
    await run(SMOKE, {**CTX, "model_route": "local-small"})
    assert {b["model"] for b in stubs.bodies()} == {"local-small"}


async def test_the_input_text_is_its_own_user_message_not_the_system_prompt(
    stubs: Stubs,
) -> None:
    text = f"{SIMPLIFIER} MARKER-7731"
    await run(text)
    first = stubs.bodies()[0]["messages"]
    system = [m for m in first if m["role"] == "system"]
    users = [m for m in first if m["role"] == "user"]
    assert system and "MARKER-7731" not in str(system)
    assert "Rewrite in plain words" in str(system), "the fixed instructions are system-side"
    assert any("MARKER-7731" in str(m["content"]) for m in users)


async def test_the_hooks_exist(stubs: Stubs) -> None:
    assert handle_module.transport is stubs.model
    assert tools_module.transport is stubs.tools
