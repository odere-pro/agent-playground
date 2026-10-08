"""PoC-4 exit criterion 2 over a real Valkey: "A repeated call with the same `Idempotency-Key`
returns the same result on any replica."

Two replicas, each with its own `ValkeyState` client to one Valkey in a container
(`chassis_contracts.containers.valkey`), as two pods share one Valkey. The offline twin, with one
`InMemoryState` standing for Valkey, is `test_idempotency_replicas.py`.

A `network` test: Docker needed; run with `make test-integration` or
`uv run pytest -m network --record-mode=none <this file>`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from chassis.adapters.valkey.state import ValkeyState
from chassis_contracts.containers.valkey import ValkeyServer, valkey_container
from poc03_harness import OutboundRouter, patch_outbound
from poc04_harness import SIMPLIFY, SLOW, HarnessModel, Replicas, call, replicas_on_unix_sockets

pytestmark = pytest.mark.network


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
            model=HarnessModel(),
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
    a, b = await asyncio.gather(
        call(pair[0], "native", SLOW, key=key), call(pair[1], "native", SLOW, key=key)
    )
    assert a.status == b.status == 200 and a.body == b.body, (a, b)
    assert sorted([a.replayed, b.replayed]) == [False, True]
    assert pair.engine_runs() == 1
    assert len(pair.model.calls) == 1
