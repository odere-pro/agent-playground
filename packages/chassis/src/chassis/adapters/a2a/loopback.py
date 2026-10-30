"""`is_loopback_host`: the one loopback check the chassis shares (review R2-30).

The `chassis serve` CLI uses it for `--proxy-host` (`chassis.server.cli`), and the sidecar
connector for the interface URL of the sidecar's agent card (`chassis.adapters.a2a.sidecar`). It
sits under `adapters` because the layer contract lets `server` import `adapters`, never the other
way. It imports no product SDK. `packages/workload-a2a` keeps its own copy: it never imports
`chassis` (ADR-002).
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
