"""Pins what `Idempotency.begin` and `Claim.finish` answer for each kind of stored entry, and how
`store_key` encodes a key (review R4-03, R4-15, R4-27). Entries are written straight into
`InMemoryState` in the documented format, so the pins do not lean on the code under test.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from chassis.core.envelope import Budget, Request, Response, TaskInput, Versions
from chassis.core.events import End, Event, Start
from chassis.fakes import InMemoryState, InMemoryTelemetry
from chassis.server.config import IdempotencySpec
from chassis.server.idempotency import (
    OTHER_VERSION_MESSAGE,
    TOO_LARGE_MESSAGE,
    Claim,
    Idempotency,
    Refusal,
    Replay,
    fingerprint,
    store_key,
)

SPEC = IdempotencySpec(ttl_s=60, lease_s=6, wait_poll_ms=100, max_entry_bytes=4096)
VERSIONS = Versions(chassis="c", config="cfg", prompt="p", model_route="route")


def _request(key: str = "key-1", text: str = "hello") -> Request:
    return Request(
        request_id="r1",
        trace_id="t1",
        idempotency_key=key,
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text=text),
        budget=Budget(timeout_ms=30_000),
    )


def _events(request: Request) -> list[Event]:
    return [Start(request_id=request.request_id), End(output={"text": "HELLO"})]


def _response(request: Request) -> Response:
    return Response(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        output={"text": "HELLO"},
        status="ok",
        versions=VERSIONS,
    )


def _entry(kind: str, request: Request) -> bytes | None:
    """The raw bytes another replica (or another version) left under the key."""
    mine = fingerprint(request)
    done: dict[str, Any] = {
        "v": 1,
        "state": "done",
        "fingerprint": mine,
        "request": request.model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in _events(request)],
        "response": _response(request).model_dump(mode="json"),
    }
    entries: dict[str, dict[str, Any] | bytes | None] = {
        "absent": None,
        "held": {"v": 1, "state": "running", "owner": "other:1", "fingerprint": mine},
        "conflict_running": {"v": 1, "state": "running", "owner": "o:1", "fingerprint": "x"},
        "conflict_done": {**done, "fingerprint": "x"},
        "done": done,
        "done_unreadable": {k: v for k, v in done.items() if k != "response"},
        "too_large": {"v": 1, "state": "too_large", "fingerprint": mine},
        "other_version": {"v": 2, "state": "done", "anything": True},
        "unreadable": b"\x00not json",
    }
    value = entries[kind]
    return value if value is None or isinstance(value, bytes) else json.dumps(value).encode()


def _summary(outcome: Claim | Replay | Refusal) -> tuple[Any, ...]:
    if isinstance(outcome, Refusal):
        return ("refusal", outcome.code, outcome.final, outcome.detail)
    return (type(outcome).__name__.lower(),)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("absent", ("claim",)),
        ("held", ("refusal", "idempotency_in_progress", False, None)),
        ("conflict_running", ("refusal", "idempotency_conflict", False, None)),
        ("conflict_done", ("refusal", "idempotency_conflict", False, None)),
        ("done", ("replay",)),
        ("done_unreadable", ("refusal", "state_unavailable", False, None)),
        ("too_large", ("refusal", "idempotency_in_progress", True, TOO_LARGE_MESSAGE)),
        ("other_version", ("refusal", "idempotency_in_progress", True, OTHER_VERSION_MESSAGE)),
        ("unreadable", ("refusal", "state_unavailable", False, None)),
    ],
)
async def test_begin_answers_as_pinned_when_the_key_holds_each_kind_of_entry(
    kind: str, expected: tuple[Any, ...]
) -> None:
    """`begin` with no wait gives the same claim, replay, or refusal for every stored entry."""
    state, request = InMemoryState(), _request()
    raw = _entry(kind, request)
    if raw is not None:
        await state.set(store_key("echo", "key-1"), raw, ttl_s=60)
    idem = Idempotency(state, SPEC, InMemoryTelemetry())

    outcome = await idem.begin(request, wait_ms=0)

    assert _summary(outcome) == expected
    if isinstance(outcome, Claim):
        await outcome.release()


@pytest.mark.parametrize("max_entry_bytes", [4096, 64], ids=["normal", "too_large"])
async def test_finish_counts_one_failure_and_returns_false_when_the_final_write_fails(
    max_entry_bytes: int,
) -> None:
    """A store failure on the last write of `finish` is counted once and never raised."""
    state, telemetry, request = InMemoryState(), InMemoryTelemetry(), _request()
    spec = SPEC.model_copy(update={"max_entry_bytes": max_entry_bytes})
    claim = await Idempotency(state, spec, telemetry).begin(request, wait_ms=0)
    assert isinstance(claim, Claim)
    state.fail_next()

    finished = await claim.finish(_events(request), _response(request))

    assert finished is False
    assert telemetry.counter_value("chassis.idempotency.finish_failed") == 1
    assert claim.lost is False


@pytest.mark.parametrize("key", ["key-1", "ключ-é-键-🔑"], ids=["ascii", "non_ascii"])
def test_store_key_hashes_as_before_when_the_key_is_valid_unicode(key: str) -> None:
    """A key with no lone surrogate keeps the store key the strict UTF-8 hash gave."""
    before = "chassis:idem:v1:echo:" + hashlib.sha256(key.encode("utf-8")).hexdigest()

    assert store_key("echo", key) == before


def test_store_key_returns_a_key_when_the_key_is_a_lone_surrogate() -> None:
    """A lone surrogate (valid JSON, invalid UTF-8) is hashed, not raised."""
    key = store_key("echo", "\ud800")

    assert key == "chassis:idem:v1:echo:" + hashlib.sha256(b"\xed\xa0\x80").hexdigest()


async def test_begin_claims_the_key_when_it_is_a_lone_surrogate() -> None:
    """`begin` with a lone-surrogate key claims it like any other key."""
    idem = Idempotency(InMemoryState(), SPEC, InMemoryTelemetry())

    outcome = await idem.begin(_request(key="\ud800"), wait_ms=0)

    assert isinstance(outcome, Claim)
    await outcome.release()


async def test_begin_polls_every_wait_poll_ms_until_the_deadline_when_a_duplicate_waits() -> None:
    """A duplicate of a held key sleeps `wait_poll_ms` each time, the last cut at its deadline."""
    now, waits = [0.0], []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    state, request = InMemoryState(clock=lambda: 1000.0), _request()
    held = _entry("held", request)
    assert held is not None
    await state.set(store_key("echo", "key-1"), held, ttl_s=60)
    idem = Idempotency(state, SPEC, InMemoryTelemetry(), clock=lambda: now[0], sleep=sleep)

    outcome = await idem.begin(request, wait_ms=450)

    assert outcome == Refusal("idempotency_in_progress")
    assert waits == pytest.approx([0.1, 0.1, 0.1, 0.1, 0.05])
