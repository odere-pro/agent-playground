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
    `named=False`, every non-empty block must be one `data: ` line with a payload, as the OpenAI
    stream sends them: a name, a comment, an `id:` line, or an empty frame fails the test.
    """
    if not named:
        _assert_only_data_blocks(text)
    return [e.data if e.data == DONE else json.loads(e.data) for e in sse([text]) if e.data]


def _assert_only_data_blocks(text: str) -> None:
    """The parser skips what it does not use, so this reads the raw blocks."""
    for block in text.split("\n\n"):
        if block.strip():
            is_one_data_line = block.startswith("data: ") and "\n" not in block
            assert is_one_data_line and block[6:].strip(), f"not one `data: ` line: {block!r}"


def sse_events(text: str) -> list[tuple[str | None, Any]]:
    """`(event name or None, data as JSON, or the raw string when it is not JSON)` per frame with
    data. For a body that may mix named frames, unnamed frames, and `[DONE]`."""
    return [(event.event, _json_or_raw(event.data)) for event in sse([text]) if event.data]


def _json_or_raw(data: str) -> Any:
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data
