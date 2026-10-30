"""The publish retry the `EventPort` adapters share (Dapr and Kafka; PoC-4, review R4-19).

An adapter wraps one publish in an `attempt` that returns on success, raises
`RetryablePublishError` for a failure the next attempt may not hit (a transport error, a 5xx, a
broker error), and raises anything else (as `PublishFailed`) to stop at once. `publish_with_retry`
makes `len(backoff_s) + 1` attempts, waits `backoff_s[i]` after the failed attempt `i + 1`, and
raises the last `RetryablePublishError`; the adapter turns it into its own `PublishFailed` text.
No product SDK and no network here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

__all__ = ["PUBLISH_BACKOFF_S", "RetryablePublishError", "publish_with_retry"]

PUBLISH_BACKOFF_S: tuple[float, ...] = (0.1, 0.5)
"""suggested: 3 publish attempts, waiting 0.1 s then 0.5 s between them, on every broker."""

sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
"""The wait between attempts, read at call time so a test can patch it."""


class RetryablePublishError(Exception):
    """One publish attempt failed and the next may not. `reason` is a short text that names the
    error class or the status, never a value or a secret."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def publish_with_retry(
    attempt: Callable[[int], Awaitable[None]], backoff_s: Sequence[float]
) -> None:
    """Call `attempt(1)`, `attempt(2)`, ... until one returns. Raise the last
    `RetryablePublishError` after `len(backoff_s) + 1` attempts; any other error at once."""
    attempts = len(backoff_s) + 1
    for number in range(1, attempts + 1):
        try:
            await attempt(number)
            return
        except RetryablePublishError:
            if number == attempts:
                raise
        await sleep(backoff_s[number - 1])
