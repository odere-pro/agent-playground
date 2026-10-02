"""ConfigPortContract: load gives a version, a missing name raises, a change gives a new version
and notifies (at once or eventually, within `notify_timeout_s`), an unsubscribed callback is not
called, and a store failure is `ConfigUnavailable`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from chassis.ports.config import ConfigNotFound, ConfigPort, ConfigUnavailable, LoadedConfig

Bump = Callable[[], Awaitable[None]]


async def wait_for_version(
    seen: list[LoadedConfig], version: str, timeout_s: float, step_s: float = 0.02
) -> None:
    """Wait until a callback has seen `version`, or `timeout_s` passes."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        if any(item.version == version for item in seen) or loop.time() >= deadline:
            return
        await asyncio.sleep(step_s)


@pytest.mark.contract
class ConfigPortContract:
    """Subclass as `Test*`. Provide `config_port`, `config_name` (a name that exists in the
    store), and `bump_config` (changes that config through the real store). A polling adapter
    sets `notify_timeout_s` above its poll interval. Provide `unavailable_config_port` (the
    same adapter pointed at a store that fails) to run the error case.

    A subscriber is notified of changes after the version it last loaded, so each case loads
    before it subscribes.
    """

    @pytest.fixture
    def config_port(self) -> ConfigPort:
        raise NotImplementedError("provide a config_port fixture")

    @pytest.fixture
    def config_name(self) -> str:
        return "echo"

    @pytest.fixture
    def bump_config(self, config_port: ConfigPort, config_name: str) -> Bump:
        pytest.skip("override bump_config to change the config through the store")

    @pytest.fixture
    def notify_timeout_s(self) -> float:
        """suggested: 2 s. How long a change may take to reach a subscriber."""
        return 2.0

    @pytest.fixture
    def unavailable_config_port(self) -> ConfigPort:
        pytest.skip("override unavailable_config_port to run the store failure case")

    async def test_load_returns_version_and_data(
        self, config_port: ConfigPort, config_name: str
    ) -> None:
        loaded = await config_port.load(config_name)
        assert loaded.name == config_name
        assert loaded.version
        assert isinstance(loaded.data, dict)

    async def test_missing_config_raises(self, config_port: ConfigPort) -> None:
        with pytest.raises(ConfigNotFound):
            await config_port.load("no-such-config")

    async def test_change_gives_new_version_and_notifies(
        self,
        config_port: ConfigPort,
        config_name: str,
        bump_config: Bump,
        notify_timeout_s: float,
    ) -> None:
        seen: list[LoadedConfig] = []

        async def on_change(loaded: LoadedConfig) -> None:
            seen.append(loaded)

        before = await config_port.load(config_name)
        unsubscribe = config_port.subscribe(config_name, on_change)
        try:
            await bump_config()
            after = await config_port.load(config_name)
            await wait_for_version(seen, after.version, notify_timeout_s)
        finally:
            unsubscribe()
        assert after.version != before.version
        assert seen and seen[-1].version == after.version
        assert seen[-1].name == config_name

    async def test_unsubscribe_stops_notifications(
        self,
        config_port: ConfigPort,
        config_name: str,
        bump_config: Bump,
        notify_timeout_s: float,
    ) -> None:
        """`gone` unsubscribes before the change; `kept` proves the change was delivered."""
        gone: list[LoadedConfig] = []
        kept: list[LoadedConfig] = []

        async def on_gone(loaded: LoadedConfig) -> None:
            gone.append(loaded)

        async def on_kept(loaded: LoadedConfig) -> None:
            kept.append(loaded)

        await config_port.load(config_name)
        unsubscribe_gone = config_port.subscribe(config_name, on_gone)
        unsubscribe_kept = config_port.subscribe(config_name, on_kept)
        try:
            unsubscribe_gone()
            await bump_config()
            after = await config_port.load(config_name)
            await wait_for_version(kept, after.version, notify_timeout_s)
        finally:
            unsubscribe_kept()
        assert kept and kept[-1].version == after.version
        assert gone == []

    async def test_store_failure_raises_config_unavailable(
        self, unavailable_config_port: ConfigPort, config_name: str
    ) -> None:
        with pytest.raises(ConfigUnavailable):
            await unavailable_config_port.load(config_name)
