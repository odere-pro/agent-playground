"""The Anthropic proxy route behind the remote listener: credentials appear nowhere.

Canary: the remote token and an `x-api-key` value reach no log record, no span attribute, no
counter label, and no response body, 400 bodies that echo request content included. Driven in
process, no socket.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pytest
from chassis.server import ChassisConfig, create_app
from chassis.server.remote_auth import create_remote_proxy_app
from proxy_fakes import (
    ANTHROPIC_BODY,
    GLOSSARY_RULES,
    PROXY_CONFIG,
    TRACEPARENT,
    RecordingModel,
    asgi_client,
    proxy_ports,
    run_request,
    telemetry_of,
)

REMOTE_TOKEN = "remote-token-canary-0123456789abcdef"  # pragma: allowlist secret (canary)
API_KEY_CANARY = "xapikey-canary-fedcba9876543210"  # pragma: allowlist secret (canary)
CANARIES = [REMOTE_TOKEN, API_KEY_CANARY]
SURFACES = ["responses", "caplog", "telemetry_logs", "span_attributes", "counter_labels"]

ECHOING_BODIES: list[dict[str, Any]] = [
    # A tool_result with an unknown id, and the canaries in every echoable place.
    {
        **ANTHROPIC_BODY,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": API_KEY_CANARY, "content": "x"},
                ],
            }
        ],
    },
    {**ANTHROPIC_BODY, "messages": [{"role": REMOTE_TOKEN, "content": API_KEY_CANARY}]},
    {**ANTHROPIC_BODY, "messages": [{"role": "user", "content": [{"type": API_KEY_CANARY}]}]},
    {**ANTHROPIC_BODY, "tools": [{"type": REMOTE_TOKEN, "name": "n"}]},
    {**ANTHROPIC_BODY, "max_tokens": API_KEY_CANARY},
    {**ANTHROPIC_BODY, "extra_" + REMOTE_TOKEN: API_KEY_CANARY},
]


@dataclass(frozen=True)
class CanaryRun:
    """The statuses of the three plain calls, the span count, and every surface as text."""

    ok_status: int
    bad_status: int
    wrong_status: int
    span_count: int
    surfaces: dict[str, str]


@pytest.fixture
async def canary_run(caplog: pytest.LogCaptureFixture) -> CanaryRun:
    """Post the echoing bodies, one good stream, and two refused calls through the remote
    listener with the canaries in the credentials, then collect every surface."""
    ports = proxy_ports(RecordingModel(GLOSSARY_RULES))
    public = create_app(ChassisConfig.model_validate(PROXY_CONFIG), ports)
    remote = create_remote_proxy_app(public, (REMOTE_TOKEN,))
    request, ctx = run_request(max_tokens=40000)
    headers = {
        "authorization": f"Bearer {REMOTE_TOKEN}",
        "x-api-key": API_KEY_CANARY,
        "traceparent": TRACEPARENT,
    }
    caplog.set_level(logging.DEBUG)
    async with (
        asgi_client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        echoed = [
            await client.post("/v1/messages", json=body, headers=headers) for body in ECHOING_BODIES
        ]
        ok = await client.post(
            "/v1/messages", json={**ANTHROPIC_BODY, "stream": True}, headers=headers
        )
        # Refusals by the middleware, with the canary in the wrong places.
        bad = await client.post(
            "/v1/messages",
            json=ANTHROPIC_BODY,
            headers={"x-api-key": REMOTE_TOKEN, "traceparent": TRACEPARENT},
        )
        wrong = await client.post(
            "/v1/messages",
            json=ANTHROPIC_BODY,
            headers={"authorization": f"Bearer {API_KEY_CANARY}x", "x-api-key": API_KEY_CANARY},
        )
    telemetry = telemetry_of(ports)
    responses = [*echoed, ok, bad, wrong]
    return CanaryRun(
        ok_status=ok.status_code,
        bad_status=bad.status_code,
        wrong_status=wrong.status_code,
        span_count=len(telemetry.spans),
        surfaces={
            "responses": "\n".join(res.text + repr(res.headers) for res in responses),
            "caplog": caplog.text,
            "telemetry_logs": repr(telemetry.logs),
            "span_attributes": repr([s.attributes for s in telemetry.spans]),
            "counter_labels": repr(list(telemetry.counters)),
        },
    )


def test_the_remote_listener_serves_the_token_and_refuses_the_rest_when_canaries_are_sent(
    canary_run: CanaryRun,
) -> None:
    """The right bearer token is served; the token in `x-api-key` or a wrong bearer is a 401."""
    assert canary_run.ok_status == 200
    assert canary_run.bad_status == 401
    assert canary_run.wrong_status == 401


def test_the_served_call_records_a_span_when_canaries_are_sent(canary_run: CanaryRun) -> None:
    """At least one span exists, so the span surface is not empty by accident."""
    assert canary_run.span_count >= 1


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("canary", CANARIES, ids=["remote_token", "x_api_key"])
def test_a_credential_appears_nowhere_on_the_surface_when_it_is_sent_as_a_canary(
    canary_run: CanaryRun, canary: str, surface: str
) -> None:
    """Neither canary reaches a response, a log, a span attribute, or a counter label."""
    assert canary not in canary_run.surfaces[surface]
