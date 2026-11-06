"""StatePort: bytes by key with a TTL, and two atomic writes. Shared by every replica.

The idempotency layer keeps its claims and cached results here (PoC-4, 018 H-18). The in-memory
fake is `chassis.fakes.state.InMemoryState`; the real adapter is `chassis.adapters.valkey`.
`PortBundle.state` has no default: a bundle always names its store (R1-12).
"""

from __future__ import annotations

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
