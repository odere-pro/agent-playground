"""The steady client's call checks, over an httpx mock transport: no socket, no stack."""

from __future__ import annotations

import httpx
import pytest
from load_tools import steady_client
from load_tools.steady_client import Call


async def send_once(handler: httpx.MockTransport) -> tuple[bool, str | None]:
    """One `attempt` of a fresh call against `handler`."""
    async with httpx.AsyncClient(transport=handler, base_url="http://agent") as client:
        return await steady_client.attempt(client, Call(key="k-1", text="hi"))


def answer(status: int, body: object) -> httpx.MockTransport:
    """A transport that answers every request with `status` and the JSON `body`."""
    return httpx.MockTransport(lambda request: httpx.Response(status, json=body))


async def test_load_tools_attempt_counts_an_ok_envelope_as_success() -> None:
    """An HTTP 200 with an `ok` envelope is a success."""
    transport = answer(200, {"status": "ok", "output": {"text": "hi"}})

    outcome = await send_once(transport)

    assert outcome == (True, None)


async def test_load_tools_attempt_fails_an_error_envelope() -> None:
    """An HTTP 200 whose envelope says `error` is a failed call."""
    transport = answer(200, {"status": "error", "output": {}})

    ok, _ = await send_once(transport)

    assert ok is False


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("ConnectError: refused", True),
        ("HTTP 503: busy", True),
        ("HTTP 409: in progress", True),
        ("HTTP 400: bad input", False),
    ],
)
def test_load_tools_retryable_retries_transport_errors_and_retry_statuses(
    error: str, expected: bool
) -> None:
    """Transport errors and the retry statuses are retried; a client error is not."""
    value = steady_client.retryable(error)

    assert value is expected


@pytest.mark.parametrize(
    ("values", "expected"),
    [([], None), ([4.0], 4.0), ([1.0, 2.0, 3.0, 4.0, 5.0], 3.0)],
)
def test_load_tools_percentile_handles_short_samples(
    values: list[float], expected: float | None
) -> None:
    """The p50 of a sample; one value is its own percentile, and none gives None."""
    value = steady_client.percentile(values, 50)

    assert value == expected
