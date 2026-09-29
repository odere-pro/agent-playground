"""The A2A adapter: the one wire contract between the chassis and a workload's `handle`.

`mapping` is the event and request mapping as pure functions over dicts (a2a-sdk only, no chassis
import, so PoC-2 can copy it into the service template). `server` is the template A2A server that
wraps `handle`. `inprocess` is the connector that runs that server in the chassis process and calls
it through `httpx.ASGITransport`. Spec: docs/contracts/contract-v0.md, "Chassis events over A2A".
"""

from __future__ import annotations

from chassis.adapters.a2a.inprocess import InProcessConnector
from chassis.adapters.a2a.server import HandleExecutor, build_agent_card, build_app

__all__ = ["HandleExecutor", "InProcessConnector", "build_agent_card", "build_app"]
