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


def test_wait_until_returns_once_the_condition_holds() -> None:
    left = [2]

    def ready() -> bool:
        left[0] -= 1
        return left[0] <= 0

    wait_until(ready, "ready", 5.0, interval=0.001)

    assert left == [0]


def test_wait_until_fails_with_the_timeout_and_the_label() -> None:
    with pytest.raises(pytest.fail.Exception, match=r"0\.05 s waiting for the label"):
        wait_until(lambda: False, "the label", TIMEOUT_S, interval=0.001)


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
