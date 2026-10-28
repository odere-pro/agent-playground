"""The freeze (PoC-6 exit criterion 8; contract v5, section C and its review findings).

The first stable line of the chassis contract is pinned here. A change that fails one of these
tests needs a new major, a new contract, or a revert. A new error code, a new optional `Context`
field, or a new `spec.*` field is allowed and breaks nothing here.

What is pinned: the sha256 of the five schema files, `handle` and `wire` through `inspect`, the six
events with their fields, the schema versions, the four A2A metadata keys, and every frozen error
code with its `retryable` value (checked where it is emitted, by running the emitter; a code that
is added is fine).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import typing
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.types import SendMessageRequest, StreamResponse, TaskState
from chassis.adapters.a2a import mapping
from chassis.adapters.a2a.inprocess import InProcessConnector
from chassis.adapters.a2a.mapping import request_to_message, update_to_event
from chassis.core import events as chassis_events
from chassis.core import handle as handle_module
from chassis.core.envelope import Budget, Context, Request, TaskInput
from chassis.core.events import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    Error,
    Event,
    parse_event,
)
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.schemas import SCHEMA_DIR
from chassis_contracts.helpers import make_context, make_request

FROZEN = (
    "contract v1 is frozen: a change here needs a new major (contract v5, section C). "
    "Revert it, or write the bump and the new contract."
)

# sha256 of the schema files at the freeze (contract v5, C.4 lists the first 16 hex).
SCHEMA_SHA256 = {
    # pragma: allowlist nextline secret (a schema sha256, not a secret)
    "events.v0.json": "60de41563a1d0582336a67ffac6a2f1891036b690e8c20373194d0bb05108521",
    # pragma: allowlist nextline secret (a schema sha256, not a secret)
    "task_input.v0.json": "6d8aa949b462c77e637de4ea61de13aefca7819248c7ee607c184b2465b68d89",
    # pragma: allowlist nextline secret (a schema sha256, not a secret)
    "context.v0.json": "d2db0e94a50379f1e01b3441e1c6d0f5afbfe599a72b8905086bb5f447db99d6",
    # pragma: allowlist nextline secret (a schema sha256, not a secret)
    "request.v0.json": "4ff8a4324a90e8c035f7186e5d242f5ef25e80d100a6bdb8bb141bc978894ce5",
    # pragma: allowlist nextline secret (a schema sha256, not a secret)
    "response.v0.json": "63acf4f16dae59f952a92d9057dc85d7524148dc9dd842f06d476c9c87167aca",
}


def _read_schema(name: str) -> bytes:
    return (SCHEMA_DIR / name).read_bytes()


@pytest.mark.parametrize("name", sorted(SCHEMA_SHA256))
def test_frozen_schema_file_is_byte_identical(name: str) -> None:
    digest = hashlib.sha256(_read_schema(name)).hexdigest()
    assert digest == SCHEMA_SHA256[name], f"{name}: {FROZEN}"


# --- handle ---


def test_handle_alias_is_typed_input_and_context_to_events() -> None:
    params, returns = typing.get_args(handle_module.Handle)
    assert params == [TaskInput, Context], FROZEN
    assert typing.get_origin(returns) is not None
    assert typing.get_args(returns) == (Event,), FROZEN


def test_wire_signature_is_dicts_in_events_out() -> None:
    hints = typing.get_type_hints(handle_module.wire, include_extras=True)
    assert list(inspect.signature(handle_module.wire).parameters) == ["handle"], FROZEN
    assert hints["handle"] == handle_module.Handle, FROZEN
    wired = handle_module.wire(handle_module.echo)
    assert inspect.isasyncgenfunction(wired), FROZEN
    sig = inspect.signature(wired)
    assert list(sig.parameters) == ["input", "ctx"], FROZEN
    assert all(p.kind is p.POSITIONAL_OR_KEYWORD for p in sig.parameters.values()), FROZEN
    wired_hints = typing.get_type_hints(wired, include_extras=True)
    assert wired_hints["input"] == dict[str, Any], FROZEN
    assert wired_hints["ctx"] == dict[str, Any], FROZEN
    assert wired_hints["return"] == AsyncIterator[Event], FROZEN
    assert hints["return"] == Callable[[dict[str, Any], dict[str, Any]], AsyncIterator[Event]], (
        FROZEN
    )


async def test_wire_form_takes_dicts_and_streams_start_first_and_end_last() -> None:
    request = make_request("a b")
    ctx = make_context(request).model_dump(mode="json")
    out = [e async for e in handle_module.echo_wire({"text": "a b"}, ctx)]
    types = [e.type for e in out]
    assert types[0] == "start" and types[-1] == "end", FROZEN
    assert types.count("start") == 1 and types.count("end") == 1, FROZEN


def test_an_event_without_schema_version_is_filled_with_zero() -> None:
    assert parse_event({"type": "start", "request_id": "r"}).schema_version == "0", FROZEN


# --- the six events ---

EVENT_FIELDS: dict[str, dict[str, tuple[str, bool]]] = {
    # field -> (annotation, required)
    "start": {"request_id": ("str", True)},
    "delta": {"text": ("str", True)},
    "tool_call": {
        "call_id": ("str", True),
        "name": ("str", True),
        "arguments": ("dict[str, typing.Any]", False),
        "result": ("dict[str, typing.Any] | None", False),
    },
    "metrics": {
        "input_tokens": ("int", False),
        "output_tokens": ("int", False),
        "cost_usd": ("float | None", False),
        "model_route": ("str | None", False),
        "latency_ms": ("int | None", False),
        "attempt": ("int", False),
    },
    "end": {
        "status": ("typing.Literal['ok', 'retry', 'fallback']", False),
        "output": ("dict[str, typing.Any] | None", False),
    },
    "error": {"code": ("str", True), "message": ("str", True), "retryable": ("bool", False)},
}
COMMON = {"type", "schema_version"}


def _fmt(annotation: Any) -> str:
    return annotation.__name__ if isinstance(annotation, type) else str(annotation)


def _event_models() -> dict[str, type[Any]]:
    union = typing.get_args(Event)[0]
    return {m.model_fields["type"].default: m for m in typing.get_args(union)}


def test_there_are_exactly_six_event_types() -> None:
    assert set(_event_models()) == set(EVENT_FIELDS), FROZEN


@pytest.mark.parametrize("kind", sorted(EVENT_FIELDS))
def test_event_fields_are_pinned(kind: str) -> None:
    model = _event_models()[kind]
    fields = {
        name: (_fmt(info.annotation), info.is_required())
        for name, info in model.model_fields.items()
        if name not in COMMON
    }
    assert fields == EVENT_FIELDS[kind], f"{kind}: {FROZEN}"
    assert model.model_config.get("extra") == "forbid", f"{kind}: {FROZEN}"
    assert model.model_fields["schema_version"].default == "0", FROZEN


def test_an_unknown_event_field_is_refused() -> None:
    with pytest.raises(ValueError):
        parse_event({"type": "delta", "text": "x", "extra": 1})


def test_schema_versions() -> None:
    assert SCHEMA_VERSION == "0", FROZEN
    assert "0" in SUPPORTED_SCHEMA_VERSIONS, FROZEN
    assert chassis_events.SUPPORTED_SCHEMA_VERSIONS == ("0",), FROZEN
    assert mapping.SCHEMA_VERSION == "0", FROZEN


def test_the_four_metadata_keys() -> None:
    assert (
        mapping.EVENT_KEY,
        mapping.CTX_KEY,
        mapping.INPUT_KEY,
        mapping.SCHEMA_VERSION_KEY,
    ) == ("chassis.event", "chassis.ctx", "chassis.input", "chassis.schema_version"), FROZEN


# --- the frozen error codes: still emitted, same `retryable`. A new code is allowed. ---

FROZEN_CODES: dict[str, bool] = {
    "a2a.unsupported_schema_version": False,
    "a2a.bad_request": False,
    "workload.bad_event": False,
    "workload.bad_order": False,
    "workload.no_end": False,
    "workload.exception": False,
    "a2a.bad_event": False,
    "a2a.request_mismatch": False,
    "a2a.timeout": True,
    "a2a.transport": True,
    "a2a.canceled": False,
    "a2a.failed": False,
    "a2a.unsupported_state": False,
    "engine_error": False,
}


def _start(ctx: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}


async def _bad_event(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield _start(ctx)
    yield {"schema_version": "0", "type": "delta", "text": "x", "extra": 1}


async def _late_start(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "delta", "text": "no start"}


async def _raising(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield _start(ctx)
    raise RuntimeError("workload fell over")


async def _no_end(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield _start(ctx)


async def _foreign_start(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "start", "request_id": "someone-else"}
    yield {"schema_version": "0", "type": "end", "status": "ok"}


async def _slow(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield _start(ctx)
    await asyncio.sleep(30)
    yield {"schema_version": "0", "type": "end", "status": "ok"}


async def _via_inprocess(handle: str, *, timeout_ms: int | None = None) -> list[Any]:
    connector = InProcessConnector()
    bundle = PortBundle(
        model=ScriptedModel(),
        engine=connector,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    await connector.setup({"connector": "inprocess", "handle": f"{__name__}:{handle}"}, bundle)
    request: Request = make_request()
    if timeout_ms is not None:
        request = request.model_copy(update={"budget": Budget(timeout_ms=timeout_ms)})
    try:
        return [e async for e in connector.run(request, make_context(request))]
    finally:
        await connector.close()


def _last_error(events: list[Any]) -> Error:
    assert isinstance(events[-1], Error), f"no error emitted: {events!r}"
    return events[-1]


@pytest.mark.parametrize(
    ("handle", "code"),
    [
        ("_bad_event", "workload.bad_event"),
        ("_late_start", "workload.bad_order"),
        ("_raising", "workload.exception"),
        ("_no_end", "workload.no_end"),
        ("_foreign_start", "a2a.request_mismatch"),
    ],
)
async def test_server_and_connector_codes_keep_retryable(handle: str, code: str) -> None:
    error = _last_error(await _via_inprocess(handle))
    assert error.code == code, f"{code} is no longer emitted: {FROZEN}"
    assert error.retryable is FROZEN_CODES[code], f"{code}: {FROZEN}"


async def test_a2a_timeout_is_still_retryable() -> None:
    error = _last_error(await _via_inprocess("_slow", timeout_ms=100))
    assert error.code == "a2a.timeout" and error.retryable is FROZEN_CODES[error.code], FROZEN


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://inprocess")


async def _send(app: Any, req: SendMessageRequest) -> list[StreamResponse]:
    async with _client(app) as http:
        client = await ClientFactory(
            ClientConfig(httpx_client=http, streaming=True)
        ).create_from_url("http://inprocess")
        try:
            return [r async for r in client.send_message(req)]
        finally:
            await client.close()


def _server_module(name: str) -> Any:
    if name == "chassis":
        from chassis.adapters.a2a import server as chassis_server

        return chassis_server
    from workload_a2a import server as workload_server

    return workload_server


async def _echo(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield _start(ctx)
    yield {"schema_version": "0", "type": "end", "status": "ok"}


@pytest.mark.parametrize("server_name", ["chassis", "workload_a2a"])
async def test_both_servers_refuse_an_unknown_schema_version(server_name: str) -> None:
    module = _server_module(server_name)
    app = module.build_app(_echo, module.build_agent_card(name="freeze", version="1"))
    ctx = make_context(make_request()).model_dump(mode="json")
    sent = await _send(app, request_to_message({"text": "hi"}, ctx, schema_version="9"))
    raws = [r for r in (update_to_event(s) for s in sent) if r is not None]
    event = parse_event(raws[-1])
    assert isinstance(event, Error), FROZEN
    assert event.code == "a2a.unsupported_schema_version", FROZEN
    assert event.retryable is FROZEN_CODES[event.code], FROZEN


@pytest.mark.parametrize("server_name", ["chassis", "workload_a2a"])
async def test_both_servers_refuse_a_request_that_is_not_json(server_name: str) -> None:
    module = _server_module(server_name)
    app = module.build_app(_echo, module.build_agent_card(name="freeze", version="1"))
    req = request_to_message({"text": "hi"}, make_context(make_request()).model_dump(mode="json"))
    req.metadata[mapping.CTX_KEY] = "{not json"
    sent = await _send(app, req)
    raws = [r for r in (update_to_event(s) for s in sent) if r is not None]
    event = parse_event(raws[-1])
    assert isinstance(event, Error) and event.code == "a2a.bad_request", FROZEN
    assert event.retryable is FROZEN_CODES[event.code], FROZEN


@pytest.mark.parametrize("server_name", ["chassis", "workload_a2a"])
@pytest.mark.parametrize(
    ("handle", "code"),
    [
        (_bad_event, "workload.bad_event"),
        (_late_start, "workload.bad_order"),
        (_raising, "workload.exception"),
        (_no_end, "workload.no_end"),
    ],
)
async def test_the_workload_a2a_copy_emits_the_same_codes(
    server_name: str, handle: Any, code: str
) -> None:
    module = _server_module(server_name)
    app = module.build_app(handle, module.build_agent_card(name="freeze", version="1"))
    ctx = make_context(make_request()).model_dump(mode="json")
    sent = await _send(app, request_to_message({"text": "hi"}, ctx))
    raws = [r for r in (update_to_event(s) for s in sent) if r is not None]
    event = parse_event(raws[-1])
    assert isinstance(event, Error) and event.code == code, f"{code}: {FROZEN}"
    assert event.retryable is FROZEN_CODES[code], f"{code}: {FROZEN}"


def _state(state: int) -> StreamResponse:
    return StreamResponse(
        status_update={"task_id": "t", "context_id": "c", "status": {"state": state}}
    )


@pytest.mark.parametrize(
    ("state", "code"),
    [
        (TaskState.TASK_STATE_CANCELED, "a2a.canceled"),
        (TaskState.TASK_STATE_FAILED, "a2a.failed"),
        (TaskState.TASK_STATE_REJECTED, "a2a.failed"),
        (TaskState.TASK_STATE_INPUT_REQUIRED, "a2a.unsupported_state"),
        (TaskState.TASK_STATE_AUTH_REQUIRED, "a2a.unsupported_state"),
    ],
)
def test_state_only_codes_keep_retryable(state: int, code: str) -> None:
    raw = update_to_event(_state(state))
    assert raw is not None, f"{code} is no longer emitted: {FROZEN}"
    event = parse_event(raw)
    assert isinstance(event, Error) and event.code == code, FROZEN
    assert event.retryable is FROZEN_CODES[code], f"{code}: {FROZEN}"


async def test_a2a_transport_is_still_retryable_when_the_stream_ends_early() -> None:
    from chassis.adapters.a2a import connector as connector_module

    source = inspect.getsource(connector_module)
    assert 'code="a2a.transport"' in source, FROZEN
    # Behavior: a stream that ends without end or error is `a2a.transport`, retryable.
    stub_events = await _transport_failure()
    error = _last_error(stub_events)
    assert error.code == "a2a.transport" and error.retryable is FROZEN_CODES[error.code], FROZEN


class _EmptyStream:
    def __aiter__(self) -> _EmptyStream:
        return self

    async def __anext__(self) -> StreamResponse:
        raise StopAsyncIteration

    async def aclose(self) -> None:
        return None


class _EmptyClient:
    def send_message(self, message: Any, *, context: Any = None) -> _EmptyStream:
        return _EmptyStream()

    async def cancel_task(self, request: Any, *, context: Any = None) -> None:
        return None

    async def close(self) -> None:
        return None


async def _transport_failure() -> list[Any]:
    connector = InProcessConnector()
    bundle = PortBundle(
        model=ScriptedModel(),
        engine=connector,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    await connector.setup({"connector": "inprocess", "handle": f"{__name__}:_echo"}, bundle)
    real: Any = connector._client
    connector._client = _EmptyClient()  # type: ignore[assignment]
    request = make_request()
    try:
        return [e async for e in connector.run(request, make_context(request))]
    finally:
        connector._client = real
        await connector.close()


async def test_engine_error_is_still_the_pipeline_guard() -> None:
    from chassis.server.pipeline import _guarded

    async def boom() -> AsyncIterator[Any]:
        raise RuntimeError("engine fell over")
        yield

    events = [e async for e in _guarded(boom())]
    error = _last_error(events)
    assert error.code == "engine_error" and error.retryable is FROZEN_CODES[error.code], FROZEN
