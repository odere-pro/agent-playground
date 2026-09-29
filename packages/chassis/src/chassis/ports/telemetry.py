"""TelemetryPort: spans, counters, and logs for every stage. The real adapter is the OpenTelemetry
SDK.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Span:
    name: str
    span_id: str
    parent_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    ended: bool = False


class TelemetryPort(Protocol):
    def span(self, name: str, **attributes: Any) -> AbstractContextManager[Span]: ...

    def counter(self, name: str, value: int = 1, **labels: Any) -> None: ...

    def log(self, level: str, message: str, **fields: Any) -> None: ...
