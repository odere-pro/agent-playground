"""The W3C `traceparent` of a run, from `ctx.trace_id`. Standard library only.

`trace_id_hex(ctx.trace_id)` is the 32-hex trace id: `ctx.trace_id` itself when it is 32
lowercase hex, else the first 32 hex of its sha256. `traceparent_for(ctx, span_id)` is
`00-<trace id>-<parent id>-01`: the parent id is the connector's run span when `span_id` is a W3C
span id (16 lowercase hex, not all zero), else a fresh random one (suggested), so a new value per
call. It is the one source: the A2A connectors send it to the workload both as the `traceparent`
HTTP header and as `ctx.traceparent` (contract-v0.md, "Changes decided for v1", item 4); `handle`
forwards it on its model and MCP calls, and the chassis proxies look the run up by its trace id.

`parse_traceparent(header)` is the reverse: the 32-hex trace id of an inbound `traceparent`, or
None. It lives here, in core, so both the model proxy (`chassis.server`) and the MCP adapter
(`chassis.adapters.mcp`, which may not import `chassis.server`) use one parser.
`chassis.server.correlation` re-exports it.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chassis.core.envelope import Context

_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX16 = re.compile(r"[0-9a-f]{16}")
_NO_SPAN = "0" * 16
_TRACEPARENT = re.compile(r"([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})")
_TRACEPARENT_LEN = 55


def trace_id_hex(trace_id: str) -> str:
    """The 32 lowercase hex W3C trace id for `ctx.trace_id`."""
    if _HEX32.fullmatch(trace_id):
        return trace_id
    return hashlib.sha256(trace_id.encode()).hexdigest()[:32]


def traceparent_for(ctx: Context, span_id: str | None = None) -> str:
    """A `traceparent` value for one run: version 00, sampled, parent `span_id` when it is a W3C
    span id, else a random one.
    """
    if span_id is None or not _HEX16.fullmatch(span_id) or span_id == _NO_SPAN:
        span_id = secrets.token_hex(8)
        while span_id == _NO_SPAN:  # all-zero is invalid in W3C trace context
            span_id = secrets.token_hex(8)
    return f"00-{trace_id_hex(ctx.trace_id)}-{span_id}-01"


def parse_traceparent(header: str | None) -> str | None:
    """The trace id in a W3C `traceparent`, or None when it is absent or malformed. Never raises.

    `00-<32 hex trace id>-<16 hex parent id>-<2 hex flags>`, 55 characters for version `00`. A
    later version may append `-<fields>`; version `ff` and all-zero ids are invalid.
    """
    if not isinstance(header, str):
        return None
    value = header.strip().lower()
    match = _TRACEPARENT.match(value)
    if match is None:
        return None
    version, trace_id, parent_id, _flags = match.groups()
    if version == "ff" or trace_id == "0" * 32 or parent_id == _NO_SPAN:
        return None
    if version == "00" and len(value) != _TRACEPARENT_LEN:
        return None
    if len(value) > _TRACEPARENT_LEN and value[_TRACEPARENT_LEN] != "-":
        return None
    return trace_id
