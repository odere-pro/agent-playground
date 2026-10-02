"""StatePort: bytes by key with a TTL, and two atomic writes. Shared by every replica.

The idempotency layer keeps its claims and cached results here (PoC-4, 018 H-18). The in-memory
fake is `chassis.fakes.state.InMemoryState`; the real adapter is `chassis.adapters.valkey`.

`InMemoryState` lives here, not only in `fakes`, because `PortBundle` defaults to it and `ports`
sits below `fakes` in the layers (as `NoTools` and `NoEvents` do). `chassis.fakes.state`
re-exports it; it holds no network code.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

MAX_KEY_BYTES = 512
"""suggested: the longest key, in UTF-8 bytes."""


class StateUnavailable(RuntimeError):
    """The store cannot be reached or answered with an error. The message holds no credential."""


class StatePort(Protocol):
    """Values are bytes; the caller serializes. `ttl_s` is seconds, greater than 0.

    `set_if_absent` and `compare_and_set` are atomic: of N concurrent callers of
    `set_if_absent`, exactly one gets `True`. `compare_and_set` replaces the value only when the
    stored value equals `expected` byte for byte; `value=None` deletes; it returns `False` when the
    key is absent or the value differs. Every method raises `StateUnavailable` when the store
    fails, and no other exception.
    """

    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes, *, ttl_s: float | None = None) -> None: ...

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool: ...

    async def compare_and_set(
        self, key: str, expected: bytes, value: bytes | None, *, ttl_s: float | None = None
    ) -> bool: ...

    async def delete(self, key: str) -> None: ...

    async def aclose(self) -> None: ...


class InMemoryState:
    """A dict of `key -> (value, expires_at)`. Expired entries are dropped on read. No method
    awaits inside, so each is atomic on one event loop. `fail_next` scripts one
    `StateUnavailable`; `calls` records `(method, key)`.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._data: dict[str, tuple[bytes, float | None]] = {}
        self._fail: StateUnavailable | None = None
        self.calls: list[tuple[str, str]] = []

    def fail_next(self, exc: StateUnavailable | None = None) -> None:
        self._fail = exc or StateUnavailable("scripted failure")

    def _enter(self, method: str, key: str) -> None:
        self.calls.append((method, key))
        if self._fail is not None:
            exc, self._fail = self._fail, None
            raise exc

    def _expires(self, ttl_s: float | None) -> float | None:
        if ttl_s is None:
            return None
        if ttl_s <= 0:
            raise ValueError("ttl_s must be greater than 0")
        return self._clock() + ttl_s

    def _live(self, key: str) -> bytes | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and self._clock() >= expires_at:
            del self._data[key]
            return None
        return value

    async def get(self, key: str) -> bytes | None:
        self._enter("get", key)
        return self._live(key)

    async def set(self, key: str, value: bytes, *, ttl_s: float | None = None) -> None:
        self._enter("set", key)
        self._data[key] = (bytes(value), self._expires(ttl_s))

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        self._enter("set_if_absent", key)
        if self._live(key) is not None:
            return False
        self._data[key] = (bytes(value), self._expires(ttl_s))
        return True

    async def compare_and_set(
        self, key: str, expected: bytes, value: bytes | None, *, ttl_s: float | None = None
    ) -> bool:
        self._enter("compare_and_set", key)
        if self._live(key) != expected:
            return False
        if value is None:
            del self._data[key]
        else:
            self._data[key] = (bytes(value), self._expires(ttl_s))
        return True

    async def delete(self, key: str) -> None:
        self._enter("delete", key)
        self._data.pop(key, None)

    async def aclose(self) -> None:
        return None
