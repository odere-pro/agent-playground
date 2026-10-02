"""`InMemoryState`: the `StatePort` fake. One dict per process; two replicas in a test share one
object to stand for Valkey. Defined in `chassis.ports.state` because `PortBundle` defaults to it.
"""

from chassis.ports.state import InMemoryState, StateUnavailable

__all__ = ["InMemoryState", "StateUnavailable"]
