"""The cap on new idempotency keys per caller (022 H-6). No HTTP in this module.

The rate limit (`chassis.server.rate_limit`) is a rate, not a cap: at 600 a minute one caller
could add 864,000 keys in a day, and each `done` entry lives `ttl_s` in the shared store. So every
new entry a checked caller adds is also counted here, and the caller may add at most
`spec.idempotency.new_keys_per_caller` (N) per agent in one fixed window of `ttl_s` seconds.

**The counter.** One per agent and caller, in `StatePort`, so every replica that shares the store
shares the cap. The key is `chassis:idemquota:v1:<agent>:<sha256(subject)[:32]>`
(`security.subject_hash`), so the store never holds the IdP's subject; the prefix is not
`idempotency.KEY_PREFIX`, so no agent name can make a quota key look like an entry. The value is
`<count>:<window start, unix time>`, and it expires when its window ends. A call reads it and
takes one with `compare_and_set`; a key that is not there, cannot be read, or holds a window that
has ended starts a new window at 1. A write lost to another replica is read again, up to
`CAS_ATTEMPTS` times, then the call is refused (only one caller's own calls race on one key).

**The bound.** A window admits at most N new entries, and an entry lives at most `ttl_s` (a
`running` lease is renewed only while its run is alive). So at any time one caller holds at most
2N entries per agent: those of the current window and of the one before. Replicas use their own
wall clocks; skew can only move a window's end, never add a window.

**What counts.** An entry this call created (`Idempotency.begin`): a new key, and a key taken
over after its holder freed it or died. A replay, a conflict, or a wait adds nothing and is not
counted. With auth off (no subject) nothing is counted: there is no caller to tell apart, as for
the rate limit.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from chassis.ports.state import StatePort
from chassis.server.security import subject_hash

__all__ = ["CAS_ATTEMPTS", "QUOTA_PREFIX", "NewKeyQuota", "QuotaFull", "quota_key"]

QUOTA_PREFIX = "chassis:idemquota:v1:"
"""suggested: the namespace of the per-caller counters."""
CAS_ATTEMPTS = 8
"""suggested: how often a lost write is read again before the call is refused."""


@dataclass(frozen=True, slots=True)
class QuotaFull:
    """The caller has added its cap of new keys in this window. `retry_after_s`: whole seconds
    until the window ends (at least 1)."""

    retry_after_s: int


def quota_key(agent: str, subject: str) -> str:
    """The store key of one caller's counter for one agent. The subject is hashed."""
    return f"{QUOTA_PREFIX}{agent}:{subject_hash(subject)}"


def _encode(count: int, start: float) -> bytes:
    return f"{count}:{start:.6f}".encode()


def _decode(raw: bytes) -> tuple[int, float] | None:
    try:
        count, start = raw.decode("ascii").split(":", 1)
        return int(count), float(start)
    except (UnicodeDecodeError, ValueError):
        return None


class NewKeyQuota:
    """The counter over `StatePort` (module docstring). `clock` is the wall clock in seconds.
    `StateUnavailable` from the store is the caller's to handle."""

    def __init__(self, state: StatePort, *, clock: Callable[[], float] = time.time) -> None:
        self.state = state
        self.clock = clock

    async def take(
        self, agent: str, subject: str, *, cap: int, window_s: float
    ) -> QuotaFull | None:
        """Count one new key for the caller, or say how long until it may add another."""
        key = quota_key(agent, subject)
        for _ in range(CAS_ATTEMPTS):
            now = self.clock()
            raw = await self.state.get(key)
            if raw is None:
                if await self.state.set_if_absent(key, _encode(1, now), ttl_s=window_s):
                    return None
                continue
            stored = _decode(raw)
            if stored is None or now - stored[1] >= window_s:
                if await self.state.compare_and_set(key, raw, _encode(1, now), ttl_s=window_s):
                    return None
                continue
            count, start = stored
            left_s = min(window_s, start + window_s - now)
            if count >= cap:
                return QuotaFull(retry_after_s=max(1, math.ceil(left_s)))
            if await self.state.compare_and_set(
                key, raw, _encode(count + 1, start), ttl_s=max(left_s, 0.001)
            ):
                return None
        return QuotaFull(retry_after_s=1)
