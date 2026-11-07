"""PoC-6b, the remote lane: the chassis remote connector through the front door, offline.

Exit criteria 2 and 4. The real `RemoteConnector` (plain A2A, as kagent-adk) talks to the real
front door app (`code_runner.a2a_dispatch`, one port per engine) over `httpx.ASGITransport`. The
door claims from a fake Kubernetes API (`httpx.MockTransport`) and streams to a fake pod: the
plain-A2A stub behind the workload's bearer check, also over `httpx.ASGITransport`. No socket.

The connector's contract is "never an exception from run" (`chassis.adapters.a2a.connector`). So:
a normal run streams through and ends ok; a claim failure is a retryable `a2a.transport` Error
event; a pod 401 (the door passes it on as HTTP 401, as the pod did before the door) is an
`a2a.transport` Error event. In each case no claim is left.

Run: `make test-poc POC=06b`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import yaml
from chassis.adapters.a2a.remote import RemoteConnector
from chassis.core.events import Delta, End, Error, Event
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.server.config import EngineSpec
from chassis_contracts.helpers import make_context, make_request
from code_runner.a2a_dispatch import (
    Engine,
    FrontDoorConfig,
    RunDispatcher,
    create_front_doors,
)
from code_runner.dispatch import API_GROUP_PATH, api_client, sandbox_client
from poc_harness.lane_tokens import TOKEN_ENV, Tokens
from poc_harness.plain_a2a import ANSWER, USAGE_KEY, PlainStub, kagent_script, stub_app
from workload_a2a.auth import BearerTokenMiddleware

ROOT = Path(__file__).resolve().parents[3]
FRONT_DOOR_FILE = ROOT / "deploy/kind/poc06/platform/remote-run-dispatch.yaml"
NS = "poc05-remote"
ENGINE = "kagent-adk"
PORT = 9004  # the door's kagent-adk port (deploy/kind/poc06/platform/remote-run-dispatch.yaml)
HOST_URL = "http://remote-run-dispatch.poc05-platform.svc.cluster.local"
URL = f"{HOST_URL}:{PORT}/{ENGINE}"
CLAIMS = f"{API_GROUP_PATH}/namespaces/{NS}/sandboxclaims"
API_TOKEN = "api-token-for-the-fake-api"  # pragma: allowlist secret (a test value)
WRONG = "not-the-workload-token-at-all-0123456789"  # pragma: allowlist secret (a test value)


def _pinned_card() -> dict[str, Any]:
    """The kagent-adk card the door serves, as pinned in the manifest's ConfigMap."""
    docs = [d for d in yaml.safe_load_all(FRONT_DOOR_FILE.read_text()) if d]
    (cards,) = [d for d in docs if d["kind"] == "ConfigMap"]
    return dict(json.loads(cards["data"][f"{ENGINE}.json"]))


class FakeApi:
    """SandboxClaims in memory; `fail_create` answers every create with a 500."""

    def __init__(self, *, fail_create: bool = False) -> None:
        self.fail_create = fail_create
        self.claims: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []

    def live(self) -> set[str]:
        return set(self.claims) - set(self.deleted)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith(CLAIMS)
        if request.method == "POST":
            if self.fail_create:
                return httpx.Response(500, json={"kind": "Status"})
            claim = json.loads(request.content)
            self.claims[claim["metadata"]["name"]] = claim
            return httpx.Response(201, json=claim)
        name = request.url.path.rsplit("/", 1)[-1]
        if request.method == "DELETE":
            self.deleted.append(name)
            return httpx.Response(200, json={"kind": "Status"})
        claim = dict(self.claims[name])
        claim["status"] = {
            "conditions": [{"type": "Ready", "status": "True"}],
            "sandbox": {"name": name, "podIPs": ["10.244.0.20"]},
        }
        return httpx.Response(200, json=claim)


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "token"
    path.write_text(API_TOKEN)
    return path


def _spec() -> dict[str, object]:
    spec = EngineSpec.model_validate(
        {
            "connector": "remote",
            "protocol": "a2a",
            "url": URL,
            "auth": {"scheme": "bearer", "token_env": TOKEN_ENV},
            "a2a": {"usage_key": USAGE_KEY},
        }
    )
    return spec.as_mapping()


async def _connector(
    monkeypatch: pytest.MonkeyPatch, api: FakeApi, token_file: Path, *, pod_token: str | None = None
) -> AsyncIterator[RemoteConnector]:
    """The door on its kagent-adk port, its pod behind `pod_token` (default: the token the
    connector sends), and a set-up connector whose clients reach the door over ASGI."""
    tokens = Tokens.one()  # a fresh random token per call: made once here
    tokens.apply(monkeypatch)
    pod = BearerTokenMiddleware(stub_app(PlainStub(kagent_script)), pod_token or tokens.workload)
    claims = api_client(
        "https://10.96.0.1:443", token_file, verify=True, transport=httpx.MockTransport(api)
    )
    config = FrontDoorConfig(
        namespace=NS,
        public_url=HOST_URL,
        claim_timeout_s=2,
        poll_s=0.01,
        connect_retry_s=0.1,
        engine_ports={ENGINE: PORT},
    )
    engines = {ENGINE: Engine(ENGINE, f"remote-{ENGINE}", _pinned_card())}
    door = RunDispatcher(
        claims, sandbox_client(transport=httpx.ASGITransport(app=cast(Any, pod))), config, engines
    )
    app = create_front_doors(door)[PORT]

    def client_for(
        self: RemoteConnector, token: str, uds: str | None, timeout: float | None
    ) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=self.url or "",
            headers={"Authorization": f"Bearer {token}"},
            trust_env=False,
            follow_redirects=False,
        )
        if timeout is not None:
            client.timeout = httpx.Timeout(timeout)
        return client

    monkeypatch.setattr(RemoteConnector, "_client_for", client_for)
    connector = RemoteConnector()
    ports = PortBundle(
        model=ScriptedModel(),
        engine=connector,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
    )
    await connector.setup(_spec(), ports)
    try:
        yield connector
    finally:
        await connector.close()


async def _run(connector: RemoteConnector) -> list[Event]:
    """Every event of one run; an exception here fails the test (the contract forbids one)."""
    request = make_request()
    return [e async for e in connector.run(request, make_context(request))]


async def test_a_run_streams_through_the_front_door_and_frees_its_claim(
    monkeypatch: pytest.MonkeyPatch, token_file: Path
) -> None:
    api = FakeApi()
    async for connector in _connector(monkeypatch, api, token_file):
        events = await _run(connector)
    assert isinstance(events[-1], End) and events[-1].status == "ok", events
    assert "".join(e.text for e in events if isinstance(e, Delta)) == ANSWER
    assert len(api.claims) == 1 and api.live() == set()


async def test_a_claim_failure_is_a_retryable_transport_error_event(
    monkeypatch: pytest.MonkeyPatch, token_file: Path
) -> None:
    api = FakeApi(fail_create=True)
    async for connector in _connector(monkeypatch, api, token_file):
        events = await _run(connector)
    errors = [e for e in events if isinstance(e, Error)]
    assert [(e.code, e.retryable) for e in errors] == [("a2a.transport", True)], events
    assert api.live() == set()


async def test_a_pod_401_is_a_transport_error_event_never_an_exception(
    monkeypatch: pytest.MonkeyPatch, token_file: Path
) -> None:
    """The pod wants another token: it answers 401, the door passes 401 on (fixed body), and the
    connector reports `a2a.transport`. The paired control is the first test: the right token
    runs."""
    api = FakeApi()
    async for connector in _connector(monkeypatch, api, token_file, pod_token=WRONG):
        events = await _run(connector)
    errors = [e for e in events if isinstance(e, Error)]
    assert [e.code for e in errors] == ["a2a.transport"], events
    assert len(api.claims) == 1 and api.live() == set()
