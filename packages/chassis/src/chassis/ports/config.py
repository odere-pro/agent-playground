"""ConfigPort: the agent config from the config store, versioned, reloaded without a restart."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from pydantic import BaseModel, Field


class LoadedConfig(BaseModel):
    name: str
    version: str
    data: dict[str, Any] = Field(default_factory=dict)


class ConfigNotFound(KeyError):
    """No config with that name in the store."""


class ConfigUnavailable(RuntimeError):
    """The store cannot be reached or answered with an error other than not found. The message
    holds no credential.
    """


Unsubscribe = Callable[[], None]
ConfigCallback = Callable[[LoadedConfig], Awaitable[None]]


class ConfigPort(Protocol):
    async def load(self, name: str) -> LoadedConfig: ...

    def subscribe(self, name: str, callback: ConfigCallback) -> Unsubscribe: ...
