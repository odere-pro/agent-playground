"""PoC-6a: the PoC-3 interface contract (`chassis_contracts.interface.InterfaceContract`: any
client, any engine, over native, OpenAI, Anthropic, MCP, and A2A) bound for the new sidecar engines
of the registry, in both lanes, offline. Today that is `echo-openai-agents`.

Part of exit criterion 4 (every supported engine passes the contract suites, in its lane) and of
the README's "rerun the PoC-3 contract suite on each new engine". It binds the suite the way
`pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py` does, with PoC-3's own
harness (`poc03_harness.chassis_on_unix_sockets`: a real chassis on Unix sockets, the workload
behind `inprocess` or `sidecar`, model calls replayed from a cassette), but over the engines of
`poc06_harness.ENGINES` that PoC-3 does not list, so PoC-3's engine list stays as it is. A new
Python sidecar engine in the registry is bound with no edit here, and records one cassette.

The cassette of an engine is `cassettes/interfaces/<engine>.yaml` here. Every interface, mode, and
lane must send the engine the same model call, so each cassette holds exactly one interaction.
Re-record offline: `make record-cassettes CASSETTE_TESTS=<this file>`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from chassis.adapters.litellm import LiteLLMModel
from chassis.fakes import ScriptedModel
from chassis.ports.model import ModelPort
from chassis_contracts.inbound import Logical
from chassis_contracts.interface import LOGICAL, Chassis, ExpectedAnswer, InterfaceContract
from chassis_contracts.recording import CassetteTransport
from fake_model_server import Script
from fake_model_server import create_app as create_fake_model_app
from poc03_harness import (
    AGENT,
    EXAMPLE_SCRIPT,
    LANES,
    MODEL_BASE_URL,
    SIMPLIFIED,
    SIMPLIFY,
    TEST_KEY,
    OutboundRouter,
    chassis_on_unix_sockets,
    patch_outbound,
)
from poc03_harness import ENGINES as POC03_ENGINES
from poc06a_harness import python_engines

CASSETTES = Path(__file__).resolve().parent / "cassettes" / "interfaces"
HANDLES = {name: engine.handle for name, engine in python_engines().items()}
NEW_ENGINES = tuple(
    name for name, handle in HANDLES.items() if handle not in POC03_ENGINES.values()
)
"""The registry's Python sidecar engines that PoC-3 does not bind."""


@pytest.fixture(scope="module")
def cassettes(record_mode: str) -> Iterator[dict[str, CassetteTransport]]:
    """One transport per new engine over `cassettes/interfaces/<engine>.yaml`, shared by every
    interface, mode, and lane. Replay has no inner transport; `rewrite` records through the fake
    model server over ASGI (no socket). At teardown: saved, no misses, and one interaction each.
    """
    transports: dict[str, CassetteTransport] = {}
    for engine in NEW_ENGINES:
        inner = None
        if record_mode != "none":
            app = create_fake_model_app(Script.from_yaml(EXAMPLE_SCRIPT))
            inner = httpx.ASGITransport(app=app)
        transports[engine] = CassetteTransport(inner, CASSETTES / f"{engine}.yaml", record_mode)
    yield transports
    for transport in transports.values():
        if transport.requests:
            transport.save()
    for engine, transport in transports.items():
        transport.assert_no_misses()
        if transport.requests:
            assert len(transport) == 1, (
                f"{engine}: {len(transport)} distinct model calls in {transport.path}; every "
                "interface, mode, and lane must send the same one"
            )


def _list_failures() -> int:
    """The runs of the new engines that found the MCP endpoint unreachable and ran without tools
    (only `echo_openai_agents` counts them so far)."""
    import importlib

    return sum(
        int(importlib.import_module(f"{handle.split(':')[0]}.tools").list_failures)
        for handle in (HANDLES[n] for n in NEW_ENGINES)
    )


class TestInterfacesNewEngines(InterfaceContract):
    """The interface contract suite over the registry's new sidecar engines and all four
    interfaces, streaming and complete, in the `inprocess` lane (A2A in memory) and the `sidecar`
    lane (A2A over a Unix socket), offline."""

    engine_labels: ClassVar[tuple[str, ...]] = NEW_ENGINES
    lane_labels: ClassVar[tuple[str, ...]] = LANES
    inprocess_engines: ClassVar[tuple[str, ...]] = NEW_ENGINES
    expected_answers: ClassVar[dict[str, ExpectedAnswer]] = {
        engine: ExpectedAnswer(SIMPLIFIED, 42, 9) for engine in NEW_ENGINES
    }
    """The fake model server's answer to `simplify: ...` (usage 42 in, 9 out), in every engine."""

    @pytest.fixture
    def logical(self) -> Logical:
        """The suite's logical request, with the text the simplifiers' script answers."""
        return Logical(
            SIMPLIFY, system=LOGICAL.system, history=LOGICAL.history, max_tokens=LOGICAL.max_tokens
        )

    @pytest.fixture(scope="class")
    @classmethod
    def chassis_for(
        cls, cassettes: dict[str, CassetteTransport]
    ) -> Iterator[Callable[[str, str], AbstractContextManager[Chassis]]]:
        """The workloads' outbound HTTP is routed by run for the whole class; each engine's
        chassis gets `LiteLLMModel` over its cassette, the suite's probe and error handles a
        scripted model nothing calls. At teardown: no request went unrouted, and no workload ran
        without its tools."""
        router = OutboundRouter()
        patch = pytest.MonkeyPatch()
        patch_outbound(patch, router.send)
        failures = _list_failures()

        def chassis_for(target: str, lane: str) -> AbstractContextManager[Chassis]:
            model: ModelPort = ScriptedModel()
            if target in cassettes:
                model = LiteLLMModel(
                    MODEL_BASE_URL, TEST_KEY, agent=AGENT, transport=cassettes[target]
                )
            return chassis_on_unix_sockets(
                HANDLES.get(target, target), lane, model=model, router=router
            )

        try:
            yield chassis_for
        finally:
            patch.undo()
        assert router.unrouted == [], f"outbound calls that named no run: {router.unrouted}"
        assert _list_failures() == failures, "a workload's MCP listing failed and was swallowed"


def test_cassettes_hold_no_key() -> None:
    """A cassette is a file in git: no key and no auth header reaches it."""
    for path in CASSETTES.glob("*.yaml"):
        text = path.read_text().lower()
        assert TEST_KEY not in text, path
        assert "authorization" not in text and "bearer" not in text, path
