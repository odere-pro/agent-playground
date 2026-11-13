"""022 H-6: a workload's `guardrails` key never reaches LiteLLM (contract v6, ADR-008 item 4).

LiteLLM reads a request body's `guardrails` list (and the same under `metadata`) to pick the
guardrails of one call, so a workload that could send one could turn the hard control off. The
model proxy builds its upstream request from `ModelPort`, never from the workload's body. This
test sends every such field to both model routes (the OpenAI route and the Anthropic proxy route),
complete and streamed, on both listeners (the proxy port and the remote listener), and reads what
the LiteLLM adapter really sent upstream. Offline: an `httpx.MockTransport` stands in for
LiteLLM.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from chassis.adapters.litellm import LiteLLMModel
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from chassis.server.remote_auth import RUN_SECRET, create_remote_proxy_app
from proxy_fakes import (
    ANTHROPIC_BODY,
    PROXY_CONFIG,
    TRACEPARENT,
    asgi_client,
    proxy_ports,
    run_request,
)

MARK = "workload-guardrail-choice"
"""A value only the workload's guardrail fields hold."""
REMOTE_TOKEN = "remote-token-guardrails-0123456789"  # pragma: allowlist secret (a test value)
RUN_SECRET_VALUE = "run-secret-guardrails"  # pragma: allowlist secret (a test value)

GUARDRAIL_FIELDS: dict[str, Any] = {
    "guardrails": [MARK, "presidio-off"],
    "disable_global_guardrails": True,
    "metadata": {"guardrails": [MARK], "disable_global_guardrails": True},
    "litellm_metadata": {"guardrails": [MARK]},
    "extra_body": {"guardrails": [MARK]},
}
"""Each place a LiteLLM caller can name or switch off guardrails for one call."""

OPENAI_BODY: dict[str, Any] = {
    "model": "big-default",
    "messages": [{"role": "user", "content": "simplify: the quick brown fox"}],
    "max_tokens": 64,
}


class Router:
    """LiteLLM's stand-in: records each request body and answers, complete or streamed."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if body.get("stream"):
            chunk = {"choices": [{"index": 0, "delta": {"content": "hi"}}]}
            usage = {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            text = f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(usage)}\n\ndata: [DONE]\n\n"
            return httpx.Response(
                200, content=text.encode(), headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )


def _keys(value: Any) -> set[str]:
    """Every object key at any depth."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def _listener(kind: str, app: Any) -> Any:
    if kind == "proxy":
        return create_proxy_app(app)
    return create_remote_proxy_app(app, (REMOTE_TOKEN,))


@pytest.mark.parametrize("kind", ["proxy", "remote"])
@pytest.mark.parametrize("stream", [False, True], ids=["complete", "stream"])
@pytest.mark.parametrize(
    ("path", "body"),
    [("/v1/chat/completions", OPENAI_BODY), ("/v1/messages", ANTHROPIC_BODY)],
    ids=["openai-route", "anthropic-route"],
)
async def test_a_workloads_guardrails_key_never_reaches_litellm(
    kind: str, stream: bool, path: str, body: dict[str, Any]
) -> None:
    """The call is served, and the upstream body names no guardrail and holds no marker."""
    router = Router()
    adapter = LiteLLMModel(
        "http://router/v1", "chassis-key", agent="echo", transport=httpx.MockTransport(router)
    )
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(adapter))
    request, ctx = run_request(max_tokens=100)
    ctx = ctx.model_copy(update={"run_secret": RUN_SECRET_VALUE})
    headers = {
        "traceparent": TRACEPARENT,
        "authorization": f"Bearer {REMOTE_TOKEN}",
        RUN_SECRET: RUN_SECRET_VALUE,
    }

    async with (
        asgi_client(_listener(kind, app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx),
    ):
        reply = await client.post(
            path, json={**body, **GUARDRAIL_FIELDS, "stream": stream}, headers=headers
        )

    assert reply.status_code == 200, reply.text
    assert len(router.bodies) == 1, "the call never reached the LiteLLM adapter"
    sent = router.bodies[0]
    assert not {k for k in _keys(sent) if "guardrail" in k}, sent
    assert MARK not in json.dumps(sent)
    assert sent["metadata"] == {"tags": ["agent:echo"]}  # the adapter's own, nothing merged in
