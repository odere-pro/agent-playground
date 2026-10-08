"""`ValkeyState`: `StatePort` over Valkey with valkey-py (`valkey.asyncio`), PoC-4.

valkey is imported only here (`make lint`). Picked by `spec.adapters.state: valkey`, built lazily
by `chassis.profiles`, so an unused SDK is never loaded.
"""

from chassis.adapters.valkey.state import ValkeyState

__all__ = ["ValkeyState"]
