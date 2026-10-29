"""Rate limits per caller on the public app (PoC-8, 022 H-6).

**The bucket.** One token bucket per agent and per caller, kept in `StatePort`, so every replica
that shares the store (Valkey in `local` and `cloud`; one `InMemoryState` in a test) shares the
limit, and a replica keeps nothing of it. The caller is the checked `Principal.subject`; the key is
`ratelimit:<agent>:<sha256(subject)[:32]>`, so the store never holds the IdP's subject. The value
is `<tokens>:<unix time>`. A call reads it, adds `requests_per_minute / 60` tokens per second since
then (up to `request_burst`), and takes one with `compare_and_set`; a key that is not there is a
full bucket, created with `set_if_absent`. A write lost to another replica is read again, up to
`CAS_ATTEMPTS` times, then the call is refused (only one caller's own calls race on one key). The
key expires once a full bucket would have refilled. No port method is added: `get`,
`set_if_absent`, and `compare_and_set` are the `StatePort` contract (PoC-4).

Replicas use their own wall clocks. A replica whose clock is behind reads no time passed (never
negative), so skew can only grant up to `skew * rate` extra tokens.

**Which calls count.** `POST /v1/run`, the OpenAI interface and the Anthropic interface (public
port), and `POST /v1/feedback`. Not `/v1/mcp`: its tool calls reach `/v1/run` in process with the
caller's own token, and count there once; a 429 there is an MCP tool error, MCP's own shape. Not
`/health`, `/ready`, `/manifest`, or the OpenAPI documents. Every keyed call counts too, so the
limit also caps how fast one caller adds idempotency keys (022 H-6, the PoC-4 status).

**On by default, for callers with an identity.** `spec.limits.rate_limit` is true. With auth off
(`NoAuth`, the anonymous principal that holds every scope) there is no caller to tell apart: one
shared bucket would turn a per-caller limit into a per-agent cap and break the PoC-4 load matrix,
so no limit applies; LiteLLM's key budget stays the cluster-wide cap. The defaults (600 a minute,
a burst of 100, suggested) are far above any test or interactive client, so turning it on breaks
no existing call.

**Past the limit.** 429 `rate_limited` with `Retry-After` (whole seconds until one token is back),
in the route's format (`security.refusal_body`), before the body is read or the run opens. Counted
as `chassis.rate_limited{path_class}` (suggested). A store outage follows
`spec.limits.rate_limit_on_unavailable`: `fail_open` (default) lets the call through, counts
`chassis.rate_limit_unavailable`, and logs a warning; `fail_closed` answers 503
`state_unavailable` with `Retry-After: 1`. The config is read per request, so a reload of
`spec.limits` applies to the next call.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from chassis.ports.auth import ANY_SCOPE
from chassis.ports.state import StatePort, StateUnavailable
from chassis.ports.telemetry import TelemetryPort
from chassis.server.config import ChassisConfig, LimitsSpec
from chassis.server.security import path_class, principal_of, refusal_body, send_json

__all__ = [
    "CAS_ATTEMPTS",
    "RATE_LIMITED",
    "RATE_LIMIT_UNAVAILABLE",
    "REFUSED_COUNTER",
    "Allowed",
    "RateLimit",
    "RateLimiter",
    "Refused",
    "bucket_key",
    "install_rate_limit",
]

RATE_LIMITED = "rate_limited"
"""suggested: the error code past the limit (429)."""
STATE_UNAVAILABLE = "state_unavailable"
REFUSED_COUNTER = "chassis.rate_limited"
RATE_LIMIT_UNAVAILABLE = "chassis.rate_limit_unavailable"
CAS_ATTEMPTS = 8
"""suggested: how often a lost write is read again before the call is refused."""
MESSAGES = {
    RATE_LIMITED: "too many requests from this caller; retry after the Retry-After delay",
    STATE_UNAVAILABLE: "the chassis cannot check the rate limit now; retry later",
}
MCP_PREFIX = "/v1/mcp"


@dataclass(frozen=True)
class Allowed:
    """The call may go on. `unavailable`: the store failed and `fail_open` let it through."""

    unavailable: bool = False


@dataclass(frozen=True)
class Refused:
    """The call is refused: `rate_limited` (429) or `state_unavailable` (503)."""

    retry_after_s: int
    code: str = RATE_LIMITED


def bucket_key(agent: str, subject: str) -> str:
    """The store key of one caller's bucket for one agent. The subject is hashed."""
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:32]
    return f"ratelimit:{agent}:{digest}"


def _encode(tokens: float, at: float) -> bytes:
    return f"{tokens:.6f}:{at:.6f}".encode()


def _decode(raw: bytes) -> tuple[float, float] | None:
    try:
        tokens, at = raw.decode("ascii").split(":", 1)
        return float(tokens), float(at)
    except (UnicodeDecodeError, ValueError):
        return None


class RateLimiter:
    """The bucket over `StatePort` (module docstring). `clock` is the wall clock in seconds."""

    def __init__(self, state: StatePort, *, clock: Callable[[], float] = time.time) -> None:
        self.state = state
        self.clock = clock

    async def take(self, agent: str, subject: str, limits: LimitsSpec) -> Allowed | Refused:
        """Take one token from the caller's bucket, or say why not."""
        try:
            return await self._take(bucket_key(agent, subject), limits)
        except StateUnavailable:
            if limits.rate_limit_on_unavailable == "fail_closed":
                return Refused(retry_after_s=1, code=STATE_UNAVAILABLE)
            return Allowed(unavailable=True)

    async def _take(self, key: str, limits: LimitsSpec) -> Allowed | Refused:
        rate = limits.requests_per_minute / 60.0
        burst = float(limits.request_burst)
        ttl_s = burst / rate + 1.0
        tokens = 0.0
        for _ in range(CAS_ATTEMPTS):
            now = self.clock()
            raw = await self.state.get(key)
            if raw is None:
                if await self.state.set_if_absent(key, _encode(burst - 1, now), ttl_s=ttl_s):
                    return Allowed()
                continue
            stored = _decode(raw)
            tokens, at = stored if stored is not None else (burst, now)
            tokens = min(burst, tokens + max(0.0, now - at) * rate)
            if tokens < 1:
                return Refused(retry_after_s=max(1, math.ceil((1 - tokens) / rate)))
            if await self.state.compare_and_set(key, raw, _encode(tokens - 1, now), ttl_s=ttl_s):
                return Allowed()
        return Refused(retry_after_s=max(1, math.ceil(max(0.0, 1 - tokens) / rate)))


def _counted(path: str) -> bool:
    kind = path_class(path)
    if kind == "feedback":
        return True
    return kind == "run" and not (path == MCP_PREFIX or path.startswith(MCP_PREFIX + "/"))


class RateLimit:
    """Pure ASGI, inside `CallerAuth`: the caller's bucket, before the body limit and the run."""

    def __init__(self, app: ASGIApp, *, public: FastAPI) -> None:
        self.app = app
        self.public = public

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _counted(str(scope.get("path", ""))):
            await self.app(scope, receive, send)
            return
        config: ChassisConfig | None = getattr(self.public.state, "config", None)
        ports = getattr(self.public.state, "ports", None)
        principal = principal_of(scope)
        if (
            config is None
            or ports is None
            or not config.spec.limits.rate_limit
            or principal is None
            or ANY_SCOPE in principal.scopes  # auth off: no caller to count
        ):
            await self.app(scope, receive, send)
            return
        limits = config.spec.limits
        outcome = await RateLimiter(ports.state).take(config.agent.name, principal.subject, limits)
        if isinstance(outcome, Allowed):
            if outcome.unavailable:
                ports.telemetry.counter(RATE_LIMIT_UNAVAILABLE)
                ports.telemetry.log("warning", "rate limit store unavailable; passed (fail_open)")
            await self.app(scope, receive, send)
            return
        await self._refuse(send, scope["path"], outcome, ports.telemetry)

    async def _refuse(
        self, send: Send, path: str, refused: Refused, telemetry: TelemetryPort
    ) -> None:
        status = 429 if refused.code == RATE_LIMITED else 503
        if refused.code == RATE_LIMITED:
            telemetry.counter(REFUSED_COUNTER, path_class=path_class(path))
        else:
            telemetry.counter(RATE_LIMIT_UNAVAILABLE)
        body = refusal_body(path, refused.code, MESSAGES[refused.code], status)
        await send_json(send, status, body, {"Retry-After": str(refused.retry_after_s)})


def install_rate_limit(app: FastAPI) -> None:
    """Add `RateLimit` to the public app. Call before `install_security`, so it sits inside
    `CallerAuth` and sees the checked principal."""
    app.add_middleware(RateLimit, public=app)
