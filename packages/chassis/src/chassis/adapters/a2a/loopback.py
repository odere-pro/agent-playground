"""Re-exports `chassis.adapters.loopback.is_loopback_host` (review LOW 13), so the callers that
import it from here keep working. New code imports `chassis.adapters.loopback`, which loads no
product SDK.
"""

from __future__ import annotations

from chassis.adapters.loopback import is_loopback_host

__all__ = ["is_loopback_host"]
