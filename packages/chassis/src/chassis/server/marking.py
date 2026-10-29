"""AI-generated marking (PoC-7, backlog 023 C-5; AI Act Art. 50).

When `spec.governance.marking` is on (`risk_class` `limited` or `high`, or
`ai_generated_marking: true`), every response of every run interface carries two headers:
`X-AI-Generated: true` and `X-AI-Disclosure-Required: true` (suggested names). The run
interfaces are native `/v1/run`, the OpenAI interface (public port, `/v1/chat/completions`), the
Anthropic interface (public port, `/v1/messages`), and the agent as a tool (`/v1/mcp`). Streamed
and complete answers, and a refusal such as a 422 or a 413 on those paths, are marked alike.
`/health`, `/ready`, and `/manifest` are not: they carry no output.

`MarkOutputs` is pure ASGI middleware on the public app. It rewrites `http.response.start` on the
way out, after the workload's events came back and were mapped, so a workload cannot remove the
flags: any header of the same name the response already holds is dropped first. The config is read
per request from `state.config`, so the flags follow the config the chassis serves now. Nothing in
the envelope, the events, or the wire bodies changes (the contract-v5 surface is frozen); the
PoC-8 adds the rest of 023 C-5: `Access-Control-Expose-Headers` names the two flags on every
marked response; the body field is `chassis.adapters.ai_marking` (OpenAI and Anthropic); the flag
on result events is the CloudEvent extension `aigenerated` (`chassis.server.results`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.datastructures import State

__all__ = [
    "AI_GENERATED_HEADER",
    "DISCLOSURE_HEADER",
    "RUN_PATHS",
    "MarkOutputs",
    "is_run_path",
    "marking_on",
]

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

AI_GENERATED_HEADER = "x-ai-generated"
"""suggested: the output is AI-generated."""
DISCLOSURE_HEADER = "x-ai-disclosure-required"
"""suggested: the client app must tell the user they are dealing with AI."""
RUN_PATHS: tuple[str, ...] = ("/v1/run", "/v1/chat/completions", "/v1/messages", "/v1/mcp")
"""The public run routes. `/v1/mcp` is matched with what follows it too."""

_NAMES = frozenset({AI_GENERATED_HEADER.encode(), DISCLOSURE_HEADER.encode()})
_FLAGS = [(AI_GENERATED_HEADER.encode(), b"true"), (DISCLOSURE_HEADER.encode(), b"true")]
EXPOSE_HEADER = b"access-control-expose-headers"
"""PoC-8: names the two flags, so a browser client of a CORS-enabled ingress may read them."""
_EXPOSED = ("X-AI-Generated", "X-AI-Disclosure-Required")


def _expose(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """`headers` with the flags added to `Access-Control-Expose-Headers` (merged, one header)."""
    names: list[str] = []
    rest = []
    for key, value in headers:
        if bytes(key).lower() == EXPOSE_HEADER:
            names.extend(n.strip() for n in bytes(value).decode("latin-1").split(",") if n.strip())
        else:
            rest.append((key, value))
    lower = {n.lower() for n in names}
    names.extend(n for n in _EXPOSED if n.lower() not in lower)
    return [*rest, (EXPOSE_HEADER, ", ".join(names).encode("latin-1"))]


def is_run_path(path: str) -> bool:
    """`path` is one of the run routes (`RUN_PATHS`), with or without a trailing segment."""
    path = path.rstrip("/") or "/"
    return any(path == p or path.startswith(p + "/") for p in RUN_PATHS)


def marking_on(state: State) -> bool:
    """`spec.governance.marking` of the config the app serves now."""
    config = getattr(state, "config", None)
    return bool(config is not None and config.spec.governance.marking)


class MarkOutputs:
    """Pure ASGI: sets the two flags on every response of a run route when marking is on."""

    def __init__(self, app: ASGIApp, state: State) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or not is_run_path(str(scope.get("path", "")))
            or not marking_on(self.state)
        ):
            await self.app(scope, receive, send)
            return

        async def marked(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for k, v in message.get("headers", ()) if bytes(k).lower() not in _NAMES
                ]
                message = {**message, "headers": _expose([*headers, *_FLAGS])}
            await send(message)

        await self.app(scope, receive, marked)
