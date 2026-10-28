"""The three bake-off tasks (smoke, simplifier, lookup) through `handle`, plus the wire rules:
event order and schema, the prompt, the route, the headers, the key, the metrics.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import jsonschema
import oai_support as s
import pytest
from echo_openai_agents import PROMPT_VERSION, SYSTEM_PROMPT


@pytest.fixture
async def stubs(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[s.Stubs]:
    async with s.running(monkeypatch) as stubs:
        yield stubs


async def test_smoke_ends_ok_after_a_delta(stubs: s.Stubs) -> None:
    events = await s.run(s.SMOKE)
    s.check_shape(events)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and "delta" in kinds
    assert kinds.index("delta") < kinds.index("end")
    assert events[-1] == {"schema_version": "0", "type": "end", "status": "ok"}
    assert events[0]["request_id"] == "req-1"
    assert s.answer(events) == "Hello."
    assert "tool_call" not in kinds


async def test_simplifier_keeps_the_facts(stubs: s.Stubs) -> None:
    events = await s.run(s.SIMPLIFIER)
    s.check_shape(events)
    text = s.answer(events)
    assert "2026" in text and "Acme" in text and "30" in text
    kinds = [e["type"] for e in events]
    assert kinds[:2] == ["start", "delta"] and kinds[-2:] == ["metrics", "end"]
    assert kinds.count("delta") > 1, "the reply streams in more than one delta"
    assert "tool_call" not in kinds


async def test_lookup_calls_both_tools_in_order_with_valid_arguments(stubs: s.Stubs) -> None:
    events = await s.run(s.LOOKUP)
    s.check_shape(events)
    calls = [e for e in events if e["type"] == "tool_call"]
    assert [(c["name"], c["arguments"]) for c in calls] == [
        ("glossary_lookup", {"term": "SLM"}),
        ("acronym_expand", {"acronym": "RAG"}),
    ]
    assert "small language model" in calls[0]["result"]["definition"]
    assert calls[1]["result"] == {"acronym": "RAG", "expansion": "retrieval-augmented generation"}
    assert all(c["call_id"] for c in calls) and len({c["call_id"] for c in calls}) == 2
    # The arguments are valid against the schema each tool advertises to the model.
    offered = {t["function"]["name"]: t["function"]["parameters"] for t in _tools(stubs)}
    assert set(offered) == s.TWO_TOOLS
    for call in calls:
        jsonschema.validate(call["arguments"], offered[call["name"]])
    text = s.answer(events)
    assert "small language model" in text and "retrieval-augmented generation" in text
    assert stubs.tool_calls == [
        ("glossary_lookup", {"term": "SLM"}),
        ("acronym_expand", {"acronym": "RAG"}),
    ], "each tool ran once on the MCP stub"
    kinds = [e["type"] for e in events]
    assert kinds.index("tool_call") < kinds.index("delta")
    assert kinds[-2:] == ["metrics", "end"]


def _tools(stubs: s.Stubs) -> list[dict[str, Any]]:
    return list(stubs.model_bodies[0].get("tools") or [])


async def test_metrics_sum_every_model_call(stubs: s.Stubs) -> None:
    events = await s.run(s.LOOKUP)
    metrics = [e for e in events if e["type"] == "metrics"]
    assert len(metrics) == 1
    assert len(stubs.model_bodies) == 3
    # Three model calls at the fake server's default usage (10 in, 5 out) each.
    assert (metrics[0]["input_tokens"], metrics[0]["output_tokens"]) == (30, 15)
    assert metrics[0]["model_route"] == "big-default"


async def test_system_prompt_and_input_are_separate_messages(stubs: s.Stubs) -> None:
    await s.run("simplify: secret facts here")
    body = stubs.model_bodies[-1]
    messages = body["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert "secret facts" not in messages[0]["content"]
    assert messages[1]["content"] == "simplify: secret facts here"
    assert body["model"] == "big-default" and body["stream"] is True
    assert PROMPT_VERSION == "simplifier-v1"


async def test_it_uses_chat_completions_not_the_responses_api(stubs: s.Stubs) -> None:
    await s.run(s.SMOKE)
    paths = {r.url.path for r in stubs.model.requests}
    assert paths == {"/v1/chat/completions"}


async def test_the_second_model_call_carries_the_tool_loop(stubs: s.Stubs) -> None:
    await s.run(s.LOOKUP)
    second = stubs.model_bodies[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[2]["tool_calls"][0]["function"]["name"] == "glossary_lookup"
    assert second[3]["tool_call_id"] == second[2]["tool_calls"][0]["id"]


async def test_route_comes_from_ctx(stubs: s.Stubs) -> None:
    await s.run(s.SMOKE, {**s.CTX, "model_route": "local-small"})
    assert stubs.model_bodies[-1]["model"] == "local-small"
    events = await s.run(s.SMOKE, {**s.CTX, "model_route": None})
    assert stubs.model_bodies[-1]["model"] == "big-default"
    assert next(e for e in events if e["type"] == "metrics")["model_route"] == "big-default"


async def test_every_model_and_mcp_request_carries_the_traceparent(stubs: s.Stubs) -> None:
    await s.run(s.LOOKUP)
    assert len(stubs.model.requests) == 3
    assert stubs.tools.requests, "the MCP stub saw no request"
    for request in stubs.all_requests():
        assert request.headers.get("traceparent") == s.TRACEPARENT, request.url


async def test_no_traceparent_header_when_ctx_has_none(stubs: s.Stubs) -> None:
    await s.run(s.LOOKUP, {k: v for k, v in s.CTX.items() if k != "traceparent"})
    assert stubs.all_requests()
    assert all("traceparent" not in r.headers for r in stubs.all_requests())


async def test_the_remote_token_is_the_bearer_on_every_request(
    stubs: s.Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHASSIS_API_TOKEN", s.TOKEN)
    events = await s.run(s.LOOKUP)
    assert events[-1]["type"] == "end"
    assert stubs.model.requests and stubs.tools.requests
    for request in stubs.all_requests():
        assert request.headers.get("authorization") == f"Bearer {s.TOKEN}", request.url
        assert request.headers.get("traceparent") == s.TRACEPARENT, request.url
    assert s.PLANTED not in " ".join(str(r.headers) for r in stubs.all_requests())


async def test_an_empty_remote_token_is_no_token(
    stubs: s.Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHASSIS_API_TOKEN", "")
    await s.run(s.LOOKUP)
    assert stubs.all_requests()
    assert all("authorization" not in r.headers for r in stubs.all_requests())


async def test_a_planted_openai_api_key_never_leaves_the_process(
    stubs: s.Stubs, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    events = await s.run(s.LOOKUP)
    requests = stubs.all_requests()
    assert requests
    for request in requests:
        assert "authorization" not in request.headers, request.url
        assert all(s.PLANTED not in v for v in request.headers.values()), request.url
        assert s.PLANTED not in str(request.url)
        assert s.PLANTED.encode() not in request.content
    assert s.PLANTED not in repr(events)
    assert s.PLANTED not in caplog.text


def test_the_workload_reads_no_api_key_variable_and_never_imports_chassis() -> None:
    import importlib
    import inspect
    import re

    names = ("echo_openai_agents.handle", "echo_openai_agents.mapping", "echo_openai_agents.tools")
    for name in names:
        source = inspect.getsource(importlib.import_module(name))
        reads = re.findall(r"(?:environ|getenv)[^\n]*_API_KEY", source)
        assert not reads, (name, reads)
        assert not re.search(r"^\s*(?:import|from)\s+chassis\b", source, re.M), name


async def test_tool_endpoint_down_goes_on_with_no_tools(
    stubs: s.Stubs, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from echo_openai_agents import tools

    monkeypatch.setattr(s.tools_module, "transport", httpx2.MockTransport(s.refuse))
    before = tools.list_failures
    with caplog.at_level(logging.WARNING):
        events = await s.run(s.SMOKE)
    s.check_shape(events)
    assert events[-1]["type"] == "end" and s.answer(events) == "Hello."
    assert tools.list_failures == before + 1
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert not stubs.model_bodies[-1].get("tools"), "no tools are offered"


def test_budget_timeout_is_used() -> None:
    assert s.handle_module.timeout_s({"budget": {"timeout_ms": 1500}}) == 1.5
    assert s.handle_module.timeout_s({}) == s.handle_module.DEFAULT_TIMEOUT_S


async def test_a_slow_consumer_is_not_cancelled_by_the_budget(stubs: s.Stubs) -> None:
    """No whole-run deadline across the `yield`s: the connector owns it."""
    ctx = {**s.CTX, "budget": {"max_tokens": 2000, "timeout_ms": 300}}
    events: list[dict[str, Any]] = []
    async for ev in s.handle({"text": s.SIMPLIFIER, "data": {}}, ctx):
        events.append(ev)
        if ev["type"] == "delta" and len(events) == 2:
            await asyncio.sleep(0.5)
    s.check_shape(events)
    assert events[-1]["type"] == "end", events[-1]


async def test_two_runs_share_no_state(stubs: s.Stubs) -> None:
    """The SDK keeps no session or history between runs: the second run's model call carries
    only its own system and user messages, and the same input gives the same events."""
    first = await s.run(s.LOOKUP)
    again = await s.run(s.LOOKUP)
    await s.run(s.SMOKE)
    assert [e["type"] for e in first] == [e["type"] for e in again]
    assert s.answer(first) == s.answer(again)
    assert [m["role"] for m in stubs.model_bodies[-1]["messages"]] == ["system", "user"]
    assert len(stubs.model_bodies) == 7
