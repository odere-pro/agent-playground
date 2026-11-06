"""`wait_until` raises on timeout (R4-18), `sse_events` keeps names and non-JSON data (R4-17), and
replicas refuse a target and lane that cannot run (R0-16)."""

from __future__ import annotations

import pytest
from poc_harness.interfaces import TYPESCRIPT, OutboundRouter
from poc_harness.replica_doubles import HarnessModel
from poc_harness.replicas import replicas_on_unix_sockets
from poc_harness.sse import sse_events
from poc_harness.waits import wait_until, wait_until_async

TIMEOUT_S = 0.05


class FakeClock:
    """A monotonic clock that moves only when `sleep` is called."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_wait_until_returns_once_the_condition_holds() -> None:
    """`wait_until` returns on the poll where the condition first holds, sleeping between polls."""
    clock = FakeClock()
    left = [2]

    def ready() -> bool:
        left[0] -= 1
        return left[0] <= 0

    wait_until(ready, "ready", 5.0, interval=0.5, clock=clock.monotonic, sleep=clock.sleep)

    assert (left, clock.sleeps) == ([0], [0.5])


def test_wait_until_fails_with_the_timeout_and_the_label() -> None:
    """At the deadline `wait_until` fails with the timeout and the label, never quietly."""
    clock = FakeClock()

    expected = r"^timed out after 2\.0 s waiting for the label$"

    with pytest.raises(pytest.fail.Exception, match=expected):
        wait_until(
            lambda: False, "the label", 2.0, interval=0.5, clock=clock.monotonic, sleep=clock.sleep
        )

    assert clock.now == 2.5


async def test_wait_until_async_takes_a_plain_condition() -> None:
    await wait_until_async(lambda: True, "ready", 5.0)


async def test_wait_until_async_awaits_an_async_condition() -> None:
    left = [2]

    async def ready() -> bool:
        left[0] -= 1
        return left[0] <= 0

    await wait_until_async(ready, "ready", 5.0, interval=0.001)

    assert left == [0]


async def test_wait_until_async_fails_with_the_timeout_and_the_label() -> None:
    async def never() -> bool:
        return False

    with pytest.raises(pytest.fail.Exception, match=r"0\.05 s waiting for the label"):
        await wait_until_async(never, "the label", TIMEOUT_S, interval=0.001)


def test_sse_events_keeps_the_name_the_json_and_a_raw_done() -> None:
    body = 'event: delta\r\ndata: {"text": "a"}\r\n\r\ndata: {"n": 1}\n\ndata: [DONE]\n\n: ping\n\n'

    assert sse_events(body) == [("delta", {"text": "a"}), (None, {"n": 1}), (None, "[DONE]")]


def test_a_typescript_target_in_the_inprocess_lane_is_refused() -> None:
    model = HarnessModel()

    with (
        pytest.raises(ValueError, match="sidecar lane only"),
        replicas_on_unix_sockets(1, TYPESCRIPT, "inprocess", model=model, router=OutboundRouter()),
    ):
        pass


def test_an_unknown_lane_is_refused() -> None:
    model = HarnessModel()

    with (
        pytest.raises(ValueError, match="unknown lane"),
        replicas_on_unix_sockets(1, "echo-python", "nowhere", model=model, router=OutboundRouter()),
    ):
        pass
