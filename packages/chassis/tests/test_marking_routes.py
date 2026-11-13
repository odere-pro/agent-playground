"""023 C-5: the marking contract on every endpoint, driven by the routes the public app exposes.

`test_marking.py` checks a fixed list of calls. This test reads `app.routes` instead, so a route
added later is checked too. Every route not in `NO_OUTPUT` is a run route (today native
`/v1/run`, the OpenAI and Anthropic interfaces on the public port, and the agent as a tool on
`/v1/mcp`). A run route must carry `X-AI-Generated: true` and `X-AI-Disclosure-Required: true`,
complete and streamed. Every other route (`/health`, `/ready`, `/manifest`, `/metrics`, the
OpenAPI docs) must carry neither flag. A run route with no call here fails the test, so it
cannot go unchecked.

Run in the `inprocess` lane and in the `sidecar` lane, where the workload answers over A2A on a
Unix socket. Through `httpx.ASGITransport`; no TCP, no keys.
"""

from __future__ import annotations

from typing import Any

import pytest
from a2a_uds import SIDECAR_URL, serve_uds
from chassis.adapters.a2a import InProcessConnector
from chassis.adapters.a2a.server import build_agent_card, build_app
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.core.handle import echo_wire
from chassis.server.interfaces.mcp import MCP_PATH
from fastapi import FastAPI
from starlette.routing import BaseRoute
from test_marking import CALLS, MARKED, _app, _config, _flags, _mcp_responses, _serving

LIMITED = {"risk_class": "limited"}
RUN_BODIES: dict[str, dict[str, Any]] = dict(CALLS)
UNMARKED = (None, None)
NO_OUTPUT = frozenset(
    {"/health", "/ready", "/manifest", "/metrics", "/openapi.json", "/docs", "/redoc"}
    | {"/docs/oauth2-redirect"}
)
"""The routes that carry no agent output. Every other route is a run route and must be marked;
the list is kept here, not read from `chassis.server.marking`, so the test does not trust it."""


def _is_run(path: str) -> bool:
    return path not in NO_OUTPUT


def _paths(app: FastAPI) -> list[tuple[str, frozenset[str]]]:
    """Every (path, methods) the app routes; a mount such as `/v1/mcp` has no methods."""
    out = []
    for route in app.routes:
        assert isinstance(route, BaseRoute)
        path = getattr(route, "path", None)
        assert isinstance(path, str), route
        out.append((path, frozenset(getattr(route, "methods", None) or ())))
    return out


async def _route_flags(app: FastAPI, path: str, methods: frozenset[str]) -> list[Any]:
    """The flags of every response one route gives, complete and streamed for a run route."""
    if path == MCP_PATH:
        return [_flags(r) for r in await _mcp_responses(app)]
    if _is_run(path):
        assert path in RUN_BODIES, f"run route {path} has no marking call in this test"
        out = []
        async with _serving(app) as client:
            for stream in (False, True):
                res = await client.post(path, json={**RUN_BODIES[path], "stream": stream})
                assert res.status_code == 200, (path, stream, res.text)
                assert ("text/event-stream" in res.headers["content-type"]) is stream
                out.append(_flags(res))
        return out
    method = "GET" if "GET" in methods else sorted(methods)[0]
    async with _serving(app) as client:
        res = await client.request(method, path)
    assert res.status_code < 500, (path, res.status_code, res.text)
    return [_flags(res)]


async def _check_every_route(app: FastAPI) -> None:
    paths = _paths(app)
    run = {p for p, _ in paths if _is_run(p)}
    assert run >= {*RUN_BODIES, MCP_PATH}, run  # all four run interfaces are mounted
    assert {"/health", "/ready", "/manifest"} <= {p for p, _ in paths if not _is_run(p)}
    for path, methods in paths:
        flags = await _route_flags(app, path, methods)
        want = MARKED if _is_run(path) else UNMARKED
        assert flags == [want] * len(flags), (path, flags)


async def test_every_route_is_marked_when_it_runs_and_unmarked_otherwise_inprocess() -> None:
    """inprocess lane: run routes carry both flags, the other routes carry none."""
    config = _config(LIMITED, connector="inprocess", handle="chassis.core.handle:echo_wire")
    await _check_every_route(_app(None, InProcessConnector(), config))


async def test_every_route_is_marked_when_it_runs_and_unmarked_otherwise_over_a2a() -> None:
    """sidecar lane, the workload over A2A: the same contract on every route."""
    card = build_agent_card(name="echo", version="0.0.1", url=SIDECAR_URL)
    async with serve_uds(build_app(echo_wire, card)) as uds:
        config = _config(LIMITED, connector="sidecar", url=SIDECAR_URL, uds=uds)
        await _check_every_route(_app(None, SidecarConnector(), config))


@pytest.mark.parametrize("governance", [None, {"risk_class": "minimal"}])
async def test_no_route_is_marked_when_marking_is_off(governance: dict[str, Any] | None) -> None:
    """With marking off, no route carries either flag, run routes included."""
    app = _app(governance)
    for path, methods in _paths(app):
        flags = await _route_flags(app, path, methods)
        assert flags == [UNMARKED] * len(flags), (path, flags)
