"""PoC-4 exit criterion 2 over a real Valkey, in the README's words:

"A repeated call with the same key returns the same result on any replica."

This file covers "any replica" when the replicas share the result only through Valkey. "The same
key" is the `Idempotency-Key` header the client sends.

Two replicas, each with its own `ValkeyState` client to one Valkey in a container
(`chassis_contracts.containers.valkey`), as two pods share one Valkey. The offline twin, with one
`InMemoryState` standing for Valkey, is `test_idempotency_replicas.py`.

A `network` test: Docker needed; run with `make test-integration` or
`uv run pytest -m network --record-mode=none <this file>`.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Iterator, Sequence

import pytest
from chassis.adapters.valkey.state import ValkeyState
from chassis.ports.model import ModelMessage
from chassis_contracts.containers.valkey import ValkeyServer, valkey_container
from poc_harness.interfaces import OutboundRouter
from poc_harness.outbound import patch_outbound
from poc_harness.replica_calls import call
from poc_harness.replica_doubles import HarnessModel
from poc_harness.replicas import Replicas, replicas_on_unix_sockets
from poc_harness.scenario import SIMPLIFY
from poc_harness.waits import wait_until_async

pytestmark = pytest.mark.network

GATED = "simplify: GATED"
"""suggested: a prompt `GatedModel` holds in the model until the test lets it go."""
HANG_GUARD_S = 10.0
"""Only a hang reaches this."""


class GatedModel(HarnessModel):
    """`HarnessModel`, holding a `GATED` call until `release`. It sets `entered` then. Runs on the
    replicas' loop, in another thread, so its signals are `threading.Event`s.
    """

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    async def _wait(self, messages: Sequence[ModelMessage]) -> None:
        if any(GATED in (m.content or "") for m in messages if m.role == "user"):
            self.entered.set()
            await asyncio.to_thread(self.release.wait, HANG_GUARD_S)
        await super()._wait(messages)


@pytest.fixture(scope="module")
def valkey_server(request: pytest.FixtureRequest) -> Iterator[ValkeyServer]:
    if request.config.getoption("--disable-socket", default=False):
        pytest.skip("sockets are disabled; run `make test-integration`")
    with valkey_container() as server:
        yield server


@pytest.fixture
def pair(valkey_server: ValkeyServer) -> Iterator[Replicas]:
    router = OutboundRouter()
    patch = pytest.MonkeyPatch()
    patch_outbound(patch, router.send)
    clients = [
        ValkeyState.from_url(valkey_server.url, password=valkey_server.password) for _ in range(2)
    ]
    try:
        with replicas_on_unix_sockets(
            2,
            "echo_python",
            "inprocess",
            model=GatedModel(),
            router=router,
            state=clients,
            adapters={"state": "valkey"},
            idempotency={"lease_s": 2, "wait_poll_ms": 20},
        ) as replicas:
            yield replicas
    finally:
        patch.undo()


async def test_two_replicas_share_one_result_through_valkey(pair: Replicas) -> None:
    """Exit criterion 2: the repeat on replica 1 is replica 0's stored answer, read from Valkey,
    with `Idempotent-Replayed: true`; the model is called once."""
    key = f"valkey-{uuid.uuid4().hex}"
    first = await call(pair[0], "native", SIMPLIFY, key=key)
    again = await call(pair[1], "native", SIMPLIFY, key=key, stream=True)
    repeat = await call(pair[1], "native", SIMPLIFY, key=key)
    assert first.status == again.status == repeat.status == 200, (first, again, repeat)
    assert again.replayed and repeat.replayed and not first.replayed
    assert repeat.body == first.body
    assert again.body[-1] == ("response", first.body)
    assert len(pair.model.calls) == 1


async def test_concurrent_duplicates_run_once_through_valkey(pair: Replicas) -> None:
    """Exit criterion 2 (in flight): one key on both replicas at once runs the engine once; the
    claim in Valkey makes the duplicate wait and replay."""
    key = f"valkey-{uuid.uuid4().hex}"
    model = pair.model
    assert isinstance(model, GatedModel)
    first = asyncio.create_task(call(pair[0], "native", GATED, key=key))
    entered = await asyncio.to_thread(model.entered.wait, HANG_GUARD_S)
    assert entered, "the first call never reached the model"

    duplicate = asyncio.create_task(call(pair[1], "native", GATED, key=key))
    try:
        await wait_until_async(
            lambda: pair.counter("chassis.idempotency.waited") == 1,
            "the duplicate to wait on the first call",
            HANG_GUARD_S,
        )
        answered_early = duplicate.done()
    finally:
        model.release.set()
    a, b = await asyncio.gather(first, duplicate)

    assert not answered_early, "the duplicate answered while the first call held the key"
    assert a.status == b.status == 200 and a.body == b.body, (a, b)
    assert (a.replayed, b.replayed) == (False, True)
    assert pair.engine_runs() == 1
    assert len(pair.model.calls) == 1
