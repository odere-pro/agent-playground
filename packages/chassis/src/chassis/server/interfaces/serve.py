"""`serve`: one inbound call, the same for every interface, over an `InboundAdapter`.

A router validates the body (FastAPI does), resolves the ids (`ids.resolve_ids`), and calls
`serve`. In order:

1. Not ready: `adapter.error("not_ready", ...)`, 503 in the format's shape.
2. `adapter.to_request`, then `limits.enforce_limits` (`spec.limits`); a `Refused` from either
   is answered as it is.
3. Idempotency (PoC-4, `server.idempotency`), only for a key the client sent (`ids.key_from`):
   `begin(request, wait_ms=budget.timeout_ms)`. A `Refusal` is `adapter.error(code, ...)`
   (422 `idempotency_conflict`, 409 `idempotency_in_progress`, 503 `state_unavailable`). A
   `Replay` answers the stored run (below) and opens no run. A `Claim` goes on; one that took
   the key over after a wait runs with what is left of its budget (`Claim.timeout_ms`). A claim
   lost mid-run (`server.idempotency`, the fence) cancels the run and answers 409
   `idempotency_in_progress`, retryable, in complete mode or before the first delta; later in a
   stream, as an `error` event.
4. `ids.open_run`, re-minting the trace id when `ids.may_remint` allows; otherwise a trace id in
   use is `adapter.error("trace_id_in_use", ...)` (native: 409). The claim is released.
5. `chassis.inbound_ignored{interface, param}` (suggested) once per name in `ignored`.
6. Complete: the whole run, then `adapter.complete(response, meta)`. With `errors_as_http`, a run
   that had an `error` event is `adapter.error(code, message, retryable, meta)` instead. With
   `disconnected` (the route's `http.is_disconnected`), a watcher polls it every
   `DISCONNECT_POLL_S`; when the client has left, the run is cancelled (its events closed, which
   cancels the A2A task and frees its budget), `chassis.client_disconnected{interface}` is
   counted, and the claim is released. MCP has no watcher: its inner hop does not see the client.
7. Stream: `RunStream` over `adapter.stream(events, meta)`. With `errors_as_http`, the hold rule
   (`hold.hold`) runs first, and an `error` before the first `delta` is an HTTP error.

8. Any other exception after the run opens (the adapter's `complete` or `stream` raising, a
   telemetry error, a reader exception from the hold rule) closes the run, is logged with its
   detail, and is `adapter.error("internal_error", <fixed message>, False, meta)`: a 500 with
   `x-should-retry: false` for the SDK formats, so their clients do not run the agent again.

When a run is over, once: a claim is finished (`Claim.finish(run.seen, response)`: cached when the
run ended with `end`) when the run is `done`, else released; then `RunPipeline.finished(run)`
calls the `on_finished` callbacks. A chassis failure releases the claim. Complete mode does this
after the run; stream mode in `RunStream`'s `on_close`, after the events are closed.

**Replay.** The stored `Response` and events, bound to the original request
(`meta.for_request`, the original versions): complete mode is `adapter.complete`, stream mode
`adapter.stream` over the stored events, in the mode of this call. Inside a
`chassis.idempotent_replay` span (`interface`, `mode`), counted as
`chassis.idempotency.replayed{interface}`. Every replayed answer carries `Idempotent-Replayed:
true` (`REPLAYED_HEADER`, suggested); a first run never does.

A run `error` answered as HTTP carries its code and the fixed text for it (`public_message`), never
the run's own message, which can hold internal URLs or an upstream body; that stays in the log and
the `chassis.run` span.

`errors_as_http` is True for OpenAI and Anthropic, False for native and MCP (their run errors are
a 200 envelope with `status: error`). The run's trace id is freed on every path.

`SSE_EVENT_SCHEMA` is the one declared schema of a streamed 200 on every interface: one
server-sent event, `data` required, `event`, `id`, and `retry` optional (OpenAPI 3.1 tooling such
as Schemathesis checks it against each event). `event_stream_content()` is the response
`content` entry the routes declare.
"""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import aclosing, suppress
from dataclasses import replace
from typing import Any

from starlette.responses import JSONResponse, StreamingResponse
from starlette.responses import Response as HTTPResponse

from chassis.core.events import Error, Event
from chassis.core.inbound import (
    InboundAdapter,
    Refused,
    Reply,
    ReplyMeta,
    StreamReply,
    public_message,
)
from chassis.server.correlation import TraceIdInUse
from chassis.server.idempotency import Claim, Refusal, Replay
from chassis.server.interfaces.hold import hold
from chassis.server.interfaces.ids import ResolvedIds, may_remint, open_run
from chassis.server.interfaces.limits import enforce_limits
from chassis.server.pipeline import Run, RunPipeline, RunStream

__all__ = [
    "CLIENT_DISCONNECTED",
    "DISCONNECT_POLL_S",
    "IGNORED",
    "INTERNAL_ERROR",
    "NOT_READY_MESSAGE",
    "REPLAYED_HEADER",
    "SSE_EVENT_SCHEMA",
    "event_stream_content",
    "serve",
    "to_response",
    "trace_id_in_use_message",
]

IGNORED = "chassis.inbound_ignored"
REPLAYED_HEADER = "Idempotent-Replayed"
"""suggested: the header on every replayed answer (as Stripe and the IETF draft use)."""
REPLAYED = "chassis.idempotency.replayed"
REPLAY_SPAN = "chassis.idempotent_replay"
CLIENT_DISCONNECTED = "chassis.client_disconnected"
"""suggested: a complete call whose client left before the answer; the run was cancelled."""
DISCONNECT_POLL_S = 0.25
"""suggested: how often a complete call checks that its client is still there."""
CLIENT_GONE_STATUS = 499
"""The status of the answer nobody reads after a client left (nginx's convention)."""
Disconnected = Callable[[], Awaitable[bool]]
CLAIM_LOST = "chassis.idempotency.run_fenced"
"""suggested: a run whose claim was lost; it was cancelled and published nothing."""
CLAIM_LOST_CODE = "idempotency_in_progress"
"""The answer of a run whose claim was lost (409, retryable): another call may hold the key now,
and a retry with the same key gets that call's result. See `server.idempotency`."""
NOT_READY_MESSAGE = "engine not ready"
INTERNAL_ERROR = "internal_error"
"""The code for an exception after the run opened that is not a run `error` event: 500, not
retryable, a fixed message (`public_message`); the detail is logged."""

SSE_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["data"],
    "properties": {
        "event": {"type": "string"},
        "data": {"type": "string"},
        "id": {"type": "string"},
        "retry": {"type": "integer"},
    },
}
"""One server-sent event, as the 200 `text/event-stream` schema of every streaming route."""


def event_stream_content() -> dict[str, Any]:
    """The `content` entry for a streamed 200: `text/event-stream` with `SSE_EVENT_SCHEMA`
    (a copy, so a route's spec never shares a mutable dict with another's)."""
    return {"text/event-stream": {"schema": copy.deepcopy(SSE_EVENT_SCHEMA)}}


def trace_id_in_use_message(trace_id: str) -> str:
    return (
        f"trace_id {trace_id} is in use by an in-flight run; "
        "send a fresh trace_id or wait for that run to end"
    )


def to_response(reply: Reply) -> JSONResponse:
    """A `Reply` as a JSON response."""
    return JSONResponse(reply.body, status_code=reply.status, headers=dict(reply.headers))


async def serve[BodyT](
    adapter: InboundAdapter[BodyT],
    body: BodyT,
    headers: Mapping[str, str],
    *,
    pipeline: RunPipeline,
    ids: ResolvedIds,
    errors_as_http: bool,
    options: Mapping[str, object] | None = None,
    ignored: Sequence[str] = (),
    disconnected: Disconnected | None = None,
) -> HTTPResponse:
    """Answer one call in `adapter`'s format (module docstring). `options` go to
    `ReplyMeta.options`; `ignored` names the accepted-and-ignored params the body set;
    `disconnected` tells a complete call that its client left (the route's `is_disconnected`).
    """
    meta = ReplyMeta(ids.ids, pipeline.served, int(time.time()), options=dict(options or {}))
    if not pipeline.ready:
        return to_response(adapter.error("not_ready", NOT_READY_MESSAGE, True, meta))
    try:
        request = adapter.to_request(body, headers, ids=ids.ids, served=meta.served)
        enforce_limits(adapter, request, pipeline.config.spec.limits, meta)
    except Refused as refused:
        return to_response(refused.reply)
    interface = adapter.interface
    claim: Claim | None = None
    idempotency = pipeline.idempotency
    if idempotency.applies(ids.key_from):
        outcome = await idempotency.begin(request, wait_ms=request.budget.timeout_ms)
        if isinstance(outcome, Refusal):
            return to_response(
                adapter.error(
                    outcome.code, outcome.message, outcome.retryable, meta.for_request(request)
                )
            )
        if isinstance(outcome, Replay):
            return _replay(adapter, outcome, meta, pipeline, stream=request.stream)
        claim = outcome
        if claim.timeout_ms is not None:  # it waited: only what is left of its own budget
            budget = request.budget.model_copy(update={"timeout_ms": claim.timeout_ms})
            request = request.model_copy(update={"budget": budget})
    try:
        run = open_run(pipeline, request, interface=interface, remint=may_remint(ids, interface))
    except TraceIdInUse as exc:
        if claim is not None:
            await claim.release()
        message = trace_id_in_use_message(exc.trace_id)
        return to_response(
            adapter.error("trace_id_in_use", message, False, meta.for_request(request))
        )
    except BaseException:
        if claim is not None:
            await claim.release()
        raise
    meta = meta.for_request(run.request)
    ending = _Ending(pipeline, run, claim)
    try:
        return await _answer(
            adapter,
            run,
            meta,
            pipeline,
            ending,
            errors_as_http=errors_as_http,
            ignored=ignored,
            disconnected=disconnected,
        )
    except Exception as exc:  # never Starlette's plain 500: an SDK would run the agent again
        run.close()
        _log_internal(pipeline, run, exc)
        await ending.failed()
        message = public_message(INTERNAL_ERROR)
        return to_response(adapter.error(INTERNAL_ERROR, message, False, meta))
    except BaseException:
        run.close()
        await asyncio.shield(ending.failed())
        raise


class _Ending:
    """What happens once when a run is over: the claim (finish when the run is done, else release)
    and then `pipeline.finished(run)`, unless the claim was lost: then no `on_finished` callback
    runs, so a fenced run publishes no result event. Only the first `over` or `failed` acts."""

    def __init__(self, pipeline: RunPipeline, run: Run, claim: Claim | None) -> None:
        self._pipeline = pipeline
        self._run = run
        self._claim = claim
        self._ended = False

    @property
    def claim(self) -> Claim | None:
        return self._claim

    @property
    def lost(self) -> bool:
        """The run's claim was lost (fenced or taken over): its result must not reach anyone."""
        return self._claim is not None and self._claim.lost

    async def over(self) -> None:
        if self._ended:
            return
        self._ended = True
        run, claim = self._run, self._claim
        if claim is not None:
            if run.done:
                try:
                    response = await run.response()
                except Exception:
                    await claim.release()
                else:
                    await claim.finish(run.seen, response)
            else:
                await claim.release()
            if claim.lost:
                self._pipeline.ports.telemetry.counter(CLAIM_LOST, interface=run.interface)
                return
        await self._pipeline.finished(run)

    async def failed(self) -> None:
        """The chassis failed: the client got a 500, so the result is never cached."""
        if self._ended:
            return
        if self._claim is not None:
            await self._claim.release()
        await self.over()


def _replay[BodyT](
    adapter: InboundAdapter[BodyT],
    replay: Replay,
    meta: ReplyMeta,
    pipeline: RunPipeline,
    *,
    stream: bool,
) -> HTTPResponse:
    """The stored run, answered in this call's format and mode, with the original ids and
    versions. No run is opened, the engine is not called, and nothing is charged."""
    interface = adapter.interface
    telemetry = pipeline.ports.telemetry
    served = replace(meta.served, versions=replay.response.versions)
    meta = replace(meta, served=served).for_request(replay.request)
    mode = "stream" if stream else "complete"
    with telemetry.span(REPLAY_SPAN, interface=interface, mode=mode):
        telemetry.counter(REPLAYED, interface=interface)
        if not stream:
            reply = adapter.complete(replay.response, meta)
            headers = {**dict(reply.headers), REPLAYED_HEADER: "true"}
            return JSONResponse(reply.body, status_code=reply.status, headers=headers)
        events = list(replay.events)

        async def stored() -> AsyncIterator[Event]:
            for event in events:
                yield event

        streamed = adapter.stream(stored(), meta)
        headers = {**dict(streamed.headers), REPLAYED_HEADER: "true"}
        return StreamingResponse(streamed.frames, media_type=streamed.media_type, headers=headers)


async def _answer[BodyT](
    adapter: InboundAdapter[BodyT],
    run: Run,
    meta: ReplyMeta,
    pipeline: RunPipeline,
    ending: _Ending,
    *,
    errors_as_http: bool,
    ignored: Sequence[str],
    disconnected: Disconnected | None,
) -> HTTPResponse:
    """Steps 5 to 7 of the module docstring, for an opened run."""
    for param in ignored:
        pipeline.ports.telemetry.counter(IGNORED, interface=adapter.interface, param=param)
    if not run.request.stream:
        reply = await _complete(
            adapter,
            run,
            meta,
            ending.claim,
            errors_as_http=errors_as_http,
            disconnected=disconnected,
        )
        if reply is None:
            pipeline.ports.telemetry.counter(CLIENT_DISCONNECTED, interface=adapter.interface)
            await ending.over()
            return HTTPResponse(status_code=CLIENT_GONE_STATUS)
        answer = to_response(reply)  # an adapter that raises here is an internal error
        await ending.over()
        if ending.lost:  # lost after the engine ended: the result is not this call's to give
            return to_response(_lost(adapter, meta))
        return answer
    if not errors_as_http:
        events = _fenced(run.events(), ending.claim)
        return await _stream(run, events, adapter, meta, events.aclose, ending)
    try:
        held = await hold(_fenced(run.events(), ending.claim))
    except BaseException:
        run.close()
        raise
    if held.error is not None:
        try:
            await held.aclose()
        finally:
            run.close()
        await ending.over()
        return to_response(_run_error(adapter, held.error, meta))
    return await _stream(run, held.events(), adapter, meta, held.aclose, ending)


def _run_error[BodyT](adapter: InboundAdapter[BodyT], error: Error, meta: ReplyMeta) -> Reply:
    """A run `error` answered as HTTP: its code and retryability, and the fixed text for the
    code (`public_message`); the run's own message stays in the log and the span."""
    return adapter.error(error.code, public_message(error.code), error.retryable, meta)


def _log_internal(pipeline: RunPipeline, run: Run, exc: Exception) -> None:
    """Log what the client is not told. A failing telemetry port must not lose the answer."""
    with suppress(Exception):
        pipeline.ports.telemetry.log(
            "error",
            "internal error while answering a run",
            request_id=run.request.request_id,
            trace_id=run.request.trace_id,
            interface=run.interface,
            detail=f"{type(exc).__name__}: {exc}",
        )


def _lost[BodyT](adapter: InboundAdapter[BodyT], meta: ReplyMeta) -> Reply:
    """The answer of a run whose claim was lost: 409 `idempotency_in_progress`, retryable."""
    return adapter.error(CLAIM_LOST_CODE, public_message(CLAIM_LOST_CODE), True, meta)


def _lost_event() -> Error:
    return Error(code=CLAIM_LOST_CODE, message=public_message(CLAIM_LOST_CODE), retryable=True)


async def _fenced(events: AsyncGenerator[Event], claim: Claim | None) -> AsyncGenerator[Event]:
    """The run's events, cancelled when `claim` is lost. The loss cancels the task that is
    waiting for the next event (the same cancel a client disconnect causes), so the engine's
    stream ends at once; then one `idempotency_in_progress` `Error` (retryable) closes the
    stream. That event is not part of the run (`run.seen`), so the run is not `done`."""
    if claim is None:
        async with aclosing(events):
            async for event in events:
                yield event
        return
    async with aclosing(events):
        while not claim.lost:
            task = asyncio.current_task()
            fired = False

            def cancel(task: asyncio.Task[Any] | None = task) -> None:
                nonlocal fired
                fired = True
                if task is not None:
                    task.cancel()

            remove = claim.on_lost(cancel)
            try:
                event = await anext(events)
            except StopAsyncIteration:
                return
            except asyncio.CancelledError:
                if not fired:
                    raise
                if task is not None:
                    task.uncancel()
                break
            finally:
                remove()
            if fired:  # the engine swallowed the cancel; the claim is lost all the same
                if task is not None:
                    task.uncancel()
                break
            yield event
    yield _lost_event()


async def _drain(run: Run, claim: Claim | None = None) -> Error | None:
    """Read the whole run; the last `error` event, if any."""
    error: Error | None = None
    try:
        async with aclosing(_fenced(run.events(), claim)) as events:
            async for event in events:
                if isinstance(event, Error):
                    error = event
    finally:
        run.close()
    return error


async def _watch(reader: asyncio.Task[Error | None], disconnected: Disconnected) -> bool:
    """Wait for `reader`, checking every `DISCONNECT_POLL_S` that the client is still there. True
    when the client left first. The check runs in the request's own task (Starlette's
    `is_disconnected` needs that). A check that raises stops the watching: the run then ends by
    itself, bounded by `budget.timeout_ms`."""
    while True:
        done, _ = await asyncio.wait({reader}, timeout=DISCONNECT_POLL_S)
        if done:
            return False
        try:
            if await disconnected():
                return True
        except Exception:
            await asyncio.wait({reader})
            return False


async def _complete[BodyT](
    adapter: InboundAdapter[BodyT],
    run: Run,
    meta: ReplyMeta,
    claim: Claim | None = None,
    *,
    errors_as_http: bool,
    disconnected: Disconnected | None = None,
) -> Reply | None:
    """The whole run, then its reply. None when the client left first: the run was cancelled.
    A lost claim (`_fenced`) is answered `_lost`."""
    if disconnected is None:
        error = await _drain(run, claim)
    else:
        reader = asyncio.create_task(_drain(run, claim), name=f"run:{run.request.request_id}")
        try:
            gone = await _watch(reader, disconnected)
        finally:
            if not reader.done():
                reader.cancel()
                with suppress(asyncio.CancelledError):
                    await reader
            run.close()
        if gone:
            return None
        error = reader.result()
    if claim is not None and claim.lost:
        return _lost(adapter, meta)
    if errors_as_http and error is not None:
        return _run_error(adapter, error, meta)
    return adapter.complete(await run.response(), meta)


async def _stream[BodyT](
    run: Run,
    events: AsyncGenerator[Event],
    adapter: InboundAdapter[BodyT],
    meta: ReplyMeta,
    close: Callable[[], Awaitable[None]],
    ending: _Ending,
) -> RunStream:
    """The `RunStream` over `adapter.stream`. When `adapter.stream` raises, the run's events and
    the run are closed before the exception goes on (to `serve`'s `internal_error`). When the
    response ends, the events are closed and then the run is over (`ending`)."""
    try:
        reply = adapter.stream(events, meta)
    except BaseException:
        try:
            await close()
        finally:
            run.close()
        raise

    async def on_close() -> None:
        try:
            await close()
        finally:
            await ending.over()

    return RunStream(
        run,
        _closing(reply, close),
        media_type=reply.media_type,
        headers=dict(reply.headers) or None,
        on_close=on_close,
    )


async def _closing(reply: StreamReply, close: Callable[[], Awaitable[None]]) -> AsyncIterator[str]:
    """The adapter's frames; when they end or the client leaves, the frames and then the run's
    events are closed, so the `chassis.run` span ends at once.
    """
    try:
        async for frame in reply.frames:
            yield frame
    finally:
        aclose = getattr(reply.frames, "aclose", None)
        try:
            if aclose is not None:
                await aclose()
        finally:
            await close()
