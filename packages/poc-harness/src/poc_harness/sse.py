"""SSE bodies decoded: `(event name, JSON payload)` pairs, or the JSON payloads alone.

The one SSE parser is `chassis_contracts.inbound.sse`; these are thin wrappers over it, for the
`event: <name>` / `data: <json>` frames `/v1/run` and Anthropic stream, and the `data:` frames
OpenAI streams.
"""

from __future__ import annotations

import json
from typing import Any

from chassis_contracts.inbound import sse

Frame = tuple[str, dict[str, Any]]
DONE = "[DONE]"
"""The OpenAI end-of-stream marker: a `data:` payload that is not JSON."""


def sse_frames(text: str) -> list[Frame]:
    """`/v1/run` streamed: `(event name, payload)` per frame. Every frame must name its event."""
    out: list[Frame] = []
    for event in sse([text]):
        assert event.event is not None, f"a frame without `event:`: {event.data!r}"
        out.append((event.event, json.loads(event.data)))
    return out


def sse_data(text: str, *, named: bool = True) -> list[Any]:
    """The `data:` payloads of an SSE body, JSON-decoded, `[DONE]` kept as the string. With
    `named=False`, a frame that names its event fails the test (the OpenAI stream names none).
    """
    events = sse([text])
    assert named or not [e for e in events if e.event is not None], f"named frames in {text!r}"
    return [e.data if e.data == DONE else json.loads(e.data) for e in events if e.data]


def sse_events(text: str) -> list[tuple[str | None, Any]]:
    """`(event name or None, data as JSON, or the raw string when it is not JSON)` per frame with
    data. For a body that may mix named frames, unnamed frames, and `[DONE]`."""
    return [(event.event, _json_or_raw(event.data)) for event in sse([text]) if event.data]


def _json_or_raw(data: str) -> Any:
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data
