"""Guardrail server auth: with a token, both POST routes need `x-api-key` (what LiteLLM's
`generic_guardrail_api` sends) or `Authorization: Bearer`. `/health` stays open. The CLI reads the
token from a file and refuses to start without one off loopback. Offline: httpx over ASGI.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from guardrail_server import GENERIC_PATH, PatternClassifier, create_app
from guardrail_server.cli import StartupError, main, parse_args, resolve_token
from guardrail_server.litellm_stub import APPLY_PATH, StubSettings, create_stub

TOKEN = "guard-" + "token-not-real-0123"  # pragma: allowlist secret (a test value)
WRONG = "guard-" + "token-wrong-9876"  # pragma: allowlist secret (a test value)
MASTER = "stub-" + "key-not-real"  # pragma: allowlist secret (a test value)
UNAUTHORIZED = {"detail": "guardrail auth required"}
ROUTES: list[tuple[str, dict[str, Any]]] = [
    (GENERIC_PATH, {"input_type": "request", "texts": ["hello"], "request_data": {}}),
    ("/v1/check", {"text": "hello"}),
]


def _client(token: str | None = TOKEN) -> httpx.AsyncClient:
    app = create_app(PatternClassifier(), token=token)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gs")


@pytest.mark.parametrize(("path", "body"), ROUTES)
async def test_routes_answer_401_when_no_credential_is_sent(
    path: str, body: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """No header is a 401 with a fixed body, and the refusal is logged."""
    with caplog.at_level(logging.WARNING, logger="guardrail_server"):
        async with _client() as client:
            response = await client.post(path, json=body)

    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED
    assert "refused" in caplog.text


@pytest.mark.parametrize(("path", "body"), ROUTES)
@pytest.mark.parametrize(
    "headers",
    [{"x-api-key": WRONG}, {"Authorization": f"Bearer {WRONG}"}, {"Authorization": WRONG}],
    ids=["x-api-key", "bearer", "bare-authorization"],
)
async def test_routes_answer_401_without_echo_when_the_token_is_wrong(
    path: str, body: dict[str, Any], headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """A wrong token is a 401 that never echoes what was sent, in the body or the log."""
    with caplog.at_level(logging.DEBUG, logger="guardrail_server"):
        async with _client() as client:
            response = await client.post(path, json=body, headers=headers)

    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED
    assert WRONG not in response.text
    assert WRONG not in caplog.text


async def test_generic_route_answers_401_before_it_validates_the_body() -> None:
    """A caller without the token learns nothing about the schema."""
    async with _client() as client:
        response = await client.post(GENERIC_PATH, json={"input_type": "sideways"})

    assert response.status_code == 401


@pytest.mark.parametrize(("path", "body"), ROUTES)
@pytest.mark.parametrize(
    "headers",
    [
        {"x-api-key": TOKEN},
        {"Authorization": f"Bearer {TOKEN}"},
        {"authorization": f"bearer {TOKEN}"},
    ],
    ids=["x-api-key", "bearer", "lowercase-bearer"],
)
async def test_routes_answer_200_when_either_header_holds_the_token(
    path: str, body: dict[str, Any], headers: dict[str, str]
) -> None:
    """`x-api-key` or `Authorization: Bearer` with the token is let in."""
    async with _client() as client:
        response = await client.post(path, json=body, headers=headers)

    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
async def test_the_schema_and_docs_routes_are_off(path: str) -> None:
    """Security review 2026-11-06: an unauthenticated caller cannot read the schema."""
    async with _client() as client:
        answer = await client.get(path)
    assert answer.status_code == 404


async def test_health_answers_200_when_no_credential_is_sent() -> None:
    """`/health` stays open, so a probe needs no token."""
    async with _client() as client:
        response = await client.get("/health")

    assert response.status_code == 200


@pytest.mark.parametrize(("path", "body"), ROUTES)
async def test_routes_answer_200_when_no_token_is_configured(
    path: str, body: dict[str, Any]
) -> None:
    """Without a token the routes are open, as before (loopback only, by the CLI)."""
    async with _client(token=None) as client:
        response = await client.post(path, json=body)

    assert response.status_code == 200


def _stub(guardrail_token: str | None) -> httpx.AsyncClient:
    guard = create_app(PatternClassifier(), token=TOKEN)
    stub = create_stub(
        StubSettings(master_key=MASTER, guardrail_token=guardrail_token),
        guardrail_transport=httpx.ASGITransport(app=guard),
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stub),
        base_url="http://litellm",
        headers={"Authorization": f"Bearer {MASTER}"},
    )


@pytest.mark.parametrize(("token", "status"), [(TOKEN, 200), (None, 500), (WRONG, 500)])
async def test_stub_sends_the_guardrail_token_as_x_api_key(token: str | None, status: int) -> None:
    """The stub sends its guardrail token as `x-api-key`, as LiteLLM does; a 401 fails closed."""
    async with _stub(token) as client:
        response = await client.post(
            APPLY_PATH, json={"guardrail_name": "secret-guard", "text": "hello"}
        )

    assert response.status_code == status
    assert TOKEN not in response.text


def _token_file(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "guardrail-token"
    path.write_text(content)
    return path


def test_resolve_token_reads_and_strips_the_token_file(tmp_path: Path) -> None:
    """The token is the file's content, stripped."""
    args = parse_args(["--token-file", str(_token_file(tmp_path, f"  {TOKEN}\n"))], env={})

    assert resolve_token(args) == TOKEN


def test_parse_args_takes_the_token_file_from_the_environment(tmp_path: Path) -> None:
    """`GUARDRAIL_TOKEN_FILE` is the default for `--token-file`."""
    path = _token_file(tmp_path, TOKEN)

    args = parse_args([], env={"GUARDRAIL_TOKEN_FILE": str(path)})

    assert args.token_file == str(path)


@pytest.mark.parametrize("content", ["", "  \n\t"], ids=["empty", "blank"])
def test_resolve_token_refuses_an_empty_token_file(tmp_path: Path, content: str) -> None:
    """An empty file is refused, naming the path."""
    path = _token_file(tmp_path, content)

    with pytest.raises(StartupError, match="empty") as caught:
        resolve_token(parse_args(["--token-file", str(path)], env={}))

    assert str(path) in str(caught.value)


def test_resolve_token_refuses_an_unreadable_token_file_without_its_content(
    tmp_path: Path,
) -> None:
    """A missing file is refused, naming the path, never a content."""
    path = tmp_path / "missing"

    with pytest.raises(StartupError, match="cannot read") as caught:
        resolve_token(parse_args(["--token-file", str(path)], env={}))

    assert str(path) in str(caught.value)


def test_resolve_token_refuses_a_directory_as_the_token_file(tmp_path: Path) -> None:
    """A path that is not a readable file is refused."""
    with pytest.raises(StartupError, match="cannot read"):
        resolve_token(parse_args(["--token-file", str(tmp_path)], env={}))


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.5", "guardrail-server"])
def test_resolve_token_refuses_to_start_off_loopback_without_a_token(host: str) -> None:
    """No token off loopback is refused, so the check is never an open oracle."""
    with pytest.raises(StartupError, match="--token-file"):
        resolve_token(parse_args(["--host", host], env={}))


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_resolve_token_warns_when_auth_is_off_on_loopback(
    host: str, caplog: pytest.LogCaptureFixture
) -> None:
    """No token on loopback starts with a warning that auth is off."""
    with caplog.at_level(logging.WARNING, logger="guardrail_server"):
        token = resolve_token(parse_args(["--host", host], env={}))

    assert token is None
    assert "auth is off" in caplog.text


def test_main_exits_with_a_message_when_the_host_is_not_loopback_and_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main` exits before it starts a server, with the reason."""
    monkeypatch.delenv("GUARDRAIL_TOKEN_FILE", raising=False)

    with pytest.raises(SystemExit) as caught:
        main(["--host", "0.0.0.0", "--classifier", "pattern"])

    assert "--token-file" in str(caught.value.code)
