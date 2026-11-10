"""A broken response shape fails the matching interface contract case in both transports (015 H-8).

The chassis serves its own `echo_wire` in the `inprocess` lane (A2A in memory) and in the
`sidecar` lane (A2A over a Unix socket, the workload template server), through
`poc_harness.interfaces.chassis_on_unix_sockets`, as the PoC-3 binding does. The fault: the native
interface's complete answer drops `status` from the envelope. The suite's own case,
`InterfaceContract.test_every_cell_answers`, is called directly, so its failure is an
`AssertionError` this test can expect. The native stream cell still passes: only the complete
shape is broken, so the failure is the matching case, not every case.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar

import pytest
from chassis.core.envelope import Response
from chassis.core.inbound import Reply, ReplyMeta
from chassis.fakes import ScriptedModel
from chassis.server.interfaces.native import NativeInbound
from chassis_contracts.interface import LOGICAL, ChassisPool, InterfaceContract
from poc_harness.interfaces import LANES, OutboundRouter, chassis_on_unix_sockets

ECHO = "chassis.core.handle:echo_wire"


class _Suite(InterfaceContract):
    engine_labels: ClassVar[tuple[str, ...]] = (ECHO,)
    lane_labels: ClassVar[tuple[str, ...]] = LANES
    inprocess_engines: ClassVar[tuple[str, ...]] = (ECHO,)


@pytest.fixture(scope="module")
def pool() -> Iterator[ChassisPool]:
    router = OutboundRouter()
    chassis = ChassisPool(
        lambda target, lane: chassis_on_unix_sockets(
            target, lane, model=ScriptedModel(), router=router
        )
    )
    yield chassis
    chassis.close()
    assert router.unrouted == [], router.unrouted


@pytest.fixture
def no_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """The native complete answer without `status`, in every chassis while the test runs."""
    sound = NativeInbound.complete

    def complete(self: NativeInbound, response: Response, meta: ReplyMeta) -> Reply:
        reply = sound(self, response, meta)
        body = {k: v for k, v in reply.body.items() if k != "status"}
        return Reply(reply.status, reply.headers, body)

    monkeypatch.setattr(NativeInbound, "complete", complete)


def test_the_suite_covers_both_transports() -> None:
    assert LANES == ("inprocess", "sidecar")


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize("mode", ["complete", "stream"])
async def test_the_native_cell_passes_over_a_sound_chassis(
    pool: ChassisPool, lane: str, mode: str
) -> None:
    await _Suite().test_every_cell_answers(pool, LOGICAL, "native", ECHO, mode, lane)


@pytest.mark.parametrize("lane", LANES)
async def test_a_missing_status_fails_the_native_complete_cell(
    pool: ChassisPool, no_status: None, lane: str
) -> None:
    suite = _Suite()
    with pytest.raises(AssertionError, match="the envelope has no status"):
        await suite.test_every_cell_answers(pool, LOGICAL, "native", ECHO, "complete", lane)
    await suite.test_every_cell_answers(pool, LOGICAL, "native", ECHO, "stream", lane)
