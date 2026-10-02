"""StatePortContract: bytes by key, TTL, one winner for `set_if_absent`, an atomic
`compare_and_set`, and every store failure as `StateUnavailable` (PoC-4, plan section 1).

Not exported from `chassis_contracts/__init__.py` yet; import it as `chassis_contracts.state`.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from chassis.ports.state import StatePort, StateUnavailable

TTL_SHORT_S = 0.2
"""suggested: a TTL short enough for a test, long enough for one round trip to a container."""
WAIT_PAST_TTL_S = 0.6
"""suggested: how long a test sleeps to be sure a `TTL_SHORT_S` entry is gone."""
TTL_LONG_S = 30.0
CONCURRENT_CLAIMS = 20


@pytest.mark.contract
class StatePortContract:
    """Subclass as `Test*`. Provide `state_port`. Optional: `broken_state_port` (a port whose
    store fails on every call) and `key_prefix` (default: a fresh `uuid4().hex` per test, so runs
    against a shared store never collide)."""

    @pytest.fixture
    def state_port(self) -> StatePort:
        raise NotImplementedError("provide a state_port fixture")

    @pytest.fixture
    def key_prefix(self) -> str:
        return uuid.uuid4().hex

    @pytest.fixture
    def broken_state_port(self) -> StatePort:
        pytest.skip("override broken_state_port with a port whose store fails")

    async def test_get_of_a_missing_key_is_none(
        self, state_port: StatePort, key_prefix: str
    ) -> None:
        assert await state_port.get(f"{key_prefix}:missing") is None

    async def test_set_then_get_round_trips_any_bytes(
        self, state_port: StatePort, key_prefix: str
    ) -> None:
        for i, value in enumerate([b"", b"plain", b"\x00\xff", bytes(range(256)), "é€".encode()]):
            key = f"{key_prefix}:v{i}"
            await state_port.set(key, value)
            assert await state_port.get(key) == value

    async def test_set_overwrites(self, state_port: StatePort, key_prefix: str) -> None:
        key = f"{key_prefix}:k"
        await state_port.set(key, b"one")
        await state_port.set(key, b"two")
        assert await state_port.get(key) == b"two"

    async def test_set_if_absent_claims_once_and_never_overwrites(
        self, state_port: StatePort, key_prefix: str
    ) -> None:
        key = f"{key_prefix}:claim"
        assert await state_port.set_if_absent(key, b"first", ttl_s=TTL_LONG_S) is True
        assert await state_port.set_if_absent(key, b"second", ttl_s=TTL_LONG_S) is False
        assert await state_port.get(key) == b"first"
        other = f"{key_prefix}:set"
        await state_port.set(other, b"there")
        assert await state_port.set_if_absent(other, b"new", ttl_s=TTL_LONG_S) is False
        assert await state_port.get(other) == b"there"

    async def test_concurrent_set_if_absent_has_one_winner(
        self, state_port: StatePort, key_prefix: str
    ) -> None:
        key = f"{key_prefix}:race"
        results = await asyncio.gather(
            *(
                state_port.set_if_absent(key, f"c{i}".encode(), ttl_s=TTL_LONG_S)
                for i in range(CONCURRENT_CLAIMS)
            )
        )
        assert results.count(True) == 1
        winner = results.index(True)
        assert await state_port.get(key) == f"c{winner}".encode()

    async def test_a_value_expires_after_its_ttl(
        self, state_port: StatePort, key_prefix: str
    ) -> None:
        key = f"{key_prefix}:ttl"
        await state_port.set(key, b"soon gone", ttl_s=TTL_SHORT_S)
        assert await state_port.get(key) == b"soon gone"
        await asyncio.sleep(WAIT_PAST_TTL_S)
        assert await state_port.get(key) is None

    async def test_an_expired_claim_can_be_claimed_again(
        self, state_port: StatePort, key_prefix: str
    ) -> None:
        key = f"{key_prefix}:lease"
        assert await state_port.set_if_absent(key, b"a", ttl_s=TTL_SHORT_S) is True
        await asyncio.sleep(WAIT_PAST_TTL_S)
        assert await state_port.set_if_absent(key, b"b", ttl_s=TTL_LONG_S) is True
        assert await state_port.get(key) == b"b"

    async def test_compare_and_set_replaces_only_on_a_match(
        self, state_port: StatePort, key_prefix: str
    ) -> None:
        key = f"{key_prefix}:cas"
        await state_port.set(key, b"old")
        assert await state_port.compare_and_set(key, b"other", b"new") is False
        assert await state_port.get(key) == b"old"
        assert await state_port.compare_and_set(key, b"old\x00", b"new") is False
        assert await state_port.compare_and_set(key, b"old", b"new") is True
        assert await state_port.get(key) == b"new"

    async def test_compare_and_set_on_a_missing_key_is_false(
        self, state_port: StatePort, key_prefix: str
    ) -> None:
        key = f"{key_prefix}:nothing"
        assert await state_port.compare_and_set(key, b"", b"new") is False
        assert await state_port.compare_and_set(key, b"x", None) is False
        assert await state_port.get(key) is None

    async def test_compare_and_set_with_none_deletes_on_a_match(
        self, state_port: StatePort, key_prefix: str
    ) -> None:
        key = f"{key_prefix}:del"
        await state_port.set(key, b"mine")
        assert await state_port.compare_and_set(key, b"theirs", None) is False
        assert await state_port.get(key) == b"mine"
        assert await state_port.compare_and_set(key, b"mine", None) is True
        assert await state_port.get(key) is None
        assert await state_port.set_if_absent(key, b"again", ttl_s=TTL_LONG_S) is True

    async def test_compare_and_set_renews_the_ttl(
        self, state_port: StatePort, key_prefix: str
    ) -> None:
        renewed = f"{key_prefix}:renewed"
        await state_port.set(renewed, b"lease", ttl_s=TTL_SHORT_S)
        assert await state_port.compare_and_set(renewed, b"lease", b"lease2", ttl_s=TTL_LONG_S)
        shortened = f"{key_prefix}:shortened"
        await state_port.set(shortened, b"forever")
        assert await state_port.compare_and_set(shortened, b"forever", b"brief", ttl_s=TTL_SHORT_S)
        cleared = f"{key_prefix}:cleared"
        await state_port.set(cleared, b"brief", ttl_s=TTL_SHORT_S)
        assert await state_port.compare_and_set(cleared, b"brief", b"kept")
        await asyncio.sleep(WAIT_PAST_TTL_S)
        assert await state_port.get(renewed) == b"lease2"
        assert await state_port.get(shortened) is None
        assert await state_port.get(cleared) == b"kept"

    async def test_delete_of_a_missing_key_is_fine(
        self, state_port: StatePort, key_prefix: str
    ) -> None:
        key = f"{key_prefix}:gone"
        await state_port.delete(key)
        await state_port.set(key, b"x")
        await state_port.delete(key)
        await state_port.delete(key)
        assert await state_port.get(key) is None

    async def test_a_store_failure_is_state_unavailable(
        self, broken_state_port: StatePort, key_prefix: str
    ) -> None:
        key = f"{key_prefix}:broken"
        with pytest.raises(StateUnavailable):
            await broken_state_port.get(key)
        with pytest.raises(StateUnavailable):
            await broken_state_port.set(key, b"x", ttl_s=TTL_LONG_S)
        with pytest.raises(StateUnavailable):
            await broken_state_port.set_if_absent(key, b"x", ttl_s=TTL_LONG_S)
        with pytest.raises(StateUnavailable):
            await broken_state_port.compare_and_set(key, b"x", b"y")
        with pytest.raises(StateUnavailable):
            await broken_state_port.delete(key)
