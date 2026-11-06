"""The HTTP front of both servers: the body cap (R5a-11) and the dispatcher's bearer (R5a-23).

Each test builds the app the way `code-runner` builds it (`cli.serve_app`, `cli.dispatch_app`)
and talks to it through `httpx.ASGITransport`: no socket, no cluster. The fake API records every
request, so "no claim is created" is a list that stays empty.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from code_runner import asgi, cli, dispatch, isolation
from code_runner.runner import DEFAULT_LIMITS
from code_runner.server import TOOL_NAME

TOKEN = "0123456789abcdef" * 4  # the shape seed.sh makes: `openssl rand -hex 32`
NS = "poc05-tools"
MIB = 1024 * 1024
CAP = asgi.MAX_BODY_FACTOR * DEFAULT_LIMITS.max_code_bytes
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class FakeApi:
    """Records every API request; refuses each create, so a call ends `sandbox_unavailable`."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(403, json={"kind": "Status"})

    def creates(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeApi:
    """`code-runner dispatch` with its API client pointed at the fake, nothing else changed."""
    api = FakeApi()
    sa_token = tmp_path / "sa-token"
    sa_token.write_text("service-account-token")

    def fake_client(_env: Any, *, token_file: Any, ca_file: Any) -> httpx.AsyncClient:
        transport = httpx.MockTransport(api)
        return dispatch.api_client(
            "https://10.96.0.1:443", sa_token, verify=True, transport=transport
        )

    monkeypatch.setattr(dispatch, "api_client_from_env", fake_client)
    return api


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "inbound-token"
    path.write_text(TOKEN + "\n")
    return path


def _dispatch_args(*extra: str) -> list[str]:
    return ["--pool", "code-runner", "--namespace", NS, *extra]


def _rpc(code: str = "print(1)", key: str = "k-1") -> bytes:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": TOOL_NAME,
            "arguments": {"code": code, "timeout_s": 2},
            "_meta": {"idempotency_key": key},
        },
    }
    return json.dumps(body).encode()


def _inner(app: Any) -> Any:
    """The Starlette app under the middleware, for its lifespan (ASGITransport runs none)."""
    while not hasattr(app, "router"):
        app = app.app
    return app


@asynccontextmanager
async def _client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    inner = _inner(app)
    async with inner.router.lifespan_context(inner):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://dispatch") as client:
            yield client


class _Counted:
    """A request body of `total` bytes in 1 MiB chunks; `pulled` is what the app read."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.pulled = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        chunk = b" " * MIB
        while self.pulled < self.total:
            self.pulled += len(chunk)
            yield chunk


# --- R5a-11: the body cap ----------------------------------------------------------------------


def test_code_runner_body_cap_is_a_small_multiple_of_the_code_cap() -> None:
    """Room for JSON escaping (one byte becomes at most six) and the envelope, far below 5 MB."""
    assert asgi.max_body_bytes(DEFAULT_LIMITS) == CAP
    assert 6 * DEFAULT_LIMITS.max_code_bytes < CAP < 5 * MIB


async def test_code_runner_dispatch_50mb_body_with_a_length_is_413_before_it_is_read(
    fake_api: FakeApi,
) -> None:
    """Spec R5a-11: a 50 MB tools/call is refused with 413 before 5 MB is buffered."""
    app = cli.dispatch_app(cli.dispatch_parser().parse_args(_dispatch_args()), {})
    body = _Counted(50 * MIB)
    headers = {**HEADERS, "Content-Length": str(body.total)}

    async with _client(app) as client:
        response = await client.post("/mcp", content=body, headers=headers)

    assert response.status_code == 413
    assert response.json() == {"error": "body_too_large"}
    assert body.pulled == 0
    assert fake_api.requests == []


async def test_code_runner_dispatch_50mb_streamed_body_is_413_before_5mb_is_read(
    fake_api: FakeApi,
) -> None:
    """Spec R5a-11, with no Content-Length (chunked): the streamed bytes are counted."""
    app = cli.dispatch_app(cli.dispatch_parser().parse_args(_dispatch_args()), {})
    body = _Counted(50 * MIB)

    async with _client(app) as client:
        response = await client.post("/mcp", content=body, headers=HEADERS)

    assert "content-length" not in {k.lower() for k in response.request.headers}
    assert response.status_code == 413
    assert response.json() == {"error": "body_too_large"}
    assert body.pulled < 5 * MIB
    assert fake_api.requests == []


async def test_code_runner_413_body_never_echoes_the_request(fake_api: FakeApi) -> None:
    app = cli.dispatch_app(cli.dispatch_parser().parse_args(_dispatch_args()), {})
    big = _rpc(code="# SECRET-CODE " + "x" * CAP)

    async with _client(app) as client:
        response = await client.post("/mcp", content=big, headers=HEADERS)

    assert response.status_code == 413
    assert b"SECRET-CODE" not in response.content


async def test_code_runner_body_at_the_worst_escaping_still_reaches_the_tool(
    fake_api: FakeApi,
) -> None:
    """A 64 KiB code made of control characters is about 6x on the wire, under the cap."""
    app = cli.dispatch_app(cli.dispatch_parser().parse_args(_dispatch_args()), {})
    body = _rpc(code="\x01" * DEFAULT_LIMITS.max_code_bytes)
    assert 6 * DEFAULT_LIMITS.max_code_bytes < len(body) <= CAP

    async with _client(app) as client:
        response = await client.post("/mcp", content=body, headers=HEADERS)

    assert response.status_code == 200
    assert len(fake_api.creates()) == 1  # past the cap and the schema: a claim was tried


async def test_code_runner_serve_mode_has_the_body_cap_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(isolation, "harden_server", lambda: None)
    app = cli.serve_app(cli.serve_parser().parse_args(["--tmp-root", str(tmp_path)]))
    body = _Counted(50 * MIB)
    headers = {**HEADERS, "Content-Length": str(body.total)}

    async with _client(app) as client:
        response = await client.post("/mcp", content=body, headers=headers)

    assert response.status_code == 413
    assert body.pulled == 0


# --- R5a-23: the dispatcher's bearer -----------------------------------------------------------


async def test_code_runner_dispatch_mcp_without_a_bearer_is_401_and_makes_no_claim(
    fake_api: FakeApi, token_file: Path
) -> None:
    """Spec R5a-23: a configured token, a call to /mcp with no bearer: 401, no claim."""
    args = cli.dispatch_parser().parse_args(_dispatch_args("--inbound-token-file", str(token_file)))
    app = cli.dispatch_app(args, {})

    async with _client(app) as client:
        response = await client.post("/mcp", content=_rpc(), headers=HEADERS)

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert fake_api.requests == []


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer wrong",
        f"Bearer {TOKEN}x",
        f"Basic {TOKEN}",
        TOKEN,
        "Bearer ",
    ],
    ids=["wrong", "longer", "basic", "no-scheme", "empty"],
)
async def test_code_runner_dispatch_wrong_bearer_is_401_and_never_echoes_it(
    fake_api: FakeApi, token_file: Path, authorization: str
) -> None:
    args = cli.dispatch_parser().parse_args(_dispatch_args("--inbound-token-file", str(token_file)))
    app = cli.dispatch_app(args, {})

    async with _client(app) as client:
        response = await client.post(
            "/mcp", content=_rpc(), headers={**HEADERS, "Authorization": authorization}
        )

    assert response.status_code == 401
    assert TOKEN.encode() not in response.content
    assert b"wrong" not in response.content
    assert fake_api.requests == []


async def test_code_runner_dispatch_right_bearer_reaches_the_tool(
    fake_api: FakeApi, token_file: Path
) -> None:
    args = cli.dispatch_parser().parse_args(_dispatch_args("--inbound-token-file", str(token_file)))
    app = cli.dispatch_app(args, {})

    async with _client(app) as client:
        response = await client.post(
            "/mcp", content=_rpc(), headers={**HEADERS, "Authorization": f"Bearer {TOKEN}"}
        )

    assert response.status_code == 200
    assert len(fake_api.creates()) == 1
    assert "sandbox_unavailable" in response.text  # the fake API refused the claim


async def test_code_runner_dispatch_two_authorization_headers_are_401(
    fake_api: FakeApi, token_file: Path
) -> None:
    args = cli.dispatch_parser().parse_args(_dispatch_args("--inbound-token-file", str(token_file)))
    app = cli.dispatch_app(args, {})
    headers = [*HEADERS.items(), ("Authorization", f"Bearer {TOKEN}"), ("Authorization", "x")]

    async with _client(app) as client:
        response = await client.post("/mcp", content=_rpc(), headers=headers)

    assert response.status_code == 401
    assert fake_api.requests == []


async def test_code_runner_dispatch_health_stays_open_with_a_token(
    fake_api: FakeApi, token_file: Path
) -> None:
    """Probes carry no bearer: /health answers without one and never calls the API."""
    args = cli.dispatch_parser().parse_args(_dispatch_args("--inbound-token-file", str(token_file)))
    app = cli.dispatch_app(args, {})

    async with _client(app) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert fake_api.requests == []


async def test_code_runner_dispatch_unauthenticated_large_body_is_401_unread(
    fake_api: FakeApi, token_file: Path
) -> None:
    """The bearer is checked first: a caller without it cannot make the dispatcher read."""
    args = cli.dispatch_parser().parse_args(_dispatch_args("--inbound-token-file", str(token_file)))
    app = cli.dispatch_app(args, {})
    body = _Counted(50 * MIB)

    async with _client(app) as client:
        response = await client.post("/mcp", content=body, headers=HEADERS)

    assert response.status_code == 401
    assert body.pulled == 0


async def test_code_runner_dispatch_without_a_token_file_keeps_mcp_open(
    fake_api: FakeApi,
) -> None:
    """The token is optional: with no `--inbound-token-file`, /mcp needs no bearer."""
    app = cli.dispatch_app(cli.dispatch_parser().parse_args(_dispatch_args()), {})

    async with _client(app) as client:
        response = await client.post("/mcp", content=_rpc(), headers=HEADERS)

    assert response.status_code == 200
    assert len(fake_api.creates()) == 1


@pytest.mark.parametrize(
    "content", ["", "  \n", "short", "two words " + TOKEN], ids=["empty", "blank", "short", "space"]
)
def test_code_runner_dispatch_refuses_to_start_with_an_unusable_token_file(
    fake_api: FakeApi, tmp_path: Path, content: str
) -> None:
    path = tmp_path / "inbound-token"
    path.write_text(content)
    args = cli.dispatch_parser().parse_args(_dispatch_args("--inbound-token-file", str(path)))

    with pytest.raises(ValueError, match="inbound token") as raised:
        cli.dispatch_app(args, {})

    assert content.strip() == "" or content.strip() not in str(raised.value)


def test_code_runner_dispatch_missing_token_file_refuses_to_start(
    fake_api: FakeApi, tmp_path: Path
) -> None:
    args = cli.dispatch_parser().parse_args(
        _dispatch_args("--inbound-token-file", str(tmp_path / "absent"))
    )
    with pytest.raises(OSError):
        cli.dispatch_app(args, {})


def test_code_runner_inbound_token_flag_is_not_the_api_token_flag() -> None:
    """`--token-file` stays the projected API token; the inbound bearer has its own flag."""
    args = cli.dispatch_parser().parse_args(_dispatch_args("--inbound-token-file", "/run/t"))
    assert args.inbound_token_file == "/run/t"
    assert args.token_file == dispatch.TOKEN_FILE
    assert cli.dispatch_parser().parse_args(_dispatch_args()).inbound_token_file is None
