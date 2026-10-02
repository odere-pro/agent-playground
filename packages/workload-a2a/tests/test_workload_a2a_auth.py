"""`--require-token-env`: the remote lane's bearer check on every request, the agent card included.

No token, a wrong token, or a malformed header gets one fixed 401. The token never appears in a
response, a start-up error, or a log line. Without the flag the server is open, as before.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from workload_a2a import cli
from workload_a2a.auth import (
    UNAUTHORIZED_BODY,
    BearerTokenMiddleware,
    read_previous_token,
    read_token,
)

TOKEN = "s3cret-token-value-0123456789abcdef"
PREVIOUS = "pr3vious-token-value-fedcba9876543210"
ENV = "WA_TEST_TOKEN"
PREV_ENV = "WA_TEST_PREVIOUS_TOKEN"
EXAMPLE_SCRIPT = (
    Path(__file__).resolve().parents[3] / "packages/fake-model-server/scripts/example.yaml"
)
CARD_PATH = "/.well-known/agent-card.json"


def _argv(*extra: str) -> list[str]:
    return ["serve", "--handle", "echo_python:handle", "--port", "9000", *extra]


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://workload.test"
    )


def _guarded() -> Any:
    server = cli.build(_argv("--require-token-env", ENV))
    return server.config.app


def _guarded_with_previous() -> Any:
    server = cli.build(
        _argv("--require-token-env", ENV, "--previous-token-env", PREV_ENV),
    )
    return server.config.app


@pytest.fixture
def token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, TOKEN)


@pytest.mark.usefixtures("token_env")
async def test_missing_header_is_401_with_the_fixed_body() -> None:
    async with _client(_guarded()) as http:
        for path in (CARD_PATH, "/", "/anything"):
            response = await http.get(path)
            assert response.status_code == 401
            assert response.content == UNAUTHORIZED_BODY
            assert response.headers["content-type"] == "application/json"
            assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.usefixtures("token_env")
async def test_wrong_and_malformed_tokens_are_401_with_the_same_body() -> None:
    headers = [
        {"Authorization": "Bearer nope"},
        {"Authorization": f"Bearer {TOKEN}x"},
        {"Authorization": "Bearer "},
        {"Authorization": TOKEN},
        {"Authorization": f"Basic {TOKEN}"},
    ]
    async with _client(_guarded()) as http:
        bodies = set()
        for h in headers:
            response = await http.get(CARD_PATH, headers=h)
            assert response.status_code == 401, h
            bodies.add(response.content)
        assert bodies == {UNAUTHORIZED_BODY}
        assert TOKEN.encode() not in UNAUTHORIZED_BODY


@pytest.mark.usefixtures("token_env")
async def test_right_token_reaches_the_card_and_message_send() -> None:
    auth = {"Authorization": f"Bearer {TOKEN}"}
    async with _client(_guarded()) as http:
        card = await http.get(CARD_PATH, headers=auth)
        assert card.status_code == 200
        assert "name" in card.json()
        rpc = await http.post("/", headers=auth, json={"jsonrpc": "2.0", "id": 1, "method": "x"})
        assert rpc.status_code != 401


@pytest.mark.usefixtures("token_env")
async def test_message_send_without_token_never_reaches_the_handler() -> None:
    async with _client(_guarded()) as http:
        rpc = await http.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "message/send"})
        assert rpc.status_code == 401
        assert rpc.content == UNAUTHORIZED_BODY


async def test_without_the_flag_the_server_is_open() -> None:
    server = cli.build(_argv())
    async with _client(server.config.app) as http:
        assert (await http.get(CARD_PATH)).status_code == 200


async def test_non_http_scopes_pass_through() -> None:
    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    guard = BearerTokenMiddleware(inner, TOKEN)
    await guard({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]


@pytest.mark.parametrize("value", [None, "", "   "])
def test_start_refused_when_the_variable_is_missing_or_empty(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, value)
    with pytest.raises(SystemExit) as exc:
        cli.build(_argv("--require-token-env", ENV))
    assert ENV in str(exc.value.code)


def test_read_token_error_names_the_variable_not_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)
    with pytest.raises(ValueError, match=ENV):
        read_token(ENV)
    monkeypatch.setenv(ENV, TOKEN)
    assert read_token(ENV) == TOKEN


@pytest.mark.usefixtures("token_env")
def test_error_messages_never_carry_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.build(_argv("--require-token-env", ENV, "--handle", "nope:missing"))
    assert TOKEN not in str(exc.value.code)


def test_non_loopback_host_refused_without_the_flag() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.build(_argv("--host", "0.0.0.0"))
    assert "loopback" in str(exc.value.code)


@pytest.mark.usefixtures("token_env")
def test_non_loopback_host_allowed_with_the_flag() -> None:
    server = cli.build(_argv("--host", "0.0.0.0", "--require-token-env", ENV))
    assert server.config.host == "0.0.0.0"


@pytest.mark.usefixtures("token_env")
async def test_token_is_in_no_log_record_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    import importlib
    import logging

    from a2a.client import ClientConfig, ClientFactory
    from fake_model_server import Script, create_app
    from workload_a2a.mapping import request_to_message

    workload: Any = importlib.import_module("echo_python.handle")
    script = Script.from_yaml(EXAMPLE_SCRIPT)
    workload.transport = httpx.ASGITransport(app=create_app(script))
    ctx = {
        "request_id": "r",
        "trace_id": "t",
        "idempotency_key": "i",
        "agent": "echo",
        "agent_version": "0.0.1",
        "budget": {"max_tokens": 2000, "timeout_ms": 30000},
        "versions": {"chassis": "0.1.0", "config": None, "prompt": None, "model_route": "r"},
        "model_route": "big-default",
    }
    caplog.set_level(logging.DEBUG)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_guarded()),
            base_url="http://127.0.0.1:9000",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as http:
            client = await ClientFactory(
                ClientConfig(httpx_client=http, streaming=True)
            ).create_from_url("http://127.0.0.1:9000")
            responses = [
                r async for r in client.send_message(request_to_message({"text": "hi"}, ctx))
            ]
            await client.close()
    finally:
        workload.transport = None
    assert responses
    assert caplog.records
    for record in caplog.records:
        assert TOKEN not in record.getMessage()
        assert TOKEN not in str(record.args)
        assert TOKEN not in str(record.__dict__)


# Rotation: `--previous-token-env` makes the server accept the old token as well.


@pytest.mark.usefixtures("token_env")
async def test_previous_token_is_accepted_with_the_current_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PREV_ENV, PREVIOUS)
    async with _client(_guarded_with_previous()) as http:
        for token in (TOKEN, PREVIOUS):
            response = await http.get(CARD_PATH, headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200, token


@pytest.mark.usefixtures("token_env")
async def test_a_third_token_is_refused_with_the_fixed_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PREV_ENV, PREVIOUS)
    async with _client(_guarded_with_previous()) as http:
        for header in ("Bearer some-other-token", f"Bearer {PREVIOUS}x", "Bearer "):
            response = await http.get(CARD_PATH, headers={"Authorization": header})
            assert response.status_code == 401, header
            assert response.content == UNAUTHORIZED_BODY
        missing = await http.get(CARD_PATH)
        assert missing.status_code == 401
        assert missing.content == UNAUTHORIZED_BODY


@pytest.mark.usefixtures("token_env")
@pytest.mark.parametrize("value", [None, "", "   "])
async def test_unset_or_empty_previous_is_ignored(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(PREV_ENV, raising=False)
    else:
        monkeypatch.setenv(PREV_ENV, value)
    assert read_previous_token(PREV_ENV) is None
    async with _client(_guarded_with_previous()) as http:
        ok = await http.get(CARD_PATH, headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200
        # An empty previous must not let an empty bearer value in.
        for header in ("Bearer ", "Bearer    ", "Bearer anything"):
            refused = await http.get(CARD_PATH, headers={"Authorization": header})
            assert refused.status_code == 401, header


def test_previous_flag_without_require_token_is_a_start_up_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PREV_ENV, PREVIOUS)
    with pytest.raises(SystemExit) as exc:
        cli.build(_argv("--previous-token-env", PREV_ENV))
    assert "--require-token-env" in str(exc.value.code)
    assert PREVIOUS not in str(exc.value.code)


def test_middleware_repr_and_errors_hide_both_tokens() -> None:
    guard = BearerTokenMiddleware(None, TOKEN, PREVIOUS)
    for text in (repr(guard), str(guard)):
        assert TOKEN not in text
        assert PREVIOUS not in text
    with pytest.raises(ValueError, match="empty") as exc:
        BearerTokenMiddleware(None, TOKEN, "")
    assert TOKEN not in str(exc.value)


@pytest.mark.usefixtures("token_env")
async def test_neither_token_is_in_a_log_record_at_debug(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    monkeypatch.setenv(PREV_ENV, PREVIOUS)
    caplog.set_level(logging.DEBUG)
    async with _client(_guarded_with_previous()) as http:
        for token in (TOKEN, PREVIOUS, "wrong"):
            await http.get(CARD_PATH, headers={"Authorization": f"Bearer {token}"})
    assert caplog.records
    for record in caplog.records:
        blob = record.getMessage() + str(record.args) + str(record.__dict__)
        assert TOKEN not in blob
        assert PREVIOUS not in blob


async def test_header_is_stripped_after_a_previous_token_match() -> None:
    seen: list[list[tuple[bytes, bytes]]] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(list(scope["headers"]))

    guard = BearerTokenMiddleware(inner, TOKEN, PREVIOUS)
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {PREVIOUS}".encode()), (b"x-a", b"1")],
    }
    await guard(scope, None, None)
    assert seen == [[(b"x-a", b"1")]]
