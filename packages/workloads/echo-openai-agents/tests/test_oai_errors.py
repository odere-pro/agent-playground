"""Every error code of `handle`, the 4th-round limit, and that tracing is off. A failure is one
`error` event after `start`, never an exception, and the workload does not retry.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import oai_support as s
import pytest
from fake_model_server import Script
from fake_model_server.script import ErrorSpec, Rule, ToolCallSpec


@pytest.fixture
async def stubs(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[s.Stubs]:
    async with s.running(monkeypatch) as stubs:
        yield stubs


def _only_error(events: list[dict[str, Any]], code: str, *, retryable: bool) -> dict[str, Any]:
    s.check_shape(events)
    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "error", events[-1]
    error = events[-1]
    assert error["code"] == code and error["retryable"] is retryable, error
    return error


async def test_http_500_is_a_retryable_error_and_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = Script(rules=[Rule(match="boom", error=ErrorSpec(status=500, message="scripted"))])
    async with s.running(monkeypatch, script=script) as own:
        events = await s.run("boom")
        error = _only_error(events, "http_500", retryable=True)
        assert "scripted" in error["message"]
        assert [e["type"] for e in events] == ["start", "error"]
        assert len(own.model.requests) == 1, "the workload does not retry; the chassis does"


async def test_http_4xx_is_not_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script(rules=[Rule(match="nope", error=ErrorSpec(status=429, message="slow down"))])
    async with s.running(monkeypatch, script=script) as own:
        _only_error(await s.run("nope"), "http_429", retryable=False)
        assert len(own.model.requests) == 1


async def test_connect_error_is_retryable(stubs: s.Stubs, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(s.handle_module, "transport", httpx2.MockTransport(s.refuse))
    events = await s.run(s.SMOKE)
    _only_error(events, "connect_error", retryable=True)
    assert [e["type"] for e in events] == ["start", "error"]


async def test_timeout_is_retryable(stubs: s.Stubs, monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow model", request=request)

    monkeypatch.setattr(s.handle_module, "transport", httpx2.MockTransport(hang))
    _only_error(await s.run(s.SMOKE), "timeout", retryable=True)


class Stalling(httpx2.AsyncBaseTransport):
    """A model that never answers. It waits the per-call read timeout the client sets on the
    request, as a socket transport does, then raises `ReadTimeout` (`ASGITransport` sets none)."""

    def __init__(self) -> None:
        self.timeouts: list[float] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        read = float(request.extensions["timeout"]["read"])
        self.timeouts.append(read)
        await asyncio.sleep(read)
        raise httpx2.ReadTimeout("no answer within the per-call timeout", request=request)


async def test_a_stalled_model_hits_the_budget_timeout(
    stubs: s.Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    stalling = Stalling()
    monkeypatch.setattr(s.handle_module, "transport", stalling)
    ctx = {**s.CTX, "budget": {"max_tokens": 2000, "timeout_ms": 200}}
    async with asyncio.timeout(5):
        events = await s.run(s.SMOKE, ctx)
    _only_error(events, "timeout", retryable=True)
    assert stalling.timeouts == [0.2], "the per-call timeout is the run budget"


async def test_a_reply_that_is_not_a_completion_stream_is_bad_response(
    stubs: s.Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def garbage(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"data: {not json\n\ndata: [DONE]\n\n",
        )

    monkeypatch.setattr(s.handle_module, "transport", httpx2.MockTransport(garbage))
    _only_error(await s.run(s.SMOKE), "bad_response", retryable=False)


async def test_tool_arguments_that_are_not_json_are_bad_response(
    stubs: s.Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    call: dict[str, Any] = {
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {"name": "glossary_lookup", "arguments": "{not json"},
    }

    def bad_args(request: httpx2.Request) -> httpx2.Response:
        chunks: list[dict[str, Any]] = [
            {"role": "assistant", "content": ""},
            {"tool_calls": [call]},
        ]
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=_sse(chunks, "tool_calls")
        )

    monkeypatch.setattr(s.handle_module, "transport", httpx2.MockTransport(bad_args))
    _only_error(await s.run(s.LOOKUP), "bad_response", retryable=False)
    assert stubs.tool_calls == [], "no tool ran on arguments that are not JSON"


def _sse(deltas: list[dict[str, Any]], finish: str = "stop") -> bytes:
    frames = []
    for i, delta in enumerate(deltas):
        done = i == len(deltas) - 1
        payload = {
            "id": "chatcmpl-x",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish if done else None}],
        }
        frames.append(f"data: {json.dumps(payload)}\n\n")
    frames.append("data: [DONE]\n\n")
    return "".join(frames).encode()


async def test_an_unexpected_failure_is_model_error(
    stubs: s.Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise RuntimeError("something odd")

    monkeypatch.setattr(s.handle_module, "transport", httpx2.MockTransport(broken))
    error = _only_error(await s.run(s.SMOKE), "model_error", retryable=False)
    assert "something odd" in error["message"]


# --- Tool failures and the tool loop ------------------------------------------------------------


async def test_a_tool_that_fails_is_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """`note_write` without an idempotency key is an `isError` result on the fake MCP server."""
    script = Script(
        rules=[
            Rule(match="note:", tool_call=ToolCallSpec(name="note_write", arguments={"text": "x"}))
        ]
    )
    async with s.running(monkeypatch, script=script, tools=frozenset({"note_write"})) as own:
        events = await s.run("note: remember this")
        error = _only_error(events, "tool_error", retryable=False)
        assert "note_write" in error["message"]
        assert [e["type"] for e in events] == ["start", "error"]
        assert len(own.model.requests) == 1, "the model is not asked again after a failed tool"


async def test_a_tool_transport_failure_during_the_call_is_tool_error(
    stubs: s.Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    real: httpx2.AsyncBaseTransport = s.tools_module.transport

    class DownAfterList(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
            body = json.loads(request.content or b"{}") if request.method == "POST" else {}
            if isinstance(body, dict) and body.get("method") == "tools/call":
                raise httpx2.ConnectError("gone", request=request)
            return await real.handle_async_request(request)

    monkeypatch.setattr(s.tools_module, "transport", DownAfterList())
    _only_error(await s.run(s.LOOKUP), "tool_error", retryable=False)


def _rounds(rounds: int) -> Script:
    """A scripted loop of `rounds` tool rounds, then the answer. Each round's result holds the text
    the next rule matches: glossary SLM, acronym RAG, glossary MCP, glossary A2A, glossary lane."""
    steps = [
        ("small language model", "acronym_expand", {"acronym": "RAG"}),
        ("retrieval-augmented generation", "glossary_lookup", {"term": "MCP"}),
        ("Model Context Protocol", "glossary_lookup", {"term": "A2A"}),
        ("Agent-to-Agent", "glossary_lookup", {"term": "lane"}),
    ]
    rules = [
        Rule(
            match="loop:", tool_call=ToolCallSpec(name="glossary_lookup", arguments={"term": "SLM"})
        )
    ]
    for i, (text, name, arguments) in enumerate(steps):
        if i + 1 < rounds:
            rules.append(
                Rule(
                    after_tool=True,
                    match=text,
                    tool_call=ToolCallSpec(name=name, arguments=arguments),
                )
            )
        else:
            rules.append(Rule(after_tool=True, match=text, reply="done after the loop"))
            break
    return Script(rules=rules)


async def test_three_tool_rounds_are_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    async with s.running(monkeypatch, script=_rounds(3)) as own:
        events = await s.run("loop: go")
        s.check_shape(events)
        assert events[-1]["type"] == "end", events[-1]
        assert s.answer(events) == "done after the loop"
        assert len([e for e in events if e["type"] == "tool_call"]) == 3
        assert len(own.model.requests) == 4, "three tool rounds, then the answer"


async def test_the_fourth_tool_round_is_tool_loop_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with s.running(monkeypatch, script=_rounds(4)) as own:
        events = await s.run("loop: go")
        _only_error(
            [e for e in events if e["type"] != "tool_call"], "tool_loop_exceeded", retryable=False
        )
        assert len([e for e in events if e["type"] == "tool_call"]) == 3
        assert len(own.tool_calls) == 3, "the 4th round's tool did not run"
        assert len(own.model.requests) == 4
        assert "metrics" not in [e["type"] for e in events]


# --- Tracing --------------------------------------------------------------------------------------


class _SpyProcessor:
    """A trace processor that records every call; none should come."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def on_trace_start(self, trace: Any) -> None:
        self.calls.append("trace_start")

    def on_trace_end(self, trace: Any) -> None:
        self.calls.append("trace_end")

    def on_span_start(self, span: Any) -> None:
        self.calls.append("span_start")

    def on_span_end(self, span: Any) -> None:
        self.calls.append("span_end")

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass


async def test_the_sdk_trace_export_is_off(stubs: s.Stubs, monkeypatch: pytest.MonkeyPatch) -> None:
    from agents.tracing import add_trace_processor
    from agents.tracing.processors import BackendSpanExporter, BatchTraceProcessor

    exported: list[str] = []
    monkeypatch.setattr(
        BackendSpanExporter, "export", lambda self, items: exported.append("export")
    )
    queued: list[str] = []
    monkeypatch.setattr(
        BatchTraceProcessor, "on_trace_start", lambda self, trace: queued.append("trace")
    )
    monkeypatch.setattr(
        BatchTraceProcessor, "on_span_start", lambda self, span: queued.append("span")
    )
    spy = _SpyProcessor()
    add_trace_processor(spy)  # type: ignore[arg-type]
    events = await s.run(s.LOOKUP)
    assert events[-1]["type"] == "end"
    assert spy.calls == [], "no trace or span reached a trace processor"
    assert queued == [] and exported == [], "the default batch exporter saw nothing"


def test_the_default_trace_processor_is_removed_at_import() -> None:
    from agents.tracing import get_trace_provider

    provider: Any = get_trace_provider()
    processors = getattr(getattr(provider, "_multi_processor", None), "_processors", None)
    assert processors is not None
    assert not any(type(p).__name__ == "BatchTraceProcessor" for p in processors)
    assert provider._disabled is True or s.handle_module.TRACING_DISABLED is True
