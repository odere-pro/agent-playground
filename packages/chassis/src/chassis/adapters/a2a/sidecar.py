"""The `sidecar` connector: the workload's own A2A server in another process on the same host,
reached over loopback (ADR-001: the sidecar serves localhost only).

`setup(config, ports)` reads `spec.engine`:

- `url` (required): `http://127.0.0.1:<port>` or `http://localhost:<port>`, no path, no query, no
  user info, no other host. Anything else is a `ValueError` that names ADR-001.
- `uds` (optional): a Unix socket path the requests go over instead of TCP, for tests and local
  runs. The `url` still fills the `Host` header and must still be loopback.

`setup` reads the agent card from the sidecar, so it fails at once, with a `RuntimeError` naming
the url, when the sidecar is not up; it does not retry. A card whose interface URL is not loopback
is refused too, so the card cannot send the chassis's requests off the host. The httpx client
ignores proxy environment variables for the same reason.

Everything after `setup` is `A2AConnector`. Over a socket the events stream: each `delta` is
yielded as it arrives, `budget.timeout_ms` (per read and as the run deadline) fires mid-run as
`error {code: "a2a.timeout"}`, the cancel lands mid-run, and a sidecar that dies mid-stream is
`error {code: "a2a.transport", retryable: true}`.

`probe()` GETs the agent card over a client of its own (not the run client, so a pool full of
long runs never delays it), with a `PROBE_TIMEOUT_S` timeout. `True` on 200; `False` on any other
status, a timeout, or a transport error. It never raises.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
from a2a.types import AgentCard
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from a2a.utils.errors import A2AError

from chassis.adapters.a2a.connector import A2AConnector
from chassis.ports.engine import Lane

if TYPE_CHECKING:
    from chassis.ports.bundle import PortBundle

LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
"""The hosts `spec.engine.url` may name."""

PROBE_TIMEOUT_S = 1.0
"""suggested: how long `probe()` waits for the agent card."""


def _refuse(what: str, value: object) -> ValueError:
    return ValueError(
        f"{what} must be http://127.0.0.1:<port> or http://localhost:<port> with no path; "
        f"got {value!r}. The sidecar serves localhost only (ADR-001)"
    )


def loopback_url(url: object) -> str:
    """`url` without a trailing slash, when it is a loopback base URL; else `ValueError`."""
    if not isinstance(url, str) or not url:
        raise ValueError("engine.url is required for the sidecar connector (ADR-001: loopback)")
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = None
    host = parts.hostname or ""
    if (
        parts.scheme != "http"
        or host not in LOOPBACK_HOSTS
        or port is None
        or parts.netloc.lower() != f"{host}:{port}"
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise _refuse("engine.url", url)
    return f"http://{host}:{port}"


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class SidecarConnector(A2AConnector):
    kind: Lane = "sidecar"

    def __init__(self) -> None:
        super().__init__()
        self.url: str | None = None
        self._probe_http: httpx.AsyncClient | None = None

    def _check_card(self, card: AgentCard) -> None:
        for interface in card.supported_interfaces:
            parts = urlsplit(interface.url)
            if parts.scheme != "http" or not _is_loopback_host(parts.hostname or ""):
                raise _refuse("the sidecar's agent card interface url", interface.url)

    async def setup(self, config: Mapping[str, Any], ports: PortBundle) -> None:
        url = loopback_url(config.get("url"))
        uds = config.get("uds")
        if uds is not None and (not isinstance(uds, str) or not uds):
            raise ValueError(f"engine.uds must be a Unix socket path, got {uds!r}")
        transport = httpx.AsyncHTTPTransport(uds=uds) if uds else None
        http = httpx.AsyncClient(transport=transport, base_url=url, trust_env=False)
        try:
            await self._open(http, url, ports)
        except (A2AError, httpx.HTTPError, OSError) as exc:
            await http.aclose()
            self._http = None
            over = f" over {uds}" if uds else ""
            raise RuntimeError(
                f"the sidecar at {url}{over} is not reachable: {exc}. "
                "Start the workload's A2A server first (workload-a2a serve)"
            ) from exc
        except BaseException:
            await http.aclose()
            self._http = None
            raise
        self.url = url
        probe_transport = httpx.AsyncHTTPTransport(uds=uds) if uds else None
        self._probe_http = httpx.AsyncClient(
            transport=probe_transport, base_url=url, trust_env=False, timeout=PROBE_TIMEOUT_S
        )

    async def probe(self) -> bool:
        if self._probe_http is None:
            return False
        try:
            response = await self._probe_http.get(AGENT_CARD_WELL_KNOWN_PATH)
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
