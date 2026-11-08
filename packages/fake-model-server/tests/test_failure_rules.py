"""CH-16 rules: a scripted error with a `type` answers in the OpenAI error shape (LiteLLM's
`budget_exceeded` refusal), a rule with `delay_s` holds the answer, and a client that leaves
during the delay is counted in `app.state.aborted` (the fake saw the connection close).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fake_model_server import Script, create_app
from fake_model_server.script import ErrorSpec, Rule

BUDGET_MESSAGE = "ExceededBudget: Crossed spend threshold for key"


def _app(*rules: Rule) -> httpx.AsyncClient:
    app = create_app(Script(rules=list(rules)))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fake")
    client.app = app  # type: ignore[attr-defined]
    return client


async def test_an_error_with_a_type_answers_in_the_openai_error_shape() -> None:
    """LiteLLM's refusal is `{"error": {"message", "type", "param", "code"}}` with the status as
    a string in `code`; a script can replay it."""
    rule = Rule(
        match="budget", error=ErrorSpec(status=400, type="budget_exceeded", message=BUDGET_MESSAGE)
    )
    async with _app(rule) as client:
        res = await client.post(
            "/v1/chat/completions",
            json={"model": "x", "messages": [{"role": "user", "content": "budget please"}]},
        )
    assert res.status_code == 400
    assert res.json() == {
        "error": {
            "message": BUDGET_MESSAGE,
            "type": "budget_exceeded",
            "param": None,
            "code": "400",
        }
    }


async def test_an_error_without_a_type_keeps_the_detail_shape() -> None:
    rule = Rule(match="fail", error=ErrorSpec(status=500, message="scripted failure"))
    async with _app(rule) as client:
        res = await client.post(
            "/v1/chat/completions",
            json={"model": "x", "messages": [{"role": "user", "content": "fail"}]},
        )
    assert res.status_code == 500 and res.json() == {"detail": "scripted failure"}


@pytest.mark.parametrize("stream", [False, True])
async def test_a_delayed_rule_answers_after_its_delay(stream: bool) -> None:
    rule = Rule(match="slow", reply="late", delay_s=0.05)
    async with _app(rule) as client:
        loop = asyncio.get_running_loop()
        started = loop.time()
        res = await client.post(
            "/v1/chat/completions",
            json={
                "model": "x",
                "messages": [{"role": "user", "content": "slow"}],
                "stream": stream,
            },
        )
        assert loop.time() - started >= 0.05
    assert res.status_code == 200 and "late" in res.text
    assert client.app.state.aborted == 0  # type: ignore[attr-defined]


async def test_a_client_that_leaves_during_the_delay_is_counted_as_aborted() -> None:
    """The ASGI transport cancels the handler when the caller gives up; the fake counts it, so a
    test can show the upstream call ended."""
    rule = Rule(match="slow", reply="late", delay_s=30.0)
    async with _app(rule) as client:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await client.post(
                    "/v1/chat/completions",
                    json={"model": "x", "messages": [{"role": "user", "content": "slow"}]},
                )
        assert client.app.state.aborted == 1  # type: ignore[attr-defined]
        assert client.app.state.calls_total == 1  # type: ignore[attr-defined]
