"""The dispatcher: a fresh sandbox per call (per-call sandbox plan, "Decision" and "Tests").

A fake Kubernetes API and a fake sandbox on `httpx.MockTransport`, no socket. The fake
sandbox forwards to the real runner server in process unless a test gives it another answer.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import tracemalloc
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from code_runner import cli
from code_runner.dispatch import (
    API_GROUP_PATH,
    DispatchConfig,
    Dispatcher,
    api_base_url,
    api_client,
    create_dispatch_server,
    sandbox_client,
)
from code_runner.runner import RunnerError
from code_runner.server import TOOL_NAME, create_server
from fastmcp import Client
from fastmcp.exceptions import ToolError

NS = "poc05-tools"
TOKEN = "token-one"
NOW = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
CLAIMS = f"{API_GROUP_PATH}/namespaces/{NS}/sandboxclaims"
RANDOM = "import os\nprint(os.urandom(8).hex())"
FAST = DispatchConfig(
    namespace=NS,
    claim_timeout_s=1,
    poll_s=0.01,
    answer_grace_s=2,
    connect_retry_s=0.2,
)

Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


class FakeApi:
    """SandboxClaims in memory. `ready_after` GETs before Ready; `None` never gets Ready."""

    def __init__(self, *, ready_after: int | None = 1, pod_ip: str = "10.244.0.7") -> None:
        self.ready_after = ready_after
        self.pod_ip = pod_ip
        self.requests: list[httpx.Request] = []
        self.claims: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []
        self.gets = 0
        self.create_answer: httpx.Response | None = None
        self.create_times_out = False
        self.delete_status = 200
        self.delete_answers: list[int] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            if self.create_answer is not None:
                return self.create_answer
            claim = json.loads(request.content)
            # Like the API server: a client-chosen name is kept, else one is generated.
            name = claim["metadata"].get("name") or f"call-{len(self.claims)}"
            claim["metadata"] = {"name": name, "namespace": NS}
            self.claims[name] = claim
            if self.create_times_out:
                raise httpx.ReadTimeout("applied, answer lost", request=request)
            return httpx.Response(201, json=claim)
        name = request.url.path.rsplit("/", 1)[-1]
        if request.method == "DELETE":
            self.deleted.append(name)
            status = self.delete_answers.pop(0) if self.delete_answers else self.delete_status
            return httpx.Response(status, json={"kind": "Status"})
        self.gets += 1
        claim = dict(self.claims[name])
        if self.ready_after is not None and self.gets >= self.ready_after:
            claim["status"] = {
                "conditions": [{"type": "Ready", "status": "True"}],
                "sandbox": {"name": name, "podIPs": [self.pod_ip]},
            }
        return httpx.Response(200, json=claim)

    def methods(self) -> list[str]:
        return [r.method for r in self.requests]


class FakeSandbox:
    """Records every request; answers with `answer`, or forwards to the real runner server."""

    def __init__(self, server: Any) -> None:
        self.requests: list[httpx.Request] = []
        self.answer: Handler | None = None
        self._server = server

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.answer is not None:
            answer = await self.answer(request)
            # As a real transport does: the raw bytes as a stream, not yet read or decoded.
            return httpx.Response(answer.status_code, headers=answer.headers, stream=answer.stream)
        # A fresh app per request: each claimed pod is a fresh server.
        app = self._server.http_app(path="/mcp", stateless_http=True)
        async with app.router.lifespan_context(app):
            response = await httpx.ASGITransport(app=app).handle_async_request(request)
            body = await response.aread()
        return httpx.Response(
            response.status_code, headers=response.headers, stream=httpx.ByteStream(body)
        )


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "token"
    path.write_text(TOKEN)
    return path


@pytest.fixture
def fake_api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def fake_sandbox(tmp_path: Path) -> FakeSandbox:
    return FakeSandbox(create_server(tmp_root=tmp_path))


def _dispatcher(
    fake_api: FakeApi, fake_sandbox: FakeSandbox, token_file: Path, config: DispatchConfig = FAST
) -> Dispatcher:
    api = api_client(
        "https://10.96.0.1:443", token_file, verify=True, transport=httpx.MockTransport(fake_api)
    )
    sandbox = sandbox_client(transport=httpx.MockTransport(fake_sandbox))
    return Dispatcher(api, sandbox, config, now=lambda: NOW)


async def _call(dispatcher: Dispatcher, code: str, key: str, timeout_s: int = 2) -> Any:
    async with Client(create_dispatch_server(dispatcher)) as client:
        return await client.call_tool(
            TOOL_NAME, {"code": code, "timeout_s": timeout_s}, meta={"idempotency_key": key}
        )


@pytest.fixture
def dispatcher(fake_api: FakeApi, fake_sandbox: FakeSandbox, token_file: Path) -> Dispatcher:
    return _dispatcher(fake_api, fake_sandbox, token_file)


async def test_code_runner_dispatch_claims_waits_runs_and_deletes_in_order(
    dispatcher: Dispatcher, fake_api: FakeApi, fake_sandbox: FakeSandbox
) -> None:
    result = await _call(dispatcher, "print(6 * 7)", "k-1")
    assert result.structured_content["stdout"] == "42\n"
    assert fake_api.methods() == ["POST", "GET", "DELETE"]
    assert fake_api.deleted == list(fake_api.claims)
    assert [str(r.url) for r in fake_sandbox.requests] == ["http://10.244.0.7:8000/mcp"]
    delete = fake_api.requests[-1]
    assert delete.url.params["propagationPolicy"] == "Background"


async def test_code_runner_dispatch_claim_holds_no_caller_data(
    dispatcher: Dispatcher, fake_api: FakeApi
) -> None:
    await _call(dispatcher, "print('caller-secret')", "key-caller")
    body = json.loads(fake_api.requests[0].content)
    assert body == {
        "apiVersion": "extensions.agents.x-k8s.io/v1beta1",
        "kind": "SandboxClaim",
        "metadata": {"name": body["metadata"]["name"]},
        "spec": {
            "warmPoolRef": {"name": "code-runner"},
            "lifecycle": {"shutdownTime": "2026-10-09T12:01:00Z", "shutdownPolicy": "Delete"},
        },
    }


async def test_code_runner_dispatch_sends_only_create_get_delete_on_claims(
    dispatcher: Dispatcher, fake_api: FakeApi
) -> None:
    await _call(dispatcher, "print(1)", "k-a")
    fake_api.ready_after = None
    with pytest.raises(ToolError):
        await _call(dispatcher, "print(1)", "k-b")
    assert set(fake_api.methods()) == {"POST", "GET", "DELETE"}
    for request in fake_api.requests:
        assert request.url.host == "10.96.0.1"
        assert request.url.path.startswith(CLAIMS)


async def test_code_runner_dispatch_token_reaches_the_api_and_never_a_sandbox(
    dispatcher: Dispatcher, fake_api: FakeApi, fake_sandbox: FakeSandbox, token_file: Path
) -> None:
    await _call(dispatcher, "print(1)", "k-1")
    await asyncio.to_thread(token_file.write_text, "token-two")  # the projected token rotated
    await _call(dispatcher, "print(2)", "k-2")
    # The control: every API request carries the token, read again after the rotation.
    auth = [r.headers.get("authorization") for r in fake_api.requests]
    assert auth == ["Bearer token-one"] * 3 + ["Bearer token-two"] * 3
    assert len(fake_sandbox.requests) == 2
    for request in fake_sandbox.requests:
        assert "authorization" not in request.headers
        assert "token" not in json.dumps(dict(request.headers)).lower()
        assert b"token-" not in request.content


async def test_code_runner_dispatch_repeated_key_does_not_claim_twice(
    dispatcher: Dispatcher, fake_api: FakeApi
) -> None:
    first = await _call(dispatcher, RANDOM, "k-same")
    second = await _call(dispatcher, RANDOM, "k-same")
    assert first.structured_content == second.structured_content
    assert fake_api.methods().count("POST") == 1


async def test_code_runner_dispatch_ready_timeout_is_unavailable_and_deletes(
    dispatcher: Dispatcher, fake_api: FakeApi, fake_sandbox: FakeSandbox
) -> None:
    fake_api.ready_after = None
    with pytest.raises(ToolError, match=r"^sandbox_unavailable"):
        await _call(dispatcher, "print(1)", "k-1")
    assert fake_api.deleted == list(fake_api.claims)
    assert fake_sandbox.requests == []


async def test_code_runner_dispatch_no_free_slot_is_unavailable(
    fake_api: FakeApi, fake_sandbox: FakeSandbox, token_file: Path
) -> None:
    config = DispatchConfig(namespace=NS, max_claims=1, claim_timeout_s=0.2, poll_s=0.01)
    dispatcher = _dispatcher(fake_api, fake_sandbox, token_file, config)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(_request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(500)

    fake_sandbox.answer = held
    first = asyncio.create_task(_call(dispatcher, "print(1)", "k-first"))
    await entered.wait()
    with pytest.raises(ToolError, match=r"^sandbox_unavailable"):
        await _call(dispatcher, "print(2)", "k-second")
    assert fake_api.methods().count("POST") == 1
    release.set()
    with pytest.raises(ToolError, match=r"^sandbox_lost"):
        await first


@pytest.mark.parametrize(
    "pod_ip", ["evil.example", "169.254.169.254", "127.0.0.1", "", "10.0.0.1/8"]
)
async def test_code_runner_dispatch_pod_ip_that_is_not_a_pod_address_sends_nothing(
    dispatcher: Dispatcher, fake_api: FakeApi, fake_sandbox: FakeSandbox, pod_ip: str
) -> None:
    fake_api.pod_ip = pod_ip
    with pytest.raises(ToolError, match=r"^sandbox_unavailable"):
        await _call(dispatcher, "print(1)", "k-1")
    assert fake_sandbox.requests == []
    assert fake_api.deleted == list(fake_api.claims)


async def test_code_runner_dispatch_ipv6_pod_address_is_bracketed(
    dispatcher: Dispatcher, fake_api: FakeApi, fake_sandbox: FakeSandbox
) -> None:
    fake_api.pod_ip = "fd00:10:244::7"

    async def ok(_request: httpx.Request) -> httpx.Response:
        return _rpc({"structuredContent": _result("ok\n"), "isError": False})

    fake_sandbox.answer = ok
    await _call(dispatcher, "print(1)", "k-1")
    assert str(fake_sandbox.requests[0].url) == "http://[fd00:10:244::7]:8000/mcp"


def _result(stdout: str) -> dict[str, Any]:
    return {"stdout": stdout, "stderr": "", "exit_code": 0, "timed_out": False, "truncated": False}


def _rpc(result: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


async def test_code_runner_dispatch_response_over_the_cap_is_lost_and_frees_the_key(
    fake_api: FakeApi, fake_sandbox: FakeSandbox, token_file: Path
) -> None:
    config = DispatchConfig(namespace=NS, poll_s=0.01, response_cap_bytes=1024)
    dispatcher = _dispatcher(fake_api, fake_sandbox, token_file, config)

    async def big(_request: httpx.Request) -> httpx.Response:
        return _rpc({"structuredContent": _result("x" * 4096), "isError": False})

    fake_sandbox.answer = big
    with pytest.raises(ToolError, match=r"^sandbox_lost"):
        await _call(dispatcher, "print(1)", "k-big")
    assert fake_api.deleted == list(fake_api.claims)
    fake_sandbox.answer = None
    result = await _call(dispatcher, "print(1)", "k-big")
    assert result.structured_content["stdout"] == "1\n"
    assert fake_api.methods().count("POST") == 2


async def test_code_runner_dispatch_dropped_session_is_lost_and_frees_the_key(
    dispatcher: Dispatcher, fake_api: FakeApi, fake_sandbox: FakeSandbox
) -> None:
    async def dropped(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("peer closed connection", request=request)

    fake_sandbox.answer = dropped
    with pytest.raises(ToolError, match=r"^sandbox_lost"):
        await _call(dispatcher, "print(1)", "k-drop")
    assert fake_api.deleted == list(fake_api.claims)
    fake_sandbox.answer = None
    await _call(dispatcher, "print(1)", "k-drop")
    assert fake_api.methods().count("POST") == 2


async def test_code_runner_dispatch_no_answer_in_time_is_lost_and_deletes(
    fake_api: FakeApi, fake_sandbox: FakeSandbox, token_file: Path
) -> None:
    config = DispatchConfig(namespace=NS, poll_s=0.01, answer_grace_s=0)

    async def silent(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(500)

    fake_sandbox.answer = silent
    dispatcher = _dispatcher(fake_api, fake_sandbox, token_file, config)
    with pytest.raises(ToolError, match=r"^sandbox_lost"):
        await _call(dispatcher, "print(1)", "k-slow", timeout_s=1)
    assert fake_api.deleted == list(fake_api.claims)


async def test_code_runner_dispatch_retries_the_connect_until_the_server_listens(
    dispatcher: Dispatcher, fake_sandbox: FakeSandbox
) -> None:
    refused = 2

    async def starting(request: httpx.Request) -> httpx.Response:
        nonlocal refused
        if refused:
            refused -= 1
            raise httpx.ConnectError("connection refused", request=request)
        return _rpc({"structuredContent": _result("up\n"), "isError": False})

    fake_sandbox.answer = starting
    result = await _call(dispatcher, "print(1)", "k-1")
    assert result.structured_content["stdout"] == "up\n"
    assert len(fake_sandbox.requests) == 3


async def test_code_runner_dispatch_retries_a_connect_timeout_like_a_refused_connect(
    dispatcher: Dispatcher, fake_sandbox: FakeSandbox
) -> None:
    timed_out = 1

    async def slow_to_listen(request: httpx.Request) -> httpx.Response:
        nonlocal timed_out
        if timed_out:
            timed_out -= 1
            raise httpx.ConnectTimeout("connect timed out", request=request)
        return _rpc({"structuredContent": _result("up\n"), "isError": False})

    fake_sandbox.answer = slow_to_listen
    result = await _call(dispatcher, "print(1)", "k-ct")
    assert result.structured_content["stdout"] == "up\n"
    assert len(fake_sandbox.requests) == 2


async def test_code_runner_dispatch_logs_each_connect_retry_by_class_name_only(
    dispatcher: Dispatcher, fake_sandbox: FakeSandbox, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    timed_out = 1

    async def slow_to_listen(request: httpx.Request) -> httpx.Response:
        nonlocal timed_out
        if timed_out:
            timed_out -= 1
            raise httpx.ConnectTimeout("connect timed out", request=request)
        return _rpc({"structuredContent": _result("up\n"), "isError": False})

    fake_sandbox.answer = slow_to_listen
    await _call(dispatcher, "print(1)", "k-ct-log")
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("code_runner")]
    assert [m for m in ours if "connect_retry" in m] == [
        "dispatch connect_retry error=ConnectTimeout"
    ]
    assert not [m for m in ours if "10.244.0.7" in m]


async def test_code_runner_dispatch_connect_timeout_for_the_whole_window_is_lost(
    dispatcher: Dispatcher, fake_api: FakeApi, fake_sandbox: FakeSandbox, caplog: Any
) -> None:
    caplog.set_level("DEBUG")

    async def never_listens(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=request)

    fake_sandbox.answer = never_listens
    with pytest.raises(ToolError, match=r"^sandbox_lost"):
        await _call(dispatcher, "print(1)", "k-ct-all")
    assert fake_api.deleted == list(fake_api.claims)
    assert "error=ConnectTimeout" in caplog.text
    assert "10.244.0.7" not in caplog.text


async def test_code_runner_dispatch_read_timeout_after_the_send_is_not_retried(
    dispatcher: Dispatcher, fake_sandbox: FakeSandbox, caplog: Any
) -> None:
    caplog.set_level("DEBUG")

    async def sent_then_silent(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    fake_sandbox.answer = sent_then_silent
    with pytest.raises(ToolError, match=r"^sandbox_lost"):
        await _call(dispatcher, "print(1)", "k-rt")
    assert len(fake_sandbox.requests) == 1
    assert "error=ReadTimeout" in caplog.text


async def test_code_runner_dispatch_api_redirect_is_not_followed(
    dispatcher: Dispatcher, fake_api: FakeApi, fake_sandbox: FakeSandbox
) -> None:
    fake_api.create_answer = httpx.Response(307, headers={"location": "https://10.0.0.99/steal"})
    with pytest.raises(ToolError, match=r"^sandbox_unavailable"):
        await _call(dispatcher, "print(1)", "k-1")
    # A 3xx applied nothing, so no delete; nothing goes to the redirect target.
    assert [(r.method, r.url.host) for r in fake_api.requests] == [("POST", "10.96.0.1")]
    assert fake_sandbox.requests == []


async def test_code_runner_dispatch_sandbox_redirect_is_not_followed(
    dispatcher: Dispatcher, fake_api: FakeApi, fake_sandbox: FakeSandbox
) -> None:
    async def redirect(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"location": "https://10.96.0.1/apis"})

    fake_sandbox.answer = redirect
    with pytest.raises(ToolError, match=r"^sandbox_lost"):
        await _call(dispatcher, "print(1)", "k-1")
    assert len(fake_sandbox.requests) == 1
    assert fake_api.deleted == list(fake_api.claims)


async def test_code_runner_dispatch_errors_never_carry_upstream_bodies_or_the_token(
    dispatcher: Dispatcher, fake_api: FakeApi, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    fake_api.create_answer = httpx.Response(403, text=f"forbidden UPSTREAM-BODY {TOKEN}")
    with pytest.raises(ToolError) as raised:
        await _call(dispatcher, "print(1)", "k-1")
    assert str(raised.value) == "sandbox_unavailable: no sandbox could be claimed"
    assert "UPSTREAM-BODY" not in caplog.text
    assert TOKEN not in caplog.text


async def test_code_runner_dispatch_sandbox_tool_error_keeps_only_its_code(
    dispatcher: Dispatcher, fake_api: FakeApi, fake_sandbox: FakeSandbox
) -> None:
    async def failed(_request: httpx.Request) -> httpx.Response:
        text = "run_failed: UPSTREAM-DETAIL"
        return _rpc({"content": [{"type": "text", "text": text}], "isError": True})

    fake_sandbox.answer = failed
    with pytest.raises(ToolError) as raised:
        await _call(dispatcher, "print(1)", "k-1")
    assert str(raised.value) == "run_failed: the sandbox could not run the call"
    assert fake_api.deleted == list(fake_api.claims)


async def test_code_runner_dispatch_malformed_sandbox_answer_is_lost(
    dispatcher: Dispatcher, fake_sandbox: FakeSandbox
) -> None:
    async def odd(_request: httpx.Request) -> httpx.Response:
        return _rpc({"structuredContent": {"stdout": 1}, "isError": False})

    fake_sandbox.answer = odd
    with pytest.raises(ToolError, match=r"^sandbox_lost"):
        await _call(dispatcher, "print(1)", "k-1")


async def test_code_runner_dispatch_failed_delete_does_not_fail_the_call(
    dispatcher: Dispatcher, fake_api: FakeApi
) -> None:
    fake_api.delete_status = 500
    result = await _call(dispatcher, "print(1)", "k-1")
    assert result.structured_content["stdout"] == "1\n"
    assert dispatcher.delete_failures == 1


async def test_code_runner_dispatch_without_a_key_makes_no_claim(
    dispatcher: Dispatcher, fake_api: FakeApi
) -> None:
    async with Client(create_dispatch_server(dispatcher)) as client:
        with pytest.raises(ToolError, match="idempotency_key_required"):
            await client.call_tool(TOOL_NAME, {"code": "print(1)"})
    assert fake_api.requests == []


async def test_code_runner_dispatch_health_never_calls_the_api(
    dispatcher: Dispatcher, fake_api: FakeApi
) -> None:
    app = create_dispatch_server(dispatcher).http_app(path="/mcp", stateless_http=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://dispatch") as client:
        response = await client.get("/health")
    assert response.json() == {"status": "ok"}
    assert fake_api.requests == []


def test_code_runner_dispatch_clients_follow_no_redirect_and_ignore_the_env(
    token_file: Path,
) -> None:
    api = api_client("https://10.96.0.1:443", token_file, verify=True)
    sandbox = sandbox_client()
    for client in (api, sandbox):
        assert client.follow_redirects is False
        assert client.trust_env is False
    assert sandbox.auth is None
    assert "authorization" not in sandbox.headers


def test_code_runner_dispatch_api_base_url_comes_from_the_service_env() -> None:
    env = {"KUBERNETES_SERVICE_HOST": "10.96.0.1", "KUBERNETES_SERVICE_PORT": "443"}
    assert api_base_url(env) == "https://10.96.0.1:443"
    v6 = {"KUBERNETES_SERVICE_HOST": "fd00:10:96::1", "KUBERNETES_SERVICE_PORT": "443"}
    assert api_base_url(v6) == "https://[fd00:10:96::1]:443"
    for bad in ({}, {"KUBERNETES_SERVICE_HOST": "api.evil", "KUBERNETES_SERVICE_PORT": "443"}):
        with pytest.raises(ValueError, match="KUBERNETES_SERVICE"):
            api_base_url(bad)


def test_code_runner_cli_dispatch_mode_parses_and_serve_stays_the_default() -> None:
    args = cli.dispatch_parser().parse_args(["--pool", "code-runner", "--namespace", NS])
    assert (args.pool, args.namespace, args.port) == ("code-runner", NS, 8000)
    assert cli.serve_parser().parse_args(["--port", "8000"]).port == 8000


async def test_code_runner_dispatch_claim_name_is_chosen_by_the_dispatcher_when_it_creates(
    dispatcher: Dispatcher, fake_api: FakeApi
) -> None:
    """Each claim gets its own `call-<uuid>` name, so cleanup never waits for the answer."""
    await _call(dispatcher, "print(1)", "k-1")
    await _call(dispatcher, "print(2)", "k-2")

    names = [json.loads(r.content)["metadata"]["name"] for r in fake_api.requests if r.content]
    assert [n.startswith("call-") and len(n) == 37 for n in names] == [True, True]
    assert names[0] != names[1]
    assert fake_api.deleted == names


async def test_code_runner_dispatch_claim_is_deleted_by_name_when_the_create_answer_times_out(
    dispatcher: Dispatcher, fake_api: FakeApi
) -> None:
    """A create the API server applied but whose answer was lost still gets its delete."""
    fake_api.create_times_out = True

    with pytest.raises(ToolError, match=r"^sandbox_unavailable"):
        await _call(dispatcher, "print(1)", "k-1")

    assert len(fake_api.claims) == 1
    assert fake_api.deleted == list(fake_api.claims)


async def test_code_runner_dispatch_delete_is_retried_when_the_first_delete_fails(
    dispatcher: Dispatcher, fake_api: FakeApi
) -> None:
    """A failed delete is tried again; one that then works is not counted as a failure."""
    fake_api.delete_answers = [500]

    await _call(dispatcher, "print(1)", "k-1")

    assert fake_api.deleted == list(fake_api.claims) * 2
    assert dispatcher.delete_failures == 0


async def test_code_runner_dispatch_delete_error_is_counted_when_the_token_cannot_be_read(
    dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A delete that raises ValueError (an unreadable token) is retried and counted, not raised."""

    async def broken(*_args: object, **_kwargs: object) -> httpx.Response:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(dispatcher._api, "delete", broken)

    result = await _call(dispatcher, "print(1)", "k-1")

    assert result.structured_content["stdout"] == "1\n"
    assert dispatcher.delete_failures == 1


async def test_code_runner_dispatch_create_is_unavailable_when_the_token_cannot_be_read(
    dispatcher: Dispatcher, fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A create that raises ValueError (an unreadable token) is `sandbox_unavailable`, no delete."""

    async def broken(*_args: object, **_kwargs: object) -> httpx.Response:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(dispatcher._api, "post", broken)

    with pytest.raises(ToolError, match=r"^sandbox_unavailable"):
        await _call(dispatcher, "print(1)", "k-1")

    assert fake_api.deleted == []


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), httpx.ConnectTimeout("connect timed out")],
    ids=["connect-error", "connect-timeout"],
)
async def test_code_runner_dispatch_sends_no_delete_when_the_create_never_connected(
    dispatcher: Dispatcher, fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """A create that never reached the API server applied nothing, so no delete is sent."""

    async def unreachable(*_args: object, **_kwargs: object) -> httpx.Response:
        raise failure

    monkeypatch.setattr(dispatcher._api, "post", unreachable)

    with pytest.raises(ToolError, match=r"^sandbox_unavailable"):
        await _call(dispatcher, "print(1)", "k-1")

    assert fake_api.deleted == []
    assert dispatcher.delete_failures == 0


async def test_code_runner_dispatch_sends_no_delete_when_the_create_is_refused(
    dispatcher: Dispatcher, fake_api: FakeApi
) -> None:
    """A 4xx answer to the create applied nothing, so no delete is sent."""
    fake_api.create_answer = httpx.Response(403, json={"kind": "Status"})

    with pytest.raises(ToolError, match=r"^sandbox_unavailable"):
        await _call(dispatcher, "print(1)", "k-1")

    assert fake_api.methods() == ["POST"]


async def test_code_runner_dispatch_deletes_by_name_when_the_create_answers_5xx(
    dispatcher: Dispatcher, fake_api: FakeApi
) -> None:
    """A 5xx answer may follow an applied create, so the claim is deleted by name."""
    fake_api.create_answer = httpx.Response(500, json={"kind": "Status"})

    with pytest.raises(ToolError, match=r"^sandbox_unavailable"):
        await _call(dispatcher, "print(1)", "k-1")

    assert fake_api.methods() == ["POST", "DELETE"]


async def test_code_runner_dispatch_deeply_nested_answer_is_lost_with_the_fixed_text_when_parsed(
    dispatcher: Dispatcher, fake_sandbox: FakeSandbox
) -> None:
    """100 KB of nested `[` ends `sandbox_lost` with the fixed text, no interpreter text."""

    async def nested(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"[" * 100_000, headers={"content-type": "application/json"}
        )

    fake_sandbox.answer = nested

    with pytest.raises(ToolError) as raised:
        await _call(dispatcher, "print(1)", "k-1")

    assert str(raised.value) == "sandbox_lost: the sandbox gave no usable answer"


async def test_code_runner_dispatch_gzip_answer_is_lost_holding_little_when_it_inflates_past_cap(
    dispatcher: Dispatcher, fake_sandbox: FakeSandbox
) -> None:
    """An answer that gunzips to 50 MB ends `sandbox_lost` and is never inflated."""
    bomb = gzip.compress(b"0" * (50 * 1024 * 1024))

    async def compressed(_request: httpx.Request) -> httpx.Response:
        headers = {"content-type": "application/json", "content-encoding": "gzip"}
        return httpx.Response(200, stream=httpx.ByteStream(bomb), headers=headers)

    fake_sandbox.answer = compressed
    tracemalloc.start()

    try:
        with pytest.raises(RunnerError) as raised:
            await dispatcher.run("print(1)", 2, "k-1")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert raised.value.code == "sandbox_lost"
    assert peak < 8 * 1024 * 1024


async def test_code_runner_dispatch_asks_the_sandbox_for_an_uncompressed_answer(
    dispatcher: Dispatcher, fake_sandbox: FakeSandbox
) -> None:
    """The sandbox call accepts only `identity`, so the size cap counts what is held."""
    await _call(dispatcher, "print(1)", "k-1")

    assert fake_sandbox.requests[0].headers["accept-encoding"] == "identity"


async def test_code_runner_dispatch_lists_the_same_run_python_as_the_in_pod_server_when_listed(
    dispatcher: Dispatcher, tmp_path: Path
) -> None:
    """The dispatcher and the in-pod server show LiteLLM one tool definition."""
    async with Client(create_dispatch_server(dispatcher)) as client:
        dispatched = await client.list_tools()
    async with Client(create_server(tmp_root=tmp_path)) as client:
        in_pod = await client.list_tools()

    assert [t.model_dump() for t in dispatched] == [t.model_dump() for t in in_pod]
