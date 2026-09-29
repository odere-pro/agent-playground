"""ConfigPortContract: load gives a version, a missing name raises, a change gives a new version
and notifies.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from chassis.ports.config import ConfigNotFound, ConfigPort, LoadedConfig

Bump = Callable[[], Awaitable[None]]


@pytest.mark.contract
class ConfigPortContract:
    """Subclass as `Test*`. Provide `config_port`, `config_name` (a name that exists in the
    store), and `bump_config` (changes that config through the real store)."""

    @pytest.fixture
    def config_port(self) -> ConfigPort:
        raise NotImplementedError("provide a config_port fixture")

    @pytest.fixture
    def config_name(self) -> str:
        return "echo"

    @pytest.fixture
    def bump_config(self, config_port: ConfigPort, config_name: str) -> Bump:
        pytest.skip("override bump_config to change the config through the store")

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
        self, config_port: ConfigPort, config_name: str, bump_config: Bump
    ) -> None:
        seen: list[LoadedConfig] = []

        async def on_change(loaded: LoadedConfig) -> None:
            seen.append(loaded)

        before = await config_port.load(config_name)
        unsubscribe = config_port.subscribe(config_name, on_change)
        await bump_config()
        after = await config_port.load(config_name)
        unsubscribe()
        assert after.version != before.version
        assert seen and seen[-1].version == after.version
