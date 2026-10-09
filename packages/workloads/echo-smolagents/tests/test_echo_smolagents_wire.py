"""What `handle` puts on the wire: headers on every request, no key leaves the process, and the
exact keys of the chat body (router compatibility, with the chassis model proxy's key filter).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from echo_smolagents_support import (
    CTX,
    LOOKUP,
    PLANTED,
    ROUTER_KEEPS,
    SMOKE,
    TOKEN,
    TRACEPARENT,
    RouterFilter,
    Stubs,
    check_shape,
    handle_module,
    run,
    stub_servers,
    text_of,
)
from smolagents.utils import parse_code_blobs  # type: ignore[import-untyped]


@pytest.fixture
async def stubs(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Stubs]:
    async with stub_servers(monkeypatch) as s:
        yield s


def _header(request: Any, name: str) -> str | None:
    value = request.headers.get(name)
    return None if value is None else str(value)


async def test_traceparent_is_on_every_model_call_and_every_mcp_request(stubs: Stubs) -> None:
    events = await run(LOOKUP)
    assert events[-1]["type"] == "end"
    assert len(stubs.model.requests) == 3
    assert len(stubs.tools.requests) >= 5, "list session plus one session per tool call"
    for request in stubs.all_requests():
        assert _header(request, "traceparent") == TRACEPARENT, request.url


async def test_no_traceparent_header_when_the_context_has_none(stubs: Stubs) -> None:
    ctx = {k: v for k, v in CTX.items() if k != "traceparent"}
    await run(SMOKE, ctx)
    assert stubs.all_requests()
    assert all(_header(r, "traceparent") is None for r in stubs.all_requests())


async def test_remote_token_is_the_bearer_on_every_model_and_mcp_request(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHASSIS_API_TOKEN", TOKEN)
    events = await run(LOOKUP)
    assert events[-1]["type"] == "end"
    assert stubs.model.requests and stubs.tools.requests
    for request in stubs.all_requests():
        assert _header(request, "authorization") == f"Bearer {TOKEN}", request.url
        assert _header(request, "traceparent") == TRACEPARENT


@pytest.mark.parametrize("value", [None, ""])
async def test_without_a_token_no_authorization_header_is_sent(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is not None:
        monkeypatch.setenv("CHASSIS_API_TOKEN", value)
    events = await run(LOOKUP)
    assert events[-1]["type"] == "end"
    for request in stubs.all_requests():
        assert _header(request, "authorization") is None, request.url


async def test_a_planted_openai_key_never_leaves_the_process(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OPENAI_ORG_ID", "org-planted")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", PLANTED + "-admin")
    monkeypatch.setenv("CHASSIS_API_TOKEN", TOKEN)
    with caplog.at_level(logging.DEBUG):
        events = await run(LOOKUP)
    seen = [str(r.url) + str(dict(r.headers)) + r.read().decode() for r in stubs.all_requests()]
    seen.extend(str(e) for e in events)
    seen.extend(str(b) for b in stubs.bodies())
    seen.extend(r.getMessage() for r in caplog.records)
    blob = "\n".join(seen)
    assert PLANTED not in blob and "org-planted" not in blob
    assert TOKEN not in str(events) and TOKEN not in "\n".join(
        r.getMessage() for r in caplog.records
    )


async def test_the_planted_key_is_not_sent_without_a_token_either(stubs: Stubs) -> None:
    await run(SMOKE)
    for request in stubs.all_requests():
        assert PLANTED not in str(dict(request.headers))
        assert _header(request, "authorization") is None


async def test_the_chat_body_has_exactly_these_keys(stubs: Stubs) -> None:
    await run(LOOKUP)
    for body in stubs.bodies():
        assert set(body) == {"model", "messages", "stop"}
        assert body["stop"] == ["Observation:", "Calling tools:", "</code>"]
        assert "stream" not in body and "tools" not in body and "temperature" not in body


async def test_the_model_call_has_no_retry_and_the_context_timeout(stubs: Stubs) -> None:
    await run(SMOKE, {**CTX, "budget": {"timeout_ms": 7000}})
    timeouts = {r.extensions["timeout"]["read"] for r in stubs.model.requests}
    assert timeouts == {7.0}


# --- router compatibility: the proxy keeps only ROUTER_KEEPS, so `stop` is dropped -------------


async def test_lookup_passes_when_the_router_drops_stop(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = RouterFilter(handle_module.transport)
    monkeypatch.setattr(handle_module, "transport", router)
    events = await run(LOOKUP)
    check_shape(events)
    assert events[-1]["type"] == "end"
    answer = text_of(events)
    assert "small language model" in answer and "retrieval-augmented generation" in answer
    assert router.received and all("stop" in b for b in router.received), "the workload sends it"
    assert all(set(b) <= ROUTER_KEEPS for b in router.forwarded)
    assert all("stop" not in b for b in stubs.bodies()), "the fake model never saw it"


async def test_a_model_that_runs_past_the_code_block_is_cut_by_the_workload(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handle_module, "transport", RouterFilter(handle_module.transport))
    events = await run("overrun: answer")
    check_shape(events)
    assert text_of(events) == "right"


def test_without_the_workloads_own_cut_the_overrun_would_run_two_code_blocks() -> None:
    reply = (
        'Thought: Done.\n<code>\nprint("first block")\n</code>\nObservation: made up\n'
        'Thought: Again.\n<code>\nfinal_answer("WRONG")\n</code>\n'
    )
    code = parse_code_blobs(reply, ("<code>", "</code>"))
    assert "first block" in code and "WRONG" in code, "smolagents joins every block it finds"
