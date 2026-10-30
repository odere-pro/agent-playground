"""Named-event SSE bodies as `(event name, JSON payload)` pairs.

The one SSE parser is `chassis_contracts.inbound.sse`; this is a thin wrapper over it for the
`event: <name>` / `data: <json>` frames `/v1/run` streams.
"""

from __future__ import annotations

import json
from typing import Any

from chassis_contracts.inbound import sse

Frame = tuple[str, dict[str, Any]]


def sse_frames(text: str) -> list[Frame]:
    """`/v1/run` streamed: `(event name, payload)` per frame. Every frame must name its event."""
    out: list[Frame] = []
    for event in sse([text]):
        assert event.event is not None, f"a frame without `event:`: {event.data!r}"
        out.append((event.event, json.loads(event.data)))
    return out
