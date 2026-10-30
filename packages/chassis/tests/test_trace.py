"""`chassis.core.trace`: the W3C `traceparent` parser and the checked header a span continues.

Pins today's behavior of `parse_traceparent`, `continued`, and `parent_span_id`, imported from
core directly (no re-export in between).
"""

from __future__ import annotations

import pytest
from chassis.core.trace import continued, parent_span_id, parse_traceparent

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"  # pragma: allowlist secret
SPAN = "00f067aa0ba902b7"
VALID = f"00-{TRACE}-{SPAN}-01"
LATER = f"01-{TRACE}-{SPAN}-01-extra"

INVALID = [
    pytest.param(None, id="none"),
    pytest.param("", id="empty"),
    pytest.param("garbage", id="garbage"),
    pytest.param(f"00-{TRACE}-{SPAN}", id="no-flags"),
    pytest.param(f"00-{TRACE}-{SPAN}-01-extra", id="version-00-too-long"),
    pytest.param(f"01-{TRACE}-{SPAN}-01x", id="later-version-no-dash-after-55"),
    pytest.param(f"ff-{TRACE}-{SPAN}-01", id="version-ff"),
    pytest.param(f"00-{'0' * 32}-{SPAN}-01", id="zero-trace-id"),
    pytest.param(f"00-{TRACE}-{'0' * 16}-01", id="zero-parent-id"),
    pytest.param(f"00-{TRACE[:-1]}g-{SPAN}-01", id="not-hex"),
    pytest.param(f"00-{TRACE}0-0f067aa0ba902b7-01", id="wrong-field-lengths"),
]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        pytest.param(VALID, TRACE, id="valid"),
        pytest.param(f"  {VALID.upper()} ", TRACE, id="upper-and-spaces"),
        pytest.param(LATER, TRACE, id="later-version-extra-fields"),
    ],
)
def test_parse_traceparent_returns_the_trace_id_when_the_header_is_valid(
    header: str, expected: str
) -> None:
    """A valid header gives its 32-hex trace id."""
    result = parse_traceparent(header)

    assert result == expected


@pytest.mark.parametrize("header", INVALID)
def test_parse_traceparent_returns_none_when_the_header_is_absent_or_malformed(
    header: str | None,
) -> None:
    """An absent or malformed header gives None."""
    result = parse_traceparent(header)

    assert result is None


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        pytest.param(VALID, VALID, id="valid-as-is"),
        pytest.param(f"{VALID}   ", VALID, id="trailing-spaces-stripped"),
        pytest.param(f"  {VALID.upper()}", VALID, id="lowercased"),
        pytest.param(LATER, LATER[:55], id="cut-to-55"),
    ],
)
def test_continued_returns_the_cleaned_header_when_the_header_is_valid(
    header: str, expected: str
) -> None:
    """A valid header is stripped, lowercased, and cut to its 55 version-00 characters."""
    result = continued(header)

    assert result == expected


@pytest.mark.parametrize("header", INVALID)
def test_continued_returns_none_when_the_header_is_absent_or_malformed(
    header: str | None,
) -> None:
    """No raw header text survives an invalid header."""
    result = continued(header)

    assert result is None


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        pytest.param(VALID, SPAN, id="valid"),
        pytest.param(f" {LATER.upper()} ", SPAN, id="later-version-upper"),
    ],
)
def test_parent_span_id_returns_the_16_hex_parent_when_the_header_is_valid(
    header: str, expected: str
) -> None:
    """The parent span id is the third field of the cleaned header."""
    result = parent_span_id(header)

    assert result == expected


@pytest.mark.parametrize("header", INVALID)
def test_parent_span_id_returns_none_when_the_header_is_absent_or_malformed(
    header: str | None,
) -> None:
    """An invalid header has no parent span id."""
    result = parent_span_id(header)

    assert result is None
