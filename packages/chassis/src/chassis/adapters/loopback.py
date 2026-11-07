"""`is_loopback_host`: the one loopback check the chassis shares (review R2-30).

The `chassis serve` CLI uses it for `--proxy-host` (`chassis.server.cli`), the sidecar connector
for the interface URL of the sidecar's agent card (`chassis.adapters.a2a.sidecar`), and the OIDC
and MCP gateway adapters for their `http` exemptions. It sits directly under `adapters`, not in
a product package, so importing it loads no product SDK (review LOW 13): `chassis.adapters.a2a`
would load a2a-sdk. `chassis.adapters.a2a.loopback` re-exports it. `packages/workload-a2a` keeps
its own copy: it never imports `chassis` (ADR-002).
"""

from __future__ import annotations

import ipaddress

__all__ = ["is_loopback_host"]


def is_loopback_host(host: str) -> bool:
    """True for `localhost` and for any loopback IP address (`127.0.0.0/8`, `::1`). A name that is
    not an IP address, and a bracketed IPv6 such as `[::1]`, is not loopback."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
