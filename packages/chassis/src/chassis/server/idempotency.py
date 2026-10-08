"""Idempotency over `StatePort` (PoC-4, 018 H-18; plan section 4). No HTTP in this module.

A call with a key the client sent (the `Idempotency-Key` header or the native body's
`idempotency_key`) runs once per agent and key. A minted key never repeats, so such a call never
touches the store: check `Idempotency.applies(key_from)` first.

**The store key** is `chassis:idem:v1:<agent>:<sha256(key) hex>` (`store_key`, suggested).
**The fingerprint** is the sha256 of the canonical JSON of `{agent, input, context_ref}`
(`fingerprint`); the ids, `stream`, `budget`, `agent_version`, and the interface are not in it.

**The entry** is JSON bytes. A claim is a lease, `ttl_s=lease_s`, renewed every `lease_s / 3`
while the run is alive:
`{"v": 1, "state": "running", "owner": "<replica id>:<uuid4 hex>", "fingerprint": "..."}`.
A finished run is cached for `ttl_s`:
`{"v": 1, "state": "done", "fingerprint": "...", "request": <Request>, "events": [<wire
events>], "response": <Response>}`. `request` is the original request, so a replay can bind
its `ReplyMeta` to the original ids.

**`begin(request, *, wait_ms)`** returns one of:

- `Claim`: this call runs. Its renew task is already started.
- `Replay(request, events, response)`: a run with the same fingerprint finished; replay it.
- `Refusal("idempotency_conflict")` (422): the key holds another fingerprint, running or done.
- `Refusal("idempotency_in_progress")` (409): a run with the same fingerprint is still in
  flight after `wait_ms`. The duplicate polls every `wait_poll_ms` until then. When the entry is
  gone (finished with an error, released, or its lease expired after its holder died), the
  duplicate claims it itself: the takeover, counted as `chassis.idempotency.taken_over`. Its
  `Claim.timeout_ms` is what is left of `wait_ms` (at least `MIN_RUN_MS`, suggested 500); `serve`
  runs it with that timeout, so the client never waits about twice its own. An entry gone
  between the claim attempt and the read is polled the same way, within the same deadline.
- A final `Refusal("idempotency_in_progress")` (409, not retryable, its own `message`): the key
  holds a `too_large` marker (below), or an entry of another version (`v` is not 1, as in a
  rolling upgrade). The entry is left alone; counted as `.unknown_version` for the second. A
  503 would be retried until `ttl_s` ran out, an outage per key; the key was not misused, so 409
  over 422.
- `Refusal("state_unavailable")` (503): the store failed (`StateUnavailable`) or holds an entry
  that cannot be read. suggested: a keyed call fails closed.

`Refusal.status`, `.retryable`, and `.message` come from `chassis.core.inbound`.

**`Claim.finish(events, response)`** caches the run when its last event is `end` (`ok`,
`retry`, or `fallback`) and the entry fits `max_entry_bytes`. Over it, a small marker
`{"v": 1, "state": "too_large", "fingerprint": "..."}` is stored for `ttl_s`, so the agent never
runs twice for one key. Anything else frees the key, so a retry runs again (suggested: errors
are never cached). **`Claim.release()`** frees the key.
Each stops the renew task. Only the first `finish` or `release` of a claim acts; later calls do
nothing. Neither raises: a store failure is counted and logged. A lost claim (`Claim.lost`)
never writes again.

**The fence (lease loss).** A claim is lost when a renew finds another owner's entry (or none),
or when no renew got through for `FENCE_FRACTION * lease_s` (suggested: 0.8), timed on the
replica's own monotonic clock from the send time of the last renew that did (or of the claim).
The store's lease runs at least `lease_s` from that same send time, so on a replica whose clock
runs at the store's rate the fence comes before another replica can take the key over. A store
that fails or answers slower than the time left to the fence counts as a failed renew. Losing
the claim runs the `Claim.on_lost` callbacks: `serve` cancels the run (the same cancel a client
disconnect causes), answers 409 `idempotency_in_progress` (retryable: a retry with the same key
gets the result of the call that holds the key now), and calls no `on_finished` callback, so a
lost run publishes no result event. Counted as `.fenced` (by the clock) and `.lease_lost` (any
loss).

What is still possible: (1) the side effects the engine of the lost run caused up to the moment
of the cancel (a model call already sent, a tool already called); write tools (PoC-5) need their
own idempotency. (2) The window between the fence and the cancel landing in the engine, and on a
replica whose clock or scheduler stalls (a paused VM, a long GC) past the store's lease, a short
overlap of two runs. Neither publishes twice: a lost run calls no `on_finished`. (3) A stream
that already sent its last event when its claim is lost: the client saw the answer, which is not
cached and not published; the call that took the key over gives the kept one.

**Wiring for `serve` (P11).** Build one `Idempotency(state.ports.state,
config.spec.idempotency, telemetry)` per pipeline; it does no I/O when built. After a config
reload, set `idempotency.spec` to the new spec (only `ttl_s` reloads).

After `to_request` and `enforce_limits`, and before `open_run`:

    if idempotency.applies(ids.key_from):
        outcome = await idempotency.begin(request, wait_ms=request.budget.timeout_ms)

- `Refusal`: answer `adapter.error(outcome.code, outcome.message, outcome.retryable, meta)`.
  No run is opened.
- `Replay`: no run is opened, nothing is charged, the engine is not called. Inside a
  `chassis.idempotent_replay` span (`interface`, `mode`), count
  `chassis.idempotency.replayed{interface}`, then with `meta = meta.for_request(outcome.request)`:
  complete mode answers `adapter.complete(outcome.response, meta)`; stream mode answers
  `adapter.stream(<async iterator over outcome.events>, meta)`. Every replayed answer carries
  `Idempotent-Replayed: true`.
- `Claim`: open the run as usual, and call exactly one of these once the run is over, whatever
  happened (a `finally`):
  - complete mode, after `_complete`: `await claim.finish(run.seen, response)`, where
    `response` is the `Response` the run gave.
  - stream mode, from `RunStream`'s `on_close`, after the events are closed:
    `await claim.finish(run.seen, await meta.collect(run.seen))`. A stream the client left
    early has no `end` in `run.seen`, so it is not cached.
  - the run never opened, or the chassis failed: `await claim.release()`.

**Telemetry** (suggested: the names): `chassis.idempotency.claimed`,
`.refused{code}`, `.waited`, `.taken_over`, `.lease_lost`, `.renew_failed`,
`.fenced`, `.unknown_version`, `.not_cached{reason}` (`no_end`, `too_large`), `.finish_failed`.
Logs carry the store key,
which holds only the hash of the client's key.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import socket
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from chassis.core.envelope import Request, Response
from chassis.core.events import End, Event, UnsupportedSchemaVersion, parse_event
from chassis.core.inbound import public_message, status_for
from chassis.ports.state import StatePort, StateUnavailable
from chassis.ports.telemetry import TelemetryPort
from chassis.server.config import IdempotencySpec

__all__ = [
    "KEY_PREFIX",
    "Claim",
    "Idempotency",
    "Refusal",
    "RefusalCode",
    "Replay",
    "fingerprint",
    "store_key",
]

KEY_PREFIX = "chassis:idem:v1:"
"""suggested: the namespace of every idempotency entry in the state store."""

ENTRY_VERSION = 1

MIN_RUN_MS = 500
"""suggested: the least budget a run that took a key over after a wait gets (`Claim.timeout_ms`)."""

TOO_LARGE_MESSAGE = (
    "the result for this Idempotency-Key was too large to cache, so it cannot be replayed; "
    "send a new key to run the request again"
)
OTHER_VERSION_MESSAGE = (
    "this Idempotency-Key is held by an entry another version of the chassis wrote, which this "
    "replica cannot replay; send a new key to run the request again"
)

FENCE_FRACTION = 0.8
"""suggested: a claim is lost when no renew got through for this share of `lease_s`, measured on
the replica's own monotonic clock from the send time of the last renew that did (or the claim).
The store's lease runs at least `lease_s` from that send time, so the fence comes first."""

RefusalCode = Literal["idempotency_conflict", "idempotency_in_progress", "state_unavailable"]

_RETRYABLE: dict[str, bool] = {
    "idempotency_conflict": False,
    "idempotency_in_progress": True,
    "state_unavailable": True,
}

Sleep = Callable[[float], Awaitable[None]]


def _nothing() -> None:
    return None


def store_key(agent: str, idempotency_key: str) -> str:
    """`chassis:idem:v1:<agent>:<sha256(idempotency_key) hex>`: scoped by agent, and hashed, so a
    long or odd key cannot shape the store key."""
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"{KEY_PREFIX}{agent}:{digest}"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fingerprint(request: Request) -> str:
    """The sha256 hex of the canonical JSON of `{agent, input, context_ref}`."""
    body = {
        "agent": request.agent,
        "input": request.input.model_dump(mode="json"),
        "context_ref": request.context_ref,
    }
    return hashlib.sha256(_canonical(body)).hexdigest()


@dataclass(frozen=True, slots=True)
class Replay:
    """A finished run under the same key and fingerprint: the original request, its wire events,
    and its `Response`, as stored."""

    request: Request
    events: tuple[Event, ...]
    response: Response


@dataclass(frozen=True, slots=True)
class Refusal:
    """This call does not run. Answer it with `adapter.error(code, message, retryable, meta)`.
    `final` marks a refusal a retry with the same key cannot change (not retryable), with its
    own `detail` text."""

    code: RefusalCode
    final: bool = False
    detail: str | None = None

    @property
    def retryable(self) -> bool:
        return False if self.final else _RETRYABLE[self.code]

    @property
    def status(self) -> int:
        return status_for(self.code, self.retryable)

    @property
    def message(self) -> str:
        return self.detail or public_message(self.code)


class Claim:
    """This call holds the key. Call `finish` or `release` once the run is over."""

    def __init__(
        self, owner: Idempotency, request: Request, key: str, entry: bytes, *, since: float
    ) -> None:
        self._idem = owner
        self.request = request
        self.key = key
        self._entry = entry
        self._fingerprint = fingerprint(request)
        self._closed = False
        self._lost = False
        self._last_ok = since
        self._on_lost: list[Callable[[], None]] = []
        self._renewer: asyncio.Task[None] | None = None
        self.timeout_ms: int | None = None
        """Set when this call took the key over after a wait: the budget left of its own
        `timeout_ms` (never below `MIN_RUN_MS`). `serve` runs it with this timeout."""

    @property
    def lost(self) -> bool:
        """The claim is lost: fenced (no renew got through for `FENCE_FRACTION * lease_s`), or
        the store holds another owner's entry. A lost claim never writes again."""
        return self._lost

    def on_lost(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Call `callback` (sync, in the task that finds the loss) once the claim is lost; at
        once when it is lost already. Returns a function that removes the callback."""
        if self._lost:
            callback()
            return _nothing
        self._on_lost.append(callback)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._on_lost.remove(callback)

        return remove

    def _fence_at(self) -> float:
        return self._last_ok + self._idem.spec.lease_s * FENCE_FRACTION

    def _fenced(self) -> bool:
        return self._idem.clock() >= self._fence_at()

    def _start_renewing(self) -> None:
        self._renewer = asyncio.create_task(self._renew_loop(), name=f"idem-renew:{self.key}")

    async def _renew_loop(self) -> None:
        interval = self._idem.spec.lease_s / 3
        while True:
            until_fence = self._fence_at() - self._idem.clock()
            await self._idem.renew_sleep(max(min(interval, until_fence), 0.0))
            if not await self.renew():
                return

    async def _stop_renewing(self) -> None:
        task, self._renewer = self._renewer, None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def renew(self) -> bool:
        """Extend the lease once. False when the claim is over or lost. A store failure (or a
        call slower than the time left to the fence) keeps the claim until the fence time, and
        is counted; past the fence time the claim is lost."""
        if self._closed or self._lost:
            return False
        if self._check_fence():
            return False
        idem = self._idem
        sent = idem.clock()
        try:
            kept = await asyncio.wait_for(
                idem.state.compare_and_set(
                    self.key, self._entry, self._entry, ttl_s=idem.spec.lease_s
                ),
                timeout=max(self._fence_at() - sent, 0.001),
            )
        except (StateUnavailable, TimeoutError) as exc:
            idem.telemetry.counter("chassis.idempotency.renew_failed")
            idem.log("idempotency renew failed", self.key, exc)
            return not self._check_fence()
        if not kept:
            self._lose()
            return False
        self._last_ok = sent  # the store's new lease started no earlier than `sent`
        return True

    def _check_fence(self) -> bool:
        """Lose the claim when the fence time has passed. True when lost."""
        if self._lost:
            return True
        if not self._fenced():
            return False
        self._idem.telemetry.counter("chassis.idempotency.fenced")
        self._lose()
        return True

    def _lose(self) -> None:
        self._lost = True
        self._idem.telemetry.counter("chassis.idempotency.lease_lost")
        self._idem.telemetry.log("warning", "idempotency lease lost", key=self.key)
        callbacks, self._on_lost = self._on_lost, []
        for callback in callbacks:
            try:
                callback()
            except Exception as exc:  # one callback must not stop the others
                self._idem.log("idempotency on_lost callback failed", self.key, exc)

    async def finish(self, events: Sequence[Event], response: Response) -> bool:
        """Cache the run when its last event is `end` and the entry fits `max_entry_bytes`, else
        free the key. True only when the result was cached."""
        if self._closed:
            return False
        self._closed = True
        await self._stop_renewing()
        if self._check_fence():
            return False
        idem = self._idem
        if not events or not isinstance(events[-1], End):
            idem.telemetry.counter("chassis.idempotency.not_cached", reason="no_end")
            await self._free()
            return False
        done = _canonical(
            {
                "v": ENTRY_VERSION,
                "state": "done",
                "fingerprint": self._fingerprint,
                "request": self.request.model_dump(mode="json"),
                "events": [event.model_dump(mode="json") for event in events],
                "response": response.model_dump(mode="json"),
            }
        )
        if len(done) > idem.spec.max_entry_bytes:
            idem.telemetry.counter("chassis.idempotency.not_cached", reason="too_large")
            marker = _canonical(
                {"v": ENTRY_VERSION, "state": "too_large", "fingerprint": self._fingerprint}
            )
            try:
                await idem.state.compare_and_set(
                    self.key, self._entry, marker, ttl_s=idem.spec.ttl_s
                )
            except StateUnavailable as exc:
                idem.telemetry.counter("chassis.idempotency.finish_failed")
                idem.log("idempotency finish failed", self.key, exc)
            return False
        try:
            stored = await idem.state.compare_and_set(
                self.key, self._entry, done, ttl_s=idem.spec.ttl_s
            )
        except StateUnavailable as exc:
            idem.telemetry.counter("chassis.idempotency.finish_failed")
            idem.log("idempotency finish failed", self.key, exc)
            return False
        if not stored:
            self._lose()
        return stored

    async def release(self) -> None:
        """Free the key, so the next call with it runs."""
        if self._closed:
            return
        self._closed = True
        await self._stop_renewing()
        if not self._check_fence():
            await self._free()

    async def _free(self) -> None:
        try:
            await self._idem.state.compare_and_set(self.key, self._entry, None)
        except StateUnavailable as exc:
            self._idem.telemetry.counter("chassis.idempotency.finish_failed")
            self._idem.log("idempotency release failed", self.key, exc)


class Idempotency:
    """The idempotency layer of one replica. `clock` and `sleep` time the wait for a duplicate;
    `renew_sleep` (default: `sleep`) times the renew task. Tests pass fakes for all three."""

    def __init__(
        self,
        state: StatePort,
        spec: IdempotencySpec,
        telemetry: TelemetryPort,
        *,
        replica_id: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        renew_sleep: Sleep | None = None,
    ) -> None:
        self.state = state
        self.spec = spec
        self.telemetry = telemetry
        self.replica_id = replica_id or socket.gethostname()
        self.clock = clock
        self.sleep = sleep
        self.renew_sleep = renew_sleep or sleep

    def applies(self, key_from: str) -> bool:
        """True when idempotency is on and the client sent the key (`header` or `body`)."""
        return self.spec.enabled and key_from in ("header", "body")

    def log(self, message: str, key: str, exc: BaseException) -> None:
        """A warning with the store key (only a hash of the client's key) and the error class."""
        self.telemetry.log("warning", message, key=key, error=type(exc).__name__)

    def _refuse(self, code: RefusalCode, *, detail: str | None = None) -> Refusal:
        self.telemetry.counter("chassis.idempotency.refused", code=code)
        return Refusal(code, final=detail is not None, detail=detail)

    async def begin(self, request: Request, *, wait_ms: int) -> Claim | Replay | Refusal:
        """Claim the key, replay its cached result, or refuse. `wait_ms` bounds the wait for a
        duplicate in flight; pass the caller's own `budget.timeout_ms`."""
        key = store_key(request.agent, request.idempotency_key)
        mine = fingerprint(request)
        owner = f"{self.replica_id}:{uuid.uuid4().hex}"
        running = _canonical(
            {"v": ENTRY_VERSION, "state": "running", "owner": owner, "fingerprint": mine}
        )
        deadline = self.clock() + max(wait_ms, 0) / 1000
        poll_s = self.spec.wait_poll_ms / 1000
        waited = False
        try:
            while True:
                sent = self.clock()
                if waited and sent >= deadline:
                    return self._refuse("idempotency_in_progress")
                if await self.state.set_if_absent(key, running, ttl_s=self.spec.lease_s):
                    self.telemetry.counter("chassis.idempotency.claimed")
                    claim = Claim(self, request, key, running, since=sent)
                    if waited:
                        self.telemetry.counter("chassis.idempotency.taken_over")
                        left_ms = int((deadline - self.clock()) * 1000)
                        claim.timeout_ms = max(left_ms, MIN_RUN_MS)
                    claim._start_renewing()
                    return claim
                raw = await self.state.get(key)
                entry = None if raw is None else _read(raw)
                if raw is None or entry is _OTHER_VERSION:
                    if entry is _OTHER_VERSION:
                        self.telemetry.counter("chassis.idempotency.unknown_version")
                        self.telemetry.log(
                            "warning", "idempotency entry of another version", key=key
                        )
                        return self._refuse("idempotency_in_progress", detail=OTHER_VERSION_MESSAGE)
                    # gone between the claim and the read: try again, within the deadline
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        return self._refuse("idempotency_in_progress")
                    waited = True
                    await self.sleep(min(poll_s, remaining))
                    continue
                if entry is None:
                    self.telemetry.log("warning", "idempotency entry unreadable", key=key)
                    return self._refuse("state_unavailable")
                if entry.get("fingerprint") != mine:
                    return self._refuse("idempotency_conflict")
                if entry.get("state") == "too_large":
                    return self._refuse("idempotency_in_progress", detail=TOO_LARGE_MESSAGE)
                if entry.get("state") == "done":
                    replay = _replay(entry)
                    if replay is None:
                        self.telemetry.log("warning", "idempotency entry unreadable", key=key)
                        return self._refuse("state_unavailable")
                    return replay
                remaining = deadline - self.clock()
                if remaining <= 0:
                    return self._refuse("idempotency_in_progress")
                if not waited:
                    waited = True
                    self.telemetry.counter("chassis.idempotency.waited")
                await self.sleep(min(poll_s, remaining))
        except StateUnavailable as exc:
            self.log("idempotency store failed", key, exc)
            return self._refuse("state_unavailable")


_OTHER_VERSION: dict[str, Any] = {}
"""`_read`'s answer for a well-formed entry with another `v` (identity-compared)."""


def _read(raw: bytes) -> dict[str, Any] | None:
    """The entry, `_OTHER_VERSION` for an entry of another version, None when unreadable."""
    try:
        entry = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(entry, dict):
        return None
    if entry.get("v") != ENTRY_VERSION:
        return _OTHER_VERSION if isinstance(entry.get("v"), int) else None
    if entry.get("state") not in ("running", "done", "too_large"):
        return None
    return entry


def _replay(entry: dict[str, Any]) -> Replay | None:
    try:
        return Replay(
            request=Request.model_validate(entry["request"]),
            events=tuple(parse_event(event) for event in entry["events"]),
            response=Response.model_validate(entry["response"]),
        )
    except (KeyError, TypeError, ValidationError, UnsupportedSchemaVersion):
        return None
