"""InMemoryConfig: a ConfigPort held in a dict. `put` bumps the version and notifies subscribers.
`fail_next` scripts one `ConfigUnavailable` on the next `load`."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from chassis.ports.config import (
    ConfigCallback,
    ConfigNotFound,
    ConfigUnavailable,
    LoadedConfig,
    Unsubscribe,
)


class InMemoryConfig:
    def __init__(self, configs: dict[str, dict[str, Any]] | None = None) -> None:
        self._data: dict[str, LoadedConfig] = {}
        self._subs: dict[str, list[ConfigCallback]] = defaultdict(list)
        for name, data in (configs or {}).items():
            self._data[name] = LoadedConfig(name=name, version="1", data=data)
        self._fail: ConfigUnavailable | None = None

    def fail_next(self, exc: ConfigUnavailable | None = None) -> None:
        """The next `load` raises `exc` (default: a scripted `ConfigUnavailable`)."""
        self._fail = exc or ConfigUnavailable("scripted outage")

    async def load(self, name: str) -> LoadedConfig:
        if self._fail is not None:
            exc, self._fail = self._fail, None
            raise exc
        try:
            return self._data[name]
        except KeyError as exc:
            raise ConfigNotFound(name) from exc

    def subscribe(self, name: str, callback: ConfigCallback) -> Unsubscribe:
        self._subs[name].append(callback)

        def unsubscribe() -> None:
            self._subs[name].remove(callback)

        return unsubscribe

    async def put(self, name: str, data: dict[str, Any]) -> LoadedConfig:
        current = self._data.get(name)
        version = str(int(current.version) + 1) if current else "1"
        loaded = LoadedConfig(name=name, version=version, data=data)
        self._data[name] = loaded
        for callback in list(self._subs[name]):
            await callback(loaded)
        return loaded
