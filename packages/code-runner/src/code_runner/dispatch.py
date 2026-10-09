"""The dispatcher: a fresh sandbox per `run_python` call (per-call sandbox plan, "Decision").

Per call it creates a `SandboxClaim` on the code-runner warm pool, waits for it to be Ready,
calls `run_python` on the claimed pod, and deletes the claim on every path. It never runs code
itself and never reuses a pod. The idempotency cache lives here, in front of the sandboxes.

Two `httpx` clients, never shared. The API client is the only one that sends the service
account token. The sandbox client sends no credential of any kind. Errors are fixed strings:
no upstream body, and never the token, goes into a message or a log line.

No `from __future__ import annotations` here: FastMCP reads the tool's live annotations.
"""

import asyncio
import ipaddress
import json
import logging
import re
import ssl
import time
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from code_runner.runner import DEFAULT_LIMITS, Limits, RunnerError, RunResult
from code_runner.server import (
    CACHE_SIZE,
    DEFAULT_TIMEOUT_S,
    MIN_TIMEOUT_S,
    TOOL_NAME,
    ResultCache,
    fingerprint,
    resolve_key,
)

API_GROUP_PATH = "/apis/extensions.agents.x-k8s.io/v1beta1"
TOKEN_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
SANDBOX_PORT = 8000
SANDBOX_PATH = "/mcp"
API_TIMEOUT_S = 5.0  # suggested: one API request
SANDBOX_CONNECT_S = 1.0  # suggested: one connect attempt; the retry window is in the config

UNAVAILABLE = "sandbox_unavailable"  # nothing ran; safe to retry with the same key
LOST = "sandbox_lost"  # the sandbox died, did not answer, or answered too much; key freed
# Tool error codes the sandbox's own server may answer with; only the code is passed on.
SANDBOX_CODES = frozenset({"idempotency_key_required", "bad_arguments", "run_failed"})

_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_RPC_ID = 1

log = logging.getLogger("code_runner.dispatch")


@dataclass(frozen=True)
class DispatchConfig:
    """Per-call sandbox settings. Values are `suggested:` in the per-call sandbox plan."""

    namespace: str
    pool: str = "code-runner"
    max_claims: int = 2
    claim_timeout_s: float = 20
    poll_s: float = 0.1
    shutdown_after_s: float = 60
    answer_grace_s: float = 5
    connect_retry_s: float = 2
    response_cap_bytes: int = 256 * 1024


class _TokenFile(httpx.Auth):
    """Reads the projected token on every request: it rotates."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        token = self._path.read_text(encoding="utf-8").strip()
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


def api_base_url(env: Mapping[str, str]) -> str:
    """The API server URL from the in-cluster service variables; an IP and a port only."""
    host = env.get("KUBERNETES_SERVICE_HOST", "")
    port = env.get("KUBERNETES_SERVICE_PORT", "")
    try:
        address = ipaddress.ip_address(host)
        number = int(port)
    except ValueError:
        address, number = None, 0
    if address is None or not 0 < number < 65536:
        raise ValueError(
            "KUBERNETES_SERVICE_HOST and KUBERNETES_SERVICE_PORT must be an IP and port"
        )
    return f"https://{_url_host(address)}:{number}"


def api_client(
    base_url: str,
    token_file: str | Path,
    *,
    verify: ssl.SSLContext | bool,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """The only client that sends the token. No redirects, nothing taken from the environment."""
    return httpx.AsyncClient(
        base_url=base_url,
        auth=_TokenFile(token_file),
        verify=verify,
        follow_redirects=False,
        trust_env=False,
        timeout=API_TIMEOUT_S,
        transport=transport,
    )


def api_client_from_env(
    env: Mapping[str, str], *, token_file: str | Path = TOKEN_FILE, ca_file: str | Path = CA_FILE
) -> httpx.AsyncClient:
    """The API client for the dispatcher pod: the cluster CA verifies the API server."""
    verify = ssl.create_default_context(cafile=str(ca_file))
    return api_client(api_base_url(env), token_file, verify=verify)


def sandbox_client(*, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """The client for the claimed pods: no auth of any kind, no redirects, no environment."""
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(None, connect=SANDBOX_CONNECT_S),
        transport=transport,
    )


def _url_host(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    return f"[{address}]" if address.version == 6 else str(address)


def _unavailable(message: str) -> RunnerError:
    return RunnerError(UNAVAILABLE, message)


def _lost(message: str) -> RunnerError:
    return RunnerError(LOST, message)


def _pod_host(value: object) -> str:
    """`podIPs[0]` as a URL host. Anything but a plain pod address is refused."""
    try:
        address = ipaddress.ip_address(value if isinstance(value, str) else "")
    except ValueError:
        raise _unavailable("the sandbox has no usable address") from None
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or getattr(address, "scope_id", None)
    ):
        raise _unavailable("the sandbox has no usable address")
    return _url_host(address)


def _ready_ip(claim: object) -> object | None:
    """`status.sandbox.podIPs[0]` once the claim is Ready, else None."""
    status = claim.get("status") if isinstance(claim, dict) else None
    if not isinstance(status, dict):
        return None
    conditions = status.get("conditions")
    ready = isinstance(conditions, list) and any(
        isinstance(c, dict) and c.get("type") == "Ready" and c.get("status") == "True"
        for c in conditions
    )
    sandbox = status.get("sandbox")
    ips = sandbox.get("podIPs") if isinstance(sandbox, dict) else None
    if not ready or not isinstance(ips, list) or not ips:
        return None
    first: object = ips[0]
    return first


def _rpc_message(content_type: str, data: bytes) -> dict[str, Any]:
    """The JSON-RPC answer to our one request, from a JSON or an SSE body."""
    if "text/event-stream" in content_type:
        for line in data.decode("utf-8").splitlines():
            if not line.startswith("data:"):
                continue
            message = json.loads(line[len("data:") :])
            if isinstance(message, dict) and message.get("id") == _RPC_ID:
                return message
        raise ValueError("no answer")
    message = json.loads(data)
    if not isinstance(message, dict):
        raise ValueError("not an object")
    return message


def _run_result(result: object) -> RunResult:
    if not isinstance(result, dict):
        raise ValueError("no result")
    out = result.get("structuredContent")
    if not isinstance(out, dict):
        raise ValueError("no structured content")
    stdout, stderr = out.get("stdout"), out.get("stderr")
    exit_code, timed_out, truncated = (
        out.get("exit_code"),
        out.get("timed_out"),
        out.get("truncated"),
    )
    if not (isinstance(stdout, str) and isinstance(stderr, str)):
        raise ValueError("bad streams")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise ValueError("bad exit code")
    if not (isinstance(timed_out, bool) and isinstance(truncated, bool)):
        raise ValueError("bad flags")
    return RunResult(stdout, stderr, exit_code, timed_out, truncated)


def _tool_error(result: dict[str, Any]) -> RunnerError:
    """The sandbox's tool error keeps its stable code only; its text is dropped."""
    content = result.get("content")
    first = content[0] if isinstance(content, list) and content else None
    text = first.get("text") if isinstance(first, dict) else None
    code = text.split(":", 1)[0] if isinstance(text, str) else ""
    if code in SANDBOX_CODES:
        return RunnerError(code, "the sandbox could not run the call")
    return _lost("the sandbox gave no usable answer")


def _answer(content_type: str, data: bytes) -> RunResult:
    try:
        message = _rpc_message(content_type, data)
        result = message.get("result")
        if isinstance(result, dict) and result.get("isError") is True:
            raise _tool_error(result)
        return _run_result(result)
    except (ValueError, UnicodeDecodeError):
        raise _lost("the sandbox gave no usable answer") from None


class Dispatcher:
    """Claims a sandbox per call. `cache` keeps results by idempotency key, in memory."""

    def __init__(
        self,
        api: httpx.AsyncClient,
        sandbox: httpx.AsyncClient,
        config: DispatchConfig,
        *,
        cache_size: int = CACHE_SIZE,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._api = api
        self._sandbox = sandbox
        self.config = config
        self.cache = ResultCache(cache_size)
        self._slots = asyncio.Semaphore(config.max_claims)
        self._now = now
        self._claims = f"{API_GROUP_PATH}/namespaces/{config.namespace}/sandboxclaims"
        self.delete_failures = 0

    async def call(self, code: str, timeout_s: int, key: str) -> RunResult:
        """A repeated key returns the first result without a claim; a failure frees the key."""

        async def run() -> RunResult:
            return await self.run(code, timeout_s, key)

        return await self.cache.get_or_run(key, fingerprint(code, timeout_s), run)

    async def run(self, code: str, timeout_s: int, key: str) -> RunResult:
        started = time.monotonic()
        outcome = "ok"
        try:
            return await self._run_in_slot(code, timeout_s, key)
        except RunnerError as exc:
            outcome = exc.code
            raise
        finally:
            log.info(
                "dispatch outcome=%s code_bytes=%d seconds=%.2f",
                outcome,
                len(code.encode("utf-8")),
                time.monotonic() - started,
            )

    async def _run_in_slot(self, code: str, timeout_s: int, key: str) -> RunResult:
        try:
            async with asyncio.timeout(self.config.claim_timeout_s):
                await self._slots.acquire()
        except TimeoutError:
            raise _unavailable("no free sandbox slot") from None
        try:
            name = await self._create()
            try:
                host = await self._wait_ready(name)
                return await self._call_sandbox(host, code, timeout_s, key)
            finally:
                # Shielded: a cancelled call still deletes its claim. shutdownTime is the backstop.
                await asyncio.shield(self._delete(name))
        finally:
            self._slots.release()

    def _claim_body(self) -> dict[str, Any]:
        """The pool name and the lifecycle only: nothing from the caller."""
        shutdown = self._now() + timedelta(seconds=self.config.shutdown_after_s)
        return {
            "apiVersion": "extensions.agents.x-k8s.io/v1beta1",
            "kind": "SandboxClaim",
            "metadata": {"generateName": "call-"},
            "spec": {
                "warmPoolRef": {"name": self.config.pool},
                "lifecycle": {
                    "shutdownTime": shutdown.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "shutdownPolicy": "Delete",
                },
            },
        }

    async def _create(self) -> str:
        try:
            response = await self._api.post(self._claims, json=self._claim_body())
            name = response.json()["metadata"]["name"] if response.status_code in (200, 201) else ""
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
            name = ""
        if not isinstance(name, str) or not _NAME.match(name):
            raise _unavailable("no sandbox could be claimed")
        return name

    async def _wait_ready(self, name: str) -> str:
        try:
            async with asyncio.timeout(self.config.claim_timeout_s):
                while True:
                    ip = await self._ready_ip(name)
                    if ip is not None:
                        return _pod_host(ip)
                    await asyncio.sleep(self.config.poll_s)
        except TimeoutError:
            raise _unavailable("the sandbox was not ready in time") from None

    async def _ready_ip(self, name: str) -> object | None:
        try:
            response = await self._api.get(f"{self._claims}/{name}")
            return _ready_ip(response.json()) if response.status_code == 200 else None
        except (httpx.HTTPError, OSError, ValueError):
            return None

    async def _delete(self, name: str) -> None:
        try:
            response = await self._api.delete(
                f"{self._claims}/{name}", params={"propagationPolicy": "Background"}
            )
            deleted = response.status_code in (200, 202, 404)
        except (httpx.HTTPError, OSError):
            deleted = False
        if not deleted:
            self.delete_failures += 1
            log.warning("dispatch claim_delete_failed total=%d", self.delete_failures)

    async def _call_sandbox(self, host: str, code: str, timeout_s: int, key: str) -> RunResult:
        url = f"http://{host}:{SANDBOX_PORT}{SANDBOX_PATH}"
        body = {
            "jsonrpc": "2.0",
            "id": _RPC_ID,
            "method": "tools/call",
            "params": {
                "name": TOOL_NAME,
                "arguments": {"code": code, "timeout_s": timeout_s},
                "_meta": {"idempotency_key": key},
            },
        }
        try:
            async with asyncio.timeout(timeout_s + self.config.answer_grace_s):
                content_type, data = await self._post_with_retry(url, body)
        except TimeoutError:
            raise _lost("the sandbox did not answer in time") from None
        return _answer(content_type, data)

    async def _post_with_retry(self, url: str, body: dict[str, Any]) -> tuple[str, bytes]:
        """Retries only a refused connect: Ready may come before the server listens."""
        deadline = time.monotonic() + self.config.connect_retry_s
        while True:
            try:
                return await self._post(url, body)
            except httpx.ConnectError:
                if time.monotonic() >= deadline:
                    raise _lost("the sandbox ended before it answered") from None
            await asyncio.sleep(self.config.poll_s)

    async def _post(self, url: str, body: dict[str, Any]) -> tuple[str, bytes]:
        headers = {"Accept": "application/json, text/event-stream"}
        cap = self.config.response_cap_bytes
        try:
            async with self._sandbox.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code != 200:
                    raise _lost("the sandbox gave no usable answer")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data += chunk
                    if len(data) > cap:
                        raise _lost("the sandbox answer was over the size cap")
                return response.headers.get("content-type", ""), bytes(data)
        except httpx.ConnectError:
            raise
        except httpx.HTTPError:
            raise _lost("the sandbox ended before it answered") from None


def create_dispatch_server(dispatcher: Dispatcher, *, limits: Limits = DEFAULT_LIMITS) -> FastMCP:
    """The MCP server LiteLLM registers as `code_runner`: the same tool, run in a fresh sandbox."""
    mcp = FastMCP(
        "code-runner",
        instructions=(
            f"{TOOL_NAME} runs Python in a sandbox with no network. "
            "Print what you need: the result has stdout, stderr, exit_code, timed_out, truncated."
        ),
    )
    max_timeout = int(limits.max_timeout_s)

    @mcp.tool(
        name=TOOL_NAME,
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    async def run_python_tool(
        code: Annotated[str, Field(description="Python source, run as __main__.")],
        timeout_s: Annotated[
            int,
            Field(ge=MIN_TIMEOUT_S, le=max_timeout, description="Wall-clock limit in seconds."),
        ] = DEFAULT_TIMEOUT_S,
        idempotency_key: Annotated[
            str | None,
            Field(description="Fallback when the client cannot send _meta.idempotency_key."),
        ] = None,
    ) -> RunResult:
        """Run Python 3.12 (standard library only) with no network and no files kept.

        Each call runs in a fresh sandbox that is deleted afterwards. stdout and stderr are
        each cut at a fixed size; `truncated` says so. A write tool: the same idempotency key
        returns the first result without running the code again.
        """
        try:
            key = resolve_key(idempotency_key)
            if len(code.encode("utf-8")) > limits.max_code_bytes:
                raise RunnerError("bad_arguments", f"code is over {limits.max_code_bytes} bytes")
            return await dispatcher.call(code, timeout_s, key)
        except RunnerError as exc:
            raise ToolError(str(exc)) from None

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        # Never calls the API: a slow API server must not get the dispatcher restarted.
        return JSONResponse({"status": "ok"})

    return mcp
