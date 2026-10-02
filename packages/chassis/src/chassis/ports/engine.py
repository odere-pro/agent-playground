"""EngineConnector: the lane that runs the workload. Picked by `spec.engine.connector` (ADR-001)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any, Literal, Protocol

from chassis.core.envelope import Context, Request
from chassis.core.events import Event

if TYPE_CHECKING:
    from chassis.ports.bundle import PortBundle

Lane = Literal["inprocess", "sidecar", "remote"]
LANES: tuple[Lane, ...] = ("inprocess", "sidecar", "remote")


class EngineConnector(Protocol):
    kind: Lane
    capabilities: frozenset[str]
    """Any of: streaming, tools, structured_output, code_exec, multi_agent."""

    async def setup(self, config: Mapping[str, Any], ports: PortBundle) -> None: ...

    def run(self, request: Request, ctx: Context) -> AsyncIterator[Event]: ...

    async def probe(self) -> bool:
        """Is the workload answering? `True` or `False`, never an exception. Cheap and bounded in
        time: the readiness monitor calls it every few seconds (contract v3, PoC-4).
        """
        ...

    async def close(self) -> None: ...
