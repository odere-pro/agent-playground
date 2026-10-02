"""Self-test of the interface suite: it passes over a sound chassis, and it fails over one whose
OpenAI interface answers other text than the rest.

The chassis here serves the chassis's own `echo_wire` in the `inprocess` lane, on a Unix socket
under uvicorn (no TCP; `make test` allows Unix sockets). No model is called. The workload binding
over the four engines and both lanes is `pocs/poc-03-one-interface-every-client/tests/
test_interface_contract.py`. The suite's cases are called directly, with a pool this file builds,
so a failure is an `AssertionError` this test can expect.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from chassis.adapters.a2a import InProcessConnector
from chassis.adapters.openai_compat.inbound import OpenAIInbound
from chassis.core.envelope import Response
from chassis.core.inbound import Reply, ReplyMeta
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.interfaces.openai import openai_router
from chassis_contracts.interface import (
    LOGICAL,
    Chassis,
    ChassisPool,
    InterfaceContract,
    UnixApp,
    call,
    serve_on_unix_sockets,
)

ECHO = "chassis.core.handle:echo_wire"
CHANGED = " (changed)"


class ChangedText(OpenAIInbound):
    """The OpenAI interface with one fault: its complete answer has other text."""

    def complete(self, response: Response, meta: ReplyMeta) -> Reply:
        reply = super().complete(response, meta)
        body: dict[str, Any] = {**reply.body, "choices": [dict(c) for c in reply.body["choices"]]}
        message = dict(body["choices"][0]["message"])
        message["content"] = f"{message['content']}{CHANGED}"
        body["choices"][0]["message"] = message
        return Reply(reply.status, reply.headers, body)


def _config(*, openai: bool) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-1",
            "profile": "fake",
            "agent": {"name": "echo", "version": "0.0.1"},
            "spec": {
                "engine": {"connector": "inprocess", "handle": ECHO},
                "model": {"route": "fake-route"},
                "prompt": {"version": "p1"},
                "interfaces": {"openai": openai},
            },
        }
    )


@contextmanager
def _echo_chassis(*, broken: bool) -> Iterator[Chassis]:
    """`echo_wire` in-process, the public app on a Unix socket. `broken`: the OpenAI interface is
    `ChangedText`, mounted by hand in place of the configured one."""
    config = _config(openai=not broken)
    telemetry = InMemoryTelemetry()
    ports = PortBundle(
        model=ScriptedModel(),
        engine=InProcessConnector(),
        config=InMemoryConfig(),
        telemetry=telemetry,
    )
    app = create_app(config, ports)
    if broken:
        app.include_router(openai_router(app.state.pipeline, ChangedText()))
    folder = tempfile.mkdtemp(prefix="ifsuite-")
    try:
        with serve_on_unix_sockets([UnixApp(app, os.path.join(folder, "public.sock"))]):
            yield Chassis(os.path.join(folder, "public.sock"), telemetry, config)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


class _Suite(InterfaceContract):
    engine_labels = ("echo",)
    lane_labels = ("inprocess", "inprocess-second")
    inprocess_engines = ("echo",)


@pytest.fixture(scope="module")
def sound() -> Iterator[ChassisPool]:
    pool = ChassisPool(lambda target, lane: _echo_chassis(broken=False))
    yield pool
    pool.close()


@pytest.fixture(scope="module")
def broken() -> Iterator[ChassisPool]:
    pool = ChassisPool(lambda target, lane: _echo_chassis(broken=True))
    yield pool
    pool.close()


async def test_the_suite_passes_over_a_sound_chassis(sound: ChassisPool) -> None:
    suite = _Suite()
    await suite.test_every_interface_gives_the_same_answer(sound, LOGICAL, "echo", "inprocess")
    for interface in ("native", "openai", "anthropic"):
        await suite.test_stream_and_complete_agree(sound, LOGICAL, interface, "echo", "inprocess")
    for interface in ("native", "openai", "anthropic", "mcp"):
        await suite.test_every_cell_answers(
            sound, LOGICAL, interface, "echo", "complete", "inprocess"
        )


async def test_the_suite_fails_when_one_interface_answers_other_text(broken: ChassisPool) -> None:
    """The fault is real (the OpenAI client reads the changed text), and the two cases that
    compare answers both catch it; the other interfaces still agree with each other."""
    suite = _Suite()
    chassis = broken.get("echo", "inprocess")
    native = await call(chassis, "native", LOGICAL, stream=False)
    changed = await call(chassis, "openai", LOGICAL, stream=False)
    assert changed.text == native.text + CHANGED

    with pytest.raises(AssertionError, match="the interfaces disagree on the text"):
        await suite.test_every_interface_gives_the_same_answer(broken, LOGICAL, "echo", "inprocess")
    with pytest.raises(AssertionError):
        await suite.test_stream_and_complete_agree(broken, LOGICAL, "openai", "echo", "inprocess")
    await suite.test_stream_and_complete_agree(broken, LOGICAL, "anthropic", "echo", "inprocess")


def test_skips_are_stated_never_silent() -> None:
    """MCP x stream and an engine outside `inprocess_engines` in `inprocess` skip with a reason."""
    suite = _Suite()
    with pytest.raises(pytest.skip.Exception, match="MCP answers once"):
        suite._skip_cell(engine="echo", lane="inprocess", interface="mcp", mode="stream")
    with pytest.raises(pytest.skip.Exception, match="imports a Python handle by path"):
        suite._skip_cell(engine="node", lane="inprocess")
    suite._skip_cell(engine="echo", lane="inprocess", interface="mcp", mode="complete")
    suite._skip_cell(engine="node", lane="sidecar", interface="openai", mode="stream")
