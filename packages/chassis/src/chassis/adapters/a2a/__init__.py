"""The A2A adapter: the one wire contract between the chassis and a workload's `handle`.

`mapping` is the event and request mapping as pure functions over dicts, chassis JSON crossing as
strings (a2a-sdk only, no chassis import; `packages/workload-a2a` holds a byte-for-byte copy).
`server` is the template A2A server that wraps `handle`, with a task store pruned once a task is
terminal (`packages/workload-a2a` mirrors it). `connector` is `A2AConnector`, the client side every
lane shares: the mapping back to events, the run's `traceparent` (header and `ctx.traceparent`),
cancel, a transport failure as `error {code: "a2a.transport"}`, and one span per run. `inprocess`
runs the server in the chassis process through `httpx.ASGITransport` (events arrive in one batch);
`sidecar` reaches the workload's own server on loopback, over TCP or a Unix socket, and streams.
Spec: docs/contracts/contract-v0.md, "Chassis events over A2A".
"""

from __future__ import annotations

from chassis.adapters.a2a.connector import A2AConnector
from chassis.adapters.a2a.inprocess import InProcessConnector
from chassis.adapters.a2a.server import HandleExecutor, build_agent_card, build_app
from chassis.adapters.a2a.sidecar import SidecarConnector

__all__ = [
    "A2AConnector",
    "HandleExecutor",
    "InProcessConnector",
    "SidecarConnector",
    "build_agent_card",
    "build_app",
]
