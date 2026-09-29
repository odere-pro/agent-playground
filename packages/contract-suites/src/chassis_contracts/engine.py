"""EngineConnectorContract: the stream starts with `start`, ends with `end` or `error`, and cancels
cleanly.
"""

from __future__ import annotations

import pytest
from chassis.core.envelope import Context, Request
from chassis.core.events import End, Error, Start, parse_event
from chassis.ports.engine import LANES, EngineConnector

from chassis_contracts.helpers import make_context, make_request


@pytest.mark.contract
class EngineConnectorContract:
    """Subclass as `Test*`, provide `engine` (already set up). Override `run_request` if the echo
    text is not enough.
    """

    @pytest.fixture
    def engine(self) -> EngineConnector:
        raise NotImplementedError("provide an engine fixture")

    @pytest.fixture
    def run_request(self) -> Request:
        return make_request()

    @pytest.fixture
    def context(self, run_request: Request) -> Context:
        return make_context(run_request)

    def test_declares_lane_and_capabilities(self, engine: EngineConnector) -> None:
        assert engine.kind in LANES
        assert "streaming" in engine.capabilities

    async def test_stream_starts_and_ends(
        self, engine: EngineConnector, run_request: Request, context: Context
    ) -> None:
        events = [e async for e in engine.run(run_request, context)]
        assert events, "no events"
        assert isinstance(events[0], Start) and events[0].request_id == run_request.request_id
        assert isinstance(events[-1], End | Error)
        assert not any(isinstance(e, Start) for e in events[1:]), "start must come once"

    async def test_events_survive_the_wire(
        self, engine: EngineConnector, run_request: Request, context: Context
    ) -> None:
        async for event in engine.run(run_request, context):
            wire = event.model_dump(mode="json")
            assert parse_event(wire) == event

    async def test_cancel_is_clean(
        self, engine: EngineConnector, run_request: Request, context: Context
    ) -> None:
        stream = engine.run(run_request, context)
        first = await anext(stream)
        assert isinstance(first, Start)
        closer = getattr(stream, "aclose", None)
        if closer is not None:
            await closer()
        await engine.close()
