"""PoC-5 exit criterion 1, the no-cluster part: the hostile checks that need no cluster run
offline in `make test` on every commit.

There is no probe workload (decision 2026-10-02: T10, `packages/workloads/hostile`, is dropped).
Each check is framed as "the control refuses X", next to its paired allowed control in the same
test, and is driven through the existing harness (`poc05_harness.remote_lane`): the chassis app,
`RemoteConnector` with the token over a Unix socket, the `echo_python` workload served by its
own CLI, and the chassis's remote listener, with the scripted model and the fake tools behind
the ports. No TCP, no key.

Ids from `notes/2026-10-02-threat-model.md`:

- H08: a tool outside the allow-list, called through the chassis, is refused `tool_denied`; an
  allow-listed tool works (`test_h08_*`).
- H13: the chassis's public port binds the pod IP, the proxy loopback (`test_h13_*`). The CLI
  has no rule for the public `--host`; the manifests set it, and this test drives the manifests'
  own args through the real CLI to the bind addresses.
- H15, H16: model calls with no `traceparent` are served up to the uncorrelated cap, then 429
  `budget_exhausted`; a call inside a run is 200 (`test_h15_h16_*`).
- H17: covered end to end by `test_poc05_remote_lane_offline.py`; `test_existing_coverage_*`
  names the tests and checks that they still exist, so they are not duplicated here.
- H29: a model route outside the key's routes is refused; past the run budget 429; an in-scope
  call 200 (`test_h29_*`). The route scope is LiteLLM's virtual key, so offline it is a stand-in
  (see the test); the kind tier checks the real key.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import httpx2
import poc05_harness
import pytest
import yaml
from chassis.core.inbound import public_message
from chassis.fakes import ScriptedModel
from chassis.fakes.tool import UNLISTED_MARKER, UNLISTED_PROBE, write_mode_tools
from chassis.ports.model import ModelChunk, ModelError, ModelMessage, ModelResult, ToolSpec
from chassis.server import ChassisConfig
from chassis.server.cli import build_all, parse_args
from chassis.server.proxy_app import create_proxy_app
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from poc05_harness import (
    HOLD,
    REMOTE_PROXY_URL,
    REPLY,
    ROUTE,
    HeldRun,
    RemoteLane,
    new_token,
    new_trace_id,
    remote_lane,
    scripted_model,
)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
AGENTS = REPO / "deploy" / "kind" / "poc05" / "agents"
CHAT = "/v1/chat/completions"


def _code(body: Any) -> str:
    return str(body.get("error", {}).get("code")) if isinstance(body, dict) else ""


def _chat_body(route: str = ROUTE) -> dict[str, Any]:
    return {"model": route, "messages": [{"role": "user", "content": "check"}], "max_tokens": 5}


async def _remote_chat(lane: RemoteLane, trace: str | None, route: str = ROUTE) -> tuple[int, Any]:
    """One model call to the remote listener with the remote's own token, as the remote pod."""
    headers = {} if trace is None else {"traceparent": trace}
    async with lane.remote_proxy_client(lane.tokens.sends) as client:
        response = await client.post(CHAT, json=_chat_body(route), headers=headers)
    return response.status_code, response.json()


# --- H08: the tool allow-list, through the chassis's tool endpoint on the remote listener ---


def _mcp_client(lane: RemoteLane, trace: str) -> Client[Any]:
    """An MCP client of the chassis's `/mcp` on the remote listener, with the remote's own token
    and the run's `traceparent`, over the listener's Unix socket. Never TCP.
    """
    uds = lane.remote_proxy_uds

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **kw: Any,
    ) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.AsyncHTTPTransport(uds=uds),
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kw,
        )

    headers = {"Authorization": f"Bearer {lane.tokens.sends}", "traceparent": trace}
    transport = StreamableHttpTransport(
        f"{REMOTE_PROXY_URL}/mcp", headers=headers, httpx_client_factory=factory
    )
    return Client(transport)


async def test_h08_a_tool_off_the_allow_list_is_refused_through_the_chassis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit criterion 1 (offline), H08: the tool port is the write-mode set (`glossary_lookup`
    and `note_write` allowed, `unlisted_probe` behind the port but off the allow-list). Inside a
    run, through the chassis's `/mcp` on the remote listener with the remote's own token:
    `unlisted_probe` is not listed, and a call to it is refused with the public error code
    `tool_denied` and its fixed public message, before the tool runs. The control: the same
    client, in the same run, calls the allow-listed `glossary_lookup` and gets its answer.
    """
    monkeypatch.setattr(poc05_harness, "default_tools", write_mode_tools)
    async with remote_lane(monkeypatch) as lane:
        tools = lane.tools.inner
        async with lane.held_run() as run, _mcp_client(lane, run.traceparent) as mcp:
            listed = {t.name for t in await mcp.list_tools()}
            refused = await mcp.call_tool(UNLISTED_PROBE.name, {}, raise_on_error=False)
            allowed = await mcp.call_tool("glossary_lookup", {"term": "SLM"}, raise_on_error=False)
        calls = list(tools.calls)
    assert run.response is not None and run.response.json()["status"] == "ok", run.response
    assert allowed.is_error is False, allowed
    assert "SLM" in "".join(getattr(part, "text", "") for part in allowed.content), allowed
    assert "glossary_lookup" in listed and UNLISTED_PROBE.name not in listed, listed
    assert refused.is_error is True, refused
    assert refused.structured_content == {
        "code": "tool_denied",
        "message": public_message("tool_denied"),
        "retryable": False,
    }, refused.structured_content
    text = "".join(getattr(part, "text", "") for part in refused.content)
    assert UNLISTED_MARKER not in text, text
    # The refusal reached the port (the gateway refuses it) but the tool never ran.
    assert (UNLISTED_PROBE.name, {}) in calls, calls


# --- H13: the bind addresses, from the manifests' own args through the real CLI ---


POD_IP = "10.244.0.7"
"""suggested: a pod IP in kind's default pod range, standing in for `$(POD_IP)`."""
CHASSIS_MANIFESTS = {
    "agent-echo": (AGENTS / "agent-echo.yaml", AGENTS / "chassis" / "echo.yaml"),
    "chassis-echo-remote": (
        AGENTS / "chassis-echo-remote.yaml",
        AGENTS / "chassis" / "echo-remote.yaml",
    ),
}


def _chassis_args(manifest: Path) -> list[str]:
    """The chassis container's args in `manifest` (a container or init container named
    `chassis`), with `$(POD_IP)` filled the way the kubelet fills it.
    """
    for doc in yaml.safe_load_all(manifest.read_text()):
        if not isinstance(doc, dict) or doc.get("kind") != "Deployment":
            continue
        spec = doc["spec"]["template"]["spec"]
        for container in [*spec.get("initContainers", []), *spec.get("containers", [])]:
            if container["name"] == "chassis":
                return [str(a).replace("$(POD_IP)", POD_IP) for a in container["args"]]
    raise AssertionError(f"no chassis container in {manifest}")


def _offline_config(source: Path, tmp_path: Path) -> Path:
    """The pod's chassis config with the fake profile and fake adapters, so nothing reaches a
    service; the engine and its auth (what the CLI checks) stay as the manifest has them.
    """
    data = yaml.safe_load(source.read_text())
    data["profile"] = "fake"
    data["spec"]["adapters"] = {"model": "fake"}
    path = tmp_path / source.name
    path.write_text(yaml.safe_dump(data))
    return path


def _with_config(args: Sequence[str], config: Path) -> list[str]:
    out = list(args)
    out[out.index("--config") + 1] = str(config)
    return out


@pytest.mark.parametrize("pod", list(CHASSIS_MANIFESTS))
def test_h13_the_public_port_binds_the_pod_ip_and_the_proxy_loopback(
    pod: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit criterion 1 (offline), H13: the chassis container's args from the PoC-5 manifest,
    with `$(POD_IP)` filled, go through the real `chassis serve` argument checks and
    `build_all`. The public listener's bind address is the pod IP, neither loopback nor a
    wildcard, so a workload's `connect 127.0.0.1:8080` finds nothing. The allowed control: the
    loopback proxy binds `127.0.0.1` (and the remote listener, when present, the pod IP). Nothing
    binds.

    The CLI has no rule that refuses a loopback `--host`: what refuses it is the manifests (also
    pinned by `test_poc05_hardening_static.py::test_chassis_public_port_binds_the_pod_ip`). The
    CLI's own refusals for the proxy hosts are `test_poc05_remote_lane_offline.py::test_h13_*`.
    """
    manifest, config = CHASSIS_MANIFESTS[pod]
    source = yaml.safe_load(config.read_text())
    auth = source["spec"]["engine"].get("auth")
    if auth is not None:
        monkeypatch.setenv(auth["token_env"], new_token())
    args = parse_args(
        ["serve", *_with_config(_chassis_args(manifest), _offline_config(config, tmp_path))]
    )
    servers = build_all(args)
    public = servers.public.config.host
    assert public == POD_IP, public
    assert public not in {"0.0.0.0", "::", "127.0.0.1", "::1", "localhost"}, public
    assert servers.proxy.config.host == "127.0.0.1", servers.proxy.config.host
    if auth is not None:
        assert servers.remote is not None and servers.remote.config.host == POD_IP
    else:
        assert servers.remote is None


# --- H15, H16: the uncorrelated cap on the loopback proxy, next to a real run's calls ---


UNCORRELATED_CAP = 30
"""Two of the scripted model's calls (15 tokens each) fit; the third is refused."""


def _with_cap(config: ChassisConfig, cap: int) -> ChassisConfig:
    data = config.model_dump(mode="json", by_alias=True, exclude_unset=True)
    data["spec"].setdefault("limits", {})["uncorrelated_tokens_per_minute"] = cap
    return ChassisConfig.model_validate(data)


async def test_h15_h16_uncorrelated_calls_are_served_up_to_the_cap_then_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit criterion 1 (offline), H15 and H16: the loopback proxy (the sidecar's, no
    credential, by design) serves model calls with no `traceparent` and counts them
    uncorrelated, up to `spec.limits.uncorrelated_tokens_per_minute`; past it, 429
    `budget_exhausted` before the model is called. The allowed control, with the cap spent: a
    whole run through the chassis makes its model call (correlated, so not capped) and ends
    `ok`, and a call on the loopback proxy that names the run is 200.
    """
    async with remote_lane(monkeypatch) as lane:
        lane.app.state.config = _with_cap(lane.app.state.config, UNCORRELATED_CAP)
        proxy = create_proxy_app(lane.app)
        transport = httpx.ASGITransport(app=proxy)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as client:
            served = [(await client.post(CHAT, json=_chat_body())) for _ in range(2)]
            refused = await client.post(CHAT, json=_chat_body())
            model_calls = len(lane.model.calls)
            async with lane.held_run() as run:
                inside = await client.post(
                    CHAT, json=_chat_body(), headers={"traceparent": run.traceparent}
                )
        uncorrelated = lane.counter_total("chassis.model_calls_uncorrelated")
    assert [r.status_code for r in served] == [200, 200], [r.text for r in served]
    assert refused.status_code == 429 and _code(refused.json()) == "budget_exhausted", refused
    assert model_calls == 2, model_calls
    assert uncorrelated == 3, uncorrelated
    assert inside.status_code == 200, inside.text
    assert inside.json()["choices"][0]["message"]["content"] == REPLY
    assert run.response is not None and run.response.json()["status"] == "ok", run.response


# --- H29: route scope and run budget ---


IN_SCOPE = frozenset({ROUTE})
OUT_OF_SCOPE = "big-unscoped"
"""suggested: a route the key does not list."""


class KeyScopedModel(ScriptedModel):
    """The scripted model behind a stand-in for LiteLLM's virtual-key route scope: a route the
    key does not list raises what `LiteLLMModel` raises for LiteLLM's 401 on it (`http_401`, not
    retryable; `packages/chassis/tests/test_litellm.py::test_401_is_a_model_error_and_not_
    retryable`). The scope is LiteLLM's, not the chassis's; this pins the chassis's half.
    """

    def __init__(self, inner: ScriptedModel, routes: frozenset[str]) -> None:
        super().__init__(inner.rules, default_reply=inner.default_reply)
        self.routes = routes
        self.refused: list[str] = []

    def _check(self, route: str) -> None:
        if route not in self.routes:
            self.refused.append(route)
            raise ModelError("http_401", "key not allowed to access model", retryable=False)

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self._check(route)
        return await super().complete(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        )

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        self._check(route)
        chunks = super().stream(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        )
        async for chunk in chunks:
            yield chunk


@asynccontextmanager
async def _held_run_with_budget(lane: RemoteLane, max_tokens: int) -> AsyncIterator[HeldRun]:
    """`RemoteLane.held_run` with `budget.max_tokens` set: one `/v1/run` held at its tool call,
    after its first model call has settled.
    """
    held = HeldRun(new_trace_id())
    lane.tools.hold()
    body = {
        "trace_id": held.trace_id,
        "input": {"text": f"simplify: {HOLD}"},
        "budget": {"max_tokens": max_tokens},
    }
    async with lane.client() as client:
        task = asyncio.create_task(client.post("/v1/run", json=body))
        entered = asyncio.create_task(lane.tools.entered.wait())
        try:
            async with asyncio.timeout(10):
                await asyncio.wait({task, entered}, return_when=asyncio.FIRST_COMPLETED)
            if not entered.done():
                raise AssertionError(f"the run ended before its tool call: {task.result()}")
            yield held
        finally:
            entered.cancel()
            lane.tools.release.set()
            held.response = await task


async def test_h29_a_route_outside_the_key_is_refused_and_an_in_scope_call_is_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit criterion 1 (offline), H29, route scope: inside a run, with its own token, the
    remote calls the chassis's model proxy with a route the key does not list. The chassis
    forwards the route as is (no rewrite, no fallback), and the key's refusal reaches the remote
    as a non-retryable error with no completion. The control: the in-scope route in the same
    run is 200, and the run ends `ok`.

    Stand-in: the route scope is LiteLLM's virtual key (`models: [<route>]`, seed script), and
    the chassis has no route check of its own; `KeyScopedModel` refuses the way the LiteLLM
    adapter maps that 401. The real key is checked on kind.
    """
    scoped: list[KeyScopedModel] = []

    def model() -> KeyScopedModel:
        scoped.append(KeyScopedModel(scripted_model(), IN_SCOPE))
        return scoped[-1]

    monkeypatch.setattr(poc05_harness, "scripted_model", model)
    async with remote_lane(monkeypatch) as lane, lane.held_run() as run:
        out_of_scope = await _remote_chat(lane, run.traceparent, OUT_OF_SCOPE)
        in_scope = await _remote_chat(lane, run.traceparent)
    assert in_scope[0] == 200, in_scope
    assert in_scope[1]["choices"][0]["message"]["content"] == REPLY
    assert run.response is not None and run.response.json()["status"] == "ok", run.response
    assert out_of_scope[0] >= 400 and "choices" not in out_of_scope[1], out_of_scope
    error = out_of_scope[1]["error"]
    assert error["code"] == "http_401" and error["retryable"] is False, error
    assert scoped[0].refused == [OUT_OF_SCOPE], scoped[0].refused


RUN_BUDGET = 15
"""One scripted model call (10 input + 5 output tokens) spends it all."""


async def test_h29_past_the_run_budget_is_429_and_a_run_with_budget_left_is_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit criterion 1 (offline), H29, budget: a run with `budget.max_tokens` 15 spends it on
    its first model call; then a call from the remote, with its own token and the run's
    `traceparent`, is refused 429 `budget_exhausted`, not retryable, before the model is
    called. The control: in a run with the default budget, the same call is 200.
    """
    async with remote_lane(monkeypatch) as lane:
        async with _held_run_with_budget(lane, RUN_BUDGET) as spent:
            before = len(lane.model.calls)
            over = await _remote_chat(lane, spent.traceparent)
            after = len(lane.model.calls)
        async with lane.held_run() as run:
            allowed = await _remote_chat(lane, run.traceparent)
    assert allowed[0] == 200, allowed
    assert over[0] == 429 and _code(over[1]) == "budget_exhausted", over
    assert over[1]["error"]["retryable"] is False, over
    assert after == before, (before, after)


# --- H17: already covered end to end; named here so the map is complete ---


EXISTING = {
    "H17": (
        "test_poc05_remote_lane_offline.py",
        [
            "test_h17_remote_listener_needs_its_own_token_and_a_run_in_flight",
            "test_h17_workload_server_refuses_a_caller_without_the_token",
            "test_h17_a_rotation_accepts_the_previous_token_on_both_sides",
        ],
    ),
    "H13 (CLI proxy hosts)": (
        "test_poc05_remote_lane_offline.py",
        [
            "test_h13_cli_refuses_a_wildcard_or_loopback_remote_proxy_host",
            "test_h13_cli_refuses_a_proxy_host_off_loopback",
        ],
    ),
    "H13 (manifest --host)": (
        "test_poc05_hardening_static.py",
        ["test_chassis_public_port_binds_the_pod_ip"],
    ),
}
"""The pairs this file does not repeat: the H id, the file in this folder, and its tests.

H17 (no token 401, wrong token 401, own token outside a run 403 `run_required`, own token in a
run 200) runs end to end through the same harness in the first test named; unit cases are in
`packages/chassis/tests/test_remote_auth.py`. The uncorrelated cap's unit cases (H16) are in
`packages/chassis/tests/test_uncorrelated_cap.py`.
"""


def _test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    }


@pytest.mark.parametrize("hid", list(EXISTING))
def test_existing_coverage_is_still_there(hid: str) -> None:
    """Exit criterion 1 (offline): every H id this file leaves to another test still has that
    test, so the map in `EXISTING` cannot go stale silently.
    """
    name, tests = EXISTING[hid]
    found = _test_names(HERE / name)
    missing = [t for t in tests if t not in found]
    assert missing == [], f"{hid}: {name} no longer has {missing}"
