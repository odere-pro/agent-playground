"""PoC-3 interface contract: `chassis_contracts.interface.InterfaceContract` bound over the four
engines and both lanes, offline.

Exit criteria (docs/planning/poc/003-PoC-3-one-interface-every-client.md):

- 1, "The interface contract suite and the Schemathesis tests run offline in CI on every commit"
  (the interface half): this binding runs under `make test` with sockets off except Unix
  sockets, no keys, and model calls replayed from cassettes.
- 2, "The contract suite passes for all engines and all interfaces, over both transports": the
  matrix interface {native, openai, anthropic, mcp} x engine {echo_python, echo_pydanticai,
  echo_langgraph, echo-typescript} x mode {stream, complete} x lane {inprocess, sidecar}.

Named `test_interface_contract.py`, not `test_interfaces.py`: `packages/chassis/tests/
test_interfaces.py` exists, and pytest's default import mode refuses two test modules with one
basename ("import file mismatch").

How each engine runs: see `poc03_harness`. The model calls replay from one cassette per engine,
`cassettes/interfaces/<engine>.yaml`, on the `LiteLLMModel` transport only. Matching is on the
body, and every interface, mode, and lane must send the same model call for the same logical
request: each cassette must hold exactly one interaction, and a miss fails at teardown.
Re-record offline with `make record-cassettes`. The TypeScript echo skips (with the reason) when
its `node_modules` is absent, as in PoC-2.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
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
    CASSETTES,
    ENGINES,
    EXAMPLE_SCRIPT,
    LANES,
    MODEL_BASE_URL,
    PYTHON_ENGINES,
    SIMPLIFIED,
    SIMPLIFY,
    TEST_KEY,
    OutboundRouter,
    chassis_on_unix_sockets,
    patch_outbound,
    tool_list_failures,
)


@pytest.fixture(scope="module")
def cassettes(record_mode: str) -> Iterator[dict[str, CassetteTransport]]:
    """One transport per engine over `cassettes/interfaces/<engine>.yaml`, shared by every
    interface, mode, and lane. Replay has no inner transport; `rewrite` records through the fake
    model server over ASGI (no socket). At teardown: saved, no misses, and one interaction each
    (every cell sent the same model call).
    """
    transports: dict[str, CassetteTransport] = {}
    for engine in ENGINES:
        path = CASSETTES / f"{engine}.yaml"
        inner = None
        if record_mode != "none":
            app = create_fake_model_app(Script.from_yaml(EXAMPLE_SCRIPT))
            inner = httpx.ASGITransport(app=app)
        transports[engine] = CassetteTransport(inner, path, record_mode)
    yield transports
    for transport in transports.values():
        if transport.requests:  # an engine that did not run (no Node) keeps its file as it is
            transport.save()
    for engine, transport in transports.items():
        transport.assert_no_misses()
        if transport.requests:
            assert len(transport) == 1, (
                f"{engine}: {len(transport)} distinct model calls in {transport.path}; every "
                "interface, mode, and lane must send the same one"
            )


class TestInterfaces(InterfaceContract):
    """Exit criteria 1 and 2: the interface contract suite over all four engines and all four
    interfaces, streaming and complete, in the `inprocess` lane (A2A in memory) and the `sidecar`
    lane (A2A over a Unix socket), offline.
    """

    engine_labels: ClassVar[tuple[str, ...]] = tuple(ENGINES)
    lane_labels: ClassVar[tuple[str, ...]] = LANES
    inprocess_engines: ClassVar[tuple[str, ...]] = PYTHON_ENGINES
    expected_answers: ClassVar[dict[str, ExpectedAnswer]] = {
        engine: ExpectedAnswer(SIMPLIFIED, 42, 9) for engine in ENGINES
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
        without its tools.
        """
        router = OutboundRouter()
        patch = pytest.MonkeyPatch()
        patch_outbound(patch, router.send)
        failures = tool_list_failures()

        def chassis_for(target: str, lane: str) -> AbstractContextManager[Chassis]:
            model: ModelPort = ScriptedModel()
            if target in cassettes:
                model = LiteLLMModel(
                    MODEL_BASE_URL, TEST_KEY, agent=AGENT, transport=cassettes[target]
                )
            return chassis_on_unix_sockets(target, lane, model=model, router=router)

        try:
            yield chassis_for
        finally:
            patch.undo()
        assert router.unrouted == [], f"outbound calls that named no run: {router.unrouted}"
        assert tool_list_failures() == failures, "a workload's MCP listing failed and was swallowed"
