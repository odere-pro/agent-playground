"""Dapr adapter for `EventPort` (httpx to the daprd sidecar, no Dapr SDK). PoC-4."""

from chassis.adapters.dapr.events import DaprEvents

__all__ = ["DaprEvents"]
