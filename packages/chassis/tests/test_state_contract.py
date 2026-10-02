"""`StatePortContract` bound to `InMemoryState`, plus the offline checks of `ValkeyState` that
need no server. The same suite runs against a real Valkey in
`tests/integration/test_valkey_state_contract.py`.
"""

from __future__ import annotations

import pytest
from chassis.adapters.valkey.state import PASSWORD_VAR, URL_VAR, USERNAME_VAR, ValkeyState, ttl_ms
from chassis.fakes.state import InMemoryState, StateUnavailable
from chassis.ports.state import StatePort
from chassis_contracts.state import StatePortContract


class AlwaysFailingState(InMemoryState):
    """A store that fails on every call, for the failure case."""

    def _enter(self, method: str, key: str) -> None:
        self.fail_next()
        super()._enter(method, key)


class TestInMemoryState(StatePortContract):
    @pytest.fixture
    def state_port(self) -> StatePort:
        return InMemoryState()

    @pytest.fixture
    def broken_state_port(self) -> StatePort:
        return AlwaysFailingState()


async def test_fail_next_fails_one_call_only() -> None:
    state = InMemoryState()
    state.fail_next()
    with pytest.raises(StateUnavailable):
        await state.get("k")
    assert await state.get("k") is None
    assert state.calls == [("get", "k"), ("get", "k")]


def test_valkey_from_env_needs_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(URL_VAR, raising=False)
    with pytest.raises(LookupError, match=URL_VAR):
        ValkeyState.from_env()


async def test_valkey_from_env_builds_without_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(URL_VAR, "valkey://valkey:6379/0")
    monkeypatch.setenv(USERNAME_VAR, "chassis")
    monkeypatch.setenv(PASSWORD_VAR, "not-a-real-secret")
    state = ValkeyState.from_env()
    assert "not-a-real-secret" not in repr(state)
    await state.aclose()


def test_valkey_refuses_a_password_in_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(URL_VAR, "valkey://user:hunter2@valkey:6379/0")
    with pytest.raises(ValueError, match=PASSWORD_VAR) as caught:
        ValkeyState.from_env()
    assert "hunter2" not in str(caught.value)


@pytest.mark.parametrize(
    ("ttl_s", "expected"), [(None, None), (0.0001, 1), (0.2, 200), (1.0001, 1001), (30, 30000)]
)
def test_ttl_ms_rounds_up_to_at_least_one(ttl_s: float | None, expected: int | None) -> None:
    assert ttl_ms(ttl_s) == expected


@pytest.mark.parametrize("ttl_s", [0, -1.0])
def test_ttl_ms_refuses_a_ttl_of_zero_or_less(ttl_s: float) -> None:
    with pytest.raises(ValueError, match="ttl_s"):
        ttl_ms(ttl_s)
