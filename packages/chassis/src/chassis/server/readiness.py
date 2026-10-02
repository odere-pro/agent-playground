"""Readiness: what `GET /ready` answers, and the task that watches the workload (PoC-4 plan,
section 7).

`/ready` is 200 only when the lifespan finished, the chassis is not draining, and the workload
answers its probe. Else 503 with a `reason`: `starting`, `draining`, or `workload_unreachable`.
`/health` never reads any of this, so a hung workload never restarts the chassis.

`ReadinessMonitor` calls `EngineConnector.probe()` every `interval_s`, each call bounded by
`timeout_s`. A `False`, a timeout, or an exception is a failure. After `failures` in a row the
workload is unhealthy; one success makes it healthy again. `/ready` reads the cached result and
never calls the workload itself. It starts healthy: the engine's `setup` has just reached it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any, Literal

from chassis.ports.engine import EngineConnector

log = logging.getLogger("chassis.readiness")

Reason = Literal["starting", "draining", "workload_unreachable"]


@dataclass(frozen=True)
class ProbeSettings:
    """suggested: every default. No CLI flag; tests pass small values to `create_app`."""

    interval_s: float = 2.0
    timeout_s: float = 1.0
    failures: int = 3


class ReadinessMonitor:
    def __init__(self, engine: EngineConnector, settings: ProbeSettings) -> None:
        self._engine = engine
        self.settings = settings
        self.failures = 0
        self.healthy = True
        self._task: asyncio.Task[None] | None = None

    async def check_once(self) -> bool:
        """Probe once and update `healthy`. Returns the probe's result."""
        try:
            ok = await asyncio.wait_for(self._engine.probe(), self.settings.timeout_s)
        except Exception as exc:  # a probe must never take the monitor down
            log.debug("workload probe failed: %s", type(exc).__name__)
            ok = False
        if ok:
            if not self.healthy:
                log.info("workload answers again")
            self.failures = 0
            self.healthy = True
        else:
            self.failures += 1
            if self.healthy and self.failures >= self.settings.failures:
                log.warning("workload unreachable after %d failed probes", self.failures)
                self.healthy = False
        return bool(ok)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.interval_s)
            await self.check_once()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="chassis.readiness")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def not_ready_reason(state: Any) -> Reason | None:
    """Why the app at `state` is not ready, or `None` when it is. Reads `state.ready` (the
    lifespan finished), `state.draining`, and `state.readiness` (a `ReadinessMonitor` or `None`).
    `state.config_loaded` set to `False` (the config loader has not set the first config yet,
    `chassis.server.config_loader`) is `starting` too; a state without it is not held back.
    """
    if getattr(state, "draining", False):
        return "draining"
    if getattr(state, "config_loaded", True) is False:
        return "starting"
    if not getattr(state, "ready", False):
        return "starting"
    monitor: ReadinessMonitor | None = getattr(state, "readiness", None)
    if monitor is not None and not monitor.healthy:
        return "workload_unreachable"
    return None
