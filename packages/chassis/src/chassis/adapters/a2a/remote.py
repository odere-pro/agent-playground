"""The `remote` connector (PoC-5, plan section 2.1): the workload's A2A server on another host,
reached over `http` or `https` with a bearer token (ADR-001 item 7: one A2A client, a different
URL).

`setup(config, ports)` reads `spec.engine` (`EngineSpec.as_mapping()`):

- `url` (required): `http(s)://host:port[/path]`, no user info, no query, no fragment. The config
  checks it too and requires `https` in the `cloud` profile; the connector only refuses what it
  cannot use.
- `auth` (required): `{scheme: bearer, token_env, previous_token_env}`. The token is read from
  `os.environ[token_env]` here; a missing or empty variable is a `LookupError` that names the
  variable. `previous_token_env` is for the chassis's remote listener, not for this client.
- `uds` (optional): a Unix socket path the requests go over instead of TCP, for tests and local
  runs. The `url` still fills the `Host` header and the path.
- `probe_timeout_s` (default `PROBE_TIMEOUT_S`): the probe crosses the network.

Every request carries `Authorization: Bearer <token>`: the card fetch, every message, the cancel,
and the probe. The httpx clients ignore the proxy and TLS environment (`trust_env=False`) and
never follow a redirect, so the token goes to the configured host only. For the same reason the
card's interface URL is never followed: `_check_card` keeps only the JSON-RPC interface and sets
its URL to the configured `url` (a card with none is refused). The token is never kept as an
attribute, logged, put in a span or an error, or shown in `repr`.

- `protocol` (default `chassis`): `a2a` reads a third-party agent's own A2A stream (no
  `chassis.event` metadata) through `chassis.adapters.a2a.plain`; `a2a` (optional) holds its
  `usage_key` and `context_id`. The bearer, the card pin, and the probe are the same in both modes.
  The mode is the operator's choice; the connector never detects it.

Everything after `setup` is `A2AConnector`: the mapping, the deadline, the cancel, the span, and
the `traceparent`. `probe()` GETs the agent card with the token over a client of its own. `True`
on 200 only; `False` on any other status, a timeout, or a transport error. It never raises.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
from a2a.types import AgentCard, AgentInterface, SendMessageRequest
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, TransportProtocol
from a2a.utils.errors import A2AError

from chassis.adapters.a2a.connector import A2AConnector, EventTranslator
from chassis.adapters.a2a.plain import PlainOptions, PlainTranslator, plain_message
from chassis.core.envelope import Context, Request
from chassis.ports.engine import Lane

if TYPE_CHECKING:
    from chassis.ports.bundle import PortBundle

PROBE_TIMEOUT_S = 2.0
"""suggested: the probe's timeout when `probe_timeout_s` is not given."""


def remote_url(url: object) -> str:
    """`url` without a trailing slash when it is `http(s)://host:port[/path]`; else `ValueError`."""
    if not isinstance(url, str) or not url:
        raise ValueError("engine.url is required for the remote connector")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        port = None
        parts = urlsplit("")
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or port is None
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or "?" in url
        or "#" in url
    ):
        raise ValueError(
            "engine.url for the remote connector must be http(s)://host:port[/path] "
            "with no user info, query, or fragment"
        )
    return url.rstrip("/")


def read_token(auth: object) -> str:
    """The bearer token from the variable `auth.token_env` names. Errors name the variable only."""
    if not isinstance(auth, Mapping) or auth.get("scheme", "bearer") != "bearer":
        raise ValueError("engine.auth for the remote connector must be {scheme: bearer, ...}")
    name = auth.get("token_env")
    if not isinstance(name, str) or not name:
        raise ValueError("engine.auth.token_env must name an environment variable")
    token = os.environ.get(name, "")
    if not token:
        raise LookupError(f"{name} is not set or empty: the remote connector needs its token")
    return token


class RemoteConnector(A2AConnector):
    kind: Lane = "remote"

    def __init__(self) -> None:
        super().__init__()
        self.url: str | None = None
        self._probe_http: httpx.AsyncClient | None = None
        self._plain: PlainOptions | None = None
        """Set in `protocol: a2a` mode only."""

    def __repr__(self) -> str:
        return f"RemoteConnector(url={self.url!r})"

    def _check_card(self, card: AgentCard) -> None:
        """Keep the JSON-RPC interface only, at the configured URL. The card's own URL, which
        could name any host, is never used."""
        if self.url is None:
            raise RuntimeError("remote connector url is not set")
        jsonrpc = [
            i for i in card.supported_interfaces if i.protocol_binding == TransportProtocol.JSONRPC
        ]
        if not jsonrpc:
            raise ValueError("the remote's agent card has no JSON-RPC interface")
        pinned = AgentInterface(
            url=self.url,
            protocol_binding=TransportProtocol.JSONRPC,
            protocol_version=jsonrpc[0].protocol_version,
        )
        del card.supported_interfaces[:]
        card.supported_interfaces.append(pinned)

    def _message_for(self, request: Request, ctx: Context) -> SendMessageRequest:
        if self._plain is None:
            return super()._message_for(request, ctx)
        return plain_message(
            request.input.model_dump(mode="json"), ctx.model_dump(mode="json"), self._plain
        )

    def _translator_for(self, request: Request) -> EventTranslator:
        if self._plain is None or self._ports is None:
            return super()._translator_for(request)
        return PlainTranslator(request.request_id, self._plain, self._ports.telemetry.log)

    def _client_for(self, token: str, uds: str | None, timeout: float | None) -> httpx.AsyncClient:
        transport = httpx.AsyncHTTPTransport(uds=uds) if uds else None
        kwargs: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
        return httpx.AsyncClient(
            transport=transport,
            base_url=self.url or "",
            headers={"Authorization": f"Bearer {token}"},
            trust_env=False,
            follow_redirects=False,
            **kwargs,
        )

    async def setup(self, config: Mapping[str, Any], ports: PortBundle) -> None:
        url = remote_url(config.get("url"))
        token = read_token(config.get("auth"))
        uds = config.get("uds")
        if uds is not None and (not isinstance(uds, str) or not uds):
            raise ValueError("engine.uds must be a Unix socket path")
        probe_timeout = config.get("probe_timeout_s", PROBE_TIMEOUT_S)
        if not isinstance(probe_timeout, int | float) or probe_timeout <= 0:
            raise ValueError("engine.probe_timeout_s must be a positive number")
        protocol = config.get("protocol", "chassis")
        if protocol not in ("chassis", "a2a"):
            raise ValueError("engine.protocol must be 'chassis' or 'a2a'")
        plain = PlainOptions.from_mapping(config.get("a2a")) if protocol == "a2a" else None
        if protocol != "a2a" and config.get("a2a") is not None:
            raise ValueError("engine.a2a needs engine.protocol: a2a")
        self._plain = plain
        self.url = url
        http = self._client_for(token, uds, None)
        try:
            await self._open(http, url, ports)
        except (A2AError, httpx.HTTPError, OSError, ValueError) as exc:
            await http.aclose()
            self._http = None
            self.url = None
            over = f" over {uds}" if uds else ""
            # Only the class name: an SDK message could quote a request.
            raise RuntimeError(
                f"the remote at {url}{over} is not reachable: {type(exc).__name__}"
            ) from None
        except BaseException:
            await http.aclose()
            self._http = None
            self.url = None
            raise
        self._probe_http = self._client_for(token, uds, float(probe_timeout))

    async def probe(self) -> bool:
        if self._probe_http is None:
            return False
        try:
            response = await self._probe_http.get(f"{self.url}{AGENT_CARD_WELL_KNOWN_PATH}")
        except (httpx.HTTPError, OSError):
            return False
        return response.status_code == 200

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            if self._probe_http is not None:
                await self._probe_http.aclose()
                self._probe_http = None
