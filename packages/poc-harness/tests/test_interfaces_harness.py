"""`poc_harness.interfaces`: the lane rules of `chassis_on_unix_sockets` and the router's refusal,
plus the two shared helpers PoC-3's exit criteria moved onto (`on_unix_socket`, `sse_data`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from chassis.fakes import ScriptedModel
from fastapi import FastAPI
from poc_harness.interfaces import TYPESCRIPT, OutboundRouter, chassis_on_unix_sockets
from poc_harness.sockets import on_unix_socket
from poc_harness.sse import sse_data

OPENAI_STREAM = 'data: {"id": 1}\n\ndata: [DONE]\n\n'
ANTHROPIC_STREAM = 'event: ping\ndata: {"type": "ping"}\n\n: keep-alive\n\n'


def test_the_typescript_echo_is_refused_in_the_inprocess_lane() -> None:
    """R0-16: the error names the sidecar-only rule."""
    with (
        pytest.raises(ValueError, match="runs in the sidecar lane only"),
        chassis_on_unix_sockets(
            TYPESCRIPT, "inprocess", model=ScriptedModel(), router=OutboundRouter()
        ),
    ):
        pass


def test_an_unknown_lane_is_refused() -> None:
    with (
        pytest.raises(ValueError, match="unknown lane 'remote'"),
        chassis_on_unix_sockets(
            "echo_python", "remote", model=ScriptedModel(), router=OutboundRouter()
        ),
    ):
        pass


async def test_a_request_that_names_no_run_is_a_connect_error_and_is_kept() -> None:
    router = OutboundRouter()
    with pytest.raises(httpx.ConnectError, match="no chassis holds the run"):
        await router.send(httpx.Request("POST", "http://model.invalid/v1/chat/completions"))
    assert len(router.unrouted) == 1


def test_sse_data_decodes_json_and_keeps_done_as_a_string() -> None:
    assert sse_data(OPENAI_STREAM) == [{"id": 1}, "[DONE]"]


def test_sse_data_reads_named_frames_and_drops_comments() -> None:
    assert sse_data(ANTHROPIC_STREAM) == [{"type": "ping"}]


def test_sse_data_refuses_a_named_frame_when_told_every_frame_is_unnamed() -> None:
    assert sse_data(OPENAI_STREAM, named=False) == [{"id": 1}, "[DONE]"]
    with pytest.raises(AssertionError, match="ping"):
        sse_data(ANTHROPIC_STREAM, named=False)


def test_a_failed_lifespan_fails_on_unix_socket_at_start() -> None:
    """`serve_on_uds` used `lifespan="on"`; this pins that the shared helper still fails loudly."""

    @asynccontextmanager
    async def broken(app: Any) -> AsyncIterator[None]:
        raise RuntimeError("no ports")
        yield

    with (
        pytest.raises(RuntimeError, match="did not start"),
        on_unix_socket(FastAPI(lifespan=broken)),
    ):
        pass
