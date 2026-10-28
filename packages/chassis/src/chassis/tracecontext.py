"""`continued(header)`: an inbound W3C `traceparent`, checked, for a span to continue (PoC-7).

The model proxy and the tool endpoint pass it as the `traceparent` span attribute, so a telemetry
adapter makes their span a child of the workload's call (`TelemetryPortContract`). Only a header
`chassis.core.trace.parse_traceparent` accepts is kept, cut to its 55 version-00 characters and
lowercased, so no raw header text reaches telemetry. Standard library only.
"""

from __future__ import annotations

from chassis.core.trace import parse_traceparent

TRACEPARENT_LEN = 55


def continued(header: str | None) -> str | None:
    """The `traceparent` to continue, or None when `header` is absent or invalid."""
    if parse_traceparent(header) is None or header is None:
        return None
    return header.strip().lower()[:TRACEPARENT_LEN]


def parent_span_id(header: str | None) -> str | None:
    """The parent span id (16 hex) of a valid `traceparent`, or None."""
    value = continued(header)
    return value.split("-")[2] if value is not None else None
