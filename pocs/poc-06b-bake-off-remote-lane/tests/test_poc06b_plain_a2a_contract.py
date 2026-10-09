"""PoC-6b, exit criterion 2 offline: the `remote` lane in plain-A2A mode passes the engine
contract suite against an agent shaped like a third party.

`chassis_contracts.EngineConnectorContract`, the suite every lane binds, bound to
`RemoteConnector` with `spec.engine.protocol: a2a` (built through `EngineSpec`, the way
`chassis serve` builds it). The agent is `plain_a2a_stub`: an a2a-sdk server that streams the shape
the kagent-adk probe recorded and sends no `chassis.event`. It sits behind the template server's
own bearer check, with the token from the PoC-5 harness (`Tokens`, `TOKEN_ENV`), so a missing
bearer would fail every case. Unix sockets only; no key.

Two cases skip, by design: the JSON-values and `traceparent`-in-`ctx` cases need a workload
that echoes `ctx` back as a chassis `end`. A third-party agent receives no `ctx` and no
`tool_call` can appear, so those cases do not apply (contract v5 draft, B.10). The observable
subset below is what a plain agent can show: `start` first and once, deltas that join to the
answer, `metrics` with integer counts, `end ok`, a clean cancel, and the probe.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from chassis.adapters.a2a.remote import RemoteConnector
from chassis.core.collector import collect
from chassis.core.envelope import Request, Versions
from chassis.core.events import Delta, End, Metrics, Start
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server.config import EngineSpec
from chassis_contracts import EngineConnectorContract
from chassis_contracts.helpers import make_context
from plain_a2a_stub import ANSWER, USAGE_KEY, PlainStub, kagent_script, stub_app
from poc05_harness import REMOTE_URL, TOKEN_ENV, Tokens, app_on_socket
from workload_a2a.auth import BearerTokenMiddleware


def _plain_spec(uds: str) -> dict[str, object]:
    spec = EngineSpec.model_validate(
        {
            "connector": "remote",
            "protocol": "a2a",
            "url": REMOTE_URL,
            "auth": {"scheme": "bearer", "token_env": TOKEN_ENV},
            "uds": uds,
            "a2a": {"usage_key": USAGE_KEY},
        }
    )
    return spec.as_mapping()


async def _plain_remote(
    tokens: Tokens, stub: PlainStub | None = None
) -> AsyncIterator[RemoteConnector]:
    """The stub behind the bearer check, and a set-up `RemoteConnector` in plain mode."""
    app = BearerTokenMiddleware(stub_app(stub or PlainStub(kagent_script)), tokens.workload)
    async with app_on_socket(app, "plain.sock") as uds:
        connector = RemoteConnector()
        ports = PortBundle(
            model=ScriptedModel(),
            engine=connector,
            config=InMemoryConfig(),
            telemetry=InMemoryTelemetry(),
        )
        await connector.setup(_plain_spec(uds), ports)
        try:
            yield connector
        finally:
            await connector.close()


class TestPlainA2ARemoteLane(EngineConnectorContract):
    """Every `EngineConnectorContract` case that a plain agent can show, over the remote lane."""

    @pytest.fixture
    async def engine(self, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[RemoteConnector]:
        tokens = Tokens.one()
        tokens.apply(monkeypatch)
        async for connector in _plain_remote(tokens):
            yield connector

    async def test_the_answer_joins_from_the_deltas_and_ends_ok(
        self, engine: RemoteConnector, run_request: Request
    ) -> None:
        """The observable subset of the lane contract (B.10): start first, deltas that join to the
        answer, `metrics`, `end ok`, in order."""
        events = [e async for e in engine.run(run_request, make_context(run_request))]
        assert isinstance(events[0], Start) and events[0].request_id == run_request.request_id
        assert isinstance(events[-1], End) and events[-1].status == "ok"
        assert isinstance(events[-2], Metrics) and events[-2].input_tokens == 42
        assert "".join(e.text for e in events if isinstance(e, Delta)) == ANSWER
        assert [e.type for e in events].count("start") == 1

    async def test_the_response_has_the_answer(
        self, engine: RemoteConnector, run_request: Request
    ) -> None:
        response = await collect(
            engine.run(run_request, make_context(run_request)), run_request, Versions(chassis="t")
        )
        assert response.status == "ok" and response.output == {"text": ANSWER}


async def test_the_bearer_check_is_in_force(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control for the binding: a connector with another token cannot even read the card."""
    tokens = Tokens(chassis="a" * 8, workload="b" * 8, sends="a" * 8)
    tokens.apply(monkeypatch)
    with pytest.raises(RuntimeError, match="not reachable"):
        async for _ in _plain_remote(tokens):
            pytest.fail("setup must fail")
