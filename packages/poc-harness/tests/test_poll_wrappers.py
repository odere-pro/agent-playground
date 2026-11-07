"""The thin wrappers over `wait_until` (R4-18), and the rule that no PoC-4 test writes its own
poll loop."""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
from poc_harness.paths import ROOT
from poc_harness.waits import BODY_LIMIT, wait_ready, wait_ready_async, wait_until_serving

POC04_TESTS = ROOT / "pocs" / "poc-04-stateless-scalable" / "tests"
OWN_POLL_LOOP = re.compile(
    r"def (_?until|_?wait_ready|_?wait_for|_?until_pruned)\b|monotonic\(\) \+ "
)
"""A poll helper by name (R4-18's list), or a hand-rolled deadline."""
PAST = -1.0
"""A timeout already spent: the wait fails on its first missed poll, with no real wait."""


def _answers(*statuses: int) -> httpx.MockTransport:
    left = list(statuses)

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(left.pop(0) if len(left) > 1 else left[0], json={"n": len(left)})

    return httpx.MockTransport(answer)


class ExitedChild:
    """A child process that has already exited with output on stdout."""

    def __init__(self) -> None:
        self.killed = False

    def poll(self) -> int:
        return 1

    def kill(self) -> None:
        self.killed = True

    def communicate(self, timeout: float) -> tuple[str, None]:
        return "boom", None


def test_wait_ready_returns_the_answer_once_the_status_matches() -> None:
    """`wait_ready` polls past a 503 and returns the first 200."""
    client = httpx.Client(transport=_answers(503, 200), base_url="http://agent")

    answer = wait_ready(client, 5.0, interval=0)

    assert answer.status_code == 200


def test_wait_ready_fails_with_the_url_and_the_last_answer_at_the_deadline() -> None:
    """At the deadline `wait_ready` fails with the URL, the status it wanted, and what it saw."""
    client = httpx.Client(transport=_answers(503), base_url="http://agent")

    with pytest.raises(
        pytest.fail.Exception, match=r"http://agent/ready to answer 200; last answer 503"
    ):
        wait_ready(client, PAST, interval=0)


async def test_wait_ready_async_returns_the_answer_with_the_wanted_status() -> None:
    """`wait_ready_async` polls past a 200 and returns the first 503 when 503 is wanted."""
    client = httpx.AsyncClient(transport=_answers(200, 503), base_url="http://agent")

    answer = await wait_ready_async(client, 5.0, status=503, interval=0)

    assert answer.status_code == 503


async def test_wait_ready_async_fails_with_the_last_answer_and_its_body_at_the_deadline() -> None:
    """At the deadline `wait_ready_async` fails with the URL, the last status, and its body."""
    client = httpx.AsyncClient(transport=_answers(503), base_url="http://agent")

    with pytest.raises(
        pytest.fail.Exception,
        match=r'http://agent/ready to answer 200; last answer 503: \{"n":1\}$',
    ):
        await wait_ready_async(client, PAST, interval=0)


async def test_wait_ready_async_cuts_a_long_body_in_the_failure() -> None:
    """A long `/ready` body is cut to `BODY_LIMIT` characters in the failure message."""
    long_body = "x" * (BODY_LIMIT * 3)
    transport = httpx.MockTransport(lambda _: httpx.Response(503, text=long_body))
    client = httpx.AsyncClient(transport=transport, base_url="http://agent")

    with pytest.raises(pytest.fail.Exception) as failed:
        await wait_ready_async(client, PAST, interval=0)

    assert str(failed.value).endswith(f"last answer 503: {'x' * BODY_LIMIT}...")


def test_wait_until_serving_kills_and_raises_with_the_output_when_the_child_exits() -> None:
    """A child that exits before it serves is killed, and the error carries its output."""
    child = ExitedChild()
    probe = httpx.Client(transport=_answers(503), base_url="http://agent")
    proc: Any = child

    with pytest.raises(RuntimeError, match="the agent did not start:\nboom"):
        wait_until_serving(proc, probe, "/card", 5.0, failure="the agent did not start", interval=0)

    assert child.killed


def test_poc04_tests_define_no_poll_loop_of_their_own() -> None:
    """Given the PoC-4 tests, when they wait for a condition, then they call the one poc_harness
    helper and no PoC-4 file defines its own poll loop."""
    files = sorted(POC04_TESTS.rglob("*.py"))

    found = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for path in files
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if OWN_POLL_LOOP.search(line)
    ]

    assert files
    assert found == []
