"""The test doubles of the replica harness: a model that can hang, a store a replica can lose, a
task store the test thread can read, and a clock the test moves. Used through
`poc_harness.replicas`, and on their own by tests that build a store.

- **One model for all.** The `HarnessModel` is shared, so `len(model.calls)` counts the model
  calls of every replica.
- **A kill switch.** `SeverableState` wraps the shared store for one replica; `sever()` makes
  every later call from that replica fail, as after SIGKILL: its lease is no longer renewed, and
  its finish and release never land.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

from a2a.server.tasks import InMemoryTaskStore
from chassis.fakes import ScriptedModel
from chassis.fakes.model import ScriptRule
from chassis.ports.model import ModelChunk, ModelMessage, ModelResult, ToolSpec
from chassis.ports.state import StatePort, StateUnavailable

from poc_harness.scenario import SIMPLIFIED

SLOW = "simplify: SLOW"
"""A prompt the model answers after `HarnessModel.slow_s`."""
HANG = "simplify: HANG"
"""A prompt the model never answers while `HarnessModel.hang_armed` is set (once)."""
FAIL = "simplify: FAIL"
"""A prompt the model answers with a retryable error."""


class OffsetClock:
    """`time.monotonic()` plus an offset the test moves forward. For `InMemoryState(clock=...)`."""

    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.monotonic() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


class HarnessModel(ScriptedModel):
    """`ScriptedModel` answering `SIMPLIFIED` to anything, `FAIL` with a retryable error, `SLOW`
    after `slow_s`, and `HANG` never while `hang_armed` is set (the first such call disarms it, so
    a retry of the same input is answered). Runs on the replicas' loop, in another thread, so its
    signals are `threading.Event`s.
    """

    def __init__(self, slow_s: float = 0.4) -> None:
        super().__init__(
            [
                ScriptRule(match=FAIL, error="model.overloaded", retryable=True),
                ScriptRule(match=None, reply=SIMPLIFIED),
            ]
        )
        self.slow_s = slow_s
        self.hang_armed = threading.Event()
        self.hung = threading.Event()

    async def _wait(self, messages: Sequence[ModelMessage]) -> None:
        text = " ".join(m.content or "" for m in messages if m.role == "user")
        if HANG in text and self.hang_armed.is_set():
            self.hang_armed.clear()
            self.hung.set()
            await asyncio.Event().wait()  # until the run is cancelled
        if SLOW in text:
            await asyncio.sleep(self.slow_s)

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        await self._wait(messages)
        return await super().complete(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        )

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        await self._wait(messages)
        async for chunk in super().stream(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        ):
            yield chunk

    def user_texts(self) -> list[str]:
        """Every model call's user and assistant texts joined, one string per call."""
        return [
            " ".join(m.content or "" for m in call if m.role in ("user", "assistant"))
            for call in self.calls
        ]


class SeverableState:
    """One replica's view of the shared store. After `sever()` every call raises
    `StateUnavailable`, so nothing this replica does reaches the store any more: a killed process.
    """

    def __init__(self, inner: StatePort) -> None:
        self.inner = inner
        self.severed = False

    def sever(self) -> None:
        self.severed = True

    def _check(self) -> None:
        if self.severed:
            raise StateUnavailable("replica killed")

    async def get(self, key: str) -> bytes | None:
        self._check()
        return await self.inner.get(key)

    async def set(self, key: str, value: bytes, *, ttl_s: float | None = None) -> None:
        self._check()
        await self.inner.set(key, value, ttl_s=ttl_s)

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        self._check()
        return await self.inner.set_if_absent(key, value, ttl_s=ttl_s)

    async def compare_and_set(
        self, key: str, expected: bytes, value: bytes | None, *, ttl_s: float | None = None
    ) -> bool:
        self._check()
        return await self.inner.compare_and_set(key, expected, value, ttl_s=ttl_s)

    async def delete(self, key: str) -> None:
        self._check()
        await self.inner.delete(key)

    async def aclose(self) -> None:
        return None  # the shared store outlives every replica


class RecordingTaskStore(InMemoryTaskStore):
    """The A2A server's task store, with the ids of the tasks it holds in a plain set the test
    thread can read (the store itself lives on the workload's loop)."""

    def __init__(self) -> None:
        super().__init__()
        self.live: set[str] = set()
        self.ever: set[str] = set()

    async def save(self, task: Any, *args: Any, **kwargs: Any) -> None:
        await super().save(task, *args, **kwargs)
        self.live.add(task.id)
        self.ever.add(task.id)

    async def delete(self, task_id: str, *args: Any, **kwargs: Any) -> None:
        await super().delete(task_id, *args, **kwargs)
        self.live.discard(task_id)
