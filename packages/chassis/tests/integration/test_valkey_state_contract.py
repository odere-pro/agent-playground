"""`StatePortContract` bound to `ValkeyState` against a real Valkey in a container (PoC-4 exit
criterion 1, the swap drill for `StatePort`). The same suite binds `InMemoryState` in
`tests/test_state_contract.py`. A `network` test: run with `make test-integration`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from chassis.adapters.valkey.state import PASSWORD_VAR, URL_VAR, ValkeyState
from chassis.ports.state import StatePort, StateUnavailable
from chassis.profiles import AdapterSpec, build_ports
from chassis_contracts.containers.valkey import ValkeyServer, valkey_container
from chassis_contracts.state import StatePortContract


@pytest.fixture(scope="module")
def valkey_server() -> Iterator[ValkeyServer]:
    with valkey_container() as server:
        yield server


class TestValkeyState(StatePortContract):
    @pytest.fixture
    async def state_port(self, valkey_server: ValkeyServer) -> AsyncIterator[StatePort]:
        state = ValkeyState.from_url(valkey_server.url, password=valkey_server.password)
        yield state
        await state.aclose()

    @pytest.fixture
    async def broken_state_port(self) -> AsyncIterator[StatePort]:
        # Port 1 on loopback: nothing listens, so every call fails to connect.
        state = ValkeyState.from_url("valkey://127.0.0.1:1/0")
        yield state
        await state.aclose()


async def test_a_wrong_password_is_state_unavailable_and_not_echoed(
    valkey_server: ValkeyServer,
) -> None:
    state = ValkeyState.from_url(valkey_server.url, password="wrong-password-for-test")
    with pytest.raises(StateUnavailable) as caught:
        await state.get("any")
    assert "wrong-password-for-test" not in str(caught.value)
    assert valkey_server.password not in str(caught.value)
    await state.aclose()


async def test_spec_adapters_state_valkey_builds_a_working_port(
    valkey_server: ValkeyServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(URL_VAR, valkey_server.url)
    monkeypatch.setenv(PASSWORD_VAR, valkey_server.password)
    ports = build_ports("fake", AdapterSpec(state="valkey"))
    assert isinstance(ports.state, ValkeyState)
    assert await ports.state.set_if_absent("swap-drill", b"ok", ttl_s=5.0)
    assert await ports.state.get("swap-drill") == b"ok"
    await ports.state.aclose()
